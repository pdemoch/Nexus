import os
import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
from sqlalchemy import create_engine, text

# Ajuste para importar as configurações do seu projeto Nexus
from app.core.config import settings

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

def virada_mensal_ciclo(engine, hoje):
    """Vira o mês automaticamente na tabela configuracao_sistema se for dia 1º"""
    if hoje.day == 1:
        print("📅 Dia 1º detetado! A iniciar a virada do Ciclo S&OP global...")
        with engine.begin() as conn:
            res = conn.execute(text("SELECT ciclo_ativo_global FROM configuracao_sistema ORDER BY id DESC LIMIT 1")).fetchone()
            ciclo_atual = res[0] if res else hoje.strftime("%m/%Y")
            
            mes, ano = map(int, ciclo_atual.split('/'))
            novo_ciclo_dt = datetime.date(ano, mes, 1) + relativedelta(months=1)
            novo_ciclo_str = novo_ciclo_dt.strftime("%m/%Y")
            
            conn.execute(
                text("INSERT INTO configuracao_sistema (ciclo_ativo_global, data_atualizacao) VALUES (:c, :d)"),
                {"c": novo_ciclo_str, "d": hoje}
            )
            print(f"✅ Ciclo S&OP virado com sucesso: {ciclo_atual} -> {novo_ciclo_str}")
    else:
        print(f"⏭️ Hoje é dia {hoje.day}. Nenhuma virada de ciclo necessária.")

def gerar_snapshot_estoque(engine, ontem):
    """Extrai a fotografia do Risco de Estoque de D-1 e guarda no AWS S3"""
    data_str = ontem.strftime("%Y-%m-%d")
    arquivo_parquet = f"{S3_PREFIX}/{ontem.strftime('%Y_%m_%d')}_riscoestoque.parquet"
    
    print(f"📸 A tirar fotografia do S&OP e Estoque para a data: {data_str}")
    
    mes_sql = ontem.strftime("%Y-%m")
    dt_lag2 = ontem.replace(day=1) - relativedelta(months=2)
    ciclo_congelado = dt_lag2.strftime("%m/%Y")
    
    query_sku = text("""
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
                CASE WHEN COALESCE(m.mtd_vol, 0) >= COALESCE(h.media_vol, 0) * 0.8 THEN 0 ELSE GREATEST(0, COALESCE(h.media_vol, 0) - COALESCE(m.mtd_vol, 0)) END as previsao_vol
            FROM Hist_Clientes h
            LEFT JOIN MTD_Clientes m ON h.sku = m.sku AND h.regional = m.regional AND h.razaosocial = m.razaosocial
            LEFT JOIN Freq_Clientes f ON h.sku = f.sku AND h.regional = f.regional AND h.razaosocial = f.razaosocial
            WHERE COALESCE(f.freq_meses, 0) >= 4
        ),
        Previsao_SKU AS (SELECT sku, SUM(previsao_vol) as previsao_entrada_vol FROM Previsao_Clientes GROUP BY sku),
        Metas AS (
            SELECT m.sku, SUM(m.vol_final) as meta_mes, COALESCE(SUM(m.vol_final * m.pmv_aplicado) / NULLIF(SUM(m.vol_final), 0), MAX(m.pmv_aplicado)) as pmv 
            FROM fato_ibp_granular m LEFT JOIN dim_clientes c ON m.cgc = c.cgc
            WHERE TO_CHAR(m.mes_projetado, 'YYYY-MM') = :mes_sql AND m.ciclo_sop = :ciclo_congelado GROUP BY m.sku
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
        df_sku = pd.read_sql(query_sku, engine, params={"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado})
        if not df_sku.empty:
            # Escreve o DataFrame diretamente para a AWS S3
            df_sku.to_parquet(
                arquivo_parquet, 
                engine='pyarrow', 
                compression='snappy',
                storage_options=AWS_STORAGE_OPTIONS
            )
            print(f"🚀 Sucesso! Snapshot gravado no S3: {arquivo_parquet} ({len(df_sku)} SKUs)")
        else:
            print("⚠️ Operação cancelada: Nenhum dado retornado para a fotografia de hoje.")
    except Exception as e:
        print(f"❌ Falha crítica ao gerar Snapshot Parquet: {e}")

if __name__ == "__main__":
    print("==================================================")
    print("🌙 INICIANDO ROTINA NOTURNA NEXUS (00:01)")
    print("==================================================")
    hoje = datetime.date.today()
    ontem = hoje - relativedelta(days=1)
    engine = obter_engine()
    
    virada_mensal_ciclo(engine, hoje)
    gerar_snapshot_estoque(engine, ontem)
    print("==================================================")