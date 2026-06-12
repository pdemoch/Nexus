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
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/gerenciamento", tags=["Consenso Gerenciamento"])

# =====================================================================
# PAYLOADS & SCHEMAS
# =====================================================================
class AjusteGerente(BaseModel):
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGerente(BaseModel):
    origem_ajuste: str
    visao: str = 'portfolio'
    ajustes: List[AjusteGerente]

def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gerência Comercial.")
    return usuario

def check_topdown_lock(db: Session, ciclo: str):
    td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
    if not td or td.status != 'Fechado':
        raise HTTPException(
            status_code=403, 
            detail="Fase Comercial Bloqueada: A Diretoria ainda não liberou o ciclo para a modelagem Bottom-Up comercial."
        )

# =====================================================================
# GET: ÁRVORE DE DADOS (PORTFÓLIO GLOBAL)
# =====================================================================
@router.get("")
async def listar_gerenciamento(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        td_reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
        is_topdown_fechado = True if (td_reg and td_reg.status == 'Fechado') else False
        
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        # Extração de Peso Histórico para o Frontend fatiar Categorias e Segmentos
        data_hist_inicio = hoje - relativedelta(months=12)
        q_peso = db.query(FatoVendas.sku, func.sum(FatoVendas.qt_pedido).label('v')).filter(FatoVendas.data_pedido >= data_hist_inicio).group_by(FatoVendas.sku).all()
        peso_hist_dict = {str(r.sku).strip(): int(r.v or 0) for r in q_peso}

        # QUERY PORTFÓLIO (GLOBAL E VOL_META PARA O ORÇAMENTO)
        q_port = get_truth_query(db, ciclo, data_ini, data_fim)
        resultados_port = q_port.with_entities(
            DimProduto.categoria.label('cat'), DimProduto.segmento.label('seg'),
            FatoIbpGranular.sku, DimProduto.descricao.label('prod_desc'),
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_meta).label('v_meta'), # Orçamento Real
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all()

        # Extração de Lag 1 (Ciclo Anterior)
        ant_dict_port = defaultdict(lambda: defaultdict(int))
        skus_port = list({r.sku for r in resultados_port if r.sku})
        if skus_port:
            q_ant_port = get_truth_query(db, ciclo_anterior, data_ini, data_fim).filter(FatoIbpGranular.sku.in_(skus_port))
            for ra in q_ant_port.with_entities(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_final).label('v_ant')).group_by(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado).all():
                ant_dict_port[str(ra.sku).strip()][str(ra.mes_projetado)] = int(ra.v_ant or 0)

        def criar_meses(): return {"vol_ia":0, "vol_base":0, "receita_base":0, "vol_ajustado":0, "receita":0, "receita_orcamento":0, "pmv":0.0, "vol_anterior": 0}

        # MONTAGEM DA ÁRVORE PORTFÓLIO
        arvore_port = {}
        for p in db.query(DimProduto).all():
            cat, seg, sk, de = p.categoria or 'SEM CATEGORIA', p.segmento or 'SEM SEGMENTO', p.sku, p.descricao or 'SEM NOME'
            if cat not in arvore_port: arvore_port[cat] = {"nome": cat, "tipo": "categoria", "meses": {m: criar_meses() for m in meses_alvo}, "segmentos": {}}
            if seg not in arvore_port[cat]["segmentos"]: arvore_port[cat]["segmentos"][seg] = {"nome": seg, "tipo": "segmento", "meses": {m: criar_meses() for m in meses_alvo}, "produtos": {}}
            if sk not in arvore_port[cat]["segmentos"][seg]["produtos"]: arvore_port[cat]["segmentos"][seg]["produtos"][sk] = {"nome": de, "produto": sk, "tipo": "produto", "meses": {m: criar_meses() for m in meses_alvo}}

        for r in resultados_port:
            cat, seg, sk, ms = r.cat or 'SEM CATEGORIA', r.seg or 'SEM SEGMENTO', str(r.sku).strip(), str(r.mes_projetado)
            if ms in meses_alvo and cat in arvore_port and seg in arvore_port[cat]["segmentos"] and sk in arvore_port[cat]["segmentos"][seg]["produtos"]:
                v_bu = int(r.v_bu or 0)
                v_td = int(r.v_td or 0)
                v_meta = int(r.v_meta or 0)
                pmv_b = float(r.pmv or 0)
                v_ant = ant_dict_port[sk][ms]
                for nivel in [arvore_port[cat]["meses"][ms], arvore_port[cat]["segmentos"][seg]["meses"][ms], arvore_port[cat]["segmentos"][seg]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_ia"] += int(r.v_ia or 0)
                    nivel["vol_base"] += v_td
                    nivel["receita_base"] += (v_td * pmv_b)
                    nivel["vol_ajustado"] += v_bu
                    nivel["receita"] += (v_bu * pmv_b)
                    nivel["receita_orcamento"] += (v_meta * pmv_b)
                    nivel["vol_anterior"] += v_ant
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_b

        final_portfolio = []
        for cat_k, cat_v in arvore_port.items():
            segs = []
            for seg_k, seg_v in cat_v["segmentos"].items():
                prods = [{"id": f"{cat_k}|{seg_k}|{sk_k}", "chave_matriz": f"{cat_k}|{seg_k}|{sk_k}", "nome": sk_v["nome"], "produto": sk_k, "tipo": "produto", "vol_historico_mix": peso_hist_dict.get(sk_k, 1), "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in sk_v["meses"].items()]} for sk_k, sk_v in seg_v["produtos"].items()]
                segs.append({"id": f"{cat_k}|{seg_k}", "chave_matriz": f"{cat_k}|{seg_k}", "nome": seg_k, "tipo": "segmento", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in seg_v["meses"].items()], "subRows": prods})
            final_portfolio.append({"id": cat_k, "chave_matriz": cat_k, "nome": cat_k, "tipo": "categoria", "status": "Aberto", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cat_v["meses"].items()], "subRows": segs})

        # Retorna apenas o portfólio, perfeitamente compatível com o novo frontend
        return {"status": "success", "is_topdown_fechado": is_topdown_fechado, "dados": {"portfolio": sorted(final_portfolio, key=lambda x: x["nome"])}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=repr(e))


# =====================================================================
# POST: SALVAR RASCUNHO (RATEIO ABSOLUTO DO PORTFÓLIO)
# =====================================================================
@router.post("/salvar")
async def salvar_rascunho_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        check_topdown_lock(db, ciclo)

        hoje = datetime.date.today()
        data_hist = hoje - relativedelta(months=12)

        if not payload.ajustes:
            return {"status": "success", "message": "Nenhuma alteração enviada."}

        meses_alvos = list({parse_date_safe(a.mes_projetado) for a in payload.ajustes})
        skus_alvos = list({a.chave.split('|')[-1].strip() for a in payload.ajustes})

        # Captura todas as linhas impactadas pelos SKUs
        todas_linhas = db.query(FatoIbpGranular).filter(
            FatoIbpGranular.ciclo_sop == ciclo,
            FatoIbpGranular.mes_projetado.in_(meses_alvos),
            FatoIbpGranular.sku.in_(skus_alvos)
        ).all()

        mapa_linhas = defaultdict(list)
        for l in todas_linhas:
            mapa_linhas[(l.mes_projetado, l.sku)].append(l)

        # Matriz Histórica para Share de Clientes
        peso_map_global = {}
        if skus_alvos:
            q_hist = db.query(FatoVendas.cgc, FatoVendas.sku, func.sum(FatoVendas.qt_pedido).label('vol_hist'))\
                       .filter(FatoVendas.sku.in_(skus_alvos), FatoVendas.data_pedido >= data_hist)
            for r in q_hist.group_by(FatoVendas.cgc, FatoVendas.sku).all():
                if r.vol_hist and r.vol_hist > 0:
                    peso_map_global[(r.cgc, r.sku)] = float(r.vol_hist)

        # Rateio Absoluto e Esmagamento do vol_bu
        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            sku = ajuste.chave.split('|')[-1].strip()
            linhas = mapa_linhas.get((dt, sku), [])
            if not linhas: continue

            total_hist_no = sum(peso_map_global.get((l.cgc, l.sku), 0) for l in linhas)
            linhas.sort(key=lambda x: peso_map_global.get((x.cgc, x.sku), 0), reverse=True)

            target_volume = ajuste.novo_volume
            allocated = 0

            for i, linha in enumerate(linhas):
                peso_bruto = peso_map_global.get((linha.cgc, linha.sku), 0)
                p_val = peso_bruto / total_hist_no if total_hist_no > 0 else 1.0 / len(linhas)

                if i == len(linhas) - 1:
                    inc = target_volume - allocated
                else:
                    inc = int(round(target_volume * p_val))

                linha.vol_bottomup = max(0, inc)
                allocated += inc

        db.commit()
        return {"status": "success", "message": "Proposta de Portfólio consolidada na base de dados."}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=repr(e))

