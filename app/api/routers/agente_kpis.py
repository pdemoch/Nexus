"""
agente_kpis.py — Agente analítico de KPIs S&OP (Nexus / Linea Alimentos)

Responsabilidades:
  1. Montar o dataset consolidado que fundamenta o relatório (tudo rastreável).
  2. Chamar a API da Anthropic para produzir a análise narrativa.
  3. Responder perguntas ad-hoc sobre os indicadores (chat).
  4. Gerar o PDF one-page + anexo com os 114 SKUs

Princípios:
  • O modelo NUNCA vê dados brutos — recebe JSON pré-agregado e compacto.
  • Todo número afirmado tem origem declarada (tabela + fórmula).
  • Economia de tokens: só top-N no contexto; tabela completa vai só no PDF.

Fontes de dados (validadas no banco):
  fato_vendas            → qt_pedido, vl_pedido, qtfatura, vlfatura, qtcorte, vlcorte
  fato_ibp_granular      → vol_ia, vol_final (M-2), pmv_aplicado  [jun/26+]
  fato_previsao_humana   → vol_humano, fonte='HISTORICO'          [jan/23–mai/26]
  dim_produtos           → sku, descricao, categoria, ativo
"""

import os
import io
import json
import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
from dateutil.relativedelta import relativedelta


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTES DE ESCOPO — declaradas no relatório para auditoria
# ═══════════════════════════════════════════════════════════════════════════
CICLO_PISO       = datetime.date(2026, 4, 1)   # primeiro ciclo Nexus
DEFASAGEM_MESES  = 2                            # M-2: previsão congelada 2 meses antes
PISO_NEXUS       = datetime.date(2026, 6, 1)   # CICLO_PISO + DEFASAGEM = primeiro mês auditável
INICIO_HISTORICO = datetime.date(2023, 1, 1)


def _sdiv(num, den, mult=1.0):
    """Divisão segura: retorna None se denominador <= 0 ou NaN."""
    try:
        d = float(den)
        if d <= 0 or not (d == d):
            return None
        return float(num) / d * float(mult)
    except (ZeroDivisionError, TypeError, ValueError):
        return None


def _hoje_br() -> datetime.date:
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()


def _ultimo_mes_fechado() -> str:
    h = _hoje_br().replace(day=1)
    return (h - datetime.timedelta(days=1)).strftime("%Y-%m")


