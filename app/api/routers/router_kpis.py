# router_kpis.py — versão com _sdiv + correção fill-rate (ativo) + guarda ZeroDivision no /diagnostico
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


# ─── divisão segura ────────────────────────────────────────────
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


# ─── helpers de data ──────────────────────────────────────
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


# ═══════════════════════════════════════════════════════════════════
# QUERY CENTRAL — LÊ DA CAMADA ANALÍTICA (mart_acuracia_sku_mes)
# ═══════════════════════════════════════════════════════════════════
#
# DEFINIÇÃO OFICIAL DO WMAPE (padrão S&OP)
# ---------------------------------------------------------------------------
# Acurácia de previsão mede contra DEMANDA (qt_pedido), nunca contra embarque.
# O parâmetro `base` continua aceito para não quebrar o frontend, mas NÃO
# altera o denominador do WMAPE. A resposta devolve base_efetiva='pedido'.
#
# TRATAMENTO DE SKU SEM PLANO
# ---------------------------------------------------------------------------
# vol_humano vem COALESCE(qt_plano, 0): vender um item que não estava no plano
# é erro de previsão do tamanho do volume vendido — o plano disse zero.
#
# FILTRO ativo=TRUE (ACURÁCIA): mantido aqui de propósito. WMAPE/BIAS cobram
# previsão de portfolio ativo; item descontinuado não entra na acurácia. Os
# endpoints de fill-rate/corte usam outro critério (qt_pedido>0), porque
# atendimento histórico deve refletir tudo que teve pedido no período.
# ═══════════════════════════════════════════════════════════════════
def _carregar(db: Session, inicio: str, fim: str,
              categoria: Optional[str] = None,
              sku: Optional[str] = None,
              base: str = "pedido") -> pd.DataFrame:
    """
    Lê de mart_acuracia_sku_mes. `base` é aceito por compatibilidade e
    ignorado: o WMAPE é sempre contra a demanda (qt_pedido).
    """
    filtros, params = ["a.ativo = TRUE"], {}
    if categoria:
        filtros.append("a.categoria = :cat"); params["cat"] = categoria
    if sku:
        filtros.append("a.sku = :sku");       params["sku"] = sku
    w = " AND ".join(filtros)

    params["inicio"] = inicio
    params["fim"]    = fim

    sql = text(f"""
        SELECT a.sku,
               COALESCE(p.descricao, a.sku)      AS descricao,
               a.categoria,
               TO_CHAR(a.mes, 'YYYY-MM')         AS mes,
               a.qt_pedido                       AS vol_real,
               COALESCE(a.qt_plano, 0)           AS vol_humano,
               a.qt_ia                           AS vol_ia,
               a.tem_plano,
               a.maturidade,
               a.qt_corte,
               a.qt_entregue
        FROM mart_acuracia_sku_mes a
        LEFT JOIN dim_produtos p ON p.sku = a.sku
        WHERE {w}
          AND a.qt_pedido > 0
          AND a.mes >= CAST(:inicio AS date)
          AND a.mes <= CAST(:fim AS date)
        ORDER BY a.sku, a.mes
    """)

    resultado = db.execute(sql, params or {})
    colunas   = list(resultado.keys())
    df        = pd.DataFrame(resultado.fetchall(), columns=colunas)
    if df.empty:
        return df

    df["vol_real"]    = pd.to_numeric(df["vol_real"],    errors="coerce").fillna(0)
    df["vol_humano"]  = pd.to_numeric(df["vol_humano"],  errors="coerce").fillna(0)
    df["vol_ia"]      = pd.to_numeric(df["vol_ia"],      errors="coerce")
    df["qt_corte"]    = pd.to_numeric(df["qt_corte"],    errors="coerce").fillna(0)
    df["qt_entregue"] = pd.to_numeric(df["qt_entregue"], errors="coerce").fillna(0)
    df["tem_plano"]   = df["tem_plano"].fillna(False).astype(bool)
    return df


