import traceback
import time
import os
import polars as pl
import asyncio # <--- IMPORTANTE: Adicionar isto
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
        
        hoje = date.today()
        data_fim = hoje 
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 4.0 (Arquitetura Delta/Upsert com Sincronia de Histórico)...")

        # =================================================================
        # FASE 1: O CÉREBRO DA CARGA INCREMENTAL (DELTA LOAD)
        # =================================================================
        extractor = GobiExtractor()
        data_inicio = date(2026, 4, 1)

        log(f"📥 [EXTRACT] Extraindo dados do Gobi ERP ({data_inicio} a {data_fim})...")
        lf_150, lf_188, df_seg = await extractor.extrair_tudo(data_inicio, data_fim)
        
        if lf_150.is_empty() and lf_188.is_empty():
            log("⚠️ [SYSTEM] Nenhuma venda ou faturamento encontrado. O pipeline será encerrado sem gerar projeções.")
            AppState.pipeline_rodando = False
            return

        transformer = NexusTransformer()
        lf_silver = transformer.processar_camada_silver(lf_150, lf_188, df_seg)

        loader = NexusLoader()
        
        # BLINDAGEM 1: Mandar o processamento do Polars para thread secundária
        log("   -> Executando processamento em memória (Polars)...")
        df_silver_coletado = await asyncio.to_thread(lf_silver.collect)

        # BLINDAGEM 2: Mandar o Upsert no Banco de Dados para thread secundária
        log("   -> Iniciando injeção no Banco de Dados...")
        await asyncio.to_thread(loader.executar_carga_silver, df_silver_coletado, log_callback=log)
        
        # BLINDAGEM 3: Sincronia Histórica para thread secundária
        await asyncio.to_thread(loader.atualizar_hierarquia_historica, lf_silver, log_callback=log)

        # =================================================================
        # GESTÃO DE CICLOS
        # =================================================================
        hoje = date.today()
        ciclo_atual = hoje.strftime("%m/%Y")
        ciclo_existe = False
        with SessionLocal() as db:
            trava = db.query(func.count(FatoIbpGranular.id)).filter(FatoIbpGranular.ciclo_sop == ciclo_atual).scalar()
            ciclo_existe = (trava > 0)
        
        log(f"   ⏳ Tempo Total FASE 1 (ETL): {time.time() - tempo_inicio_total:.2f}s.")

        # =================================================================
        # FASE 2: INTELIGÊNCIA ARTIFICIAL E S&OP
        # =================================================================
        if ciclo_existe:
            log(f"⏸️ [S&OP] O ciclo {ciclo_atual} já existe no banco de dados.")
            log("   -> A IA e o Rateio foram ignorados para manter ESTÁTICOS os ajustes do Top-Down e Bottom-Up.")
        else:
            forecaster = NexusForecaster()
            t0 = time.time()
            log("🧠 [ML] Novo Mês Detectado! Acordando a IA (Thread Secundária)...")
            
            # BLINDAGEM 4: O CORAÇÃO DO PROBLEMA (Mandar a IA para thread secundária)
            df_forecast = await asyncio.to_thread(forecaster.executar_arena, log_callback=log)
            log(f"✅ [ML] Previsões S&OP concluídas em {time.time() - t0:.2f}s.")

            t0 = time.time()
            log("⏳ [LOAD] Rateando e injetando as metas S&OP no Banco...")
            
            # BLINDAGEM 5: Carga do S&OP no Banco
            await asyncio.to_thread(loader.executar_carga_forecast, df_forecast, ciclo_atual, log_callback=log) 
            log(f"✅ [LOAD] Metas atomizadas com sucesso em {time.time() - t0:.2f}s.")

        tempo_total = time.time() - tempo_inicio_total
        minutos, segundos = divmod(tempo_total, 60)
        AppState.pipeline_rodando = False
        log(f"🏁 [SYSTEM] Pipeline Nexus concluído com sucesso em {int(minutos)}m {int(segundos)}s.")

    except Exception as e:
        AppState.pipeline_rodando = False
        erro_formatado = traceback.format_exc()
        log(f"❌ [ERRO CRÍTICO] Falha no pipeline: {str(e)}")
        log(f"🔍 Detalhes: {erro_formatado}")