# ═══════════════════════════════════════════════════════════════════════════
# DATASET CONSOLIDADO
# ═══════════════════════════════════════════════════════════════════════════
def montar_dataset(
    db: Session,
    meses: List[str],
    base: str = "pedido",       # pedido | faturado
    unidade: str = "cx",        # cx | rs
) -> Dict[str, Any]:
    """
    Monta o dataset completo que fundamenta o relatório.

    Granularidade base do erro: SEMPRE (sku, mês).
    O erro absoluto é computado nesse nível e só depois agregado — nunca
    agregamos volumes antes de calcular o erro, senão erros de sinais
    opostos se cancelariam e o WMAPE ficaria artificialmente baixo.

    base:    'pedido'   -> realizado = qt_pedido (demanda do cliente)
             'faturado' -> realizado = qtfatura  (o que a empresa entregou)
    unidade: 'cx' -> erro em caixas
             'rs' -> erro em R$, valorizado pelo PMV do PEDIDO (vl_pedido/qt_pedido).
                     O mesmo PMV multiplica previsto e realizado, isolando o erro
                     de volume sem contaminação de desconto de faturamento.
    """
    teto = _ultimo_mes_fechado()
    meses_validos = sorted({m for m in meses if m <= teto})
    if not meses_validos:
        return {"erro": "Nenhum mês fechado no período selecionado."}

    ini = meses_validos[0]  + "-01"
    fim = meses_validos[-1] + "-01"

    # Período espelho no ano anterior (YoY)
    meses_yoy = []
    for m in meses_validos:
        d = datetime.datetime.strptime(m + "-01", "%Y-%m-%d").date()
        meses_yoy.append((d - relativedelta(years=1)).strftime("%Y-%m"))
    ini_yoy = min(meses_yoy) + "-01"
    fim_yoy = max(meses_yoy) + "-01"

    col_vol = "qtfatura" if base == "faturado" else "qt_pedido"

    # ── Query central: erro por (sku, mês), com PMV do pedido ──────────────
    sql_base = f"""
        WITH ativos AS (
            SELECT sku, descricao, COALESCE(categoria,'SEM CATEGORIA') AS categoria
            FROM dim_produtos
            WHERE COALESCE(ativo, FALSE) = TRUE
        ),
        primeira_venda AS (
            SELECT TRIM(v.sku::text) AS sku,
                   MIN(DATE_TRUNC('month', v.data_pedido))::date AS nasceu
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            GROUP BY 1
        ),
        -- PMV do PEDIDO por SKU/mes (N1). Cobertura validada em 99,98 por cento
        pmv_mes AS (
            SELECT TRIM(v.sku::text) AS sku,
                   DATE_TRUNC('month', v.data_pedido)::date AS mes,
                   SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0) AS pmv
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            GROUP BY 1, 2
            HAVING SUM(v.qt_pedido) > 0
        ),
        -- PMV global do SKU (N3 — fallback)
        pmv_global AS (
            SELECT TRIM(v.sku::text) AS sku,
                   SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0) AS pmv
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            GROUP BY 1
            HAVING SUM(v.qt_pedido) > 0
        ),
        realizado AS (
            SELECT TRIM(v.sku::text) AS sku,
                   DATE_TRUNC('month', v.data_pedido)::date AS mes,
                   SUM(v.{col_vol})  AS vol_real,
                   SUM(v.qt_pedido)  AS qt_pedido,
                   SUM(v.qtfatura)   AS qt_fatura,
                   SUM(v.vl_pedido)  AS vl_pedido,
                   SUM(v.vlfatura)   AS vl_fatura
            FROM fato_vendas v
            JOIN ativos a ON a.sku = TRIM(v.sku::text)
            WHERE v.data_pedido >= :ini
              AND v.data_pedido < (CAST(:fim AS date) + INTERVAL '1 month')
            GROUP BY 1, 2
        ),
        -- Meta humana: Excel até mai/26, Nexus vol_final M-2 a partir de jun/26
        humano_hist AS (
            SELECT h.sku, h.mes_projetado::date AS mes, SUM(h.vol_humano) AS vol_humano
            FROM fato_previsao_humana h
            JOIN ativos a ON a.sku = h.sku
            WHERE h.fonte = 'HISTORICO'
              AND h.mes_projetado >= :ini AND h.mes_projetado <= :fim
            GROUP BY 1, 2
        ),
        humano_nexus AS (
            SELECT TRIM(i.sku::text) AS sku, i.mes_projetado::date AS mes,
                   SUM(i.vol_final) AS vol_humano
            FROM fato_ibp_granular i
            JOIN ativos a ON a.sku = TRIM(i.sku::text)
            WHERE (EXTRACT(YEAR FROM i.mes_projetado)*12 + EXTRACT(MONTH FROM i.mes_projetado))
                - (SPLIT_PART(i.ciclo_sop,'/',2)::int*12 + SPLIT_PART(i.ciclo_sop,'/',1)::int) = 2
              AND i.mes_projetado >= '2026-06-01'
              AND i.mes_projetado >= :ini AND i.mes_projetado <= :fim
            GROUP BY 1, 2
        ),
        humano AS (
            SELECT * FROM humano_hist UNION ALL SELECT * FROM humano_nexus
        ),
        ia AS (
            SELECT TRIM(i.sku::text) AS sku, i.mes_projetado::date AS mes,
                   SUM(i.vol_ia) AS vol_ia
            FROM fato_ibp_granular i
            JOIN ativos a ON a.sku = TRIM(i.sku::text)
            WHERE (EXTRACT(YEAR FROM i.mes_projetado)*12 + EXTRACT(MONTH FROM i.mes_projetado))
                - (SPLIT_PART(i.ciclo_sop,'/',2)::int*12 + SPLIT_PART(i.ciclo_sop,'/',1)::int) = 2
              AND i.mes_projetado >= :ini AND i.mes_projetado <= :fim
            GROUP BY 1, 2
        )
        SELECT a.sku, a.descricao, a.categoria,
               TO_CHAR(r.mes,'YYYY-MM') AS mes,
               r.vol_real, r.qt_pedido, r.qt_fatura, r.vl_pedido, r.vl_fatura,
               h.vol_humano, i.vol_ia,
               COALESCE(pm.pmv, pg.pmv, 0) AS pmv
        FROM realizado r
        JOIN ativos a          ON a.sku = r.sku
        JOIN primeira_venda pv ON pv.sku = r.sku AND r.mes >= pv.nasceu
        LEFT JOIN humano h     ON h.sku = r.sku AND h.mes = r.mes
        LEFT JOIN ia i         ON i.sku = r.sku AND i.mes = r.mes
        LEFT JOIN pmv_mes pm   ON pm.sku = r.sku AND pm.mes = r.mes
        LEFT JOIN pmv_global pg ON pg.sku = r.sku
        WHERE r.vol_real > 0
        ORDER BY a.categoria, a.sku, r.mes
    """

    rows     = db.execute(text(sql_base), {"ini": ini,     "fim": fim}).fetchall()
    rows_yoy = db.execute(text(sql_base), {"ini": ini_yoy, "fim": fim_yoy}).fetchall()

    def _peso(r):
        """Fator de conversão: 1 para caixas, PMV para reais."""
        return float(r.pmv or 0) if unidade == "rs" else 1.0

    def _agregar(registros, chave_fn):
        """
        Agrega erro por chave. O erro absoluto é computado em (sku, mês)
        e SÓ DEPOIS somado — preserva a granularidade correta.
        """
        acc: Dict[Any, Dict[str, float]] = {}
        for r in registros:
            if r.mes not in (meses_validos + meses_yoy):
                continue
            k = chave_fn(r)
            a = acc.setdefault(k, {
                "real": 0.0, "hum": 0.0, "ia": 0.0,
                "err_h": 0.0, "err_ia": 0.0,
                "real_h": 0.0, "real_ia": 0.0,
                "qt_ped": 0.0, "qt_fat": 0.0,
                "vl_ped": 0.0, "vl_fat": 0.0,
                "n_meses_h": 0, "n_over_h": 0, "n_under_h": 0,
            })
            p    = _peso(r)
            real = float(r.vol_real or 0) * p
            a["real"]   += real
            a["qt_ped"] += float(r.qt_pedido or 0)
            a["qt_fat"] += float(r.qt_fatura or 0)
            a["vl_ped"] += float(r.vl_pedido or 0)
            a["vl_fat"] += float(r.vl_fatura or 0)

            if r.vol_humano is not None:
                hum = float(r.vol_humano) * p
                a["hum"]    += hum
                a["err_h"]  += abs(hum - real)      # erro ABSOLUTO em (sku, mês)
                a["real_h"] += real
                a["n_meses_h"] += 1
                if real > 0:
                    d = (hum - real) / real
                    if d >  0.02: a["n_over_h"]  += 1
                    if d < -0.02: a["n_under_h"] += 1

            if r.vol_ia is not None:
                ia_v = float(r.vol_ia) * p
                a["ia"]      += ia_v
                a["err_ia"]  += abs(ia_v - real)
                a["real_ia"] += real
        return acc

    def _metricas(a: Dict[str, float]) -> Dict[str, Any]:
        wm_h  = _sdiv(a["err_h"],  a["real_h"],  100)
        bi_h  = _sdiv(a["hum"] - a["real_h"], a["real_h"], 100)
        wm_ia = _sdiv(a["err_ia"], a["real_ia"], 100)
        bi_ia = _sdiv(a["ia"] - a["real_ia"], a["real_ia"], 100)
        fva   = round(wm_h - wm_ia, 1) if (wm_h is not None and wm_ia is not None) else None
        # Persistência: % de meses em que o erro foi na direção do BIAS médio
        n = a["n_meses_h"]
        pers = None
        if n > 0 and bi_h is not None:
            pers = round((a["n_over_h"] if bi_h >= 0 else a["n_under_h"]) / n * 100, 0)
        return {
            "wmape_h":  round(wm_h, 1)  if wm_h  is not None else None,
            "bias_h":   round(bi_h, 1)  if bi_h  is not None else None,
            "wmape_ia": round(wm_ia, 1) if wm_ia is not None else None,
            "bias_ia":  round(bi_ia, 1) if bi_ia is not None else None,
            "fva_pp":   fva,
            "volume":   round(a["real"]),
            "persistencia_pct": pers,
            "meses": n,
        }

    def _classe(m: Dict[str, Any]) -> str:
        b, w = m.get("bias_h"), m.get("wmape_h")
        if b is None or w is None: return "Sem meta"
        if w <= 20 and abs(b) <= 10: return "Saudável"
        if b >  10: return "Superestimando"
        if b < -10: return "Subestimando"
        return "Errático"

    # ── Portfólio ─────────────────────────────────────────────────────────
    port     = _metricas(_agregar(rows,     lambda r: "T")["T"]) if rows     else {}
    port_yoy = _metricas(_agregar(rows_yoy, lambda r: "T")["T"]) if rows_yoy else {}
    if port and port_yoy and port.get("wmape_h") is not None and port_yoy.get("wmape_h") is not None:
        port["yoy_wmape_delta_pp"] = round(port["wmape_h"] - port_yoy["wmape_h"], 1)
        port["yoy_wmape_h"]        = port_yoy["wmape_h"]
        port["yoy_bias_h"]         = port_yoy.get("bias_h")
    port["classe"] = _classe(port)

    # ── Fill Rate (sempre em cx e R$, independe do toggle) ────────────────
    agg_fill = _agregar(rows, lambda r: "T").get("T", {})
    fr_cx = _sdiv(agg_fill.get("qt_fat", 0), agg_fill.get("qt_ped", 0), 100)
    fr_rs = _sdiv(agg_fill.get("vl_fat", 0), agg_fill.get("vl_ped", 0), 100)
    gap   = round(fr_rs - fr_cx, 1) if (fr_cx is not None and fr_rs is not None) else None
    valor_desconto = None
    if gap is not None and agg_fill.get("vl_ped"):
        # Valor perdido além do corte de volume
        esperado_rs = agg_fill["vl_ped"] * (fr_cx / 100)
        valor_desconto = round(esperado_rs - agg_fill.get("vl_fat", 0))

    fill = {
        "fill_rate_cx_pct": round(fr_cx, 1) if fr_cx is not None else None,
        "fill_rate_rs_pct": round(fr_rs, 1) if fr_rs is not None else None,
        "gap_desconto_pp":  gap,
        "valor_desconto_rs": valor_desconto,
        "corte_cx":  round(agg_fill.get("qt_ped", 0) - agg_fill.get("qt_fat", 0)),
        "pedido_cx": round(agg_fill.get("qt_ped", 0)),
    }

    # ── Série mensal (fill rate + wmape) ──────────────────────────────────
    serie = []
    agg_mes     = _agregar(rows, lambda r: r.mes)
    agg_mes_yoy = _agregar(rows_yoy, lambda r: r.mes)
    for m in meses_validos:
        a = agg_mes.get(m)
        if not a: continue
        mt = _metricas(a)
        serie.append({
            "mes": m,
            "wmape_h":  mt["wmape_h"],
            "bias_h":   mt["bias_h"],
            "wmape_ia": mt["wmape_ia"],
            "fill_cx":  round(_sdiv(a["qt_fat"], a["qt_ped"], 100) or 0, 1),
            "fill_rs":  round(_sdiv(a["vl_fat"], a["vl_ped"], 100) or 0, 1),
            "volume":   mt["volume"],
        })

    # ── Categorias (todas — são 13) ───────────────────────────────────────
    agg_cat     = _agregar(rows,     lambda r: r.categoria)
    agg_cat_yoy = _agregar(rows_yoy, lambda r: r.categoria)
    categorias = []
    for cat, a in agg_cat.items():
        mt = _metricas(a)
        prev = agg_cat_yoy.get(cat)
        mt_prev = _metricas(prev) if prev else {}
        delta = None
        if mt.get("wmape_h") is not None and mt_prev.get("wmape_h") is not None:
            delta = round(mt["wmape_h"] - mt_prev["wmape_h"], 1)
        categorias.append({
            "categoria": cat, **mt,
            "classe": _classe(mt),
            "yoy_wmape_h": mt_prev.get("wmape_h"),
            "yoy_delta_pp": delta,
            "fill_cx": round(_sdiv(a["qt_fat"], a["qt_ped"], 100) or 0, 1),
            "fill_rs": round(_sdiv(a["vl_fat"], a["vl_ped"], 100) or 0, 1),
        })
    categorias.sort(key=lambda c: c.get("volume") or 0, reverse=True)

    # ── SKUs (todos — tabela completa vai para o PDF) ─────────────────────
    agg_sku     = _agregar(rows,     lambda r: (r.sku, r.descricao, r.categoria))
    agg_sku_yoy = _agregar(rows_yoy, lambda r: (r.sku, r.descricao, r.categoria))
    skus = []
    for (sk, desc, cat), a in agg_sku.items():
        mt = _metricas(a)
        prev = agg_sku_yoy.get((sk, desc, cat))
        mt_prev = _metricas(prev) if prev else {}
        delta = None
        if mt.get("wmape_h") is not None and mt_prev.get("wmape_h") is not None:
            delta = round(mt["wmape_h"] - mt_prev["wmape_h"], 1)
        skus.append({
            "sku": sk, "descricao": desc, "categoria": cat, **mt,
            "classe": _classe(mt),
            "yoy_wmape_h": mt_prev.get("wmape_h"),
            "yoy_delta_pp": delta,
            "fill_cx": round(_sdiv(a["qt_fat"], a["qt_ped"], 100) or 0, 1),
            "erro_abs": round(a["err_h"]),
        })
    skus.sort(key=lambda s: s.get("erro_abs") or 0, reverse=True)

    # ── Rankings (só top 10 vão para o contexto do modelo) ────────────────
    com_yoy = [s for s in skus if s.get("yoy_delta_pp") is not None]
    rankings = {
        "melhor_evolucao": sorted(com_yoy, key=lambda s: s["yoy_delta_pp"])[:10],
        "pior_evolucao":   sorted(com_yoy, key=lambda s: -s["yoy_delta_pp"])[:10],
        "maior_erro_abs":  skus[:10],
        "pior_fill":       sorted([s for s in skus if s.get("fill_cx")],
                                  key=lambda s: s["fill_cx"])[:10],
    }

    # ── Escopo declarado ──────────────────────────────────────────────────
    esc = db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE COALESCE(ativo,FALSE))          AS ativos,
               COUNT(*)                                              AS total
        FROM dim_produtos
    """)).fetchone()

    return {
        "escopo": {
            "skus_ativos": esc.ativos,
            "skus_total_base": esc.total,
            "meses_analisados": meses_validos,
            "meses_comparativo_yoy": meses_yoy,
            "base_volume": "qt_pedido (demanda do cliente)" if base == "pedido"
                           else "qtfatura (entregue pela empresa)",
            "unidade": "caixas" if unidade == "cx" else "R$ (PMV do pedido)",
            "fonte_meta_humana": "fato_previsao_humana (Excel) jan/23–mai/26 | fato_ibp_granular.vol_final (M-2) jun/26+",
            "fonte_ia": "fato_ibp_granular.vol_ia (M-2), disponível apenas a partir de jun/26",
            "filtro_cliente": "nenhum — todos os clientes considerados",
            "granularidade_erro": "erro absoluto computado em (sku, mês) e só depois agregado",
        },
        "portfolio":  port,
        "fill_rate":  fill,
        "serie":      serie,
        "categorias": categorias,
        "skus":       skus,          # completo — vai para o PDF
        "rankings":   rankings,      # top 10 — vai para o modelo
    }


# ═══════════════════════════════════════════════════════════════════════════
# CONTEXTO ENXUTO PARA O MODELO (economia de tokens)
# ═══════════════════════════════════════════════════════════════════════════
def _contexto_modelo(ds: Dict[str, Any]) -> str:
    """Reduz o dataset ao mínimo necessário — não envia os 114 SKUs."""
    compacto = {
        "escopo":     ds["escopo"],
        "portfolio":  ds["portfolio"],
        "fill_rate":  ds["fill_rate"],
        "serie":      ds["serie"],
        "categorias": ds["categorias"],
        "rankings": {
            k: [{"sku": s["sku"], "desc": s["descricao"][:40], "cat": s["categoria"],
                 "wmape": s.get("wmape_h"), "bias": s.get("bias_h"),
                 "yoy_delta": s.get("yoy_delta_pp"), "fill": s.get("fill_cx"),
                 "vol": s.get("volume"), "classe": s.get("classe"),
                 "persist": s.get("persistencia_pct")}
                for s in v]
            for k, v in ds["rankings"].items()
        },
        "totais": {
            "skus_analisados": len(ds["skus"]),
            "categorias_analisadas": len(ds["categorias"]),
        },
    }
    return json.dumps(compacto, ensure_ascii=False, separators=(",", ":"))


METODOLOGIA = """
FÓRMULAS (granularidade base do erro = (sku, mês); agregação só depois):

