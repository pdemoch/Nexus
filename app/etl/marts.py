"""
=====================================================================
MARTS — CAMADA ANALÍTICA MATERIALIZADA
=====================================================================
Destino: app/etl/marts.py

Recalcula, nesta ordem obrigatória (cada um depende do anterior):

    1. mart_pmv_sku_mes        fonte única de preço
    2. mart_vendas_mes         realizado, três estados
    3. mart_plano_sku_mes      plano normalizado (dissolve o UNION ALL)
    4. mart_acuracia_sku_mes   plano x realizado + preço do erro

Chamado no fim do pipeline (ciclo novo E ciclo existente) ou à mão:

    sudo docker exec nexus_backend python -m app.etl.marts

---------------------------------------------------------------------
ESTRATÉGIA: FULL REFRESH
---------------------------------------------------------------------
TRUNCATE + INSERT em cada mart. Escolha deliberada:

  - O grão é (sku, mes): ~114 SKUs x ~44 meses = ~5.000 linhas por mart.
    Full refresh roda em segundos.
  - Refresh incremental exigiria rastrear quais meses mudaram. Como a
    janela de ETL recarrega 5 meses e uma edição de S&OP pode alterar
    qualquer mês do ciclo, o conjunto "sujo" é grande e difícil de
    delimitar com segurança.
  - Full refresh é idempotente por construção: rodar duas vezes dá o
    mesmo resultado. Incremental acumula divergência silenciosa.

Cada mart roda em transação própria. Se o 3 falhar, os marts 1 e 2 já
estão consistentes e o 4 não é escrito pela metade.

---------------------------------------------------------------------
REGRAS TRAVADAS
---------------------------------------------------------------------
PMV     = SUM(vl_pedido) / SUM(qt_pedido). Razão de totais, nunca média
          de PMVs ao agregar categoria.
Estados = qt_pedido = qt_entregue + qt_corte + carteira. Três, não dois.
          Corte é qtcorte do ERP, nunca subtração.
Plano   = regra M-2. O mês M é auditado contra o ciclo (M-2).
WMAPE   = computado no grão (sku, mes) e só depois agregado.
"""

import logging
import time
from datetime import date

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.constants import (
    DEFASAGEM_MESES, PISO_NEXUS, PISO_HISTORICO,
    MESES_LANCAMENTO, MESES_RECENTE,
)

logger = logging.getLogger(__name__)


# =====================================================================
# 1. PMV — FONTE ÚNICA DE PREÇO
# =====================================================================
_SQL_PMV = """
INSERT INTO mart_pmv_sku_mes
    (sku, mes, pmv_mes, pmv_3m, pmv_historico, qt_base, vl_base)
WITH base AS (
    -- Só linhas com volume E receita: uma sem a outra distorce a razão.
    SELECT TRIM(v.sku::text)                          AS sku,
           DATE_TRUNC('month', v.data_pedido)::date   AS mes,
           SUM(v.qt_pedido)                           AS qt,
           SUM(v.vl_pedido)                           AS vl
    FROM fato_vendas v
    WHERE v.qt_pedido > 0 AND v.vl_pedido > 0
      AND v.data_pedido >= :piso
    GROUP BY 1, 2
),
hist AS (
    -- PMV de todo o histórico do SKU: último degrau da cascata.
    SELECT sku, SUM(vl) / NULLIF(SUM(qt), 0) AS pmv_hist
    FROM base GROUP BY sku
)
SELECT b.sku,
       b.mes,
       b.vl / NULLIF(b.qt, 0)                                   AS pmv_mes,
       -- ROWS (não RANGE): pega os 3 últimos meses COM venda. Item
       -- intermitente não fica sem preço por causa de um mês vazio.
       SUM(b.vl) OVER w / NULLIF(SUM(b.qt) OVER w, 0)           AS pmv_3m,
       h.pmv_hist,
       b.qt,
       b.vl
FROM base b
JOIN hist h ON h.sku = b.sku
WINDOW w AS (PARTITION BY b.sku ORDER BY b.mes ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
"""


