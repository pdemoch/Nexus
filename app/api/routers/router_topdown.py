from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

# Importação do nosso "Cérebro" que garante a Fonte da Verdade
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_projection_window,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/macro", tags=["Consenso Top-Down"])

# =====================================================================
# PERMISSÕES
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito à Diretoria/Gerência.")
    return usuario

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso Restrito ao Administrador.")
    return usuario

# =====================================================================
# SCHEMAS (PAYLOADS)
# =====================================================================
class AjusteTopDown(BaseModel):
    produto: str
    mes_projetado: str
    novo_volume: int

class PayloadCongelar(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteTopDown]

# =====================================================================
# ENDPOINTS PRINCIPAIS (A VISÃO MACRO)
# =====================================================================
@router.get("")  # <-- CORREÇÃO AQUI: Sem a barra! Isso evita o bloqueio da AWS.
async def listar_macro(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()

        query_base = get_truth_query(db, ciclo, m2, m4)
        
        projecoes = query_base.with_entities(
            FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'), 
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_avg'), 
            func.sum(FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'), 
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento, 
            DimProduto.modelo_vencedor, DimProduto.acuracia_ia
        ).group_by(
            FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, 
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento, 
            DimProduto.modelo_vencedor, DimProduto.acuracia_ia
        ).all()

        prod_map = defaultdict(lambda: {"meses": []})
        for r in projecoes:
            p = prod_map[r.sku]
            if not p.get('produto'): 
                p.update({
                    "produto": r.sku, "descricao": r.descricao, "categoria": r.categoria, 
                    "segmento": r.segmento, "modelo_vencedor": r.modelo_vencedor or "N/A", 
                    "acuracia_ia": float(r.acuracia_ia or 0)
                })
            
            v_td = int(r.v_td or 0)
            rec_td = float(r.rec_td or 0)
            pmv_real = (rec_td / v_td) if v_td > 0 else float(r.pmv_avg or 0)
            
            p["meses"].append({
                "mes_banco": str(r.mes_projetado), 
                "mes_str": r.mes_projetado.strftime("%b/%y").capitalize() if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado), 
                "vol_ia": int(r.v_ia or 0), "vol_ajustado": v_td, "pmv": pmv_real, "receita": rec_td  
            })
            
        return {"status": "success", "dados": list(prod_map.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.post("/congelar")
async def congelar_macro(payload: PayloadCongelar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            
            # Buscar apenas os registros atômicos permitidos pela Regra de Ouro
            linhas = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo)).filter(
                FatoIbpGranular.sku == str(ajuste.produto)
            ).all()

            if not linhas: continue

            skus = list({l.sku for l in linhas if l.sku})
            cgcs = list({l.cgc for l in linhas if l.cgc})
            
            # 1. Puxa a representatividade histórica de clientes ativos para o rateio
            historico = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_hist'))\
                .filter(FatoVendas.sku.in_(skus), FatoVendas.cgc.in_(cgcs), FatoVendas.data_pedido >= data_limite_str)\
                .group_by(FatoVendas.sku, FatoVendas.cgc).all()
                
            dict_hist = {f"{h.sku}_{h.cgc}": float(h.vol_hist or 0) for h in historico}
            soma_hist = sum([dict_hist.get(f"{l.sku}_{l.cgc}", 0) for l in linhas])
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            # 2. Executa o rateio cascata
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    peso = dict_hist.get(f"{l.sku}_{l.cgc}", 0) / soma_hist if soma_hist > 0 else 1.0 / len(linhas)
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                    
                # Como é Top-Down, a alteração empurra as metas para a base (Comercial e Final)
                l.vol_topdown = rateado
                l.vol_bottomup = rateado
                l.vol_final = rateado

        # 3. Trava o Ciclo
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down', status='Fechado'))
        else: 
            registro.status = 'Fechado'
            
        db.commit()
        return {"status": "success"}
    except HTTPException as he: 
        raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))


@router.post("/reabrir")
async def reabrir_macro(db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        
        se_alguem_fechado = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo, 
            ControleCiclo.status == 'Fechado', 
            ControleCiclo.origem.notin_(['Top-Down', 'S&OP-Final'])
        ).count()
        
        if se_alguem_fechado > 0:
            raise HTTPException(403, "Ordem Reversa Violada. Destrave as carteiras dos vendedores primeiro.")
            
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if reg: 
            reg.status = 'Aberto'
            db.commit()
        return {"status": "success"}
    except HTTPException as e: 
        raise e
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))


@router.get("/grafico")
async def grafico_macro(produto: str, db: Session = Depends(get_db)):
    try:
        m2, m4 = get_projection_window()
        hoje = datetime.date.today()
        ciclo_anterior, ciclo_atual = get_previous_cycle(), get_current_cycle()
        
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol_real'))\
            .filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2), FatoVendas.sku == produto)
        
        q_ant = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_topdown).label('vol_ant'))\
            .filter(FatoIbpGranular.ciclo_sop == ciclo_anterior, FatoIbpGranular.sku == produto)
            
        q_proj = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), func.sum(FatoIbpGranular.vol_topdown).label('vol_consenso'))\
            .filter(FatoIbpGranular.ciclo_sop == ciclo_atual, FatoIbpGranular.sku == produto)

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist.group_by(func.to_char(FatoVendas.data_pedido, 'YYYY-MM')).all()}
        ant_dict = {p.mes_projetado.strftime('%Y-%m-%d') if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado): int(p.vol_ant or 0) for p in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        projecoes = q_proj.group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = [{"name": (hoje - relativedelta(months=i)).strftime("%b/%y").capitalize(), "data_iso": (hoje - relativedelta(months=i)).replace(day=1).strftime("%Y-%m-%d"), "Realizado": hist_dict.get((hoje - relativedelta(months=i)).strftime('%Y-%m'), 0), "IA": None, "Consenso": None, "CicloAnterior": None} for i in range(24, 0, -1)]
        
        for p in projecoes:
            p_str = p.mes_projetado.strftime('%Y-%m-%d') if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado)
            timeline.append({
                "name": p.mes_projetado.strftime("%b/%y").capitalize() if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado), 
                "data_iso": p_str, "Realizado": None, "IA": int(p.vol_ia or 0), 
                "Consenso": int(p.vol_consenso or 0) if m2 <= p_str <= m4 else None, 
                "CicloAnterior": ant_dict.get(p_str, None)
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))
    
@router.get("/status")
async def checar_status_macro(db: Session = Depends(get_db)):
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == 'Top-Down').first()
    return {"is_topdown_fechado": reg.status == 'Fechado' if reg else False}