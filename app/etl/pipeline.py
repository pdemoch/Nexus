import traceback
import time
import os
import polars as pl
import asyncio
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, text
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

def aplicar_pmv_historico_pipeline(engine, ciclo_atual: str):
    """
    Substitui o PMV genérico da fato_ibp_granular pelo PMV exato 
    praticado por cada cliente (CGC) nos últimos 4 meses.
    """
    log(f"💰 [FINANÇAS] Calculando PMV Histórico Dinâmico (Cliente x SKU) para o ciclo {ciclo_atual}...")
    
    sql_update_pmv = text("""
        -- 1. Calcula o PMV exato por Cliente + SKU nos últimos 4 meses
        WITH pmv_cliente_sku AS (
            SELECT 
                cgc, 
                sku, 
                SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv_real
            FROM fato_vendas
            WHERE data_pedido >= CURRENT_DATE - INTERVAL '4 months'
            GROUP BY cgc, sku
        ),
        -- 2. Fallback: Calcula o PMV médio nacional do SKU (caso o cliente não tenha comprado nos últimos 4 meses)
        pmv_nacional_sku AS (
            SELECT 
                sku, 
                SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv_nacional
            FROM fato_vendas
            WHERE data_pedido >= CURRENT_DATE - INTERVAL '4 months'
            GROUP BY sku
        )
        
        -- 3. Aplica a atualização na fato_ibp_granular
        UPDATE fato_ibp_granular AS f
        SET pmv_aplicado = COALESCE(
            c.pmv_real,           -- Prioridade 1: Preço exato do Cliente
            n.pmv_nacional,       -- Prioridade 2: Preço médio Nacional
            f.pmv_aplicado,       -- Prioridade 3: Mantém o valor base que a IA inseriu
            0
        )
        FROM fato_ibp_granular f_target
        LEFT JOIN pmv_cliente_sku c ON f_target.cgc = c.cgc AND f_target.sku = c.sku
        LEFT JOIN pmv_nacional_sku n ON f_target.sku = n.sku
        WHERE f.id = f_target.id
          AND f.ciclo_sop = :ciclo;
    """)

    try:
        with engine.begin() as conn:
            resultado = conn.execute(sql_update_pmv, {"ciclo": ciclo_atual})
            linhas_afetadas = resultado.rowcount
            log(f"✅ [FINANÇAS] Sucesso! {linhas_afetadas} SKUs atualizados com precificação histórica de precisão.")
    except Exception as e:
        log(f"❌ [ERRO CRÍTICO] Falha ao atualizar PMV no pipeline: {str(e)}")
        raise e

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

            # =====================================================================
            # 🔥 7. APLICAÇÃO DA REGRA DE NEGÓCIO FINANCEIRA (PMV DINÂMICO)
            # =====================================================================
            with SessionLocal() as db_session:
                engine_db = db_session.get_bind()
                await asyncio.to_thread(aplicar_pmv_historico_pipeline, engine_db, ciclo_alvo)

        tempo_total = time.time() - tempo_inicio_total
        minutos, segundos = divmod(tempo_total, 60)
        AppState.pipeline_rodando = False
        log(f"🏁 [SYSTEM] Pipeline Nexus concluído com sucesso em {int(minutos)}m {int(segundos)}s.")

    except Exception as e:
        AppState.pipeline_rodando = False
        erro_formatado = traceback.format_exc()
        log(f"❌ [ERRO CRÍTICO] Falha no pipeline: {str(e)}")
        log(f"🔍 Detalhes: {erro_formatado}")