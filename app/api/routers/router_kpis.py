"""
router_kpis.py — Acurácia do S&OP, diagnóstico por SKU e agente de análise
==========================================================================

ARQUITETURA
    Uma consulta única traz sku × mês × (realizado, humano, ia, pmv) para todo
    o período. Todo o resto é pandas. A versão anterior fazia uma query por mês
    dentro de um laço Python — com 43 meses eram 86+ idas ao banco por
    carregamento de tela.

FONTES
    fato_previsao_humana   previsão humana histórica (jan/23 → mai/26)
    fato_ibp_granular      vol_final (plano humano) e vol_ia, ciclos Nexus
    fato_vendas            realizado (qt_pedido, vl_pedido)
    dim_produtos           portfólio ativo, BU, categoria

REGRA DE MÊS FECHADO
    Nada com mês >= DATE_TRUNC('month', CURRENT_DATE) entra em métrica. O mês
    corrente é parcial e contamina qualquer WMAPE.

O QUE A TELA RESPONDE
    WMAPE mede o tamanho do erro. Ele não diz o que fazer. Um SKU com 30% de
    erro e viés zero é volátil — a demanda é imprevisível e mexer no nível do
    plano não resolve. Um SKU com 20% de erro e +18% de viés em 10 dos últimos
    12 meses é sistematicamente superestimado, e isso se corrige amanhã.

    Por isso o diagnóstico combina três eixos:
      VIÉS          direção média do erro, ponderada por volume
      PERSISTÊNCIA  em quantos meses o erro foi na mesma direção do viés
      TENDÊNCIA     WMAPE da metade recente menos o da metade anterior

    Só viés alto COM persistência alta vira recomendação de ajuste de nível.
    Sem esse segundo filtro, um SKU que mudou de patamar no meio do período
    seria classificado como crônico e receberia a correção errada.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
import datetime
import json
import os
import pandas as pd
import numpy as np

from app.core.database import get_db, engine
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["KPIs e Acurácia do S&OP"])

# ─── limiares do diagnóstico ─────────────────────────────────────────────────
VIES_RELEVANTE  = 0.10   # 10% de viés já é material para o plano
PERSIST_CRONICA = 0.70   # mesma direção em 70% dos meses = padrão, não acaso
WMAPE_CONTROLE  = 0.20   # abaixo disso o SKU está sob controle
WMAPE_ERRATICO  = 0.30   # acima disso, com viés baixo, é volatilidade
MIN_MESES       = 4      # menos que isso não sustenta diagnóstico
TOLERANCIA_DIR  = 0.02   # erro abaixo de 2% conta como "no alvo", sem direção


# ═════════════════════════════════════════════════════════════════════════════
# NÚCLEO — uma consulta, todo o resto em pandas
# ═════════════════════════════════════════════════════════════════════════════
def _carregar_base(inicio: str, fim: str, bu=None, categoria=None,
                   sku=None, apenas_ativos=True) -> pd.DataFrame:
    """
    Devolve sku × mês com realizado, plano humano, plano IA e PMV.

    O plano humano tem duas origens e a coluna `origem_humano` registra qual
    foi usada em cada linha — sem isso não há como auditar um número na tela:
      NEXUS      vol_final do ciclo congelado M+2 (a partir de 04/2026)
      HISTORICO  fato_previsao_humana (jan/23 → mai/26)
    Quando as duas existem, NEXUS ganha: é o plano que de fato governou o mês.
    """
    filtros, params = [], {"ini": inicio, "fim": fim}
    if bu:
        filtros.append("p.bu = :bu");         params["bu"] = bu
    if categoria:
        filtros.append("p.categoria = :cat"); params["cat"] = categoria
    if sku:
        filtros.append("p.sku = :sku");       params["sku"] = sku
    if apenas_ativos:
        filtros.append("COALESCE(p.ativo, FALSE) = TRUE")
    w = ("AND " + " AND ".join(filtros)) if filtros else ""

    sql = text(f"""
        WITH grade AS (
            SELECT p.sku, p.descricao, p.bu, p.categoria, p.segmento,
                   g.mes::date AS mes
            FROM dim_produtos p
            CROSS JOIN generate_series(
                :ini::date, :fim::date, '1 month'::interval
            ) AS g(mes)
            WHERE 1=1 {w}
        ),
        realizado AS (
            SELECT TRIM(v.sku::text) AS sku,
                   DATE_TRUNC('month', v.data_pedido)::date AS mes,
                   SUM(v.qt_pedido) AS vol_real,
                   SUM(v.vl_pedido) AS val_real,
                   COALESCE(SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0), 0) AS pmv
            FROM fato_vendas v
            WHERE v.data_pedido >= :ini
              AND v.data_pedido < (:fim::date + INTERVAL '1 month')
            GROUP BY 1, 2
        ),
        nexus AS (
            -- M+2 congelado: o ciclo cujo mês de início é 2 meses antes do alvo
            SELECT TRIM(i.sku::text) AS sku,
                   i.mes_projetado AS mes,
                   SUM(i.vol_final) AS vol_humano,
                   SUM(i.vol_ia)    AS vol_ia
            FROM fato_ibp_granular i
            WHERE (
                EXTRACT(YEAR FROM i.mes_projetado) * 12
              + EXTRACT(MONTH FROM i.mes_projetado)
            ) - (
                SPLIT_PART(i.ciclo_sop, '/', 2)::int * 12
              + SPLIT_PART(i.ciclo_sop, '/', 1)::int
            ) = 2
            GROUP BY 1, 2
        ),
        historico AS (
            SELECT h.sku, h.mes_projetado AS mes, SUM(h.vol_humano) AS vol_humano
            FROM fato_previsao_humana h
            WHERE h.fonte = 'HISTORICO'
            GROUP BY 1, 2
        )
        SELECT g.sku, g.descricao, g.bu, g.categoria, g.segmento,
               TO_CHAR(g.mes, 'YYYY-MM')            AS mes,
               COALESCE(r.vol_real, 0)              AS vol_real,
               COALESCE(r.val_real, 0)              AS val_real,
               COALESCE(r.pmv, 0)                   AS pmv,
               COALESCE(n.vol_humano, h.vol_humano) AS vol_humano,
               n.vol_ia                             AS vol_ia,
               CASE WHEN n.vol_humano IS NOT NULL THEN 'NEXUS'
                    WHEN h.vol_humano IS NOT NULL THEN 'HISTORICO'
                    ELSE NULL END                   AS origem_humano
        FROM grade g
        LEFT JOIN realizado r ON r.sku = g.sku AND r.mes = g.mes
        LEFT JOIN nexus     n ON n.sku = g.sku AND n.mes = g.mes
        LEFT JOIN historico h ON h.sku = g.sku AND h.mes = g.mes
        WHERE COALESCE(r.vol_real, 0) > 0
           OR COALESCE(n.vol_humano, 0) > 0
           OR COALESCE(h.vol_humano, 0) > 0
        ORDER BY g.sku, g.mes
    """)
    df = pd.read_sql(sql, engine, params=params)
    if df.empty:
        return df

    # PMV de referência por SKU: preenche meses sem venda com a média do SKU,
    # senão a exposição em R$ de um mês sem venda sairia zerada.
    pmv_sku = df[df.pmv > 0].groupby("sku").pmv.mean().rename("pmv_ref")
    df = df.merge(pmv_sku, on="sku", how="left")
    df["pmv"] = np.where(df.pmv > 0, df.pmv, df.pmv_ref.fillna(0))
    df = df.drop(columns=["pmv_ref"])

    for c in ["vol_real", "val_real", "vol_humano", "vol_ia", "pmv"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["vol_humano"] = df["vol_humano"].fillna(0)
    return df


def _ultimo_mes_fechado() -> str:
    hoje = datetime.datetime.utcnow() - datetime.timedelta(hours=3)
    fechado = hoje.replace(day=1) - datetime.timedelta(days=1)
    return fechado.strftime("%Y-%m")


def _periodo(inicio: Optional[str], fim: Optional[str]) -> tuple:
    """Normaliza o período e trava o fim no último mês FECHADO."""
    ini = (inicio or "2023-01") + "-01"
    teto = _ultimo_mes_fechado()
    f = fim or teto
    if f > teto:
        f = teto
    return ini, f + "-01"


# ═════════════════════════════════════════════════════════════════════════════
# MÉTRICAS
# ═════════════════════════════════════════════════════════════════════════════
def _metricas(g: pd.DataFrame, col_prev: str = "vol_humano") -> dict:
    """WMAPE, viés, persistência, tendência e exposição em R$ de um recorte."""
    g = g[g.vol_real > 0]
    if g.empty:
        return {}

    real = float(g.vol_real.sum())
    prev = float(g[col_prev].sum())
    if real <= 0:
        return {}

    erro_abs = float((g[col_prev] - g.vol_real).abs().sum())
    wmape = erro_abs / real
    vies = (prev - real) / real

    # erro relativo mês a mês — base da persistência e da tendência
    m = (g.groupby("mes")
           .agg(real=("vol_real", "sum"), prev=(col_prev, "sum"))
           .reset_index())
    m = m[m.real > 0]
    if m.empty:
        return {}
    m["erro_rel"] = (m.prev - m.real) / m.real

    if vies >= 0:
        persist = float((m.erro_rel > TOLERANCIA_DIR).mean())
    else:
        persist = float((m.erro_rel < -TOLERANCIA_DIR).mean())

    metade = max(len(m) // 2, 1)
    ant, rec = m.head(metade), m.tail(metade)
    wmape_ant = float((ant.prev - ant.real).abs().sum() / max(ant.real.sum(), 1))
    wmape_rec = float((rec.prev - rec.real).abs().sum() / max(rec.real.sum(), 1))

    # exposição financeira, valorizada pelo PMV do próprio mês
    gap = g[col_prev] - g.vol_real
    excesso_rs = float((gap.clip(lower=0) * g.pmv).sum())
    falta_rs = float(((-gap).clip(lower=0) * g.pmv).sum())

    return {
        "wmape":         round(wmape, 4),
        "vies":          round(vies, 4),
        "persistencia":  round(persist, 4),
        "wmape_recente": round(wmape_rec, 4),
        "wmape_antigo":  round(wmape_ant, 4),
        "tendencia":     round(wmape_rec - wmape_ant, 4),
        "meses":         int(len(m)),
        "vol_real":      round(real, 0),
        "vol_previsto":  round(prev, 0),
        "erro_abs_cx":   round(erro_abs, 0),
        "erro_abs_rs":   round(excesso_rs + falta_rs, 2),
        "excesso_rs":    round(excesso_rs, 2),
        "falta_rs":      round(falta_rs, 2),
    }


def _classificar(m: dict) -> dict:
    """
    Traduz as métricas numa recomendação.

    A ordem importa: crônico é testado ANTES de errático, porque um SKU pode
    ter erro grande e viés persistente ao mesmo tempo — nesse caso a direção é
    a informação acionável, não a volatilidade.

    O ajuste sugerido é vies/(1+vies), não vies. Se o plano ficou 25% acima do
    vendido, reduzir 25% do plano corta demais: a correção correta é 20%
    (1 − 1/1,25). Reduzir pelo próprio viés levaria a subestimar no ciclo
    seguinte.
    """
    if not m or m.get("meses", 0) < MIN_MESES:
        return {"classe": "Sem base", "acao": "Histórico insuficiente",
                "severidade": 0, "direcao": "indefinido"}

    wmape, vies, persist = m["wmape"], m["vies"], m["persistencia"]

    if wmape <= WMAPE_CONTROLE and abs(vies) <= VIES_RELEVANTE:
        return {"classe": "Sob controle", "acao": "Manter",
                "severidade": 0, "direcao": "estavel"}

    if vies > VIES_RELEVANTE and persist >= PERSIST_CRONICA:
        ajuste = -round(vies / (1 + vies) * 100, 1)
        return {"classe": "Superestimando", "direcao": "excesso",
                "acao": f"Reduzir plano em ~{abs(ajuste):.0f}%",
                "ajuste_sugerido_pct": ajuste,
                "severidade": 3 if vies > 0.25 else 2}

    if vies < -VIES_RELEVANTE and persist >= PERSIST_CRONICA:
        ajuste = round(-vies / (1 + vies) * 100, 1)
        return {"classe": "Subestimando", "direcao": "falta",
                "acao": f"Aumentar plano em ~{abs(ajuste):.0f}%",
                "ajuste_sugerido_pct": ajuste,
                "severidade": 3 if vies < -0.25 else 2}

    if wmape > WMAPE_ERRATICO:
        return {"classe": "Errático", "direcao": "volatil",
                "acao": "Investigar causa — erro sem direção fixa",
                "severidade": 2}

    return {"classe": "Atenção", "direcao": "misto",
            "acao": "Monitorar — viés sem padrão consistente",
            "severidade": 1}


# ═════════════════════════════════════════════════════════════════════════════
# 1. FILTROS
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/filtros")
async def filtros(db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    df = pd.read_sql(text("""
        SELECT DISTINCT bu, categoria, sku, descricao
        FROM dim_produtos
        WHERE COALESCE(ativo, FALSE) = TRUE
        ORDER BY categoria, sku
    """), engine)
    return {
        "bus":        sorted(df.bu.dropna().unique().tolist()),
        "categorias": sorted(df.categoria.dropna().unique().tolist()),
        "skus":       df[["sku", "descricao", "categoria"]].to_dict("records"),
        "ultimo_mes_fechado": _ultimo_mes_fechado(),
        "primeiro_mes": "2023-01",
    }


# ═════════════════════════════════════════════════════════════════════════════
# 2. RESUMO GLOBAL
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/resumo")
async def resumo(
    inicio:    Optional[str] = Query(None, description="YYYY-MM"),
    fim:       Optional[str] = Query(None, description="YYYY-MM"),
    bu:        Optional[str] = None,
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    _: dict = Depends(get_current_user),
):
    ini, f = _periodo(inicio, fim)
    df = _carregar_base(ini, f, bu, categoria, sku)
    if df.empty:
        return {"totais": {}, "periodo": {"inicio": ini[:7], "fim": f[:7]}}

    hum = _metricas(df, "vol_humano")
    df_ia = df[df.vol_ia.notna()]
    ia = _metricas(df_ia, "vol_ia") if not df_ia.empty else {}
    fva = round(ia["wmape"] - hum["wmape"], 4) if (ia and hum) else None

    return {
        "totais": {
            **hum,
            "acuracia":       round(max(0, 1 - hum.get("wmape", 1)), 4),
            "wmape_ia":       ia.get("wmape"),
            "vies_ia":        ia.get("vies"),
            "acuracia_ia":    round(max(0, 1 - ia["wmape"]), 4) if ia else None,
            "meses_com_ia":   ia.get("meses", 0),
            "fva":            fva,
            "skus_avaliados": int(df[df.vol_real > 0].sku.nunique()),
        },
        "periodo": {"inicio": ini[:7], "fim": f[:7],
                    "ultimo_fechado": _ultimo_mes_fechado()},
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. EVOLUÇÃO MENSAL
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/evolucao")
async def evolucao(
    inicio:    Optional[str] = Query(None),
    fim:       Optional[str] = Query(None),
    bu:        Optional[str] = None,
    categoria: Optional[str] = None,
    sku:       Optional[str] = None,
    _: dict = Depends(get_current_user),
):
    ini, f = _periodo(inicio, fim)
    df = _carregar_base(ini, f, bu, categoria, sku)
    if df.empty:
        return {"serie": []}

    g = (df.groupby("mes")
           .agg(vol_real=("vol_real", "sum"),
                vol_humano=("vol_humano", "sum"),
                val_real=("val_real", "sum"))
           .reset_index())
    g["vol_ia"] = g.mes.map(df[df.vol_ia.notna()].groupby("mes").vol_ia.sum())
    g["origem"] = g.mes.map(
        df.dropna(subset=["origem_humano"]).groupby("mes").origem_humano
          .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else None))

    g["wmape_humano"] = np.where(g.vol_real > 0,
        (g.vol_humano - g.vol_real).abs() / g.vol_real, np.nan)
    g["bias_humano"] = np.where(g.vol_real > 0,
        (g.vol_humano - g.vol_real) / g.vol_real, np.nan)
    g["wmape_ia"] = np.where((g.vol_real > 0) & g.vol_ia.notna(),
        (g.vol_ia - g.vol_real).abs() / g.vol_real, np.nan)
    g["bias_ia"] = np.where((g.vol_real > 0) & g.vol_ia.notna(),
        (g.vol_ia - g.vol_real) / g.vol_real, np.nan)

    return {"serie": g.replace({np.nan: None}).round(4).to_dict("records")}


# ═════════════════════════════════════════════════════════════════════════════
# 4. DIAGNÓSTICO  ← o coração da tela
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/diagnostico")
async def diagnostico(
    inicio:    Optional[str] = Query(None),
    fim:       Optional[str] = Query(None),
    bu:        Optional[str] = None,
    categoria: Optional[str] = None,
    nivel:     str = Query("sku", description="sku | categoria"),
    _: dict = Depends(get_current_user),
):
    """
    Por SKU ou categoria: viés, persistência, tendência, exposição em R$ e a
    ação recomendada.

    Ordenado por exposição financeira, não por percentual: 40% de erro num SKU
    de 500 caixas importa menos que 15% num de 20 mil.
    """
    ini, f = _periodo(inicio, fim)
    df = _carregar_base(ini, f, bu, categoria, None)
    if df.empty:
        return {"itens": [], "agregados": {}, "periodo": {"inicio": ini[:7], "fim": f[:7]}}

    chave = ["sku", "descricao", "categoria", "bu"] if nivel == "sku" else ["categoria"]
    itens = []
    for valores, g in df.groupby(chave):
        vals = valores if isinstance(valores, tuple) else (valores,)
        m = _metricas(g, "vol_humano")
        if not m:
            continue
        g_ia = g[g.vol_ia.notna()]
        m_ia = _metricas(g_ia, "vol_ia") if not g_ia.empty else {}

        item = dict(zip(chave, vals))
        item.update(m)
        item.update(_classificar(m))
        item["wmape_ia"] = m_ia.get("wmape")
        item["vies_ia"] = m_ia.get("vies")
        item["fva"] = round(m_ia["wmape"] - m["wmape"], 4) if m_ia else None
        itens.append(item)

    itens.sort(key=lambda x: x.get("erro_abs_rs", 0), reverse=True)

    def _soma(classe):
        sel = [i for i in itens if i["classe"] == classe]
        return {
            "qtd": len(sel),
            "erro_rs": round(sum(i.get("erro_abs_rs", 0) for i in sel), 2),
            "top": [i.get("sku") or i.get("categoria") for i in sel[:5]],
        }

    return {
        "itens": itens,
        "agregados": {
            "superestimando": _soma("Superestimando"),
            "subestimando":   _soma("Subestimando"),
            "erratico":       _soma("Errático"),
            "atencao":        _soma("Atenção"),
            "sob_controle":   _soma("Sob controle"),
            "exposicao_total_rs": round(sum(i.get("erro_abs_rs", 0) for i in itens), 2),
            "excesso_total_rs":   round(sum(i.get("excesso_rs", 0) for i in itens), 2),
            "falta_total_rs":     round(sum(i.get("falta_rs", 0) for i in itens), 2),
        },
        "periodo": {"inicio": ini[:7], "fim": f[:7]},
    }


# ═════════════════════════════════════════════════════════════════════════════
# 5. HISTÓRICO DE UM SKU (alimenta o dossiê)
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/sku/{sku}/historico")
async def sku_historico(
    sku: str,
    inicio: Optional[str] = Query(None),
    fim:    Optional[str] = Query(None),
    _: dict = Depends(get_current_user),
):
    ini, f = _periodo(inicio, fim)
    df = _carregar_base(ini, f, None, None, sku, apenas_ativos=False)
    if df.empty:
        raise HTTPException(404, f"Sem dados para o SKU {sku} no período.")

    df["gap_cx"] = df.vol_humano - df.vol_real
    df["gap_rs"] = df.gap_cx * df.pmv
    df["erro_rel"] = np.where(df.vol_real > 0, df.gap_cx / df.vol_real, np.nan)

    m = _metricas(df, "vol_humano")
    cab = df.iloc[0]
    return {
        "sku": sku,
        "descricao": cab.descricao,
        "categoria": cab.categoria,
        "bu": cab.bu,
        "metricas": {**m, **_classificar(m)},
        "serie": df[["mes", "vol_real", "vol_humano", "vol_ia", "pmv",
                     "gap_cx", "gap_rs", "erro_rel", "origem_humano"]]
                 .replace({np.nan: None}).round(4).to_dict("records"),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 6. ALERTAS (consumo pela Visão Geral)
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/alertas")
async def alertas(
    inicio: Optional[str] = Query(None),
    fim:    Optional[str] = Query(None),
    nivel:  str = Query("sku", description="sku | categoria"),
    limite: int = Query(20),
    _: dict = Depends(get_current_user),
):
    """
    Formato enxuto para a Visão Geral pintar badges em categoria e SKU.
    Só o que exige ação, ordenado por severidade e depois por R$.
    """
    d = await diagnostico(inicio=inicio, fim=fim, nivel=nivel, _=_)
    itens = [i for i in d["itens"] if i.get("severidade", 0) >= 2]
    itens.sort(key=lambda x: (-x["severidade"], -x.get("erro_abs_rs", 0)))

    saida = []
    for i in itens[:limite]:
        rs = f"{i['erro_abs_rs']:,.0f}".replace(",", ".")
        saida.append({
            "chave": i.get("sku") or i.get("categoria"),
            "descricao": i.get("descricao") or i.get("categoria"),
            "categoria": i.get("categoria"),
            "nivel": "critico" if i["severidade"] >= 3 else "atencao",
            "direcao": i["direcao"],
            "classe": i["classe"],
            "acao": i["acao"],
            "vies_pct": round(i["vies"] * 100, 1),
            "persistencia_pct": round(i["persistencia"] * 100),
            "erro_rs": i["erro_abs_rs"],
            "mensagem": (f"{i['classe']} em {abs(i['vies'] * 100):.0f}% "
                         f"({i['persistencia'] * 100:.0f}% dos {i['meses']} meses) "
                         f"— exposição de R$ {rs}"),
        })
    return {"alertas": saida, "periodo": d["periodo"]}


# ═════════════════════════════════════════════════════════════════════════════
# 7. AGENTE DE ANÁLISE
# ═════════════════════════════════════════════════════════════════════════════
class PerguntaAgente(BaseModel):
    pergunta: str
    inicio:    Optional[str] = None
    fim:       Optional[str] = None
    bu:        Optional[str] = None
    categoria: Optional[str] = None


@router.post("/agente")
async def agente(payload: PerguntaAgente, _: dict = Depends(get_current_user)):
    """
    Responde perguntas de negócio sobre os indicadores.

    DESENHO DELIBERADO: todo número entregue ao modelo já vem calculado pelos
    endpoints determinísticos acima. O modelo escreve a leitura, não faz a
    conta. Num painel que decide plano de produção, número alucinado é pior que
    resposta nenhuma — e LLM erra aritmética em série longa.
    """
    chave = os.getenv("ANTHROPIC_API_KEY")
    if not chave:
        raise HTTPException(503,
            "ANTHROPIC_API_KEY não configurada no container. Adicione a "
            "variável em docker-compose.yml (environment) e reinicie o backend.")

    diag = await diagnostico(inicio=payload.inicio, fim=payload.fim, bu=payload.bu,
                             categoria=payload.categoria, nivel="sku", _=_)
    res = await resumo(inicio=payload.inicio, fim=payload.fim, bu=payload.bu,
                       categoria=payload.categoria, _=_)
    evo = await evolucao(inicio=payload.inicio, fim=payload.fim, bu=payload.bu,
                         categoria=payload.categoria, _=_)

    campos = ["sku", "descricao", "categoria", "wmape", "vies", "persistencia",
              "tendencia", "meses", "vol_real", "erro_abs_rs", "excesso_rs",
              "falta_rs", "classe", "acao", "ajuste_sugerido_pct",
              "wmape_ia", "fva"]
    contexto = {
        "periodo": diag["periodo"],
        "resumo_global": res["totais"],
        "agregados": diag["agregados"],
        "serie_mensal": evo["serie"][-24:],
        "top_25_por_exposicao": [{k: i.get(k) for k in campos}
                                 for i in diag["itens"][:25]],
    }

    sistema = (
        "Você é analista de S&OP da Linea Alimentos, respondendo dentro do "
        "painel Nexus sobre acurácia de previsão de demanda.\n\n"
        "REGRAS INEGOCIÁVEIS\n"
        "1. Use SOMENTE os números do JSON fornecido. Nunca calcule, estime ou "
        "invente valor ausente. Se a resposta exigir um número que não está "
        "lá, diga qual filtro o usuário precisa aplicar para obtê-lo.\n"
        "2. Volume em caixas (cx), dinheiro em R$. Nunca use 'faturamento': os "
        "termos corretos são 'valor pedido' e 'volume pedido'.\n"
        "3. Ao recomendar ação, cite o SKU pelo código e descrição.\n\n"
        "COMO INTERPRETAR OS CAMPOS\n"
        "- wmape: tamanho do erro (0,25 = 25%). Mede precisão, não direção.\n"
        "- vies: direção. Positivo = plano acima do vendido (superestimou). "
        "Negativo = plano abaixo do vendido (subestimou).\n"
        "- persistencia: fração dos meses em que o erro foi na mesma direção "
        "do viés. Acima de 0,70 é padrão sistemático, não acaso — só aí faz "
        "sentido recomendar ajuste de nível. Viés alto com persistência baixa "
        "significa que o SKU mudou de comportamento no meio do período.\n"
        "- tendencia: wmape recente menos wmape antigo. Negativo = melhorando.\n"
        "- excesso_rs: valor planejado além do vendido (capital parado em "
        "estoque). falta_rs: valor vendido além do planejado (risco de ruptura "
        "e venda perdida). Não chame nenhum dos dois de 'prejuízo' — são "
        "exposições, não perdas realizadas.\n"
        "- ajuste_sugerido_pct: correção de nível já calculada. Use esse "
        "número, não recalcule.\n"
        "- fva: wmape da IA menos wmape do humano. Negativo = a IA estava mais "
        "precisa que o ajuste humano naquele recorte.\n\n"
        "ESTILO: direto e curto. Comece pela resposta, sem preâmbulo. Priorize "
        "por R$ de exposição, não por percentual — 40% de erro num SKU pequeno "
        "importa menos que 15% num grande."
    )

    import httpx
    try:
        async with httpx.AsyncClient(timeout=90) as cli:
            r = await cli.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": chave,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={
                    "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                    "max_tokens": 1500,
                    "system": sistema,
                    "messages": [{"role": "user", "content":
                        f"Dados do painel:\n```json\n"
                        f"{json.dumps(contexto, ensure_ascii=False, default=str)}\n```\n\n"
                        f"Pergunta: {payload.pergunta}"}],
                },
            )
        if r.status_code != 200:
            raise HTTPException(502, f"API Anthropic devolveu {r.status_code}: {r.text[:300]}")
        texto = "".join(b.get("text", "") for b in r.json().get("content", [])
                        if b.get("type") == "text")
        return {"resposta": texto,
                "contexto": {"skus_analisados": len(diag["itens"]),
                             "periodo": diag["periodo"]}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Falha ao consultar o agente: {e}")


@router.get("/agente/sugestoes")
async def agente_sugestoes(_: dict = Depends(get_current_user)):
    return {"sugestoes": [
        "Em quais SKUs estamos prevendo demais e quanto isso representa em R$?",
        "Onde estamos subestimando a demanda de forma consistente?",
        "A acurácia melhorou ou piorou na segunda metade do período?",
        "Quais categorias mais contribuem para o erro total?",
        "Em quais SKUs a IA foi mais precisa que o ajuste humano?",
        "Quais SKUs têm erro grande mas sem direção definida?",
    ]}