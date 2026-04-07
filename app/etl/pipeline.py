import traceback
from datetime import date
from dateutil.relativedelta import relativedelta
import polars as pl

from app.core.state import AppState
from app.etl.extractor import GobiExtractor
from app.etl.transformer import NexusTransformer
from app.etl.loader import NexusLoader
from app.ml.forecaster import NexusForecaster

def log(mensagem: str):
    """Grava o log no estado global para o React consumir em tempo real."""
    from datetime import datetime
    hora = datetime.now().strftime('%H:%M:%S')
    linha_log = f"[{hora}] {mensagem}"
    AppState.logs.append(linha_log)
    print(linha_log)

async def executar_pipeline_nexus():
    try:
        hoje = date.today()
        data_inicio = date(2022, 1, 1)                  
        data_fim = hoje - relativedelta(days=1)
        
        log("✅ [SYSTEM] Iniciando Nexus Pipeline Oficial...")
        
        extractor = GobiExtractor()
        transformer = NexusTransformer()
        forecaster = NexusForecaster()
        loader = NexusLoader()
        
        log("⏳ [EXTRACT] Conectando ao ERP Linea (APIs 150, 188 e Portfólio)...")
        df_150, df_188, df_seg = await extractor.extrair_tudo(data_inicio, data_fim)
        
        if df_150.is_empty():
            log("❌ [EXTRACT] Falha: API 150 não retornou dados. Abortando.")
            return

        log("⏳ [TRANSFORM] Aplicando regras da Camada Silver e limpando inativos...")
        df_silver = transformer.processar_camada_silver(df_150, df_188, df_seg)
        df_ia = transformer.preparar_camada_ia(df_silver)
        
        log("🧠 [MACHINE LEARNING] Iniciando Arena de Modelos Preditivos...")
        df_forecast = forecaster.executar_arena(df_ia, log_callback=log)

        log("⏳ [LOAD] Injetando Fatos e Dimensões no banco local SQLite...")
        loader.executar_carga_silver(df_silver)
        loader.executar_carga_forecast(df_forecast, df_silver)
        
        log("🎯 [SYSTEM] Pipeline 100% Concluído. A plataforma está pronta para o S&OP.")

    except Exception as e:
        log(f"❌ [ERRO CRÍTICO] Falha na execução: {str(e)}")
        traceback.print_exc() 
    finally:
        AppState.pipeline_rodando = False