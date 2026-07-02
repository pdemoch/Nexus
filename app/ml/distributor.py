import pandas as pd
import numpy as np
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.core.database import engine, SessionLocal
from app.models.domain_models import FatoIbpGranular

class TopDownDistributor:
    def _obter_matriz_rateio_e_pmv(self) -> pd.DataFrame:
        """Busca o Share dos últimos 6 meses e o PMV dos últimos 3 meses num só fôlego."""
        hoje = date.today()
        corte_6m = (hoje - relativedelta(months=6)).strftime("%Y-%m-%d")
        corte_3m = (hoje - relativedelta(months=3)).strftime("%Y-%m-%d")

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
                SELECT sku, cgc, COALESCE(SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0), 0) as preco_medio
                FROM fato_vendas
                WHERE data_pedido >= '{corte_3m}'
                GROUP BY sku, cgc
            )
            SELECT 
                s.sku, s.cgc, s.vendedor_nome, s.vol_cliente,
                COALESCE(p.preco_medio, 0) as pmv_aplicado
            FROM share_6m s
            LEFT JOIN pmv_3m p ON s.sku = p.sku AND s.cgc = p.cgc
        """
        return pd.read_sql(query, engine)

    def executar_rateio_e_carga(self, df_forecast: pl.DataFrame, ciclo_atual: str):
        df_hist = self._obter_matriz_rateio_e_pmv()
        
        # Calcula o Representatividade (%) do CNPJ no SKU
        total_por_sku = df_hist.groupby('sku')['vol_cliente'].sum().reset_index()
        total_por_sku.rename(columns={'vol_cliente': 'vol_total_sku'}, inplace=True)
        df_hist = df_hist.merge(total_por_sku, on='sku')
        df_hist['share_pct'] = np.where(df_hist['vol_total_sku'] > 0, df_hist['vol_cliente'] / df_hist['vol_total_sku'], 0)

        dados_granulares = []
        df_forecast_pd = df_forecast.to_pandas()

        for _, row in df_forecast_pd.iterrows():
            sku = row['sku']
            vol_ia = float(row['vol_ia_global'])
            clientes_sku = df_hist[df_hist['sku'] == sku].copy()

            if clientes_sku.empty: continue

            # O Método do Maior Resto para fatiar as caixas inteiras
            clientes_sku['vol_dist'] = clientes_sku['share_pct'] * vol_ia
            clientes_sku['vol_arr'] = np.floor(clientes_sku['vol_dist']).astype(int)
            clientes_sku['fracao'] = clientes_sku['vol_dist'] - clientes_sku['vol_arr']
            
            sobra = int(round(vol_ia - clientes_sku['vol_arr'].sum()))
            if sobra > 0:
                clientes_sku = clientes_sku.sort_values(by='fracao', ascending=False)
                clientes_sku.iloc[:sobra, clientes_sku.columns.get_loc('vol_arr')] += 1

            for _, cli in clientes_sku.iterrows():
                vol_final = int(cli['vol_arr'])
                
                # A Regra Atômica: Preenche todas as colunas com a IA (Nascimento do Ciclo)
                dados_granulares.append({
                    "ciclo_sop": ciclo_atual, "mes_projetado": row['mes_projetado'], "sku": sku,
                    "cgc": cli['cgc'], "vendedor_nome": cli['vendedor_nome'],
                    "vol_ia": vol_final, "vol_topdown": vol_final, "vol_bottomup": vol_final, 
                    "vol_supply": vol_final, "vol_meta": vol_final, "vol_final": vol_final,
                    "pmv_aplicado": round(float(cli['pmv_aplicado']), 2),
                    "modelo_vencedor": row['modelo_vencedor'], "acuracia_ia": round(float(row['acuracia_ia']), 2)
                })

        with SessionLocal() as db:
            # Varre o ciclo antes do Upsert para expurgar mortos
            db.execute(text("UPDATE fato_ibp_granular SET vol_ia = 0 WHERE ciclo_sop = :c"), {"c": ciclo_atual})
            
            # Insere/Atualiza em blocos ultra-rápidos
            lote_size = 5000
            for i in range(0, len(dados_granulares), lote_size):
                stmt = pg_insert(FatoIbpGranular).values(dados_granulares[i:i+lote_size])
                stmt = stmt.on_conflict_do_update(
                    index_elements=['ciclo_sop', 'mes_projetado', 'sku', 'cgc'],
                    set_={
                        'vol_ia': stmt.excluded.vol_ia,
                        'pmv_aplicado': stmt.excluded.pmv_aplicado,
                        'modelo_vencedor': stmt.excluded.modelo_vencedor,
                        'acuracia_ia': stmt.excluded.acuracia_ia
                    }
                )
                db.execute(stmt)
            db.commit()