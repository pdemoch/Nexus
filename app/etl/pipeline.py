import traceback
import time
import os
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import func
from app.core.state import AppState
from app.core.database import SessionLocal
from app.models.domain_models import FatoVendas
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
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 4.0 (Arquitetura Delta/Upsert)...")

        # =================================================================
        # FASE 1: O CÉREBRO DA CARGA INCREMENTAL (DELTA LOAD)
        # =================================================================
        db = SessionLocal()
        ultima_data_banco = db.query(func.max(FatoVendas.data_pedido)).scalar()
        db.close()

        if ultima_data_banco:
            # Se já tem dados, volta só 5 dias para capturar notas fiscais atrasadas ou editadas
            data_inicio = ultima_data_banco - relativedelta(days=5)
            log(f"⚡ [DELTA LOAD] Banco detectado. Baixando notas emitidas desde {data_inicio.strftime('%d/%m/%Y')}...")
        else:
            # Se o banco está vazio (como agora que dropamos o schema), faz a carga full
            data_inicio = date(2022, 1, 1)                  
            log(f"⚠️ [FULL LOAD] Banco vazio. Iniciando carga histórica profunda desde {data_inicio.strftime('%d/%m/%Y')}...")
        
        extractor = GobiExtractor()
        transformer = NexusTransformer()
        loader = NexusLoader()
        forecaster = NexusForecaster()
        
        # --- EXTRAÇÃO (Gobi API) ---
        t0 = time.time()
        log("⏳ [EXTRACT] Baixando dados brutos do ERP...")
        lf_150, lf_188, df_seg = await extractor.extrair_tudo(data_inicio, data_fim)
        log(f"✅ [EXTRACT] Aquisição concluída em {time.time() - t0:.2f}s.")

        # --- TRANSFORMAÇÃO (Limpeza para o Banco) ---
        t0 = time.time()
        log("⏳ [TRANSFORM] Processando Camada Silver...")
        lf_silver = transformer.processar_camada_silver(lf_150, lf_188, df_seg)
        
        caminho_silver = "data/silver_final.parquet"
        lf_silver.collect(streaming=True).write_parquet(caminho_silver)
        df_silver = pl.read_parquet(caminho_silver)
        log(f"✅ [TRANSFORM] Lote tratado gerado em {time.time() - t0:.2f}s.")
        
        # --- CARGA SILVER (O Upsert no PostgreSQL) ---
        t0 = time.time()
        log("⏳ [LOAD] Injetando Fato_Vendas no Banco de Dados (Upsert)...")
        loader.executar_carga_silver(df_silver, log_callback=log)
        log(f"✅ [LOAD] Vendas gravadas em {time.time() - t0:.2f}s.")

        # =================================================================
        # FASE 2: INTELIGÊNCIA ARTIFICIAL E S&OP
        # =================================================================
        
        # --- MACHINE LEARNING (Lendo direto do DB) ---
        t0 = time.time()
        log("🧠 [ML] Acordando a IA para ler a história completa no Banco...")
        df_forecast = forecaster.executar_arena(log_callback=log)
        log(f"✅ [ML] Previsões S&OP concluídas em {time.time() - t0:.2f}s.")

        # --- CARGA S&OP (Distribuição Granular) ---
        t0 = time.time()
        log("⏳ [LOAD] Rateando e injetando as metas S&OP no Banco...")
        loader.executar_carga_forecast(df_forecast, df_silver, log_callback=log)
        log(f"✅ [LOAD] Metas atomizadas com sucesso em {time.time() - t0:.2f}s.")

        tempo_total = time.time() - tempo_inicio_total
        minutos, segundos = divmod(tempo_total, 60)
        AppState.pipeline_rodando = False
        log(f"🏁 [SYSTEM] Pipeline Nexus 4.0 concluído com sucesso! (Tempo: {int(minutos)}m {int(segundos)}s)")
        
    except Exception as e:
        AppState.pipeline_rodando = False
        erro_msg = traceback.format_exc()
        log(f"❌ [ERRO FATAL] O motor colapsou: {str(e)}")
        print(erro_msg)