import polars as pl
import pandas as pd
import numpy as np
import traceback
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal, engine
from app.models.domain_models import FatoIbpGranular, FatoVenda, DimCliente

class NexusLoader:
    def __init__(self):
        pass

    def executar_carga_silver(self, df_silver: pl.DataFrame, data_inicio: date, log_callback=print):
        """
        Responsável por sincronizar o histórico bruto extraído (Gobi) para a Camada Silver (PostgreSQL).
        Caso você possua uma lógica específica para os clientes inativos, ela é preservada aqui.
        """
        log_callback("⏳ [LOAD] Sincronizando dados históricos extraídos com o Banco de Dados...")
        try:
            df_vendas = df_silver.to_dicts()
            if not df_vendas:
                return

            with SessionLocal() as db:
                lote_size = 5000
                for i in range(0, len(df_vendas), lote_size):
                    lote = df_vendas[i:i+lote_size]
                    stmt = pg_insert(FatoVenda).values(lote)
                    # Atualiza em caso de conflito (Garante que pedidos cancelados/alterados sejam corrigidos)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['pedido', 'sku', 'cgc'],
                        set_={col: getattr(stmt.excluded, col) for col in lote[0].keys() if col not in ['pedido', 'sku', 'cgc']}
                    )
                    db.execute(stmt)
                db.commit()
            log_callback("✅ [LOAD] Histórico recente integrado com sucesso.")
        except Exception as e:
            log_callback(f"❌ [LOAD] Erro na carga de histórico: {e}")
            raise e

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, ciclo_alvo: str, log_callback=print):
        """
        O Motor Central do S&OP:
        1. Recebe a Previsão da IA (Nível SKU Nacional).
        2. Calcula Share de Clientes (Últimos 6 Meses).
        3. Calcula PMV (Últimos 3 Meses).
        4. Fatiamento por Maior Resto e Gravação Definitiva.
        """
        log_callback(f"⏳ [LOAD] Iniciando Rateio Tático e PMV para o ciclo {ciclo_alvo}...")
        
        hoje = date.today()
        corte_6m = (hoje - relativedelta(months=6)).strftime("%Y-%m-%d")
        corte_3m = (hoje - relativedelta(months=3)).strftime("%Y-%m-%d")

        try:
            log_callback("   • Extraindo Matriz de Share (6M) e Preço Médio (3M)...")
            # Uma única query ultra-eficiente que já traz o peso comercial e financeiro
            query = f"""
                WITH share_6m AS (
                    SELECT v.sku, v.cgc, c.vendedor_nome, SUM(v.qt_pedido) as vol_cliente
                    FROM fato_vendas v
                    JOIN dim_clientes c ON v.cgc = c.cgc
                    WHERE v.data_pedido >= '{corte_6m}'
                      AND UPPER(COALESCE(c.bloqueado, 'ATIVO')) != 'INATIVO'
                    GROUP BY v.sku, v.cgc, c.vendedor_nome
                ),
                pmv_3m AS (
                    SELECT sku, cgc, SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) as preco_medio
                    FROM fato_vendas
                    WHERE data_pedido >= '{corte_3m}'
                    GROUP BY sku, cgc
                )
                SELECT 
                    s.sku, s.cgc, s.vendedor_nome, s.vol_cliente,
                    COALESCE(p.preco_medio, 0.0) as pmv_aplicado
                FROM share_6m s
                LEFT JOIN pmv_3m p ON s.sku = p.sku AND s.cgc = p.cgc
            """
            df_hist = pd.read_sql(query, engine)
            
            if df_hist.empty:
                log_callback("⚠️ [LOAD] Nenhum histórico recente encontrado para rateio.")
                return

            # Calcular a % que cada cliente representa no SKU
            total_por_sku = df_hist.groupby('sku')['vol_cliente'].sum().reset_index()
            total_por_sku.rename(columns={'vol_cliente': 'vol_total_sku'}, inplace=True)
            df_hist = df_hist.merge(total_por_sku, on='sku')
            df_hist['share_pct'] = np.where(df_hist['vol_total_sku'] > 0, df_hist['vol_cliente'] / df_hist['vol_total_sku'], 0)

            dados_granulares = []
            df_forecast_pd = df_forecast.to_pandas()

            log_callback("   • Fatiando volumes com Método do Maior Resto e injetando PMV...")
            
            for _, row in df_forecast_pd.iterrows():
                sku = str(row['sku'])
                vol_ia = float(row['vol_ia_global'])
                modelo_vencedor = str(row.get('modelo_vencedor', 'Media_Simples'))
                acuracia_ia = float(row.get('acuracia_ia', 50.0))

                clientes_sku = df_hist[df_hist['sku'] == sku].copy()
                if clientes_sku.empty:
                    continue

                # Método do Maior Resto (Fatiar sem deixar casas decimais perdidas)
                clientes_sku['vol_dist'] = clientes_sku['share_pct'] * vol_ia
                clientes_sku['vol_arr'] = np.floor(clientes_sku['vol_dist']).astype(int)
                clientes_sku['fracao'] = clientes_sku['vol_dist'] - clientes_sku['vol_arr']
                
                sobra = int(round(vol_ia - clientes_sku['vol_arr'].sum()))
                if sobra > 0:
                    clientes_sku = clientes_sku.sort_values(by='fracao', ascending=False)
                    clientes_sku.iloc[:sobra, clientes_sku.columns.get_loc('vol_arr')] += 1

                # Preparar o Array final
                for _, cli in clientes_sku.iterrows():
                    vol_final = int(cli['vol_arr'])
                    pmv = float(cli['pmv_aplicado']) if not pd.isna(cli['pmv_aplicado']) else 0.0

                    # Todas as caixas nascem iguais com a meta da IA cravada
                    dados_granulares.append({
                        "ciclo_sop": ciclo_alvo, 
                        "mes_projetado": row['mes_projetado'], 
                        "sku": sku,
                        "cgc": cli['cgc'], 
                        "vendedor_nome": cli['vendedor_nome'],
                        "vol_ia": vol_final, 
                        "vol_topdown": vol_final, 
                        "vol_bottomup": vol_final, 
                        "vol_supply": vol_final, 
                        "vol_meta": vol_final, 
                        "vol_final": vol_final,
                        "pmv_aplicado": round(pmv, 2),
                        "modelo_vencedor": modelo_vencedor, 
                        "acuracia_ia": round(acuracia_ia, 2)
                    })

            log_callback(f"   • Gravando {len(dados_granulares)} linhas atômicas no PostgreSQL...")

            with SessionLocal() as db:
                # 1. Garante que o ciclo nasce virgem e limpo, sem produtos fantasmas
                db.execute(text("DELETE FROM fato_ibp_granular WHERE ciclo_sop = :c"), {"c": ciclo_alvo})
                
                # 2. Injeta as linhas em velocidade Bulk
                lote_size = 5000
                for i in range(0, len(dados_granulares), lote_size):
                    stmt = pg_insert(FatoIbpGranular).values(dados_granulares[i:i+lote_size])
                    
                    # UPSERT de proteção
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['ciclo_sop', 'mes_projetado', 'sku', 'cgc'],
                        set_={
                            'vol_ia': stmt.excluded.vol_ia,
                            'vol_topdown': stmt.excluded.vol_topdown,
                            'vol_bottomup': stmt.excluded.vol_bottomup,
                            'vol_supply': stmt.excluded.vol_supply,
                            'vol_meta': stmt.excluded.vol_meta,
                            'vol_final': stmt.excluded.vol_final,
                            'pmv_aplicado': stmt.excluded.pmv_aplicado,
                            'modelo_vencedor': stmt.excluded.modelo_vencedor,
                            'acuracia_ia': stmt.excluded.acuracia_ia
                        }
                    )
                    db.execute(stmt)
                db.commit()
                log_callback("✅ [LOAD] Pipeline Finalizado! Dados injetados com sucesso.")

        except Exception as e:
            log_callback(f"❌ [LOAD] Erro Crítico no Loader: {str(e)}")
            log_callback(traceback.format_exc())
            raise e