"""
=====================================================================
AGENTE DE KPIs S&OP — RELATORIO MENSAL DO CICLO
=====================================================================
Destino: app/api/routers/agente_kpis.py

O QUE MUDOU NESTA REESCRITA
---------------------------------------------------------------------
1. SEM FILTROS. A janela e derivada do ciclo ativo no painel admin, nao
   de toggles ou seletores de periodo. As assinaturas publicas ainda
   aceitam `meses`, `base` e `unidade` para nao quebrar o frontend, mas
   os valores sao IGNORADOS. Um ciclo, um relatorio.

2. LE DOS MARTS. mart_acuracia_sku_mes e mart_vendas_mes ja resolvem no
   grao (sku, mes) a uniao previsao_humana + ibp_granular (regra M-2), o
   PMV em cascata e os tres estados de venda. Antes era uma CTE de ~200
   linhas montada ao vivo em toda requisicao — era isso que deixava o
   chat travado em "consultando dados".

3. HAIKU. Relatorio e prosa estruturada sobre JSON ja preparado; nao
   exige o modelo mais caro.

4. DIMENSAO FINANCEIRA COM PROVA. Todo valor em R$ vem acompanhado da
   conta que o gerou.

DEFINICOES TRAVADAS (decisoes de negocio, nao de implementacao)
---------------------------------------------------------------------
WMAPE  = SUM|vol_final - qt_pedido| / SUM qt_pedido     SO EM CAIXAS
BIAS   = (SUM vol_final - SUM qt_pedido) / SUM qt_pedido SO EM CAIXAS
FVA    = WMAPE_humano - WMAPE_ia, na MESMA populacao     SO EM CAIXAS
FILL   = SUM qt_entregue / SUM qt_pedido                 SO EM CAIXAS

Acuracia mede contra DEMANDA (qt_pedido), nunca contra embarque. Medir
contra o entregue cria demanda censurada: se o plano subestima, a
producao subestima e a entrega subestima junto — o erro se apaga e
quanto pior o suprimento, melhor a acuracia aparente.

Valor monetario NAO entra em WMAPE, BIAS nem FVA. Fica na secao de
impacto financeiro, que monetiza os gaps de volume.

FONTE DO R$
---------------------------------------------------------------------
vl_pedido, vl_entregue e vl_corte vem do ERP — valor real, com nota.
O PMV so entra onde NAO existe valor no ERP, porque o volume e
hipotetico e nunca virou nota fiscal:
    capital imobilizado = max(plano - pedido, 0) x PMV
    venda nao prevista  = max(pedido - plano, 0) x PMV

JANELA (ciclo ativo 08/2026)
---------------------------------------------------------------------
YoY      Jan-Jul/2026 vs Jan-Jul/2025 vs Jan-Jul/2024 vs Jan-Jul/2023
Detalhe  Fev a Jul/2026 (6 meses fechados), mes a mes

O YoY compara o MESMO recorte de meses em cada ano: comparar 7 meses
contra 12 daria diferenca de calendario, nao de desempenho.

RENDERIZACAO
---------------------------------------------------------------------
O frontend (AuditoriaArena) renderiza texto puro linha a linha: linhas
numeradas ou em MAIUSCULAS com menos de 70 caracteres viram titulo; o
resto vira paragrafo justificado. Tabelas markdown NAO renderizam — os
pipes sairiam embaralhados no meio do texto.

Por isso o prompt exige prova de calculo em PROSA com os numeros
explicitos ("14.886 cx x R$ 18,40 = R$ 273.902") em vez de tabela.
Quando o renderizador for ajustado, basta trocar a instrucao do prompt.
"""

import os
import re
import json
import hashlib
import logging
import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

MESES_DETALHE = 6      # meses fechados detalhados mes a mes
ANOS_YOY      = 4      # ano corrente + 3 anteriores
TOP_N         = 12     # itens nos rankings


# =====================================================================
# DDL DO CACHE
# =====================================================================
DDL_CACHE = """
CREATE TABLE IF NOT EXISTS agente_relatorio_cache (
    id          SERIAL PRIMARY KEY,
    ciclo_sop   VARCHAR(10) NOT NULL,
    chave       VARCHAR(64) NOT NULL,
    meses       TEXT,
    base        VARCHAR(20),
    unidade     VARCHAR(10),
    relatorio   TEXT        NOT NULL,
    dataset     TEXT,
    gerado_por  VARCHAR(120),
    gerado_em   TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT uix_agente_rel UNIQUE (ciclo_sop, chave)
);

CREATE TABLE IF NOT EXISTS agente_chat_cache (
    id          SERIAL PRIMARY KEY,
    ciclo_sop   VARCHAR(10) NOT NULL,
    chave       VARCHAR(64) NOT NULL,
    pergunta    TEXT        NOT NULL,
    resposta    TEXT        NOT NULL,
    gerado_em   TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT uix_agente_chat UNIQUE (ciclo_sop, chave)
);
"""

# Chave fixa: um relatorio por ciclo. O relatorio nao depende mais de
# recorte, entao nao ha o que hashear.
CHAVE_CICLO = "ciclo"


# =====================================================================
# HELPERS
# =====================================================================
def _sdiv(num, den, mult=1.0):
    """Divisao segura: None se denominador <= 0 ou NaN."""
    try:
        d = float(den)
        if d <= 0 or not (d == d):
            return None
        return float(num) / d * float(mult)
    except (ZeroDivisionError, TypeError, ValueError):
        return None


def _corrigir_nome_empresa(texto: str) -> str:
    """
    Salvaguarda deterministica pos-geracao.

    'Linea' e nome proprio, mas 'Linha' e palavra comum em portugues
    (significa 'linha', como em 'linha de producao'). O modelo tende a
    autocorrigir para a grafia comum mesmo com instrucao explicita no
    prompt. Instrucao sozinha nao e garantia — aqui a correcao roda
    sempre, independente do que o modelo escreveu, preservando o padrao
    de capitalizacao (CAIXA ALTA / Titulo / minusculo) do trecho original.
    """
    def _rep(m):
        original = m.group(0)
        if original.isupper():
            return "LINEA ALIMENTOS"
        if original[0].isupper():
            return "Linea Alimentos"
        return "linea alimentos"
    return re.sub(r'\bLinha\s+Alimentos\b', _rep, texto, flags=re.IGNORECASE)


