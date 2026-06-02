import datetime
import math
from typing import List
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas, DimCliente
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/macro", tags=["Consenso Top-Down"])

class AjusteTopDown(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarTopDown(BaseModel):
    ajustes: List[AjusteTopDown]

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria.")
    return usuario

@router.get("")
async def listar_topdown(categoria_filtro: str = None, valor_filtro: str = None, db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        # MÁQUINA DO TEMPO: O "Hoje" respeita o relógio global
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        
        # S&OP Clássico: M2 a M4 (3 meses) para a Diretoria (Top-Down)
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        q = get_truth_query(db, ciclo, data_ini, data_fim)
        
        if categoria_filtro == 'categoria' and valor_filtro:
            q = q.filter(func.upper(func.trim(DimProduto.categoria)) == valor_filtro.strip().upper())
        elif categoria_filtro == 'segmento' and valor_filtro:
            q = q.filter(func.upper(func.trim(DimProduto.segmento)) == valor_filtro.strip().upper())

        resultados = q.with_entities(
            FatoIbpGranular.sku,
            DimProduto.descricao,
            DimProduto.categoria,
            DimProduto.segmento,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(FatoIbpGranular.sku, DimProduto.descricao, DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.mes_projetado).all()

        # Extração Rápida do Ciclo Anterior (Lag 1) para a Tabela
        skus_encontrados = list({r.sku for r in resultados if r.sku})
        ant_dict = defaultdict(lambda: defaultdict(int))
        
        if skus_encontrados:
            q_ant = get_truth_query(db, ciclo_anterior, data_ini, data_fim)
            res_ant = q_ant.filter(FatoIbpGranular.sku.in_(skus_encontrados)).with_entities(
                FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_final).label('v_ant')
            ).group_by(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado).all()
            
            for ra in res_ant:
                ant_dict[ra.sku][str(ra.mes_projetado)] = int(ra.v_ant or 0)

        arvore = {}
        def criar_meses():
            return {m: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0.0, "vol_anterior": 0} for m in meses_alvo}

        for r in resultados:
            cat = str(r.categoria).strip() if r.categoria else "SEM CATEGORIA"
            seg = str(r.segmento).strip() if r.segmento else "SEM SEGMENTO"
            sku = str(r.sku).strip()
            desc = str(r.descricao).strip()
            ms = str(r.mes_projetado)

            if cat not in arvore: arvore[cat] = {"nome": cat, "tipo": "categoria", "meses": criar_meses(), "segmentos": {}}
            if seg not in arvore[cat]["segmentos"]: arvore[cat]["segmentos"][seg] = {"nome": seg, "tipo": "segmento", "meses": criar_meses(), "produtos": {}}
            if sku not in arvore[cat]["segmentos"][seg]["produtos"]: arvore[cat]["segmentos"][seg]["produtos"][sku] = {"nome": desc, "produto": sku, "tipo": "produto", "meses": criar_meses()}

            if ms in meses_alvo:
                v_td = int(r.v_td or 0)
                pmv_item = float(r.pmv or 0)
                v_ant = ant_dict[sku][ms]

                for nivel in [arvore[cat]["meses"][ms], arvore[cat]["segmentos"][seg]["meses"][ms], arvore[cat]["segmentos"][seg]["produtos"][sku]["meses"][ms]]:
                    nivel["vol_ia"] += int(r.v_ia or 0)
                    nivel["vol_td"] += v_td
                    nivel["vol_ajustado"] += v_td
                    nivel["receita"] += (v_td * pmv_item)
                    nivel["vol_anterior"] += v_ant
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_item

        final = []
        for cat_k, cat_v in arvore.items():
            segs = []
            for seg_k, seg_v in cat_v["segmentos"].items():
                prods = []
                for p_k, p_v in seg_v["produtos"].items():
                    prods.append({"id": f"{cat_k}|{seg_k}|{p_k}", "chave_matriz": p_k, "nome": p_v["nome"], "produto": p_k, "tipo": "produto", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in p_v["meses"].items()]})
                segs.append({"id": f"{cat_k}|{seg_k}", "chave_matriz": f"{cat_k}|{seg_k}", "nome": seg_k, "tipo": "segmento", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in seg_v["meses"].items()], "subRows": prods})
            final.append({"id": cat_k, "chave_matriz": cat_k, "nome": cat_k, "tipo": "categoria", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cat_v["meses"].items()], "subRows": segs})

        return {"status": "success", "dados": sorted(final, key=lambda x: x["nome"])}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/status")
async def status_topdown(db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)
        status = db.query(ControleCiclo.status).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').scalar()
        return {"is_fechado": status == 'Fechado'}
    except:
        return {"is_fechado": True}

