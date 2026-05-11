from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
import io
import pandas as pd
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.core.state import AppState
from app.models.domain_models import ControleCiclo, DimProduto, DimCliente, FatoIbpGranular, AuditoriaAjuste
from app.etl.pipeline import executar_pipeline_nexus
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import get_current_cycle, get_truth_query, get_projection_window

router = APIRouter(prefix="/api/v1/admin", tags=["Administração e Pipeline"])

# SCHEMA PARA ALTERAÇÃO DE CICLO
class PayloadCiclo(BaseModel):
    novo_ciclo: str  # Formato "MM/YYYY"

@router.get("/pipeline/status")
async def obter_status_pipeline(usuario_logado: dict = Depends(get_current_user)):
    return {"is_running": AppState.pipeline_rodando, "logs": AppState.logs}

@router.post("/pipeline/start")
async def iniciar_pipeline(background_tasks: BackgroundTasks, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Administradores podem rodar o pipeline.")
        
    if AppState.pipeline_rodando:
        raise HTTPException(status_code=400, detail="O pipeline já está em execução.")

    AppState.pipeline_rodando = True
    AppState.logs = [] 
    background_tasks.add_task(executar_pipeline_nexus)
    return {"status": "success", "message": "Pipeline de Engenharia de Dados iniciado em Background!"}

# =====================================================================
# ROTAS DA MÁQUINA DO TEMPO (NOVO)
# =====================================================================

@router.get("/ciclo-ativo")
async def obter_ciclo_ativo(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Retorna o ciclo que está atualmente a comandar o sistema"""
    try:
        resultado = db.execute(text("SELECT ciclo_ativo_global FROM configuracao_sistema ORDER BY id DESC LIMIT 1")).fetchone()
        ciclo = resultado[0] if resultado else datetime.date.today().strftime("%m/%Y")
        return {"ciclo_ativo": ciclo}
    except Exception as e:
        return {"ciclo_ativo": datetime.date.today().strftime("%m/%Y"), "error": str(e)}

@router.post("/ciclo-ativo")
async def atualizar_ciclo_ativo(payload: PayloadCiclo, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Altera o relógio global do sistema S&OP"""
    if usuario['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Apenas administradores podem viajar no tempo.")
    
    try:
        # Validação simples de formato
        partes = payload.novo_ciclo.split('/')
        if len(partes) != 2 or len(partes[0]) != 2 or len(partes[1]) != 4:
            raise HTTPException(status_code=400, detail="Formato inválido. Use MM/YYYY (ex: 05/2026)")

        db.execute(
            text("INSERT INTO configuracao_sistema (ciclo_ativo_global) VALUES (:ciclo)"),
            {"ciclo": payload.novo_ciclo}
        )
        db.commit()
        return {"status": "success", "message": f"Sistema Nexus agora focado no ciclo {payload.novo_ciclo}"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================================
# GESTÃO DE CADEADOS E EXPORTAÇÃO (ATUALIZADO COM 'db')
# =====================================================================

@router.post("/reabrir-ciclo")
async def reabrir_ciclo(origem: str, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")
        
    # ATUALIZAÇÃO: Passando 'db' para a função
    ciclo_atual = get_current_cycle(db)
    try:
        cadeado = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo_atual, 
            func.upper(func.trim(ControleCiclo.origem)) == origem.strip().upper()
        ).first()
        
        if cadeado:
            db.delete(cadeado)
            db.commit()
            return {"status": "success", "message": f"O cadeado de '{origem}' foi removido com sucesso!"}
            
        return {"status": "success", "message": f"A tela de '{origem}' já se encontra aberta."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/exportar-base")
async def exportar_base_granular(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")
        
    try:
        # ATUALIZAÇÃO: Passando 'db' para as funções de tempo
        ciclo = get_current_cycle(db)
        m2, m4 = get_projection_window(db)
        
        resultados = get_truth_query(db, ciclo, m2, m4).with_entities(
            DimCliente.vendedor_nome, DimCliente.gerente_nome, DimCliente.regional, DimCliente.cgc, DimCliente.razaosocial,
            DimProduto.sku, DimProduto.descricao, DimProduto.categoria,
            FatoIbpGranular.mes_projetado, FatoIbpGranular.pmv_aplicado,
            FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, FatoIbpGranular.vol_bottomup, 
            FatoIbpGranular.vol_supply, FatoIbpGranular.vol_final, FatoIbpGranular.vol_meta
        ).all()

        if not resultados:
            raise HTTPException(404, detail="Sem dados no ciclo selecionado.")

        dados = []
        for r in resultados:
            dados.append({
                "Ciclo S&OP": ciclo,
                "Gerente": r.gerente_nome,
                "Vendedor": r.vendedor_nome,
                "Razão Social": r.razaosocial,
                "Categoria": r.categoria,
                "Produto": r.descricao,
                "Mês Projetado": str(r.mes_projetado),
                "Sinal IA (CX)": int(r.vol_ia or 0),
                "Top-Down (CX)": int(r.vol_topdown or 0),
                "Comercial (CX)": int(r.vol_bottomup or 0),
                "Supply (CX)": int(r.vol_supply or 0),
                "Final S&OP (CX)": int(r.vol_final or 0)
            })

        df = pd.DataFrame(dados)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Base_SOP_Granular')
        
        buffer.seek(0)
        return StreamingResponse(
            buffer, 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=Nexus_Base_Granular_{ciclo.replace('/','_')}.xlsx"}
        )
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/auditoria/logs")
async def listar_logs_auditoria(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")
        
    try:
        # Busca os últimos 500 registos de alteração
        logs = db.query(AuditoriaAjuste).order_by(AuditoriaAjuste.data_ajuste.desc()).limit(500).all()
        
        dados = []
        for l in logs:
            dados.append({
                "id": l.id,
                "data": l.data_ajuste.strftime("%d/%m/%Y %H:%M"),
                "usuario": l.usuario_nome or "Sistema",
                "origem": l.origem_ajuste,
                "cliente": l.razaosocial_afetada,
                "sku": l.sku,
                "mes_ref": l.mes_projetado.strftime("%m/%Y"),
                "de": int(l.valor_antigo or 0),
                "para": int(l.vol_novo)
            })
        return {"status": "success", "dados": dados}
    except Exception as e:
        raise HTTPException(500, f"Erro ao carregar auditoria: {str(e)}")