def _r(v, casas=2):
    """Arredonda tolerando None."""
    return None if v is None else round(float(v), casas)


def _hoje_br() -> datetime.date:
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()


def _garantir_tabelas(db: Session) -> None:
    for stmt in [s.strip() for s in DDL_CACHE.split(";") if s.strip()]:
        db.execute(text(stmt))
    db.commit()


def _ciclo_atual(db: Session) -> str:
    try:
        from app.api.routers.shared_ibp import get_current_cycle
        return get_current_cycle(db)
    except Exception:
        return _hoje_br().strftime("%m/%Y")


def _normalizar(txt: str) -> str:
    import unicodedata, re
    t = unicodedata.normalize("NFKD", txt or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _chave(*partes) -> str:
    bruto = "|".join(str(p) for p in partes)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:40]


def janela_do_ciclo(ciclo: str) -> Dict[str, Any]:
    """
    Deriva a janela de analise do ciclo ativo.

    Ciclo 08/2026 -> ultimo mes fechado = 07/2026
      YoY:      Jan-Jul de 2026, 2025, 2024, 2023
      Detalhe:  Fev a Jul/2026 (6 meses fechados)

    O YoY usa o MESMO recorte de meses em cada ano. Comparar 7 meses
    contra 12 mediria calendario, nao desempenho.
    """
    mes, ano = int(ciclo[:2]), int(ciclo[3:])
    ref = datetime.date(ano, mes, 1) - relativedelta(months=1)   # ultimo fechado

    ini_det = (ref - relativedelta(months=MESES_DETALHE - 1)).replace(day=1)
    meses_detalhe = []
    d = ini_det
    while d <= ref:
        meses_detalhe.append(d.strftime("%Y-%m"))
        d += relativedelta(months=1)

    anos = list(range(ref.year - (ANOS_YOY - 1), ref.year + 1))
    return {
        "ciclo":          ciclo,
        "mes_referencia": ref.strftime("%Y-%m"),
        "mes_num_fim":    ref.month,
        "anos_yoy":       anos,
        "meses_detalhe":  meses_detalhe,
        "ini_detalhe":    ini_det.isoformat(),
        "fim_detalhe":    ref.isoformat(),
        "rotulo_yoy":     f"Jan a {ref.strftime('%b')}/{{ano}}",
    }


# =====================================================================
# DATASET — LE DOS MARTS
# =====================================================================
_SQL_YOY = """
SELECT EXTRACT(YEAR FROM a.mes)::int              AS ano,
       SUM(a.qt_pedido)                           AS qt_pedido,
       SUM(a.vl_pedido)                           AS vl_pedido,
       SUM(a.qt_entregue)                         AS qt_entregue,
       SUM(a.vl_entregue)                         AS vl_entregue,
       SUM(a.qt_corte)                            AS qt_corte,
       SUM(a.vl_corte)                            AS vl_corte,
       SUM(a.qt_plano)                            AS qt_plano,
       SUM(a.erro_abs_cx)                         AS erro_abs,
       SUM(a.qt_pedido) FILTER (WHERE a.tem_plano) AS qt_com_plano,
       SUM(a.vl_excesso_plano)                    AS vl_excesso,
       SUM(a.vl_perda_subplano)                   AS vl_subplano,
       COUNT(DISTINCT a.sku)                      AS n_skus
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.qt_pedido > 0
  AND EXTRACT(MONTH FROM a.mes) <= :mes_fim
  AND EXTRACT(YEAR  FROM a.mes) = ANY(:anos)
GROUP BY 1 ORDER BY 1
"""

_SQL_MENSAL = """
SELECT TO_CHAR(a.mes, 'YYYY-MM')                  AS mes,
       SUM(a.qt_pedido)                           AS qt_pedido,
       SUM(a.vl_pedido)                           AS vl_pedido,
       SUM(a.qt_entregue)                         AS qt_entregue,
       SUM(a.vl_entregue)                         AS vl_entregue,
       SUM(a.qt_corte)                            AS qt_corte,
       SUM(a.vl_corte)                            AS vl_corte,
       SUM(a.qt_plano)                            AS qt_plano,
       SUM(a.erro_abs_cx)                         AS erro_abs,
       SUM(a.qt_pedido) FILTER (WHERE a.tem_plano) AS qt_com_plano,
       SUM(a.vl_excesso_plano)                    AS vl_excesso,
       SUM(a.vl_perda_subplano)                   AS vl_subplano,
       -- FVA: as duas pernas na MESMA populacao (onde a IA opinou).
       SUM(a.erro_abs_cx)  FILTER (WHERE a.qt_ia IS NOT NULL) AS erro_h_ia,
       SUM(ABS(COALESCE(a.qt_ia,0) - a.qt_pedido))
           FILTER (WHERE a.qt_ia IS NOT NULL)     AS erro_ia,
       SUM(a.qt_pedido)    FILTER (WHERE a.qt_ia IS NOT NULL) AS qt_base_ia
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
GROUP BY 1 ORDER BY 1
"""

_SQL_CATEGORIA = """
SELECT a.categoria,
       SUM(a.qt_pedido)   AS qt_pedido,
       SUM(a.vl_pedido)   AS vl_pedido,
       SUM(a.qt_entregue) AS qt_entregue,
       SUM(a.qt_corte)    AS qt_corte,
       SUM(a.vl_corte)    AS vl_corte,
       SUM(a.qt_plano)    AS qt_plano,
       SUM(a.erro_abs_cx) AS erro_abs,
       SUM(a.vl_excesso_plano)  AS vl_excesso,
       SUM(a.vl_perda_subplano) AS vl_subplano,
       SUM(a.qt_pedido) FILTER (WHERE a.tem_plano) AS qt_com_plano
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
GROUP BY 1 ORDER BY SUM(a.erro_abs_cx) DESC
"""

