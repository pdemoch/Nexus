import asyncio
from datetime import date
from dateutil.relativedelta import relativedelta

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
        
        # ====================================================================
        # 1. ETL Clássico (Extração e Sincronização dos 3 Meses Recentes)
        # ====================================================================
        log_callback("⏳ [EXTRACT/TRANSFORM] Sincronizando ERP...")
        
        # Definir a janela temporal de extração de forma dinâmica
        data_fim = date.today()
        data_inicio = (data_fim - relativedelta(months=3)).replace(day=1)
        
        # 1.1 Extração Bruta (Gobi)
        lf_150, lf_188, df_seg, df_orc = await extrator.extrair_tudo(data_inicio, data_fim)
        
        # 1.2 Transformação (Limpeza, Filtros e Cruzamentos)
        lf_silver, lf_clientes, df_orc_final = transformer.processar_camada_silver(lf_150, lf_188, df_seg, df_orc)
        
        # 1.3 Carga no PostgreSQL
        if lf_silver is not None:
            # A. Gravar Histórico de Vendas
            df_silver_coletado = lf_silver.collect() 
            loader.executar_carga_silver(df_silver_coletado, data_inicio, log_callback=log_callback)
            
            # B. Gravar Cadastro Atualizado de Clientes
            df_clientes_coletado = lf_clientes.collect()
            loader.executar_carga_clientes(df_clientes_coletado, log_callback=log_callback)
            
            # C. Gravar Metas Financeiras (Orçamento)
            # Nota: O df_orc_final já é um DataFrame comum (não Lazy), não precisa de .collect()
            loader.executar_carga_orcamento(df_orc_final, log_callback=log_callback)
        else:
            log_callback("⚠️ [AVISO] O cruzamento com o portfólio não retornou dados nesta rodada.")
            
        # ====================================================================
        # 2. PREVISÃO DA IA (Apenas SKU)
        # ====================================================================
        log_callback("\n🧠 [FORECASTER] Iniciando Arena de Modelos (Base de 3 Anos)...")
        df_forecast = await asyncio.to_thread(forecaster.executar_arena, ciclo_alvo, log_callback=log_callback)
        
        # ====================================================================
        # 3. RATEIO, PMV E CARGA IBp GRANULAR
        # ====================================================================
        log_callback("\n🔀 [DISTRIBUTOR] Fatiando Share (6m), calculando PMV (3m) e Injetando no Banco...")
        await asyncio.to_thread(loader.executar_carga_forecast, df_forecast, ciclo_alvo, log_callback)
        
        log_callback("\n✅ Pipeline Executado com Sucesso Absoluto!")

    except Exception as e:
        log_callback(f"❌ [ERRO CRÍTICO] Falha no pipeline: {e}")
        raise e