# router_kpis.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List
import datetime
import pandas as pd
import numpy as np

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["KPIs Acurácia S&OP"])

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

# ═════════════════════════════════════════════════════════════════════════════
# QUERY CENTRAL
# ═════════════════════════════════════════════════════════════════════════════
def _carregar(db: Session, inicio: str, fim: str,
              categoria: Optional[str] = None,
              sku: Optional[str] = None) -> pd.DataFrame:
    
    filtros, params = ["COALESCE(p.ativo, FALSE) = TRUE"], {}
    if categoria:
        filtros.append("p.categoria = :cat"); params["cat"] = categoria
    if sku:
        filtros.append("p.sku = :sku");       params["sku"] = sku
    w = "AND " + " AND ".join(filtros)

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
                   SUM(v.qt_pedido) AS vol_real
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            WHERE v.data_pedido >= '{inicio}'
              AND v.data_pedido < ('{fim}'::date + INTERVAL '1 month')
            GROUP BY 1, 2
        ),
        humano_hist AS (
            SELECT h.sku, h.mes_projetado::date AS mes,
                   SUM(h.vol_humano) AS vol_humano
            FROM fato_previsao_humana h
            JOIN ativos a ON a.sku = h.sku
            WHERE h.fonte = 'HISTORICO'
              AND h.mes_projetado >= '{inicio}'
              AND h.mes_projetado <= '{fim}'
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
              AND i.mes_projetado >= '{inicio}'
              AND i.mes_projetado <= '{fim}'
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
              AND i.mes_projetado >= '{inicio}'
              AND i.mes_projetado <= '{fim}'
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
            h.vol_humano AS vol_humano, -- Agora permite Nulo/NaN
            i.vol_ia     AS vol_ia      
        FROM real r
        JOIN ativos a ON a.sku = r.sku
        JOIN primeira_venda pv ON pv.sku = r.sku AND r.mes >= pv.primeiro_mes
        LEFT JOIN humano h ON h.sku = r.sku AND h.mes = r.mes
        LEFT JOIN ia     i ON i.sku = r.sku AND i.mes = r.mes
        WHERE r.vol_real > 0  -- Não cortamos mais a falta de meta humana!
        ORDER BY a.sku, r.mes
    """)

    df = pd.read_sql(sql, db.bind, params=params or None)
    if df.empty:
        return df

    df["vol_real"] = pd.to_numeric(df["vol_real"], errors="coerce").fillna(0)
    df["vol_humano"] = pd.to_numeric(df["vol_humano"], errors="coerce") # Mantém NaN se faltar
    df["vol_ia"] = pd.to_numeric(df["vol_ia"], errors="coerce")
    return df

# ═════════════════════════════════════════════════════════════════════════════
# CÁLCULO DAS MÉTRICAS POR MÊS
# ═════════════════════════════════════════════════════════════════════════════
def _serie_metricas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows = []
    for mes, g in df.groupby("mes"):
        row = {"mes": mes}

        # ── HUMANO ──────────────────────────────────────────────────────────
        g_h = g[g.vol_humano.notna()].copy()
        if not g_h.empty and g_h.vol_real.sum() > 0:
            real_h_t = g_h.vol_real.sum()
            prev_h_t = g_h.vol_humano.sum()

            row["wmape_h"] = round((g_h.vol_humano - g_h.vol_real).abs().sum() / real_h_t * 100, 2)
            row["bias_h"]  = round((prev_h_t - real_h_t) / real_h_t * 100, 2)

            ok = g_h[g_h.vol_real > 0].copy()
            ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
            row["mape_h"]   = round(ok.ape.mean(), 2) if len(ok) else None
            row["n_skus_h"] = int(len(ok))
        else:
            row["wmape_h"] = row["bias_h"] = row["mape_h"] = None
            row["n_skus_h"] = 0

        # ── IA ──────────────────────────────────────────────────────────────
        g_ia = g[g.vol_ia.notna()].copy()
        if not g_ia.empty and g_ia.vol_real.sum() > 0:
            real_ia_t = g_ia.vol_real.sum()
            prev_ia_t = g_ia.vol_ia.sum()

            row["wmape_ia"] = round((g_ia.vol_ia - g_ia.vol_real).abs().sum() / real_ia_t * 100, 2)
            row["bias_ia"]  = round((prev_ia_t - real_ia_t) / real_ia_t * 100, 2)

            ok_ia = g_ia[g_ia.vol_real > 0].copy()
            ok_ia["ape"] = (ok_ia.vol_ia - ok_ia.vol_real).abs() / ok_ia.vol_real * 100
            row["mape_ia"]   = round(ok_ia.ape.mean(), 2) if len(ok_ia) else None
            row["n_skus_ia"] = int(len(ok_ia))

            if row["wmape_h"] is not None:
                row["fva"] = round(row["wmape_ia"] - row["wmape_h"], 2)
        else:
            row["wmape_ia"] = row["mape_ia"] = row["bias_ia"] = row["fva"] = None
            row["n_skus_ia"] = 0

        rows.append(row)

    return pd.DataFrame(rows).sort_values("mes")

# ═════════════════════════════════════════════════════════════════════════════
# ROTAS
# ═════════════════════════════════════════════════════════════════════════════
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

        df = _carregar(db, ini_sql, fim_sql, categoria, sku)
        if df.empty:
            return {"serie": [], "resumo": {}}

        df = df[df.mes.isin(meses_validos)]
        if df.empty:
            return {"serie": [], "resumo": {}}

        serie = _serie_metricas(df)

        # Resumo Global
        real_t = float(df.vol_real.sum()) # Real inegociável para a Tela
        
        resumo = {"vol_real_total": round(real_t, 0)}
        
        df_h = df[df.vol_humano.notna()]
        if not df_h.empty and df_h.vol_real.sum() > 0:
            real_h_t = float(df_h.vol_real.sum())
            prev_h_t = float(df_h.vol_humano.sum())
            resumo["wmape_h"]  = round((df_h.vol_humano - df_h.vol_real).abs().sum() / real_h_t * 100, 2)
            resumo["bias_h"]   = round((prev_h_t - real_h_t) / real_h_t * 100, 2)
            
            ok = df_h[df_h.vol_real > 0].copy()
            ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
            resumo["mape_h"]   = round(ok.ape.mean(), 2) if len(ok) else None
            resumo["vol_humano_total"]= round(prev_h_t, 0)
        else:
            resumo["wmape_h"] = resumo["bias_h"] = resumo["mape_h"] = resumo["vol_humano_total"] = None

        df_ia = df[df.vol_ia.notna()]
        if not df_ia.empty and df_ia.vol_real.sum() > 0:
            real_ia_t = float(df_ia.vol_real.sum())
            resumo["wmape_ia"] = round((df_ia.vol_ia - df_ia.vol_real).abs().sum() / real_ia_t * 100, 2)
            resumo["bias_ia"]  = round((float(df_ia.vol_ia.sum()) - real_ia_t) / real_ia_t * 100, 2)
            if resumo["wmape_h"] is not None:
                resumo["fva"]  = round(resumo["wmape_ia"] - resumo["wmape_h"], 2)
            
            ok_ia = df_ia[df_ia.vol_real > 0].copy()
            ok_ia["ape"] = (ok_ia.vol_ia - ok_ia.vol_real).abs() / ok_ia.vol_real * 100
            resumo["mape_ia"]  = round(ok_ia.ape.mean(), 2) if len(ok_ia) else None

        resumo["meses_analisados"] = int(serie.mes.nunique())
        resumo["meses_com_ia"]     = int(serie.wmape_ia.notna().sum() if "wmape_ia" in serie else 0)

        return {"serie": serie.replace({np.nan: None}).to_dict("records"), "resumo": resumo}
    except Exception as e:
        raise HTTPException(500, f"Erro na evolução: {e}")


@router.get("/diagnostico")
async def diagnostico(
    meses:     List[str] = Query(...),
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
        df = _carregar(db, ini_sql, fim_sql, categoria, None)
        if df.empty:
            return {"itens": [], "agregados": {}}

        df = df[df.mes.isin(meses_validos)]

        chave = ["sku", "descricao", "categoria"] if nivel == "sku" else ["categoria"]
        itens = []
        for vals, g in df.groupby(chave):
            if isinstance(vals, str): vals = (vals,)
            
            # Real Comercial Absoluto
            real_t = float(g.vol_real.sum())
            if real_t <= 0:
                continue

            item = dict(zip(chave, vals))
            item["vol_real"] = round(real_t, 0) # Real inegociável
            
            # Filtro das Metas Humanas
            g_h = g[g.vol_humano.notna()].copy()
            if not g_h.empty and g_h.vol_real.sum() > 0:
                real_h_t = float(g_h.vol_real.sum())
                prev_h_t = float(g_h.vol_humano.sum())
                
                wmape = (g_h.vol_humano - g_h.vol_real).abs().sum() / real_h_t * 100
                bias  = (prev_h_t - real_h_t) / real_h_t * 100
                
                ok = g_h[g_h.vol_real > 0].copy()
                ok["ape"] = (ok.vol_humano - ok.vol_real).abs() / ok.vol_real * 100
                mape = float(ok.ape.mean()) if len(ok) else None

                gm = g_h.groupby("mes").agg(r=("vol_real","sum"), p=("vol_humano","sum")).reset_index()
                gm = gm[gm.r > 0].copy()
                gm["er"] = (gm.p - gm.r) / gm.r
                persist = float((gm.er > 0.02).mean() if bias >= 0 else (gm.er < -0.02).mean()) if len(gm) else 0

                item.update({
                    "wmape_h": round(wmape, 2),
                    "mape_h":  round(mape, 2) if mape else None,
                    "bias_h":  round(bias, 2),
                    "persistencia": round(persist * 100, 1),
                    "vol_previsto": round(prev_h_t, 0),
                    "meses": int(len(gm)),
                })

                if wmape <= 20 and abs(bias) <= 10:
                    item["classe"] = "Sob controle"
                elif bias > 10 and persist >= 0.70:
                    aj = -round(bias / (100 + bias) * 100, 1)
                    item["classe"] = "Superestimando"
                    item["acao"] = f"Reduzir ~{abs(aj):.0f}%"
                elif bias < -10 and persist >= 0.70:
                    aj = round(-bias / (100 + bias) * 100, 1)
                    item["classe"] = "Subestimando"
                    item["acao"] = f"Aumentar ~{abs(aj):.0f}%"
                elif wmape > 30:
                    item["classe"] = "Errático"
                else:
                    item["classe"] = "Atenção"
            else:
                item.update({"wmape_h": None, "mape_h": None, "bias_h": None, "vol_previsto": 0, "meses": 0, "classe": "Sem Meta"})

            # Avaliação da IA
            g_ia = g[g.vol_ia.notna()]
            if not g_ia.empty and g_ia.vol_real.sum() > 0:
                ri = float(g_ia.vol_real.sum())
                item["wmape_ia"] = round((g_ia.vol_ia - g_ia.vol_real).abs().sum() / ri * 100, 2)
                item["bias_ia"]  = round((float(g_ia.vol_ia.sum()) - ri) / ri * 100, 2)
                if item.get("wmape_h") is not None:
                    item["fva"] = round(item["wmape_ia"] - item["wmape_h"], 2)

            itens.append(item)

        itens.sort(key=lambda x: x.get("wmape_h", 0) if x.get("wmape_h") is not None else -1, reverse=True)
        return {"itens": itens}
    except Exception as e:
        raise HTTPException(500, f"Erro no diagnóstico: {e}")


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
        criticos.sort(key=lambda x: abs(x.get("bias_h", 0)), reverse=True)
        return {"alertas": criticos[:limite]}
    except Exception as e:
        raise HTTPException(500, f"Erro nos alertas: {e}")