_SQL_SKU = """
SELECT a.sku,
       COALESCE(MAX(p.descricao), a.sku) AS descricao,
       MAX(a.categoria)   AS categoria,
       MAX(a.maturidade)  AS maturidade,
       SUM(a.qt_pedido)   AS qt_pedido,
       SUM(a.qt_plano)    AS qt_plano,
       SUM(a.qt_entregue) AS qt_entregue,
       SUM(a.qt_corte)    AS qt_corte,
       SUM(a.vl_corte)    AS vl_corte,
       SUM(a.erro_abs_cx) AS erro_abs,
       SUM(a.vl_excesso_plano)  AS vl_excesso,
       SUM(a.vl_perda_subplano) AS vl_subplano,
       AVG(a.pmv)         AS pmv,
       BOOL_AND(a.tem_plano) AS sempre_com_plano
FROM mart_acuracia_sku_mes a
LEFT JOIN dim_produtos p ON p.sku = a.sku
WHERE a.ativo AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
GROUP BY a.sku
"""


def montar_dataset(db: Session, meses: List[str] = None,
                   base: str = "pedido", unidade: str = "cx",
                   ciclo: str = None) -> Dict[str, Any]:
    """
    Monta o dataset do ciclo ativo.

    `meses`, `base` e `unidade` sao aceitos por compatibilidade com o
    frontend e IGNORADOS: o relatorio nao depende de filtro. A janela vem
    do ciclo ativo no painel admin.
    """
    ciclo = ciclo or _ciclo_atual(db)
    jan   = janela_do_ciclo(ciclo)

    p_det = {"ini": jan["ini_detalhe"], "fim": jan["fim_detalhe"]}

    # Total de SKUs ativos no portfolio (dim_produtos), independente de terem
    # tido pedido no periodo. Um SKU pode estar ativo e nao ter vendido nada
    # no recorte — n_skus (mais abaixo) conta so quem teve pedido, e os dois
    # numeros nao sao a mesma coisa. Sem isso o relatorio ja escreveu que
    # 109 SKUs com pedido "representavam a totalidade do portfolio", quando
    # o portfolio ativo tinha 112.
    total_skus_ativos = db.execute(
        text("SELECT COUNT(*) FROM dim_produtos WHERE ativo = TRUE")
    ).scalar() or 0

    # ---- YoY: mesmo recorte de meses em cada ano ----
    yoy = []
    for r in db.execute(text(_SQL_YOY),
                        {"mes_fim": jan["mes_num_fim"], "anos": jan["anos_yoy"]}).fetchall():
        qp = float(r.qt_pedido or 0)
        yoy.append({
            "ano":        int(r.ano),
            "qt_pedido":  round(qp),
            "vl_pedido":  round(float(r.vl_pedido or 0)),
            "qt_entregue": round(float(r.qt_entregue or 0)),
            "vl_entregue": round(float(r.vl_entregue or 0)),
            "qt_corte":   round(float(r.qt_corte or 0)),
            "vl_corte":   round(float(r.vl_corte or 0)),
            "qt_plano":   round(float(r.qt_plano or 0)),
            "wmape":      _r(_sdiv(r.erro_abs, qp, 100)),
            "bias":       _r(_sdiv(float(r.qt_plano or 0) - qp, qp, 100)),
            "fill_rate":  _r(_sdiv(r.qt_entregue, qp, 100), 1),
            "cobertura_plano": _r(_sdiv(r.qt_com_plano, qp, 100), 1),
            "vl_excesso": round(float(r.vl_excesso or 0)),
            "vl_subplano": round(float(r.vl_subplano or 0)),
            "n_skus":     int(r.n_skus or 0),
        })

    # ---- Mensal: ultimos 6 fechados ----
    mensal = []
    for r in db.execute(text(_SQL_MENSAL), p_det).fetchall():
        qp = float(r.qt_pedido or 0)
        wm_h = _sdiv(r.erro_h_ia, r.qt_base_ia, 100)
        wm_i = _sdiv(r.erro_ia,   r.qt_base_ia, 100)
        mensal.append({
            "mes":        r.mes,
            "qt_pedido":  round(qp),
            "vl_pedido":  round(float(r.vl_pedido or 0)),
            "qt_entregue": round(float(r.qt_entregue or 0)),
            "vl_entregue": round(float(r.vl_entregue or 0)),
            "qt_corte":   round(float(r.qt_corte or 0)),
            "vl_corte":   round(float(r.vl_corte or 0)),
            "qt_plano":   round(float(r.qt_plano or 0)),
            "wmape":      _r(_sdiv(r.erro_abs, qp, 100)),
            "bias":       _r(_sdiv(float(r.qt_plano or 0) - qp, qp, 100)),
            "fill_rate":  _r(_sdiv(r.qt_entregue, qp, 100), 1),
            "cobertura_plano": _r(_sdiv(r.qt_com_plano, qp, 100), 1),
            "vl_excesso": round(float(r.vl_excesso or 0)),
            "vl_subplano": round(float(r.vl_subplano or 0)),
            "wmape_h_comparavel": _r(wm_h),
            "wmape_ia":   _r(wm_i),
            "fva":        _r(wm_h - wm_i) if (wm_h is not None and wm_i is not None) else None,
        })

    # ---- Categoria ----
    categorias = []
    for r in db.execute(text(_SQL_CATEGORIA), p_det).fetchall():
        qp = float(r.qt_pedido or 0)
        categorias.append({
            "categoria":  r.categoria or "SEM CATEGORIA",
            "qt_pedido":  round(qp),
            "vl_pedido":  round(float(r.vl_pedido or 0)),
            "qt_corte":   round(float(r.qt_corte or 0)),
            "vl_corte":   round(float(r.vl_corte or 0)),
            "wmape":      _r(_sdiv(r.erro_abs, qp, 100)),
            "bias":       _r(_sdiv(float(r.qt_plano or 0) - qp, qp, 100)),
            "fill_rate":  _r(_sdiv(r.qt_entregue, qp, 100), 1),
            "cobertura_plano": _r(_sdiv(r.qt_com_plano, qp, 100), 1),
            "vl_excesso": round(float(r.vl_excesso or 0)),
            "vl_subplano": round(float(r.vl_subplano or 0)),
        })

    # ---- SKU: rankings ----
    skus = []
    for r in db.execute(text(_SQL_SKU), p_det).fetchall():
        qp = float(r.qt_pedido or 0)
        skus.append({
            "sku":        r.sku,
            "descricao":  r.descricao,
            "categoria":  r.categoria,
            "maturidade": r.maturidade,
            "qt_pedido":  round(qp),
            "qt_plano":   round(float(r.qt_plano or 0)),
            "qt_corte":   round(float(r.qt_corte or 0)),
            "vl_corte":   round(float(r.vl_corte or 0)),
            "erro_abs":   round(float(r.erro_abs or 0)),
            "vl_excesso": round(float(r.vl_excesso or 0)),
            "vl_subplano": round(float(r.vl_subplano or 0)),
            "pmv":        _r(r.pmv, 4),
            "fill_rate":  _r(_sdiv(r.qt_entregue, qp, 100), 1),
            "sem_plano":  not bool(r.sempre_com_plano),
        })

    erro_total = sum(s["erro_abs"] for s in skus) or 1
    for s in skus:
        s["pct_erro_total"] = _r(s["erro_abs"] / erro_total * 100, 1)

    top_erro    = sorted(skus, key=lambda x: -x["erro_abs"])[:TOP_N]
    top_corte   = sorted(skus, key=lambda x: -x["vl_corte"])[:TOP_N]
    top_excesso = sorted(skus, key=lambda x: -x["vl_excesso"])[:TOP_N]
    top_subplan = sorted(skus, key=lambda x: -x["vl_subplano"])[:TOP_N]
    sem_plano   = [s for s in skus if s["sem_plano"]]

    # ---- Totais do periodo detalhado ----
    tot = {
        "qt_pedido":   sum(m["qt_pedido"]  for m in mensal),
        "vl_pedido":   sum(m["vl_pedido"]  for m in mensal),
        "qt_entregue": sum(m["qt_entregue"] for m in mensal),
        "vl_entregue": sum(m["vl_entregue"] for m in mensal),
        "qt_corte":    sum(m["qt_corte"]   for m in mensal),
        "vl_corte":    sum(m["vl_corte"]   for m in mensal),
        "vl_excesso":  sum(m["vl_excesso"] for m in mensal),
        "vl_subplano": sum(m["vl_subplano"] for m in mensal),
    }
    tot["fill_rate"] = _r(_sdiv(tot["qt_entregue"], tot["qt_pedido"], 100), 1)
    tot["pmv_medio"] = _r(_sdiv(tot["vl_pedido"], tot["qt_pedido"]), 4)
    tot["vl_impacto_total"] = tot["vl_corte"] + tot["vl_excesso"] + tot["vl_subplano"]

    # ---- Detalhe por mês: categorias no grão mensal para o chat ----
    # O chat precisa disto para responder perguntas como "qual categoria
    # mais contribuiu para o WMAPE de junho?" sem usar o agregado do periodo.
    detalhe_mensal = {}
    for m in mensal:
        mes_str = m["mes"]
        mes_d = datetime.date.fromisoformat(mes_str + "-01")
        rows_cat = db.execute(text("""
            SELECT a.categoria,
                   SUM(a.qt_pedido)   AS qt_pedido,
                   SUM(a.qt_plano)    AS qt_plano,
                   SUM(a.erro_abs_cx) AS erro_abs,
                   SUM(a.qt_corte)    AS qt_corte,
                   SUM(a.vl_corte)    AS vl_corte,
                   SUM(a.vl_excesso_plano)  AS vl_excesso,
                   SUM(a.vl_perda_subplano) AS vl_subplano
            FROM mart_acuracia_sku_mes a
            WHERE a.ativo AND a.qt_pedido > 0 AND a.mes = CAST(:mes AS date)
            GROUP BY 1 ORDER BY SUM(a.erro_abs_cx) DESC
        """), {"mes": mes_d.isoformat()}).fetchall()

        cats = []
        for r in rows_cat:
            qp = float(r.qt_pedido or 0)
            cats.append({
                "categoria": r.categoria,
                "qt_pedido": round(qp),
                "wmape":     _r(_sdiv(r.erro_abs, qp, 100)),
                "bias":      _r(_sdiv(float(r.qt_plano or 0) - qp, qp, 100)),
                "qt_corte":  round(float(r.qt_corte or 0)),
                "vl_corte":  round(float(r.vl_corte or 0)),
                "vl_excesso": round(float(r.vl_excesso or 0)),
                "vl_subplano": round(float(r.vl_subplano or 0)),
            })
        detalhe_mensal[mes_str] = cats

    return {
        "janela":        jan,
        "yoy":           yoy,
        "mensal":        mensal,
        "categorias":    categorias,
        "totais":        tot,
        "top_erro":      top_erro,
        "top_corte":     top_corte,
        "top_excesso":   top_excesso,
        "top_subplano":  top_subplan,
        "sem_plano":     sorted(sem_plano, key=lambda x: -x["qt_corte"])[:TOP_N],
        "n_skus":        len(skus),
        "total_skus_ativos_portfolio": int(total_skus_ativos),
        "detalhe_mensal": detalhe_mensal,
    }


