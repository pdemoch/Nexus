"""
=====================================================================
 ROUTER KPIS — MOTOR DE DESVIOS (ACURACIA DO S&OP)  ·  metodo AGREGADO
=====================================================================
Mede a acuracia do S&OP comparando o CONGELADO (vol_final=humano, vol_ia=IA)
contra o REALIZADO (fato_vendas), por multiplas dimensoes, com filtros
combinaveis e evolucao temporal IA vs Humano.

METODO AGREGADO (decisao de negocio):
  Para cada recorte (empresa, categoria, coordenador, etc.), soma-se primeiro
  o previsto e o realizado, e as metricas saem sobre os TOTAIS. Isto da numeros
  jogaveis (ex.: junho fechado ~69% de acuracia) em vez do par-a-par impiedoso
  (que grampeava tudo em zero). O erro fino de posicionamento (par-a-par) fica
  para o drilldown do ranking, nao para a nota.

REGRA TEMPORAL (dinamica): realizado do mes N vs congelado do ciclo N-2.
  07/2026 (vendas) vs 05/2026 (ciclo). O mes CORRENTE entra marcado como
  "parcial" (ainda nao fechou) — nao se compara a meses fechados sem aviso.

FONTES: previsto = fato_ibp_granular (ciclo N-2); realizado = fato_vendas.
  Pessoas do cadastro ATUAL da dim_clientes (coordenador=supervisor_nome,
  vendedor/executivo=vendedor_nome). Cliente por razaosocial. Inativos fora.
Prefixo: /api/v1/kpis
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Optional

from app.core.database import get_db

router = APIRouter(prefix="/api/v1/kpis", tags=["Desvios e Acuracia do S&OP"])

# Dimensoes de recorte/filtro (valores dinamicos, sem hardcode).
DIMENSOES = {
    "categoria": "Categoria", "segmento": "Segmento", "sku": "SKU",
    "regional": "Regional", "cliente": "Cliente",
    "coordenador": "Coordenador", "vendedor": "Vendedor",
}
COL_MAP = {
    "categoria": "categoria", "segmento": "segmento", "sku": "sku",
    "regional": "regional", "cliente": "razaosocial",
    "coordenador": "supervisor_nome", "vendedor": "vendedor_nome",
}
PESO_BIAS = 0.15
PESO_FVA = 0.20


def _ciclo_para_mes(mes_ano: str) -> str:
    mes, ano = mes_ano.split("/")
    dt = datetime.date(int(ano), int(mes), 1)
    return (dt - relativedelta(months=2)).strftime("%m/%Y")


def _mes_date_sql(mes_ano: str) -> str:
    mes, ano = mes_ano.split("/")
    return f"{ano}-{mes.zfill(2)}-01"


def _mes_corrente() -> str:
    hoje = datetime.datetime.utcnow() - datetime.timedelta(hours=3)  # Brasilia
    return hoje.strftime("%m/%Y")


def listar_meses_auditaveis(db: Session):
    """
    Meses com realizado E ciclo N-2 disponivel. Marca o mes corrente como
    parcial (ainda correndo). Retorna lista de dicts {mes, ano, parcial}.
    """
    meses_venda = db.execute(text("""
        SELECT DISTINCT TO_CHAR(data_pedido, 'MM/YYYY') AS mes
        FROM fato_vendas WHERE qt_pedido > 0
    """)).fetchall()
    set_venda = {r.mes for r in meses_venda}
    ciclos = db.execute(text("SELECT DISTINCT ciclo_sop FROM fato_ibp_granular")).fetchall()
    set_ciclos = {r.ciclo_sop for r in ciclos}
    corrente = _mes_corrente()

    aud = [m for m in set_venda if _ciclo_para_mes(m) in set_ciclos]
    aud.sort(key=lambda m: (m.split("/")[1], m.split("/")[0]))
    return [{"mes": m, "ano": m.split("/")[1], "num": m.split("/")[0], "parcial": (m == corrente)} for m in aud]


def _montar_base(db: Session, meses: List[str], visao: str, filtros: dict = None) -> pd.DataFrame:
    """
    Base por MES x SKU (grao SKU — cliente nao entra mais).
    UNIVERSO = fato_ibp_granular: todo SKU planejado no ciclo N-2 entra,
    inclusive os planejados com ZERO (BISCOITOS, HUMMM...). SKU que nao esta
    na fato_ibp fica fora (nao foi planejado, nao ha plano para auditar).
    REALIZADO = total da fato_vendas por SKU (bate com o ERP), trazido por
    left join sobre o universo do plano.
    """
    frames = []
    for mes in meses:
        ciclo = _ciclo_para_mes(mes)
        mes_date = _mes_date_sql(mes)

        # PREVISTO agregado por SKU (soma todos os clientes do plano).
        prev = db.execute(text("""
            SELECT f.sku,
                COALESCE(p.categoria,'SEM CATEGORIA') AS categoria,
                COALESCE(p.segmento,'SEM SEGMENTO')   AS segmento,
                COALESCE(p.descricao, f.sku)          AS descricao,
                SUM(f.vol_final) AS prev_final_cx, SUM(f.vol_ia) AS prev_ia_cx,
                SUM(f.vol_final * f.pmv_aplicado) AS prev_final_rs,
                SUM(f.vol_ia * f.pmv_aplicado)    AS prev_ia_rs
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON f.sku = p.sku
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = :mes_date
            GROUP BY f.sku, p.categoria, p.segmento, p.descricao
        """), {"ciclo": ciclo, "mes_date": mes_date}).fetchall()

        # REALIZADO total por SKU (fato_vendas completa — bate com ERP).
        real = db.execute(text("""
            SELECT sku, SUM(qt_pedido) AS real_cx, SUM(vl_pedido) AS real_rs
            FROM fato_vendas
            WHERE TO_CHAR(data_pedido,'MM/YYYY') = :mes AND qt_pedido > 0
            GROUP BY sku
        """), {"mes": mes}).fetchall()

        df_prev = pd.DataFrame([dict(r._mapping) for r in prev])
        df_real = pd.DataFrame([dict(r._mapping) for r in real])
        if df_prev.empty:
            continue  # sem plano no ciclo = nada a auditar neste mes
        if df_real.empty:
            df_real = pd.DataFrame(columns=["sku","real_cx","real_rs"])

        # fato_ibp e o PAI: left join do plano (universo) com o realizado.
        # SKU planejado que nao vendeu -> realizado 0. SKU que vendeu sem
        # plano nao entra (nao esta no universo do plano).
        df = pd.merge(df_prev, df_real, on="sku", how="left")
        df["mes_ano"] = mes
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    base = pd.concat(frames, ignore_index=True)
    for col in ["prev_final_cx","prev_ia_cx","prev_final_rs","prev_ia_rs","real_cx","real_rs"]:
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0.0)
    if visao == "financeiro":
        base["previsto_final"], base["previsto_ia"], base["realizado"] = base["prev_final_rs"], base["prev_ia_rs"], base["real_rs"]
    else:
        base["previsto_final"], base["previsto_ia"], base["realizado"] = base["prev_final_cx"], base["prev_ia_cx"], base["real_cx"]


    # Filtros de recorte no grao SKU: so dimensoes de PRODUTO.
    if filtros:
        col_prod = {"categoria": "categoria", "segmento": "segmento", "sku": "sku"}
        for dim, valor in filtros.items():
            if valor and dim in col_prod:
                base = base[base[col_prod[dim]].astype(str) == str(valor)]
    return base


def _metricas_agregadas(grupo: pd.DataFrame) -> dict:
    """
    METODO AGREGADO: soma primeiro, razao depois. Numeros jogaveis.
      WMAPE   = |Σreal - Σprev| / Σreal   (erro do TOTAL, nao par-a-par)
      Acuracia= max(0, 1 - WMAPE)
      BIAS    = (Σprev - Σreal) / Σreal
      FVA     = WMAPE_ia - WMAPE_final
    """
    soma_real = float(grupo["realizado"].sum())
    soma_final = float(grupo["previsto_final"].sum())
    soma_ia = float(grupo["previsto_ia"].sum())

    if soma_real > 0:
        wmape_final = abs(soma_final - soma_real) / soma_real
        wmape_ia = abs(soma_ia - soma_real) / soma_real
        bias_final = (soma_final - soma_real) / soma_real
        bias_ia = (soma_ia - soma_real) / soma_real
    else:
        wmape_final = 1.0 if soma_final > 0 else 0.0
        wmape_ia = 1.0 if soma_ia > 0 else 0.0
        bias_final = 1.0 if soma_final > 0 else 0.0
        bias_ia = 1.0 if soma_ia > 0 else 0.0

    acuracia = max(0.0, 1.0 - wmape_final)
    acuracia_ia = max(0.0, 1.0 - wmape_ia)
    fva = wmape_ia - wmape_final
    nota = max(0.0, min(1.0, acuracia - abs(bias_final)*PESO_BIAS + fva*PESO_FVA)) * 100.0
    if abs(bias_final) < 0.05: vies = "equilibrado"
    elif bias_final > 0: vies = "prevê demais"
    else: vies = "prevê de menos"

    # COBERTURA nao se aplica mais: com a fato_ibp como pai da auditoria,
    # todo o realizado auditado e, por definicao, o dos pares planejados.

    return {
        "previsto_final": round(soma_final,2), "previsto_ia": round(soma_ia,2),
        "realizado": round(soma_real,2),
        "wmape": round(wmape_final,4), "wmape_ia": round(wmape_ia,4),
        "acuracia": round(acuracia,4), "acuracia_ia": round(acuracia_ia,4),
        "bias": round(bias_final,4), "bias_ia": round(bias_ia,4),
        "vies_label": vies, "fva": round(fva,4), "nota": round(nota,1),
        "linhas": int(len(grupo)),
    }


def _metricas_par_a_par(grupo: pd.DataFrame) -> dict:
    """
    METODO PAR-A-PAR (so para drilldown/diagnostico fino): soma dos erros
    linha a linha. Revela erro de POSICIONAMENTO (qual cliente/SKU), que o
    agregado esconde por compensacao.
    """
    soma_real = float(grupo["realizado"].sum())
    erro_final = float(np.abs(grupo["realizado"] - grupo["previsto_final"]).sum())
    wmape = erro_final / soma_real if soma_real > 0 else (1.0 if grupo["previsto_final"].sum() > 0 else 0.0)
    return {"wmape_fino": round(wmape,4), "acuracia_fina": round(max(0.0, 1.0-wmape),4)}


# =====================================================================
# ROTAS
# =====================================================================
@router.get("/meses-auditaveis")
async def meses_auditaveis(db: Session = Depends(get_db)):
    try:
        return {"meses": listar_meses_auditaveis(db),
                "dimensoes": [{"chave": k, "label": v} for k, v in DIMENSOES.items()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opcoes-filtro")
async def opcoes_filtro(dimensao: str = Query(...), db: Session = Depends(get_db)):
    """Valores distintos de uma dimensao, para popular a caixa de selecao."""
    try:
        if dimensao not in COL_MAP:
            raise HTTPException(status_code=400, detail="Dimensao invalida.")
        if dimensao in ("coordenador", "vendedor", "cliente", "regional"):
            col = {"coordenador":"supervisor_nome","vendedor":"vendedor_nome",
                   "cliente":"razaosocial","regional":"regional"}[dimensao]
            # Sem filtro de inativos: a auditoria olha o passado, e clientes hoje
            # inativos tem vendas historicas auditaveis.
            rows = db.execute(text(f"""
                SELECT DISTINCT {col} AS v FROM dim_clientes
                WHERE {col} IS NOT NULL
                ORDER BY {col}
            """)).fetchall()
        else:
            col = {"categoria":"categoria","segmento":"segmento","sku":"sku"}[dimensao]
            rows = db.execute(text(f"""
                SELECT DISTINCT {col} AS v FROM dim_produtos WHERE {col} IS NOT NULL ORDER BY {col}
            """)).fetchall()
        return {"dimensao": dimensao, "opcoes": [str(r.v) for r in rows if r.v]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _parse_filtros(categoria, segmento, sku, regional, cliente, coordenador, vendedor):
    f = {}
    if categoria: f["categoria"] = categoria
    if segmento: f["segmento"] = segmento
    if sku: f["sku"] = sku
    if regional: f["regional"] = regional
    if cliente: f["cliente"] = cliente
    if coordenador: f["coordenador"] = coordenador
    if vendedor: f["vendedor"] = vendedor
    return f


@router.get("/evolucao")
async def evolucao(
    visao: str = Query("caixas"),
    meses: List[str] = Query(None),
    categoria: Optional[str] = None, segmento: Optional[str] = None, sku: Optional[str] = None,
    regional: Optional[str] = None, cliente: Optional[str] = None,
    coordenador: Optional[str] = None, vendedor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Serie temporal (eixo X = mes) das 4 metricas, IA vs Humano, para o recorte
    dado pelos filtros. Marca mes parcial.
    """
    try:
        todos = listar_meses_auditaveis(db)
        parciais = {m["mes"]: m["parcial"] for m in todos}
        lista = meses if meses else [m["mes"] for m in todos]
        filtros = _parse_filtros(categoria, segmento, sku, regional, cliente, coordenador, vendedor)

        serie = []
        for mes in lista:
            base = _montar_base(db, [mes], visao, filtros)
            if base.empty:
                continue
            m = _metricas_agregadas(base)
            serie.append({
                "mes": mes, "parcial": parciais.get(mes, False),
                "wmape": round(m["wmape"]*100,1), "wmape_ia": round(m["wmape_ia"]*100,1),
                "acuracia": round(m["acuracia"]*100,1), "acuracia_ia": round(m["acuracia_ia"]*100,1),
                "bias": round(m["bias"]*100,1), "bias_ia": round(m["bias_ia"]*100,1),
                "fva": round(m["fva"]*100,1),
                "previsto_final": m["previsto_final"], "previsto_ia": m["previsto_ia"], "realizado": m["realizado"],
            })
        # ordena cronologico
        serie.sort(key=lambda x: (x["mes"].split("/")[1], x["mes"].split("/")[0]))
        return {"visao": visao, "filtros": filtros, "serie": serie}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/resumo")
