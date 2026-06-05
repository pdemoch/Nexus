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
                "regional", "bloqueado", "vendedor_nome", "gerente_nome", 
                "supervisor_nome" 
            ]).unique(subset=["cgc"])

            for row in df_clientes.to_dicts():
                stmt = pg_insert(DimCliente).values(
                    cgc=row['cgc'], cod_cliente=row['cod_cliente'], loja=row['loja'],
                    razaosocial=row['cliente_razaosocial'], regional=row['regional'],
                    bloqueado=row['bloqueado'], vendedor_nome=row['vendedor_nome'], 
                    gerente_nome=row['gerente_nome'],
                    supervisor_nome=row['supervisor_nome'] 
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'cod_cliente': stmt.excluded.cod_cliente, 'loja': stmt.excluded.loja,
                        'razaosocial': stmt.excluded.razaosocial, 'regional': stmt.excluded.regional,
                        'bloqueado': stmt.excluded.bloqueado, 'vendedor_nome': stmt.excluded.vendedor_nome, 
                        'gerente_nome': stmt.excluded.gerente_nome,
                        'supervisor_nome': stmt.excluded.supervisor_nome
                    }
                )
                db.execute(stmt)

            log_callback("      • Sincronizando Cadastro de Produtos (Portfólio)...")
            df_produtos = df_silver.select([
                "produto", "descricao", "bu", "categoria", "segmento", "curva_2026"
            ]).unique(subset=["produto"])

            for row in df_produtos.to_dicts():
                stmt_prod = pg_insert(DimProduto).values(
                    sku=row['produto'], descricao=row['descricao'], bu=row['bu'],
                    categoria=row['categoria'], segmento=row['segmento'], curva=row['curva_2026']
                )
                stmt_prod = stmt_prod.on_conflict_do_update(
                    index_elements=['sku'],
                    set_={
                        'descricao': stmt_prod.excluded.descricao, 'bu': stmt_prod.excluded.bu,
                        'categoria': stmt_prod.excluded.categoria, 'segmento': stmt_prod.excluded.segmento,
                        'curva': stmt_prod.excluded.curva
                    }
                )
                db.execute(stmt_prod)

            log_callback("      • Realizando Upsert Atômico na Fato_Vendas (S&OE Ready)...")
            
            vendas_dicts = df_silver.select([
                "pedido", "dtapedido", "produto", "cgc", "vendedor_nome", 
                "qtpedido", "vlpedido", "qtfatura", "qtcorte" 
            ]).to_dicts()

            for row in vendas_dicts:
                qt_fatura_val = row.get('qtfatura') or 0.0
                qt_corte_val = row.get('qtcorte') or 0.0

                stmt_vendas = pg_insert(FatoVendas).values(
                    pedido=row['pedido'], data_pedido=row['dtapedido'],
                    sku=row['produto'], cgc=row['cgc'], vendedor_nome=row['vendedor_nome'],
                    qt_pedido=row['qtpedido'], vl_pedido=row['vlpedido'],
                    qtfatura=qt_fatura_val, qtcorte=qt_corte_val 
                )
                
                stmt_vendas = stmt_vendas.on_conflict_do_update(
                    constraint='uix_vendas_pedido',
                    set_={
                        'qt_pedido': stmt_vendas.excluded.qt_pedido,
                        'vl_pedido': stmt_vendas.excluded.vl_pedido,
                        'vendedor_nome': stmt_vendas.excluded.vendedor_nome,
                        'qtfatura': stmt_vendas.excluded.qtfatura, 
                        'qtcorte': stmt_vendas.excluded.qtcorte    
                    }
                )
                db.execute(stmt_vendas)

            db.commit()
            log_callback("✅ [SILVER] Upsert concluído com sucesso!")
        except Exception as e:
            db.rollback()
            log_callback(f"❌ [SILVER] Erro no Upsert: {str(e)}")
            raise e

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, ciclo_alvo: str, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoIbpGranular
        import numpy as np

        if df_forecast.is_empty():
            log_callback("❌ [LOAD] O DataFrame do Forecast está vazio.")
            return

        db = SessionLocal() 
        try:
            ciclo_atual = ciclo_alvo 
            log_callback(f"   -> [LOAD] Iniciando construção da matriz FatoIBP para o ciclo {ciclo_atual}...")
            
            # BLINDAGEM DO RATEIO: Exclui INATIVOS usando o mesmo padrão do shared_ibp
            query_share = text("""
                WITH cte_base AS (
                    SELECT v.sku, v.cgc, c.vendedor_nome, SUM(v.qt_pedido) as total_cliente
                    FROM fato_vendas v
                    JOIN dim_clientes c ON v.cgc = c.cgc
                    WHERE v.data_pedido >= CURRENT_DATE - INTERVAL '6 months'
                      AND UPPER(TRIM(COALESCE(c.bloqueado, 'ATIVO'))) != 'INATIVO'
                    GROUP BY v.sku, v.cgc, c.vendedor_nome
                ), cte_total AS (
                    SELECT sku, SUM(total_cliente) as total_sku
                    FROM cte_base GROUP BY sku
                )
                SELECT b.sku, b.cgc, b.vendedor_nome, 
                       COALESCE(b.total_cliente / NULLIF(t.total_sku, 0), 0) as share_cliente
                FROM cte_base b
                JOIN cte_total t ON b.sku = t.sku
                WHERE b.total_cliente > 0
            """)
            res_share = db.execute(query_share).fetchall()
            df_share = pl.DataFrame([dict(r._mapping) for r in res_share]) if res_share else pl.DataFrame()

            log_callback("      • Aplicando Rateio Atômico (Down-scaling) nos dados da IA...")
            
            if df_share.is_empty():
                df_final = df_forecast.with_columns([
                    pl.lit("00000000000000").alias("cgc"),
                    pl.lit("SEM VENDEDOR").alias("vendedor_nome"),
                    pl.col("vol_ia_global").alias("vol_ia_atomico"),
                    pl.col("pmv_aplicado").alias("pmv_ref")
                ])
            else:
                df_share = df_share.rename({"sku": "produto"})
                df_join = df_forecast.join(df_share, on="produto", how="left")
                
                df_join = df_join.with_columns([
                    pl.col("cgc").fill_null("00000000000000"),
                    pl.col("vendedor_nome").fill_null("SEM VENDEDOR"),
                    pl.col("share_cliente").fill_null(1.0)
                ])
                
                # AQUI FOI REMOVIDO O FILTRO ( > 0 ) PARA GARANTIR QUE ZEROS SEJAM GRAVADOS
                df_final = df_join.with_columns(
                    (pl.col("vol_ia_global") * pl.col("share_cliente")).round(0).cast(pl.Int32).alias("vol_ia_atomico"),
                    pl.col("pmv_aplicado").alias("pmv_ref")
                )

            total_ibp = len(df_final)
            log_callback(f"      • Iniciando injeção Estática S&OP (Chunks de 10k) - Total previsto: {total_ibp} linhas...")
            
            ibp_dicts = []
            processados = 0
            for row in df_final.to_dicts():
                ibp_dicts.append({
                    'ciclo_sop': ciclo_atual, 
                    'mes_projetado': row['mes_projetado'], 
                    'sku': row['produto'],
                    'cgc': row['cgc'], 
                    'vendedor_nome': row['vendedor_nome'], 
                    'vol_ia': row['vol_ia_atomico'],
                    'vol_topdown': row['vol_ia_atomico'], 
                    'vol_bottomup': row['vol_ia_atomico'],
                    'vol_supply': row['vol_ia_atomico'],
                    'vol_meta': row['vol_ia_atomico'],                    
                    'vol_final': row['vol_ia_atomico'],      
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
            log_callback("✅ [LOAD] S&OP Injetado e Congelado na Base de Dados!")
        except Exception as e:
            db.rollback()
            log_callback(f"❌ [LOAD] Erro Crítico no Rateio: {str(e)}")
            raise e

    def atualizar_hierarquia_historica(self, lf_clientes: pl.LazyFrame, log_callback=print) -> None:
        from app.core.database import SessionLocal
        
        log_callback("      • Sincronizando Histórico de Vendas com a Hierarquia Atual (Retroativo)...")
        db = SessionLocal()
        try:
            df_clientes = lf_clientes.select([
                "cgc", "vendedor_nome", "gerente_nome", "supervisor_nome"
            ]).unique(subset=["cgc"]).collect()

            if df_clientes.is_empty():
                log_callback("⚠️ [LOADER] Cadastro de clientes vazio. Pulando sincronização histórica.")
                return

            query_temp = text("""
                CREATE TEMP TABLE temp_clientes_hierarquia (
                    cgc VARCHAR(255),
                    vendedor_nome VARCHAR(255),
                    gerente_nome VARCHAR(255),
                    supervisor_nome VARCHAR(255)
                ) ON COMMIT DROP;
            """)
            db.execute(query_temp)

            dados_clientes = df_clientes.to_dicts()
            
            query_insert = text("""
                INSERT INTO temp_clientes_hierarquia (cgc, vendedor_nome, gerente_nome, supervisor_nome)
                VALUES (:cgc, :vendedor_nome, :gerente_nome, :supervisor_nome)
            """)
            db.execute(query_insert, dados_clientes)

            query_update = text("""
                WITH hierarquia_atualizada AS (
                    UPDATE fato_vendas f
                    SET vendedor_nome = t.vendedor_nome
                    FROM temp_clientes_hierarquia t
                    WHERE f.cgc = t.cgc
                      AND f.vendedor_nome IS DISTINCT FROM t.vendedor_nome
                    RETURNING f.cgc
                )
                SELECT count(*) FROM hierarquia_atualizada;
            """)
            res = db.execute(query_update)
            linhas_vendas_atualizadas = res.scalar() or 0

            query_update_dim = text("""
                WITH dim_atualizada AS (
                    UPDATE dim_clientes d
                    SET vendedor_nome = t.vendedor_nome,
                        gerente_nome = t.gerente_nome,
                        supervisor_nome = t.supervisor_nome
                    FROM temp_clientes_hierarquia t
                    WHERE d.cgc = t.cgc
                    AND (
                        d.vendedor_nome IS DISTINCT FROM t.vendedor_nome OR
                        d.gerente_nome IS DISTINCT FROM t.gerente_nome OR
                        d.supervisor_nome IS DISTINCT FROM t.supervisor_nome
                    )
                    RETURNING d.cgc
                )
                SELECT count(*) FROM dim_atualizada;
            """)
            res_dim = db.execute(query_update_dim)
            linhas_dim_atualizadas = res_dim.scalar() or 0

            db.commit()
            log_callback(f"✅ [LOADER] Histórico Sincronizado! {linhas_vendas_atualizadas} Vendas antigas e {linhas_dim_atualizadas} Clientes atualizados.")

        except Exception as e:
            db.rollback()
            log_callback(f"❌ [LOADER] Erro ao sincronizar hierarquia histórica: {e}")
            raise e
        finally:
            db.close()