# =====================================================================
# PROMPT
# =====================================================================
METODOLOGIA = """
DEFINICOES

WMAPE = soma dos erros absolutos dividida pela soma do realizado, x100.
  Computado em (SKU, mes) e agregado depois. O WMAPE de uma categoria nao e a
  media dos WMAPEs dos SKUs: itens de maior volume pesam proporcionalmente mais.
  Medido SEMPRE em caixas, plano contra pedido. Nunca contra entregue.

BIAS = (soma do previsto menos soma do realizado) dividido pela soma do
  realizado, x100. Positivo indica plano acima do realizado; negativo, abaixo.
  Erros de sinais opostos se cancelam: um BIAS baixo pode conviver com SKUs
  individualmente muito enviesados.

PERSISTENCIA = percentual de meses em que o erro ocorreu na mesma direcao do
  BIAS medio. Igual ou acima de 70% caracteriza vies estrutural, nao aleatorio.

FVA = WMAPE do plano humano menos WMAPE da previsao estatistica, na MESMA
  populacao de SKUs. Positivo: o ajuste manual aumentou o erro. Negativo: reduziu.

FILL RATE = entregue dividido por pedido, em caixas. Mede execucao, nao
  acuracia. Serve para separar erro de previsao de restricao de suprimento:
  fill rate baixo com BIAS negativo indica plano subdimensionado; fill rate
  baixo com BIAS neutro indica restricao operacional.

DIAGNOSTICO WMAPE x BIAS
  WMAPE alto e BIAS proximo de zero: erro disperso. Volatilidade ou sazonalidade
    nao capturada.
  WMAPE alto e BIAS positivo: superestimacao. Capital imobilizado em excesso.
  WMAPE alto e BIAS negativo: subestimacao. Risco de ruptura e perda de venda.
  WMAPE baixo e BIAS proximo de zero: previsao sob controle.

IMPACTO FINANCEIRO (campos novos)

  vl_corte = SOMA DIRETA do campo vlcorte do ERP (fato_vendas), por SKU e mes.
    NAO HA FORMULA. NAO e diferenca entre pedido e entregue. NAO e multiplicado
    por PMV. E' um valor que ja vem pronto do ERP, com nota fiscal por tras.
    Ao citar vl_corte, diga apenas "R$ X em corte, valor registrado no ERP" —
    NUNCA invente um calculo do tipo "diferenca x PMV" para este campo. Se
    descrever um calculo para vl_corte que nao seja "soma direta do ERP", a
    frase esta ERRADA e nao deve ser escrita.

  vl_excesso e vl_subplano SAO CALCULADOS, ao contrario de vl_corte. A conta
    roda por (SKU, mes) — nunca por categoria ou pelo PMV medio do periodo:
      vl_excesso   (naquele SKU, naquele mes) = max(qt_plano - qt_pedido, 0) x PMV do SKU naquele mes
      vl_subplano  (naquele SKU, naquele mes) = max(qt_pedido - qt_plano, 0) x PMV do SKU naquele mes
    O PMV usado e sempre o do SKU no mes especifico (cascata mes -> 3 meses ->
    historico), nunca uma media de categoria. Os valores por categoria ou por
    periodo sao a SOMA dos valores de cada par (SKU, mes) individual — por isso
    uma categoria pode ter excesso e subplano ao mesmo tempo: SKUs diferentes,
    ou o mesmo SKU em meses diferentes, podem errar em direcoes opostas.
    Nenhum dos dois "virou nota fiscal": sao volumes hipoteticos que nunca
    foram vendidos (excesso) ou nunca foram previstos (subplano).

  REGRA DE CONSISTENCIA — excesso, subplano e BIAS tem que contar a MESMA
  historia. Antes de escrever qualquer frase interpretativa sobre excesso ou
  subplano, compare os dois valores e confira contra o sinal do BIAS:
      vl_excesso MAIOR que vl_subplano  <=>  BIAS deve ser POSITIVO
          Leitura correta: "superestimacao", "planejamento acima da demanda",
          "capital potencialmente imobilizado".
          NUNCA chame isso de "planejamento conservador" — conservador
          significa planejar por baixo para evitar excesso de estoque, o que
          produziria subplano dominante, nao excesso dominante. Dizer
          "excesso domina, logo o planejamento foi conservador" e uma
          contradicao direta e nao pode aparecer no relatorio.
      vl_subplano MAIOR que vl_excesso  <=>  BIAS deve ser NEGATIVO
          Leitura correta: "subestimacao", "planejamento abaixo da demanda",
          "risco de ruptura por plano insuficiente".
      Se o sinal do BIAS no JSON nao bater com qual dos dois e maior, NAO
      escreva uma interpretacao — apenas relate os tres numeros (excesso,
      subplano, BIAS) sem qualificar a causa, porque algo no recorte precisa
      de investigacao antes de uma leitura definitiva.

  PMV = vl_pedido / qt_pedido no par (SKU, mes). Razao de totais, nunca media
    de PMVs de SKU ao agregar categoria.

MATURIDADE DO ITEM
  Lancamento: ate 6 meses de historico. WMAPE alto e esperado e nao indica
    falha de processo. Cobrar acuracia de lancamento e incorreto.
  Recente: 7 a 18 meses. Ja ha serie, mas sem ciclo sazonal completo.
  Maduro: acima de 18 meses. Universo em que a acuracia deve ser cobrada.

COBERTURA DE PLANEJAMENTO
  Percentual do volume vendido que tinha plano. Itens sem plano tiveram fill
  rate de 30% contra 94,9% dos planejados no periodo Jan-Jul/2026. Cobertura
  baixa antecede e explica parte do problema de fill rate.

"""
_SYSTEM = f"""Voce e especialista em S&OP e escreve o relatorio de acuracia de demanda
para a diretoria e a gerencia da Linea Alimentos.

REGRAS DE REDACAO - obrigatorias:
- Tom tecnico, factual e imparcial. Descreva o que os dados mostram.
- A empresa se chama "Linea Alimentos" — com E, nao "Linha" (que e uma
  palavra comum em portugues). Confira a grafia sempre que citar o nome.
- PROIBIDO adjetivos de julgamento: desastre, pessimo, absurdo, catastrofico.
  Use: "erro de X porcento", "vies de X pontos percentuais".
- Nunca cite nomes de tabelas, campos de banco, sistemas ou ferramentas.
  Nao mencione Nexus, planilha, plataforma, migracao, transicao em nenhuma hipotese.
- NOME DO PRODUTO: use SEMPRE a descricao exata do campo "descricao",
  sem reescrever, abreviar, traduzir ou padronizar.
- Todo numero citado deve existir no JSON. Nao estime nem invente.
- NUNCA atribua variacao de resultado a mudanca de sistema, plataforma ou metodologia.
  Quando a causa nao for identificavel pelos dados, declare isso diretamente.
- vl_corte NAO tem formula: e soma direta do ERP. Nunca descreva um calculo
  para ele (nada de "diferenca x PMV"). vl_excesso e vl_subplano SAO
  calculados por (SKU, mes) — veja METODOLOGIA para a formula exata e a
  regra de consistencia com o sinal do BIAS antes de qualificar qualquer um
  dos dois como "conservador", "agressivo" ou similar.
- Todo valor em R$ vem acompanhado da conta que o gerou na mesma frase,
  EXCETO vl_corte, que e citado como valor direto do ERP sem formula.
- NUNCA atribua uma recomendacao ou acao a uma area, departamento, cargo ou
  responsavel especifico (nada de "Planejamento de Demanda deve...",
  "a area de Suprimentos precisa..."). Descreva a acao e o resultado
  esperado; quem executa nao e parte do relatorio.

{METODOLOGIA}

ESTRUTURA (700 a 900 palavras, prosa densa):

1. ESCOPO E METODO
   Periodo medido, quantidade de SKUs, origem do plano e limitacoes. Cite os
   DOIS numeros de SKU do contexto: quantos tem pedido no periodo e quantos
   existem no portfolio ativo total. Se forem diferentes, diga "X de Y SKUs
   ativos tiveram pedido no periodo" — PROIBIDO escrever que os SKUs com
   pedido "representam a totalidade do portfolio" quando os dois numeros
   nao forem iguais.

2. LEITURA DO PERIODO
   WMAPE e BIAS do portfolio, classificacao pelo cruzamento e persistencia.

3. EVOLUCAO ANO A ANO
   Compare cada ano usando os mesmos meses do calendario. Diga se o erro
   aumentou, diminuiu ou permaneceu estavel, e quanto.

4. CONCENTRACAO DO ERRO
   Onde o erro absoluto se concentra. Distinga erro percentual de erro em
   volume. Separe maduros de lancamentos.

5. COBERTURA DE PLANEJAMENTO
   Percentual do volume vendido com plano, fill rate dos itens sem plano,
   e o impacto da ausencia de previsao.

6. IMPACTO FINANCEIRO
   vl_corte (receita perdida, valor DIRETO do ERP — cite sem formula).
   vl_excesso e vl_subplano (com a formula por SKU-mes). Total e distribuicao
   por categoria. Ao comentar qual dos dois domina numa categoria, aplique a
   regra de consistencia com o BIAS da METODOLOGIA antes de escrever.

7. ATENDIMENTO E ORIGEM DO CORTE
   Fill rate do portfolio e evolucao mes a mes. Maior corte por categoria.
   Classifique a origem: BIAS negativo indica planejamento; neutro ou positivo
   indica restricao operacional.

8. VALOR AGREGADO DA PREVISAO
   FVA: onde o ajuste manual aumentou ou reduziu o erro.

9. RECOMENDACOES
   Quatro a seis itens objetivos e diretos. Cada item traz o dado numerico
   que o sustenta e o resultado esperado. Descreva O QUE fazer e POR QUE —
   nunca QUEM deve fazer. Nao atribua a acao a nenhuma area, departamento
   ou cargo.

10. CONSIDERACAO FINAL
   Um paragrafo unico. Responda: o processo esta melhorando ou piorando,
   onde esta o maior ganho no proximo ciclo, e qual o principal risco.
   Sem repetir numeros ja citados.

IMPORTANTE: complete todas as 10 secoes. Se precisar economizar espaco,
encurte as secoes 4 e 5, mas sempre entregue a secao 10 completa.

Escreva em portugues do Brasil, paragrafos curtos.

FORMATACAO: prosa limpa, sem markdown. Sem #, **, ---, *, _.
Titulos em MAIUSCULAS numa linha isolada, precedidos do numero.
Exemplo: 1. ESCOPO E METODO
Numeros em padrao brasileiro: 1.234.567 e R$ 1.234.567,89.
Sem emojis. Sem preambulo. Cite numeros dentro das frases, nao em tabela."""