# =====================================================================
# 2. VENDAS — REALIZADO COM OS TRÊS ESTADOS
# =====================================================================
_SQL_VENDAS = """
INSERT INTO mart_vendas_mes
    (sku, mes, categoria, segmento, curva, ativo,
     qt_pedido, vl_pedido, qt_entregue, vl_entregue,
     qt_corte, vl_corte, qt_corte_transferencia, vl_corte_transferencia,
     qt_carteira, vl_carteira, pmv, n_clientes)
SELECT TRIM(v.sku::text)                            AS sku,
       DATE_TRUNC('month', v.data_pedido)::date     AS mes,
       MAX(p.categoria)                             AS categoria,
       MAX(p.segmento)                              AS segmento,
       MAX(p.curva)                                 AS curva,
       BOOL_OR(COALESCE(p.ativo, FALSE))            AS ativo,
       SUM(v.qt_pedido)                             AS qt_pedido,
       SUM(v.vl_pedido)                             AS vl_pedido,
       -- Nomenclatura canônica Nexus: entregue, não "faturado"
       SUM(v.qtfatura)                              AS qt_entregue,
       SUM(v.vlfatura)                              AS vl_entregue,
       -- Corte é o valor do ERP. NUNCA (pedido - entregue): essa
       -- subtração conta a carteira em aberto como ruptura e destrói a
       -- métrica no mês corrente.
       SUM(v.qtcorte)                               AS qt_corte,
       SUM(v.vlcorte)                               AS vl_corte,
       -- ── CORTE POR TRANSFERÊNCIA DE CÓDIGO ────────────────────────
       -- Linha com sku_origem <> sku veio de um código consolidado por
       -- DE-PARA (COPA). Quando a promoção encerra, o pedido no código
       -- promocional é cortado e o cliente é atendido no código regular:
       -- ele RECEBEU o produto, não houve venda perdida.
       --
       -- Somar isso ao corte de ruptura mistura decisão comercial com
       -- falha de suprimento. Medido: 9.001 cx nos 5 SKUs consolidados
       -- entre jan e jul/2026, com pico de 4.312 em fevereiro contra base
       -- de ~300/mês.
       --
       -- Só fica populado após a Recarga Total com o transformer que
       -- preserva sku_origem. Antes disso é zero, e qt_corte segue
       -- valendo integralmente.
       SUM(v.qtcorte) FILTER (WHERE v.sku_origem <> v.sku) AS qt_corte_transferencia,
       SUM(v.vlcorte) FILTER (WHERE v.sku_origem <> v.sku) AS vl_corte_transferencia,
       -- GREATEST(...,0): o ERP tem raras linhas com entregue > pedido
       -- (54 cx em 2026). Sem a trava viraria carteira negativa.
       SUM(GREATEST(v.qt_pedido - v.qtfatura - v.qtcorte, 0)) AS qt_carteira,
       SUM(GREATEST(v.vl_pedido - v.vlfatura - v.vlcorte, 0)) AS vl_carteira,
       SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0)         AS pmv,
       COUNT(DISTINCT v.cgc)                        AS n_clientes
FROM fato_vendas v
LEFT JOIN dim_produtos p ON p.sku = v.sku
WHERE v.data_pedido >= :piso
GROUP BY 1, 2
"""


