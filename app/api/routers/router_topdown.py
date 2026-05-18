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
        hoje = datetime.date.today().replace(day=1)
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

        arvore = {}
        for r in resultados:
            cat = str(r.categoria).strip() if r.categoria else "SEM CATEGORIA"
            seg = str(r.segmento).strip() if r.segmento else "SEM SEGMENTO"
            sku = str(r.sku).strip()
            desc = str(r.descricao).strip()
            ms = str(r.mes_projetado)

            if cat not in arvore: arvore[cat] = {"nome": cat, "tipo": "categoria", "meses": {m: {"vol_ia":0,"vol_td":0,"vol_ajustado":0,"receita":0,"pmv":0.0} for m in meses_alvo}, "segmentos": {}}
            if seg not in arvore[cat]["segmentos"]: arvore[cat]["segmentos"][seg] = {"nome": seg, "tipo": "segmento", "meses": {m: {"vol_ia":0,"vol_td":0,"vol_ajustado":0,"receita":0,"pmv":0.0} for m in meses_alvo}, "produtos": {}}
            if sku not in arvore[cat]["segmentos"][seg]["produtos"]: arvore[cat]["segmentos"][seg]["produtos"][sku] = {"nome": desc, "produto": sku, "tipo": "produto", "meses": {m: {"vol_ia":0,"vol_td":0,"vol_ajustado":0,"receita":0,"pmv":0.0} for m in meses_alvo}}

            if ms in meses_alvo:
                v_td = int(r.v_td or 0)
                pmv_item = float(r.pmv or 0)
                # Propagação Cima-Baixo
                for nivel in [arvore[cat]["meses"][ms], arvore[cat]["segmentos"][seg]["meses"][ms], arvore[cat]["segmentos"][seg]["produtos"][sku]["meses"][ms]]:
                    nivel["vol_ia"] += int(r.v_ia or 0)
                    nivel["vol_td"] += v_td
                    nivel["vol_ajustado"] += v_td
                    nivel["receita"] += (v_td * pmv_item)
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_item

        final = []
        for cat_k, cat_v in arvore.items():
            segs = []
            for seg_k, seg_v in cat_v["segmentos"].items():
                prods = []
                for p_k, p_v in seg_v["produtos"].items():
                    prods.append({"id": f"{cat_k}|{seg_k}|{p_k}", "chave_matriz": p_k, "nome": p_v["nome"], "produto": p_k, "tipo": "produto", "meses": [{"mes_banco": k, **v} for k, v in p_v["meses"].items()]})
                segs.append({"id": f"{cat_k}|{seg_k}", "chave_matriz": f"{cat_k}|{seg_k}", "nome": seg_k, "tipo": "segmento", "meses": [{"mes_banco": k, **v} for k, v in seg_v["meses"].items()], "subRows": prods})
            final.append({"id": cat_k, "chave_matriz": cat_k, "nome": cat_k, "tipo": "categoria", "meses": [{"mes_banco": k, **v} for k, v in cat_v["meses"].items()], "subRows": segs})

        return {"status": "success", "dados": sorted(final, key=lambda x: x["nome"])}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def aprovar_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        hoje = datetime.date.today().replace(day=1)
        data_hist = hoje - relativedelta(months=12) # Histórico de 1 ano para o Rateio

        for ajuste in payload.ajustes:
            dt_mes = parse_date_safe(ajuste.mes_projetado)
            sku_alvo = ajuste.sku

            # 1. Auditoria Histórica de Vendas (Rateio Inteligente e PMV Exato)
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
                    pmv_cliente[r.cgc] = rec / vol # O PMV cirúrgico Cliente/Produto

            # 2. Resgata a base granular do IBP
            linhas = db.query(FatoIbpGranular).filter(
                FatoIbpGranular.ciclo_sop == ciclo,
                FatoIbpGranular.mes_projetado == dt_mes,
                FatoIbpGranular.sku == sku_alvo
            ).all()

            if not linhas: continue

            total_atual = sum(l.vol_topdown for l in linhas)
            delta = ajuste.novo_volume - total_atual

            # 3. Rateio do Delta e Injeção do PMV Real
            linhas.sort(key=lambda x: peso_cliente.get(x.cgc, 0), reverse=True)

            if delta != 0:
                for i, l in enumerate(linhas):
                    # Puxa o market-share do cliente. Se não tiver hitórico global, divide igual
                    peso = peso_cliente.get(l.cgc, 1/len(linhas) if total_hist == 0 else 0)
                    
                    inc = int(round(delta * peso))
                    # A sobra do arredondamento fica no maior cliente (último da lista de desconto/acréscimo)
                    if i == len(linhas) - 1:
                        inc = delta - sum(int(round(delta * peso_cliente.get(x.cgc, 1/len(linhas) if total_hist == 0 else 0))) for x in linhas[:-1])
                    
                    novo_vol = max(0, l.vol_topdown + inc)
                    l.vol_topdown = novo_vol
                    l.vol_bottomup = novo_vol # Cascata imediata
                    l.vol_supply = novo_vol
                    l.vol_final = novo_vol

            for l in linhas:
                if l.cgc in pmv_cliente and pmv_cliente[l.cgc] > 0:
                    l.pmv_aplicado = pmv_cliente[l.cgc]

        # 4. Trava a porta do Top-Down
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if reg: reg.status = 'Fechado'
        else: db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down', status='Fechado'))

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/status")
async def status_topdown(db: Session = Depends(get_db)):
    ciclo = get_current_cycle(db)
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
    return {"is_fechado": reg.status == 'Fechado' if reg else False}