def _contexto_modelo(ds: Dict[str, Any]) -> str:
    jan = ds["janela"]
    total_ativos = ds.get("total_skus_ativos_portfolio", ds["n_skus"])
    linhas = [
        f"CICLO ATIVO: {jan['ciclo']}",
        f"ULTIMO MES FECHADO: {jan['mes_referencia']}",
        f"JANELA YoY: meses 01 a {jan['mes_num_fim']:02d} de cada ano em {jan['anos_yoy']}",
        f"JANELA DETALHE: {', '.join(jan['meses_detalhe'])}",
        f"SKUs ATIVOS NO PORTFOLIO (dim_produtos, ativo=true): {total_ativos}",
        f"SKUs ATIVOS QUE TIVERAM PEDIDO NO PERIODO ANALISADO: {ds['n_skus']}",
        "  -> Estes dois numeros SAO DIFERENTES por definicao: o primeiro e o",
        "     portfolio inteiro; o segundo e quem vendeu no recorte. NUNCA",
        "     diga que o segundo 'representa a totalidade do portfolio' —",
        f"     diga '{ds['n_skus']} de {total_ativos} SKUs ativos tiveram pedido",
        "     no periodo' quando os dois numeros forem diferentes.",
        "",
        "DADOS (JSON):",
        json.dumps({
            "yoy":          ds["yoy"],
            "mensal":       ds["mensal"],
            "categorias":   ds["categorias"],
            "totais":       ds["totais"],
            "top_erro":     ds["top_erro"],
            "top_corte":    ds["top_corte"],
            "top_excesso":  ds["top_excesso"],
            "top_subplano": ds["top_subplano"],
            "sem_plano":    ds["sem_plano"],
        }, ensure_ascii=False, separators=(",", ":")),
    ]
    return "\n".join(linhas)


