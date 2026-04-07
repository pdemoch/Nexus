import polars as pl
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import text
from datetime import datetime

class NexusLoader:
    def __init__(self):
        pass

    def executar_carga_silver(self, df_silver: pl.DataFrame):
        """
        Recebe a base harmonizada do Transformer e popula DimCliente, DimProduto e FatoVendas.
        Converte as datas brutas em objetos date reais para o SQLite.
        """
        from app.core.database import SessionLocal
        from app.models.domain_models import DimCliente, FatoVendas, DimProduto

        print(f"\n⚙️ [LOAD] Iniciando carga da Camada Silver ({len(df_silver)} registros)...")
        if df_silver.is_empty(): return

        db = SessionLocal()
        try:
            # 1. ATUALIZA DIM_CLIENTES (Unique por CGC)
            print("   -> Sincronizando Cadastro de Clientes e Hierarquias...")
            df_clientes = df_silver.select([
                "cgc", "cod_cliente", "loja", "cliente_razaosocial", 
                "regional", "bloqueado", "vendedor_nome", "gerente_nome"
            ]).unique(subset=["cgc"])

            for row in df_clientes.to_dicts():
                stmt = sqlite_insert(DimCliente).values(
                    cgc=row['cgc'],
                    cod_cliente=row['cod_cliente'],
                    loja=row['loja'],
                    razaosocial=row['cliente_razaosocial'],
                    regional=row['regional'],
                    bloqueado=row['bloqueado'],
                    vendedor_nome=row['vendedor_nome'],
                    gerente_nome=row['gerente_nome']
                ).on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'razaosocial': sqlite_insert(DimCliente).excluded.razaosocial,
                        'regional': sqlite_insert(DimCliente).excluded.regional,
                        'bloqueado': sqlite_insert(DimCliente).excluded.bloqueado,
                        'vendedor_nome': sqlite_insert(DimCliente).excluded.vendedor_nome,
                        'gerente_nome': sqlite_insert(DimCliente).excluded.gerente_nome
                    }
                )
                db.execute(stmt)

            # 2. ATUALIZA DIM_PRODUTOS (Metadados básicos vindos da Silver)
            print("   -> Sincronizando Dicionário de Produtos...")
            df_prod = df_silver.select([
                "produto", "descricao", "bu", "categoria", "segmento", "curva_2026"
            ]).unique(subset=["produto"])

            for row in df_prod.to_dicts():
                stmt = sqlite_insert(DimProduto).values(
                    sku=row['produto'],
                    descricao=row['descricao'],
                    bu=row['bu'],
                    categoria=row['categoria'],
                    segmento=row['segmento'],
                    curva=row['curva_2026']
                ).on_conflict_do_update(
                    index_elements=['sku'],
                    set_={
                        'descricao': sqlite_insert(DimProduto).excluded.descricao,
                        'bu': sqlite_insert(DimProduto).excluded.bu,
                        'categoria': sqlite_insert(DimProduto).excluded.categoria,
                        'segmento': sqlite_insert(DimProduto).excluded.segmento,
                        'curva': sqlite_insert(DimProduto).excluded.curva
                    }
                )
                db.execute(stmt)

            # 3. ATUALIZA FATO_VENDAS (Limpando e injetando com data convertida)
            print("   -> Atualizando Histórico de Vendas Realizadas...")
            db.execute(text("DELETE FROM fato_vendas")) 
            
            vendas_objetos = []
            for row in df_silver.to_dicts():
                # CONVERSÃO DE DATA: Transforma '20220103' em objeto date real
                dt_str = str(row['dtapedido'])
                try:
                    dt_obj = datetime.strptime(dt_str, "%Y%m%d").date()
                except:
                    # Fallback para caso o Transformer já tenha formatado com traços
                    dt_obj = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()

                vendas_objetos.append(
                    FatoVendas(
                        data_pedido=dt_obj,
                        sku=row['produto'],
                        cgc=row['cgc'],
                        vendedor_nome=row['vendedor_nome'],
                        qt_pedido=row['qtpedido'],
                        vl_pedido=row['vlpedido']
                    )
                )
            
            # Processamento em lotes para não estourar a memória (chunks de 50k)
            chunk_size = 50000
            for i in range(0, len(vendas_objetos), chunk_size):
                db.bulk_save_objects(vendas_objetos[i : i + chunk_size])
                db.flush()
            
            db.commit()
            print("✅ [LOAD] Camada Silver (Clientes/Produtos/Vendas) carregada!")
            
        except Exception as e:
            db.rollback()
            print(f"❌ [LOAD] Erro na carga Silver: {str(e)}")
            raise e
        finally:
            db.close()

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, df_silver: pl.DataFrame):
        """
        Calcula o rateio atômico e injeta no FatoIbpGranular.
        Nova Lógica (Cascata de Herança): Todos os volumes nascem com o valor da IA.
        """
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoIbpGranular, DimProduto
        
        print("\n⚙️ [LOAD] Iniciando Injeção do Forecast e Rateio Atômico...")
        if df_forecast.is_empty(): return
        
        ciclo_atual = df_forecast["ciclo_sop"][0]
        db = SessionLocal()
        
        try:
            # 1. Limpa o ciclo atual para evitar duplicidade
            db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo_atual).delete()
            
            # 2. Atualiza Metadados de IA nos Produtos
            print("   -> Atualizando métricas de Acurácia IA...")
            df_ia_meta = df_forecast.select(["produto", "modelo_vencedor", "acuracia"]).unique()
            for row in df_ia_meta.to_dicts():
                db.execute(
                    text("UPDATE dim_produtos SET modelo_vencedor = :mod, acuracia_ia = :acu WHERE sku = :sku"),
                    {"mod": row['modelo_vencedor'], "acu": row['acuracia'], "sku": row['produto']}
                )

            # 3. Motor de Rateio Inicial (Distribuição por cliente baseada no histórico)
            print("   -> Aplicando rateio inicial da IA por cliente...")
            df_ativos = df_silver.filter(pl.col("bloqueado") != "INATIVO")
            
            df_pesos = df_ativos.group_by(["produto", "cgc", "vendedor_nome"]).agg(
                pl.col("qtpedido").sum().alias("vol_hist")
            ).with_columns(
                (pl.col("vol_hist") / pl.col("vol_hist").sum().over("produto")).fill_nan(0).alias("peso")
            )

            # Join com o Forecast 
            df_final = df_forecast.join(df_pesos, left_on="produto", right_on="produto", how="inner")
            df_final = df_final.with_columns(
                (pl.col("vol_ia_global") * pl.col("peso")).round(0).cast(pl.Int32).alias("vol_ia_atomico")
            ).filter(pl.col("vol_ia_atomico") > 0)

            # Busca PMVs vigentes (da Silver)
            df_pmv = df_silver.group_by(["produto", "cgc"]).agg(
                (pl.col("vlpedido").sum() / pl.col("qtpedido").sum()).fill_nan(0).alias("pmv_ref")
            )
            df_final = df_final.join(df_pmv, on=["produto", "cgc"], how="left")

            # A MÁGICA ACONTECE AQUI: Herança Direta nas 4 camadas.
            objetos_ibp = [
                FatoIbpGranular(
                    ciclo_sop=ciclo_atual,
                    mes_projetado=row['mes_projetado'],
                    sku=row['produto'],
                    cgc=row['cgc'],
                    vendedor_nome=row['vendedor_nome'],
                    vol_ia=row['vol_ia_atomico'],
                    vol_topdown=row['vol_ia_atomico'],    # <-- INICIA COM O VALOR DA IA
                    vol_bottomup=row['vol_ia_atomico'],   # <-- INICIA COM O VALOR DA IA
                    vol_final=row['vol_ia_atomico'],      # <-- INICIA COM O VALOR DA IA (NOVO)
                    pmv_aplicado=row['pmv_ref'] or 0.0
                ) for row in df_final.to_dicts()
            ]

            # Injeção em lotes para as metas atômicas
            chunk_size = 50000 
            for i in range(0, len(objetos_ibp), chunk_size):
                db.bulk_save_objects(objetos_ibp[i : i + chunk_size])

            db.commit()
            print(f"✅ [LOAD] Injeção IBP concluída: {len(objetos_ibp)} registros herdaram o sinal da IA nas 4 camadas.")
            
        except Exception as e:
            db.rollback()
            print(f"❌ [LOAD] Erro na Injeção IBP: {str(e)}")
        finally:
            db.close()