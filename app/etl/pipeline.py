import traceback
from datetime import date
from dateutil.relativedelta import relativedelta
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
        # Janela de extração de dados
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
            AppState.pipeline_rodando = False
            return

        log("⏳ [TRANSFORM] Aplicando regras da Camada Silver e limpando inativos...")
        df_silver = transformer.processar_camada_silver(df_150, df_188, df_seg)
        df_ia = transformer.preparar_camada_ia(df_silver)
        
        log("🧠 [MACHINE LEARNING] Iniciando Arena de Modelos Preditivos...")
        # A IA prevê os volumes a nível de SKU
        df_forecast = forecaster.executar_arena(df_ia, log_callback=log)

        log("⏳ [LOAD] Injetando Fatos e Dimensões no banco local SQLite...")
        loader.executar_carga_silver(df_silver)
        
        log("🌊 [RATEIO] Distribuindo Inteligência Artificial para Lojas (CGCs)...")
        # A GRANDE MUDANÇA: Passamos o df_silver (Histórico de Vendas) junto com o df_forecast (Previsão).
        # O Loader vai olhar os últimos 12 meses do df_silver e ratear a IA loja a loja!
        loader.executar_carga_forecast(df_forecast, df_silver)

        AppState.pipeline_rodando = False
        log("🏁 [SYSTEM] Pipeline concluído com sucesso!")
        
    except Exception as e:
        AppState.pipeline_rodando = False
        erro_msg = traceback.format_exc()
        log(f"❌ [ERRO CRÍTICO] Falha na execução: {str(e)}")
        print(erro_msg)