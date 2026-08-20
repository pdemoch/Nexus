"""
=====================================================================
CONSTANTS — PONTO ÚNICO DE VERDADE DAS REGRAS DE NEGÓCIO DO NEXUS
=====================================================================
Destino: app/core/constants.py

Antes deste módulo, as mesmas constantes estavam declaradas em arquivos
diferentes, com risco de divergirem em silêncio:

    DEFASAGEM_MESES = 2          -> agente_kpis.py, perfil_sku.py, shared_ibp.py
    CICLO_PISO      = 2026-04-01 -> agente_kpis.py, perfil_sku.py, shared_ibp.py
    PISO_NEXUS      = 2026-06-01 -> agente_kpis.py
    MESES_LANCAMENTO/RECENTE     -> agente_kpis.py
    JANELA_MESES    = 5          -> pipeline.py
    janela de PMV   = 3 meses    -> loader.py (literal em SQL)
    janela de share = 6 meses    -> loader.py (literal em Python)

Regra: nenhum módulo redeclara estes valores. Todos importam daqui.

    from app.core.constants import DEFASAGEM_MESES, HORIZ_DECISAO
"""

import datetime

# =====================================================================
# CICLO S&OP — REGRA M-2
# =====================================================================
# O ciclo N congela o plano do mês N+2. Logo, o mês M é auditado contra o
# plano do ciclo (M − 2). Isso é o que responde ao CFO cético: "o previsto
# é o compromisso assumido ANTES do mês acontecer, não o número digitado
# depois de ver a venda".
DEFASAGEM_MESES = 2

# Primeiro ciclo que existiu no Nexus. Nada antes disto.
CICLO_PISO = datetime.date(2026, 4, 1)

# Primeiro mês projetado que nasceu do Nexus (CICLO_PISO + DEFASAGEM_MESES).
# Antes disso o plano vem de fato_previsao_humana (planilha).
PISO_NEXUS = datetime.date(2026, 6, 1)

# Início do histórico disponível na API 150 do ERP.
PISO_HISTORICO = datetime.date(2023, 1, 1)


# =====================================================================
# HORIZONTES DE PREVISÃO
# =====================================================================
# O forecaster CALCULA 5 horizontes (M+0..M+4) porque a validação por origem
# rolante precisa medir o erro em cada passo. Mas o S&OP só DECIDE M+2, M+3
# e M+4 — a regra M-2 torna M+0 e M+1 inalcançáveis pelo planejamento.
#
# Medido em 20/08/2026: M+0 e M+1 representavam 140.785 de 353.589 linhas da
# fato_ibp_granular (39,8%, ~98 MB) que nunca apareceram em tela, nunca foram
# editadas e nunca entraram em cálculo de acurácia.
#
# HORIZONTE     -> quantos passos o modelo calcula internamente
# HORIZ_DECISAO -> quais passos são efetivamente GRAVADOS no banco
HORIZONTE     = 5
HORIZ_DECISAO = (2, 3, 4)


# =====================================================================
# JANELAS DE PROCESSAMENTO
# =====================================================================
# ETL: quantos meses a fato_vendas recarrega a cada pipeline. O loader apaga
# (DELETE WHERE data_pedido >= data_inicio) e reinsere. As duas pontas TÊM
# que usar a mesma data.
JANELA_ETL_MESES = 5

# PMV: janela de referência da cascata de precificação. Preço atual, não
# histórico velho.
JANELA_PMV_MESES = 3

# Share de rateio: quantos meses de histórico definem o peso de cada cliente
# dentro do SKU.
JANELA_SHARE_MESES = 6

# Recarga total: trava de segurança mínima de linhas por modo.
TRAVA_MINIMA_NORMAL  = 1_000
TRAVA_MINIMA_RECARGA = 50_000


# =====================================================================
# MATURIDADE DO SKU
# =====================================================================
# Meses de histórico de venda. Define se a acurácia do item é cobrável.
#
# Lançamento: não há série suficiente para o modelo aprender padrão. WMAPE
#   alto é esperado e não indica falha de processo.
# Recente: já há série, mas sem ciclo sazonal completo fechado.
# Maduro: universo em que a acurácia deve ser cobrada.
MESES_LANCAMENTO = 6
MESES_RECENTE    = 18


# =====================================================================
# LIMIARES DE DIAGNÓSTICO
# =====================================================================
# WMAPE e BIAS que separam "sob controle" de viés estrutural.
WMAPE_SOB_CONTROLE = 20.0
BIAS_SOB_CONTROLE  = 10.0
# Percentual de meses com erro na mesma direção que caracteriza viés
# sistemático (e não aleatório).
PERSISTENCIA_ESTRUTURAL = 70.0

# Fill rate: faixas de classificação.
FILL_RATE_CRITICO  = 85.0
FILL_RATE_ATENCAO  = 93.0
FILL_RATE_ADEQUADO = 98.0


# =====================================================================
# HELPERS DERIVADOS
# =====================================================================
def ciclo_fonte_do_mes(mes: datetime.date) -> str:
    """
    Dado um mês, devolve o ciclo 'MM/YYYY' cujo plano deve ser auditado
    contra a venda daquele mês (regra M-2), com piso no primeiro ciclo.

    Ex.: junho/2026 -> '04/2026' ; julho/2026 -> '05/2026'
    """
    from dateutil.relativedelta import relativedelta
    alvo = mes.replace(day=1) - relativedelta(months=DEFASAGEM_MESES)
    if alvo < CICLO_PISO:
        alvo = CICLO_PISO
    return alvo.strftime("%m/%Y")


def horizonte_do_par(ciclo_sop: str, mes_projetado: datetime.date) -> int:
    """
    Distância em meses entre o ciclo e o mês projetado.
    Ex.: ciclo '08/2026' e mês 2026-10-01 -> 2
    """
    m, a = ciclo_sop.split("/")
    return ((mes_projetado.year * 12 + mes_projetado.month)
            - (int(a) * 12 + int(m)))


def maturidade(meses_hist) -> str:
    """Classifica o SKU pela quantidade de meses de histórico de venda."""
    if meses_hist is None:
        return "Indefinido"
    if meses_hist <= MESES_LANCAMENTO:
        return "Lancamento"
    if meses_hist <= MESES_RECENTE:
        return "Recente"
    return "Maduro"


def sdiv(num, den, mult=1.0):
    """
    Divisão segura — devolve None se o denominador for 0, NaN ou negativo.

    Necessária porque Python puro levanta ZeroDivisionError em float/0.0
    enquanto NumPy devolve infinito em silêncio. Sem um helper único, os
    dois comportamentos convivem no mesmo cálculo.
    """
    try:
        d = float(den)
        if d <= 0 or not (d == d):      # cobre 0.0 e NaN
            return None
        return float(num) / d * float(mult)
    except (ZeroDivisionError, TypeError, ValueError):
        return None


def hoje_br() -> datetime.date:
    """Data corrente no fuso de Brasília (UTC-3)."""
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()


def ultimo_mes_fechado() -> str:
    """Último mês-calendário encerrado, no formato 'YYYY-MM'."""
    h = hoje_br().replace(day=1)
    return (h - datetime.timedelta(days=1)).strftime("%Y-%m")