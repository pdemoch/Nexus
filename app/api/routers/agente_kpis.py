"""
agente_kpis.py — Agente analítico de KPIs S&OP (Nexus / Linea Alimentos)

Escopo da análise:
  • Acurácia da previsão de demanda: WMAPE e BIAS, em caixas ou em R$.
  • Evolução ano a ano — cada ano avaliado isoladamente, mesmos meses do calendário.
  • Diagnóstico por categoria e por SKU, com cruzamento WMAPE x BIAS.
  • Atendimento (fill rate) apenas para separar erro de previsão de restrição
    de suprimento. Descontos comerciais estão fora do escopo.

Nomenclatura de negócio (o relatório NAO cita nomes de tabelas):
  "planejamento anterior ao Nexus"  -> previsao registrada em planilha (jan/23-mai/26)
  "planejamento no Nexus"           -> plano congelado M-2 do ciclo (jun/26+)
  "previsao estatistica"            -> modelo do Nexus, M-2 (jun/26+)

Principios:
  • Tom tecnico e imparcial, sem adjetivos de julgamento.
  • Todo numero rastreavel aos anexos.
  • Erro absoluto sempre computado em (SKU, mes) antes de agregar.
"""

import os
import io
import json
import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
from dateutil.relativedelta import relativedelta


CICLO_PISO      = datetime.date(2026, 4, 1)
DEFASAGEM_MESES = 2
PISO_NEXUS      = datetime.date(2026, 6, 1)



# ═══════════════════════════════════════════════════════════════════════════
# CONTEXTO DE NEGOCIO — conhecimento que nao esta nas tabelas
# Manter atualizado. O agente usa isso para nao atribuir a falha de previsao
# aquilo que foi decisao ou restricao conhecida.
# ═══════════════════════════════════════════════════════════════════════════

# Restricoes de producao: periodos em que a entrega foi limitada por decisao
# ou capacidade, nao por erro de plano. Formato:
#   {"escopo": "categoria"|"segmento"|"sku", "valor": "...",
#    "ini": "YYYY-MM", "fim": "YYYY-MM", "motivo": "..."}
RESTRICOES_PRODUCAO: List[Dict[str, Any]] = []   # desativado

# Limites de maturidade do SKU, em meses de historico de venda
MESES_LANCAMENTO = 6    # ate 6 meses: lancamento
MESES_RECENTE    = 18   # 7 a 18 meses: recente


def _maturidade(meses_hist: int) -> str:
    if meses_hist is None:
        return "Indefinido"
    if meses_hist <= MESES_LANCAMENTO:
        return "Lancamento"
    if meses_hist <= MESES_RECENTE:
        return "Recente"
    return "Maduro"


def _restricoes_aplicaveis(categoria: str, segmento: str, sku: str,
                           meses: List[str]) -> List[Dict[str, Any]]:
    """Retorna as restricoes de producao que incidem sobre o item no periodo."""
    achadas = []
    for r in RESTRICOES_PRODUCAO:
        alvo = {"categoria": categoria, "segmento": segmento, "sku": sku}.get(r["escopo"])
        if not alvo or str(alvo).upper() != str(r["valor"]).upper():
            continue
        sobrepoe = [m for m in meses if r["ini"] <= m <= r["fim"]]
        if sobrepoe:
            achadas.append({
                "motivo": r["motivo"],
                "periodo": f"{r['ini']} a {r['fim']}",
                "meses_afetados_no_recorte": len(sobrepoe),
            })
    return achadas


def _sdiv(num, den, mult=1.0):
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


def _classe(wmape, bias) -> str:
    if wmape is None or bias is None:
        return "Sem plano"
    if wmape <= 20 and abs(bias) <= 10:
        return "Sob controle"
    if bias > 10:
        return "Superestimacao"
    if bias < -10:
        return "Subestimacao"
    return "Disperso"


_SQL = """
    WITH ativos AS (
        SELECT sku, descricao,
               COALESCE(categoria,'SEM CATEGORIA') AS categoria,
               COALESCE(segmento, 'SEM SEGMENTO')  AS segmento,
               COALESCE(curva, 'SEM CURVA')        AS curva
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
    pmv_mes AS (
        SELECT TRIM(v.sku::text) AS sku,
               DATE_TRUNC('month', v.data_pedido)::date AS mes,
               SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0) AS pmv
        FROM fato_vendas v
        JOIN ativos a ON a.sku = TRIM(v.sku::text)
        GROUP BY 1, 2
        HAVING SUM(v.qt_pedido) > 0
    ),
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
               SUM(v.{col_vol}) AS vol_real,
               SUM(v.qt_pedido) AS qt_pedido,
               SUM(v.qtfatura)  AS qt_fatura,
               SUM(v.qtcorte)   AS qt_corte
        FROM fato_vendas v
        JOIN ativos a ON a.sku = TRIM(v.sku::text)
        WHERE v.data_pedido >= :ini
          AND v.data_pedido < (CAST(:fim AS date) + INTERVAL '1 month')
        GROUP BY 1, 2
    ),
    plano_planilha AS (
        SELECT h.sku, h.mes_projetado::date AS mes, SUM(h.vol_humano) AS vol_plano
        FROM fato_previsao_humana h
        JOIN ativos a ON a.sku = h.sku
        WHERE h.fonte = 'HISTORICO'
          AND h.mes_projetado >= :ini AND h.mes_projetado <= :fim
        GROUP BY 1, 2
    ),
    plano_nexus AS (
        SELECT TRIM(i.sku::text) AS sku, i.mes_projetado::date AS mes,
               SUM(i.vol_final) AS vol_plano
        FROM fato_ibp_granular i
        JOIN ativos a ON a.sku = TRIM(i.sku::text)
        WHERE (EXTRACT(YEAR FROM i.mes_projetado)*12 + EXTRACT(MONTH FROM i.mes_projetado))
            - (SPLIT_PART(i.ciclo_sop,'/',2)::int*12 + SPLIT_PART(i.ciclo_sop,'/',1)::int) = 2
          AND i.mes_projetado >= '2026-06-01'
          AND i.mes_projetado >= :ini AND i.mes_projetado <= :fim
        GROUP BY 1, 2
    ),
    plano AS (
        SELECT * FROM plano_planilha UNION ALL SELECT * FROM plano_nexus
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
    SELECT a.sku, a.descricao, a.categoria, a.segmento, a.curva,
           pv.nasceu,
           TO_CHAR(r.mes,'YYYY-MM') AS mes,
           EXTRACT(YEAR  FROM r.mes)::int AS ano,
           EXTRACT(MONTH FROM r.mes)::int AS num_mes,
           r.vol_real, r.qt_pedido, r.qt_fatura, r.qt_corte,
           p.vol_plano, i.vol_ia,
           COALESCE(pm.pmv, pg.pmv, 0) AS pmv
    FROM realizado r
    JOIN ativos a           ON a.sku = r.sku
    JOIN primeira_venda pv  ON pv.sku = r.sku AND r.mes >= pv.nasceu
    LEFT JOIN plano p       ON p.sku = r.sku AND p.mes = r.mes
    LEFT JOIN ia i          ON i.sku = r.sku AND i.mes = r.mes
    LEFT JOIN pmv_mes pm    ON pm.sku = r.sku AND pm.mes = r.mes
    LEFT JOIN pmv_global pg ON pg.sku = r.sku
    WHERE r.vol_real > 0
    ORDER BY a.categoria, a.sku, r.mes
"""


