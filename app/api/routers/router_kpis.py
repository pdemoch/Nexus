"""
=====================================================================
 ROUTER KPIS — MOTOR DE DESVIOS (ACURACIA DO S&OP)  ·  "Desvios MT"
=====================================================================
Tela unica de acuracia gamificada. Compara o que o S&OP CONGELOU contra o
REALIZADO de fato, medindo WMAPE, MAPE, BIAS, FVA e Acuracia por multiplas
dimensoes, e monta um placar competitivo (ranking) com o Nexus Bot (a IA)
como competidor nomeado.

REGRA TEMPORAL (dinamica, nunca hardcoded):
  Realizado do mes N  vs  congelado do ciclo N-2.
  Ex.: vendas 07/2026 (fato_vendas) vs ciclo 05/2026 (fato_ibp_granular).
  Ao virar 08/2026, o mes entra sozinho (ciclo 06 existe + vendas de ago).

FONTES:
  Previsto  -> fato_ibp_granular, ciclo N-2: vol_final (humano) e vol_ia (IA).
  Realizado -> fato_vendas do mes N: qt_pedido (cx) e vl_pedido (R$).
  Pessoas   -> dim_clientes (cadastro ATUAL nos dois lados p/ consistencia):
               supervisor_nome = coordenador; vendedor_nome = vendedor.
  Cliente   -> agrupado por razaosocial.  Inativos excluidos (filtro canonico).

O ranking das PESSOAS e sobre o vol_final (o que elas controlam).
O vol_ia vira o Nexus Bot (benchmark do FVA).
Prefixo: /api/v1/kpis
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
import datetime
from dateutil.relativedelta import relativedelta
from typing import List

from app.core.database import get_db

router = APIRouter(prefix="/api/v1/kpis", tags=["Desvios e Acuracia do S&OP"])


# =====================================================================
# DIMENSOES (dinamicas, sem hardcode de valores)
# =====================================================================
DIMENSOES = {
    "sku": "SKU", "categoria": "Categoria", "segmento": "Segmento",
    "cliente": "Cliente", "regional": "Regional",
    "vendedor": "Vendedor", "coordenador": "Coordenador",
}
COL_MAP = {
    "sku": "sku", "categoria": "categoria", "segmento": "segmento",
    "cliente": "razaosocial", "regional": "regional",
    "vendedor": "vendedor_nome", "coordenador": "supervisor_nome",
}
# Pesos da nota do jogo (suaves no lancamento: bias doi pouco).
PESO_BIAS = 0.15
PESO_FVA = 0.20


def _ciclo_para_mes(mes_ano: str) -> str:
    """'MM/YYYY' -> ciclo N-2 'MM/YYYY' (deriva da data, nunca constante)."""
    mes, ano = mes_ano.split("/")
    dt = datetime.date(int(ano), int(mes), 1)
    return (dt - relativedelta(months=2)).strftime("%m/%Y")


def _mes_date_sql(mes_ano: str) -> str:
    mes, ano = mes_ano.split("/")
    return f"{ano}-{mes.zfill(2)}-01"


def listar_meses_auditaveis(db: Session) -> List[str]:
    """Meses com realizado (fato_vendas) E ciclo N-2 congelado disponivel."""
    meses_venda = db.execute(text("""
        SELECT DISTINCT TO_CHAR(data_pedido, 'MM/YYYY') AS mes
        FROM fato_vendas WHERE qt_pedido > 0
    """)).fetchall()
    set_venda = {r.mes for r in meses_venda}
    ciclos = db.execute(text("SELECT DISTINCT ciclo_sop FROM fato_ibp_granular")).fetchall()
    set_ciclos = {r.ciclo_sop for r in ciclos}
    auditaveis = [m for m in set_venda if _ciclo_para_mes(m) in set_ciclos]
    auditaveis.sort(key=lambda m: (m.split("/")[1], m.split("/")[0]))
    return auditaveis


def _montar_base(db: Session, meses: List[str], visao: str) -> pd.DataFrame:
    """Base micro (cgc x sku x mes): previsto + realizado + dimensoes atuais."""
    frames = []
    for mes in meses:
        ciclo = _ciclo_para_mes(mes)
        mes_date = _mes_date_sql(mes)

        prev = db.execute(text("""
            SELECT f.cgc, f.sku,
                COALESCE(p.categoria,'SEM CATEGORIA') AS categoria,
                COALESCE(p.segmento,'SEM SEGMENTO')   AS segmento,
                COALESCE(c.razaosocial,'SEM CLIENTE') AS razaosocial,
                COALESCE(c.regional,'N/A')            AS regional,
                COALESCE(c.vendedor_nome,'SEM VENDEDOR')       AS vendedor_nome,
                COALESCE(c.supervisor_nome,'SEM COORDENADOR')  AS supervisor_nome,
                SUM(f.vol_final) AS prev_final_cx, SUM(f.vol_ia) AS prev_ia_cx,
                SUM(f.vol_final * f.pmv_aplicado) AS prev_final_rs,
                SUM(f.vol_ia * f.pmv_aplicado)    AS prev_ia_rs
            FROM fato_ibp_granular f
            LEFT JOIN dim_clientes c ON f.cgc = c.cgc
            LEFT JOIN dim_produtos p ON f.sku = p.sku
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = :mes_date
              AND UPPER(TRIM(COALESCE(c.bloqueado,'ATIVO'))) != 'INATIVO'
            GROUP BY f.cgc, f.sku, p.categoria, p.segmento, c.razaosocial,
                     c.regional, c.vendedor_nome, c.supervisor_nome
        """), {"ciclo": ciclo, "mes_date": mes_date}).fetchall()

        real = db.execute(text("""
            SELECT cgc, sku, SUM(qt_pedido) AS real_cx, SUM(vl_pedido) AS real_rs
            FROM fato_vendas
            WHERE TO_CHAR(data_pedido,'MM/YYYY') = :mes AND qt_pedido > 0
            GROUP BY cgc, sku
        """), {"mes": mes}).fetchall()

        df_prev = pd.DataFrame([dict(r._mapping) for r in prev])
        df_real = pd.DataFrame([dict(r._mapping) for r in real])
        if df_prev.empty and df_real.empty:
            continue
        if df_prev.empty:
            df_prev = pd.DataFrame(columns=["cgc","sku","categoria","segmento","razaosocial",
                "regional","vendedor_nome","supervisor_nome","prev_final_cx","prev_ia_cx","prev_final_rs","prev_ia_rs"])
        if df_real.empty:
            df_real = pd.DataFrame(columns=["cgc","sku","real_cx","real_rs"])

        df = pd.merge(df_prev, df_real, on=["cgc","sku"], how="outer")
        df["mes_ano"] = mes

        # Linhas so-realizado (vendeu sem prever): busca dimensoes no cadastro.
        faltantes = df["razaosocial"].isna()
        if faltantes.any():
            for _, k in df.loc[faltantes, ["cgc","sku"]].drop_duplicates().iterrows():
                info = db.execute(text("""
                    SELECT COALESCE(p.categoria,'SEM CATEGORIA') AS categoria,
                           COALESCE(p.segmento,'SEM SEGMENTO') AS segmento,
                           COALESCE(c.razaosocial,'SEM CLIENTE') AS razaosocial,
                           COALESCE(c.regional,'N/A') AS regional,
                           COALESCE(c.vendedor_nome,'SEM VENDEDOR') AS vendedor_nome,
                           COALESCE(c.supervisor_nome,'SEM COORDENADOR') AS supervisor_nome
                    FROM (SELECT :cgc AS cgc, :sku AS sku) x
                    LEFT JOIN dim_clientes c ON x.cgc = c.cgc
                    LEFT JOIN dim_produtos p ON x.sku = p.sku
                """), {"cgc": k["cgc"], "sku": k["sku"]}).fetchone()
                if info:
                    m = (df["cgc"]==k["cgc"]) & (df["sku"]==k["sku"]) & faltantes
                    for campo in ["categoria","segmento","razaosocial","regional","vendedor_nome","supervisor_nome"]:
                        df.loc[m, campo] = getattr(info, campo)
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
    return base


def _metricas(grupo: pd.DataFrame) -> dict:
    """
    WMAPE   = Σ|real-prev| / Σreal          (erro ponderado por volume)
    MAPE    = media |real-prev|/real (real>0) (informativo)
    BIAS    = (Σprev - Σreal) / Σreal        (>0 preve demais; <0 de menos)
    Acuracia= max(0, 1-WMAPE)
    FVA     = WMAPE_ia - WMAPE_final         (>0 humano agregou valor)
    Nota    = Acuracia - 0.15*|BIAS| + 0.20*FVA  (0-100)
    """
    soma_real = grupo["realizado"].sum()
    soma_final = grupo["previsto_final"].sum()
    soma_ia = grupo["previsto_ia"].sum()
    erro_final = np.abs(grupo["realizado"] - grupo["previsto_final"]).sum()
    erro_ia = np.abs(grupo["realizado"] - grupo["previsto_ia"]).sum()

    if soma_real > 0:
        wmape_final = erro_final / soma_real
        wmape_ia = erro_ia / soma_real
        bias_final = (soma_final - soma_real) / soma_real
        bias_ia = (soma_ia - soma_real) / soma_real
        com_real = grupo[grupo["realizado"] > 0]
        mape_final = (np.abs(com_real["realizado"]-com_real["previsto_final"])/com_real["realizado"]).mean() if len(com_real)>0 else 0.0
    else:
        wmape_final = 1.0 if soma_final > 0 else 0.0
        wmape_ia = 1.0 if soma_ia > 0 else 0.0
        bias_final = 1.0 if soma_final > 0 else 0.0
        bias_ia = 1.0 if soma_ia > 0 else 0.0
        mape_final = 1.0 if soma_final > 0 else 0.0

    acuracia = max(0.0, 1.0 - wmape_final)
    fva = wmape_ia - wmape_final
    nota = max(0.0, min(1.0, acuracia - abs(bias_final)*PESO_BIAS + fva*PESO_FVA)) * 100.0
    if abs(bias_final) < 0.05: vies = "equilibrado"
    elif bias_final > 0: vies = "prevê demais"
    else: vies = "prevê de menos"

    return {
        "previsto_final": round(float(soma_final),2), "previsto_ia": round(float(soma_ia),2),
        "realizado": round(float(soma_real),2), "wmape": round(float(wmape_final),4),
        "wmape_ia": round(float(wmape_ia),4), "mape": round(float(mape_final),4),
        "bias": round(float(bias_final),4), "bias_ia": round(float(bias_ia),4),
        "vies_label": vies, "fva": round(float(fva),4), "acuracia": round(float(acuracia),4),
        "nota": round(float(nota),1), "linhas": int(len(grupo)),
    }


def _metricas_bot(base: pd.DataFrame) -> dict:
    """Nexus Bot: usa vol_ia como 'plano' do competidor. E a regua do FVA."""
    espelho = base.copy()
    espelho["previsto_final"] = espelho["previsto_ia"]
    m = _metricas(espelho)
    m["fva"] = 0.0
    m["nota"] = round(max(0.0, min(1.0, m["acuracia"] - abs(m["bias"])*PESO_BIAS)) * 100.0, 1)
    return m


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


@router.get("/ranking")
async def ranking(dimensao: str = Query("coordenador"), meses: List[str] = Query(None),
                  visao: str = Query("caixas"), db: Session = Depends(get_db)):
    try:
        if dimensao not in DIMENSOES:
            raise HTTPException(status_code=400, detail=f"Dimensao invalida: {list(DIMENSOES.keys())}")
        if not meses:
            meses = listar_meses_auditaveis(db)
        if not meses:
            return {"dimensao": dimensao, "visao": visao, "meses": [], "itens": [], "totais": {}}
        base = _montar_base(db, meses, visao)
        if base.empty:
            return {"dimensao": dimensao, "visao": visao, "meses": meses, "itens": [], "totais": {}}

        gcol = COL_MAP[dimensao]
        itens = []
        for chave, grupo in base.groupby(gcol):
            m = _metricas(grupo); m["nome"] = str(chave); m["eh_bot"] = False
            if dimensao == "sku":
                m["descricao"] = str(grupo["categoria"].iloc[0]) if "categoria" in grupo else ""
            itens.append(m)

        # Nexus Bot: so nas dimensoes de PESSOAS.
        if dimensao in ("vendedor", "coordenador"):
            bot = _metricas_bot(base); bot["nome"] = "Nexus Bot"; bot["eh_bot"] = True
            itens.append(bot)

        itens.sort(key=lambda x: (-x["nota"], -x["realizado"]))
        pos_bot = next((i for i, it in enumerate(itens) if it.get("eh_bot")), None)
        for i, it in enumerate(itens):
            it["posicao"] = i + 1
            if pos_bot is not None and not it.get("eh_bot"):
                it["acima_do_bot"] = i < pos_bot

        return {"dimensao": dimensao, "visao": visao, "meses": meses,
                "itens": itens, "totais": _metricas(base)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/drilldown")
async def drilldown(dimensao: str = Query(...), valor: str = Query(...),
                    meses: List[str] = Query(None), visao: str = Query("caixas"),
                    sub_dimensao: str = Query("sku"), db: Session = Depends(get_db)):
    try:
        if dimensao not in DIMENSOES or sub_dimensao not in DIMENSOES:
            raise HTTPException(status_code=400, detail="Dimensao invalida.")
        if not meses:
            meses = listar_meses_auditaveis(db)
        if not meses:
            return {"itens": []}
        base = _montar_base(db, meses, visao)
        if base.empty:
            return {"itens": []}
        recorte = base[base[COL_MAP[dimensao]].astype(str) == valor]
        if recorte.empty:
            return {"itens": []}
        sub_col = COL_MAP[sub_dimensao]
        itens = []
        for chave, grupo in recorte.groupby(sub_col):
            m = _metricas(grupo); m["nome"] = str(chave); itens.append(m)
        itens.sort(key=lambda x: (x["acuracia"], -x["realizado"]))
        return {"dimensao": dimensao, "valor": valor, "sub_dimensao": sub_dimensao,
                "visao": visao, "meses": meses, "itens": itens}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/evolucao")
async def evolucao(visao: str = Query("caixas"), db: Session = Depends(get_db)):
    try:
        meses = listar_meses_auditaveis(db)
        serie = []
        for mes in meses:
            base = _montar_base(db, [mes], visao)
            if base.empty:
                continue
            m = _metricas(base); bot = _metricas_bot(base)
            serie.append({"mes": mes, "acuracia": round(m["acuracia"]*100,1),
                "acuracia_bot": round(bot["acuracia"]*100,1), "bias": round(m["bias"]*100,1),
                "fva": round(m["fva"]*100,1), "vies_label": m["vies_label"], "realizado": m["realizado"]})
        return {"visao": visao, "serie": serie}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/diagnostico")
async def diagnostico(visao: str = Query("caixas"), meses: List[str] = Query(None),
                      db: Session = Depends(get_db)):
    try:
        if not meses:
            meses = listar_meses_auditaveis(db)
        base = _montar_base(db, meses, visao)
        if base.empty:
            return {"por_categoria": [], "por_regional": [], "dispersao_sku": []}

        def _agrupar(col):
            out = []
            for chave, grupo in base.groupby(col):
                m = _metricas(grupo)
                out.append({"nome": str(chave), "acuracia": round(m["acuracia"]*100,1),
                    "wmape": round(m["wmape"]*100,1), "bias": round(m["bias"]*100,1), "realizado": m["realizado"]})
            out.sort(key=lambda x: x["acuracia"])
            return out

        dispersao = []
        for sku, grupo in base.groupby("sku"):
            m = _metricas(grupo)
            if m["realizado"] <= 0:
                continue
            dispersao.append({"sku": str(sku), "categoria": str(grupo["categoria"].iloc[0]),
                "volume": m["realizado"], "erro_pct": round(m["wmape"]*100,1), "acuracia": round(m["acuracia"]*100,1)})
        dispersao.sort(key=lambda x: -x["volume"])

        return {"visao": visao, "meses": meses,
                "por_categoria": _agrupar("categoria"), "por_regional": _agrupar("regional"),
                "dispersao_sku": dispersao}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))