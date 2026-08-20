# router_kpis.py — versão com _sdiv (fix ZeroDivisionError) aplicado
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List
import datetime
import pandas as pd
import numpy as np

from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers import agente_kpis

router = APIRouter(prefix="/api/v1/kpis", tags=["KPIs Acurácia S&OP"])


# ─── divisão segura ───────────────────────────────────────────────────────────
def _sdiv(num, den, mult=1.0):
    """
    Divisão Python segura — retorna None se denominador for 0, NaN ou negativo.

    Necessária porque Python puro levanta ZeroDivisionError em float/0.0,
    enquanto numpy retorna inf silenciosamente. O diagnóstico mistura os dois
    contextos (pandas Series vs float() explícito), então a proteção precisa
    ser explícita em cada divisão crítica.
    """
    try:
        d = float(den)
        if d <= 0 or not (d == d):   # cobre 0.0 e NaN
            return None
        return float(num) / d * float(mult)
    except (ZeroDivisionError, TypeError, ValueError):
        return None


# ─── helpers de data ──────────────────────────────────────────────────────────
def _hoje_br() -> datetime.datetime:
    return datetime.datetime.utcnow() - datetime.timedelta(hours=3)

def _ultimo_mes_fechado() -> str:
    h = _hoje_br()
    return (h.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")

def _primeiro_mes_base() -> str:
    return "2023-01"

def _clamp(mes: Optional[str]) -> str:
    teto = _ultimo_mes_fechado()
    if not mes or mes > teto:
        return teto
    if mes < _primeiro_mes_base():
        return _primeiro_mes_base()
    return mes


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY CENTRAL
# ═══════════════════════════════════════════════════════════════════════════════
def _carregar(db: Session, inicio: str, fim: str,
              categoria: Optional[str] = None,
              sku: Optional[str] = None,
              base: str = "pedido") -> pd.DataFrame:
    """
    base='pedido'   -> realizado = qt_pedido  (demanda do cliente)
    base='faturado' -> realizado = qtfatura   (o que a empresa entregou)
    """
    col_real = "qtfatura" if base == "faturado" else "qt_pedido"

    filtros, params = ["COALESCE(p.ativo, FALSE) = TRUE"], {}
    if categoria:
        filtros.append("p.categoria = :cat"); params["cat"] = categoria
    if sku:
        filtros.append("p.sku = :sku");       params["sku"] = sku
    w = "AND " + " AND ".join(filtros)

    params["inicio"] = inicio
    params["fim"]    = fim

    sql = text(f"""
        WITH ativos AS (
            SELECT p.sku, p.descricao, p.categoria, p.bu
            FROM dim_produtos p
            WHERE 1=1 {w}
        ),
        primeira_venda AS (
            SELECT TRIM(v.sku::text) AS sku,
                   MIN(DATE_TRUNC('month', v.data_pedido))::date AS primeiro_mes
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            GROUP BY 1
        ),
        real AS (
            SELECT TRIM(v.sku::text) AS sku,
                   DATE_TRUNC('month', v.data_pedido)::date AS mes,
                   SUM(v.{col_real}) AS vol_real
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            WHERE v.data_pedido >= :inicio
              AND v.data_pedido < (CAST(:fim AS date) + INTERVAL '1 month')
            GROUP BY 1, 2
        ),
        humano_hist AS (
            SELECT h.sku, h.mes_projetado::date AS mes,
                   SUM(h.vol_humano) AS vol_humano
            FROM fato_previsao_humana h
            JOIN ativos a ON a.sku = h.sku
            WHERE h.fonte = 'HISTORICO'
              AND h.mes_projetado >= :inicio
              AND h.mes_projetado <= :fim
            GROUP BY 1, 2
        ),
        humano_nexus AS (
            SELECT TRIM(i.sku::text) AS sku,
                   i.mes_projetado::date AS mes,
                   SUM(i.vol_final) AS vol_humano
            FROM fato_ibp_granular i
            JOIN ativos a ON a.sku = TRIM(i.sku::text)
            WHERE (
                EXTRACT(YEAR FROM i.mes_projetado) * 12
              + EXTRACT(MONTH FROM i.mes_projetado)
            ) - (
                SPLIT_PART(i.ciclo_sop,'/',2)::int * 12
              + SPLIT_PART(i.ciclo_sop,'/',1)::int
            ) = 2
              AND i.mes_projetado > '2026-05-01'
              AND i.mes_projetado >= :inicio
              AND i.mes_projetado <= :fim
            GROUP BY 1, 2
        ),
        ia AS (
            SELECT TRIM(i.sku::text) AS sku,
                   i.mes_projetado::date AS mes,
                   SUM(i.vol_ia) AS vol_ia
            FROM fato_ibp_granular i
            JOIN ativos a ON a.sku = TRIM(i.sku::text)
            WHERE (
                EXTRACT(YEAR FROM i.mes_projetado) * 12
              + EXTRACT(MONTH FROM i.mes_projetado)
            ) - (
                SPLIT_PART(i.ciclo_sop,'/',2)::int * 12
              + SPLIT_PART(i.ciclo_sop,'/',1)::int
            ) = 2
              AND i.mes_projetado >= :inicio
              AND i.mes_projetado <= :fim
            GROUP BY 1, 2
        ),
        humano AS (
            SELECT sku, mes, vol_humano FROM humano_hist
            UNION ALL
            SELECT sku, mes, vol_humano FROM humano_nexus
        )
        SELECT
            a.sku, a.descricao, a.categoria,
            TO_CHAR(r.mes, 'YYYY-MM') AS mes,
            r.vol_real,
            h.vol_humano AS vol_humano,
            i.vol_ia     AS vol_ia
        FROM real r
        JOIN ativos a ON a.sku = r.sku
        JOIN primeira_venda pv ON pv.sku = r.sku AND r.mes >= pv.primeiro_mes
        LEFT JOIN humano h ON h.sku = r.sku AND h.mes = r.mes
        LEFT JOIN ia     i ON i.sku = r.sku AND i.mes = r.mes
        WHERE r.vol_real > 0
        ORDER BY a.sku, r.mes
    """)

    resultado = db.execute(sql, params or {})
    colunas   = list(resultado.keys())
    df        = pd.DataFrame(resultado.fetchall(), columns=colunas)
    if df.empty:
        return df

    df["vol_real"]   = pd.to_numeric(df["vol_real"],   errors="coerce").fillna(0)
    df["vol_humano"] = pd.to_numeric(df["vol_humano"], errors="coerce")  # NaN = sem meta
    df["vol_ia"]     = pd.to_numeric(df["vol_ia"],     errors="coerce")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# MÉTRICAS POR MÊS
# ═══════════════════════════════════════════════════════════════════════════════
def _serie_metricas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows = []
    for mes, g in df.groupby("mes"):
        row = {"mes": mes}

        g_h = g[g.vol_humano.notna()].copy()
        if not g_h.empty and g_h.vol_real.sum() > 0:
            real_h_t = float(g_h.vol_real.sum())
            prev_h_t = float(g_h.vol_humano.sum())
            row["wmape_h"] = _sdiv((g_h.vol_humano - g_h.vol_real).abs().sum(), real_h_t, 100)
            row["bias_h"]  = _sdiv(prev_h_t - real_h_t, real_h_t, 100)
            ok = g_h[g_h.vol_real > 0].copy()
            if len(ok):
                ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
                row["mape_h"]   = round(float(ok.ape.mean()), 2)
            else:
                row["mape_h"] = None
            row["n_skus_h"] = int(len(ok))
        else:
            row["wmape_h"] = row["bias_h"] = row["mape_h"] = None
            row["n_skus_h"] = 0

        g_ia = g[g.vol_ia.notna()].copy()
        if not g_ia.empty and g_ia.vol_real.sum() > 0:
            real_ia_t = float(g_ia.vol_real.sum())
            prev_ia_t = float(g_ia.vol_ia.sum())
            row["wmape_ia"] = _sdiv((g_ia.vol_ia - g_ia.vol_real).abs().sum(), real_ia_t, 100)
            row["bias_ia"]  = _sdiv(prev_ia_t - real_ia_t, real_ia_t, 100)
            ok_ia = g_ia[g_ia.vol_real > 0].copy()
            if len(ok_ia):
                ok_ia["ape"] = (ok_ia.vol_ia - ok_ia.vol_real).abs() / ok_ia.vol_real * 100
                row["mape_ia"]   = round(float(ok_ia.ape.mean()), 2)
            else:
                row["mape_ia"] = None
            row["n_skus_ia"] = int(len(ok_ia))
            if row["wmape_h"] is not None and row["wmape_ia"] is not None:
                # FVA = WMAPE_Humano - WMAPE_IA
                # Positivo: humano piorou (IA era melhor); Negativo: humano agregou valor
                row["fva"] = round(row["wmape_h"] - row["wmape_ia"], 2)
        else:
            row["wmape_ia"] = row["mape_ia"] = row["bias_ia"] = row["fva"] = None
            row["n_skus_ia"] = 0

        if row["wmape_h"] is not None:
            row["wmape_h"] = round(row["wmape_h"], 2)
        if row["bias_h"] is not None:
            row["bias_h"] = round(row["bias_h"], 2)
        if row["wmape_ia"] is not None:
            row["wmape_ia"] = round(row["wmape_ia"], 2)
        if row["bias_ia"] is not None:
            row["bias_ia"] = round(row["bias_ia"], 2)
        rows.append(row)

    return pd.DataFrame(rows).sort_values("mes")


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS
# ═══════════════════════════════════════════════════════════════════════════════
@router.get("/filtros")
async def filtros(db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    try:
        df = pd.read_sql(text("""
            SELECT DISTINCT categoria, sku, descricao
            FROM dim_produtos
            WHERE COALESCE(ativo, FALSE) = TRUE
            ORDER BY categoria, sku
        """), db.bind)

        ini = datetime.date(2023, 1, 1)
        fim_str = _ultimo_mes_fechado()
        fim = datetime.date(int(fim_str[:4]), int(fim_str[5:]), 1)
        calendario = {}
        cur = ini
        while cur <= fim:
            yr = str(cur.year)
            mn = f"{cur.month:02d}"
            calendario.setdefault(yr, []).append(mn)
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)

        return {
            "categorias": sorted(df.categoria.dropna().unique().tolist()),
            "skus": df[["sku", "descricao", "categoria"]].to_dict("records"),
            "calendario": calendario,
            "ultimo_mes_fechado": fim_str,
            "primeiro_mes": _primeiro_mes_base(),
        }
    except Exception as e:
        raise HTTPException(500, f"Erro nos filtros: {e}")


@router.get("/evolucao")
async def evolucao(
    meses:     List[str] = Query(...),
    base:      str = Query("pedido"),  # pedido | faturado
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        if not meses:
            raise HTTPException(400, "Selecione pelo menos um mês.")
        teto = _ultimo_mes_fechado()
        meses_validos = sorted({m for m in meses if m <= teto})
        if not meses_validos:
            return {"serie": [], "resumo": {}}

        ini_sql = meses_validos[0] + "-01"
        fim_sql = meses_validos[-1] + "-01"
        df = _carregar(db, ini_sql, fim_sql, categoria, sku, base=base)
        if df.empty:
            return {"serie": [], "resumo": {}}
        df = df[df.mes.isin(meses_validos)]
        if df.empty:
            return {"serie": [], "resumo": {}}

        serie = _serie_metricas(df)
        if serie.empty or "mes" not in serie.columns:
            return {"serie": [], "resumo": {}}
        real_t = float(df.vol_real.sum())
        resumo = {"vol_real_total": round(real_t, 0)}

        df_h = df[df.vol_humano.notna()]
        if not df_h.empty and df_h.vol_real.sum() > 0:
            real_h_t = float(df_h.vol_real.sum())
            prev_h_t = float(df_h.vol_humano.sum())
            resumo["wmape_h"]  = _sdiv((df_h.vol_humano - df_h.vol_real).abs().sum(), real_h_t, 100)
            resumo["bias_h"]   = _sdiv(prev_h_t - real_h_t, real_h_t, 100)
            if resumo["wmape_h"] is not None: resumo["wmape_h"] = round(resumo["wmape_h"], 2)
            if resumo["bias_h"]  is not None: resumo["bias_h"]  = round(resumo["bias_h"],  2)
            ok = df_h[df_h.vol_real > 0].copy()
            if len(ok):
                ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
                resumo["mape_h"] = round(float(ok.ape.mean()), 2)
            resumo["vol_humano_total"] = round(prev_h_t, 0)
        else:
            resumo["wmape_h"] = resumo["bias_h"] = resumo["mape_h"] = resumo["vol_humano_total"] = None

        df_ia = df[df.vol_ia.notna()]
        if not df_ia.empty and df_ia.vol_real.sum() > 0:
            real_ia_t = float(df_ia.vol_real.sum())
            resumo["wmape_ia"] = _sdiv((df_ia.vol_ia - df_ia.vol_real).abs().sum(), real_ia_t, 100)
            resumo["bias_ia"]  = _sdiv(float(df_ia.vol_ia.sum()) - real_ia_t, real_ia_t, 100)
            if resumo["wmape_ia"] is not None: resumo["wmape_ia"] = round(resumo["wmape_ia"], 2)
            if resumo["bias_ia"]  is not None: resumo["bias_ia"]  = round(resumo["bias_ia"],  2)
            if resumo.get("wmape_h") is not None and resumo.get("wmape_ia") is not None:
                # FVA = WMAPE_Humano - WMAPE_IA
                resumo["fva"] = round(resumo["wmape_h"] - resumo["wmape_ia"], 2)
            ok_ia = df_ia[df_ia.vol_real > 0].copy()
            if len(ok_ia):
                ok_ia["ape"] = (ok_ia.vol_ia - ok_ia.vol_real).abs() / ok_ia.vol_real * 100
                resumo["mape_ia"] = round(float(ok_ia.ape.mean()), 2)

        resumo["meses_analisados"] = int(serie.mes.nunique())
        resumo["meses_com_ia"]     = int(serie.wmape_ia.notna().sum() if "wmape_ia" in serie.columns else 0)

        return {"serie": serie.replace({np.nan: None}).to_dict("records"), "resumo": resumo}
    except Exception as e:
        raise HTTPException(500, f"Erro na evolução: {e}")


@router.get("/diagnostico")
async def diagnostico(
    meses:     List[str] = Query(...),
    base:      str = Query("pedido"),  # pedido | faturado
    categoria: Optional[str] = None,
    nivel:     str = Query("sku"),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        teto = _ultimo_mes_fechado()
        meses_validos = sorted({m for m in meses if m <= teto})
        if not meses_validos:
            return {"itens": [], "agregados": {}}

        ini_sql = meses_validos[0] + "-01"
        fim_sql = meses_validos[-1] + "-01"
        df = _carregar(db, ini_sql, fim_sql, categoria, None, base=base)
        if df.empty:
            return {"itens": [], "agregados": {}}
        df = df[df.mes.isin(meses_validos)]

        chave = ["sku", "descricao", "categoria"] if nivel == "sku" else ["categoria"]
        itens = []
        for vals, g in df.groupby(chave):
            if isinstance(vals, str): vals = (vals,)
            real_t = float(g.vol_real.sum())
            if real_t <= 0:
                continue

            item = dict(zip(chave, vals))
            item["vol_real"] = round(real_t, 0)

            g_h = g[g.vol_humano.notna()].copy()
            if not g_h.empty and g_h.vol_real.sum() > 0:
                real_h_t = float(g_h.vol_real.sum())
                prev_h_t = float(g_h.vol_humano.sum())

                wmape = _sdiv((g_h.vol_humano - g_h.vol_real).abs().sum(), real_h_t, 100) or 0.0
                bias  = _sdiv(prev_h_t - real_h_t, real_h_t, 100) or 0.0

                ok = g_h[g_h.vol_real > 0].copy()
                mape = None
                if len(ok):
                    ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
                    mape = float(ok.ape.mean())

                gm = g_h.groupby("mes").agg(r=("vol_real","sum"), p=("vol_humano","sum")).reset_index()
                gm = gm[gm.r > 0].dropna(subset=["r", "p"]).copy()
                if len(gm):
                    gm["er"] = (gm.p - gm.r) / gm.r  # pandas division — seguro após dropna
                    persist = float((gm.er > 0.02).mean() if bias >= 0 else (gm.er < -0.02).mean())
                else:
                    persist = 0.0

                item.update({
                    "wmape_h":     round(wmape, 2),
                    "mape_h":      round(mape, 2) if mape is not None else None,
                    "bias_h":      round(bias, 2),
                    "persistencia":round(persist * 100, 1),
                    "vol_previsto":round(prev_h_t, 0),
                    "meses":       int(len(gm)),
                })

                if wmape <= 20 and abs(bias) <= 10:
                    item["classe"] = "Sob controle"
                elif bias > 10 and persist >= 0.70:
                    item["classe"] = "Superestimando"
                    item["acao"]   = f"Reduzir ~{abs(round(-bias/(100+bias)*100,0)):.0f}%"
                elif bias < -10 and persist >= 0.70:
                    item["classe"] = "Subestimando"
                    item["acao"]   = f"Aumentar ~{abs(round(-bias/(100+bias)*100,0)):.0f}%"
                elif wmape > 30:
                    item["classe"] = "Errático"
                else:
                    item["classe"] = "Atenção"
            else:
                item.update({"wmape_h": None, "mape_h": None, "bias_h": None,
                             "vol_previsto": 0, "meses": 0, "classe": "Sem Meta"})

            g_ia = g[g.vol_ia.notna()]
            if not g_ia.empty and g_ia.vol_real.sum() > 0:
                ri = float(g_ia.vol_real.sum())
                item["wmape_ia"] = round(_sdiv((g_ia.vol_ia - g_ia.vol_real).abs().sum(), ri, 100) or 0, 2)
                item["bias_ia"]  = round(_sdiv(float(g_ia.vol_ia.sum()) - ri, ri, 100) or 0, 2)
                if item.get("wmape_h") is not None:
                    # FVA = WMAPE_Humano - WMAPE_IA
                    # Positivo: humano piorou em relação à IA (IA era melhor)
                    # Negativo: humano melhorou em relação à IA (humano agregou valor)
                    item["fva"] = round(item["wmape_h"] - item["wmape_ia"], 2)

            itens.append(item)

        itens.sort(key=lambda x: x.get("wmape_h") or -1, reverse=True)
        return {"itens": itens}
    except Exception as e:
        raise HTTPException(500, f"Erro no diagnóstico: {e}")


@router.get("/fill-rate")
async def fill_rate(
    meses:     List[str] = Query(...),
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    unidade:   str = Query("cx"),          # cx | rs
    nivel:     str = Query("evolucao"),    # evolucao | categoria | sku
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """
    Atendimento (fill rate) sobre o volume JA DECIDIDO.

    A fato_vendas obedece a identidade de tres estados:
        qt_pedido = qtfatura + qtcorte + carteira_em_aberto

    O denominador do fill rate e (qtfatura + qtcorte), NAO qt_pedido. Usar
    qt_pedido joga a carteira em aberto contra o indicador e derruba o mes
    corrente artificialmente (08/2026 exibia 58% quando o real era 97,9%).

    Corte = qtcorte/vlcorte, valor do ERP. Nunca por subtracao.
    unidade='cx' usa qt_pedido/qtfatura/qtcorte; 'rs' usa vl_pedido/vlfatura/vlcorte.
    """
    try:
        teto = _ultimo_mes_fechado()
        meses_validos = sorted({m for m in meses if m <= teto})
        if not meses_validos:
            return {"itens": [], "serie": [], "resumo": {}}

        ini = meses_validos[0]  + "-01"
        fim = meses_validos[-1] + "-01"

        if unidade == "rs":
            c_ped, c_fat, c_cor = "vl_pedido", "vlfatura", "vlcorte"
        else:
            c_ped, c_fat, c_cor = "qt_pedido", "qtfatura", "qtcorte"

        filtros, params = [], {"ini": ini, "fim": fim, "meses": meses_validos}
        if categoria:
            filtros.append("AND p.categoria = :cat"); params["cat"] = categoria
        if sku:
            filtros.append("AND TRIM(v.sku::text) = :sku"); params["sku"] = sku
        w = " ".join(filtros)

        sel = f"""SUM(v.{c_ped}) AS pedido,
                  SUM(v.{c_fat}) AS entregue,
                  SUM(v.{c_cor}) AS corte"""

        base_from = f"""
            FROM fato_vendas v
            JOIN dim_produtos p ON p.sku = TRIM(v.sku::text)
            WHERE COALESCE(p.ativo, FALSE) = TRUE
              AND v.data_pedido >= :ini
              AND v.data_pedido < (CAST(:fim AS date) + INTERVAL '1 month')
              AND TO_CHAR(DATE_TRUNC('month', v.data_pedido),'YYYY-MM') = ANY(:meses)
              {w}
        """

        def _mont(pedido, entregue, corte):
            pedido, entregue, corte = float(pedido or 0), float(entregue or 0), float(corte or 0)
            decidido = entregue + corte
            carteira = max(pedido - decidido, 0.0)
            at  = round(entregue / decidido * 100, 1) if decidido > 0 else None
            cob = round(decidido / pedido * 100, 1) if pedido > 0 else None
            return pedido, entregue, corte, at, carteira, cob

        def _classe(at):
            if at is None:  return "Sem dado"
            if at >= 95:    return "Adequado"
            if at >= 85:    return "Atencao"
            return "Restricao"

        if nivel == "evolucao":
            rows = db.execute(text(f"""
                SELECT TO_CHAR(DATE_TRUNC('month', v.data_pedido),'YYYY-MM') AS mes, {sel}
                {base_from}
                GROUP BY 1 ORDER BY 1
            """), params).fetchall()
            serie, tp, te, tc = [], 0.0, 0.0, 0.0
            for r in rows:
                pe, en, co, at, ca, cob = _mont(r.pedido, r.entregue, r.corte)
                tp += pe; te += en; tc += co
                serie.append({"mes": r.mes, "pedido": round(pe), "entregue": round(en),
                              "corte": round(co), "atendimento": at,
                              "carteira": round(ca), "cobertura": cob})
            td = te + tc
            resumo = {"pedido": round(tp), "entregue": round(te), "corte": round(tc),
                      "carteira": round(max(tp - td, 0.0)),
                      "atendimento": round(te / td * 100, 1) if td > 0 else None,
                      "cobertura": round(td / tp * 100, 1) if tp > 0 else None,
                      "unidade": unidade}
            return {"serie": serie, "resumo": resumo}

        if nivel == "categoria":
            rows = db.execute(text(f"""
                SELECT COALESCE(p.categoria,'SEM CATEGORIA') AS chave, {sel}
                {base_from}
                GROUP BY 1 ORDER BY 4 DESC
            """), params).fetchall()
        else:
            rows = db.execute(text(f"""
                SELECT TRIM(v.sku::text) AS chave, p.descricao,
                       COALESCE(p.categoria,'SEM CATEGORIA') AS categoria, {sel}
                {base_from}
                GROUP BY 1, 2, 3
                HAVING SUM(v.{c_cor}) > 0
                ORDER BY 5 DESC
                LIMIT 150
            """), params).fetchall()

        itens = []
        for r in rows:
            pe, en, co, at, ca, cob = _mont(r.pedido, r.entregue, r.corte)
            it = {"pedido": round(pe), "entregue": round(en), "corte": round(co),
                  "carteira": round(ca), "cobertura": cob,
                  "atendimento": at, "classe": _classe(at)}
            if nivel == "categoria":
                it["categoria"] = r.chave
            else:
                it["sku"] = r.chave
                it["descricao"] = r.descricao
                it["categoria"] = r.categoria
            itens.append(it)
        return {"itens": itens, "unidade": unidade}

    except Exception as e:
        raise HTTPException(500, f"Erro no atendimento: {e}")


@router.get("/alertas")
async def alertas(limite: int = Query(20), db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    try:
        teto = _ultimo_mes_fechado()
        h = _hoje_br().replace(day=1)
        meses = []
        for i in range(6):
            d = h - datetime.timedelta(days=i * 30)
            m = d.strftime("%Y-%m")
            if m <= teto: meses.append(m)

        d = await diagnostico(meses=meses, nivel="sku", db=db, _=_)
        criticos = [i for i in d["itens"] if i.get("classe") in ("Superestimando", "Subestimando", "Errático")]
        criticos.sort(key=lambda x: abs(x.get("bias_h") or 0), reverse=True)
        return {"alertas": criticos[:limite]}
    except Exception as e:
        raise HTTPException(500, f"Erro nos alertas: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# FILL RATE — MODELO DE TRÊS ESTADOS
#
# A fato_vendas nasce do relatório de pendência e obedece à identidade:
#
#       qt_pedido = qtfatura + qtcorte + carteira_em_aberto
#
# Um pedido tem TRÊS destinos, não dois: foi faturado, foi cortado, ou ainda
# aguarda decisão. Validado no banco (jan/2025 a ago/2026): a carteira é ruído
# (±800 cx) em todo mês fechado e 28.087 cx em 08/2026 no dia 20 — exatamente
# o volume ainda em aberto no mês corrente.
#
# NUNCA usar (qt_pedido - qtfatura) como corte: isso conta carteira em aberto
# como ruptura. Com a fórmula antiga, agosto/2026 exibia 58% de fill rate e
# 28.961 cx de corte, quando o real era 97,9% e 874 cx.
#
#       Fill Rate = qtfatura / (qtfatura + qtcorte)    -> execução do decidido
#       Corte     = qtcorte                             -> valor do ERP, canônico
#       Carteira  = qt_pedido - qtfatura - qtcorte      -> ainda indefinido
#       Cobertura = (qtfatura + qtcorte) / qt_pedido    -> % do pedido resolvido
#
# As três rotas de fill rate (/fill-rate, /fill-rate/evolucao,
# /fill-rate/diagnostico) usam esta mesma definição. Qualquer divergência entre
# elas é bug.
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/fill-rate/evolucao")
async def fill_rate_evolucao(
    meses:     List[str] = Query(...),
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Evolução mensal do Fill Rate — série para gráfico de linha."""
    try:
        teto = _ultimo_mes_fechado()
        meses_validos = sorted({m for m in meses if m <= teto})
        if not meses_validos:
            return {"serie": [], "resumo": {}}

        filtros, params = ["1=1"], {}
        if categoria:
            filtros.append("p.categoria = :cat"); params["cat"] = categoria
        if sku:
            filtros.append("v.sku = :sku"); params["sku"] = sku

        params["inicio"] = meses_validos[0] + "-01"
        params["fim"]    = meses_validos[-1] + "-01"

        w = "AND " + " AND ".join(filtros)

        rows = db.execute(text(f"""
            SELECT
                TO_CHAR(DATE_TRUNC('month', v.data_pedido), 'YYYY-MM') AS mes,
                SUM(v.qt_pedido) AS pedido,
                SUM(v.qtfatura)  AS faturado,
                SUM(v.qtcorte)   AS corte,
                SUM(GREATEST(v.qt_pedido - v.qtfatura - v.qtcorte, 0)) AS carteira
            FROM fato_vendas v
            LEFT JOIN dim_produtos p ON p.sku = v.sku
            WHERE v.data_pedido >= :inicio
              AND v.data_pedido < (CAST(:fim AS date) + INTERVAL '1 month')
              AND v.qt_pedido > 0
              {w}
            GROUP BY 1
            ORDER BY 1
        """), params).fetchall()

        serie = []
        for r in rows:
            if r.mes not in meses_validos:
                continue
            pedido   = float(r.pedido   or 0)
            faturado = float(r.faturado or 0)
            corte    = float(r.corte    or 0)
            carteira = float(r.carteira or 0)
            decidido = faturado + corte          # denominador do fill rate
            serie.append({
                "mes":       r.mes,
                "pedido":    round(pedido),
                "faturado":  round(faturado),
                "corte":     round(corte),
                "carteira":  round(carteira),
                "fill_rate": round(_sdiv(faturado, decidido, 100), 1) if decidido > 0 else None,
                "cobertura": round(_sdiv(decidido, pedido, 100), 1) if pedido > 0 else None,
            })

        # Resumo acumulado — sempre sobre o volume decidido, nunca sobre o pedido
        tot_ped = sum(s["pedido"]   for s in serie)
        tot_fat = sum(s["faturado"] for s in serie)
        tot_cor = sum(s["corte"]    for s in serie)
        tot_car = sum(s["carteira"] for s in serie)
        tot_dec = tot_fat + tot_cor

        return {
            "serie": serie,
            "resumo": {
                "fill_rate":      round(_sdiv(tot_fat, tot_dec, 100), 1) if tot_dec > 0 else None,
                "pedido_total":   round(tot_ped),
                "faturado_total": round(tot_fat),
                "corte_total":    round(tot_cor),
                "carteira_total": round(tot_car),
                "corte_pct":      round(_sdiv(tot_cor, tot_dec, 100), 1) if tot_dec > 0 else None,
                "cobertura":      round(_sdiv(tot_dec, tot_ped, 100), 1) if tot_ped > 0 else None,
            }
        }
    except Exception as e:
        raise HTTPException(500, f"Erro no fill rate evolução: {e}")


@router.get("/fill-rate/diagnostico")
async def fill_rate_diagnostico(
    meses:     List[str] = Query(...),
    categoria: Optional[str] = None,
    nivel:     str = Query("sku"),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Fill Rate por categoria ou SKU — tabela de diagnóstico."""
    try:
        teto = _ultimo_mes_fechado()
        meses_validos = sorted({m for m in meses if m <= teto})
        if not meses_validos:
            return {"itens": []}

        filtros, params = ["1=1"], {}
        if categoria:
            filtros.append("p.categoria = :cat"); params["cat"] = categoria

        params["inicio"] = meses_validos[0] + "-01"
        params["fim"]    = meses_validos[-1] + "-01"

        w = "AND " + " AND ".join(filtros)

        # Agrupamento dinâmico: por categoria ou por SKU
        if nivel == "categoria":
            group_sel = "p.categoria AS chave, p.categoria AS descricao, p.categoria AS categoria"
            group_by  = "p.categoria"
        else:
            group_sel = "v.sku AS chave, COALESCE(p.descricao,'') AS descricao, COALESCE(p.categoria,'') AS categoria"
            group_by  = "v.sku, p.descricao, p.categoria"

        rows = db.execute(text(f"""
            SELECT
                {group_sel},
                SUM(v.qt_pedido) AS pedido,
                SUM(v.qtfatura)  AS faturado,
                SUM(v.qtcorte)   AS corte,
                SUM(GREATEST(v.qt_pedido - v.qtfatura - v.qtcorte, 0)) AS carteira
            FROM fato_vendas v
            LEFT JOIN dim_produtos p ON p.sku = v.sku
            WHERE v.data_pedido >= :inicio
              AND v.data_pedido < (CAST(:fim AS date) + INTERVAL '1 month')
              AND v.qt_pedido > 0
              AND COALESCE(p.ativo, FALSE) = TRUE
              {w}
            GROUP BY {group_by}
            ORDER BY SUM(v.qtcorte) DESC
        """), params).fetchall()

        itens = []
        for r in rows:
            pedido   = float(r.pedido   or 0)
            faturado = float(r.faturado or 0)
            corte    = float(r.corte    or 0)
            carteira = float(r.carteira or 0)
            decidido = faturado + corte
            if decidido <= 0:
                continue
            fr = round(faturado / decidido * 100, 1)
            itens.append({
                "chave":     r.chave,
                "descricao": r.descricao,
                "categoria": r.categoria,
                "pedido":    round(pedido),
                "faturado":  round(faturado),
                "corte":     round(corte),
                "carteira":  round(carteira),
                "fill_rate": fr,
                "corte_pct": round(corte / decidido * 100, 1),
                "cobertura": round(_sdiv(decidido, pedido, 100), 1) if pedido > 0 else None,
                # Classificação por nível de fill rate
                "classe": (
                    "Crítico"   if fr < 85 else
                    "Atenção"   if fr < 93 else
                    "Adequado"  if fr < 98 else
                    "Excelente"
                ),
            })

        return {"itens": itens}
    except Exception as e:
        raise HTTPException(500, f"Erro no fill rate diagnóstico: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# AGENTE DE IA — relatório analítico, chat e PDF
# ═══════════════════════════════════════════════════════════════════════════
def _require_lideranca(u: dict = Depends(get_current_user)):
    """Relatório executivo: restrito a Administrador, C-Level e Gerente."""
    if u.get("funcao") not in {"Administrador", "C-Level", "Gerente"}:
        raise HTTPException(403, "Relatório restrito à liderança.")
    return u


@router.get("/agente/dataset")
async def agente_dataset(
    meses:   List[str] = Query(...),
    base:    str = Query("pedido"),
    unidade: str = Query("cx"),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Dataset consolidado que fundamenta o relatório — sem chamar a IA."""
    try:
        return agente_kpis.montar_dataset(db, meses, base, unidade)
    except Exception as e:
        raise HTTPException(500, f"Erro ao montar dataset: {e}")


@router.get("/agente/relatorio")
async def agente_relatorio(
    meses:   List[str] = Query(...),
    base:    str = Query("pedido"),
    unidade: str = Query("cx"),
    forcar:  bool = Query(False),   # regerar — somente Administrador
    db: Session = Depends(get_db),
    u: dict = Depends(get_current_user),
):
    """
    Relatório do ciclo. Gerado uma única vez e compartilhado por todos.
    Somente Administrador pode gerar pela primeira vez ou regerar.
    Demais usuários leem o relatório já publicado.
    """
    eh_admin = u.get("funcao") == "Administrador"
    try:
        # Já existe relatório publicado para este recorte?
        cache = agente_kpis.relatorio_existente(db, meses, base, unidade)

        if cache and not (forcar and eh_admin):
            ds = agente_kpis.montar_dataset(db, meses, base, unidade)
            return {**cache, "dataset": ds, "pode_regerar": eh_admin}

        if not eh_admin:
            raise HTTPException(
                403,
                "O relatório deste ciclo ainda não foi publicado. "
                "Aguarde a geração pelo Administrador."
            )

        nome = u.get("nome") or u.get("email") or "Administrador"
        res = agente_kpis.gerar_relatorio(db, meses, base, unidade,
                                          usuario=nome, forcar=forcar)
        return {**res, "pode_regerar": True}

    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar relatório: {e}")


@router.get("/agente/relatorio/status")
async def agente_relatorio_status(
    meses:   List[str] = Query(...),
    base:    str = Query("pedido"),
    unidade: str = Query("cx"),
    db: Session = Depends(get_db),
    u: dict = Depends(get_current_user),
):
    """Informa se já existe relatório publicado, sem chamar a IA."""
    try:
        cache = agente_kpis.relatorio_existente(db, meses, base, unidade)
        return {
            "publicado":    bool(cache),
            "gerado_por":   cache.get("gerado_por") if cache else None,
            "gerado_em":    cache.get("gerado_em")  if cache else None,
            "ciclo":        cache.get("ciclo")      if cache else None,
            "pode_regerar": u.get("funcao") == "Administrador",
        }
    except Exception as e:
        raise HTTPException(500, f"Erro ao consultar status: {e}")


class PerguntaAgente(BaseModel):
    pergunta:  str
    meses:     List[str]
    base:      str = "pedido"
    unidade:   str = "cx"
    historico: Optional[List[dict]] = None


@router.post("/agente/chat")
async def agente_chat(
    payload: PerguntaAgente,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Chat: responde perguntas ad-hoc sobre os indicadores do recorte atual."""
    try:
        resposta = agente_kpis.responder_pergunta(
            db, payload.pergunta, payload.meses,
            payload.base, payload.unidade, payload.historico
        )
        return {"resposta": resposta}
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro no chat: {e}")


class PdfPayload(BaseModel):
    relatorio: str
    meses:     List[str]
    base:      str = "pedido"
    unidade:   str = "cx"


@router.post("/agente/pdf")
async def agente_pdf(
    payload: PdfPayload,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Gera o PDF one-page + anexos com a análise já produzida."""
    try:
        ds  = agente_kpis.montar_dataset(db, payload.meses, payload.base, payload.unidade)
        pdf = agente_kpis.gerar_pdf(payload.relatorio, ds)
        nome = f"relatorio_sop_{payload.meses[0]}_{payload.meses[-1]}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'}
        )
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar PDF: {e}")