WMAPE = Σ|previsto_sku,mês − real_sku,mês| / Σ real_sku,mês × 100
  • Ponderado por volume: SKUs grandes dominam o indicador.
  • WMAPE de categoria NÃO é média dos WMAPEs dos SKUs.
  • O erro absoluto impede cancelamento entre SKUs/meses de sinais opostos.

BIAS = (Σ previsto − Σ real) / Σ real × 100
  • Usa erro COM SINAL: cancelamento é intencional (mede viés direcional).
  • BIAS baixo em categoria pode esconder SKUs individuais muito enviesados.

FVA = WMAPE_Humano − WMAPE_IA
  • Positivo: o humano piorou em relação à IA. Negativo: o humano agregou valor.

FILL RATE cx = Σ qtfatura / Σ qt_pedido × 100  → capacidade de entrega
FILL RATE R$ = Σ vlfatura / Σ vl_pedido × 100  → valor efetivamente faturado
GAP DE DESCONTO (pp) = Fill R$ − Fill cx
  • Negativo indica desconto no faturamento ALÉM do corte de volume
    (política comercial/financeira, não falha de previsão).

PERSISTÊNCIA = % de meses em que o erro ocorreu na mesma direção do BIAS médio.
  ≥70% indica padrão sistemático, não acaso.

