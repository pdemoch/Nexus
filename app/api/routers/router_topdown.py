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

# Importação do Cérebro compartilhado
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
    if usuario['funcao'] not in ['Administrador', 'Gerente', 'Marketing']:
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
# ENDPOINTS PRINCIPAIS
# =====================================================================

@router.get("") 
async def listar_macro(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        # ATUALIZAÇÃO: Passando 'db' para as funções de tempo
        m2, m4 = get_projection_window(db)
        ciclo = get_current_cycle(db)

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
                "mes_str": r.mes_projetado.strftime("%b/%y").capitalize(), 
                "vol_ia": int(r.v_ia or 0), "vol_ajustado": v_td, "pmv": pmv_real, "receita": rec_td  
            })
            
        return {"status": "success", "dados": list(prod_map.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.get("/grafico")
async def grafico_macro(produto: str, db: Session = Depends(get_db)):
    try:
        # 1. Configuração de Janelas
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        
        # ATUALIZAÇÃO: Passando 'db' para as funções de tempo
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        m2, m4 = get_projection_window(db) 
        
        # 2. Queries de Dados
        # Busca o Histórico Real (Vendas)
        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), 
            func.sum(FatoVendas.qt_pedido).label('vol_real')
        ).filter(
            FatoVendas.data_pedido >= hoje - relativedelta(years=2), 
            FatoVendas.sku == produto
        ).group_by('mes_ano').all()

        # NOVO: Super Query! Busca TODO o histórico de projeções S&OP deste SKU
        q_all_ibp = db.query(
            FatoIbpGranular.ciclo_sop,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('vol_consenso')
        ).filter(
            FatoIbpGranular.sku == produto
        ).group_by(FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado).all()

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist}

        # Mapeamento 3D de alta performance: ibp_map[data_iso][ciclo] = volumes
        ibp_map = {}
        for r in q_all_ibp:
            d_iso = str(r.mes_projetado)
            if d_iso not in ibp_map: ibp_map[d_iso] = {}
            ibp_map[d_iso][r.ciclo_sop] = {
                'ia': int(r.vol_ia or 0),
                'consenso': int(r.vol_consenso or 0)
            }

        timeline = []

        # 3. CONSTRUÇÃO DA TIMELINE S&OE (O PASSADO)
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str = dt.strftime('%Y-%m')
            dt_iso = dt.strftime('%Y-%m-%d')
            
            # Qual era o ciclo ativo naquele exato mês no passado?
            ciclo_do_mes = dt.strftime('%m/%Y')
            # E qual era o ciclo do mês anterior a ele (Para a Proposta Antiga)?
            ciclo_mes_passado = (dt - relativedelta(months=1)).strftime('%m/%Y')
            
            # Buscamos as "fotos congeladas" do passado
            dados_mes = ibp_map.get(dt_iso, {}).get(ciclo_do_mes, {})
            dados_lag1 = ibp_map.get(dt_iso, {}).get(ciclo_mes_passado, {})
            
            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(),
                "data_iso": dt_iso,
                "Realizado": hist_dict.get(mes_str, 0),
                "IA": dados_mes.get('ia', None),
                "Consenso": None, # <-- ALTERAÇÃO AQUI: Forçamos None para ocultar a Meta Gerencial no passado
                "CicloAnterior": dados_lag1.get('consenso', None) 
            })

        # 4. CONSTRUÇÃO DA TIMELINE S&OE (O PRESENTE M0 E O FUTURO)
        mes_atual_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        dados_atual_m0 = ibp_map.get(mes_atual_iso, {}).get(ciclo_atual, {})
        
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)",
            "data_iso": mes_atual_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": dados_atual_m0.get('ia', 0), # Mudamos None para 0
            "Consenso": None, 
            "CicloAnterior": ibp_map.get(mes_atual_iso, {}).get(ciclo_anterior, {}).get('consenso', None)
        })

        # --- A MÁGICA: Régua Fixa de M1 a M4 ---
        # Garante que o eixo X nunca quebre, mesmo se a IA previu 0 e o DB não salvou.
        for i in range(1, 5):
            p_date = mes_atual_inicio + relativedelta(months=i)
            p_iso = p_date.strftime('%Y-%m-%d')
            
            dados_futuro = ibp_map.get(p_iso, {}).get(ciclo_atual, {})
            
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(),
                "data_iso": p_iso,
                "Realizado": None,
                "IA": dados_futuro.get('ia', 0), # Força 0 para desenhar a linha caindo
                "Consenso": dados_futuro.get('consenso', 0) if p_date >= m2 else None,
                "CicloAnterior": ibp_map.get(p_iso, {}).get(ciclo_anterior, {}).get('consenso', None)
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.post("/congelar")
async def congelar_macro(payload: PayloadCongelar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        # ATUALIZAÇÃO: Passando 'db' para as funções de tempo
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            linhas = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(
                FatoIbpGranular.sku == str(ajuste.produto)
            ).all()

            if not linhas: continue

            # Faz 1 única query global agrupada pelo CGC em vez de 1 query por cliente
            historico_agrupado = db.query(
                FatoVendas.cgc, 
                func.sum(FatoVendas.qt_pedido).label('vol_cli')
            ).filter(
                FatoVendas.sku == ajuste.produto, 
                FatoVendas.data_pedido >= data_limite_str
            ).group_by(FatoVendas.cgc).all()

            # Transforma em dicionário na RAM para busca instantânea O(1)
            mapa_hist = {h.cgc: float(h.vol_cli or 0) for h in historico_agrupado}
            soma_hist = sum(mapa_hist.values())
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    # Busca o histórico do cliente diretamente no dicionário na memória
                    vol_cli = mapa_hist.get(l.cgc, 0.0)
                    
                    peso = vol_cli / soma_hist if soma_hist > 0 else 1.0 / len(linhas)
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                    
                # A CASCATA DE HERANÇA CORRIGIDA:
                l.vol_topdown = rateado
                l.vol_bottomup = rateado
                l.vol_supply = rateado
                l.vol_final = rateado
                l.vol_meta = rateado

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down', status='Fechado'))
        else: 
            registro.status = 'Fechado'
            
        db.commit()
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/status")
async def checar_status_macro(db: Session = Depends(get_db)):
    # ATUALIZAÇÃO: Passando 'db' para a função get_current_cycle
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(db), ControleCiclo.origem == 'Top-Down').first()
    return {"is_topdown_fechado": reg.status == 'Fechado' if reg else False}