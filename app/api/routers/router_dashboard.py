from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
import io
import pandas as pd
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo
from app.api.routers.router_auth import get_current_user

# Importamos o nosso Cérebro de Dados
from app.api.routers.shared_ibp import get_current_cycle, get_projection_window, get_truth_query, parse_date_safe

router = APIRouter(prefix="/api/v1/dashboard", tags=["S&OP Global Dashboard"])

class AjusteGlobal(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGlobal(BaseModel):
    ajustes: List[AjusteGlobal]

@router.get("/global")
async def carregar_dashboard_global(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    # ATUALIZADO: Adicionado 'Supply Chain' à permissão de visualização
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente', 'Supply Chain']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria, Gerência ou Supply Chain.")
        
    try:
        ciclo = get_current_cycle()
        m_plus_2, m_plus_4 = get_projection_window()

        status_global = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()
        is_global_fechado = status_global.status == 'Fechado' if status_global else False
        
        status_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        is_td_fechado = status_td.status == 'Fechado' if status_td else False

        query_base = get_truth_query(db, ciclo, m_plus_2, m_plus_4)

        vendedores_ativos = query_base.with_entities(FatoIbpGranular.vendedor_nome).filter(FatoIbpGranular.vendedor_nome.isnot(None)).distinct().all()
        v_ativos = [v[0].strip() for v in vendedores_ativos if v[0] and v[0].strip()]
        
        vendedores_fechados = db.query(ControleCiclo.origem).filter(
            ControleCiclo.ciclo_sop == ciclo, ControleCiclo.status == 'Fechado', ControleCiclo.origem.notin_(['Top-Down', 'Supply Review', 'S&OP-Final'])
        ).all()
        v_fechados = [v[0].strip() for v in vendedores_fechados if v[0]]

        pendentes = [v for v in v_ativos if v not in v_fechados]

        if is_global_fechado: is_locked, lock_message = True, "Demanda Irrestrita Publicada"
        elif not is_td_fechado: is_locked, lock_message = True, "Aguardando Visão Gerencial"
        elif pendentes: is_locked, lock_message = True, f"Aguardando {len(pendentes)} Vendedor(es)"
        else: is_locked, lock_message = False, "Publicar Demanda Irrestrita"
            
        resultados = query_base.with_entities(
            FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('vol_td'),
            func.sum(FatoIbpGranular.vol_supply).label('vol_supply'), 
            func.sum(FatoIbpGranular.vol_bottomup).label('vol_bu'),
            func.sum(FatoIbpGranular.vol_final).label('vol_final'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            func.sum(FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            func.sum(FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_supply'), 
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            func.sum(FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado).label('rec_final'),
            DimProduto.descricao, DimProduto.categoria
        ).group_by(
            FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado, DimProduto.descricao, DimProduto.categoria
        ).all()

        dados_formatados = []
        for r in resultados:
            dados_formatados.append({
                "chave_matriz": f"{r.sku}_{r.razaosocial}", "categoria": r.categoria, "produto": r.sku,
                "descricao": r.descricao, "cliente_razaosocial": r.razaosocial, 
                "mes_projetado": r.mes_projetado.strftime("%Y-%m-%d") if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado),
                "vol_ia": int(r.vol_ia or 0), "vol_td": int(r.vol_td or 0), "vol_supply": int(r.vol_supply or 0), "vol_bu": int(r.vol_bu or 0), "vol_irrestrito": int(r.vol_final or 0), 
                "rec_ia": float(r.rec_ia or 0), "rec_td": float(r.rec_td or 0), "rec_supply": float(r.rec_supply or 0), "rec_bu": float(r.rec_bu or 0), "rec_final": float(r.rec_final or 0)
            })

        return {"status": "success", "is_locked": is_locked, "lock_message": lock_message, "dados": dados_formatados}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/aprovar")
async def aprovar_dashboard_global(payload: PayloadAprovarGlobal, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    # A aprovação continua restrita a Admin/Gerente por governança
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Apenas a Gerência pode publicar o plano final.")
    try:
        ciclo = get_current_cycle()
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()
        if registro and registro.status == 'Fechado': raise HTTPException(status_code=403, detail="O Ciclo já está fechado.")

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            query = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo))

            if ajuste.nivel == 'cliente':
                partes = ajuste.chave.split('_', 1)
                sku, razaosocial = partes[0], partes[1] if len(partes) > 1 else ""
                # Corrigido para retornar o objeto e permitir a edição do vol_final
                linhas = query.filter(FatoIbpGranular.sku == sku, func.trim(DimCliente.razaosocial) == razaosocial.strip()).all()
            elif ajuste.nivel == 'produto':
                linhas = query.filter(FatoIbpGranular.sku == ajuste.chave).all()
            else: continue

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

        if not registro: db.add(ControleCiclo(ciclo_sop=ciclo, origem='S&OP-Final', status='Fechado'))
        else: registro.status = 'Fechado'
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export")
async def exportar_excel_global(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    # ATUALIZADO: Permissão para Supply Chain exportar
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente', 'Supply Chain']: 
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    try:
        ciclo = get_current_cycle()
        m_plus_2, m_plus_4 = get_projection_window()
        
        resultados = get_truth_query(db, ciclo, m_plus_2, m_plus_4).with_entities(
            FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), 
            func.sum(FatoIbpGranular.vol_topdown).label('vol_td'),
            func.sum(FatoIbpGranular.vol_supply).label('vol_supply'), 
            func.sum(FatoIbpGranular.vol_bottomup).label('vol_bu'), 
            func.sum(FatoIbpGranular.vol_final).label('vol_final'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'), 
            func.sum(FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            func.sum(FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_supply'), 
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'), 
            func.sum(FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado).label('rec_final'),
            DimProduto.descricao, DimProduto.categoria
        ).group_by(FatoIbpGranular.sku, DimCliente.razaosocial, FatoIbpGranular.mes_projetado, DimProduto.descricao, DimProduto.categoria).all()

        if not resultados: raise HTTPException(status_code=404, detail="Sem dados para exportar.")

        dados = []
        for r in resultados:
            dados.append({
                "Categoria": r.categoria, "SKU": r.sku, "Produto": r.descricao, "Cliente": r.razaosocial,
                "Mês Projetado": r.mes_projetado.strftime("%m/%Y") if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado), 
                "Sinal IA (Base)": int(r.vol_ia or 0), "Receita IA (R$)": float(r.rec_ia or 0),
                "Meta Gerencial": int(r.vol_td or 0), "Receita Gerencial (R$)": float(r.rec_td or 0),
                "Meta Restrita (Supply)": int(r.vol_supply or 0), "Receita Supply (R$)": float(r.rec_supply or 0), 
                "Proposta Comercial": int(r.vol_bu or 0), "Receita Comercial (R$)": float(r.rec_bu or 0),
                "Demanda Irrestrita": int(r.vol_final or 0), "Faturamento Irrestrito (R$)": float(r.rec_final or 0)
            })

        df = pd.DataFrame(dados)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Demanda_Irrestrita')
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=Demanda_Irrestrita_Oficial.xlsx"})
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))