# =====================================================================
# POST: CONGELAR PORTFÓLIO E PASSAR BASTÃO PARA SUPPLY
# =====================================================================
@router.post("/congelar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        if payload.ajustes: 
            await salvar_rascunho_gerencia(payload, db, usuario)
        
        ciclo = get_current_cycle(db)
        
        # Como o processo agora é puramente Top-Down Force (Portfólio Global),
        # Oficializa os volumes Bottom-Up diretamente para a meta e supply.
        db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo).update({
            FatoIbpGranular.vol_supply: FatoIbpGranular.vol_bottomup,
            FatoIbpGranular.vol_final: FatoIbpGranular.vol_bottomup,
            FatoIbpGranular.vol_meta: FatoIbpGranular.vol_bottomup
        }, synchronize_session=False)
            
        token_global = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Demand-Review').first()
        if token_global: token_global.status = 'Fechado'
        else: db.add(ControleCiclo(ciclo_sop=ciclo, origem='Demand-Review', status='Fechado'))

        db.commit()
        return {"status": "success", "message": "Portfólio Global trancado e bastão estendido para Supply."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=repr(e))

# =====================================================================
# GET: TIMELINE DO GRÁFICO (APENAS HIERARQUIAS DE PRODUTO)
# =====================================================================
@router.get("/grafico")
async def grafico_gerenciamento(chave_matriz: str, visao: str = 'portfolio', db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        _mes_str, _ano_str = ciclo_atual.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        inicio_hist = hoje - relativedelta(months=24)
        m2_comercial = hoje + relativedelta(months=2)

        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            calendario[curr.strftime('%Y-%m')] = {"Realizado": None, "IA": None, "CicloAnterior": None, "TopDown": None}
            curr += relativedelta(months=1)

        partes_chave = chave_matriz.split('|')
        
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('realizado')).join(DimProduto, FatoVendas.sku == DimProduto.sku).filter(FatoVendas.data_pedido >= inicio_hist)
        if len(partes_chave) >= 1 and partes_chave[0]: q_hist = q_hist.filter(DimProduto.categoria == partes_chave[0])
        if len(partes_chave) >= 2 and partes_chave[1]: q_hist = q_hist.filter(DimProduto.segmento == partes_chave[1])
        if len(partes_chave) >= 3 and partes_chave[2]: q_hist = q_hist.filter(FatoVendas.sku == partes_chave[2].strip())
        
        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario: calendario[row.mes_ano]["Realizado"] = int(row.realizado or 0)

        q_proj = db.query(func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes_ano'), FatoIbpGranular.ciclo_sop, func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_final).label('final'), func.sum(FatoIbpGranular.vol_topdown).label('td')).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)
        if len(partes_chave) >= 1 and partes_chave[0]: q_proj = q_proj.filter(DimProduto.categoria == partes_chave[0])
        if len(partes_chave) >= 2 and partes_chave[1]: q_proj = q_proj.filter(DimProduto.segmento == partes_chave[1])
        if len(partes_chave) >= 3 and partes_chave[2]: q_proj = q_proj.filter(FatoIbpGranular.sku == partes_chave[2].strip())

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {"ia": int(row.ia or 0), "final": int(row.final or 0), "td": int(row.td or 0)}

        for mes_str in calendario.keys():
            if mes_str in proj_por_mes:
                if ciclo_atual in proj_por_mes[mes_str]:
                    calendario[mes_str]["IA"] = proj_por_mes[mes_str][ciclo_atual]["ia"]
                    calendario[mes_str]["TopDown"] = proj_por_mes[mes_str][ciclo_atual]["td"]
                if ciclo_anterior in proj_por_mes[mes_str]:
                    calendario[mes_str]["CicloAnterior"] = proj_por_mes[mes_str][ciclo_anterior]["final"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            timeline.append({
                "name": ms, "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt > hoje else (v["Realizado"] or 0),
                "IA": v["IA"], "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"] if mes_dt >= m2_comercial else None 
            })
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(status_code=500, detail=repr(e))