def montar_dataset(db: Session, meses: List[str],
                   base: str = "pedido", unidade: str = "cx") -> Dict[str, Any]:
    teto = _ultimo_mes_fechado()
    meses_validos = sorted({m for m in meses if m <= teto})
    if not meses_validos:
        return {"erro": "Nenhum mes fechado no periodo selecionado."}

    ini = meses_validos[0]  + "-01"
    fim = meses_validos[-1] + "-01"

    ano_fim  = int(meses_validos[-1][:4])
    ini_hist = f"{ano_fim - 3}-01-01"

    col_vol = "qtfatura" if base == "faturado" else "qt_pedido"
    sql     = text(_SQL.replace("{col_vol}", col_vol))

    rows      = db.execute(sql, {"ini": ini,      "fim": fim}).fetchall()
    rows_hist = db.execute(sql, {"ini": ini_hist, "fim": fim}).fetchall()

    def _peso(r):
        return float(r.pmv or 0) if unidade == "rs" else 1.0

    def _agregar(registros, chave_fn, filtro_mes=None):
        acc: Dict[Any, Dict[str, float]] = {}
        for r in registros:
            if filtro_mes and r.mes not in filtro_mes:
                continue
            k = chave_fn(r)
            if k is None:
                continue
            a = acc.setdefault(k, {
                "real": 0.0, "plano": 0.0, "ia": 0.0,
                "err": 0.0, "err_ia": 0.0,
                "real_p": 0.0, "real_ia": 0.0,
                "qt_ped": 0.0, "qt_fat": 0.0, "qt_cor": 0.0,
                "n_meses": 0, "n_over": 0, "n_under": 0,
                "meses_hist": set(),
            })
            p    = _peso(r)
            real = float(r.vol_real or 0) * p
            a["real"]   += real
            a["qt_ped"] += float(r.qt_pedido or 0)
            a["qt_fat"] += float(r.qt_fatura or 0)
            a["qt_cor"] += float(r.qt_corte or 0)
            a["meses_hist"].add(r.mes)

            if r.vol_plano is not None:
                pl = float(r.vol_plano) * p
                a["plano"]   += pl
                a["err"]     += abs(pl - real)
                a["real_p"]  += real
                a["n_meses"] += 1
                if real > 0:
                    d = (pl - real) / real
                    if d >  0.02: a["n_over"]  += 1
                    if d < -0.02: a["n_under"] += 1

            if r.vol_ia is not None:
                iv = float(r.vol_ia) * p
                a["ia"]      += iv
                a["err_ia"]  += abs(iv - real)
                a["real_ia"] += real
        return acc

    def _metricas(a: Dict[str, float]) -> Dict[str, Any]:
        wm    = _sdiv(a["err"],    a["real_p"],  100)
        bi    = _sdiv(a["plano"] - a["real_p"], a["real_p"], 100)
        wm_ia = _sdiv(a["err_ia"], a["real_ia"], 100)
        bi_ia = _sdiv(a["ia"] - a["real_ia"], a["real_ia"], 100)
        fva   = round(wm - wm_ia, 1) if (wm is not None and wm_ia is not None) else None
        n     = a["n_meses"]
        pers  = None
        if n > 0 and bi is not None:
            pers = round((a["n_over"] if bi >= 0 else a["n_under"]) / n * 100)
        return {
            "wmape":    round(wm, 1)    if wm    is not None else None,
            "bias":     round(bi, 1)    if bi    is not None else None,
            "wmape_ia": round(wm_ia, 1) if wm_ia is not None else None,
            "bias_ia":  round(bi_ia, 1) if bi_ia is not None else None,
            "fva_pp":   fva,
            "volume":   round(a["real"]),
            "erro_abs": round(a["err"]),
            "persistencia_pct": pers,
            "meses_com_plano":  n,
            "atendimento_pct": round(_sdiv(a["qt_fat"], a["qt_ped"], 100) or 0, 1),
            "corte_cx": round(a["qt_cor"]),
        }

    # Portfolio no periodo
    ag_port = _agregar(rows, lambda r: "T", meses_validos)
    port = _metricas(ag_port["T"]) if "T" in ag_port else {}
    port["classe"] = _classe(port.get("wmape"), port.get("bias"))

    # Evolucao ano a ano: mesmos meses do calendario em cada ano
    meses_num = sorted({int(m[5:7]) for m in meses_validos})
    ag_ano = _agregar(rows_hist,
                      lambda r: r.ano if r.num_mes in meses_num else None)
    evolucao_anual = []
    anos_ord = sorted(ag_ano.keys())
    for idx, ano in enumerate(anos_ord):
        mt = _metricas(ag_ano[ano])
        delta = None
        if idx > 0:
            ant = _metricas(ag_ano[anos_ord[idx-1]])
            if mt.get("wmape") is not None and ant.get("wmape") is not None:
                delta = round(mt["wmape"] - ant["wmape"], 1)
        evolucao_anual.append({
            "ano": ano, **mt,
            "delta_wmape_pp": delta,
            "classe": _classe(mt.get("wmape"), mt.get("bias")),
        })

    # Meses de historico total de cada SKU (base para maturidade)
    hist_sku: Dict[str, set] = {}
    nasc_sku: Dict[str, str] = {}
    for r in rows_hist:
        hist_sku.setdefault(r.sku, set()).add(r.mes)
        if r.nasceu is not None:
            d = r.nasceu.strftime("%Y-%m")
            if r.sku not in nasc_sku or d < nasc_sku[r.sku]:
                nasc_sku[r.sku] = d

    # Serie mensal
    ag_mes = _agregar(rows, lambda r: r.mes, meses_validos)
    serie = []
    for m in meses_validos:
        if m not in ag_mes:
            continue
        mt = _metricas(ag_mes[m])
        serie.append({"mes": m, "wmape": mt["wmape"], "bias": mt["bias"],
                      "wmape_ia": mt["wmape_ia"], "volume": mt["volume"],
                      "atendimento": mt["atendimento_pct"], "corte": mt["corte_cx"]})

    # Categorias com comparativo do ano anterior
    ano_atual  = ano_fim
    ag_cat     = _agregar(rows, lambda r: r.categoria, meses_validos)
    ag_cat_ant = _agregar(
        rows_hist,
        lambda r: r.categoria
        if (r.ano == ano_atual - 1 and r.num_mes in meses_num) else None)
    # Restricoes que incidem sobre cada categoria — via seus segmentos
    segs_por_cat: Dict[str, set] = {}
    for r in rows:
        segs_por_cat.setdefault(r.categoria, set()).add(r.segmento)
    restr_cat: Dict[str, List[Dict[str, Any]]] = {}
    for cat, segs in segs_por_cat.items():
        achadas = []
        for sg in segs:
            for x in _restricoes_aplicaveis(cat, sg, None, meses_validos):
                x = dict(x); x["segmento"] = sg
                achadas.append(x)
        if achadas:
            restr_cat[cat] = achadas

    categorias = []
    for cat, a in ag_cat.items():
        mt  = _metricas(a)
        ant = _metricas(ag_cat_ant[cat]) if cat in ag_cat_ant else {}
        delta = None
        if mt.get("wmape") is not None and ant.get("wmape") is not None:
            delta = round(mt["wmape"] - ant["wmape"], 1)
        categorias.append({
            "categoria": cat, **mt,
            "classe": _classe(mt.get("wmape"), mt.get("bias")),
            "wmape_ano_anterior": ant.get("wmape"),
            "delta_wmape_pp": delta,
            "restricao_producao": restr_cat.get(cat) or None,
        })
    categorias.sort(key=lambda c: c.get("erro_abs") or 0, reverse=True)

    # SKUs
    ag_sku     = _agregar(rows, lambda r: (r.sku, r.descricao, r.categoria, r.segmento),
                          meses_validos)
    ag_sku_ant = _agregar(
        rows_hist,
        lambda r: (r.sku, r.descricao, r.categoria, r.segmento)
        if (r.ano == ano_atual - 1 and r.num_mes in meses_num) else None)

    skus = []
    for k, a in ag_sku.items():
        sk, desc, cat, seg = k
        mt  = _metricas(a)
        ant = _metricas(ag_sku_ant[k]) if k in ag_sku_ant else {}
        delta = None
        if mt.get("wmape") is not None and ant.get("wmape") is not None:
            delta = round(mt["wmape"] - ant["wmape"], 1)

        n_hist = len(hist_sku.get(sk, set()))
        restr  = _restricoes_aplicaveis(cat, seg, sk, meses_validos)

        skus.append({
            "sku": sk, "descricao": desc, "categoria": cat, "segmento": seg, **mt,
            "classe": _classe(mt.get("wmape"), mt.get("bias")),
            "wmape_ano_anterior": ant.get("wmape"),
            "delta_wmape_pp": delta,
            "primeira_venda": nasc_sku.get(sk),
            "meses_historico": n_hist,
            "maturidade": _maturidade(n_hist),
            "restricao_producao": restr or None,
        })
    skus.sort(key=lambda s: s.get("erro_abs") or 0, reverse=True)

    # Itens de lancamento e recentes — nao comparaveis a itens maduros
    lancamentos = [s for s in skus if s["maturidade"] in ("Lancamento", "Recente")]
    lancamentos.sort(key=lambda s: (s["meses_historico"], -(s.get("volume") or 0)))

    # Itens sob restricao de producao no periodo
    sob_restricao = [s for s in skus if s.get("restricao_producao")]
    sob_restricao.sort(key=lambda s: -(s.get("corte_cx") or 0))

    # ── DETALHE MENSAL — permite responder sobre um mes especifico ────────
    # Sem isto o agente so enxerga o agregado do periodo e responde de forma
    # generica quando perguntado sobre um mes isolado.
    ag_cat_mes = _agregar(rows, lambda r: (r.mes, r.categoria), meses_validos)
    ag_sku_mes = _agregar(
        rows, lambda r: (r.mes, r.sku, r.descricao, r.categoria), meses_validos)

    detalhe_mensal = []
    for m in meses_validos:
        cats = []
        for (mm, cat), a in ag_cat_mes.items():
            if mm != m:
                continue
            mt = _metricas(a)
            cats.append({
                "categoria": cat, "wmape": mt["wmape"], "bias": mt["bias"],
                "volume": mt["volume"], "erro_abs": mt["erro_abs"],
                "atendimento": mt["atendimento_pct"], "corte": mt["corte_cx"],
                "classe": _classe(mt.get("wmape"), mt.get("bias")),
            })
        cats.sort(key=lambda x: -(x["erro_abs"] or 0))

        itens = []
        for (mm, sk, desc, cat), a in ag_sku_mes.items():
            if mm != m:
                continue
            mt = _metricas(a)
            if not mt.get("erro_abs"):
                continue
            itens.append({
                "sku": sk, "descricao": desc, "categoria": cat,
                "wmape": mt["wmape"], "bias": mt["bias"],
                "volume": mt["volume"], "erro_abs": mt["erro_abs"],
                "atendimento": mt["atendimento_pct"], "corte": mt["corte_cx"],
                "maturidade": _maturidade(len(hist_sku.get(sk, set()))),
            })
        itens.sort(key=lambda x: -(x["erro_abs"] or 0))

        mt_mes = _metricas(ag_mes[m]) if m in ag_mes else {}
        detalhe_mensal.append({
            "mes": m,
            "wmape": mt_mes.get("wmape"), "bias": mt_mes.get("bias"),
            "volume": mt_mes.get("volume"), "erro_abs": mt_mes.get("erro_abs"),
            "atendimento": mt_mes.get("atendimento_pct"),
            "corte": mt_mes.get("corte_cx"),
            "categorias": cats,
            "top_skus_erro": itens[:15],
        })

    # Atendimento: leitura de execucao, separada da acuracia
    ag_at = ag_port.get("T", {})
    atendimento = {
        "pedido":       round(ag_at.get("qt_ped", 0)),
        "entregue":     round(ag_at.get("qt_fat", 0)),
        "corte":        round(ag_at.get("qt_cor", 0)),
        "atendimento_pct": round(_sdiv(ag_at.get("qt_fat", 0),
                                       ag_at.get("qt_ped", 0), 100) or 0, 1),
        "serie_mensal": [
            {"mes": s["mes"], "atendimento": s["atendimento"], "corte": s["corte"]}
            for s in serie
        ],
    }

    # Itens com maior corte, separados entre restricao declarada e nao declarada
    com_corte = [s for s in skus if (s.get("corte_cx") or 0) > 0]
    com_corte.sort(key=lambda s: -(s.get("corte_cx") or 0))
    corte_com_restricao = [s for s in com_corte if s.get("restricao_producao")][:10]
    corte_sem_restricao = [s for s in com_corte if not s.get("restricao_producao")][:10]

    com_delta = [s for s in skus if s.get("delta_wmape_pp") is not None]
    rankings = {
        "maior_erro_absoluto": skus[:10],
        "melhor_evolucao":     sorted(com_delta, key=lambda s: s["delta_wmape_pp"])[:10],
        "pior_evolucao":       sorted(com_delta, key=lambda s: -s["delta_wmape_pp"])[:10],
        "vies_sistematico":    sorted(
            [s for s in skus if (s.get("persistencia_pct") or 0) >= 70
                             and s["maturidade"] == "Maduro"],
            key=lambda s: -(s.get("erro_abs") or 0))[:10],
        "maior_corte_sem_restricao": corte_sem_restricao,
        "maior_corte_com_restricao": corte_com_restricao,
    }

    esc = db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE COALESCE(ativo,FALSE)) AS ativos,
               COUNT(*) AS total FROM dim_produtos
    """)).fetchone()

    return {
        "escopo": {
            "skus_ativos": esc.ativos,
            "skus_total_base": esc.total,
            "meses_analisados": meses_validos,
            "meses_do_calendario_comparados": meses_num,
            "anos_na_comparacao": anos_ord,
            "base_volume": ("demanda pedida pelo cliente" if base == "pedido"
                            else "volume efetivamente entregue"),
            "unidade": "caixas" if unidade == "cx" else "R$ (preco medio do pedido)",
            "plano_congelado_em": "M-2 (dois meses antes do mes de venda)",
            "previsao_estatistica_disponivel_desde": "2026-06",
            "granularidade_erro": "erro absoluto computado em (SKU, mes) e agregado depois",
            "regra_maturidade": (f"Lancamento ate {MESES_LANCAMENTO} meses de historico; "
                                 f"Recente ate {MESES_RECENTE}; acima disso, Maduro"),
            "restricoes_de_producao_declaradas": RESTRICOES_PRODUCAO,
        },
        "portfolio":      port,
        "atendimento":    atendimento,
        "evolucao_anual": evolucao_anual,
        "serie":          serie,
        "detalhe_mensal": detalhe_mensal,
        "categorias":     categorias,
        "skus":           skus,
        "lancamentos":    lancamentos,
        "sob_restricao":  sob_restricao,
        "rankings":       rankings,
    }


def _contexto_modelo(ds: Dict[str, Any]) -> str:
    def _s(lst):
        return [{"sku": s["sku"], "desc": s["descricao"][:38], "cat": s["categoria"],
                 "seg": s.get("segmento"),
                 "wmape": s.get("wmape"), "bias": s.get("bias"),
                 "delta": s.get("delta_wmape_pp"),
                 "wmape_ant": s.get("wmape_ano_anterior"),
                 "vol": s.get("volume"), "erro_abs": s.get("erro_abs"),
                 "persist": s.get("persistencia_pct"),
                 "atend": s.get("atendimento_pct"), "corte": s.get("corte_cx"),
                 "maturidade": s.get("maturidade"),
                 "meses_hist": s.get("meses_historico"),
                 "restricao": s.get("restricao_producao"),
                 "classe": s.get("classe")} for s in lst]
    return json.dumps({
        "escopo":         ds["escopo"],
        "portfolio":      ds["portfolio"],
        "atendimento":    ds["atendimento"],
        "evolucao_anual": ds["evolucao_anual"],
        "serie_mensal":   ds["serie"],
        "categorias":     ds["categorias"],
        "rankings":       {k: _s(v) for k, v in ds["rankings"].items()},
        "lancamentos_e_recentes": _s(ds["lancamentos"]),
        "itens_sob_restricao_de_producao": _s(ds["sob_restricao"]),
        "total_skus":     len(ds["skus"]),
    }, ensure_ascii=False, separators=(",", ":"))


METODOLOGIA = """
DEFINICOES