# ═══════════════════════════════════════════════════════════════════
# MÉTRICAS POR MÊS
# ═══════════════════════════════════════════════════════════════════
def _serie_metricas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows = []
    for mes, g in df.groupby("mes"):
        row = {"mes": mes}

        if g.vol_real.sum() > 0:
            real_t = float(g.vol_real.sum())
            prev_t = float(g.vol_humano.sum())
            row["wmape_h"] = _sdiv((g.vol_humano - g.vol_real).abs().sum(), real_t, 100)
            row["bias_h"]  = _sdiv(prev_t - real_t, real_t, 100)
            ok = g[g.vol_real > 0].copy()
            if len(ok):
                ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
                row["mape_h"] = round(float(ok.ape.mean()), 2)
            else:
                row["mape_h"] = None
            row["n_skus_h"] = int(len(ok))

            g_p = g[g.tem_plano]
            if not g_p.empty and g_p.vol_real.sum() > 0:
                real_p = float(g_p.vol_real.sum())
                row["wmape_h_planejado"] = round(
                    _sdiv((g_p.vol_humano - g_p.vol_real).abs().sum(), real_p, 100) or 0, 2)
                row["cobertura_plano_pct"] = round(_sdiv(real_p, real_t, 100) or 0, 2)
            else:
                row["wmape_h_planejado"] = None
                row["cobertura_plano_pct"] = 0.0
        else:
            row["wmape_h"] = row["bias_h"] = row["mape_h"] = None
            row["n_skus_h"] = 0
            row["wmape_h_planejado"] = None
            row["cobertura_plano_pct"] = None

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

            wmape_h_comp = _sdiv((g_ia.vol_humano - g_ia.vol_real).abs().sum(), real_ia_t, 100)
            if wmape_h_comp is not None and row["wmape_ia"] is not None:
                row["wmape_h_comparavel"] = round(wmape_h_comp, 2)
                row["fva"] = round(wmape_h_comp - row["wmape_ia"], 2)
            else:
                row["wmape_h_comparavel"] = row["fva"] = None
        else:
            row["wmape_ia"] = row["mape_ia"] = row["bias_ia"] = row["fva"] = None
            row["wmape_h_comparavel"] = None
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


