import pandas as pd
import numpy as np
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from app.core.database import engine, SessionLocal
from app.models.domain_models import FatoIbpGranular

class TopDownDistributor:
    def __init__(self, meses_historico=6):
        self.meses_historico = meses_historico

    def _obter_share_historico(self) -> pd.DataFrame:
        print("📥 [RATEIO] Buscando histórico de Share (últimos 6 meses) no Banco de Dados...")
        hoje = date.today()
        data_corte = (hoje - relativedelta(months=self.meses_historico)).strftime("%Y-%m-%d")

        # JOIN milimétrico entre a Fato de Vendas e a Dimensão Cliente para buscar quem comprou o quê
        # FILTRO CRÍTICO: Exclui clientes inativos do rateio futuro!
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
              AND UPPER(COALESCE(c.bloqueado, 'ATIVO')) != 'INATIVO'  -- <-- BLINDAGEM AQUI
            GROUP BY v.sku, v.cgc, c.cod_cliente, c.loja, c.razaosocial, c.regional, c.vendedor_nome
        """
        df_silver = pd.read_sql(query, engine)
        if df_silver.empty: return df_silver

        # Calcula o Market Share de cada Loja/CGC dentro daquele Produto
        df_silver['qtpedido'] = pd.to_numeric(df_silver['qtpedido'], errors='coerce').fillna(0)
        df_cliente = df_silver[df_silver['qtpedido'] > 0]
        
        df_total_produto = df_cliente.groupby('produto')['qtpedido'].sum().reset_index().rename(columns={'qtpedido': 'total_produto'})
        df_share = pd.merge(df_cliente, df_total_produto, on='produto')
        df_share['share_historico'] = df_share['qtpedido'] / df_share['total_produto']
        
        return df_share

    def executar_rateio(self, df_forecast: pd.DataFrame, ciclo_atual: str):
        print("\n==================================================")
        print("🍰 INICIANDO DISTRIBUIÇÃO TOP-DOWN (RATEIO ATÔMICO)")
        print("==================================================")
        
        if df_forecast.empty:
            print("❌ [RATEIO] Nenhum forecast recebido da IA.")
            return

        df_share = self._obter_share_historico()
        dados_granulares = []

        for _, row in df_forecast.iterrows():
            produto = row['sku']
            vol_ia_total = row['volume_projetado']
            mes_proj = row['mes_projetado']
            
            clientes_produto = df_share[df_share['produto'] == produto].copy() if not df_share.empty else pd.DataFrame()
            
            # Se é um produto novo sem histórico, aloca a uma conta "Orfã" temporariamente
            if clientes_produto.empty:
                dados_granulares.append({
                    "ciclo_sop": ciclo_atual, "mes_projetado": mes_proj, "sku": produto,
                    "cgc": "00000000000000", "vendedor_nome": "SEM VENDEDOR",
                    "vol_ia": vol_ia_total, "vol_topdown": vol_ia_total, 
                    "vol_bottomup": vol_ia_total, "pmv_aplicado": 0.0
                })
            else:
                # Matemática pura: Algoritmo do Maior Resto
                clientes_produto['vol_exato'] = vol_ia_total * clientes_produto['share_historico']
                clientes_produto['vol_arredondado'] = np.floor(clientes_produto['vol_exato']).astype(int)
                clientes_produto['fracao'] = clientes_produto['vol_exato'] - clientes_produto['vol_arredondado']
                
                sobra = int(round(vol_ia_total - clientes_produto['vol_arredondado'].sum()))
                clientes_produto = clientes_produto.sort_values(by='fracao', ascending=False)
                
                if sobra > 0:
                    indices = clientes_produto.index[:sobra]
                    clientes_produto.loc[indices, 'vol_arredondado'] += 1

                for _, cli in clientes_produto.iterrows():
                    vol_final = cli['vol_arredondado']
                    if vol_final > 0:
                        dados_granulares.append({
                            "ciclo_sop": ciclo_atual, "mes_projetado": mes_proj, "sku": produto,
                            "cgc": cli['cgc'], "vendedor_nome": cli['vendedor_nome'],
                            "vol_ia": vol_final, "vol_topdown": vol_final, 
                            "vol_bottomup": vol_final, "pmv_aplicado": 0.0
                        })

        print(f"📤 [RATEIO] Salvando {len(dados_granulares)} linhas na Base Atômica (Fato_IBP_Granular)...")
        
        with SessionLocal() as db:
            try:
                # Idempotência: Se o script falhar e rodar duas vezes, não duplica. Limpa o ciclo atual antes.
                db.execute(text("DELETE FROM fato_ibp_granular WHERE ciclo_sop = :ciclo"), {"ciclo": ciclo_atual})
                
                # Bulk Insert (O mesmo usado no Loader, imune a timeouts)
                lote_size = 15000
                for i in range(0, len(dados_granulares), lote_size):
                    db.bulk_insert_mappings(FatoIbpGranular, dados_granulares[i:i+lote_size])
                
                db.commit()
                print("✅ [RATEIO] Rateio concluído com integridade de 100%!")
            except Exception as e:
                db.rollback()
                print(f"❌ [RATEIO] Erro Crítico ao salvar o rateio: {str(e)}")
                raise e