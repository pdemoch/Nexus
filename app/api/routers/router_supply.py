from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo
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
    # ATUALIZAÇÃO: Passando 'db'
    ciclo = get_current_cycle(db)
    m2, m4 = get_projection_window(db)
    
    reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
    is_td_fechado = reg_td.status == 'Fechado' if reg_td else False

    query_base = get_truth_query(db, ciclo, m2, m4)
    vendedores_ativos = query_base.with_entities(FatoIbpGranular.vendedor_nome).filter(FatoIbpGranular.vendedor_nome.isnot(None)).distinct().all()
    v_ativos = [v[0].strip() for v in vendedores_ativos if v[0] and v[0].strip()]
    
    vendedores_fechados = db.query(ControleCiclo.origem).filter(
        ControleCiclo.ciclo_sop == ciclo, ControleCiclo.status == 'Fechado', ControleCiclo.origem.notin_(['Top-Down', 'Supply Review', 'S&OP-Final'])
    ).all()
    v_fechados = [v[0].strip() for v in vendedores_fechados if v[0]]
    
    pendentes = [v for v in v_ativos if v not in v_fechados]
    is_fase2_fechada = len(pendentes) == 0

    reg_sp = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Supply Review').first()
    
    return {
        "is_topdown_fechado": is_td_fechado,
        "is_fase2_fechada": is_fase2_fechada,
        "qtd_pendentes": len(pendentes),
        "is_supply_fechado": reg_sp.status == 'Fechado' if reg_sp else False
    }

@router.get("") 
async def listar_supply(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    try:
        # ATUALIZAÇÃO: Passando 'db'
        m2, m4 = get_projection_window(db)
        ciclo = get_current_cycle(db)
        
        reg_sp = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Supply Review').first()
        is_supply_fechado = reg_sp.status == 'Fechado' if reg_sp else False

        query_base = get_truth_query(db, ciclo, m2, m4)
        
        projecoes = query_base.with_entities(
            FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_supply).label('v_sp'), 
            func.max(FatoIbpGranular.justificativa_supply).label('justificativa'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_avg'), 
            func.sum(FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_sp'), 
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'), 
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
            
            v_bu = int(r.v_bu or 0)
            v_sp_banco = int(r.v_sp or 0)
            
            v_exibido = v_sp_banco if is_supply_fechado else v_bu
            
            rec_sp_banco = float(r.rec_sp or 0)
            rec_bu_banco = float(r.rec_bu or 0)
            rec_exibida = rec_sp_banco if is_supply_fechado else rec_bu_banco
            
            pmv_real = (rec_exibida / v_exibido) if v_exibido > 0 else float(r.pmv_avg or 0)
            
            p["meses"].append({
                "mes_banco": str(r.mes_projetado), 
                "mes_str": r.mes_projetado.strftime("%b/%y").capitalize(), 
                "vol_ref": v_bu, 
                "vol_ajustado": v_exibido, 
                "justificativa": r.justificativa or "",
                "pmv": pmv_real, 
                "receita": rec_exibida  
            })
            
        return {"status": "success", "dados": list(prod_map.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_supply(payload: PayloadCongelarSupply, db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    try:
        # ATUALIZAÇÃO: Passando 'db'
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        
        m2, m4 = get_projection_window(db)
        v_ativos_tuples = get_truth_query(db, ciclo, m2, m4).with_entities(FatoIbpGranular.vendedor_nome).filter(FatoIbpGranular.vendedor_nome.isnot(None)).distinct().all()
        v_ativos = [v[0].strip() for v in v_ativos_tuples if v[0]]
        
        v_fechados_tuples = db.query(ControleCiclo.origem).filter(
            ControleCiclo.ciclo_sop == ciclo, 
            ControleCiclo.status == 'Fechado', 
            ControleCiclo.origem.notin_(['Top-Down', 'Supply Review', 'S&OP-Final'])
        ).all()
        v_fechados = [v[0].strip() for v in v_fechados_tuples if v[0]]
        
        pendentes = [v for v in v_ativos if v not in v_fechados]
        if pendentes:
            raise HTTPException(status_code=403, detail=f"A Fase Comercial ainda não foi concluída. Faltam {len(pendentes)} equipes trancarem as carteiras.")

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Supply Review').first()
        
        if not registro: 
            # A CASCATA INICIAL DO PULO DO GATO ATUALIZADA
            db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo).update({
                FatoIbpGranular.vol_supply: FatoIbpGranular.vol_bottomup,
                FatoIbpGranular.vol_final: FatoIbpGranular.vol_bottomup,
                FatoIbpGranular.vol_meta: FatoIbpGranular.vol_bottomup
            }, synchronize_session=False)
            db.commit()

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            linhas = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == str(ajuste.produto)).all()

            if not linhas: continue

            soma_bu_total = sum([float(l.vol_bottomup or 0) for l in linhas])
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    if soma_bu_total > 0:
                        peso = float(l.vol_bottomup or 0) / soma_bu_total
                    else:
                        peso = 1.0 / len(linhas) 
                        
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                    
                l.vol_supply = rateado
                l.justificativa_supply = ajuste.justificativa
                
                # A CASCATA DE HERANÇA CORRIGIDA:
                l.vol_final = rateado 
                l.vol_meta = rateado

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