import polars as pl
# 1. AQUI ESTÁ A CORREÇÃO DO DIALETO PARA POSTGRESQL
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

class NexusLoader:
    def __init__(self):
        pass

    def executar_carga_silver(self, df_silver: pl.DataFrame):
        from app.core.database import SessionLocal
        from app.models.domain_models import DimCliente, FatoVendas, DimProduto

        print(f"\n⚙️ [LOAD] Iniciando carga da Camada Silver ({len(df_silver)} registos)...")
        if df_silver.is_empty(): return

        db = SessionLocal()
        try:
            print("   -> Sincronizando Cadastro de Clientes e Hierarquias...")
            df_clientes = df_silver.select([
                "cgc", "cod_cliente", "loja", "cliente_razaosocial", 
                "regional", "bloqueado", "vendedor_nome", "gerente_nome"
            ]).unique(subset=["cgc"])

            for row in df_clientes.to_dicts():
                # USANDO O DIALETO POSTGRESQL
                stmt = pg_insert(DimCliente).values(
                    cgc=row['cgc'], cod_cliente=row['cod_cliente'], loja=row['loja'],
                    razaosocial=row['cliente_razaosocial'], regional=row['regional'],
                    bloqueado=row['bloqueado'], vendedor_nome=row['vendedor_nome'], gerente_nome=row['gerente_nome']
                ).on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'razaosocial': row['cliente_razaosocial'],
                        'regional': row['regional'],
                        'bloqueado': row['bloqueado'],
                        'vendedor_nome': row['vendedor_nome'],
                        'gerente_nome': row['gerente_nome']
                    }
                )
                db.execute(stmt)

            print("   -> Sincronizando Dicionário de Produtos...")
            df_prod = df_silver.select(["produto", "descricao", "bu", "categoria", "segmento", "curva_2026"]).unique(subset=["produto"])

            for row in df_prod.to_dicts():
                stmt = pg_insert(DimProduto).values(
                    sku=row['produto'], descricao=row['descricao'], bu=row['bu'],
                    categoria=row['categoria'], segmento=row['segmento'], curva=row['curva_2026']
                ).on_conflict_do_update(
                    index_elements=['sku'],
                    set_={
                        'descricao': row['descricao'], 'bu': row['bu'],
                        'categoria': row['categoria'], 'segmento': row['segmento'],
                        'curva': row['curva_2026']
                    }
                )
                db.execute(stmt)

            print("   -> Atualizando Histórico de Vendas Realizadas...")
            db.execute(text("DELETE FROM fato_vendas")) 
            db.commit() # Comita o delete primeiro para limpar a memória
            
            # 2. ESTRATÉGIA ANTI-ESTOURO DE MEMÓRIA NA AWS (Lotes de 10.000)
            vendas_dicts = []
            for row in df_silver.to_dicts():
                dt_str = str(row['dtapedido'])
                try:
                    dt_obj = datetime.strptime(dt_str, "%Y%m%d").date()
                except:
                    dt_obj = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()

                vendas_dicts.append({
                    'data_pedido': dt_obj, 'sku': row['produto'], 'cgc': row['cgc'],
                    'vendedor_nome': row['vendedor_nome'], 'qt_pedido': row['qtpedido'], 'vl_pedido': row['vlpedido']
                })

                # Se chegar a 10.000, guarda e esvazia a memória da AWS
                if len(vendas_dicts) >= 10000:
                    db.bulk_insert_mappings(FatoVendas, vendas_dicts)
                    vendas_dicts.clear()
            
            # Guarda o que sobrou no final
            if vendas_dicts:
                db.bulk_insert_mappings(FatoVendas, vendas_dicts)
            
            db.commit()
            print("✅ [LOAD] Camada Silver (Clientes/Produtos/Vendas) carregada!")
            
        except Exception as e:
            db.rollback()
            print(f"❌ [LOAD] Erro na carga Silver: {str(e)}")
            raise e
        finally:
            db.close()

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, df_silver: pl.DataFrame):
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoIbpGranular, DimProduto
        
        print("\n⚙️ [LOAD] Iniciando Injeção do Forecast e Rateio Atômico...")
        if df_forecast.is_empty(): return
        
        ciclo_atual = df_forecast["ciclo_sop"][0]
        db = SessionLocal()
        
        try:
            db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo_atual).delete()
            db.commit() # Limpa a memória do banco antes de inserir o novo

            print("   -> Atualizando métricas de Acurácia IA...")
            df_ia_meta = df_forecast.select(["produto", "modelo_vencedor", "acuracia"]).unique()
            for row in df_ia_meta.to_dicts():
                db.execute(
                    text("UPDATE dim_produtos SET modelo_vencedor = :mod, acuracia_ia = :acu WHERE sku = :sku"),
                    {"mod": row['modelo_vencedor'], "acu": row['acuracia'], "sku": row['produto']}
                )

            print("   -> Aplicando rateio cascata IA (Histórico 12 Meses)...")
            
            df_ativos = df_silver.filter(pl.col("bloqueado") != "INATIVO")
            
            data_corte = (date.today() - relativedelta(months=12)).strftime('%Y%m%d')
            df_12m = df_ativos.filter(pl.col("dtapedido").cast(pl.Utf8) >= data_corte)
            
            df_pesos = df_12m.group_by(["produto", "cgc", "vendedor_nome"]).agg(
                pl.col("qtpedido").sum().alias("vol_hist")
            )

            df_base = df_ativos.select(["produto", "cgc", "vendedor_nome"]).unique()
            df_pesos = df_base.join(df_pesos, on=["produto", "cgc", "vendedor_nome"], how="left").fill_null(0)
            
            df_pesos = df_pesos.with_columns(
                (pl.col("vol_hist") / pl.col("vol_hist").sum().over("produto")).fill_nan(0).alias("peso")
            )

            df_pesos = df_pesos.with_columns(
                pl.when(pl.col("peso").sum().over("produto") == 0)
                .then(1.0 / pl.col("cgc").count().over("produto"))
                .otherwise(pl.col("peso"))
                .alias("peso")
            )

            df_final = df_forecast.join(df_pesos, left_on="produto", right_on="produto", how="inner")
            df_final = df_final.with_columns(
                (pl.col("vol_ia_global") * pl.col("peso")).round(0).cast(pl.Int32).alias("vol_ia_atomico")
            ).filter(pl.col("vol_ia_atomico") > 0)

            df_pmv = df_silver.group_by(["produto", "cgc"]).agg(
                (pl.col("vlpedido").sum() / pl.col("qtpedido").sum()).fill_nan(0).alias("pmv_ref")
            )
            df_final = df_final.join(df_pmv, on=["produto", "cgc"], how="left")

            # 3. ESTRATÉGIA ANTI-ESTOURO APLICADA AO FORECAST
            ibp_dicts = []
            total_impactado = 0
            for row in df_final.to_dicts():
                ibp_dicts.append({
                    'ciclo_sop': ciclo_atual, 'mes_projetado': row['mes_projetado'], 'sku': row['produto'],
                    'cgc': row['cgc'], 'vendedor_nome': row['vendedor_nome'], 'vol_ia': row['vol_ia_atomico'],
                    'vol_topdown': row['vol_ia_atomico'], 'vol_bottomup': row['vol_ia_atomico'], 'vol_final': row['vol_ia_atomico'],      
                    'pmv_aplicado': row['pmv_ref'] or 0.0
                })
                total_impactado += 1

                if len(ibp_dicts) >= 10000:
                    db.bulk_insert_mappings(FatoIbpGranular, ibp_dicts)
                    ibp_dicts.clear()
            
            if ibp_dicts:
                db.bulk_insert_mappings(FatoIbpGranular, ibp_dicts)

            db.commit()
            print(f"✅ [LOAD] Injeção IBP concluída com Sucesso Histórico: {total_impactado} lojas impactadas.")
            
        except Exception as e:
            db.rollback()
            print(f"❌ [LOAD] Erro na Injeção IBP: {str(e)}")
        finally:
            db.close()