"""
=====================================================================
PIPELINE FINANCEIRO — extração Gobi API → S3 (parquets mensais)
=====================================================================
Destino: app/financeiro/pipeline_financeiro.py

Dois modos, mesmo comportamento que o pipeline S&OP:
  recarga_total=False   Apaga e re-extrai os últimos 3 meses + clientes
  recarga_total=True    Extrai todo o histórico desde 2023-01 + clientes

Clientes sempre: apaga o parquet latest e baixa de novo (sem data).

Chamado em background por router_financeiro.py — nunca bloqueia a API.
"""

import logging
from calendar import monthrange
from datetime import date
from typing import Callable

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


def executar_pipeline(
    recarga_total: bool = False,
    log_callback: Callable[[str], None] = print,
) -> None:
    """
    Ponto de entrada único para o pipeline financeiro.

    recarga_total=False → janela móvel: hoje − 3 meses até hoje
    recarga_total=True  → histórico completo: 2023-01-01 até hoje
    """
    from app.financeiro import gobi_client as gc
    from app.financeiro import s3_store as store

    hoje = date.today()

    if recarga_total:
        data_ini = date(2023, 1, 1)
        log_callback("🗄️  [FINANCEIRO] RECARGA TOTAL — histórico desde jan/2023.")
    else:
        data_ini = (date(hoje.year, hoje.month, 1) - relativedelta(months=3))
        log_callback(
            f"🔄  [FINANCEIRO] Atualização — últimos 3 meses "
            f"({data_ini.strftime('%m/%Y')} → {hoje.strftime('%m/%Y')})."
        )

    # ── Lista de meses a processar ─────────────────────────────────────
    meses: list[tuple[int, int]] = []
    d = date(data_ini.year, data_ini.month, 1)
    while d <= date(hoje.year, hoje.month, 1):
        meses.append((d.year, d.month))
        d += relativedelta(months=1)

    total_meses = len(meses)
    log_callback(f"   Meses a processar: {total_meses}")

    # ── Extração mês a mês ─────────────────────────────────────────────
    erros: list[str] = []

    for idx, (ano, mes) in enumerate(meses, 1):
        d_ini = date(ano, mes, 1)
        d_fim = date(ano, mes, monthrange(ano, mes)[1])
        prefixo = f"[{idx:02d}/{total_meses}] {ano}/{mes:02d}"

        log_callback(f"   {prefixo} — extraindo notas de saída...")
        try:
            df = gc.extrair_notas_saida(d_ini, d_fim)
            store.salvar_mensal(df, "notas_saida", ano, mes)
            log_callback(f"   {prefixo} ✔ notas_saida ({len(df)} linhas)")
        except Exception as e:
            msg = f"{prefixo} ✘ notas_saida: {e}"
            log_callback(f"   ⚠️  {msg}")
            erros.append(msg)

        log_callback(f"   {prefixo} — extraindo contas a receber...")
        try:
            df = gc.extrair_contas_receber(d_ini, d_fim)
            store.salvar_mensal(df, "contas_receber", ano, mes)
            log_callback(f"   {prefixo} ✔ contas_receber ({len(df)} linhas)")
        except Exception as e:
            msg = f"{prefixo} ✘ contas_receber: {e}"
            log_callback(f"   ⚠️  {msg}")
            erros.append(msg)

        log_callback(f"   {prefixo} — extraindo movimentação bancária...")
        try:
            df = gc.extrair_movimentacao_bancaria(d_ini, d_fim)
            store.salvar_mensal(df, "movimentacao_bancaria", ano, mes)
            log_callback(f"   {prefixo} ✔ movimentacao_bancaria ({len(df)} linhas)")
        except Exception as e:
            msg = f"{prefixo} ✘ movimentacao_bancaria: {e}"
            log_callback(f"   ⚠️  {msg}")
            erros.append(msg)

    # ── Clientes: sempre apaga e re-baixa completo ─────────────────────
    log_callback("   → Atualizando cadastro de clientes (snapshot completo)...")
    try:
        df = gc.extrair_clientes()
        store.salvar_clientes(df)
        log_callback(f"   ✔ clientes ({len(df)} registros)")
    except Exception as e:
        msg = f"clientes: {e}"
        log_callback(f"   ⚠️  {msg}")
        erros.append(msg)

    # ── Resumo final ───────────────────────────────────────────────────
    if erros:
        log_callback(
            f"⚠️  [FINANCEIRO] Concluído com {len(erros)} erro(s):\n"
            + "\n".join(f"     • {e}" for e in erros)
        )
    else:
        modo = "Recarga Total" if recarga_total else "Atualização"
        log_callback(f"✅  [FINANCEIRO] {modo} concluída — {total_meses} meses processados.")