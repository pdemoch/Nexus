"""
router_kpis.py — Acurácia do S&OP e Diagnóstico por SKU
=========================================================

FONTES
    fato_previsao_humana   metas históricas jan/23 → mai/26 (em caixas)
    fato_ibp_granular      vol_final (plano Nexus) e vol_ia
    fato_vendas            realizado: qt_pedido (cx) e vl_pedido (R$)
    dim_produtos           portfólio ativo, BU, Categoria

PESO DAS MÉTRICAS
    Sempre em R$. vol_humano (caixas históricas) é convertido para R$ pelo
    PMV real (vl_pedido / qt_pedido) de cada SKU. WMAPE = |prev_rs - real_rs|
    / real_rs. Caixas ficam disponíveis para exibição, não pesam no erro.

REGRA DE MÊS FECHADO
    fim trava no último mês anterior ao corrente (UTC-3). Mês parcial
    contaminaria qualquer WMAPE.

DIAGNÓSTICO
    WMAPE      → tamanho do erro em R$
    VIÉS       → direção: positivo = superestimou em R$
    PERSIST.   → fração dos meses na mesma direção do viés
    TENDÊNCIA  → WMAPE recente menos WMAPE antigo
    EXPOSIÇÃO  → gap em R$ sobre TODO o período (inclui meses sem venda —
                  esses são os maiores riscos de over-stock)
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import datetime
import pandas as pd
import numpy as np

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["KPIs e Acurácia do S&OP"])

# ─── limiares ────────────────────────────────────────────────────────────────
VIES_RELEVANTE  = 0.10   # 10 % em R$ — material para o plano
PERSIST_CRONICA = 0.70   # ≥ 70 % dos meses na mesma direção = padrão
WMAPE_CONTROLE  = 0.20   # ≤ 20 % → sob controle
WMAPE_ERRATICO  = 0.30   # > 30 % sem viés → errático / volátil
MIN_MESES       = 4      # mínimo para diagnóstico confiável
TOLERANCIA_DIR  = 0.02   # erro < 2 % em R$ = "no alvo" (não conta como direção)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════
def _ultimo_mes_fechado() -> str:
    hoje = datetime.datetime.utcnow() - datetime.timedelta(hours=3)
    return (hoje.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")


def _periodo(inicio: Optional[str], fim: Optional[str]) -> tuple:
    """Retorna (ini_str, fim_str) ambos em 'YYYY-MM-DD'. fim trava no fechado."""
    ini = (inicio or "2023-01") + "-01"
    teto = _ultimo_mes_fechado()
    f = fim if fim and fim <= teto else teto
    return ini, f + "-01"


# ═════════════════════════════════════════════════════════════════════════════
# NÚCLEO — uma query, todo o resto em pandas
# ═════════════════════════════════════════════════════════════════════════════
def _carregar_base(db: Session, inicio: str, fim: str,
                   bu=None, categoria=None, sku=None,
                   apenas_ativos=True) -> pd.DataFrame:
    """
    Retorna sku × mês com realizado, plano humano, plano IA e PMV.

    As datas são embutidas como literais no SQL (não via params) para evitar
    a ambiguidade :nome::cast do SQLAlchemy vs %(nome)s do psycopg2.
    _periodo() valida o formato — sem risco de injeção.
    """
    filtros, params = [], {}
    if bu:        filtros.append("p.bu = :bu");         params["bu"] = bu
    if categoria: filtros.append("p.categoria = :cat"); params["cat"] = categoria
    if sku:       filtros.append("p.sku = :sku");       params["sku"] = sku
    if apenas_ativos:
        filtros.append("COALESCE(p.ativo, FALSE) = TRUE")
    w = ("AND " + " AND ".join(filtros)) if filtros else ""

    sql = text(f"""
        WITH grade AS (
            SELECT p.sku, p.descricao, p.bu, p.categoria, p.segmento,
                   g.mes::date AS mes
            FROM dim_produtos p
            CROSS JOIN generate_series(
                '{inicio}'::date, '{fim}'::date, '1 month'::interval
            ) AS g(mes)
            WHERE 1=1 {w}
        ),
        realizado AS (
            SELECT TRIM(v.sku::text) AS sku,
                   DATE_TRUNC('month', v.data_pedido)::date AS mes,
                   SUM(v.qt_pedido) AS vol_real,
                   SUM(v.vl_pedido) AS val_real
            FROM fato_vendas v
            WHERE v.data_pedido >= '{inicio}'
              AND v.data_pedido < ('{fim}'::date + INTERVAL '1 month')
            GROUP BY 1, 2
        ),
        nexus AS (
            -- M+2 congelado: ciclo cujo início é 2 meses antes do mês alvo
            SELECT TRIM(i.sku::text) AS sku,
                   i.mes_projetado   AS mes,
                   SUM(i.vol_final)  AS vol_humano,
                   SUM(i.vol_ia)     AS vol_ia
            FROM fato_ibp_granular i
            WHERE (
                EXTRACT(YEAR  FROM i.mes_projetado) * 12
              + EXTRACT(MONTH FROM i.mes_projetado)
            ) - (
                SPLIT_PART(i.ciclo_sop, '/', 2)::int * 12
              + SPLIT_PART(i.ciclo_sop, '/', 1)::int
            ) = 2
              AND i.mes_projetado >= '{inicio}'
              AND i.mes_projetado <= '{fim}'
            GROUP BY 1, 2
        ),
        historico AS (
            SELECT h.sku,
                   h.mes_projetado AS mes,
                   SUM(h.vol_humano) AS vol_humano
            FROM fato_previsao_humana h
            WHERE h.fonte = 'HISTORICO'
              AND h.mes_projetado >= '{inicio}'
              AND h.mes_projetado <= '{fim}'
            GROUP BY 1, 2
        )
        SELECT
            g.sku, g.descricao, g.bu, g.categoria, g.segmento,
            TO_CHAR(g.mes, 'YYYY-MM')             AS mes,
            COALESCE(r.vol_real,  0)              AS vol_real,
            COALESCE(r.val_real,  0)              AS val_real,
            COALESCE(n.vol_humano, h.vol_humano)  AS vol_humano,
            n.vol_ia                              AS vol_ia,
            CASE WHEN n.vol_humano IS NOT NULL THEN 'NEXUS'
                 WHEN h.vol_humano IS NOT NULL THEN 'HISTORICO'
                 ELSE NULL END                    AS origem_humano
        FROM grade g
        LEFT JOIN realizado r ON r.sku = g.sku AND r.mes = g.mes
        LEFT JOIN nexus     n ON n.sku = g.sku AND n.mes = g.mes
        LEFT JOIN historico h ON h.sku = g.sku AND h.mes = g.mes
        WHERE COALESCE(r.vol_real,  0) > 0
           OR COALESCE(n.vol_humano, 0) > 0
           OR COALESCE(h.vol_humano, 0) > 0
        ORDER BY g.sku, g.mes
    """)

    df = pd.read_sql(sql, db.bind, params=params or None)
    if df.empty:
        return df

    for c in ["vol_real", "val_real", "vol_humano"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    # vol_ia NULL → NaN (preservado, não zero — distingue "sem plano" de "plano zero")
    df["vol_ia"] = pd.to_numeric(df["vol_ia"], errors="coerce")

    # PMV mensal por SKU: val_real / vol_real (meses com venda real)
    # Meses sem venda recebem a média do SKU para converter vol_humano → R$
    df["pmv"] = np.where(df.vol_real > 0,
                         df.val_real / df.vol_real.replace(0, np.nan),
                         np.nan)
    pmv_sku = df[df.pmv.notna()].groupby("sku").pmv.mean()
    df["pmv"] = df["pmv"].fillna(df.sku.map(pmv_sku)).fillna(0)

    # Planos em R$
    df["val_humano"] = df["vol_humano"] * df["pmv"]
    df["val_ia"]     = df["vol_ia"]     * df["pmv"]   # NaN onde vol_ia é NaN

    return df


# ═════════════════════════════════════════════════════════════════════════════
# MÉTRICAS — ponderadas em R$
# ═════════════════════════════════════════════════════════════════════════════
def _metricas(df: pd.DataFrame, col_vol: str = "vol_humano") -> dict:
    """
    WMAPE, viés, persistência, tendência e exposição financeira.

    col_vol é a coluna de previsão em caixas; val_{col_vol} é a correspondente
    em R$ (já calculada em _carregar_base). Todo erro é ponderado por val_real.

    EXPOSIÇÃO: calculada sobre o DataFrame COMPLETO (inclui meses com
    vol_real = 0 mas plano > 0 — são os maiores riscos de over-stock).
    WMAPE e VIÉS: calculados só onde há vendas reais (denominador válido).
    """
    col_val = col_vol.replace("vol_", "val_")   # vol_humano → val_humano
    if col_val not in df.columns:
        return {}

    # ── exposição sobre TODO o período (antes de filtrar por vol_real > 0) ──
    gap_rs   = df[col_val].fillna(0) - df.val_real
    excesso  = float(gap_rs.clip(lower=0).sum())    # planejei mais que vendi
    falta    = float((-gap_rs).clip(lower=0).sum()) # vendi mais que planejei

    # ── WMAPE e viés só onde há venda real ──────────────────────────────────
    g = df[df.vol_real > 0].copy()
    if g.empty:
        return {"excesso_rs": round(excesso, 2), "falta_rs": round(falta, 2),
                "erro_abs_rs": round(excesso + falta, 2)}

    g[col_val] = g[col_val].fillna(0)
    real_rs = float(g.val_real.sum())
    prev_rs = float(g[col_val].sum())
    if real_rs <= 0:
        return {}

    erro_rs = float((g[col_val] - g.val_real).abs().sum())
    wmape   = erro_rs / real_rs
    vies    = (prev_rs - real_rs) / real_rs

    # por mês em R$ → persistência e tendência
    m = (g.groupby("mes")
          .agg(real_rs=("val_real", "sum"), prev_rs=(col_val, "sum"))
          .reset_index())
    m = m[m.real_rs > 0].copy()
    if m.empty:
        return {}
    m["erro_rel"] = (m.prev_rs - m.real_rs) / m.real_rs

    persist = float(
        (m.erro_rel > TOLERANCIA_DIR).mean() if vies >= 0
        else (m.erro_rel < -TOLERANCIA_DIR).mean()
    )

    metade   = max(len(m) // 2, 1)
    ant, rec = m.head(metade), m.tail(metade)
    wmape_ant = float((ant.prev_rs - ant.real_rs).abs().sum() / max(ant.real_rs.sum(), 1))
    wmape_rec = float((rec.prev_rs - rec.real_rs).abs().sum() / max(rec.real_rs.sum(), 1))

    return {
        "wmape":         round(wmape, 4),
        "vies":          round(vies, 4),
        "persistencia":  round(persist, 4),
        "wmape_recente": round(wmape_rec, 4),
        "wmape_antigo":  round(wmape_ant, 4),
        "tendencia":     round(wmape_rec - wmape_ant, 4),
        "meses":         int(len(m)),
        "vol_real":      round(float(g.vol_real.sum()), 0),
        "vol_previsto":  round(float(g[col_vol].fillna(0).sum()), 0),
        "val_real":      round(real_rs, 2),
        "val_previsto":  round(prev_rs, 2),
        "erro_abs_rs":   round(excesso + falta, 2),
        "excesso_rs":    round(excesso, 2),
        "falta_rs":      round(falta, 2),
    }


def _classificar(m: dict) -> dict:
    """
    Traduz métricas em diagnóstico e recomendação.

    Ajuste = viés/(1+viés), não o próprio viés. Se o plano ficou 25% acima,
    reduzir 25% corta demais (→ 0,9375×). A fórmula devolve exatamente 1,000×.
    """
    if not m or m.get("meses", 0) < MIN_MESES:
        return {"classe": "Sem base", "acao": "Histórico insuficiente",
                "severidade": 0, "direcao": "indefinido"}

    wmape   = m.get("wmape", 1.0)
    vies    = m.get("vies", 0.0)
    persist = m.get("persistencia", 0.0)

    if wmape <= WMAPE_CONTROLE and abs(vies) <= VIES_RELEVANTE:
        return {"classe": "Sob controle", "acao": "Manter",
                "severidade": 0, "direcao": "estavel"}

    if vies > VIES_RELEVANTE and persist >= PERSIST_CRONICA:
        aj = -round(vies / (1 + vies) * 100, 1)
        return {"classe": "Superestimando", "direcao": "excesso",
                "acao": f"Reduzir plano em ~{abs(aj):.0f}%",
                "ajuste_sugerido_pct": aj,
                "severidade": 3 if vies > 0.25 else 2}

    if vies < -VIES_RELEVANTE and persist >= PERSIST_CRONICA:
        aj = round(-vies / (1 + vies) * 100, 1)
        return {"classe": "Subestimando", "direcao": "falta",
                "acao": f"Aumentar plano em ~{abs(aj):.0f}%",
                "ajuste_sugerido_pct": aj,
                "severidade": 3 if vies < -0.25 else 2}

    if wmape > WMAPE_ERRATICO:
        return {"classe": "Errático", "direcao": "volatil",
                "acao": "Investigar causa — erro sem direção fixa",
                "severidade": 2}

    return {"classe": "Atenção", "direcao": "misto",
            "acao": "Monitorar — viés sem padrão consistente",
            "severidade": 1}


# ═════════════════════════════════════════════════════════════════════════════
# LÓGICA INTERNA DO DIAGNÓSTICO
# ═════════════════════════════════════════════════════════════════════════════
def _calcular_diagnostico(db: Session, ini: str, f: str,
                          bu, categoria, nivel: str) -> dict:
    df = _carregar_base(db, ini, f, bu, categoria, None)
    if df.empty:
        return {"itens": [], "agregados": {}, "periodo": {"inicio": ini[:7], "fim": f[:7]}}

    chave = ["sku", "descricao", "categoria", "bu"] if nivel == "sku" else ["categoria"]
    itens = []
    for valores, g in df.groupby(chave):
        vals = valores if isinstance(valores, tuple) else (valores,)
        m = _metricas(g, "vol_humano")
        if not m:
            continue

        # IA: só linhas com plano Nexus (vol_ia não nulo)
        g_ia = g[g.vol_ia.notna() & (g.vol_ia > 0)]
        m_ia = _metricas(g_ia, "vol_ia") if not g_ia.empty else {}

        item = dict(zip(chave, vals))
        item.update(m)
        item.update(_classificar(m))
        item["wmape_ia"] = m_ia.get("wmape")
        item["vies_ia"]  = m_ia.get("vies")
        item["fva"]      = round(m_ia["wmape"] - m["wmape"], 4) if m_ia.get("wmape") is not None else None
        itens.append(item)

    itens.sort(key=lambda x: x.get("erro_abs_rs", 0), reverse=True)

    def _soma(classe):
        sel = [i for i in itens if i.get("classe") == classe]
        return {"qtd": len(sel),
                "erro_rs": round(sum(i.get("erro_abs_rs", 0) for i in sel), 2),
                "top": [i.get("sku") or i.get("categoria") for i in sel[:5]]}

    return {
        "itens": itens,
        "agregados": {
            "superestimando":     _soma("Superestimando"),
            "subestimando":       _soma("Subestimando"),
            "erratico":           _soma("Errático"),
            "atencao":            _soma("Atenção"),
            "sob_controle":       _soma("Sob controle"),
            "exposicao_total_rs": round(sum(i.get("erro_abs_rs", 0) for i in itens), 2),
            "excesso_total_rs":   round(sum(i.get("excesso_rs",   0) for i in itens), 2),
            "falta_total_rs":     round(sum(i.get("falta_rs",     0) for i in itens), 2),
        },
        "periodo": {"inicio": ini[:7], "fim": f[:7]},
    }


# ═════════════════════════════════════════════════════════════════════════════
# 1. FILTROS
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/filtros")
async def filtros(db: Session = Depends(get_db),
                  _: dict = Depends(get_current_user)):
    try:
        df = pd.read_sql(text("""
            SELECT DISTINCT bu, categoria, sku, descricao
            FROM dim_produtos
            WHERE COALESCE(ativo, FALSE) = TRUE
            ORDER BY categoria, sku
        """), db.bind)
        return {
            "bus":                sorted(df.bu.dropna().unique().tolist()),
            "categorias":         sorted(df.categoria.dropna().unique().tolist()),
            "skus":               df[["sku", "descricao", "categoria"]].to_dict("records"),
            "ultimo_mes_fechado": _ultimo_mes_fechado(),
            "primeiro_mes":       "2023-01",
        }
    except Exception as e:
        raise HTTPException(500, f"Erro ao carregar filtros: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 2. RESUMO GLOBAL (cards de KPI)
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/resumo")
async def resumo(
    inicio:    Optional[str] = Query(None),
    fim:       Optional[str] = Query(None),
    bu:        Optional[str] = None,
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        ini, f = _periodo(inicio, fim)
        df = _carregar_base(db, ini, f, bu, categoria, sku)
        if df.empty:
            return {"totais": {}, "periodo": {"inicio": ini[:7], "fim": f[:7]}}

        hum  = _metricas(df, "vol_humano")
        g_ia = df[df.vol_ia.notna() & (df.vol_ia > 0)]
        ia   = _metricas(g_ia, "vol_ia") if not g_ia.empty else {}
        fva  = round(ia["wmape"] - hum["wmape"], 4) if (ia.get("wmape") is not None and hum.get("wmape") is not None) else None

        return {
            "totais": {
                **hum,
                "acuracia":       round(max(0, 1 - hum.get("wmape", 1)), 4),
                "wmape_ia":       ia.get("wmape"),
                "vies_ia":        ia.get("vies"),
                "acuracia_ia":    round(max(0, 1 - ia["wmape"]), 4) if ia.get("wmape") is not None else None,
                "meses_com_ia":   ia.get("meses", 0),
                "fva":            fva,
                "skus_avaliados": int(df[df.vol_real > 0].sku.nunique()),
            },
            "periodo": {"inicio": ini[:7], "fim": f[:7],
                        "ultimo_fechado": _ultimo_mes_fechado()},
        }
    except Exception as e:
        raise HTTPException(500, f"Erro no resumo: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. EVOLUÇÃO MENSAL (gráfico de linha)
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/evolucao")
async def evolucao(
    inicio:    Optional[str] = Query(None),
    fim:       Optional[str] = Query(None),
    bu:        Optional[str] = None,
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        ini, f = _periodo(inicio, fim)
        df = _carregar_base(db, ini, f, bu, categoria, sku)
        if df.empty:
            return {"serie": []}

        g = (df.groupby("mes")
               .agg(vol_real   =("vol_real",    "sum"),
                    val_real   =("val_real",    "sum"),
                    val_humano =("val_humano",  "sum"),
                    vol_humano =("vol_humano",  "sum"))
               .reset_index())

        # IA: só meses com dados Nexus (vol_ia > 0)
        ia_mes = (df[df.vol_ia.notna() & (df.vol_ia > 0)]
                  .groupby("mes")
                  .agg(val_ia=("val_ia", "sum"),
                       vol_ia=("vol_ia", "sum"))
                  .reset_index())
        g = g.merge(ia_mes, on="mes", how="left")

        g["wmape_humano"] = np.where(g.val_real > 0,
            (g.val_humano - g.val_real).abs() / g.val_real, np.nan)
        g["bias_humano"]  = np.where(g.val_real > 0,
            (g.val_humano - g.val_real) / g.val_real, np.nan)
        g["wmape_ia"]     = np.where((g.val_real > 0) & g.val_ia.notna(),
            (g.val_ia - g.val_real).abs() / g.val_real, np.nan)
        g["bias_ia"]      = np.where((g.val_real > 0) & g.val_ia.notna(),
            (g.val_ia - g.val_real) / g.val_real, np.nan)

        return {"serie": g.replace({np.nan: None}).round(4).to_dict("records")}
    except Exception as e:
        raise HTTPException(500, f"Erro na evolução: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 4. DIAGNÓSTICO POR SKU / CATEGORIA
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/diagnostico")
async def diagnostico(
    inicio:    Optional[str] = Query(None),
    fim:       Optional[str] = Query(None),
    bu:        Optional[str] = None,
    categoria: Optional[str] = None,
    nivel:     str = Query("sku"),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        ini, f = _periodo(inicio, fim)
        return _calcular_diagnostico(db, ini, f, bu, categoria, nivel)
    except Exception as e:
        raise HTTPException(500, f"Erro no diagnóstico: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 5. HISTÓRICO DE UM SKU (alimenta o dossiê)
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/sku/{sku}/historico")
async def sku_historico(
    sku:    str,
    inicio: Optional[str] = Query(None),
    fim:    Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        ini, f = _periodo(inicio, fim)
        df = _carregar_base(db, ini, f, None, None, sku, apenas_ativos=False)
        if df.empty:
            raise HTTPException(404, f"Sem dados para SKU {sku} no período.")

        df["gap_cx"] = df.vol_humano - df.vol_real
        df["gap_rs"] = df.val_humano - df.val_real
        df["erro_rel_rs"] = np.where(df.val_real > 0,
                                     df.gap_rs / df.val_real, np.nan)

        m   = _metricas(df, "vol_humano")
        cab = df.iloc[0]
        cols = ["mes", "vol_real", "val_real", "vol_humano", "val_humano",
                "vol_ia", "val_ia", "pmv", "gap_cx", "gap_rs",
                "erro_rel_rs", "origem_humano"]
        return {
            "sku":       sku,
            "descricao": cab.descricao,
            "categoria": cab.categoria,
            "bu":        cab.bu,
            "metricas":  {**m, **_classificar(m)},
            "serie":     df[[c for c in cols if c in df.columns]]
                         .replace({np.nan: None}).round(4).to_dict("records"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erro no histórico do SKU {sku}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 6. ALERTAS (consumo pela Visão Geral — badges em categoria e SKU)
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/alertas")
async def alertas(
    inicio: Optional[str] = Query(None),
    fim:    Optional[str] = Query(None),
    nivel:  str = Query("sku"),
    limite: int = Query(20),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        ini, f = _periodo(inicio, fim)
        d = _calcular_diagnostico(db, ini, f, None, None, nivel)
        itens = [i for i in d["itens"] if i.get("severidade", 0) >= 2]
        itens.sort(key=lambda x: (-x["severidade"], -x.get("erro_abs_rs", 0)))

        saida = []
        for i in itens[:limite]:
            rs = f"{i['erro_abs_rs']:,.0f}".replace(",", ".")
            saida.append({
                "chave":            i.get("sku") or i.get("categoria"),
                "descricao":        i.get("descricao") or i.get("categoria"),
                "categoria":        i.get("categoria"),
                "nivel":            "critico" if i["severidade"] >= 3 else "atencao",
                "direcao":          i["direcao"],
                "classe":           i["classe"],
                "acao":             i["acao"],
                "vies_pct":         round(i["vies"] * 100, 1),
                "persistencia_pct": round(i["persistencia"] * 100),
                "erro_rs":          i["erro_abs_rs"],
                "mensagem": (
                    f"{i['classe']} em {abs(i['vies'] * 100):.0f}% "
                    f"({i['persistencia'] * 100:.0f}% dos {i['meses']} meses) "
                    f"— exposição de R$ {rs}"
                ),
            })
        return {"alertas": saida, "periodo": d["periodo"]}
    except Exception as e:
        raise HTTPException(500, f"Erro nos alertas: {e}")