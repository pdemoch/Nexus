import traceback
import time
import os
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from app.core.state import AppState
from app.etl.extractor import GobiExtractor
from app.etl.transformer import NexusTransformer
from app.etl.loader import NexusLoader
from app.ml.forecaster import NexusForecaster

def log(mensagem: str):
    from datetime import datetime
    hora = datetime.now().strftime('%H:%M:%S')
    linha_log = f"[{hora}] {mensagem}"
    AppState.logs.append(linha_log)
    print(linha_log)

async def executar_pipeline_nexus():
    tempo_inicio_total = time.time()
    try:
        os.makedirs("data", exist_ok=True)
        
        hoje = date.today()
        # DE VOLTA AOS 4 ANOS - JANELA DO NEGÓCIO MANTIDA!
        data_inicio = date(2022, 1, 1)                  
        data_fim = hoje - relativedelta(days=1)
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 3.0 (Arquitetura OOC / Streaming)...")
        log(f"📅 [SYSTEM] Janela de processamento: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}")
        
        extractor = GobiExtractor()
        transformer = NexusTransformer()
        forecaster = NexusForecaster()
        loader = NexusLoader()
        
        # --- EXTRAÇÃO ---
        t0 = time.time()
        log("⏳ [EXTRACT] Baixando dados do ERP e armazenando em lotes no disco...")
        lf_150, lf_188, df_seg = await extractor.extrair_tudo(data_inicio, data_fim)
        log(f"✅ [EXTRACT] Aquisição concluída em {time.time() - t0:.2f}s.")

        # --- TRANSFORMAÇÃO ---
        t0 = time.time()
        log("⏳ [TRANSFORM] Limpando Camada Silver e descartando histórico de itens inativos (Inner Join)...")
        lf_silver = transformer.processar_camada_silver(lf_150, lf_188, df_seg)
        
        caminho_silver = "data/silver_final.parquet"
        
        # O SEGREDO DO OOM: O Polars processa os dados aos pedaços e salva o resultado final no disco!
        lf_silver.collect(streaming=True).write_parquet(caminho_silver)
        log(f"✅ [TRANSFORM] Matriz consolidada gerada no disco em {time.time() - t0:.2f}s.")
        
        # Carregamos a matriz já reduzida pelas suas regras de negócio para a memória
        df_silver = pl.read_parquet(caminho_silver)
        df_ia = transformer.preparar_camada_ia(df_silver)
        
        # --- MACHINE LEARNING ---
        t0 = time.time()
        log("🧠 [ML] Acordando Redes Neurais e Modelos Preditivos...")
        df_forecast = forecaster.executar_arena(df_ia, log_callback=log)
        log(f"✅ [ML] Previsões globais concluídas em {time.time() - t0:.2f}s.")

        # --- CARGA (LOAD) ---
        t0 = time.time()
        log("⏳ [LOAD] Preparando injeção no Banco de Dados (PostgreSQL AWS)...")
        
        log("   -> [ETAPA 1/2] Atualizando Dimensões e Fatos (Silver)...")
        loader.executar_carga_silver(df_silver, log_callback=log)
        
        log("   -> [ETAPA 2/2] Calculando Rateio Atômico e distribuindo Inteligência (S&OP)...")
        loader.executar_carga_forecast(df_forecast, df_silver, log_callback=log)
        log(f"✅ [LOAD] Gravação finalizada em {time.time() - t0:.2f}s.")

        tempo_total = time.time() - tempo_inicio_total
        minutos, segundos = divmod(tempo_total, 60)
        AppState.pipeline_rodando = False
        log(f"🏁 [SYSTEM] Pipeline S&OP concluído com sucesso! (Tempo total: {int(minutos)}m {int(segundos)}s)")
        
    except Exception as e:
        AppState.pipeline_rodando = False
        erro_msg = traceback.format_exc()
        log(f"❌ [ERRO FATAL] O motor colapsou: {str(e)}")
        print(erro_msg)