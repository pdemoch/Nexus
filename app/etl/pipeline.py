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
from app.models.domain_models import FatoIbpGranular
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
        
        log("🚀 [SYSTEM] Iniciando Nexus Engine 4.0 (Arquitetura CPFR com AWS Data Lake)...")

        # =========================================================================
        # 1. OBTER CICLO ATIVO (GOVERNANÇA DE S&OP)
        # =========================================================================
        with SessionLocal() as db:
            # O Pipeline obedece cegamente ao ciclo que o usuário escolheu no Frontend
            ciclo_alvo = getattr(AppState, 'ciclo_ativo', None)
            if not ciclo_alvo:
                ciclo_alvo = hoje.strftime("%m/%Y")
            
            log(f"🧭 [S&OP] Ciclo âncora travado na competência: {ciclo_alvo}")
            
            # Verifica se já passamos pela IA neste ciclo para evitar duplicações
            trava = db.query(func.count(FatoIbpGranular.id)).filter(FatoIbpGranular.ciclo_sop == ciclo_alvo).scalar()
            ciclo_existe = (trava > 0)

        # =========================================================================
        # 2. FASE DE EXTRAÇÃO DO ERP INTERNO (SELL-IN / GOBI ERP)
        # =========================================================================
        extractor = GobiExtractor()
        data_inicio = date(2023, 4, 1) # Janela histórica para a IA de Fábrica

        log(f"📥 [EXTRACT] Extraindo dados da Fábrica (Gobi ERP: {data_inicio} a {data_fim})...")
        lf_150, lf_188, df_seg, df_orc = await extractor.extrair_tudo(data_inicio, data_fim)
        
        if len(lf_150.columns) == 0 or len(lf_188.columns) == 0:
            log("⚠️ [SYSTEM] Arquivos de Vendas vazios. O pipeline será encerrado por segurança.")
            AppState.pipeline_rodando = False
            return

        # =========================================================================
        # 3. FASE DE TRANSFORMAÇÃO (SILVER LAYER - FÁBRICA)
        # =========================================================================
        transformer = NexusTransformer()
        lf_silver, lf_clientes, df_orc_final = transformer.processar_camada_silver(lf_150, lf_188, df_seg, df_orc)

        if lf_silver is None or lf_clientes is None:
            log("⚠️ [SYSTEM] Arquivo Segmentos.xlsx ausente. Abortando pipeline.")
            AppState.pipeline_rodando = False
            return

        # =========================================================================
        # 4. FASE DE INJEÇÃO (POSTGRESQL RELACIONAL)
        # =========================================================================
        loader = NexusLoader()
        
        log("   -> Executando processamento Polars em memória...")
        df_silver_coletado = await asyncio.to_thread(lf_silver.collect)
        log(f"📊 [AUDITORIA] ETL Gobi Concluído: {len(df_silver_coletado)} linhas consolidadas prontas para injeção.")

        log("   -> Iniciando injeção no Banco de Dados (Upsert Atômico)...")
        await asyncio.to_thread(loader.executar_carga_silver, df_silver_coletado, log_callback=log)
        await asyncio.to_thread(loader.atualizar_hierarquia_historica, lf_clientes, log_callback=log)

        if df_orc_final is not None and not df_orc_final.is_empty():
            df_orc_coletado = await asyncio.to_thread(df_orc_final.collect) if isinstance(df_orc_final, pl.LazyFrame) else df_orc_final
            await asyncio.to_thread(loader.executar_carga_orcamento, df_orc_coletado, log_callback=log)

        # =========================================================================
        # 5. FASE DE CONSUMO DO DATA LAKE (SELL-OUT / MTRIX)
        # =========================================================================
        log(f"📥 [LAKE] Consumindo Data Lake de Canal Indireto na AWS...")
        # Lê os Parquets da AWS, cruza com o ERP e grava as tabelas leves
        await asyncio.to_thread(loader.executar_carga_mtrix, ciclo_alvo, log_callback=log)
        
        log(f"   ⏳ Tempo Total FASE ETL + Lake: {time.time() - tempo_inicio_total:.2f}s.")

        # =========================================================================
        # 6. FASE PREDITIVA (ML MACHINE LEARNING ARENA & RATEIO)
        # =========================================================================
        if ciclo_existe:
            log(f"⏸️ [S&OP] O ciclo {ciclo_alvo} já está trancado no banco de dados.")
            log("   -> As Redes Neurais e o Rateio foram ignorados para manter a auditoria dos Gestores intacta.")
        else:
            forecaster = NexusForecaster()
            t0 = time.time()
            log("🧠 [ML] Acordando os Motores Duplos de Inteligência Artificial (Alpha e Beta)...")
            
            # Passamos a âncora de tempo para a IA não se perder nas projeções
            df_forecast = await asyncio.to_thread(forecaster.executar_arena, ciclo_alvo, log_callback=log)
            log(f"✅ [ML] Previsões Concluídas em {time.time() - t0:.2f}s.")

            t0 = time.time()
            log("⏳ [LOAD] Rateando e injetando as Metas e Previsões S&OP no Banco...")
            await asyncio.to_thread(loader.executar_carga_forecast, df_forecast, ciclo_alvo, log_callback=log) 
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