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
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Administradores podem rodar o pipeline.")
        
    if AppState.pipeline_rodando:
        raise HTTPException(status_code=400, detail="O pipeline já está em execução.")

    AppState.pipeline_rodando = True
    AppState.logs = [] 
    background_tasks.add_task(executar_pipeline_nexus)
    return {"status": "success", "message": "Pipeline de Engenharia de Dados iniciado em Background!"}

# =====================================================================
# ROTAS DA MÁQUINA DO TEMPO
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
    if usuario.get('funcao') != 'Administrador':
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
# GESTÃO DE CADEADOS E EXPORTAÇÃO
# =====================================================================

@router.post("/reabrir-ciclo")
async def reabrir_ciclo(origem: str, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")
        
    ciclo_atual = get_current_cycle(db)
    try:
        # 1. Lógica especial para o Gerenciamento (Destranca todos os vendedores)
        if origem.strip().upper() == "GERENCIAMENTO":
            # Apaga qualquer cadeado que NÃO seja as 3 etapas globais
            # (Ou seja, vai apagar as travas de "Ricardo", "João", etc.)
            deletados = db.query(ControleCiclo).filter(
                ControleCiclo.ciclo_sop == ciclo_atual,
                ~func.upper(func.trim(ControleCiclo.origem)).in_([
                    'TOP-DOWN ARENA', 'SUPPLY REVIEW', 'S&OP-FINAL'
                ])
            ).delete(synchronize_session=False)
            
            db.commit()
            
            if deletados > 0:
                return {"status": "success", "message": f"Todos os {deletados} bloqueios de Vendedores foram removidos!"}
            return {"status": "success", "message": "A tela de Gerenciamento já se encontra aberta."}

        # 2. Lógica padrão para as etapas globais (Top-Down, Supply, Dashboard)
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
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")
        
    try:
        ciclo = get_current_cycle(db)
        m2, m4 = get_projection_window(db)
        
        resultados = get_truth_query(db, ciclo, m2, m4).with_entities(
            DimCliente.vendedor_nome, DimCliente.gerente_nome, DimCliente.regional, DimCliente.cgc, DimCliente.razaosocial,
            DimCliente.cod_cliente, DimCliente.loja, DimCliente.supervisor_nome,
            DimProduto.sku, DimProduto.descricao, DimProduto.categoria, DimProduto.segmento,
            FatoIbpGranular.mes_projetado, FatoIbpGranular.pmv_aplicado,
            FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, FatoIbpGranular.vol_bottomup, 
            FatoIbpGranular.vol_supply, FatoIbpGranular.vol_final, FatoIbpGranular.vol_meta
        ).all()

        if not resultados:
            raise HTTPException(404, detail="Sem dados no ciclo selecionado.")

        dados = []
        for r in resultados:
            # Multiplicamos o volume final pelo PMV para já exportar o valor financeiro (Opcional, mas muito útil)
            receita_projetada = float(r.vol_final or 0) * float(r.pmv_aplicado or 0)

            dados.append({
                "Ciclo S&OP": ciclo,
                "CGC": r.cgc,
                "Cliente": r.cod_cliente,
                "Loja": r.loja,
                "Razão Social": r.razaosocial,                
                "Regional": r.regional,                
                "Gerente": r.gerente_nome,
                "Coordenador": r.supervisor_nome,
                "Vendedor": r.vendedor_nome,           
                "Categoria": r.categoria,
                "Segmento": r.segmento,
                "SKU": r.sku,
                "Produto": r.descricao,
                "Mês Projetado": str(r.mes_projetado),
                "PMV Unitário (R$)": float(r.pmv_aplicado or 0),
                "Final S&OP (CX)": int(r.vol_final or 0),
                "Receita S&OP (R$)": round(receita_projetada, 2)
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
    if usuario_logado.get('funcao') != 'Administrador':
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

# =====================================================================
# GESTÃO DE USUÁRIOS E SEGURANÇA (SQL PURO)
# =====================================================================

@router.delete("/delete-user/{user_id}")
async def excluir_usuario_sistema(user_id: int, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    """Exclui permanentemente um usuário ativo do sistema via Raw SQL para evitar erros de importação"""
    
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem excluir usuários.")
    
    if user_id == usuario_logado.get('id'):
        raise HTTPException(
            status_code=400, 
            detail="Operação negada: Você não pode excluir a sua própria conta de administrador."
        )

    try:
        # Apaga o usuário diretamente com SQL, sem necessitar da classe Modelo
        db.execute(text("DELETE FROM usuarios WHERE id = :id"), {"id": user_id})
        db.commit()
        return {"status": "success", "message": f"Utilizador removido com sucesso do Nexus."}
    except Exception as e:
        db.rollback()
        # Se a tabela não se chamar "usuarios", tenta a "users"
        try:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            db.commit()
            return {"status": "success", "message": f"Utilizador removido com sucesso do Nexus."}
        except Exception as e2:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Erro interno ao excluir utilizador. Contacte o suporte.")