@router.get("/grafico")
async def grafico_topdown(chave_matriz: str, db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        hoje = datetime.date.today().replace(day=1)
        inicio_hist = hoje - relativedelta(months=24) 

        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            mes_str = curr.strftime('%Y-%m')
            calendario[mes_str] = {"Realizado": None, "IA": None, "CicloAnterior": None, "TopDown": None}
            curr += relativedelta(months=1)

        # Filtro Inteligente: Sabe se a chave é Categoria, Segmento ou SKU
        def apply_matrix_filter(query, model_dim, model_fact):
            if "|" in chave_matriz:
                parts = chave_matriz.split("|")
                return query.filter(func.upper(func.trim(model_dim.segmento)) == parts[1].strip().upper())
            else:
                exists = db.query(DimProduto).filter(DimProduto.sku == chave_matriz).first()
                if exists: return query.filter(model_fact.sku == chave_matriz)
                else: return query.filter(func.upper(func.trim(model_dim.categoria)) == chave_matriz.strip().upper())

        # 1. LINHA PRETA: REALIZADO
        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).outerjoin(DimProduto, FatoVendas.sku == DimProduto.sku).filter(FatoVendas.data_pedido >= inicio_hist)
        
        q_hist = apply_matrix_filter(q_hist, DimProduto, FatoVendas)

        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario:
                calendario[row.mes_ano]["Realizado"] = int(row.realizado or 0)

        # PREPARA BASE IBP
        q_proj = db.query(
            func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes_ano'),
            FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_final).label('final'),
            func.sum(FatoIbpGranular.vol_topdown).label('td')
        ).outerjoin(DimProduto, FatoIbpGranular.sku == DimProduto.sku)
        
        q_proj = apply_matrix_filter(q_proj, DimProduto, FatoIbpGranular)

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {"ia": int(row.ia or 0), "final": int(row.final or 0), "td": int(row.td or 0)}

        # REGRAS DO GRÁFICO (As 4 Linhas)
        ciclo_base_zero = datetime.datetime.strptime('04/2026', '%m/%Y').date()
        ciclo_atual_dt = datetime.datetime.strptime(ciclo_atual, '%m/%Y').date()

        for mes_str in calendario.keys():
            mes_dt = datetime.datetime.strptime(mes_str, '%Y-%m').date()
            if mes_str not in proj_por_mes: continue

            # 2. LINHA CINZA: IA (A Escadinha)
            alvo_ia_dt = mes_dt - relativedelta(months=2)
            if alvo_ia_dt < ciclo_base_zero: alvo_ia_dt = ciclo_base_zero
            if alvo_ia_dt > ciclo_atual_dt: alvo_ia_dt = ciclo_atual_dt
            ciclo_ia_str = alvo_ia_dt.strftime('%m/%Y')
            
            if ciclo_ia_str in proj_por_mes[mes_str]:
                calendario[mes_str]["IA"] = proj_por_mes[mes_str][ciclo_ia_str]["ia"]

            # 3. LINHA ROXA: CICLO ANTERIOR
            if ciclo_anterior in proj_por_mes[mes_str]:
                calendario[mes_str]["CicloAnterior"] = proj_por_mes[mes_str][ciclo_anterior]["final"]

            # 4. LINHA AZUL: TOP-DOWN (Proposta Atual)
            if ciclo_atual in proj_por_mes[mes_str]:
                calendario[mes_str]["TopDown"] = proj_por_mes[mes_str][ciclo_atual]["td"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            is_future_or_current = mes_dt >= hoje
            
            timeline.append({
                "name": ms,
                "data_iso": f"{ms}-01",
                "Realizado": None if is_future_or_current else (v["Realizado"] or 0),
                "IA": v["IA"],
                "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"] if is_future_or_current else None
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))