WMAPE = soma dos erros absolutos dividida pela soma do realizado, x100.
  Computado em (SKU, mes) e agregado depois. O WMAPE de uma categoria nao e a
  media dos WMAPEs dos SKUs: itens de maior volume pesam proporcionalmente mais.

BIAS = (soma do previsto menos soma do realizado) dividido pela soma do
  realizado, x100. Positivo indica plano acima do realizado; negativo, abaixo.
  Erros de sinais opostos se cancelam, portanto um BIAS baixo numa categoria
  pode conviver com SKUs individualmente muito enviesados.

PERSISTENCIA = percentual de meses em que o erro ocorreu na mesma direcao do
  BIAS medio. Igual ou acima de 70% caracteriza vies estrutural, nao aleatorio.

FVA = WMAPE do plano menos WMAPE da previsao estatistica.
  Positivo: o ajuste manual aumentou o erro. Negativo: reduziu.

DIAGNOSTICO WMAPE x BIAS
  WMAPE alto e BIAS proximo de zero: erro disperso. Volatilidade ou
    sazonalidade nao capturada pelo metodo de previsao.
  WMAPE alto e BIAS positivo: superestimacao. Excesso de cobertura e
    capital imobilizado em estoque.
  WMAPE alto e BIAS negativo: subestimacao. Risco de ruptura e perda de venda.
  WMAPE baixo e BIAS proximo de zero: previsao sob controle.

