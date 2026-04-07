from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo

router = APIRouter(prefix="/api/v1/dashboard", tags=["S&OP Global Dashboard"])

# --- Utilitários ---
def get_current_cycle() -> str:
    return datetime.date.today().strftime("%m/%Y")

def get_projection_window() -> tuple[str, str]:
    hoje = datetime.date.today().replace(day=1)
    return (
        (hoje + relativedelta(months=2)).strftime('%Y-%m-%d'),
        (hoje + relativedelta(months=4)).strftime('%Y-%m-%d')
    )

class AjusteGlobal(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGlobal(BaseModel):
    ajustes: List[AjusteGlobal]

@router.get("/global")
async def carregar_dashboard_global(db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle()
        m_plus_2, m_plus_4 = get_projection_window()

        # --- GATEKEEPER ---
        status_global = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()
        is_global_fechado = status_global.status == 'Fechado' if status_global else False
        
        status_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        is_td_fechado = status_td.status == 'Fechado' if status_td else False

        vendedores_ativos = db.query(FatoIbpGranular.vendedor_nome).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
            .filter(
                func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2,
                func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4,
                DimCliente.bloqueado != 'INATIVO',
                FatoIbpGranular.vendedor_nome.isnot(None)
            ).distinct().all()
        
        v_ativos = [v[0].strip() for v in vendedores_ativos if v[0] and v[0].strip()]
        
        vendedores_fechados = db.query(ControleCiclo.origem).filter(
            ControleCiclo.ciclo_sop == ciclo, ControleCiclo.status == 'Fechado',
            ControleCiclo.origem.notin_(['Top-Down', 'S&OP-Final'])
        ).all()
        v_fechados = [v[0].strip() for v in vendedores_fechados if v[0]]

        pendentes = [v for v in v_ativos if v not in v_fechados]

        if is_global_fechado:
            is_locked, lock_message = True, "Demanda Irrestrita Publicada"
        elif not is_td_fechado:
            is_locked, lock_message = True, "Aguardando Visão Gerencial"
        elif pendentes:
            is_locked, lock_message = True, f"Aguardando {len(pendentes)} Vendedor(es)"
        else:
            is_locked, lock_message = False, "Publicar Demanda Irrestrita"
            
        # --- BUSCA DE DADOS ---
        resultados = db.query(
            FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('vol_topdown'),
            func.sum(FatoIbpGranular.vol_bottomup).label('vol_bottomup'),
            func.sum(FatoIbpGranular.vol_final).label('vol_final'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('receita_base'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_simples'),
            DimProduto.descricao, DimProduto.categoria
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(
             func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2,
             func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4,
             DimCliente.bloqueado != 'INATIVO'
         ).group_by(
             FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado,
             DimProduto.descricao, DimProduto.categoria
         ).all()

        dados_formatados = []
        for r in resultados:
            vol_ia = int(r.vol_ia or 0)
            pmv_real = (float(r.receita_base or 0) / vol_ia) if vol_ia > 0 else float(r.pmv_simples or 0)
            dados_formatados.append({
                "chave_matriz": f"{r.sku}_{r.razaosocial}", "categoria": r.categoria, "produto": r.sku,
                "descricao": r.descricao, "cliente_razaosocial": r.razaosocial, "mes_projetado": r.mes_projetado.strftime("%Y-%m-%d"),
                "vol_ia": vol_ia, "vol_td": int(r.vol_topdown or 0), "vol_bu": int(r.vol_bottomup or 0),
                "vol_irrestrito": int(r.vol_final or 0), "pmv": pmv_real
            })

        return {
            "status": "success", 
            "is_locked": is_locked, 
            "lock_message": lock_message, 
            "dados": dados_formatados
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/aprovar")
async def aprovar_dashboard_global(payload: PayloadAprovarGlobal, db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle()
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()
        if registro and registro.status == 'Fechado':
            raise HTTPException(status_code=403, detail="O Ciclo já está fechado.")

        for ajuste in payload.ajustes:
            data_alvo = datetime.datetime.strptime(ajuste.mes_projetado, "%Y-%m-%d").date()
            if ajuste.nivel == 'cliente':
                partes = ajuste.chave.split('_', 1)
                sku, razaosocial = partes[0], partes[1] if len(partes) > 1 else ""
                linhas = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(
                    FatoIbpGranular.sku == sku, DimCliente.razaosocial == razaosocial, FatoIbpGranular.mes_projetado == data_alvo, DimCliente.bloqueado != 'INATIVO'
                ).all()
                if linhas:
                    total_base = sum([l.vol_final for l in linhas])
                    soma_dist = 0
                    for i, l in enumerate(linhas):
                        if i == len(linhas) - 1: l.vol_final = ajuste.novo_volume - soma_dist 
                        else:
                            peso = l.vol_final / total_base if total_base > 0 else 1.0 / len(linhas)
                            rateado = int(round(ajuste.novo_volume * peso))
                            l.vol_final = rateado
                            soma_dist += rateado
            elif ajuste.nivel == 'produto':
                linhas = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(
                    FatoIbpGranular.sku == ajuste.chave, FatoIbpGranular.mes_projetado == data_alvo, DimCliente.bloqueado != 'INATIVO'
                ).all()
                if not linhas: continue
                total_base = sum([l.vol_final for l in linhas])
                soma_dist = 0
                for i, l in enumerate(linhas):
                    if i == len(linhas) - 1: l.vol_final = ajuste.novo_volume - soma_dist
                    else:
                        peso = l.vol_final / total_base if total_base > 0 else 1.0 / len(linhas)
                        rateado = int(round(ajuste.novo_volume * peso))
                        l.vol_final = rateado
                        soma_dist += rateado

        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='S&OP-Final', status='Fechado'))
        else: 
            registro.status = 'Fechado'
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export")
async def exportar_excel_global(db: Session = Depends(get_db)):
    try:
        m_plus_2, m_plus_4 = get_projection_window()
        resultados = db.query(
            FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('vol_td'),
            func.sum(FatoIbpGranular.vol_bottomup).label('vol_bu'),
            func.sum(FatoIbpGranular.vol_final).label('vol_final'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('receita_base'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_simples'),
            DimProduto.descricao, DimProduto.categoria
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(
             func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2,
             func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4,
             DimCliente.bloqueado != 'INATIVO'
         ).group_by(
             FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado,
             DimProduto.descricao, DimProduto.categoria
         ).all()

        if not resultados:
            raise HTTPException(status_code=404, detail="Sem dados para exportar.")

        dados = []
        for r in resultados:
            vol_ia = int(r.vol_ia or 0)
            pmv_real = (float(r.receita_base or 0) / vol_ia) if vol_ia > 0 else float(r.pmv_simples or 0)
            dados.append({
                "Categoria": r.categoria, "SKU": r.sku, "Produto": r.descricao, "Cliente (Razão Social)": r.razaosocial,
                "Mês Projetado": r.mes_projetado.strftime("%m/%Y"), "Sinal IA (Base)": vol_ia,
                "Meta Gerencial": int(r.vol_td or 0), "Proposta Comercial": int(r.vol_bu or 0),
                "Demanda Irrestrita": int(r.vol_final or 0), "PMV Ponderado (R$)": round(pmv_real, 2),
                "Faturamento Irrestrito (R$)": int(r.vol_final or 0) * pmv_real
            })

        df = pd.DataFrame(dados)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Demanda_Irrestrita')
        
        buffer.seek(0)
        return StreamingResponse(
            buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            headers={"Content-Disposition": "attachment; filename=Demanda_Irrestrita_Oficial.xlsx"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))