# =====================================================================
# 3. PLANO — NORMALIZA AS DUAS ERAS DO PLANEJAMENTO
# =====================================================================
_SQL_PLANO = """
INSERT INTO mart_plano_sku_mes
    (sku, mes, fonte, ciclo_plano, qt_plano, vl_plano,
     vl_plano_pmv_real, qt_ia, pmv_plano)
WITH
-- ERA 1: planilha (jan/2023 a mai/2026). Só caixas, grão sku x mes.
historico AS (
    SELECT TRIM(h.sku::text)          AS sku,
           h.mes_projetado::date      AS mes,
           SUM(h.vol_humano)          AS qt_plano
    FROM fato_previsao_humana h
    WHERE h.fonte = 'HISTORICO'
      AND h.mes_projetado >= :piso
      AND h.mes_projetado <  :piso_nexus
    GROUP BY 1, 2
),
-- ERA 2: Nexus (jun/2026+). Grão ciclo x mes x sku x cgc, filtrado pelo
-- horizonte de decisão M-2 (o compromisso assumido 2 meses antes).
nexus AS (
    SELECT TRIM(g.sku::text)          AS sku,
           g.mes_projetado::date      AS mes,
           MAX(g.ciclo_sop)           AS ciclo_plano,
           SUM(g.vol_final)           AS qt_plano,
           SUM(g.vol_ia)              AS qt_ia,
           -- PMV do plano: razão de totais, ponderada pelo volume de cada
           -- cliente. Não é média dos pmv_aplicado.
           SUM(g.vol_final * g.pmv_aplicado)
               / NULLIF(SUM(g.vol_final), 0)          AS pmv_plano,
           SUM(g.vol_final * g.pmv_aplicado)          AS vl_plano
    FROM fato_ibp_granular g
    WHERE (EXTRACT(YEAR FROM g.mes_projetado) * 12
           + EXTRACT(MONTH FROM g.mes_projetado))
        - (SPLIT_PART(g.ciclo_sop, '/', 2)::int * 12
           + SPLIT_PART(g.ciclo_sop, '/', 1)::int) = :defasagem
      AND g.mes_projetado >= :piso_nexus
    GROUP BY 1, 2
),
unificado AS (
    SELECT sku, mes, 'HISTORICO' AS fonte, NULL::varchar AS ciclo_plano,
           qt_plano, NULL::numeric AS vl_plano_ciclo,
           NULL::numeric AS qt_ia, NULL::numeric AS pmv_plano
    FROM historico
    UNION ALL
    SELECT sku, mes, 'NEXUS', ciclo_plano,
           qt_plano, vl_plano, qt_ia, pmv_plano
    FROM nexus
)
SELECT u.sku,
       u.mes,
       u.fonte,
       u.ciclo_plano,
       u.qt_plano,
       -- vl_plano: para NEXUS é o compromisso ao preço do ciclo; para
       -- HISTORICO a planilha só tinha caixas, então o R$ nasce do PMV
       -- realizado daquele mês (cascata mes -> 3m -> histórico).
       COALESCE(u.vl_plano_ciclo,
                u.qt_plano * COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0)),
       -- vl_plano_pmv_real: o MESMO volume ao preço que de fato ocorreu.
       -- Comparado com vl_plano, isola erro de preço de erro de volume.
       u.qt_plano * COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0),
       u.qt_ia,
       u.pmv_plano
FROM unificado u
LEFT JOIN mart_pmv_sku_mes pm ON pm.sku = u.sku AND pm.mes = u.mes
"""