ATENDIMENTO = volume entregue dividido pelo volume pedido. Mede execucao, nao
  acuracia. Serve para separar erro de previsao de restricao de suprimento:
  atendimento baixo com BIAS negativo indica plano subdimensionado; atendimento
  baixo com BIAS neutro indica restricao operacional.

MATURIDADE DO ITEM
  Lancamento: ate 6 meses de historico de venda. Nao ha serie suficiente para
    o modelo estatistico aprender padrao. WMAPE alto e esperado e nao indica
    falha de processo. Cobrar acuracia de item nesta faixa e incorreto.
  Recente: 7 a 18 meses. Ja ha serie, mas ainda sem ciclo sazonal completo
    fechado. Comparacao ano a ano pode ser parcial ou inexistente.
  Maduro: acima de 18 meses. Este e o universo em que a acuracia deve ser
    cobrada e onde padroes sistematicos sao acionaveis.

"""

SYSTEM_PROMPT = f"""Voce e especialista em S&OP e escreve o relatorio de acuracia de demanda
para a diretoria e a gerencia da Linea Alimentos.

REGRAS DE REDACAO - obrigatorias:
- Tom tecnico, factual e imparcial. Descreva o que os dados mostram.
- PROIBIDO usar adjetivos de julgamento como desastre, pessimo, absurdo,
  destroi, catastrofico, alarmante. Use linguagem descritiva: "erro de X por
  cento", "vies de X pontos percentuais", "acima do patamar do portfolio",
  "abaixo do observado no ano anterior".
- Nunca cite nomes de tabelas, campos de banco, sistemas ou ferramentas.
  Nao mencione Nexus, planilha, plataforma, migracao, transicao,
  parametrizacao ou curva de aprendizado em nenhuma hipotese.
- NOME DO PRODUTO: use SEMPRE a descricao exata do campo "desc" ou
  "descricao", sem reescrever, abreviar, traduzir ou padronizar. Se a
  descricao e "LINEA ADOC SACARINA LIQ 12X100ml", escreva exatamente assim.
- Todo numero citado deve existir no JSON. Nao estime nem invente.
- Declare limitacoes de amostra quando existirem.
- Nao trate de descontos comerciais nem de politica de preco. O escopo e
  exclusivamente a acuracia da previsao de demanda.
- NUNCA atribua variacao de resultado a mudanca de sistema, plataforma,
  ferramenta, metodologia de registro ou origem do plano. Nao especule sobre
  causas que nao estejam demonstradas nos dados. Quando a causa nao for
  identificavel, declare isso de forma direta e mostre o que os dados revelam:
  qual a direcao do erro, quais categorias e itens concentraram o desvio, e
  qual a contribuicao de cada um no erro absoluto do periodo.
- Nao trate de restricao de producao: esse tema esta fora do escopo.

{METODOLOGIA}

ESTRUTURA (700 a 900 palavras, prosa densa, sem tabelas, sem markdown):

1. ESCOPO E METODO
   Periodo medido, quantidade de SKUs, origem do plano em cada janela temporal
   e limitacao de amostra da previsao estatistica.

2. LEITURA DO PERIODO
   WMAPE e BIAS do portfolio, classificacao pelo cruzamento e persistencia.

3. EVOLUCAO ANO A ANO
   Compare cada ano isoladamente, usando os mesmos meses do calendario.
   Informe se o erro aumentou, diminuiu ou permaneceu estavel, e quanto.
   PROIBIDO atribuir variacao de indicador a mudanca de sistema, ferramenta,
   plataforma, metodologia de registro ou origem do plano. Se um mes ou periodo destoa,
   descreva o que os dados mostram (direcao do erro, categorias que
   concentraram o desvio, itens com maior contribuicao) e, quando a causa nao
   estiver nos dados, diga apenas que a causa nao e identificavel pelo recorte
   disponivel.

