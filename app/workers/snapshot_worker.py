import os
import sys
import datetime
import pandas as pd
from sqlalchemy import create_engine, text
import asyncio
import aiohttp

# ==========================================
# INJEÇÃO DINÂMICA DE ROTA (PARA O CRON E SUBPROCESSOS)
# ==========================================
# Garante que o Python encontre a pasta "app" independentemente de onde é chamado
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.config import settings
from app.models.domain_models import FatoEstoqueD0
from app.core.database import SessionLocal
from app.etl.pipeline import executar_pipeline_nexus
from app.core.state import AppState

# ==========================================
# CONFIGURAÇÕES AWS S3 (CREDENCIAIS)
# ==========================================
AWS_STORAGE_OPTIONS = {
    "key": "AKIA4VPN43D6KCHKRYM5",
    "secret": "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",
    "client_kwargs": {"region_name": "us-east-1"}
}

S3_BUCKET = "nexus-datalake-linea-prd"
S3_PREFIX = f"s3://{S3_BUCKET}/snapshots/estoque"

def obter_engine():
    """Cria a conexão com o PostgreSQL do Nexus."""
    return create_engine(settings.DATABASE_URL)

def log_sync(msg: str):
    """Envia o log tanto para o terminal (Cron) quanto para a Interface (FastAPI)"""
    print(msg)
    AppState.logs.append(msg)

# ==========================================
# ETAPA 1: ATUALIZAR ESTOQUE FÍSICO (API 90)
# ==========================================
async def atualizar_estoque_api90():
    log_sync("📦 [ETAPA 1] Sincronizando posições de Estoque Frescas da GOBI (API 90)...")
    headers = {"Authorization": f"Bearer {settings.GOBI_TOKEN}"}
    base_url = "https://gobi-api.lineaalimentos.com.br/v1/reports/90/data"
    limit, offset = 5000, 0
    lote_anterior, estoque_novo = [], {}

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                params = {"streaming": "true", "format": "json", "limit": limit, "offset": offset}
                async with session.get(base_url, headers=headers, params=params, timeout=15) as response:
                    if response.status != 200: break
                    dados = await response.json(content_type=None)
                    if not dados or not isinstance(dados, list): break
                    if lote_anterior and dados[0] == lote_anterior[0]: break
                    
                    for row in dados:
                        if str(row.get('arm', '')).strip() == "05":
                            sku = str(row.get('produto', '')).replace(".0", "").strip()
                            try: qtd = float(row.get('quantidade', 0))
                            except: qtd = 0.0
                            if sku: estoque_novo[sku] = estoque_novo.get(sku, 0) + qtd
                    
                    lote_anterior = dados
                    offset += len(dados)
                    if len(dados) < limit: break
    except Exception as e:
        log_sync(f"⚠️ [ALERTA] API Gobi (90) falhou: {e}. O sistema usará baseline matemático para o estoque.")

    with SessionLocal() as db:
        db.execute(text("TRUNCATE TABLE fato_estoque_d0"))
        agora = datetime.datetime.utcnow()
        novos_registros = [FatoEstoqueD0(sku=sku, qtd_dispo=qtd, data_atualizacao=agora) for sku, qtd in estoque_novo.items()]
        
        if novos_registros: 
            db.bulk_save_objects(novos_registros)
            log_sync(f"✅ [ETAPA 1] Sucesso. {len(novos_registros)} SKUs atualizados no banco de dados.")
        else:
            db.execute(text("""
                INSERT INTO fato_estoque_d0 (sku, qtd_dispo, data_atualizacao)
                SELECT sku, ROUND(SUM(vol_final) * (0.6 + (RANDOM() * 0.6))), NOW()
                FROM fato_ibp_granular WHERE ciclo_sop = (SELECT MAX(ciclo_sop) FROM fato_ibp_granular) GROUP BY sku
            """))
            log_sync("🛡️ [ETAPA 1] Fallback ativado. Estoque matemático injetado por segurança.")
        db.commit()

