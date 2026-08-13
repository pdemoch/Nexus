import asyncio
from datetime import date
from dateutil.relativedelta import relativedelta

from app.core.database import SessionLocal
from app.core.state import AppState
from app.models.domain_models import FatoIbpGranular
from sqlalchemy import text

from app.etl.extractor import GobiExtractor
from app.etl.transformer import NexusTransformer
from app.etl.loader import NexusLoader
from app.ml.forecaster import NexusForecaster

# =========================================================================
# JANELA DE RECARGA — ÚNICO PONTO DE VERDADE
# O loader apaga (DELETE FROM fato_vendas WHERE data_pedido >= data_inicio) e
# reinsere o que o transformer deixar passar. As duas pontas TÊM que usar esta
# mesma data, por isso ela é calculada aqui e PASSADA adiante — nunca
# recalculada dentro do transformer.
# =========================================================================
JANELA_MESES = 5


def _auditar_pmv_zerado(ciclo_alvo: str, log_callback=print):
    """
    Sentinela do PMV. Conta linhas do ciclo que continuaram sem preço após a
    precificação. Preço zerado NAO e inofensivo: a linha entra no plano com
    volume mas receita ZERO, subestimando o faturamento e distorcendo toda
    comparacao com orcamento. Se isto disparar, algum SKU nao tem venda valida
    (qt>0 e vl>0) em lugar nenhum e precisa de preco manual.
    """
    try:
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE COALESCE(pmv_aplicado, 0) = 0) AS zerado
                FROM fato_ibp_granular
                WHERE ciclo_sop = :c
            """), {"c": ciclo_alvo}).fetchone()

            total = int(row.total or 0)
            zerado = int(row.zerado or 0)

            if zerado == 0:
                log_callback(f"✅ [PMV-AUDIT] Todas as {total} linhas do ciclo {ciclo_alvo} têm preço.")
                return

            pct = (100.0 * zerado / total) if total else 0.0
            log_callback(
                f"⚠️ [PMV-AUDIT] ATENÇÃO: {zerado} de {total} linhas ({pct:.1f}%) "
                f"do ciclo {ciclo_alvo} continuam SEM PREÇO (pmv_aplicado = 0). "
                f"Estas linhas entram no plano com receita ZERO e subestimam o faturamento."
            )

            skus = db.execute(text("""
                SELECT DISTINCT sku FROM fato_ibp_granular
                WHERE ciclo_sop = :c AND COALESCE(pmv_aplicado, 0) = 0
                LIMIT 10
            """), {"c": ciclo_alvo}).fetchall()
            if skus:
                lista = ", ".join(str(s.sku) for s in skus)
                log_callback(f"⚠️ [PMV-AUDIT] SKUs sem preço (amostra): {lista}")
    except Exception as e:
        log_callback(f"⚠️ [PMV-AUDIT] Falha ao auditar PMV: {e}")


async def executar_pipeline_nexus(ciclo_alvo: str, log_callback=print,
                                  modo_recarga_total: bool = False):
    """
    modo_recarga_total=True  →  uso único para reprocessamento histórico completo.
        - data_inicio forçada para jan/2023 (início do histórico disponível na API 150)
        - fato_vendas inteira é apagada via TRUNCATE antes da reinserção
        - trava de segurança elevada para 50.000 linhas (volume esperado em 3+ anos)
        - após executar, voltar para modo_recarga_total=False (padrão)

    modo_recarga_total=False →  comportamento normal, janela deslizante de 5 meses.
    """
    extrator = GobiExtractor()
    transformer = NexusTransformer()
    loader = NexusLoader()
    forecaster = NexusForecaster()

    try:
        log_callback(f"🚀 Iniciando Pipeline Nexus (Ciclo: {ciclo_alvo})")

        # ====================================================================
        # 1. ETL Clássico (Extração e Sincronização)
        # ====================================================================
        log_callback("⏳ [EXTRACT/TRANSFORM] Sincronizando ERP...")

        data_fim = date.today()

        if modo_recarga_total:
            # ----------------------------------------------------------------
            # MODO RECARGA TOTAL — uso único, janeiro/2023 → hoje
            # Apaga TODA a fato_vendas e reinsere o histórico completo sem
            # nenhum filtro de canal (op.51 / fifeiro / EIC já foram removidos
            # do transformer permanentemente em 2026-08).
            # ----------------------------------------------------------------
            data_inicio = date(2023, 1, 1)
            trava_minima = 50_000
            log_callback(
                f"🔴 [RECARGA TOTAL] Modo histórico completo ativado. "
                f"Extraindo jan/2023 → {data_fim}. "
                f"TODA a fato_vendas será apagada (TRUNCATE) e reinserida."
            )
        else:
            # ----------------------------------------------------------------
            # MODO NORMAL — janela deslizante de JANELA_MESES meses
            # ----------------------------------------------------------------
            data_inicio = (data_fim - relativedelta(months=JANELA_MESES)).replace(day=1)
            trava_minima = 1_000
            log_callback(f"🗓️ [JANELA] Recarga de {JANELA_MESES} meses: "
                         f"{data_inicio} até {data_fim}. Tudo a partir de {data_inicio} "
                         f"será APAGADO da fato_vendas e reinserido do ERP.")

        # A. Extração Original
        lf_150, lf_188, df_seg, df_orc = await extrator.extrair_tudo(data_inicio, data_fim)

        # B. Extração de Estoque D0 (API 90)
        lf_90 = await extrator.extrair_estoque_90()

        # C. Transformações (Camada Silver e Estoque)
        # data_inicio é PASSADA: o transformer não recalcula a janela.
        lf_silver, lf_clientes, df_orc_final = transformer.processar_camada_silver(
            lf_150, lf_188, df_seg, df_orc, data_inicio_janela=data_inicio)
        lf_estoque_d0 = transformer.processar_estoque_d0(lf_90)

        # D. Cargas no Banco (Se houver dados retornados do cruzamento)
        if lf_silver is not None:
            # Coleta as queries 'preguiçosas' (LazyFrames) do Polars para a RAM
            df_silver_coletado = lf_silver.collect()
            df_clientes_coletado = lf_clientes.collect()

            # Carga Drop & Replace de Vendas e Orçamento + UPSERT de Clientes
            # dim_produtos PRIMEIRO: popula a dimensao com os SKUs que aparecem nas
            # VENDAS (enriquecidos pelo Segmentos), para que a FK fato_vendas_sku_fkey
            # nunca falhe. O produto nasce da venda; o Segmentos so enriquece.
            loader.executar_carga_produtos(df_silver_coletado, df_seg, log_callback=log_callback)
            loader.executar_carga_clientes(df_clientes_coletado, log_callback=log_callback)

            # TRAVA DE SEGURANÇA: o DELETE/TRUNCATE do loader é irreversível.
            # trava_minima é 1.000 no modo normal (5 meses) e 50.000 na recarga
            # total (3+ anos) — escala com o volume esperado de cada modo.
            linhas_silver = df_silver_coletado.height
            if linhas_silver < trava_minima:
                raise ValueError(
                    f"[SEGURANÇA] Só {linhas_silver} linhas de venda vieram do ERP "
                    f"(mínimo esperado: {trava_minima}). Carga abortada para não "
                    f"destruir a fato_vendas com dados incompletos. Verifique a API 150."
                )
            loader.executar_carga_silver(df_silver_coletado, data_inicio,
                                         log_callback=log_callback,
                                         recarga_total=modo_recarga_total)
            loader.executar_carga_orcamento(df_orc_final, log_callback=log_callback)

            # E. Carga de Estoque
            if lf_estoque_d0 is not None:
                loader.executar_carga_estoque(lf_estoque_d0.collect(), log_callback=log_callback)
        else:
            log_callback("⚠️ [AVISO] O cruzamento com o portfólio não retornou dados nesta rodada.")

        # ====================================================================
        # 2. VERIFICAÇÃO DE SEGURANÇA DO CICLO (A TRAVA S&OP)
        # ====================================================================
        with SessionLocal() as db:
            ciclo_existe = db.query(FatoIbpGranular.id).filter(FatoIbpGranular.ciclo_sop == ciclo_alvo).first()

        if ciclo_existe:
            log_callback(f"🛡️ [SECURITY] O ciclo {ciclo_alvo} já existe! IA e Rateio abortados para proteger o S&OP atual.")

            # PRECIFICAÇÃO SEMPRE RODA — mesmo em ciclo existente.
            # Ela é idempotente (Passo 1 só toca pares com venda própria; Passo 2
            # só preenche linhas com pmv=0), então nunca sobrescreve preço bom.
            # Rodar aqui garante que pares NOVOS (cliente/produto que entraram no
            # plano depois do nascimento do ciclo) não fiquem com preço zerado —
            # o que zeraria a receita deles e distorceria todo o S&OP em silêncio.
            log_callback("\n💰 [PMV] Reprecificando (idempotente) para cobrir pares sem preço...")
            await asyncio.to_thread(loader.executar_precificacao_ciclo, ciclo_alvo, log_callback)

            # Sentinela: alerta se sobrou alguma linha sem preço.
            await asyncio.to_thread(_auditar_pmv_zerado, ciclo_alvo, log_callback)

            log_callback("✅ Sincronização de Dados finalizada com sucesso.")
            return  # IA e Rateio permanecem bloqueados (protege o S&OP em curso).

        # ====================================================================
        # 3. PREVISÃO DA IA, RATEIO E PRECIFICAÇÃO (Só roda se for ciclo novo)
        # ====================================================================
        log_callback("\n🧠 [FORECASTER] Iniciando Arena de Modelos (Base de 3 Anos)...")
        df_forecast = await asyncio.to_thread(forecaster.executar_arena, ciclo_alvo, log_callback=log_callback)

        log_callback("\n🔀 [LOADER] Fatiando Share (6m) e injetando volumes no Banco...")
        await asyncio.to_thread(loader.executar_carga_forecast, df_forecast, ciclo_alvo, log_callback)

        log_callback("\n💰 [PMV] Definindo preços pela última venda (base de precificação global)...")
        await asyncio.to_thread(loader.executar_precificacao_ciclo, ciclo_alvo, log_callback)

        # Sentinela: nenhuma linha pode ficar sem preço (receita zero silenciosa).
        await asyncio.to_thread(_auditar_pmv_zerado, ciclo_alvo, log_callback)

        log_callback("\n✅ Pipeline Executado com Sucesso Absoluto!")

    except BaseException as e:
        # BaseException e não Exception: pyo3_runtime.PanicException (pânico do
        # Rust vindo do polars) herda de BaseException justamente para não ser
        # engolido por except genérico. Com `except Exception`, a falha de schema
        # da API 188 encerrou o pipeline SEM mensagem no painel — só apareceu a
        # linha do finally.
        log_callback(f"❌ [ERRO CRÍTICO] Falha no pipeline: {type(e).__name__}: {e}")
        raise
    finally:
        # GARANTIA DE DESTRAVAMENTO: o flag global e desligado em QUALQUER
        # caminho de saida — sucesso completo, abort do ciclo existente (return
        # antecipado) ou excecao. Sem isto, o painel fica preso em "PROCESSANDO"
        # para sempre e o front faz polling infinito (era o bug do trave).
        AppState.pipeline_rodando = False
        log_callback("🏁 [PIPELINE] Execução encerrada — sistema liberado.")