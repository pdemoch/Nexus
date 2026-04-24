from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_projection_window, get_truth_query, check_global_lock, parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/supply", tags=["Consenso Supply Review"])

def require_supply_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Supply Chain']:
        raise HTTPException(status_code=403, detail="Acesso Restrito ao time de Supply Chain.")
    return usuario

class AjusteSupply(BaseModel):
    produto: str
    mes_projetado: str
    novo_volume: int
    justificativa: Optional[str] = None

class PayloadCongelarSupply(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteSupply]

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
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), # Referência do Supply agora é o Bottom-Up!
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
                "vol_ref": int(r.v_bu or 0), # Front-end verá a intenção do comercial
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
        
        # Garante que pelo menos o macro começou. Não trava por Vendedor pois eles trancam individualmente.
        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="O Ciclo ainda não foi iniciado pela Diretoria.")

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            linhas = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == str(ajuste.produto)).all()

            if not linhas: continue

            # ---------------------------------------------------------------------
            # NOVA REGRA DE RATEIO (FASE 3): Pesa pela Intenção Comercial (Bottom-Up)
            # ---------------------------------------------------------------------
            soma_bu_total = sum([float(l.vol_bottomup or 0) for l in linhas])
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    # Proteção contra "Divisão por Zero" (Caso o comercial tenha zerado tudo)
                    if soma_bu_total > 0:
                        peso = float(l.vol_bottomup or 0) / soma_bu_total
                    else:
                        peso = 1.0 / len(linhas) # Fallback: divide igual
                        
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                    
                l.vol_supply = rateado
                l.justificativa_supply = ajuste.justificativa
                
                # IMPORTANTE: vol_bottomup NÃO É SOBRESCRITO AQUI! Fica preservado.
                l.vol_final = rateado # O Final herda do Supply

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