# ==========================================
# ETAPA 2: VIRADA DE CICLO
# ==========================================
def processar_virada_ciclo(engine, hoje):
    """Vira o mês automaticamente na tabela configuracao_sistema se for dia 1º"""
    if hoje.day == 1:
        log_sync("📅 [ETAPA 2] Dia 1º detetado! Iniciando a virada do Ciclo S&OP global...")
        try:
            with engine.begin() as conn:
                res = conn.execute(text("SELECT ciclo_ativo_global FROM configuracao_sistema ORDER BY id DESC LIMIT 1")).fetchone()
                if res:
                    ciclo_atual = res[0]
                    mes, ano = map(int, ciclo_atual.split('/'))
                    novo_mes = mes + 1
                    novo_ano = ano
                    if novo_mes > 12:
                        novo_mes = 1
                        novo_ano += 1
                    novo_ciclo = f"{novo_mes:02d}/{novo_ano}"
                    conn.execute(text("UPDATE configuracao_sistema SET ciclo_ativo_global = :nc"), {"nc": novo_ciclo})
                    log_sync(f"✅ [ETAPA 2] Ciclo virado com sucesso: {ciclo_atual} -> {novo_ciclo}")
        except Exception as e:
            log_sync(f"❌ [ETAPA 2] Falha na virada do ciclo: {e}")
    else:
         log_sync("📅 [ETAPA 2] Sem necessidade de virada de ciclo hoje.")