async def resumo(
    visao: str = Query("caixas"),
    meses: List[str] = Query(None),
    categoria: Optional[str] = None, segmento: Optional[str] = None, sku: Optional[str] = None,
    regional: Optional[str] = None, cliente: Optional[str] = None,
    coordenador: Optional[str] = None, vendedor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Números consolidados do recorte (cartões de topo)."""
    try:
        todos = listar_meses_auditaveis(db)
        lista = meses if meses else [m["mes"] for m in todos]
        filtros = _parse_filtros(categoria, segmento, sku, regional, cliente, coordenador, vendedor)
        base = _montar_base(db, lista, visao, filtros)
        if base.empty:
            return {"visao": visao, "totais": {}}
        return {"visao": visao, "meses": lista, "totais": _metricas_agregadas(base)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@router.get("/tabela")
async def tabela_drill(
    visao: str = Query("caixas"),
    meses: List[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    A TABELA QUE COMPROVA OS GRAFICOS: drill Categoria -> SKU com os volumes
    lado a lado (realizado, previsto humano, previsto IA) e as metricas de
    cada item no periodo selecionado. Escopo = plano (fato_ibp pai).
    """
    try:
        todos = listar_meses_auditaveis(db)
        lista = meses if meses else [m["mes"] for m in todos]
        base = _montar_base(db, lista, visao)
        if base.empty:
            return {"visao": visao, "meses": lista, "categorias": []}

        categorias = []
        for cat, gcat in base.groupby("categoria"):
            mcat = _metricas_agregadas(gcat)
            skus = []
            for sku, gsku in gcat.groupby("sku"):
                msku = _metricas_agregadas(gsku)
                desc = ""
                if "descricao" in gsku.columns and len(gsku) > 0:
                    d = gsku["descricao"].dropna()
                    desc = str(d.iloc[0]) if len(d) > 0 else str(sku)
                skus.append({
                    "nome": str(sku), "descricao": desc,
                    "realizado": msku["realizado"],
                    "previsto_final": msku["previsto_final"],
                    "previsto_ia": msku["previsto_ia"],
                    "acuracia": msku["acuracia"], "bias": msku["bias"],
                    "fva": msku["fva"],
                })
            skus.sort(key=lambda x: -x["realizado"])
            categorias.append({
                "nome": str(cat),
                "realizado": mcat["realizado"],
                "previsto_final": mcat["previsto_final"],
                "previsto_ia": mcat["previsto_ia"],
                "acuracia": mcat["acuracia"], "bias": mcat["bias"],
                "fva": mcat["fva"],
                "skus": skus,
            })
        categorias.sort(key=lambda x: -x["realizado"])
        return {"visao": visao, "meses": lista, "categorias": categorias}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))