CRUZAMENTO WMAPE × BIAS (diagnóstico acionável):
  WMAPE alto + BIAS ~0    → Errático: volatilidade/sazonalidade não capturada.
  WMAPE alto + BIAS alto+ → Superestimação sistemática: gera estoque parado.
  WMAPE alto + BIAS alto− → Subestimação sistemática: gera ruptura/perda de venda.
  WMAPE baixo + BIAS ~0   → Previsão saudável.

CRUZAMENTO CORTE × BIAS (separa culpa):
  Corte alto + BIAS negativo → falha de PLANEJAMENTO (subestimou, não produziu).
  Corte alto + BIAS ~0/+     → falha de OPERAÇÃO (previu certo, não entregou).
"""

SYSTEM_PROMPT = f"""Você é analista sênior de S&OP da Linea Alimentos, escrevendo para C-Level e gerentes.

O público é CÉTICO e técnico. Regras absolutas:
• Todo número citado deve vir do JSON fornecido. NUNCA invente ou estime.
• Se um dado não existe no JSON, diga explicitamente que não está disponível.
• Declare limitações de amostra (ex.: IA só tem dados a partir de jun/26).
• Seja acionável: cada achado deve ter uma implicação operacional clara.
• Português do Brasil, tom executivo, direto, sem jargão vazio.

