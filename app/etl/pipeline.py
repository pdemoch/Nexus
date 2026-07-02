import asyncio
from app.etl.extractor import GobiExtractor
from app.etl.transformer import NexusTransformer
from app.etl.loader import NexusLoader
from app.ml.forecaster import NexusForecaster
from app.ml.distributor import TopDownDistributor

async def executar_pipeline_nexus(ciclo_alvo: str, log_callback=print):
    extrator = GobiExtractor()
    transformer = NexusTransformer()
    loader = NexusLoader()
    forecaster = NexusForecaster()
    distributor = TopDownDistributor()
    
    try:
        log_callback(f"🚀 Iniciando Pipeline Nexus (Ciclo: {ciclo_alvo})")
        
        # 1. ETL Clássico (Os 3 meses recentes)
        log_callback("⏳ [EXTRACT/TRANSFORM] Sincronizando ERP...")
        arquivos_csv = await extrator.executar_extracao(log_callback=log_callback)
        df_historico_limpo = transformer.processar_faturamento(arquivos_csv, log_callback=log_callback)
        loader.executar_carga_historico(df_historico_limpo, log_callback=log_callback)

        # 2. PREVISÃO DA IA (Apenas SKU)
        log_callback("\n🧠 [FORECASTER] Iniciando Arena de Modelos (Base de 3 Anos)...")
        df_forecast = await asyncio.to_thread(forecaster.executar_arena, ciclo_alvo, log_callback=log_callback)
        
        # 3. RATEIO, PMV E CARGA (Tudo num único passo atômico)
        log_callback("\n🔀 [DISTRIBUTOR] Fatiando Share (6m), calculando PMV (3m) e Injetando no Banco...")
        await asyncio.to_thread(distributor.executar_rateio_e_carga, df_forecast, ciclo_alvo)
        
        log_callback("\n✅ Pipeline Executado com Sucesso Absoluto!")

    except Exception as e:
        log_callback(f"❌ [ERRO CRÍTICO] Falha no pipeline: {e}")
        raise e