4. CONCENTRACAO DO ERRO
   Onde o erro absoluto se concentra, em categorias e SKUs. Distinga o item que
   erra muito em percentual do item que erra muito em volume: sao problemas de
   naturezas diferentes e exigem acoes diferentes.
   Separe explicitamente os itens maduros dos itens de lancamento: so os
   primeiros devem entrar na leitura de acuracia do processo.

5. LANCAMENTOS E ITENS RECENTES
   Liste os itens de lancamento e recentes do periodo, com a descricao completa
   do produto e quantos meses de historico cada um tem. Declare que o erro
   deles nao e comparavel ao dos itens maduros e que nao devem entrar na
   cobranca de acuracia do processo.

6. PADROES SISTEMATICOS
   Itens MADUROS com persistencia igual ou acima de 70%. Explique a implicacao
   operacional de cada direcao de vies. Nao inclua lancamentos aqui.

7. ATENDIMENTO E ORIGEM DO CORTE
   Informe o atendimento do portfolio no periodo e como evoluiu mes a mes.
   Identifique os itens com maior corte e classifique a origem pelo cruzamento
   corte x BIAS:
     - BIAS negativo: o plano subdimensionou e a producao seguiu o plano.
       A origem esta no planejamento de demanda.
     - BIAS neutro ou positivo: o plano estava adequado e a limitacao foi de
       execucao. A origem esta em suprimentos ou producao.

8. PREVISAO ESTATISTICA COMPARADA AO PLANO
   Onde o FVA indica que o ajuste manual aumentou ou reduziu o erro, sempre
   com a ressalva sobre o tamanho da amostra disponivel.

9. RECOMENDACOES
   De quatro a seis itens objetivos. Cada um cita o dado que o sustenta e
   indica a area responsavel: planejamento de demanda, comercial ou suprimentos.

10. CONSIDERACAO FINAL
   Um paragrafo unico de fechamento. Responda diretamente: o processo esta
   melhorando ou piorando, onde esta o maior ganho possivel no proximo ciclo,
   e qual o principal risco se nada for alterado. Sem repetir numeros ja
   citados; sintetize a leitura.

IMPORTANTE: complete todas as 10 secoes. Nao interrompa o texto no meio de uma
frase. Se precisar economizar espaco, encurte as secoes 4 e 5, mas sempre
entregue a secao 10 completa.

Escreva em portugues do Brasil, paragrafos curtos.

FORMATACAO: escreva prosa limpa, sem markdown. Nao use #, ##, **, ---, *, _,
crase nem tabelas em pipe. Os titulos das secoes devem ser escritos em
MAIUSCULAS numa linha isolada, precedidos do numero da secao. Exemplo:

1. ESCOPO E METODO

Nao inclua tabelas no texto: os anexos do documento ja trazem todos os dados
tabulados. Cite os numeros dentro das frases."""


def _chamar_claude(system: str, mensagens: List[Dict[str, str]],
                   max_tokens: int = 8000) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY nao configurada no ambiente do servidor.")
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("Biblioteca 'anthropic' nao instalada.")

    client = anthropic.Anthropic(api_key=api_key)
    modelos = [
        os.environ.get("ANTHROPIC_MODEL", "").strip(),
        "claude-sonnet-4-5",
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-latest",
    ]
    erro = None
    for modelo in [m for m in modelos if m]:
        try:
            resp = client.messages.create(
                model=modelo, max_tokens=max_tokens,
                temperature=0,          # determinismo: mesma entrada, mesma saida
                system=system, messages=mensagens,
            )
            return "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text")
        except Exception as e:
            erro = e
            if "model" not in str(e).lower():
                raise
    raise RuntimeError(f"Nenhum modelo disponivel. Ultimo erro: {erro}")




# ═══════════════════════════════════════════════════════════════════════════
# CACHE — o relatorio de um ciclo e gerado uma vez e vale para todos
# ═══════════════════════════════════════════════════════════════════════════
import hashlib

DDL_CACHE = """
CREATE TABLE IF NOT EXISTS agente_relatorio_cache (
    id             SERIAL PRIMARY KEY,
    ciclo_sop      VARCHAR(10)  NOT NULL,
    chave          VARCHAR(80)  NOT NULL,
    meses          TEXT         NOT NULL,
    base           VARCHAR(20)  NOT NULL,
    unidade        VARCHAR(10)  NOT NULL,
    relatorio      TEXT         NOT NULL,
    gerado_por     VARCHAR(120),
    gerado_em      TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT uix_agente_rel UNIQUE (ciclo_sop, chave)
);
CREATE TABLE IF NOT EXISTS agente_chat_cache (
    id           SERIAL PRIMARY KEY,
    ciclo_sop    VARCHAR(10)  NOT NULL,
    chave        VARCHAR(80)  NOT NULL,
    pergunta     TEXT         NOT NULL,
    resposta     TEXT         NOT NULL,
    criado_em    TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT uix_agente_chat UNIQUE (ciclo_sop, chave)
);
"""


def _garantir_tabelas(db: Session) -> None:
    for stmt in [s.strip() for s in DDL_CACHE.split(";") if s.strip()]:
        db.execute(text(stmt))
    db.commit()


def _normalizar(txt: str) -> str:
    """Normaliza texto para chave de cache: minusculas, sem acento, sem pontuacao."""
    import unicodedata, re
    t = unicodedata.normalize("NFKD", txt or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _chave(*partes) -> str:
    bruto = "|".join(str(p) for p in partes)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:40]


def _ciclo_atual(db: Session) -> str:
    try:
        from app.api.routers.shared_ibp import get_current_cycle
        return get_current_cycle(db)
    except Exception:
        return _hoje_br().strftime("%m/%Y")


def relatorio_existente(db: Session, meses: List[str],
                        base: str, unidade: str) -> Optional[Dict[str, Any]]:
    """Retorna o relatorio ja gerado para este ciclo e recorte, se houver."""
    _garantir_tabelas(db)
    ciclo = _ciclo_atual(db)
    ch    = _chave(sorted(meses), base, unidade)
    r = db.execute(text("""
        SELECT relatorio, gerado_por, gerado_em
        FROM agente_relatorio_cache
        WHERE ciclo_sop = :c AND chave = :k
    """), {"c": ciclo, "k": ch}).fetchone()
    if not r:
        return None
    return {
        "relatorio":  r.relatorio,
        "gerado_por": r.gerado_por,
        "gerado_em":  r.gerado_em.isoformat() if r.gerado_em else None,
        "ciclo":      ciclo,
        "do_cache":   True,
    }


def salvar_relatorio(db: Session, meses: List[str], base: str, unidade: str,
                     texto: str, usuario: str) -> None:
    _garantir_tabelas(db)
    ciclo = _ciclo_atual(db)
    ch    = _chave(sorted(meses), base, unidade)
    db.execute(text("""
        INSERT INTO agente_relatorio_cache
            (ciclo_sop, chave, meses, base, unidade, relatorio, gerado_por)
        VALUES (:c, :k, :m, :b, :u, :r, :p)
        ON CONFLICT (ciclo_sop, chave) DO UPDATE SET
            relatorio  = EXCLUDED.relatorio,
            gerado_por = EXCLUDED.gerado_por,
            gerado_em  = now()
    """), {"c": ciclo, "k": ch, "m": ",".join(sorted(meses)),
           "b": base, "u": unidade, "r": texto, "p": usuario})
    db.commit()


def gerar_relatorio(db: Session, meses: List[str],
                    base: str = "pedido", unidade: str = "cx",
                    usuario: str = "-", forcar: bool = False) -> Dict[str, Any]:
    """
    Gera o relatorio do ciclo. Se ja existe um relatorio para este ciclo e
    recorte, devolve o mesmo texto — garante que todos os usuarios leem
    exatamente a mesma analise durante o mes.
    forcar=True (somente Administrador) refaz a analise e substitui a anterior.
    """
    ds = montar_dataset(db, meses, base, unidade)
    if ds.get("erro"):
        return ds

    if not forcar:
        cache = relatorio_existente(db, meses, base, unidade)
        if cache:
            return {**cache, "dataset": ds}

    texto = _chamar_claude(
        SYSTEM_PROMPT,
        [{"role": "user",
          "content": "Produza o relatorio de acuracia S&OP a partir destes dados:\n\n"
                     + _contexto_modelo(ds)}],
        max_tokens=8000,
    )
    salvar_relatorio(db, meses, base, unidade, texto, usuario)
    return {"relatorio": texto, "dataset": ds,
            "gerado_por": usuario,
            "gerado_em": datetime.datetime.utcnow().isoformat(),
            "do_cache": False}


def responder_pergunta(db: Session, pergunta: str, meses: List[str],
                       base: str = "pedido", unidade: str = "cx",
                       historico: Optional[List[Dict[str, str]]] = None) -> str:
    ds = montar_dataset(db, meses, base, unidade)
    if ds.get("erro"):
        return ds["erro"]

    skus = [{"sku": s["sku"], "desc": s["descricao"][:45], "cat": s["categoria"],
             "seg": s.get("segmento"),
             "wmape": s.get("wmape"), "bias": s.get("bias"),
             "erro_abs": s.get("erro_abs"), "vol": s.get("volume"),
             "delta": s.get("delta_wmape_pp"), "wmape_ant": s.get("wmape_ano_anterior"),
             "persist": s.get("persistencia_pct"),
             "atend": s.get("atendimento_pct"), "corte": s.get("corte_cx"),
             "maturidade": s.get("maturidade"), "meses_hist": s.get("meses_historico"),
             "restricao": s.get("restricao_producao"),
             "classe": s.get("classe")} for s in ds["skus"]]

    ctx = json.dumps({
        "escopo": ds["escopo"], "portfolio": ds["portfolio"],
        "atendimento": ds["atendimento"],
        "evolucao_anual": ds["evolucao_anual"], "serie_mensal": ds["serie"],
        "detalhe_por_mes": ds["detalhe_mensal"],
        "categorias": ds["categorias"], "skus": skus,
    }, ensure_ascii=False, separators=(",", ":"))

    system = f"""Voce e especialista em S&OP da Linea Alimentos respondendo perguntas
