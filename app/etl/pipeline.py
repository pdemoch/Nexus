import asyncio
from datetime import date
from dateutil.relativedelta import relativedelta

from app.core.database import SessionLocal
from app.models.domain_models import FatoIbpGranular
from sqlalchemy import text

from app.etl.extractor import GobiExtractor
from app.etl.transformer import NexusTransformer
from app.etl.loader import NexusLoader
from app.ml.forecaster import NexusForecaster


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


async def executar_pipeline_nexus(ciclo_alvo: str, log_callback=print):
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

        # Janela deslizante de 3 meses
        data_fim = date.today()
        data_inicio = (data_fim - relativedelta(months=3)).replace(day=1)

        # A. Extração Original
        lf_150, lf_188, df_seg, df_orc = await extrator.extrair_tudo(data_inicio, data_fim)

        # B. Extração de Estoque D0 (API 90)
        lf_90 = await extrator.extrair_estoque_90()

        # C. Transformações (Camada Silver e Estoque)
        lf_silver, lf_clientes, df_orc_final = transformer.processar_camada_silver(lf_150, lf_188, df_seg, df_orc)
        lf_estoque_d0 = transformer.processar_estoque_d0(lf_90)

        # D. Cargas no Banco (Se houver dados retornados do cruzamento)
        if lf_silver is not None:
            # Coleta as queries 'preguiçosas' (LazyFrames) do Polars para a RAM
            df_silver_coletado = lf_silver.collect()
            df_clientes_coletado = lf_clientes.collect()

            # Carga Drop & Replace de Vendas e Orçamento + UPSERT de Clientes
            loader.executar_carga_clientes(df_clientes_coletado, log_callback=log_callback)
            loader.executar_carga_silver(df_silver_coletado, data_inicio, log_callback=log_callback)
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

    except Exception as e:
        log_callback(f"❌ [ERRO CRÍTICO] Falha no pipeline: {e}")
        raise e