# ═══════════════════════════════════════════════════════════════════
# ROTAS
# ═══════════════════════════════════════════════════════════════════
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
        resumo = {
            "vol_real_total": round(real_t, 0),
            "base_efetiva": "pedido",
            "metrica": "WMAPE de previsao (plano x demanda)",
        }

        if real_t > 0:
            prev_h_t = float(df.vol_humano.sum())
            resumo["wmape_h"]  = _sdiv((df.vol_humano - df.vol_real).abs().sum(), real_t, 100)
            resumo["bias_h"]   = _sdiv(prev_h_t - real_t, real_t, 100)
            if resumo["wmape_h"] is not None: resumo["wmape_h"] = round(resumo["wmape_h"], 2)
            if resumo["bias_h"]  is not None: resumo["bias_h"]  = round(resumo["bias_h"],  2)
            ok = df[df.vol_real > 0].copy()
            if len(ok):
                ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
                resumo["mape_h"] = round(float(ok.ape.mean()), 2)
            resumo["vol_humano_total"] = round(prev_h_t, 0)

            df_p = df[df.tem_plano]
            real_p = float(df_p.vol_real.sum()) if not df_p.empty else 0.0
            if real_p > 0:
                resumo["wmape_h_planejado"] = round(
                    _sdiv((df_p.vol_humano - df_p.vol_real).abs().sum(), real_p, 100) or 0, 2)
                resumo["cobertura_plano_pct"] = round(_sdiv(real_p, real_t, 100) or 0, 2)
                resumo["vol_sem_plano"] = round(real_t - real_p, 0)
            else:
                resumo["wmape_h_planejado"] = None
                resumo["cobertura_plano_pct"] = 0.0
                resumo["vol_sem_plano"] = round(real_t, 0)
        else:
            resumo["wmape_h"] = resumo["bias_h"] = resumo["mape_h"] = resumo["vol_humano_total"] = None
            resumo["wmape_h_planejado"] = resumo["cobertura_plano_pct"] = None

        df_ia = df[df.vol_ia.notna()]
        if not df_ia.empty and df_ia.vol_real.sum() > 0:
            real_ia_t = float(df_ia.vol_real.sum())
            resumo["wmape_ia"] = _sdiv((df_ia.vol_ia - df_ia.vol_real).abs().sum(), real_ia_t, 100)
            resumo["bias_ia"]  = _sdiv(float(df_ia.vol_ia.sum()) - real_ia_t, real_ia_t, 100)
            if resumo["wmape_ia"] is not None: resumo["wmape_ia"] = round(resumo["wmape_ia"], 2)
            if resumo["bias_ia"]  is not None: resumo["bias_ia"]  = round(resumo["bias_ia"],  2)

            wmape_h_comp = _sdiv((df_ia.vol_humano - df_ia.vol_real).abs().sum(), real_ia_t, 100)
            if wmape_h_comp is not None and resumo["wmape_ia"] is not None:
                resumo["wmape_h_comparavel"] = round(wmape_h_comp, 2)
                resumo["fva"] = round(wmape_h_comp - resumo["wmape_ia"], 2)

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

            g_h = g.copy()
            if g_h.vol_real.sum() > 0:
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

                real_p = float(g_h[g_h.tem_plano].vol_real.sum())
                cobertura = _sdiv(real_p, real_h_t, 100)

                item.update({
                    "wmape_h":     round(wmape, 2),
                    "mape_h":      round(mape, 2) if mape is not None else None,
                    "bias_h":      round(bias, 2),
                    "persistencia":round(persist * 100, 1),
                    "vol_previsto":round(prev_h_t, 0),
                    "meses":       int(len(gm)),
                    "cobertura_plano_pct": round(cobertura, 1) if cobertura is not None else 0.0,
                })

                # FIX (ZeroDivisionError): o texto de ação divide por (100+bias).
                # Quando o SKU tem plano mas sum(qt_plano)=0 no recorte, bias=-100
                # exato e (100+bias)=0 -> float division by zero. O guarda
                # `abs(100+bias) > 0.01` cobre esse caso; sem plano não há percentual
                # de ajuste definido, então a ação textual é omitida.
                if cobertura is not None and cobertura < 90:
                    item["classe"] = "Sem cobertura de plano"
                    item["acao"]   = f"Incluir no plano ({100 - cobertura:.0f}% do volume sem previsão)"
                elif wmape <= 20 and abs(bias) <= 10:
                    item["classe"] = "Sob controle"
                elif bias > 10 and persist >= 0.70:
                    item["classe"] = "Superestimando"
                    if abs(100 + bias) > 0.01:
                        item["acao"] = f"Reduzir ~{abs(round(-bias/(100+bias)*100,0)):.0f}%"
                elif bias < -10 and persist >= 0.70:
                    item["classe"] = "Subestimando"
                    if abs(100 + bias) > 0.01:
                        item["acao"] = f"Aumentar ~{abs(round(-bias/(100+bias)*100,0)):.0f}%"
                elif wmape > 30:
                    item["classe"] = "Errático"
                else:
                    item["classe"] = "Atenção"
            else:
                item.update({"wmape_h": None, "mape_h": None, "bias_h": None,
                             "vol_previsto": 0, "meses": 0, "classe": "Sem Meta",
                             "cobertura_plano_pct": 0.0})

            g_ia = g[g.vol_ia.notna()]
            if not g_ia.empty and g_ia.vol_real.sum() > 0:
                ri = float(g_ia.vol_real.sum())
                item["wmape_ia"] = round(_sdiv((g_ia.vol_ia - g_ia.vol_real).abs().sum(), ri, 100) or 0, 2)
                item["bias_ia"]  = round(_sdiv(float(g_ia.vol_ia.sum()) - ri, ri, 100) or 0, 2)
                if item.get("wmape_h") is not None:
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

    Fill Rate = qt_entregue / qt_pedido. Corte = qtcorte do ERP, nunca por
    subtração. unidade='cx' usa qt_*; 'rs' usa vl_*.

    CRITÉRIO DE ESCOPO: inclui TODO SKU que teve pedido no período
    (qt_pedido>0), independente de estar ativo hoje em dim_produtos. O
    fill-rate e o corte históricos precisam refletir o que realmente foi
    pedido — SKUs descontinuados depois de venderem carregam corte real
    (ex.: jan/2026, ~19% do corte vinha de itens hoje inativos) e sair pelo
    filtro ativo=TRUE escondia esse volume. A acurácia (WMAPE) mantém o
    filtro ativo em _carregar; aqui não.
    """
    try:
        teto = _ultimo_mes_fechado()
        meses_validos = sorted({m for m in meses if m <= teto})
        if not meses_validos:
            return {"itens": [], "serie": [], "resumo": {}}

        ini = meses_validos[0]  + "-01"
        fim = meses_validos[-1] + "-01"

        if unidade == "rs":
            c_ped, c_fat, c_cor, c_tra = "vl_pedido", "vl_entregue", "vl_corte", "vl_corte_transferencia"
        else:
            c_ped, c_fat, c_cor, c_tra = "qt_pedido", "qt_entregue", "qt_corte", "qt_corte_transferencia"

        filtros, params = [], {"ini": ini, "fim": fim, "meses": meses_validos}
        if categoria:
            filtros.append("AND v.categoria = :cat"); params["cat"] = categoria
        if sku:
            filtros.append("AND v.sku = :sku"); params["sku"] = sku
        w = " ".join(filtros)

        sel = f"""SUM(v.{c_ped}) AS pedido,
                  SUM(v.{c_fat}) AS entregue,
                  SUM(v.{c_cor}) AS corte,
                  SUM(COALESCE(v.{c_tra}, 0)) AS corte_transf"""

        # FIX (fill-rate ativo): removido `WHERE v.ativo = TRUE`. O escopo agora
        # é qt_pedido>0 (garantido no mart) para incluir SKUs descontinuados que
        # tiveram pedido/corte no período. Ver docstring.
        base_from = f"""
            FROM mart_vendas_mes v
            LEFT JOIN dim_produtos p ON p.sku = v.sku
            WHERE v.qt_pedido > 0
              AND v.mes >= CAST(:ini AS date)
              AND v.mes <= CAST(:fim AS date)
              AND TO_CHAR(v.mes,'YYYY-MM') = ANY(:meses)
              {w}
        """

        def _mont(pedido, entregue, corte, transf=0.0):
            """
            Fill Rate = qt_entregue / qt_pedido. Denominador é o PEDIDO.
            atendimento_ajustado desconta o corte por transferência de código.
            """
            pedido, entregue = float(pedido or 0), float(entregue or 0)
            corte, transf    = float(corte or 0), float(transf or 0)
            carteira = max(pedido - entregue - corte, 0.0)
            at  = round(entregue / pedido * 100, 1) if pedido > 0 else None
            cob = round((entregue + corte) / pedido * 100, 1) if pedido > 0 else None
            ped_aj = pedido - transf
            at_aj  = round(entregue / ped_aj * 100, 1) if ped_aj > 0 else None
            return pedido, entregue, corte, at, carteira, cob, transf, at_aj

        def _classe(at):
            if at is None:  return "Sem dado"
            if at >= 95:    return "Adequado"
            if at >= 85:    return "Atencao"
            return "Restricao"

        if nivel == "evolucao":
            rows = db.execute(text(f"""
                SELECT TO_CHAR(v.mes,'YYYY-MM') AS mes, {sel}
                {base_from}
                GROUP BY 1 ORDER BY 1
            """), params).fetchall()
            serie, tp, te, tc = [], 0.0, 0.0, 0.0
            for r in rows:
                pe, en, co, at, ca, cob, tr, at_aj = _mont(r.pedido, r.entregue, r.corte, r.corte_transf)
                tp += pe; te += en; tc += co
                serie.append({"mes": r.mes, "pedido": round(pe), "entregue": round(en),
                              "corte": round(co), "atendimento": at,
                              "carteira": round(ca), "cobertura": cob,
                              "corte_transferencia": round(tr),
                              "atendimento_ajustado": at_aj})
            td = te + tc
            tt = sum(float(r.corte_transf or 0) for r in rows)
            resumo = {"pedido": round(tp), "entregue": round(te), "corte": round(tc),
                      "carteira": round(max(tp - td, 0.0)),
                      "atendimento": round(te / tp * 100, 1) if tp > 0 else None,
                      "cobertura": round(td / tp * 100, 1) if tp > 0 else None,
                      "corte_transferencia": round(tt),
                      "atendimento_ajustado": round(te / (tp - tt) * 100, 1) if (tp - tt) > 0 else None,
                      "unidade": unidade}
            return {"serie": serie, "resumo": resumo}

        if nivel == "categoria":
            rows = db.execute(text(f"""
                SELECT COALESCE(v.categoria,'SEM CATEGORIA') AS chave, {sel}
                {base_from}
                GROUP BY 1 ORDER BY 4 DESC
            """), params).fetchall()
        else:
            rows = db.execute(text(f"""
                SELECT v.sku AS chave,
                       COALESCE(MAX(p.descricao), v.sku) AS descricao,
                       COALESCE(MAX(v.categoria),'SEM CATEGORIA') AS categoria, {sel}
                {base_from}
                GROUP BY 1
                HAVING SUM(v.{c_cor}) > 0
                ORDER BY 6 DESC
                LIMIT 150
            """), params).fetchall()

        itens = []
        for r in rows:
            pe, en, co, at, ca, cob, tr, at_aj = _mont(r.pedido, r.entregue, r.corte, r.corte_transf)
            it = {"pedido": round(pe), "entregue": round(en), "corte": round(co),
                  "carteira": round(ca), "cobertura": cob,
                  "corte_transferencia": round(tr),
                  "atendimento_ajustado": at_aj,
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
            filtros.append("v.categoria = :cat"); params["cat"] = categoria
        if sku:
            filtros.append("v.sku = :sku"); params["sku"] = sku

        params["inicio"] = meses_validos[0] + "-01"
        params["fim"]    = meses_validos[-1] + "-01"

        w = "AND " + " AND ".join(filtros)

        rows = db.execute(text(f"""
            SELECT
                TO_CHAR(v.mes, 'YYYY-MM') AS mes,
                SUM(v.qt_pedido)   AS pedido,
                SUM(v.qt_entregue) AS faturado,
                SUM(v.qt_corte)    AS corte,
                SUM(v.qt_carteira) AS carteira,
                SUM(COALESCE(v.qt_corte_transferencia, 0)) AS corte_transf
            FROM mart_vendas_mes v
            WHERE v.mes >= CAST(:inicio AS date)
              AND v.mes <= CAST(:fim AS date)
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
            decidido = faturado + corte          # volume com desfecho (p/ cobertura)
            serie.append({
                "mes":       r.mes,
                "pedido":    round(pedido),
                "faturado":  round(faturado),
                "corte":     round(corte),
                "carteira":  round(carteira),
                "fill_rate": round(_sdiv(faturado, pedido, 100), 1) if pedido > 0 else None,
                "cobertura": round(_sdiv(decidido, pedido, 100), 1) if pedido > 0 else None,
                "corte_transferencia": round(float(r.corte_transf or 0)),
            })

        tot_ped = sum(s["pedido"]   for s in serie)
        tot_fat = sum(s["faturado"] for s in serie)
        tot_cor = sum(s["corte"]    for s in serie)
        tot_car = sum(s["carteira"] for s in serie)
        tot_tra = sum(s.get("corte_transferencia", 0) for s in serie)
        tot_dec = tot_fat + tot_cor

        return {
            "serie": serie,
            "resumo": {
                "fill_rate":      round(_sdiv(tot_fat, tot_ped, 100), 1) if tot_ped > 0 else None,
                "pedido_total":   round(tot_ped),
                "faturado_total": round(tot_fat),
                "corte_total":    round(tot_cor),
                "carteira_total": round(tot_car),
                "corte_transferencia_total": round(tot_tra),
                "corte_pct":      round(_sdiv(tot_cor, tot_ped, 100), 1) if tot_ped > 0 else None,
                "cobertura":      round(_sdiv(tot_dec, tot_ped, 100), 1) if tot_ped > 0 else None,
                "fill_rate_ajustado": round(_sdiv(tot_fat, tot_ped - tot_tra, 100), 1)
                                      if (tot_ped - tot_tra) > 0 else None,
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
            filtros.append("v.categoria = :cat"); params["cat"] = categoria

        params["inicio"] = meses_validos[0] + "-01"
        params["fim"]    = meses_validos[-1] + "-01"

        w = "AND " + " AND ".join(filtros)

        if nivel == "categoria":
            group_sel = "v.categoria AS chave, v.categoria AS descricao, v.categoria AS categoria"
            group_by  = "v.categoria"
        else:
            group_sel = ("v.sku AS chave, COALESCE(MAX(p.descricao),'') AS descricao, "
                         "COALESCE(MAX(v.categoria),'') AS categoria")
            group_by  = "v.sku"

        # FIX (fill-rate ativo): removido `AND v.ativo = TRUE`. Mesmo critério do
        # /fill-rate: qt_pedido>0 inclui SKUs descontinuados que tiveram pedido.
        rows = db.execute(text(f"""
            SELECT
                {group_sel},
                SUM(v.qt_pedido)   AS pedido,
                SUM(v.qt_entregue) AS faturado,
                SUM(v.qt_corte)    AS corte,
                SUM(v.qt_carteira) AS carteira,
                SUM(COALESCE(v.qt_corte_transferencia, 0)) AS corte_transf
            FROM mart_vendas_mes v
            LEFT JOIN dim_produtos p ON p.sku = v.sku
            WHERE v.mes >= CAST(:inicio AS date)
              AND v.mes <= CAST(:fim AS date)
              AND v.qt_pedido > 0
              {w}
            GROUP BY {group_by}
            ORDER BY SUM(v.qt_corte) DESC
        """), params).fetchall()

        itens = []
        for r in rows:
            pedido   = float(r.pedido   or 0)
            faturado = float(r.faturado or 0)
            corte    = float(r.corte    or 0)
            carteira = float(r.carteira or 0)
            decidido = faturado + corte          # volume com desfecho
            if pedido <= 0:
                continue
            fr = round(faturado / pedido * 100, 1)
            itens.append({
                "chave":     r.chave,
                "descricao": r.descricao,
                "categoria": r.categoria,
                "pedido":    round(pedido),
                "faturado":  round(faturado),
                "corte":     round(corte),
                "carteira":  round(carteira),
                "fill_rate": fr,
                "corte_pct": round(corte / pedido * 100, 1),
                "cobertura": round(_sdiv(decidido, pedido, 100), 1) if pedido > 0 else None,
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

# ════════════════════════════════════════════════════════════════
# AGENTE DE IA — relatório analítico, chat e PDF
# ════════════════════════════════════════════════════════════════
def _require_lideranca(u: dict = Depends(get_current_user)):
    """Relatório executivo: restrito a Administrador, C-Level e Gerente."""
    if u.get("funcao") not in {"Administrador", "C-Level", "Gerente"}:
        raise HTTPException(403, "Relatório restrito à liderança.")
    return u


@router.get("/agente/dataset")
async def agente_dataset(
    ciclo: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Dataset que fundamenta o relatorio — sem chamar a IA."""
    try:
        return agente_kpis.montar_dataset(db, ciclo or agente_kpis._ciclo_atual(db))
    except Exception as e:
        raise HTTPException(500, f"Erro ao montar dataset: {e}")


@router.get("/agente/relatorio")
async def agente_relatorio(
    ciclo:  Optional[str] = Query(None),
    forcar: bool = Query(False),   # regerar — somente Administrador
    db: Session = Depends(get_db),
    u: dict = Depends(get_current_user),
):
    """Relatorio do ciclo. UM por ciclo_sop, sem filtro nenhum."""
    eh_admin = u.get("funcao") == "Administrador"
    try:
        ciclo_ativo = ciclo or agente_kpis._ciclo_atual(db)
        cache = agente_kpis.relatorio_existente(db, ciclo=ciclo_ativo)

        if cache and not (forcar and eh_admin):
            return {**cache, "pode_regerar": eh_admin}

        if not eh_admin:
            raise HTTPException(
                403,
                "O relatorio deste ciclo ainda nao foi publicado. "
                "Aguarde a geracao pelo Administrador."
            )

        nome = u.get("nome") or u.get("email") or "Administrador"
        res = agente_kpis.gerar_relatorio(
            db, ciclo=ciclo_ativo, usuario=nome, forcar=forcar
        )
        return {**res, "pode_regerar": True}

    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar relatorio: {e}")


@router.get("/agente/relatorio/status")
async def agente_relatorio_status(
    ciclo: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    u: dict = Depends(get_current_user),
):
    """Informa se ja existe relatorio publicado, sem chamar a IA."""
    try:
        ciclo_ativo = ciclo or agente_kpis._ciclo_atual(db)
        cache = agente_kpis.relatorio_existente(db, ciclo=ciclo_ativo)
        jan   = agente_kpis.janela_do_ciclo(ciclo_ativo)
        return {
            "publicado":      bool(cache),
            "gerado_por":     cache.get("gerado_por") if cache else None,
            "gerado_em":      cache.get("gerado_em")  if cache else None,
            "ciclo":          ciclo_ativo,
            "mes_referencia": jan["mes_referencia"],
            "pode_regerar":   u.get("funcao") == "Administrador",
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
        r = agente_kpis.responder_pergunta(
            db, payload.pergunta, payload.meses,
            payload.base, payload.unidade, payload.historico
        )
        if isinstance(r, dict):
            return {"resposta": r.get("resposta", ""), "do_cache": r.get("do_cache", False)}
        return {"resposta": r}
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