sobre a acuracia da previsao de demanda.

{METODOLOGIA}

Regras:
- Responda apenas com base no JSON. Nunca invente numeros.
- Se o dado nao estiver no recorte, diga isso claramente.
- Tom tecnico e imparcial, sem adjetivos de julgamento.
- Nao cite nomes de tabelas nem de sistemas. Nao mencione Nexus, planilha,
  plataforma, ferramenta, migracao, transicao, parametrizacao ou curva de
  aprendizado. PROIBIDO atribuir qualquer variacao de resultado a mudanca de
  sistema ou de metodologia de registro. Se a causa nao estiver nos dados,
  escreva apenas que nao e identificavel pelo recorte e mostre o que os dados
  revelam: direcao do erro, categorias e itens que concentraram o desvio.
- Nao trate de descontos comerciais nem de restricao de producao.
- NOME DO PRODUTO: use SEMPRE a descricao exata do campo "desc", sem
  reescrever, abreviar, traduzir ou padronizar. Se a descricao e
  "LINEA ADOC SACARINA LIQ 12X100ml", escreva exatamente assim. Nunca converta
  para "Sacarina Liquido 12x100ml" ou variacoes.
- ESCOPO TEMPORAL DA PERGUNTA: se a pergunta menciona um mes especifico, use
  o bloco "detalhe_por_mes" daquele mes — ele traz as categorias e os itens
  com maior erro NAQUELE mes. NAO responda com o agregado do periodo inteiro
  quando a pergunta e sobre um mes. Se a pergunta nao especifica mes, use o
  agregado do periodo.
- Seja conciso e cite os numeros que fundamentam a resposta.

FORMATO DA RESPOSTA - siga exatamente:
- Comece com uma frase de resposta direta a pergunta, sem titulo antes dela.
- Use no maximo dois niveis de titulo, sempre com "## " (dois cerquilhas e um
  espaco). Nunca use "#" sozinho nem "###".
- Use **negrito** apenas para numeros-chave e nomes de item. Nunca para frases
  inteiras.
- Tabelas: use pipe simples, com cabecalho e linha separadora. Maximo de 6
  colunas e 10 linhas. Sempre coloque a coluna de maior relevancia analitica
  (erro absoluto, WMAPE ou volume) como segunda coluna, logo apos o nome.
  Ordene sempre da maior para a menor contribuicao.
- Nao use listas com marcador dentro de tabela nem tabela dentro de lista.
- Termine com uma linha iniciada por "Leitura: " contendo a conclusao pratica
  em uma frase.
- Nunca use separadores horizontais como tres tracos.
- Nao repita em texto o que ja esta na tabela.
- Sempre conclua o raciocinio. Nunca interrompa a resposta no meio de
  uma frase ou de uma lista. Se a resposta for longa, priorize os itens
  mais relevantes e feche com uma sintese.

