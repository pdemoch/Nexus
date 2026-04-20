import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

class NexusLoader:
    def __init__(self):
        pass

    def executar_carga_silver(self, df_silver: pl.DataFrame, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import DimCliente, FatoVendas, DimProduto

        total_registros = len(df_silver)
        log_callback(f"   -> [SILVER] Processando {total_registros} registros para Injeção (Upsert)...")
        if df_silver.is_empty(): return

        db = SessionLocal()
        try:
            log_callback("      • Sincronizando Cadastro de Clientes e Hierarquias...")
            df_clientes = df_silver.select([
                "cgc", "cod_cliente", "loja", "cliente_razaosocial", 
                "regional", "bloqueado", "vendedor_nome", "gerente_nome"
            ]).unique(subset=["cgc"])

            for row in df_clientes.to_dicts():
                stmt = pg_insert(DimCliente).values(
                    cgc=row['cgc'], cod_cliente=row['cod_cliente'], loja=row['loja'],
                    razaosocial=row['cliente_razaosocial'], regional=row['regional'],
                    bloqueado=row['bloqueado'], vendedor_nome=row['vendedor_nome'], gerente_nome=row['gerente_nome']
                ).on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'razaosocial': row['cliente_razaosocial'], 'regional': row['regional'],
                        'bloqueado': row['bloqueado'], 'vendedor_nome': row['vendedor_nome'],
                        'gerente_nome': row['gerente_nome']
                    }
                )
                db.execute(stmt)

            log_callback("      • Sincronizando Dicionário de Produtos...")
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
            
            # =================================================================
            # O NOVO CORAÇÃO DO DELTA LOAD (FATO VENDAS COM UPSERT)
            # =================================================================
            log_callback("      • Iniciando injeção inteligente na Fato_Vendas (Upsert em Chunks de 5k)...")
            vendas_dicts = []
            processados = 0
            
            # Prepara a query de Upsert uma única vez
            stmt_vendas = pg_insert(FatoVendas)
            upsert_vendas = stmt_vendas.on_conflict_do_update(
                index_elements=['pedido', 'sku', 'cgc'], # A trava exata criada no domain_models
                set_={
                    'data_pedido': stmt_vendas.excluded.data_pedido,
                    'vendedor_nome': stmt_vendas.excluded.vendedor_nome,
                    'qt_pedido': stmt_vendas.excluded.qt_pedido,
                    'vl_pedido': stmt_vendas.excluded.vl_pedido
                }
            )
            
            for row in df_silver.to_dicts():
                dt_str = str(row['dtapedido'])
                try:
                    dt_obj = datetime.strptime(dt_str, "%Y%m%d").date()
                except:
                    dt_obj = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()

                vendas_dicts.append({
                    'pedido': str(row['pedido']), # Adicionado o número do pedido
                    'data_pedido': dt_obj, 
                    'sku': row['produto'], 
                    'cgc': row['cgc'],
                    'vendedor_nome': row['vendedor_nome'], 
                    'qt_pedido': row['qtpedido'], 
                    'vl_pedido': row['vlpedido']
                })
                
                processados += 1
                
                # Dispara o lote de Upserts no banco
                if len(vendas_dicts) >= 5000:
                    db.execute(upsert_vendas, vendas_dicts)
                    vendas_dicts.clear()
                    percentual = (processados / total_registros) * 100
                    log_callback(f"      ⏳ Progresso Vendas: {percentual:.1f}% ({processados}/{total_registros})")
            
            if vendas_dicts:
                db.execute(upsert_vendas, vendas_dicts)
            
            db.commit()
            log_callback("   -> [SILVER] Carga concluída com sucesso! Sem duplicação de dados.")
            
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoIbpGranular
        import pandas as pd
        
        if df_forecast.is_empty(): return
        
        ciclo_atual = df_forecast["ciclo_sop"][0]
        db = SessionLocal()
        
        try:
            log_callback(f"      • Inicializando Base do ciclo {ciclo_atual}...")
            db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo_atual).delete()
            db.commit()

            log_callback("      • Atualizando acurácia dos modelos na Dimensão de Produtos...")
            df_ia_meta = df_forecast.select(["produto", "modelo_vencedor", "acuracia"]).unique()
            for row in df_ia_meta.to_dicts():
                db.execute(
                    text("UPDATE dim_produtos SET modelo_vencedor = :mod, acuracia_ia = :acu WHERE sku = :sku"),
                    {"mod": row['modelo_vencedor'], "acu": row['acuracia'], "sku": row['produto']}
                )

            # CORREÇÃO ABSOLUTA: Busca histórico real do Banco de Dados para rateio, e não o "Delta" de 5 dias
            log_callback("      • Calculando matriz de Rateio Top-Down lendo histórico REAL do BD...")
            data_corte = (date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')
            
            sql_historico = text("""
                SELECT 
                    v.sku as produto, 
                    v.cgc, 
                    MAX(c.vendedor_nome) as vendedor_nome, 
                    SUM(v.qt_pedido) as vol_hist,
                    CASE WHEN SUM(v.qt_pedido) > 0 THEN SUM(v.vl_pedido)/SUM(v.qt_pedido) ELSE 0 END as pmv_ref
                FROM fato_vendas v
                JOIN dim_clientes c ON v.cgc = c.cgc
                WHERE v.data_pedido >= :data_corte
                  AND UPPER(COALESCE(c.bloqueado, 'ATIVO')) != 'INATIVO'
                GROUP BY v.sku, v.cgc
            """)
            
            df_hist_pd = pd.read_sql(sql_historico, db.bind, params={"data_corte": data_corte})
            df_pesos = pl.from_pandas(df_hist_pd)
            
            if df_pesos.is_empty():
                log_callback("      ⚠️ Nenhum histórico encontrado. Produtos cairão na gaveta 'SEM VENDEDOR'.")
                df_pesos = pl.DataFrame({"produto": [""], "cgc": ["00000000000000"], "vendedor_nome": ["SEM VENDEDOR"], "vol_hist": [0], "peso": [1.0], "pmv_ref": [0.0]})
            else:
                # Calcula a % (peso) de cada cliente dentro do SKU
                df_pesos = df_pesos.with_columns([
                    (pl.col("vol_hist") / pl.col("vol_hist").sum().over("produto")).fill_nan(0).alias("peso")
                ])
                # Se SKU não teve vendas nos últimos 12m, peso igual para todos que já compraram o SKU
                df_pesos = df_pesos.with_columns(
                    pl.when(pl.col("peso").sum().over("produto") == 0)
                      .then(1.0 / pl.col("cgc").count().over("produto"))
                      .otherwise(pl.col("peso")).alias("peso")
                )

            # Usa LEFT JOIN para não perder SKUs novos (Sem Vendas Anteriores)
            df_final = df_forecast.join(df_pesos, left_on="produto", right_on="produto", how="left")
            
            # Preenche os "órfãos" (SKUs Novos)
            df_final = df_final.with_columns([
                pl.col("cgc").fill_null("00000000000000"),
                pl.col("vendedor_nome").fill_null("NOVO PORTFÓLIO (SEM VENDEDOR)"),
                pl.col("peso").fill_null(1.0),
                pl.col("pmv_ref").fill_null(0.0)
            ])

            # Atomização da Meta
            df_final = df_final.with_columns(
                (pl.col("vol_ia_global") * pl.col("peso")).round(0).cast(pl.Int32).alias("vol_ia_atomico")
            ).filter(pl.col("vol_ia_atomico") > 0)

            total_ibp = len(df_final)
            log_callback(f"      • Iniciando injeção Estática S&OP (Chunks de 10k) - Total previsto: {total_ibp} linhas...")
            
            ibp_dicts = []
            processados = 0
            for row in df_final.to_dicts():
                ibp_dicts.append({
                    'ciclo_sop': ciclo_atual, 'mes_projetado': row['mes_projetado'], 'sku': row['produto'],
                    'cgc': row['cgc'], 'vendedor_nome': row['vendedor_nome'], 'vol_ia': row['vol_ia_atomico'],
                    'vol_topdown': row['vol_ia_atomico'], 'vol_bottomup': row['vol_ia_atomico'], 'vol_final': row['vol_ia_atomico'],      
                    'pmv_aplicado': row['pmv_ref']
                })
                processados += 1

                if len(ibp_dicts) >= 10000:
                    db.bulk_insert_mappings(FatoIbpGranular, ibp_dicts)
                    ibp_dicts.clear()
                    log_callback(f"      ⏳ Progresso S&OP: {((processados / total_ibp) * 100):.1f}% ({processados}/{total_ibp})")
            
            if ibp_dicts:
                db.bulk_insert_mappings(FatoIbpGranular, ibp_dicts)

            db.commit()
            log_callback(f"   -> [S&OP] Base Estática criada com Sucesso! ({total_ibp} células atomizadas)")
            
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()