{METODOLOGIA}

Estrutura do relatório (one-page, ~450 palavras):
1. ESCOPO E METODOLOGIA (3-4 linhas: o que foi medido, com que dados, quais limites)
2. PANORAMA (WMAPE/BIAS do portfólio, evolução YoY, classificação)
3. ONDE ERRAMOS MAIS (categorias e SKUs críticos, com o cruzamento WMAPE×BIAS)
4. EVOLUÇÃO (o que melhorou e o que piorou vs ano anterior)
5. ENTREGA E VALOR (fill rate, gap de desconto, cruzamento corte×BIAS)
6. AÇÕES RECOMENDADAS (3-5 itens objetivos, cada um ligado a um dado citado)

Não use tabelas markdown — o PDF já traz as tabelas. Use prosa densa e parágrafos curtos."""


# ═══════════════════════════════════════════════════════════════════════════
# CHAMADA À API ANTHROPIC
# ═══════════════════════════════════════════════════════════════════════════
def _chamar_claude(system: str, mensagens: List[Dict[str, str]],
                   max_tokens: int = 2000) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY não configurada. Adicione a chave no .env do servidor."
        )
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "Biblioteca 'anthropic' não instalada. Adicione 'anthropic' ao requirements.txt."
        )

    client = anthropic.Anthropic(api_key=api_key)

    # Modelo configuravel via env; fallback para variantes conhecidas.
    modelos = [
        os.environ.get("ANTHROPIC_MODEL", "").strip(),
        "claude-sonnet-4-5",
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-latest",
    ]
    erro_final = None
    for modelo in [m for m in modelos if m]:
        try:
            resp = client.messages.create(
                model=modelo,
                max_tokens=max_tokens,
                system=system,
                messages=mensagens,
            )
            return "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text")
        except Exception as e:
            erro_final = e
            # Se nao for erro de modelo inexistente, aborta o fallback
            if "model" not in str(e).lower():
                raise
    raise RuntimeError(f"Nenhum modelo disponivel. Ultimo erro: {erro_final}")


def gerar_relatorio(db: Session, meses: List[str],
                    base: str = "pedido", unidade: str = "cx") -> Dict[str, Any]:
    """Gera o relatório analítico completo."""
    ds = montar_dataset(db, meses, base, unidade)
    if ds.get("erro"):
        return ds

    ctx = _contexto_modelo(ds)
    texto = _chamar_claude(
        SYSTEM_PROMPT,
        [{"role": "user",
          "content": f"Analise os dados de KPI S&OP abaixo e produza o relatório executivo.\n\n{ctx}"}],
        max_tokens=2000,
    )
    return {"relatorio": texto, "dataset": ds,
            "gerado_em": datetime.datetime.utcnow().isoformat()}


def responder_pergunta(db: Session, pergunta: str, meses: List[str],
                       base: str = "pedido", unidade: str = "cx",
                       historico: Optional[List[Dict[str, str]]] = None) -> str:
    """Chat: responde perguntas ad-hoc sobre os indicadores."""
    ds  = montar_dataset(db, meses, base, unidade)
    if ds.get("erro"):
        return ds["erro"]

    # No chat o modelo pode consultar SKUs específicos — envia lista resumida
    skus_resumo = [
        {"sku": s["sku"], "desc": s["descricao"][:45], "cat": s["categoria"],
         "wmape": s.get("wmape_h"), "bias": s.get("bias_h"),
         "vol": s.get("volume"), "fill": s.get("fill_cx"),
         "yoy": s.get("yoy_delta_pp"), "classe": s.get("classe")}
        for s in ds["skus"]
    ]
    ctx = json.dumps({
        "escopo":     ds["escopo"],
        "portfolio":  ds["portfolio"],
        "fill_rate":  ds["fill_rate"],
        "serie":      ds["serie"],
        "categorias": ds["categorias"],
        "skus":       skus_resumo,
    }, ensure_ascii=False, separators=(",", ":"))

    system = f"""Você é analista de S&OP da Linea Alimentos respondendo perguntas sobre os KPIs.