# =====================================================================
# 4. ACURÁCIA — PLANO x REALIZADO + PREÇO DO ERRO
# =====================================================================
_SQL_ACURACIA = """
INSERT INTO mart_acuracia_sku_mes
    (sku, mes, categoria, segmento, curva, ativo,
     fonte_plano, ciclo_plano, meses_historico, maturidade,
     qt_pedido, vl_pedido, qt_entregue, vl_entregue,
     qt_corte, vl_corte, qt_carteira,
     qt_plano, vl_plano, qt_ia, tem_plano, pmv,
     gap_previsao_cx, gap_execucao_cx, erro_abs_cx,
     gap_previsao_rs, gap_execucao_rs, erro_abs_rs,
     vl_excesso_plano, vl_perda_corte, vl_perda_subplano)
WITH
-- Meses de histórico de venda: define a maturidade. Item de lançamento
-- tem WMAPE alto por natureza, e cobrar acurácia dele contamina o
-- diagnóstico do processo.
hist_sku AS (
    SELECT sku, COUNT(DISTINCT mes) AS meses_hist
    FROM mart_vendas_mes
    WHERE qt_pedido > 0
    GROUP BY sku
),
-- Espinha: todo par (sku, mes) que teve venda OU teve plano.
espinha AS (
    SELECT sku, mes FROM mart_vendas_mes
    UNION
    SELECT sku, mes FROM mart_plano_sku_mes
)
SELECT e.sku,
       e.mes,
       COALESCE(v.categoria, p2.categoria)                  AS categoria,
       COALESCE(v.segmento,  p2.segmento)                   AS segmento,
       COALESCE(v.curva,     p2.curva)                      AS curva,
       COALESCE(v.ativo,     p2.ativo, FALSE)               AS ativo,
       pl.fonte                                             AS fonte_plano,
       pl.ciclo_plano,
       COALESCE(h.meses_hist, 0)                            AS meses_historico,
       CASE
           WHEN COALESCE(h.meses_hist, 0) <= :m_lanc  THEN 'Lancamento'
           WHEN COALESCE(h.meses_hist, 0) <= :m_rec   THEN 'Recente'
           ELSE 'Maduro'
       END                                                  AS maturidade,

       COALESCE(v.qt_pedido,   0),
       COALESCE(v.vl_pedido,   0),
       COALESCE(v.qt_entregue, 0),
       COALESCE(v.vl_entregue, 0),
       COALESCE(v.qt_corte,    0),
       COALESCE(v.vl_corte,    0),
       COALESCE(v.qt_carteira, 0),

       pl.qt_plano,
       pl.vl_plano,
       pl.qt_ia,
       (pl.qt_plano IS NOT NULL)                            AS tem_plano,
       COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0) AS pmv,

       -- ── GAPS EM VOLUME ────────────────────────────────────────────
       -- Positivo = superestimou / sobrou.
       --
       -- COALESCE(qt_plano, 0): vender um SKU que NÃO estava no plano é
       -- erro de previsão de tamanho igual ao volume vendido — o plano
       -- disse zero, implicitamente. Tratar como NULL removeria esse item
       -- do numerador do WMAPE mantendo-o no denominador, e o indicador
       -- ficaria artificialmente bom.
       --
       -- Medido em 20/08/2026: com NULL, o WMAPE de abril caía de 20,50
       -- para 17,69 — 2,81 pp de erro escondido.
       --
       -- A coluna tem_plano preserva a distinção entre "errou a previsão"
       -- e "não havia previsão", para quem quiser separar as duas coisas.
       COALESCE(pl.qt_plano, 0) - COALESCE(v.qt_pedido, 0)   AS gap_previsao_cx,
       COALESCE(v.qt_pedido, 0) - COALESCE(v.qt_entregue, 0) AS gap_execucao_cx,
       -- erro_abs_cx alimenta o WMAPE. Gravado no grão (sku, mes) porque
       -- o WMAPE TEM que ser computado aqui e só depois agregado.
       ABS(COALESCE(pl.qt_plano, 0) - COALESCE(v.qt_pedido, 0)) AS erro_abs_cx,

       -- ── GAPS EM REAIS ─────────────────────────────────────────────
       (COALESCE(pl.qt_plano, 0) - COALESCE(v.qt_pedido, 0))
           * COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0),
       COALESCE(v.vl_pedido, 0) - COALESCE(v.vl_entregue, 0),
       ABS(COALESCE(pl.qt_plano, 0) - COALESCE(v.qt_pedido, 0))
           * COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0),

       -- ── PRECIFICAÇÃO DO ERRO ──────────────────────────────────────
       -- Capital imobilizado: plano acima do que o mercado pediu.
       GREATEST(COALESCE(pl.qt_plano, 0) - COALESCE(v.qt_pedido, 0), 0)
           * COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0),
       -- Receita perdida na entrega: corte confirmado pelo ERP.
       COALESCE(v.vl_corte, 0),
       -- Venda que o plano não enxergou: pedido acima do planejado.
       -- Inclui o caso sem plano nenhum (plano = 0).
       GREATEST(COALESCE(v.qt_pedido, 0) - COALESCE(pl.qt_plano, 0), 0)
           * COALESCE(pm.pmv_mes, pm.pmv_3m, pm.pmv_historico, 0)

FROM espinha e
LEFT JOIN mart_vendas_mes     v  ON v.sku  = e.sku AND v.mes  = e.mes
LEFT JOIN mart_plano_sku_mes  pl ON pl.sku = e.sku AND pl.mes = e.mes
LEFT JOIN mart_pmv_sku_mes    pm ON pm.sku = e.sku AND pm.mes = e.mes
LEFT JOIN hist_sku            h  ON h.sku  = e.sku
LEFT JOIN dim_produtos        p2 ON p2.sku = e.sku
"""


