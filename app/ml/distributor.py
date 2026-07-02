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
        print("📥 [RATEIO] Buscando histórico de Share (últimos 6 meses) no Banco de Dados...")
        hoje = date.today()
        data_corte = (hoje - relativedelta(months=self.meses_historico)).strftime("%Y-%m-%d")

        query = f"""
            SELECT 
                v.sku as produto, 
                v.cgc, 
                c.cod_cliente, 
                c.loja, 
                c.razaosocial as cliente_razaosocial, 
                c.regional, 
                c.vendedor_nome, 
                SUM(v.qt_pedido) as qtpedido
            FROM fato_vendas v
            JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE v.data_pedido >= '{data_corte}'
              AND UPPER(COALESCE(c.bloqueado, 'ATIVO')) != 'INATIVO'  -- <-- BLINDAGEM CLIENTES INATIVOS
            GROUP BY v.sku, v.cgc, c.cod_cliente, c.loja, c.razaosocial, c.regional, c.vendedor_nome
        """
        return pd.read_sql(query, engine)

    def executar_rateio_tatico(self, df_forecast: pl.DataFrame, ciclo_atual: str):
        print(f"\n⚙️ [RATEIO] Iniciando Motor Top-Down para o ciclo {ciclo_atual}...")
        
        df_hist = self._obter_share_historico()
        if df_hist.empty:
            raise ValueError("Sem histórico de vendas para calcular o Share!")

        total_por_produto = df_hist.groupby('produto')['qtpedido'].sum().reset_index()
        total_por_produto.rename(columns={'qtpedido': 'total_produto'}, inplace=True)
        df_hist = df_hist.merge(total_por_produto, on='produto')
        
        # Calcula o % de representatividade de cada cliente para o produto
        df_hist['share_percentual'] = np.where(
            df_hist['total_produto'] > 0, 
            df_hist['qtpedido'] / df_hist['total_produto'], 
            0
        )

        dados_granulares = []
        df_forecast_pd = df_forecast.to_pandas()

        print("🧮 [RATEIO] Fatiando Volumes Macros da IA para Granularidade Cliente...")
        
        for _, row in df_forecast_pd.iterrows():
            produto = row['sku']
            mes_proj = row['mes_projetado']
            volume_ia = float(row['vol_ia_global'])

            clientes_produto = df_hist[df_hist['produto'] == produto].copy()

            if clientes_produto.empty:
                continue

            clientes_produto['vol_distribuido'] = clientes_produto['share_percentual'] * volume_ia
            clientes_produto['vol_arredondado'] = np.floor(clientes_produto['vol_distribuido']).astype(int)
            clientes_produto['fracao'] = clientes_produto['vol_distribuido'] - clientes_produto['vol_arredondado']

            # Algoritmo do Maior Resto (Evita perda de caixas no arredondamento)
            sobra = int(round(volume_ia - clientes_produto['vol_arredondado'].sum()))
            if sobra > 0:
                clientes_produto = clientes_produto.sort_values(by='fracao', ascending=False)
                indices = clientes_produto.index[:sobra]
                clientes_produto.loc[indices, 'vol_arredondado'] += 1

            for _, cli in clientes_produto.iterrows():
                vol_final = cli['vol_arredondado']
                if vol_final > 0:
                    dados_granulares.append({
                        "ciclo_sop": ciclo_atual, 
                        "mes_projetado": mes_proj, 
                        "sku": produto,
                        "cgc": cli['cgc'], 
                        "vendedor_nome": cli['vendedor_nome'],
                        
                        # Injeção Atômica: No dia 1, todos os volumes nascem iguais à IA
                        "vol_ia": vol_final, 
                        "vol_topdown": vol_final, 
                        "vol_bottomup": vol_final,
                        "vol_supply": vol_final,
                        "vol_meta": vol_final,
                        "vol_final": vol_final,
                        
                        "pmv_aplicado": 0.0 # Será atualizado na próxima etapa do pipeline
                    })

        print(f"📤 [RATEIO] Salvando {len(dados_granulares)} linhas na Base Atômica (Upsert Inteligente)...")
        
        with SessionLocal() as db:
            try:
                lote_size = 10000
                for i in range(0, len(dados_granulares), lote_size):
                    lote = dados_granulares[i:i+lote_size]
                    
                    # 1. Prepara a Inserção
                    stmt = pg_insert(FatoIbpGranular).values(lote)
                    
                    # 2. O UPSERT (A Mágica da Proteção do S&OP)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['ciclo_sop', 'mes_projetado', 'sku', 'cgc'], # A Chave Única (Vaga)
                        set_={
                            'vol_ia': stmt.excluded.vol_ia # Atualiza SOMENTE a IA em caso de conflito
                        }
                    )
                    db.execute(stmt)
                
                db.commit()
                print("✅ [RATEIO] Operação de UPSERT concluída. Histórico humano protegido!")
            except Exception as e:
                db.rollback()
                print(f"❌ [ERRO RATEIO] Falha na gravação: {e}")
                raise e