{METODOLOGIA}

Regras:
• Responda APENAS com base no JSON fornecido. Nunca invente números.
• Se o dado não estiver no JSON, diga que não está disponível no recorte atual.
• Seja conciso e direto. Cite os números exatos que fundamentam a resposta.
• Português do Brasil.

Dados do recorte atual:
{ctx}"""

    msgs = list(historico or [])
    msgs.append({"role": "user", "content": pergunta})
    return _chamar_claude(system, msgs, max_tokens=1200)


# ═══════════════════════════════════════════════════════════════════════════
# GERAÇÃO DO PDF
# ═══════════════════════════════════════════════════════════════════════════
def gerar_pdf(relatorio: str, ds: Dict[str, Any]) -> bytes:
    """
    PDF: página 1 = análise do agente + KPIs principais.
         páginas seguintes = tabela completa de categorias e dos 114 SKUs.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.6*cm, rightMargin=1.6*cm,
                            topMargin=1.4*cm, bottomMargin=1.4*cm)
    ss = getSampleStyleSheet()
    st_titulo = ParagraphStyle("t", parent=ss["Title"], fontSize=16,
                               textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    st_sub    = ParagraphStyle("s", parent=ss["Normal"], fontSize=8.5,
                               textColor=colors.HexColor("#64748b"), spaceAfter=10)
    st_h2     = ParagraphStyle("h", parent=ss["Heading2"], fontSize=10.5,
                               textColor=colors.HexColor("#2563eb"),
                               spaceBefore=9, spaceAfter=4)
    st_txt    = ParagraphStyle("p", parent=ss["Normal"], fontSize=8.6,
                               leading=12.4, alignment=TA_JUSTIFY, spaceAfter=5)
    st_nota   = ParagraphStyle("n", parent=ss["Normal"], fontSize=6.8,
                               textColor=colors.HexColor("#94a3b8"), leading=9)

    esc  = ds["escopo"]
    port = ds["portfolio"]
    fill = ds["fill_rate"]
    hoje = _hoje_br().strftime("%d/%m/%Y")
    per  = f"{esc['meses_analisados'][0]} a {esc['meses_analisados'][-1]}" \
           if esc["meses_analisados"] else "—"

    el = []
    el.append(Paragraph("Relatório de Acurácia S&OP", st_titulo))
    el.append(Paragraph(
        f"Linea Alimentos · Período {per} · Base: {esc['base_volume']} · "
        f"Unidade: {esc['unidade']} · Emitido em {hoje}", st_sub))

    # ── Faixa de KPIs ─────────────────────────────────────────────────────
    def _f(v, suf="%"):
        return "—" if v is None else f"{v:.1f}{suf}".replace(".", ",")

    kpis = [[
        "WMAPE Humano", "BIAS Humano", "WMAPE IA", "FVA",
        "Fill Rate cx", "Fill Rate R$", "Gap desconto",
    ], [
        _f(port.get("wmape_h")), _f(port.get("bias_h")),
        _f(port.get("wmape_ia")), _f(port.get("fva_pp"), " pp"),
        _f(fill.get("fill_rate_cx_pct")), _f(fill.get("fill_rate_rs_pct")),
        _f(fill.get("gap_desconto_pp"), " pp"),
    ]]
    t = Table(kpis, colWidths=[2.5*cm]*7)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.HexColor("#64748b")),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,0), 6.5),
        ("FONTNAME",   (0,1), (-1,1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,1), (-1,1), 12),
        ("TEXTCOLOR",  (0,1), (-1,1), colors.HexColor("#0f172a")),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#e2e8f0")),
    ]))
    el.append(t)
    el.append(Spacer(1, 9))

    # ── Análise do agente ─────────────────────────────────────────────────
    for bloco in relatorio.split("\n"):
        b = bloco.strip()
        if not b:
            continue
        b = b.replace("**", "")
        # Títulos de seção numerados ou em caixa alta curta
        if (b[:2].rstrip(".").isdigit() and len(b) < 70) or (b.isupper() and len(b) < 70):
            el.append(Paragraph(b.lstrip("#").strip(), st_h2))
        else:
            el.append(Paragraph(b.lstrip("#-• ").strip(), st_txt))

    el.append(Spacer(1, 6))
    el.append(Paragraph(
        f"<b>Escopo auditável:</b> {esc['skus_ativos']} SKUs ativos de {esc['skus_total_base']} na base · "
        f"{esc['filtro_cliente']} · {esc['granularidade_erro']}.<br/>"
        f"<b>Meta humana:</b> {esc['fonte_meta_humana']}.<br/>"
        f"<b>IA:</b> {esc['fonte_ia']}.<br/>"
        f"<b>Comparativo YoY:</b> {', '.join(esc['meses_comparativo_yoy'])}.", st_nota))

    # ── Anexo I — Categorias ──────────────────────────────────────────────
    el.append(PageBreak())
    el.append(Paragraph("Anexo I — Todas as categorias", st_h2))
    cab = ["Categoria","Vol.","WMAPE","BIAS","WMAPE IA","FVA","YoY Δpp","Fill cx","Classe"]
    dados = [cab]
    for c in ds["categorias"]:
        dados.append([
            c["categoria"][:22],
            f"{c.get('volume') or 0:,}".replace(",", "."),
            _f(c.get("wmape_h")), _f(c.get("bias_h")),
            _f(c.get("wmape_ia")), _f(c.get("fva_pp"), ""),
            _f(c.get("yoy_delta_pp"), ""), _f(c.get("fill_cx")),
            c.get("classe","—"),
        ])
    tc = Table(dados, colWidths=[3.4*cm,2*cm,1.7*cm,1.7*cm,1.8*cm,1.4*cm,1.6*cm,1.6*cm,2.2*cm],
               repeatRows=1)
    tc.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 6.6),
        ("ALIGN",      (1,0), (-1,-1), "RIGHT"),
        ("ALIGN",      (0,0), (0,-1),  "LEFT"),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    el.append(tc)

    # ── Anexo II — SKUs ───────────────────────────────────────────────────
    el.append(PageBreak())
    el.append(Paragraph(
        f"Anexo II — Todos os {len(ds['skus'])} SKUs (ordenado por erro absoluto)", st_h2))
    cab2 = ["SKU","Descrição","Categoria","Vol.","WMAPE","BIAS","YoY Δpp","Fill cx","Persist.","Classe"]
    dados2 = [cab2]
    for s in ds["skus"]:
        dados2.append([
            s["sku"],
            (s["descricao"] or "")[:30],
            (s["categoria"] or "")[:14],
            f"{s.get('volume') or 0:,}".replace(",", "."),
            _f(s.get("wmape_h")), _f(s.get("bias_h")),
            _f(s.get("yoy_delta_pp"), ""), _f(s.get("fill_cx")),
            _f(s.get("persistencia_pct"), ""),
            s.get("classe","—")[:13],
        ])
    ts = Table(dados2,
               colWidths=[1.7*cm,4.6*cm,2.1*cm,1.5*cm,1.4*cm,1.4*cm,1.3*cm,1.3*cm,1.2*cm,1.9*cm],
               repeatRows=1)
    ts.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 5.7),
        ("ALIGN",      (3,0), (-1,-1), "RIGHT"),
        ("ALIGN",      (0,0), (2,-1),  "LEFT"),
        ("GRID",       (0,0), (-1,-1), 0.25, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))
    el.append(ts)

    el.append(Spacer(1, 8))
    el.append(Paragraph(
        "Fórmulas: WMAPE = Σ|previsto−real|/Σreal · BIAS = (Σprevisto−Σreal)/Σreal · "
        "FVA = WMAPE_Humano − WMAPE_IA · Fill Rate cx = Σqtfatura/Σqt_pedido · "
        "Gap desconto = Fill R$ − Fill cx. "
        "Erro absoluto sempre computado em (sku, mês) antes de agregar. "
        "Fonte: fato_vendas, fato_ibp_granular, fato_previsao_humana, dim_produtos.", st_nota))

    doc.build(el)
    buf.seek(0)
    return buf.read()