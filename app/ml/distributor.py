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
    def __init__(self, meses_historico=6):
        self.meses_historico = meses_historico

    def _obter_share_historico(self) -> pd.DataFrame:
        print("📥 [RATEIO] Buscando histórico de Share...")
        hoje = date.today()
        data_corte = (hoje - relativedelta(months=self.meses_historico)).strftime("%Y-%m-%d")

        query = f"""
            SELECT 
                v.sku as produto, v.cgc, c.vendedor_nome, SUM(v.qt_pedido) as qtpedido
            FROM fato_vendas v
            JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE v.data_pedido >= '{data_corte}'
              AND UPPER(COALESCE(c.bloqueado, 'ATIVO')) != 'INATIVO'
            GROUP BY v.sku, v.cgc, c.vendedor_nome
        """
        return pd.read_sql(query, engine)

    def executar_rateio_tatico(self, df_forecast: pl.DataFrame, ciclo_atual: str):
        print(f"⚙️ [RATEIO] Iniciando para ciclo {ciclo_atual}...")
        df_hist = self._obter_share_historico()
        
        # Prepara a base totalizadora
        total_por_produto = df_hist.groupby('produto')['qtpedido'].sum().reset_index()
        total_por_produto.rename(columns={'qtpedido': 'total_produto'}, inplace=True)
        df_hist = df_hist.merge(total_por_produto, on='produto')
        df_hist['share_percentual'] = np.where(df_hist['total_produto'] > 0, df_hist['qtpedido'] / df_hist['total_produto'], 0)

        dados_granulares = []
        df_forecast_pd = df_forecast.to_pandas()

        for _, row in df_forecast_pd.iterrows():
            produto = row['sku']
            vol_ia = float(row['vol_ia_global'])
            clientes_produto = df_hist[df_hist['produto'] == produto].copy()

            if clientes_produto.empty: continue

            clientes_produto['vol_distribuido'] = clientes_produto['share_percentual'] * vol_ia
            clientes_produto['vol_arredondado'] = np.floor(clientes_produto['vol_distribuido']).astype(int)
            clientes_produto['fracao'] = clientes_produto['vol_distribuido'] - clientes_produto['vol_arredondado']
            
            sobra = int(round(vol_ia - clientes_produto['vol_arredondado'].sum()))
            if sobra > 0:
                clientes_produto = clientes_produto.sort_values(by='fracao', ascending=False)
                clientes_produto.iloc[:sobra, clientes_produto.columns.get_loc('vol_arredondado')] += 1

            for _, cli in clientes_produto.iterrows():
                dados_granulares.append({
                    "ciclo_sop": ciclo_atual, "mes_projetado": row['mes_projetado'], "sku": produto,
                    "cgc": cli['cgc'], "vendedor_nome": cli['vendedor_nome'],
                    "vol_ia": int(cli['vol_arredondado']), "vol_topdown": int(cli['vol_arredondado']), 
                    "vol_bottomup": int(cli['vol_arredondado']), "vol_supply": int(cli['vol_arredondado']),
                    "vol_meta": int(cli['vol_arredondado']), "vol_final": int(cli['vol_arredondado']),
                    "pmv_aplicado": 0.0
                })

        with SessionLocal() as db:
            # Blindagem: Zera a IA antes de atualizar para expurgar itens descontinuados
            db.execute(text("UPDATE fato_ibp_granular SET vol_ia = 0 WHERE ciclo_sop = :c"), {"c": ciclo_atual})
            
            # UPSERT Inteligente
            lote_size = 5000
            for i in range(0, len(dados_granulares), lote_size):
                stmt = pg_insert(FatoIbpGranular).values(dados_granulares[i:i+lote_size])
                stmt = stmt.on_conflict_do_update(
                    index_elements=['ciclo_sop', 'mes_projetado', 'sku', 'cgc'],
                    set_={'vol_ia': stmt.excluded.vol_ia}
                )
                db.execute(stmt)
            db.commit()
            print("✅ [RATEIO] Carga Finalizada.")