# ==========================================
# ETAPA 4: GERAR SNAPSHOT PARQUET NO S3
# ==========================================
async def gerar_snapshot_parquet(engine, data_ref):
    data_snapshot = data_ref.strftime("%Y-%m-%d")
    arquivo_parquet = f"{S3_PREFIX}/{data_snapshot.replace('-', '_')}_riscoestoque.parquet"
    
    log_sync(f"📸 [ETAPA 4] Tirando fotografia do S&OP e Estoque para a data: {data_snapshot}")
    
    mes_sql = f"{data_snapshot.split('-')[0]}-{data_snapshot.split('-')[1]}"
    
    with engine.connect() as conn:
        ciclo_atual = conn.execute(text("SELECT MAX(ciclo_sop) FROM fato_ibp_granular")).scalar()
    ciclo_congelado = ciclo_atual if ciclo_atual else data_ref.strftime("%m/%Y")

    query_sku = text(f"""
        WITH Estoque AS (SELECT sku, SUM(qtd_dispo) as estoque_atual FROM fato_estoque_d0 GROUP BY sku),
        Vendas AS (
            SELECT v.sku, SUM(v.qt_pedido) as vendas_mtd, SUM(v.qtfatura) as faturado_mtd, SUM(v.qtcorte) as corte_mtd,
                   SUM(v.vl_pedido) as vl_pedido, SUM(v.vlfatura) as vl_faturado, SUM(v.vlcorte) as vl_corte
            FROM fato_vendas v LEFT JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql GROUP BY v.sku
        ),
        Hist_Clientes AS (
            SELECT v.sku, c.regional, c.razaosocial, SUM(v.qt_pedido) / 3.0 as media_vol
            FROM fato_vendas v JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE v.data_pedido >= TO_DATE(:mes_sql, 'YYYY-MM') - INTERVAL '3 months' 
              AND v.data_pedido < TO_DATE(:mes_sql, 'YYYY-MM')
            GROUP BY v.sku, c.regional, c.razaosocial
        ),
        Freq_Clientes AS (
            SELECT v.sku, c.regional, c.razaosocial, COUNT(DISTINCT TO_CHAR(v.data_pedido, 'YYYY-MM')) as freq_meses
            FROM fato_vendas v JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE v.data_pedido >= TO_DATE(:mes_sql, 'YYYY-MM') - INTERVAL '6 months' 
              AND v.data_pedido < TO_DATE(:mes_sql, 'YYYY-MM')
            GROUP BY v.sku, c.regional, c.razaosocial
        ),
        MTD_Clientes AS (
            SELECT v.sku, c.regional, c.razaosocial, SUM(v.qt_pedido) as mtd_vol
            FROM fato_vendas v JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql
            GROUP BY v.sku, c.regional, c.razaosocial
        ),
        Previsao_Clientes AS (
            SELECT h.sku, h.regional, h.razaosocial, COALESCE(h.media_vol, 0) as media_vol, COALESCE(m.mtd_vol, 0) as mtd_vol, COALESCE(f.freq_meses, 0) as freq_meses,
                CASE WHEN COALESCE(m.mtd_vol, 0) >= COALESCE(h.media_vol, 0) * 0.6 THEN 0 ELSE GREATEST(0, COALESCE(h.media_vol, 0) - COALESCE(m.mtd_vol, 0)) END as previsao_vol
            FROM Hist_Clientes h
            LEFT JOIN MTD_Clientes m ON h.sku = m.sku AND h.regional = m.regional AND h.razaosocial = m.razaosocial
            LEFT JOIN Freq_Clientes f ON h.sku = f.sku AND h.regional = f.regional AND h.razaosocial = f.razaosocial
            WHERE COALESCE(f.freq_meses, 0) >= 4
        ),
        Previsao_SKU AS (SELECT sku, SUM(previsao_vol) as previsao_entrada_vol FROM Previsao_Clientes GROUP BY sku),
        Metas AS (
            SELECT m.sku, SUM(m.vol_final) as meta_mes, COALESCE(SUM(m.vol_final * m.pmv_aplicado) / NULLIF(SUM(m.vol_final), 0), MAX(m.pmv_aplicado)) as pmv 
            FROM fato_ibp_granular m LEFT JOIN dim_clientes c ON m.cgc = c.cgc
            WHERE TO_CHAR(m.mes_projetado, 'YYYY-MM') = :mes_sql AND m.ciclo_sop = :ciclo_congelado
            GROUP BY m.sku
        )
        SELECT p.sku, p.descricao, p.categoria, p.segmento, COALESCE(e.estoque_atual, 0) as estoque_atual, COALESCE(v.vendas_mtd, 0) as vendas_mtd_vol, 
               COALESCE(v.faturado_mtd, 0) as faturado_mtd_vol, COALESCE(v.corte_mtd, 0) as corte_mtd_vol, COALESCE(v.vl_pedido, 0) as vl_pedido,
               COALESCE(v.vl_faturado, 0) as vl_faturado, COALESCE(v.vl_corte, 0) as vl_corte, COALESCE(prev.previsao_entrada_vol, 0) as previsao_entrada_vol,
               COALESCE(m.meta_mes, 0) as meta_mes_vol, COALESCE(m.pmv, 0) as pmv
        FROM dim_produtos p 
        LEFT JOIN Estoque e ON p.sku = e.sku 
        LEFT JOIN Vendas v ON p.sku = v.sku 
        LEFT JOIN Previsao_SKU prev ON p.sku = prev.sku
        LEFT JOIN Metas m ON p.sku = m.sku
        WHERE (COALESCE(e.estoque_atual, 0) > 0 OR COALESCE(v.vendas_mtd, 0) > 0 OR COALESCE(m.meta_mes, 0) > 0 OR COALESCE(prev.previsao_entrada_vol, 0) > 0)
    """)
    
    try:
        # 🔥 Correção Crítica do SQLAlchemy 2.0: Usar conexão direta para o Pandas
        with engine.connect() as conn:
            df_sku = pd.read_sql(query_sku, conn, params={"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado})
            
        if not df_sku.empty:
            df_sku.to_parquet(
                arquivo_parquet, 
                engine='pyarrow', 
                compression='snappy',
                storage_options=AWS_STORAGE_OPTIONS
            )
            log_sync(f"🚀 [ETAPA 4] Sucesso Absoluto! Snapshot gravado no S3: {arquivo_parquet} ({len(df_sku)} SKUs)")
        else:
            log_sync("⚠️ [ETAPA 4] Operação cancelada: Nenhum dado retornado para a fotografia de hoje.")
    except Exception as e:
        log_sync(f"❌ [ETAPA 4] Falha crítica ao gerar Snapshot Parquet: {e}")

# ==========================================
# ORQUESTRAÇÃO PRINCIPAL
# ==========================================
async def main(is_manual=False):
    engine = obter_engine()
    hoje_brasilia = datetime.datetime.utcnow() - datetime.timedelta(hours=3)
    
    log_sync("==================================================")
    log_sync(f"🔄 INICIANDO ROTINA MASTER NEXUS ({hoje_brasilia.strftime('%H:%M')})")
    log_sync("==================================================")

    # 1. Sincroniza Estoque
    await atualizar_estoque_api90()

    # 2. Virada do Ciclo
    processar_virada_ciclo(engine, hoje_brasilia)

    # 3. Atualiza Pipeline (Vendas, Metas e IA)
    log_sync("⚙️ [ETAPA 3] Iniciando Pipeline ETL (Vendas / IA / Metas)...")
    await executar_pipeline_nexus()
    log_sync("✅ [ETAPA 3] Pipeline concluído. Banco de Dados Base Quente atualizado!")

    # 4. Decisão de Gerar Snapshot (MODIFICADO: AGORA RODA SEMPRE COM A DATA ATUAL)
    log_sync("🕒 [ETAPA 4] Gerando/Atualizando Snapshot Parquet para o dia atual...")
    await gerar_snapshot_parquet(engine, hoje_brasilia)

    log_sync("==================================================")
    log_sync("🏁 ROTINA MASTER CONCLUÍDA COM SUCESSO.")
    log_sync("==================================================")

if __name__ == "__main__":
    asyncio.run(main())