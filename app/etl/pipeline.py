import traceback
import time
import os
import polars as pl
import asyncio
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
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 4.0 (Arquitetura Delta/Upsert com Orçamento Base)...")

        extractor = GobiExtractor()
        data_inicio = date(2026, 4, 1)

        log(f"📥 [EXTRACT] Extraindo dados do Gobi ERP ({data_inicio} a {data_fim})...")
        lf_150, lf_188, df_seg, df_orc = await extractor.extrair_tudo(data_inicio, data_fim)
        
        if len(lf_150.columns) == 0 or len(lf_188.columns) == 0:
            log("⚠️ [SYSTEM] Arquivos de Vendas ou Faturamento vazios. O pipeline será encerrado por segurança.")
            AppState.pipeline_rodando = False
            return

        transformer = NexusTransformer()
        
        lf_silver, lf_clientes, df_orc_final = transformer.processar_camada_silver(lf_150, lf_188, df_seg, df_orc)

        if lf_silver is None or lf_clientes is None:
            log("⚠️ [SYSTEM] Arquivo Segmentos.xlsx ausente ou inválido. Abortando pipeline.")
            AppState.pipeline_rodando = False
            return

        loader = NexusLoader()
        
        log("   -> Executando processamento em memória (Polars)...")
        df_silver_coletado = await asyncio.to_thread(lf_silver.collect)
        log(f"📊 [AUDITORIA] ETL Concluído: A camada Silver resultou em {len(df_silver_coletado)} linhas consolidadas prontas para injeção.")

        log("   -> Iniciando injeção no Banco de Dados (Upsert Atômico)...")
        await asyncio.to_thread(loader.executar_carga_silver, df_silver_coletado, log_callback=log)
        await asyncio.to_thread(loader.atualizar_hierarquia_historica, lf_clientes, log_callback=log)

        # INJEÇÃO DO ORÇAMENTO NO BANCO
        if df_orc_final is not None and not df_orc_final.is_empty():
            df_orc_coletado = await asyncio.to_thread(df_orc_final.collect) if isinstance(df_orc_final, pl.LazyFrame) else df_orc_final
            await asyncio.to_thread(loader.executar_carga_orcamento, df_orc_coletado, log_callback=log)

        hoje = date.today()
        ciclo_atual = hoje.strftime("%m/%Y")
        ciclo_existe = False
        with SessionLocal() as db:
            trava = db.query(func.count(FatoIbpGranular.id)).filter(FatoIbpGranular.ciclo_sop == ciclo_atual).scalar()
            ciclo_existe = (trava > 0)
        
        log(f"   ⏳ Tempo Total FASE 1 (ETL): {time.time() - tempo_inicio_total:.2f}s.")

        if ciclo_existe:
            log(f"⏸️ [S&OP] O ciclo {ciclo_atual} já existe no banco de dados.")
            log("   -> A IA e o Rateio foram ignorados para manter ESTÁTICOS os ajustes do Top-Down e Bottom-Up.")
        else:
            forecaster = NexusForecaster()
            t0 = time.time()
            log("🧠 [ML] Novo Mês Detectado! Acordando a IA (Thread Secundária)...")
            
            df_forecast = await asyncio.to_thread(forecaster.executar_arena, log_callback=log)
            log(f"✅ [ML] Previsões S&OP concluídas em {time.time() - t0:.2f}s.")

            t0 = time.time()
            log("⏳ [LOAD] Rateando e injetando as metas S&OP no Banco...")
            
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