# =====================================================================
# ORQUESTRAÇÃO
# =====================================================================
def _rodar(db, nome: str, sql: str, params: dict, log) -> int:
    """Executa um mart em transação própria e registra em mart_controle."""
    t0 = time.time()
    try:
        db.execute(text(f"TRUNCATE TABLE {nome}"))
        db.execute(text(sql), params)
        linhas = db.execute(text(f"SELECT COUNT(*) FROM {nome}")).scalar() or 0
        dur = round(time.time() - t0, 2)
        db.execute(text("""
            INSERT INTO mart_controle
                (mart, linhas, duracao_seg, atualizado_em, ciclo_sop, status, mensagem)
            VALUES (:m, :l, :d, now(), :c, 'OK', NULL)
            ON CONFLICT (mart) DO UPDATE
              SET linhas = EXCLUDED.linhas, duracao_seg = EXCLUDED.duracao_seg,
                  atualizado_em = now(), ciclo_sop = EXCLUDED.ciclo_sop,
                  status = 'OK', mensagem = NULL
        """), {"m": nome, "l": linhas, "d": dur, "c": params.get("_ciclo")})
        db.commit()
        log(f"   • {nome:24} {linhas:7,} linhas em {dur:5.2f}s")
        return linhas
    except Exception as e:
        db.rollback()
        try:
            db.execute(text("""
                INSERT INTO mart_controle
                    (mart, linhas, duracao_seg, atualizado_em, ciclo_sop, status, mensagem)
                VALUES (:m, 0, 0, now(), :c, 'ERRO', :msg)
                ON CONFLICT (mart) DO UPDATE
                  SET status = 'ERRO', mensagem = EXCLUDED.mensagem, atualizado_em = now()
            """), {"m": nome, "c": params.get("_ciclo"), "msg": str(e)[:500]})
            db.commit()
        except Exception:
            db.rollback()
        raise


