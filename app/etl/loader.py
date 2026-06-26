import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

class NexusLoader:
    def __init__(self):
        pass

    def executar_carga_silver(self, df_silver: pl.DataFrame, data_inicio: date, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import DimCliente, FatoVendas

        total_registros = len(df_silver)
        log_callback(f"   -> [SILVER] Processando {total_registros} registros na Janela Deslizante (a partir de {data_inicio})...")
        if df_silver.is_empty(): return

        db = SessionLocal()
        try:
            log_callback("      • Sincronizando Cadastro de Clientes e Hierarquias (Upsert)...")
            
            df_clientes = df_silver.select([
                "cgc", "cod_cliente", "loja", "cliente_razaosocial", 
                "regional", "bloqueado", "vendedor_nome", "gerente_nome", "supervisor_nome" 
            ]).unique(subset=["cgc"])

            for row in df_clientes.to_dicts():
                stmt = pg_insert(DimCliente).values(
                    cgc=row['cgc'], cod_cliente=row['cod_cliente'], loja=row['loja'],
                    razaosocial=row['cliente_razaosocial'], regional=row['regional'],
                    bloqueado=row['bloqueado'], vendedor_nome=row['vendedor_nome'],
                    gerente_nome=row['gerente_nome'], supervisor_nome=row['supervisor_nome']
                ).on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'razaosocial': row['cliente_razaosocial'],
                        'regional': row['regional'],
                        'bloqueado': row['bloqueado'],
                        'vendedor_nome': row['vendedor_nome'],
                        'gerente_nome': row['gerente_nome'],
                        'supervisor_nome': row['supervisor_nome']
                    }
                )
                db.execute(stmt)

            log_callback(f"      • Expurgo atómico de vendas canceladas/fantasmas (a partir de {data_inicio})...")
            db.execute(text("DELETE FROM fato_vendas WHERE data_pedido >= :dt_inicio"), {"dt_inicio": data_inicio})

            log_callback("      • Injetando vendas purificadas e consolidadas no banco de dados...")
            
            vendas_dicts = []
            for row in df_silver.to_dicts():
                dt_str = str(row['dtapedido'])
                if len(dt_str) == 8:
                    dt_obj = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:]))
                else:
                    dt_obj = row['dtapedido'] 

                # MAPEAMENTO ATUALIZADO COM OS NOMES CORRETOS DO BANCO
                vendas_dicts.append({
                    "pedido": row.get('pedido', 'S/N'),
                    "sku": row['produto'],
                    "cgc": row['cgc'],
                    "data_pedido": dt_obj,
                    "vendedor_nome": row.get('vendedor_nome', 'S/I'),
                    "qt_pedido": row.get('qtpedido', 0.0) or 0.0,
                    "vl_pedido": row.get('vlpedido', 0.0) or 0.0,
                    
                    # CORREÇÃO: Removido o underline para bater com o PostgreSQL
                    "qtfatura": row.get('qtfatura', 0.0) or 0.0,
                    "qtcorte": row.get('qtcorte', 0.0) or 0.0,
                    "vlfatura": row.get('vlfatura', 0.0) or 0.0,
                    "vlcorte": row.get('vlcorte', 0.0) or 0.0
                })

            if vendas_dicts:
                # O bulk_insert_mappings é extremamente veloz!
                db.bulk_insert_mappings(FatoVendas, vendas_dicts)

            db.commit()
            log_callback("✅ [SILVER] Janela de Faturamento atualizada com sucesso. Nenhuma duplicação detetada.")

        except Exception as e:
            db.rollback() 
            log_callback(f"❌ [ERRO LOADER] Falha crítica na injeção Silver: {str(e)}")
            raise e
        finally:
            db.close()

    def executar_carga_orcamento(self, df_orcamento: pl.DataFrame, log_callback=print):
        if df_orcamento.is_empty(): return
        from app.core.database import SessionLocal
        from sqlalchemy import text
        
        db = SessionLocal()
        try:
            log_callback("      • Sincronizando Base de Orçamento Financeiro (Meta Anual)...")
            
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS fato_orcamento (
                    sku VARCHAR(255),
                    mes_projetado DATE,
                    receita_orcamento NUMERIC(15,2),
                    PRIMARY KEY (sku, mes_projetado)
                )
            """))
            
            orc_dicts = df_orcamento.to_dicts()
            
            query = text("""
                INSERT INTO fato_orcamento (sku, mes_projetado, receita_orcamento)
                VALUES (:sku, :mes_projetado, :receita_orcamento)
                ON CONFLICT (sku, mes_projetado) 
                DO UPDATE SET receita_orcamento = EXCLUDED.receita_orcamento
            """)
            
            db.execute(query, orc_dicts)
            db.commit()
            log_callback("✅ [LOADER] Orçamento Financeiro injetado com sucesso!")
        except Exception as e:
            db.rollback()
            log_callback(f"❌ [LOADER] Erro ao carregar orçamento: {e}")
            raise e
        finally:
            db.close()

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, ciclo_alvo: str, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoIbpGranular
        from sqlalchemy import text

        if df_forecast.is_empty():
            log_callback("❌ [LOAD] O DataFrame do Forecast está vazio.")
            return

        db = SessionLocal() 
        try:
            ciclo_atual = ciclo_alvo 
            log_callback(f"   -> [LOAD] Iniciando construção da matriz FatoIBP para o ciclo {ciclo_atual}...")
            
            log_callback("      • Registrando performance e vencedores do Ensemble no Banco...")
            df_modelos = df_forecast.group_by("produto").agg([pl.col("modelo_vencedor").first(), pl.col("acuracia").first()]).to_dicts()
            
            for row in df_modelos:
                db.execute(text("UPDATE dim_produtos SET modelo_vencedor = :mod, acuracia_ia = :acc WHERE sku = :sku"), {"mod": row["modelo_vencedor"], "acc": row["acuracia"], "sku": row["produto"]})

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

            log_callback("      • Aplicando Rateio Atômico com Método do Maior Resto (Vetorizado)...")
            df_forecast = df_forecast.rename({"produto": "sku"})
            
            if df_share.is_empty():
                log_callback("⚠️ [LOAD] Nenhum share encontrado. Abortando injeção.")
                return
            else:
                df_share = df_share.rename({"sku": "sku_share"})
                df_join = df_forecast.join(df_share, left_on="sku", right_on="sku_share", how="inner")
                df_join = df_join.filter(pl.col("share_cliente") > 0)

                if df_join.is_empty():
                    log_callback("⚠️ [LOAD] Após cruzar com clientes ativos, nenhuma projeção sobreviveu. Injeção abortada.")
                    return

                df_join = df_join.with_columns((pl.col("vol_ia_global") * pl.col("share_cliente")).alias("vol_exato")).with_columns([
                    pl.col("vol_exato").floor().cast(pl.Int32).alias("vol_base"),
                    (pl.col("vol_exato") - pl.col("vol_exato").floor()).alias("fracao")
                ])
                
                df_rem = df_join.group_by(["sku", "mes_projetado"]).agg((pl.col("vol_ia_global").first() - pl.col("vol_base").sum()).cast(pl.Int32).alias("sobra"))
                df_join = df_join.join(df_rem, on=["sku", "mes_projetado"])
                
                df_join = df_join.with_columns(pl.col("fracao").rank(method="ordinal", descending=True).over(["sku", "mes_projetado"]).alias("rank_fracao"))
                
                df_final = df_join.with_columns(
                    pl.when(pl.col("rank_fracao") <= pl.col("sobra")).then(pl.col("vol_base") + 1).otherwise(pl.col("vol_base")).alias("vol_ia_atomico"),
                    pl.col("pmv_aplicado").alias("pmv_ref")
                )

            total_ibp = len(df_final)
            log_callback(f"      • Iniciando injeção Estática S&OP (Filtro Estrito) - Total previsto: {total_ibp} linhas...")
            
            ibp_dicts = []
            for row in df_final.to_dicts():
                ibp_dicts.append({
                    'ciclo_sop': ciclo_atual, 'mes_projetado': row['mes_projetado'], 'sku': row['sku'], 'cgc': row['cgc'], 'vendedor_nome': row['vendedor_nome'], 
                    'vol_ia': row['vol_ia_atomico'], 'vol_topdown': row['vol_ia_atomico'], 'vol_bottomup': row['vol_ia_atomico'],
                    'vol_supply': row['vol_ia_atomico'], 'vol_meta': row['vol_ia_atomico'], 'vol_final': row['vol_ia_atomico'], 'pmv_aplicado': row['pmv_ref']
                })
                if len(ibp_dicts) >= 10000:
                    db.bulk_insert_mappings(FatoIbpGranular, ibp_dicts)
                    ibp_dicts.clear()
            if ibp_dicts: db.bulk_insert_mappings(FatoIbpGranular, ibp_dicts)
            
            db.commit()
            log_callback("✅ [LOAD] S&OP Injetado! Nenhuma caixa perdida no rateio, apenas Lixo descartado.")
        except Exception as e:
            db.rollback()
            log_callback(f"❌ [LOAD] Erro Crítico no Rateio: {str(e)}")
            raise e
        finally:
            db.close()

    def atualizar_hierarquia_historica(self, lf_clientes: pl.LazyFrame, log_callback=print) -> None:
        from app.core.database import SessionLocal
        from sqlalchemy import text
        
        log_callback("      • Sincronizando Histórico de Vendas com a Hierarquia Atual (Retroativo)...")
        db = SessionLocal()
        try:
            df_clientes = lf_clientes.select(["cgc", "vendedor_nome", "gerente_nome", "supervisor_nome"]).unique(subset=["cgc"]).collect()
            if df_clientes.is_empty(): return

            query_temp = text("""
                CREATE TEMP TABLE temp_clientes_hierarquia (
                    cgc VARCHAR(255), vendedor_nome VARCHAR(255), gerente_nome VARCHAR(255), supervisor_nome VARCHAR(255)
                ) ON COMMIT DROP;
            """)
            db.execute(query_temp)

            dados_clientes = df_clientes.to_dicts()
            query_insert = text("INSERT INTO temp_clientes_hierarquia (cgc, vendedor_nome, gerente_nome, supervisor_nome) VALUES (:cgc, :vendedor_nome, :gerente_nome, :supervisor_nome)")
            db.execute(query_insert, dados_clientes)

            query_update = text("""
                WITH hierarquia_atualizada AS (
                    UPDATE fato_vendas f SET vendedor_nome = t.vendedor_nome FROM temp_clientes_hierarquia t WHERE f.cgc = t.cgc AND f.vendedor_nome IS DISTINCT FROM t.vendedor_nome RETURNING f.cgc
                ) SELECT count(*) FROM hierarquia_atualizada;
            """)
            linhas_vendas_atualizadas = db.execute(query_update).scalar() or 0

            query_update_dim = text("""
                WITH dim_atualizada AS (
                    UPDATE dim_clientes d SET vendedor_nome = t.vendedor_nome, gerente_nome = t.gerente_nome, supervisor_nome = t.supervisor_nome FROM temp_clientes_hierarquia t WHERE d.cgc = t.cgc AND (d.vendedor_nome IS DISTINCT FROM t.vendedor_nome OR d.gerente_nome IS DISTINCT FROM t.gerente_nome OR d.supervisor_nome IS DISTINCT FROM t.supervisor_nome) RETURNING d.cgc
                ) SELECT count(*) FROM dim_atualizada;
            """)
            linhas_dim_atualizadas = db.execute(query_update_dim).scalar() or 0

            db.commit()
            log_callback(f"✅ [LOADER] Histórico Sincronizado! {linhas_vendas_atualizadas} Vendas antigas e {linhas_dim_atualizadas} Clientes atualizados.")

        except Exception as e:
            db.rollback()
            log_callback(f"❌ [LOADER] Erro ao sincronizar hierarquia histórica: {e}")
            raise e
        finally:
            db.close()
    
    def executar_carga_mtrix(self, ciclo_alvo: str, log_callback=print):
        from app.core.database import SessionLocal
        from app.models.domain_models import FatoMtrixSnapshot, FatoMtrixHistoricoMensal
        from sqlalchemy import text

        log_callback(f"   -> [MTRIX] Iniciando Data Cleansing e sumarização do S3 (Ciclo {ciclo_alvo})...")

        try:
            bucket_path = "s3://nexus-datalake-linea-prd/mtrix"

            try:
                df_prod = pl.read_parquet(f"{bucket_path}/sellout_produtos.parquet")
                df_prod = df_prod.select([
                    pl.col("PRODUCT_CODE").cast(pl.Utf8),
                    pl.col("PRODUCT_SKU_CODE").cast(pl.Utf8).str.strip_chars().alias("sku")
                ])
            except Exception as e:
                log_callback(f"      ⚠️ Aviso Prod S3: {e}")
                df_prod = pl.DataFrame(schema={"PRODUCT_CODE": pl.Utf8, "sku": pl.Utf8})

            try:
                df_dist = pl.read_parquet(f"{bucket_path}/sellout_distribuidores.parquet")
                df_dist = df_dist.select([
                    pl.col("DISTRIBUTOR_CODE").cast(pl.Utf8),
                    pl.col("DISTRIBUTOR_ID").cast(pl.Utf8).str.replace_all(r"\D", "").str.zfill(14).alias("cgc")
                ])
            except Exception as e:
                log_callback(f"      ⚠️ Aviso Dist S3: {e}")
                df_dist = pl.DataFrame(schema={"DISTRIBUTOR_CODE": pl.Utf8, "cgc": pl.Utf8})

            # =================================================================
            # 2. PROCESSAR ESTOQUE (GARANTINDO O ÚLTIMO DIA DO MÊS)
            # =================================================================
            log_callback("      • Mapeando posições de estoque no canal (Filtrando Último Dia Válido)...")
            
            coluna_estoque_alvo = "QTY_CONV2"

            try:
                lf_estoque = pl.scan_parquet(f"{bucket_path}/sellout_estoque_*.parquet")
                df_estoque = (
                    lf_estoque
                    .with_columns([
                        pl.col("DISTRIBUTOR_CODE").cast(pl.Utf8),
                        pl.col("PRODUCT_CODE").cast(pl.Utf8),
                        pl.col(coluna_estoque_alvo).cast(pl.Float64, strict=False).fill_null(0.0),
                        pl.col("STOCK_DATE").cast(pl.Utf8)
                    ])
                    .group_by(["DISTRIBUTOR_CODE", "PRODUCT_CODE"])
                    .agg(
                        pl.col(coluna_estoque_alvo).sort_by("STOCK_DATE", descending=True).first().alias("estoque_atual_caixas")
                    )
                ).collect()
            except Exception as e:
                log_callback(f"      ⚠️ Aviso Estoque S3: {e}")
                df_estoque = pl.DataFrame(schema={"DISTRIBUTOR_CODE": pl.Utf8, "PRODUCT_CODE": pl.Utf8, "estoque_atual_caixas": pl.Float64})

            # =================================================================
            # 3. PROCESSAR SELL-OUT (HISTÓRICO E M-1) - QTY_CONV2
            # =================================================================
            log_callback(f"      • Sumarizando volume de saída em Caixas ({coluna_estoque_alvo})...")
            try:
                lf_sellout = pl.scan_parquet(f"{bucket_path}/sellout_sellout_*.parquet")
                
                df_sellout_mensal = (
                    lf_sellout
                    .with_columns([
                        pl.col("DISTRIBUTOR_CODE").cast(pl.Utf8),
                        pl.col("PRODUCT_CODE").cast(pl.Utf8),
                        pl.col("SELLOUT_DATE").cast(pl.Utf8).str.slice(0, 7).alias("mes_ano"),
                        pl.col(coluna_estoque_alvo).cast(pl.Float64, strict=False).fill_null(0.0)
                    ])
                    .group_by(["DISTRIBUTOR_CODE", "PRODUCT_CODE", "mes_ano"])
                    .agg(pl.col(coluna_estoque_alvo).sum().alias("volume_sellout"))
                ).collect()
                
                if len(df_sellout_mensal) > 0:
                    ultimo_mes_str = df_sellout_mensal.select(pl.col("mes_ano").max()).item()
                    df_sellout_m1 = (
                        df_sellout_mensal
                        .filter(pl.col("mes_ano") == ultimo_mes_str)
                        .select([
                            "DISTRIBUTOR_CODE", 
                            "PRODUCT_CODE", 
                            pl.col("volume_sellout").alias("sellout_m1_caixas")
                        ])
                    )
                else:
                    df_sellout_m1 = pl.DataFrame(schema={"DISTRIBUTOR_CODE": pl.Utf8, "PRODUCT_CODE": pl.Utf8, "sellout_m1_caixas": pl.Float64})
                    
            except Exception as e:
                log_callback(f"      ⚠️ Aviso Sell-out S3: {e}")
                df_sellout_mensal = pl.DataFrame(schema={"DISTRIBUTOR_CODE": pl.Utf8, "PRODUCT_CODE": pl.Utf8, "mes_ano": pl.Utf8, "volume_sellout": pl.Float64})
                df_sellout_m1 = pl.DataFrame(schema={"DISTRIBUTOR_CODE": pl.Utf8, "PRODUCT_CODE": pl.Utf8, "sellout_m1_caixas": pl.Float64})

            # =================================================================
            # 4. HARMONIZAÇÃO E CRUZAMENTO FINAL
            # =================================================================
            log_callback("      • Cruzando matrizes de Distribuição com ERP Linea...")
            
            df_snap = df_estoque.join(df_sellout_m1, on=["DISTRIBUTOR_CODE", "PRODUCT_CODE"], how="outer").fill_null(0.0)
            df_snap = df_snap.join(df_prod, on="PRODUCT_CODE", how="left").join(df_dist, on="DISTRIBUTOR_CODE", how="left")
            
            df_snap = df_snap.drop_nulls(subset=["sku", "cgc"])
            
            df_snap = df_snap.with_columns(
                pl.when(pl.col("sellout_m1_caixas") > 0)
                .then((pl.col("estoque_atual_caixas") / pl.col("sellout_m1_caixas")) * 30.0)
                .otherwise(999.0)
                .alias("dias_cobertura")
            )

            df_hist = df_sellout_mensal.join(df_prod, on="PRODUCT_CODE", how="left").join(df_dist, on="DISTRIBUTOR_CODE", how="left")
            df_hist = df_hist.drop_nulls(subset=["sku", "cgc"])

            # =================================================================
            # 5. INJEÇÃO ATÓMICA NO POSTGRESQL
            # =================================================================
            db = SessionLocal()
            try:
                db.execute(text("DELETE FROM fato_mtrix_snapshot WHERE ciclo_sop = :c"), {"c": ciclo_alvo})
                db.execute(text("DELETE FROM fato_mtrix_historico_mensal WHERE ciclo_sop = :c"), {"c": ciclo_alvo})
                
                snap_dicts = df_snap.with_columns(pl.lit(ciclo_alvo).alias("ciclo_sop"), pl.lit(0.0).alias("previsao_sellout_m0")).to_dicts()
                hist_dicts = df_hist.with_columns(pl.lit(ciclo_alvo).alias("ciclo_sop")).to_dicts()

                if snap_dicts: db.bulk_insert_mappings(FatoMtrixSnapshot, snap_dicts)
                if hist_dicts: db.bulk_insert_mappings(FatoMtrixHistoricoMensal, hist_dicts)

                db.commit()
                log_callback(f"✅ [MTRIX] Data Lake processado! {len(snap_dicts)} posições e {len(hist_dicts)} meses gravados.")
            except Exception as e:
                db.rollback()
                raise e
            finally:
                db.close()

        except Exception as general_e:
            log_callback(f"❌ [MTRIX] Erro Crítico de Pipeline: {general_e}")