def _chamar_claude(system: str, mensagens: List[Dict[str, str]],
                   max_tokens: int = 8000) -> str:
    """
    Chama a API Anthropic. Haiku por padrao: o relatorio e prosa sobre um
    JSON ja preparado — o modelo descreve e cita, nao calcula.
    """
    chave = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not chave:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ausente. Verifique o .env: chave sem espacos "
            "em volta do '=' e sem quebra de linha no fim."
        )

    try:
        import anthropic
    except ImportError:
        raise RuntimeError("Pacote 'anthropic' nao instalado no container.")

    cliente = anthropic.Anthropic(api_key=chave)
    modelos = [m for m in [
        (os.environ.get("ANTHROPIC_MODEL") or "").strip(),
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
    ] if m]

    ultimo_erro = None
    for modelo in modelos:
        try:
            resp = cliente.messages.create(
                model=modelo,
                max_tokens=max_tokens,
                temperature=0,          # relatorio e reproduzivel, nao criativo
                system=system,
                messages=mensagens,
            )
            return "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            ).strip()
        except Exception as e:
            ultimo_erro = e
            logger.warning("Modelo %s falhou: %s", modelo, e)
            continue

    raise RuntimeError(f"Nenhum modelo respondeu. Ultimo erro: {ultimo_erro}")