def compute_marts(ciclo_sop: str = None, log_callback=print) -> dict:
    """
    Recalcula os quatro marts na ordem de dependência.

    Idempotente: rodar duas vezes produz o mesmo resultado.
    Seguro em ciclo ativo: não escreve em nenhuma tabela transacional,
    apenas lê. Os marts são descartáveis e reconstrutíveis a qualquer
    momento a partir de fato_vendas, fato_ibp_granular e
    fato_previsao_humana.
    """
    log_callback("\n📊 [MARTS] Atualizando camada analítica...")
    t0 = time.time()
    resultado = {}

    params = {
        "piso":       PISO_HISTORICO,
        "piso_nexus": PISO_NEXUS,
        "defasagem":  DEFASAGEM_MESES,
        "m_lanc":     MESES_LANCAMENTO,
        "m_rec":      MESES_RECENTE,
        "_ciclo":     ciclo_sop,
    }

    with SessionLocal() as db:
        # A ordem é obrigatória: plano usa pmv, acurácia usa os três.
        resultado["mart_pmv_sku_mes"] = _rodar(
            db, "mart_pmv_sku_mes", _SQL_PMV,
            {"piso": params["piso"], "_ciclo": ciclo_sop}, log_callback)

        resultado["mart_vendas_mes"] = _rodar(
            db, "mart_vendas_mes", _SQL_VENDAS,
            {"piso": params["piso"], "_ciclo": ciclo_sop}, log_callback)

        resultado["mart_plano_sku_mes"] = _rodar(
            db, "mart_plano_sku_mes", _SQL_PLANO,
            {"piso": params["piso"], "piso_nexus": params["piso_nexus"],
             "defasagem": params["defasagem"], "_ciclo": ciclo_sop}, log_callback)

        resultado["mart_acuracia_sku_mes"] = _rodar(
            db, "mart_acuracia_sku_mes", _SQL_ACURACIA,
            {"m_lanc": params["m_lanc"], "m_rec": params["m_rec"],
             "_ciclo": ciclo_sop}, log_callback)

        # ANALYZE: o planner precisa de estatísticas frescas depois do
        # TRUNCATE + INSERT, senão escolhe plano ruim nas primeiras queries.
        for t in resultado:
            db.execute(text(f"ANALYZE {t}"))
        db.commit()

    log_callback(f"   ✅ Marts atualizados em {time.time() - t0:.1f}s")
    return resultado


def validar_marts(log_callback=print) -> bool:
    """
    Confere os marts contra o baseline medido em 20/08/2026, ANTES de
    qualquer migração de leitura.

    Se o WMAPE aqui não bater com o das telas, os marts estão errados e
    a Fase 6 (migrar routers) NÃO deve acontecer.
    """
    BASELINE = {
        "2026-01": (23.27,  13.45), "2026-02": (27.23, -13.77),
        "2026-03": (20.95,  -0.49), "2026-04": (20.50,  -4.63),
        "2026-05": (21.33,   1.84), "2026-06": (40.00,  24.11),
        "2026-07": (18.33,   3.28),
    }
    log_callback("\n🔍 [MARTS] Validando contra o baseline de 20/08/2026...")
    ok = True
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT TO_CHAR(mes, 'YYYY-MM') AS mes,
                   ROUND((SUM(erro_abs_cx) / NULLIF(SUM(qt_pedido), 0) * 100)::numeric, 2) AS wmape,
                   ROUND(((SUM(qt_plano) - SUM(qt_pedido))
                          / NULLIF(SUM(qt_pedido), 0) * 100)::numeric, 2) AS bias,
                   ROUND((SUM(qt_pedido) FILTER (WHERE NOT tem_plano)
                          / NULLIF(SUM(qt_pedido), 0) * 100)::numeric, 2) AS sem_plano_pct
            FROM mart_acuracia_sku_mes
            WHERE ativo = TRUE AND qt_pedido > 0
              AND mes >= '2026-01-01' AND mes < '2026-08-01'
            GROUP BY 1 ORDER BY 1
        """)).fetchall()

        log_callback("   mes     | wmape  (esp)   | bias    (esp)    | s/plano")
        log_callback("   --------|----------------|------------------|--------")
        for r in rows:
            ew, eb = BASELINE.get(r.mes, (None, None))
            w, b = float(r.wmape or 0), float(r.bias or 0)
            sp = float(r.sem_plano_pct or 0)
            bw = ew is not None and abs(w - ew) < 0.05
            bb = eb is not None and abs(b - eb) < 0.05
            ok = ok and bw and bb
            log_callback(f"   {r.mes} | {w:6.2f} ({ew:6.2f}) {'OK  ' if bw else '<<<<'}"
                         f" | {b:7.2f} ({eb:7.2f}) {'OK  ' if bb else '<<<<'}"
                         f" | {sp:5.2f}%")

    log_callback("   ✅ Marts batem com o baseline." if ok else
                 "   ❌ DIVERGÊNCIA. Não migre os routers (Fase 6).")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    compute_marts()
    validar_marts()