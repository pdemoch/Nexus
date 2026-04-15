import traceback
import time
import os
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import func # Adicionado para ler o banco
from app.core.state import AppState
from app.core.database import SessionLocal # Adicionado para conexão
from app.models.domain_models import FatoVendas # Adicionado para buscar a última data
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
        data_fim = hoje - relativedelta(days=1)
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 3.0 (Arquitetura Delta/OOC)...")

        # =================================================================
        # O CÉREBRO DA CARGA INCREMENTAL (DELTA LOAD)
        # =================================================================
        db = SessionLocal()
        ultima_data_banco = db.query(func.max(FatoVendas.data_pedido)).scalar()
        db.close()

        if ultima_data_banco:
            # Se já tem dados, volta só 5 dias para capturar notas fiscais atrasadas
            data_inicio = ultima_data_banco - relativedelta(days=5)
            log(f"⚡ [DELTA LOAD] Banco existente detetado. Baixando apenas novos dados desde {data_inicio.strftime('%d/%m/%Y')}...")
        else:
            # Se o banco está vazio, faz a carga histórica completa
            data_inicio = date(2022, 1, 1)                  
            log(f"⚠️ [FULL LOAD] Banco vazio. Iniciando carga histórica profunda desde {data_inicio.strftime('%d/%m/%Y')}...")
        
        extractor = GobiExtractor()
        transformer = NexusTransformer()
        forecaster = NexusForecaster()
        loader = NexusLoader()
        
        # --- EXTRAÇÃO ---
        t0 = time.time()
        log("⏳ [EXTRACT] Baixando dados do ERP...")
        lf_150, lf_188, df_seg = await extractor.extrair_tudo(data_inicio, data_fim)
        log(f"✅ [EXTRACT] Aquisição concluída em {time.time() - t0:.2f}s.")

        # --- TRANSFORMAÇÃO ---
        t0 = time.time()
        log("⏳ [TRANSFORM] Processando Camada Silver...")
        lf_silver = transformer.processar_camada_silver(lf_150, lf_188, df_seg)
        
        caminho_silver = "data/silver_final.parquet"
        lf_silver.collect(streaming=True).write_parquet(caminho_silver)
        log(f"✅ [TRANSFORM] Matriz gerada em {time.time() - t0:.2f}s.")
        
        df_silver = pl.read_parquet(caminho_silver)
        df_ia = transformer.preparar_camada_ia(df_silver)
        
        # --- MACHINE LEARNING ---
        t0 = time.time()
        log("🧠 [ML] Treinando Redes Neurais e Modelos Preditivos...")
        df_forecast = forecaster.executar_arena(df_ia, log_callback=log)
        log(f"✅ [ML] Previsões concluídas em {time.time() - t0:.2f}s.")

        # --- CARGA (LOAD) ---
        t0 = time.time()
        log("⏳ [LOAD] Injetando no Banco de Dados (PostgreSQL)...")
        loader.executar_carga_silver(df_silver, log_callback=log)
        loader.executar_carga_forecast(df_forecast, df_silver, log_callback=log)
        log(f"✅ [LOAD] Gravação finalizada em {time.time() - t0:.2f}s.")

        tempo_total = time.time() - tempo_inicio_total
        minutos, segundos = divmod(tempo_total, 60)
        AppState.pipeline_rodando = False
        log(f"🏁 [SYSTEM] Pipeline S&OP concluído com sucesso! (Tempo: {int(minutos)}m {int(segundos)}s)")
        
    except Exception as e:
        AppState.pipeline_rodando = False
        erro_msg = traceback.format_exc()
        log(f"❌ [ERRO FATAL] O motor colapsou: {str(e)}")
        print(erro_msg)