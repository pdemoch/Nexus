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
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'cod_cliente': stmt.excluded.cod_cliente, 'loja': stmt.excluded.loja,
                        'razaosocial': stmt.excluded.razaosocial, 'regional': stmt.excluded.regional,
                        'bloqueado': stmt.excluded.bloqueado, 'vendedor_nome': stmt.excluded.vendedor_nome, 
                        'gerente_nome': stmt.excluded.gerente_nome
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
            
            # ATENÇÃO: Adicionado qtfatura e qtcorte na seleção
            vendas_dicts = df_silver.select([
                "pedido", "dtapedido", "produto", "cgc", "vendedor_nome", 
                "qtpedido", "vlpedido", "qtfatura", "qtcorte" 
            ]).to_dicts()

            for row in vendas_dicts:
                # Segurança caso o campo venha nulo nalguma anomalia do ERP
                qt_fatura_val = row.get('qtfatura') or 0.0
                qt_corte_val = row.get('qtcorte') or 0.0

                stmt_vendas = pg_insert(FatoVendas).values(
                    pedido=row['pedido'], data_pedido=row['dtapedido'],
                    sku=row['produto'], cgc=row['cgc'], vendedor_nome=row['vendedor_nome'],
                    qt_pedido=row['qtpedido'], vl_pedido=row['vlpedido'],
                    qtfatura=qt_fatura_val, qtcorte=qt_corte_val # Injeção S&OE
                )
                
                # Regra de atualização em caso de conflito (O mesmo pedido atualizado no ERP)
                stmt_vendas = stmt_vendas.on_conflict_do_update(
                    constraint='uix_vendas_pedido',
                    set_={
                        'qt_pedido': stmt_vendas.excluded.qt_pedido,
                        'vl_pedido': stmt_vendas.excluded.vl_pedido,
                        'vendedor_nome': stmt_vendas.excluded.vendedor_nome,
                        'qtfatura': stmt_vendas.excluded.qtfatura, # O Faturamento vai subindo
                        'qtcorte': stmt_vendas.excluded.qtcorte    # Se cortar hoje, reflete aqui
                    }
                )
                db.execute(stmt_vendas)

            db.commit()
            log_callback("✅ [SILVER] Upsert concluído com sucesso!")
        except Exception as e:
            db.rollback()
            log_callback(f"❌ [SILVER] Erro no Upsert: {str(e)}")
            raise e

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoIbpGranular
        from app.api.routers.shared_ibp import get_current_cycle
        import numpy as np

        if df_forecast.is_empty():
            log_callback("❌ [LOAD] O DataFrame do Forecast está vazio.")
            return

        ciclo_atual = get_current_cycle()
        log_callback(f"   -> [LOAD] Iniciando construção da matriz FatoIBP para o ciclo {ciclo_atual}...")

        db = SessionLocal()
        try:
            from sqlalchemy import text
            query_share = text("""
                WITH cte_base AS (
                    SELECT v.sku, v.cgc, c.vendedor_nome, SUM(v.qt_pedido) as total_cliente
                    FROM fato_vendas v
                    JOIN dim_clientes c ON v.cgc = c.cgc
                    WHERE v.data_pedido >= CURRENT_DATE - INTERVAL '6 months'
                      AND UPPER(COALESCE(c.bloqueado, 'ATIVO')) != 'INATIVO'
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
                
                df_final = df_join.with_columns(
                    (pl.col("vol_ia_global") * pl.col("share_cliente")).round(0).cast(pl.Int32).alias("vol_ia_atomico"),
                    pl.col("pmv_aplicado").alias("pmv_ref")
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
            log_callback("✅ [LOAD] S&OP Injetado e Congelado na Base de Dados!")
        except Exception as e:
            db.rollback()
            log_callback(f"❌ [LOAD] Erro Crítico no Rateio: {str(e)}")
            raise e