# =====================================================================
# PERSISTENCIA
# =====================================================================
def relatorio_existente(db: Session, meses: List[str] = None,
                        base: str = "pedido", unidade: str = "cx",
                        ciclo: str = None) -> Optional[Dict[str, Any]]:
    """Relatorio ja gravado para o ciclo. Um por ciclo, sem recorte."""
    _garantir_tabelas(db)
    ciclo = ciclo or _ciclo_atual(db)
    r = db.execute(text("""
        SELECT relatorio, gerado_por, gerado_em
        FROM agente_relatorio_cache
        WHERE ciclo_sop = :c AND chave = :k
    """), {"c": ciclo, "k": CHAVE_CICLO}).fetchone()
    if not r:
        return None
    return {
        "relatorio":  r.relatorio,
        "gerado_por": r.gerado_por,
        "gerado_em":  r.gerado_em.isoformat() if r.gerado_em else None,
        "ciclo":      ciclo,
        "do_cache":   True,
    }


def salvar_relatorio(db: Session, texto: str, usuario: str,
                     ciclo: str, ds: Dict[str, Any] = None) -> None:
    """
    Grava o relatorio E o dataset que o originou.

    O dataset fica junto para o chat responder sem remontar tudo: era a
    montagem ao vivo que deixava a tela travada em "consultando dados".
    """
    _garantir_tabelas(db)
    jan = ds["janela"] if ds else {}
    db.execute(text("""
        INSERT INTO agente_relatorio_cache
            (ciclo_sop, chave, meses, base, unidade, relatorio, dataset, gerado_por)
        VALUES (:c, :k, :m, 'pedido', 'cx', :r, :d, :p)
        ON CONFLICT (ciclo_sop, chave) DO UPDATE SET
            relatorio  = EXCLUDED.relatorio,
            dataset    = EXCLUDED.dataset,
            gerado_por = EXCLUDED.gerado_por,
            gerado_em  = now()
    """), {
        "c": ciclo, "k": CHAVE_CICLO,
        "m": ",".join(jan.get("meses_detalhe", [])),
        "r": texto,
        "d": json.dumps(ds, ensure_ascii=False) if ds else None,
        "p": usuario,
    })
    db.commit()


def _dataset_salvo(db: Session, ciclo: str) -> Optional[Dict[str, Any]]:
    r = db.execute(text("""
        SELECT dataset FROM agente_relatorio_cache
        WHERE ciclo_sop = :c AND chave = :k
    """), {"c": ciclo, "k": CHAVE_CICLO}).fetchone()
    if r and r.dataset:
        try:
            return json.loads(r.dataset)
        except Exception:
            return None
    return None


# =====================================================================
# GERACAO
# =====================================================================
def gerar_relatorio(db: Session, meses: List[str] = None,
                    base: str = "pedido", unidade: str = "cx",
                    usuario: str = "-", forcar: bool = False,
                    ciclo: str = None) -> Dict[str, Any]:
    """
    Gera (ou devolve do cache) o relatorio do ciclo.

    `meses`, `base` e `unidade` sao ignorados: um ciclo, um relatorio.
    """
    ciclo = ciclo or _ciclo_atual(db)

    if not forcar:
        cache = relatorio_existente(db, ciclo=ciclo)
        if cache:
            return cache

    ds = montar_dataset(db, ciclo=ciclo)
    if not ds["mensal"]:
        raise RuntimeError(
            f"Sem dados fechados para o ciclo {ciclo}. "
            "Rode o pipeline para atualizar os marts."
        )

    texto = _chamar_claude(
        _SYSTEM,
        [{"role": "user", "content": _contexto_modelo(ds)}],
    )
    texto = _corrigir_nome_empresa(texto)
    salvar_relatorio(db, texto, usuario, ciclo, ds)
    return {
        "relatorio":  texto,
        "gerado_por": usuario,
        "gerado_em":  datetime.datetime.utcnow().isoformat(),
        "ciclo":      ciclo,
        "do_cache":   False,
    }


def gerar_relatorio_ciclo(db: Session, ciclo: str,
                          usuario: str = "Sistema",
                          forcar: bool = True) -> Dict[str, Any]:
    """
    Atalho chamado pelo pipeline ao abrir um ciclo novo.

    forcar=True por padrao: o ciclo acabou de nascer, entao qualquer
    relatorio anterior com essa chave seria de dados velhos.
    """
    r = gerar_relatorio(db, ciclo=ciclo, usuario=usuario, forcar=forcar)
    # mes_referencia no retorno: o pipeline registra no log qual mes fechado
    # o relatorio audita, e sem isso o operador nao sabe o que foi gerado.
    r["mes_referencia"] = janela_do_ciclo(ciclo)["mes_referencia"]
    return r


