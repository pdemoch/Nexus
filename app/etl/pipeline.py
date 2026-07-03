import asyncio
from datetime import date
from dateutil.relativedelta import relativedelta

from app.core.database import SessionLocal
from app.models.domain_models import FatoIbpGranular

from app.etl.extractor import GobiExtractor
from app.etl.transformer import NexusTransformer
from app.etl.loader import NexusLoader
from app.ml.forecaster import NexusForecaster

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
        
        # B. NOVA Extração de Estoque D0 (API 90)
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
            loader.executar_carga_silver(df_silver_coletado, data_inicio, log_callback=log_callback)
            loader.executar_carga_clientes(df_clientes_coletado, log_callback=log_callback)
            loader.executar_carga_orcamento(df_orc_final, log_callback=log_callback)
            
            # E. NOVA Carga de Estoque
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
            log_callback("✅ Sincronização de Dados finalizada com sucesso.")
            return  # <-- ISTO PARA O PIPELINE AQUI.

        # ====================================================================
        # 3. PREVISÃO DA IA E CARGA IBP (Só roda se for ciclo novo)
        # ====================================================================
        log_callback("\n🧠 [FORECASTER] Iniciando Arena de Modelos (Base de 3 Anos)...")
        df_forecast = await asyncio.to_thread(forecaster.executar_arena, ciclo_alvo, log_callback=log_callback)
        
        log_callback("\n🔀 [LOADER] Fatiando Share (6m), calculando PMV (3m) e Injetando no Banco...")
        await asyncio.to_thread(loader.executar_carga_forecast, df_forecast, ciclo_alvo, log_callback)
        
        log_callback("\n✅ Pipeline Executado com Sucesso Absoluto!")

    except Exception as e:
        log_callback(f"❌ [ERRO CRÍTICO] Falha no pipeline: {e}")
        raise e