Dados do recorte:
{ctx}"""

    # Cache por pergunta normalizada + recorte: mesma pergunta, mesma resposta
    # para qualquer usuario dentro do mesmo ciclo.
    usa_cache = not historico          # so cacheia pergunta isolada, nao follow-up
    ch = _chave(_normalizar(pergunta), sorted(meses), base, unidade)
    ciclo = _ciclo_atual(db)

    if usa_cache:
        _garantir_tabelas(db)
        r = db.execute(text("""
            SELECT resposta FROM agente_chat_cache
            WHERE ciclo_sop = :c AND chave = :k
        """), {"c": ciclo, "k": ch}).fetchone()
        if r:
            return r.resposta

    msgs = list(historico or [])
    msgs.append({"role": "user", "content": pergunta})
    resposta = _chamar_claude(system, msgs, max_tokens=3000)

    if usa_cache:
        db.execute(text("""
            INSERT INTO agente_chat_cache (ciclo_sop, chave, pergunta, resposta)
            VALUES (:c, :k, :p, :r)
            ON CONFLICT (ciclo_sop, chave) DO NOTHING
        """), {"c": ciclo, "k": ch, "p": pergunta, "r": resposta})
        db.commit()

    return resposta


def gerar_pdf(relatorio: str, ds: Dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, PageBreak)
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.linecharts import HorizontalLineChart

    AZ  = colors.HexColor("#2563eb")
    ROX = colors.HexColor("#7c3aed")
    CZ  = colors.HexColor("#94a3b8")
    ESC = colors.HexColor("#1e293b")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.3*cm, bottomMargin=1.3*cm)
    ss = getSampleStyleSheet()
    S_TIT = ParagraphStyle("t", parent=ss["Title"], fontSize=15.5,
                           textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    S_SUB = ParagraphStyle("s", parent=ss["Normal"], fontSize=8,
                           textColor=colors.HexColor("#64748b"), spaceAfter=9)
    S_H2  = ParagraphStyle("h", parent=ss["Heading2"], fontSize=9.6,
                           textColor=AZ, spaceBefore=8, spaceAfter=4)
    S_TXT = ParagraphStyle("p", parent=ss["Normal"], fontSize=8.3,
                           leading=11.8, alignment=TA_JUSTIFY, spaceAfter=4.5)
    S_NOT = ParagraphStyle("n", parent=ss["Normal"], fontSize=6.5,
                           textColor=CZ, leading=8.8)

    def f(v, suf="%"):
        return "-" if v is None else f"{v:.1f}{suf}".replace(".", ",")

    def n(v):
        return "-" if v is None else f"{int(v):,}".replace(",", ".")

    esc  = ds["escopo"]
    port = ds["portfolio"]
    per  = (f"{esc['meses_analisados'][0]} a {esc['meses_analisados'][-1]}"
            if esc["meses_analisados"] else "-")

    el = []
    el.append(Paragraph("Relatorio de Acuracia de Demanda | S&amp;OP", S_TIT))
    el.append(Paragraph(
        f"Linea Alimentos &middot; Periodo {per} &middot; Base: {esc['base_volume']} "
        f"&middot; Unidade: {esc['unidade']} &middot; "
        f"Emitido em {_hoje_br().strftime('%d/%m/%Y')}", S_SUB))

    at = ds.get("atendimento", {})
    kpi = [["WMAPE", "BIAS", "Persistencia", "Erro absoluto",
            "Atendimento", "Corte", "Volume", "Diagnostico"],
           [f(port.get("wmape")), f(port.get("bias")),
            f(port.get("persistencia_pct"), "%"), n(port.get("erro_abs")),
            f(at.get("atendimento_pct")), n(at.get("corte")),
            n(port.get("volume")), port.get("classe", "-")]]
    t = Table(kpi, colWidths=[2.25*cm]*8)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.HexColor("#64748b")),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,0), 6.0),
        ("FONTNAME",   (0,1), (-1,1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,1), (-1,1), 10),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#e2e8f0")),
    ]))
    el.append(t)
    el.append(Spacer(1, 9))

    # Grafico 1 - WMAPE por ano
    ev = [e for e in ds["evolucao_anual"] if e.get("wmape") is not None]
    if len(ev) >= 2:
        el.append(Paragraph("WMAPE por ano, mesmos meses do calendario", S_H2))
        d = Drawing(455, 122)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.width, bc.height = 32, 20, 400, 86
        bc.data = [[e["wmape"] for e in ev]]
        bc.categoryAxis.categoryNames = [str(e["ano"]) for e in ev]
        bc.categoryAxis.labels.fontSize = 8
        bc.valueAxis.valueMin = 0
        bc.valueAxis.valueMax = max(e["wmape"] for e in ev) * 1.3
        bc.valueAxis.labels.fontSize = 7
        bc.bars[0].fillColor = AZ
        bc.barWidth = 11
        bc.groupSpacing = 26
        bc.barLabels.fontSize = 7.5
        bc.barLabelFormat = "%0.1f"
        bc.barLabels.dy = 5
        d.add(bc)
        el.append(d)

    # Grafico 2 - WMAPE mes a mes
    sm = [s for s in ds["serie"] if s.get("wmape") is not None]
    if len(sm) >= 3:
        el.append(Paragraph("WMAPE mes a mes no periodo", S_H2))
        d2 = Drawing(455, 112)
        lc = HorizontalLineChart()
        lc.x, lc.y, lc.width, lc.height = 32, 22, 400, 76
        lc.data = [[s["wmape"] for s in sm]]
        lc.categoryAxis.categoryNames = [s["mes"][5:7] + "/" + s["mes"][2:4] for s in sm]
        lc.categoryAxis.labels.fontSize = 6.2
        lc.categoryAxis.labels.angle = 45
        lc.categoryAxis.labels.dy = -7
        lc.valueAxis.valueMin = 0
        lc.valueAxis.labels.fontSize = 7
        lc.lines[0].strokeColor = ROX
        lc.lines[0].strokeWidth = 1.8
        d2.add(lc)
        el.append(d2)

    # Grafico 3 - atendimento mes a mes
    sa = [s for s in (at.get("serie_mensal") or []) if s.get("atendimento") is not None]
    if len(sa) >= 3:
        el.append(Paragraph("Atendimento mes a mes no periodo", S_H2))
        d3 = Drawing(455, 108)
        lc3 = HorizontalLineChart()
        lc3.x, lc3.y, lc3.width, lc3.height = 32, 20, 400, 74
        lc3.data = [[s["atendimento"] for s in sa]]
        lc3.categoryAxis.categoryNames = [s["mes"][5:7] + "/" + s["mes"][2:4] for s in sa]
        lc3.categoryAxis.labels.fontSize = 6.2
        lc3.categoryAxis.labels.angle = 45
        lc3.categoryAxis.labels.dy = -7
        lc3.valueAxis.valueMin = 0
        lc3.valueAxis.valueMax = 100
        lc3.valueAxis.labels.fontSize = 7
        lc3.lines[0].strokeColor = ambar = colors.HexColor("#f97316")
        lc3.lines[0].strokeWidth = 1.8
        d3.add(lc3)
        el.append(d3)

    el.append(Spacer(1, 5))

    for linha in relatorio.split("\n"):
        b = linha.strip().replace("**", "").replace("&", "&amp;")
        if not b:
            continue
        eh_tit = ((b[:2].rstrip(".").isdigit() and len(b) < 75)
                  or (b.isupper() and 3 < len(b) < 75))
        el.append(Paragraph(b.lstrip("#-* ").strip(), S_H2 if eh_tit else S_TXT))

    el.append(Spacer(1, 5))
    el.append(Paragraph(
        f"<b>Escopo:</b> {esc['skus_ativos']} SKUs ativos de {esc['skus_total_base']} "
        f"cadastrados &middot; {esc['granularidade_erro']}.<br/>"
        f"<b>Plano congelado em:</b> {esc['plano_congelado_em']}.<br/>"
        f"<b>Previsao estatistica disponivel desde:</b> "
        f"{esc['previsao_estatistica_disponivel_desde']}.<br/>"
        f"<b>Comparacao ano a ano:</b> meses "
        f"{', '.join(f'{m:02d}' for m in esc['meses_do_calendario_comparados'])} de cada ano.",
        S_NOT))

    # Anexo I - evolucao anual
    el.append(PageBreak())
    el.append(Paragraph("Anexo I | Evolucao ano a ano do portfolio", S_H2))
    cab = ["Ano","Volume","WMAPE","Delta vs ano ant.","BIAS","Persist.",
           "Erro absoluto","Diagnostico"]
    dados = [cab] + [[
        str(e["ano"]), n(e.get("volume")), f(e.get("wmape")),
        f(e.get("delta_wmape_pp"), " pp"), f(e.get("bias")),
        f(e.get("persistencia_pct"), "%"), n(e.get("erro_abs")),
        e.get("classe","-"),
    ] for e in ds["evolucao_anual"]]
    ta = Table(dados, colWidths=[1.6*cm,2.4*cm,1.8*cm,2.4*cm,1.7*cm,1.7*cm,
                                 2.4*cm,3*cm], repeatRows=1)
    ta.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), ESC),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 6.3),
        ("ALIGN",      (1,0), (6,-1), "RIGHT"),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    el.append(ta)

    # Anexo II - categorias
    el.append(Spacer(1, 11))
    el.append(Paragraph("Anexo II | Categorias, ordenadas por erro absoluto", S_H2))
    cab2 = ["Categoria","Volume","Erro abs.","WMAPE","Ano ant.","Delta pp",
            "BIAS","Persist.","WMAPE IA","FVA","Atend.","Diagnostico"]
    dados2 = [cab2] + [[
        c["categoria"][:19], n(c.get("volume")), n(c.get("erro_abs")),
        f(c.get("wmape")), f(c.get("wmape_ano_anterior")),
        f(c.get("delta_wmape_pp"), ""), f(c.get("bias")),
        f(c.get("persistencia_pct"), "%"), f(c.get("wmape_ia")),
        f(c.get("fva_pp"), ""), f(c.get("atendimento_pct")), c.get("classe","-"),
    ] for c in ds["categorias"]]
    tc = Table(dados2, colWidths=[2.5*cm,1.7*cm,1.6*cm,1.35*cm,1.35*cm,1.25*cm,
                                  1.25*cm,1.3*cm,1.4*cm,1.1*cm,1.25*cm,2.1*cm],
               repeatRows=1)
    tc.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), ESC),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 5.8),
        ("ALIGN",      (1,0), (-2,-1), "RIGHT"),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0,0), (-1,-1), 2.5), ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
    ]))
    el.append(tc)

    # Anexo III - SKUs
    el.append(PageBreak())
    el.append(Paragraph(
        f"Anexo III | {len(ds['skus'])} SKUs, ordenados por erro absoluto", S_H2))
    cab3 = ["SKU","Descricao","Categoria","Maturid.","Volume","Erro abs.","WMAPE",
            "Ano ant.","Delta pp","BIAS","Persist.","Atend.","Restr.","Diagnostico"]
    dados3 = [cab3] + [[
        s["sku"], (s["descricao"] or "")[:22], (s["categoria"] or "")[:11],
        (s.get("maturidade") or "-")[:10],
        n(s.get("volume")), n(s.get("erro_abs")), f(s.get("wmape")),
        f(s.get("wmape_ano_anterior")), f(s.get("delta_wmape_pp"), ""),
        f(s.get("bias")), f(s.get("persistencia_pct"), "%"),
        f(s.get("atendimento_pct")),
        "Sim" if s.get("restricao_producao") else "-",
        s.get("classe","-")[:13],
    ] for s in ds["skus"]]
    ts = Table(dados3, colWidths=[1.35*cm,2.9*cm,1.6*cm,1.5*cm,1.3*cm,1.25*cm,1.15*cm,
                                  1.15*cm,1.05*cm,1.05*cm,1.05*cm,1.05*cm,0.85*cm,1.6*cm],
               repeatRows=1)
    ts.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), ESC),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 5.0),
        ("ALIGN",      (4,0), (-2,-1), "RIGHT"),
        ("GRID",       (0,0), (-1,-1), 0.25, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0,0), (-1,-1), 1.8), ("BOTTOMPADDING", (0,0), (-1,-1), 1.8),
    ]))
    el.append(ts)

    # Anexo IV - lancamentos e restricoes
    if ds.get("lancamentos") or ds.get("sob_restricao"):
        el.append(PageBreak())
        el.append(Paragraph(
            "Anexo IV | Itens fora do universo comparavel", S_H2))
        el.append(Paragraph(
            "Itens de lancamento nao possuem serie suficiente para o modelo "
            "estatistico aprender padrao — o erro deles nao e comparavel ao de "
            "itens maduros. Itens sob restricao de producao tiveram a entrega "
            "limitada por decisao conhecida, e o atendimento baixo nao decorre "
            "de falha de previsao.", S_NOT))
        el.append(Spacer(1, 7))

        if ds.get("lancamentos"):
            el.append(Paragraph("Lancamentos e itens recentes", S_H2))
            cabL = ["SKU","Descricao","Categoria","1a venda","Meses","Maturid.",
                    "Volume","WMAPE","BIAS","Atend."]
            dadosL = [cabL] + [[
                s["sku"], (s["descricao"] or "")[:30], (s["categoria"] or "")[:14],
                s.get("primeira_venda") or "-", str(s.get("meses_historico") or "-"),
                s.get("maturidade","-"), n(s.get("volume")),
                f(s.get("wmape")), f(s.get("bias")), f(s.get("atendimento_pct")),
            ] for s in ds["lancamentos"]]
            tl = Table(dadosL, colWidths=[1.5*cm,4.2*cm,2.1*cm,1.6*cm,1.2*cm,
                                          1.7*cm,1.5*cm,1.4*cm,1.4*cm,1.4*cm],
                       repeatRows=1)
            tl.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), ESC),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 5.9),
                ("ALIGN",      (4,0), (-1,-1), "RIGHT"),
                ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0,0), (-1,-1), 2.5), ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
            ]))
            el.append(tl)
            el.append(Spacer(1, 11))

        if ds.get("sob_restricao"):
            el.append(Paragraph("Itens sob restricao declarada de producao", S_H2))
            cabR = ["SKU","Descricao","Categoria","Segmento","Periodo","Motivo",
                    "Pedido","Corte","Atend.","BIAS"]
            dadosR = []
            for s in ds["sob_restricao"]:
                r0 = (s.get("restricao_producao") or [{}])[0]
                dadosR.append([
                    s["sku"], (s["descricao"] or "")[:26], (s["categoria"] or "")[:12],
                    (s.get("segmento") or "-")[:12],
                    r0.get("periodo","-"), (r0.get("motivo","-"))[:24],
                    n(s.get("volume")), n(s.get("corte_cx")),
                    f(s.get("atendimento_pct")), f(s.get("bias")),
                ])
            tr = Table([cabR] + dadosR,
                       colWidths=[1.4*cm,3.6*cm,1.8*cm,1.8*cm,2.1*cm,3.0*cm,
                                  1.3*cm,1.3*cm,1.3*cm,1.3*cm],
                       repeatRows=1)
            tr.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), ESC),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 5.7),
                ("ALIGN",      (6,0), (-1,-1), "RIGHT"),
                ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#FEF6EC")]),
                ("TOPPADDING", (0,0), (-1,-1), 2.5), ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
            ]))
            el.append(tr)

    el.append(Spacer(1, 7))
    el.append(Paragraph(
        "WMAPE = soma dos erros absolutos / soma do realizado &middot; "
        "BIAS = (soma do previsto - soma do realizado) / soma do realizado &middot; "
        "Persistencia = percentual de meses com erro na direcao do BIAS medio &middot; "
        "FVA = WMAPE do plano - WMAPE da previsao estatistica &middot; "
        "Atendimento = volume entregue / volume pedido. "
        "Erro absoluto computado em (SKU, mes) antes de qualquer agregacao. "
        "Comparacao ano a ano restrita aos mesmos meses do calendario em cada ano. "
        "Maturidade: Lancamento ate 6 meses de historico, Recente ate 18, Maduro acima disso. "
        "Itens sob restricao de producao tem o atendimento explicado pela restricao, nao por falha de previsao.",
        S_NOT))

    doc.build(el)
    buf.seek(0)
    return buf.read()