from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_projection_window, get_truth_query, check_global_lock, parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/supply", tags=["Consenso Supply Review"])

# =====================================================================
# PERMISSÕES
# =====================================================================
def require_supply_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Supply Chain']:
        raise HTTPException(status_code=403, detail="Acesso Restrito ao time de Supply Chain.")
    return usuario

# =====================================================================
# SCHEMAS
# =====================================================================
class AjusteSupply(BaseModel):
    produto: str
    mes_projetado: str
    novo_volume: int
    justificativa: Optional[str] = None

class PayloadCongelarSupply(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteSupply]

# =====================================================================
# ENDPOINTS
# =====================================================================
@router.get("/status")
async def checar_status_supply(db: Session = Depends(get_db)):
    reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == 'Top-Down').first()
    reg_sp = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == 'Supply Review').first()
    
    return {
        "is_topdown_fechado": reg_td.status == 'Fechado' if reg_td else False,
        "is_supply_fechado": reg_sp.status == 'Fechado' if reg_sp else False
    }

@router.get("") 
async def listar_supply(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()
        query_base = get_truth_query(db, ciclo, m2, m4)
        
        projecoes = query_base.with_entities(
            FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'), # Referência agora é o TopDown
            func.sum(FatoIbpGranular.vol_supply).label('v_sp'), 
            func.max(FatoIbpGranular.justificativa_supply).label('justificativa'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_avg'), 
            func.sum(FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_sp'), 
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento
        ).group_by(
            FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, 
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento
        ).all()

        prod_map = defaultdict(lambda: {"meses": []})
        for r in projecoes:
            p = prod_map[r.sku]
            if not p.get('produto'): 
                p.update({
                    "produto": r.sku, "descricao": r.descricao, "categoria": r.categoria, "segmento": r.segmento
                })
            
            v_sp = int(r.v_sp or 0)
            rec_sp = float(r.rec_sp or 0)
            pmv_real = (rec_sp / v_sp) if v_sp > 0 else float(r.pmv_avg or 0)
            
            p["meses"].append({
                "mes_banco": str(r.mes_projetado), 
                "mes_str": r.mes_projetado.strftime("%b/%y").capitalize(), 
                "vol_ref": int(r.v_td or 0), # Mostra o TopDown como "Base"
                "vol_ajustado": v_sp, 
                "justificativa": r.justificativa or "",
                "pmv": pmv_real, 
                "receita": rec_sp  
            })
            
        return {"status": "success", "dados": list(prod_map.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_supply(payload: PayloadCongelarSupply, db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        
        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="O Top-Down Comercial ainda não foi congelado. Aguarde a liberação da Diretoria.")

        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            linhas = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == str(ajuste.produto)).all()

            if not linhas: continue

            historico_agrupado = db.query(FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_cli'))\
                .filter(FatoVendas.sku == ajuste.produto, FatoVendas.data_pedido >= data_limite_str)\
                .group_by(FatoVendas.cgc).all()

            mapa_hist = {h.cgc: float(h.vol_cli or 0) for h in historico_agrupado}
            soma_hist = sum(mapa_hist.values())
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    vol_cli = mapa_hist.get(l.cgc, 0.0)
                    peso = vol_cli / soma_hist if soma_hist > 0 else 1.0 / len(linhas)
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                    
                l.vol_supply = rateado
                l.justificativa_supply = ajuste.justificativa
                # Efeito Cascata: Sobrescreve as fases sucessoras
                l.vol_bottomup = rateado
                l.vol_final = rateado

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Supply Review').first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='Supply Review', status='Fechado'))
        else: 
            registro.status = 'Fechado'
            
        db.commit()
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))