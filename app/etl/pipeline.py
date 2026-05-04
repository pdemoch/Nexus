import traceback
import time
import os
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import func
from app.core.state import AppState
from app.core.database import SessionLocal
from app.models.domain_models import FatoVendas, FatoIbpGranular
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
        
        # 1. MUDANÇA: O teto agora é HOJE (captura vendas em tempo real do dia atual)
        hoje = date.today()
        data_fim = hoje 
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 4.0 (Arquitetura Delta/Upsert)...")

        # =================================================================
        # FASE 1: O CÉREBRO DA CARGA INCREMENTAL (DELTA LOAD MÊS DESLIZANTE)
        # =================================================================
        db = SessionLocal()
        ultima_data_banco = db.query(func.max(FatoVendas.data_pedido)).scalar()
        
        # CHECAGEM DE BLINDAGEM S&OP: Verifica se o ciclo atual já foi gerado
        ciclo_atual = date.today().strftime("%m/%Y")
        ciclo_existe = db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo_atual).first()
        db.close()

        if ultima_data_banco:
            # 2. MUDANÇA: Lógica do Mês Deslizante (Rolling Month)
            # Se estamos nos primeiros 5 dias do mês (ex: 3 de Maio), volta para 1º do mês anterior (1º de Abril)
            # para capturar notas atrasadas do fechamento. Senão, volta para 1º do mês atual (1º de Maio).
            if hoje.day <= 5:
                data_inicio = (hoje.replace(day=1) - relativedelta(months=1))
            else:
                data_inicio = hoje.replace(day=1)
                
            log(f"⚡ [DELTA LOAD] Atualizando mês vigente e atrasos. Baixando notas desde {data_inicio.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')}...")
        else:
            data_inicio = date(2022, 1, 1)                  
            log(f"⚠️ [FULL LOAD] Banco vazio. Iniciando carga histórica profunda desde {data_inicio.strftime('%d/%m/%Y')} até {data_fim.strftime('%d/%m/%Y')}...")
        
        extractor = GobiExtractor()
        transformer = NexusTransformer()
        loader = NexusLoader()
        forecaster = NexusForecaster()
        
        # --- EXTRAÇÃO ---
        t0 = time.time()
        log("⏳ [EXTRACT] Baixando dados brutos do ERP...")
        lf_150, lf_188, df_seg = await extractor.extrair_tudo(data_inicio, data_fim)
        log(f"✅ [EXTRACT] Aquisição concluída em {time.time() - t0:.2f}s.")

        # --- TRANSFORMAÇÃO ---
        t0 = time.time()
        log("⏳ [TRANSFORM] Processando Camada Silver...")
        lf_silver = transformer.processar_camada_silver(lf_150, lf_188, df_seg)
        
        caminho_silver = "data/silver_final.parquet"
        lf_silver.collect(streaming=True).write_parquet(caminho_silver)
        df_silver = pl.read_parquet(caminho_silver)
        log(f"✅ [TRANSFORM] Lote tratado gerado em {time.time() - t0:.2f}s.")
        
        # --- CARGA SILVER (Fato Vendas) ---
        t0 = time.time()
        log("⏳ [LOAD] Injetando Fato_Vendas no Banco de Dados (Upsert)...")
        loader.executar_carga_silver(df_silver, log_callback=log)
        log(f"✅ [LOAD] Vendas gravadas em {time.time() - t0:.2f}s.")

        # =================================================================
        # FASE 2: INTELIGÊNCIA ARTIFICIAL E S&OP (AGORA BLINDADA)
        # =================================================================
        if ciclo_existe:
            log(f"⏸️ [S&OP] O ciclo {ciclo_atual} já existe no banco de dados.")
            log("   -> A IA e o Rateio foram ignorados para manter ESTÁTICOS os ajustes do Top-Down e Bottom-Up.")
        else:
            t0 = time.time()
            log("🧠 [ML] Novo Mês Detectado! Acordando a IA para ler a história e prever...")
            df_forecast = forecaster.executar_arena(log_callback=log)
            log(f"✅ [ML] Previsões S&OP concluídas em {time.time() - t0:.2f}s.")

            t0 = time.time()
            log("⏳ [LOAD] Rateando e injetando as metas S&OP no Banco...")
            # Passando o ciclo_atual que foi definido no topo do arquivo
            loader.executar_carga_forecast(df_forecast, ciclo_atual, log_callback=log) 
            log(f"✅ [LOAD] Metas atomizadas com sucesso em {time.time() - t0:.2f}s.")

        tempo_total = time.time() - tempo_inicio_total
        minutos, segundos = divmod(tempo_total, 60)
        AppState.pipeline_rodando = False
        log(f"🏁 [SYSTEM] Pipeline Nexus concluído com sucesso! (Tempo: {int(minutos)}m {int(segundos)}s)")
        
    except Exception as e:
        AppState.pipeline_rodando = False
        erro_msg = traceback.format_exc()
        log(f"❌ [ERRO FATAL] O motor colapsou: {str(e)}")
        print(erro_msg)