# =====================================================================
# CHAT
# =====================================================================
_SYSTEM_CHAT = f"""Voce e especialista em S&OP da Linea Alimentos respondendo perguntas
sobre a acuracia da previsao de demanda.

{METODOLOGIA}

Regras:
- Responda APENAS com base no JSON. Nunca invente numeros.
- Se o dado nao estiver no recorte, diga claramente.
- Tom tecnico e imparcial, sem adjetivos de julgamento.
- Nao cite nomes de tabelas, sistemas ou ferramentas. Nao mencione Nexus,
  planilha, plataforma, migracao, transicao ou curva de aprendizado.
  PROIBIDO atribuir qualquer variacao de resultado a mudanca de sistema.
- NOME DO PRODUTO: use SEMPRE a descricao exata do campo "descricao",
  sem reescrever, abreviar, traduzir ou padronizar.
- ESCOPO TEMPORAL DA PERGUNTA: se a pergunta menciona um mes especifico (ex:
  "junho", "jun/2026", "no mes passado"), use o bloco "mensal" daquele mes
  e os dados de "categorias" filtrados para aquele periodo — eles trazem
  as categorias e os itens com maior erro NAQUELE mes. NAO responda com o
  agregado do periodo inteiro quando a pergunta e sobre um mes especifico.
  Se a pergunta nao especifica mes, use o agregado do periodo.
- vl_corte NAO tem formula: e soma direta do ERP. Nunca invente um calculo
  para ele. vl_excesso e vl_subplano SAO calculados por (SKU, mes) — antes
  de chamar um de "conservador" ou "agressivo", confira contra o sinal do
  BIAS (ver METODOLOGIA): excesso dominante = BIAS positivo = superestimacao,
  nunca "conservador". Subplano dominante = BIAS negativo = subestimacao.
- Todo valor em R$ vem acompanhado da conta que o gerou na mesma frase,
  EXCETO vl_corte, que e citado como valor direto do ERP sem formula.
- NUNCA atribua uma recomendacao a uma area, departamento ou responsavel
  especifico. Descreva a acao e o resultado esperado, nao quem executa.
- Seja conciso. Cite os numeros que fundamentam a resposta.

FORMATO DA RESPOSTA:
- Comece com uma frase de resposta direta a pergunta, sem titulo.
- Use no maximo dois niveis de titulo com "## " (dois cerquilhas e espaco).
- Use **negrito** apenas para numeros-chave e nomes de item.
- Tabelas: use pipe simples com cabecalho e linha separadora. Maximo 6
  colunas e 10 linhas. Ordene da maior para a menor contribuicao.
- Termine com uma linha iniciada por "Leitura: " contendo a conclusao pratica.
- Conclua sempre o raciocinio. Nunca interrompa no meio de uma frase.
"""


def responder_pergunta(db: Session, pergunta: str,
                       meses: List[str] = None, base: str = "pedido",
                       unidade: str = "cx", historico: List[dict] = None,
                       ciclo: str = None) -> Dict[str, Any]:
    """
    Responde uma pergunta sobre o ciclo.

    Reaproveita o dataset gravado junto do relatorio. Remontar a cada
    pergunta era o que deixava a tela travada em "consultando dados".
    """
    _garantir_tabelas(db)
    ciclo = ciclo or _ciclo_atual(db)

    ch = _chave(ciclo, _normalizar(pergunta))
    r = db.execute(text("""
        SELECT resposta FROM agente_chat_cache
        WHERE ciclo_sop = :c AND chave = :k
    """), {"c": ciclo, "k": ch}).fetchone()
    if r:
        return {"resposta": r.resposta, "do_cache": True}

    ds = _dataset_salvo(db, ciclo) or montar_dataset(db, ciclo=ciclo)

    msgs = []
    for h in (historico or [])[-4:]:
        papel = "user" if h.get("role") == "user" else "assistant"
        msgs.append({"role": papel, "content": str(h.get("content", ""))[:2000]})
    msgs.append({
        "role": "user",
        "content": f"{_contexto_modelo(ds)}\n\nDETALHE POR MES:\n{json.dumps(ds.get('detalhe_mensal', {}), ensure_ascii=False, separators=(',', ':'))}\n\nPERGUNTA: {pergunta}",
    })

    resposta = _chamar_claude(_SYSTEM_CHAT, msgs, max_tokens=1500)
    resposta = _corrigir_nome_empresa(resposta)

    db.execute(text("""
        INSERT INTO agente_chat_cache (ciclo_sop, chave, pergunta, resposta)
        VALUES (:c, :k, :p, :r)
        ON CONFLICT (ciclo_sop, chave) DO UPDATE SET
            resposta = EXCLUDED.resposta, gerado_em = now()
    """), {"c": ciclo, "k": ch, "p": pergunta, "r": resposta})
    db.commit()

    return {"resposta": resposta, "do_cache": False}


# =====================================================================
# PDF
# =====================================================================
def gerar_pdf(relatorio: str, ds: Dict[str, Any] = None) -> bytes:
    """PDF do relatorio com um resumo numerico no cabecalho."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    import io as _io

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm)
    ss = getSampleStyleSheet()
    st_tit = ParagraphStyle("t", parent=ss["Heading2"], fontSize=10,
                            textColor=colors.HexColor("#6d28d9"), spaceBefore=10, spaceAfter=5)
    st_txt = ParagraphStyle("p", parent=ss["BodyText"], fontSize=9,
                            leading=13.5, alignment=TA_JUSTIFY, spaceAfter=6)

    fluxo = [Paragraph("Relatorio S&OP — Nexus", ss["Heading1"]), Spacer(1, 4)]

    if ds and ds.get("totais"):
        t, jan = ds["totais"], ds.get("janela", {})
        fluxo.append(Paragraph(
            f"Ciclo {jan.get('ciclo','-')} · ultimo mes fechado {jan.get('mes_referencia','-')}",
            st_txt))
        dados = [
            ["Pedido (cx)",   f"{t['qt_pedido']:,.0f}".replace(",", ".")],
            ["Entregue (cx)", f"{t['qt_entregue']:,.0f}".replace(",", ".")],
            ["Corte (cx)",    f"{t['qt_corte']:,.0f}".replace(",", ".")],
            ["Fill Rate",     f"{t['fill_rate']}%"],
            ["Receita pedida (R$)",  f"{t['vl_pedido']:,.0f}".replace(",", ".")],
            ["Perda no corte (R$)",  f"{t['vl_corte']:,.0f}".replace(",", ".")],
            ["Excesso de plano (R$)", f"{t['vl_excesso']:,.0f}".replace(",", ".")],
        ]
        tb = Table(dados, colWidths=[70*mm, 40*mm])
        tb.setStyle(TableStyle([
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#f8fafc")),
            ("ALIGN", (1,0), (1,-1), "RIGHT"),
        ]))
        fluxo += [tb, Spacer(1, 8)]

    for linha in (relatorio or "").split("\n"):
        t = linha.strip().replace("**", "")
        if not t:
            continue
        eh_titulo = (t[:1].isdigit() and len(t) < 70) or (t == t.upper() and 3 < len(t) < 70)
        fluxo.append(Paragraph(t, st_tit if eh_titulo else st_txt))

    doc.build(fluxo)
    return buf.getvalue()