# =========================================================
# FUNÇÃO COMPARTILHADA: Rateio Histórico para S&OP
# =========================================================
def aplicar_rateio_topdown(db: Session, payload: PayloadAprovarTopDown, ciclo: str):
    _mes_str, _ano_str = ciclo.split('/')
    hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
    data_hist = hoje - relativedelta(months=12)

    for ajuste in payload.ajustes:
        dt_mes = parse_date_safe(ajuste.mes_projetado)
        sku_alvo = ajuste.sku

        hist_data = db.query(
            FatoVendas.cgc,
            func.sum(FatoVendas.qt_pedido).label('vol_hist'),
            func.sum(FatoVendas.vl_pedido).label('rec_hist')
        ).filter(
            FatoVendas.sku == sku_alvo,
            FatoVendas.data_pedido >= data_hist
        ).group_by(FatoVendas.cgc).all()

        peso_cliente = {}
        pmv_cliente = {}
        total_hist = sum(r.vol_hist for r in hist_data if r.vol_hist)
        
        for r in hist_data:
            vol = float(r.vol_hist or 0)
            rec = float(r.rec_hist or 0)
            if vol > 0:
                peso_cliente[r.cgc] = vol / total_hist
                pmv_cliente[r.cgc] = rec / vol 

        linhas = db.query(FatoIbpGranular).filter(
            FatoIbpGranular.ciclo_sop == ciclo,
            FatoIbpGranular.mes_projetado == dt_mes,
            FatoIbpGranular.sku == sku_alvo
        ).all()

        if not linhas: continue

        total_atual = sum(l.vol_topdown for l in linhas)
        delta = ajuste.novo_volume - total_atual

        linhas.sort(key=lambda x: peso_cliente.get(x.cgc, 0), reverse=True)

        if delta != 0:
            for i, l in enumerate(linhas):
                peso = peso_cliente.get(l.cgc, 1/len(linhas) if total_hist == 0 else 0)
                inc = int(round(delta * peso))
                if i == len(linhas) - 1:
                    inc = delta - sum(int(round(delta * peso_cliente.get(x.cgc, 1/len(linhas) if total_hist == 0 else 0))) for x in linhas[:-1])
                
                novo_vol = max(0, l.vol_topdown + inc)
                l.vol_topdown = novo_vol
                l.vol_bottomup = novo_vol 
                l.vol_supply = novo_vol
                l.vol_final = novo_vol

        for l in linhas:
            if l.cgc in pmv_cliente and pmv_cliente[l.cgc] > 0:
                l.pmv_aplicado = pmv_cliente[l.cgc]

# =========================================================
# ENDPOINTS
# =========================================================
@router.post("/salvar")
async def salvar_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        
        # Aplica os volumes, mas NÃO altera o ControleCiclo para "Fechado"
        aplicar_rateio_topdown(db, payload, ciclo)
        
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def aprovar_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        # Aplica os volumes
        aplicar_rateio_topdown(db, payload, ciclo)

        # Trava o ciclo
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if reg: 
            reg.status = 'Fechado'
        else: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down', status='Fechado'))

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_topdown(chave_matriz: str, db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        _mes_str, _ano_str = ciclo_atual.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        
        data_ini = hoje - relativedelta(months=6)
        data_fim = hoje + relativedelta(months=6)
        
        sku_alvo = chave_matriz.split('|')[-1]
        
        # Histórico de Vendas
        vendas = db.query(
            func.date_trunc('month', FatoVendas.data_pedido).label('mes'),
            func.sum(FatoVendas.qt_pedido).label('volume')
        ).filter(
            FatoVendas.sku == sku_alvo,
            FatoVendas.data_pedido >= data_ini,
            FatoVendas.data_pedido < hoje
        ).group_by('mes').all()

        # Projeções IBP
        ibp = db.query(
            FatoIbpGranular.mes_projetado,
            FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('td'),
            func.sum(FatoIbpGranular.vol_final).label('final')
        ).filter(
            FatoIbpGranular.sku == sku_alvo,
            FatoIbpGranular.mes_projetado >= data_ini,
            FatoIbpGranular.mes_projetado <= data_fim
        ).group_by(FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop).all()

        proj_por_mes = defaultdict(lambda: defaultdict(dict))
        for r in ibp:
            mes_str = r.mes_projetado.strftime('%Y-%m')
            proj_por_mes[mes_str][r.ciclo_sop] = {
                "ia": float(r.ia or 0),
                "td": float(r.td or 0),
                "final": float(r.final or 0)
            }

        calendario = {}
        curr = data_ini
        while curr <= data_fim:
            ms = curr.strftime('%Y-%m')
            calendario[ms] = {"Realizado": 0, "IA": None, "CicloAnterior": None, "TopDown": None}
            curr += relativedelta(months=1)

        for v in vendas:
            ms = v.mes.strftime('%Y-%m')
            if ms in calendario:
                calendario[ms]["Realizado"] = float(v.volume or 0)

        for ms in calendario.keys():
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            if ms in proj_por_mes:
                if ciclo_atual in proj_por_mes[ms]:
                    calendario[ms]["IA"] = proj_por_mes[ms][ciclo_atual].get("ia")
                    calendario[ms]["TopDown"] = proj_por_mes[ms][ciclo_atual].get("td")
                if ciclo_anterior in proj_por_mes[ms]:
                    calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_anterior].get("final")

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            timeline.append({
                "name": ms,
                "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt >= hoje else (v["Realizado"] or 0),
                "IA": v["IA"],
                "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"]
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))