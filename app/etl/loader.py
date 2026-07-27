import polars as pl
import pandas as pd
import numpy as np
import traceback
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal, engine
from app.models.domain_models import FatoIbpGranular, FatoVendas, DimCliente, FatoOrcamento, FatoEstoqueD0


class NexusLoader:
    def __init__(self):
        pass

    def executar_carga_silver(self, df_silver: pl.DataFrame, data_inicio: date, log_callback=print):
        log_callback(f"⏳ [LOAD] Apagando vendas a partir de {data_inicio} e substituindo pelos dados extraídos...")
        try:
            # 🔥 FILTRO DE INTEGRALIDADE: Garante apenas os campos existentes na fato_vendas
            colunas_vendas = ["pedido", "data_pedido", "sku", "cgc", "vendedor_nome", "qt_pedido", "vl_pedido", "qtfatura", "qtcorte", "vlfatura", "vlcorte"]
            df_vendas = df_silver.select(colunas_vendas).to_dicts()
            if not df_vendas:
                log_callback("⚠️ [LOAD] Nenhum dado encontrado para carga.")
                return

            with SessionLocal() as db:
                # 1. Deleção massiva do período exato que foi extraído
                db.execute(text("DELETE FROM fato_vendas WHERE data_pedido >= :dt"), {"dt": data_inicio})

                # 2. Inserção direta e bruta (bulk insert) muito mais rápida que o UPSERT
                lote_size = 5000
                for i in range(0, len(df_vendas), lote_size):
                    lote = df_vendas[i:i+lote_size]
                    db.bulk_insert_mappings(FatoVendas, lote)

                db.commit()
            log_callback("✅ [LOAD] Histórico recente recarregado (Drop & Replace) com sucesso.")
        except Exception as e:
            log_callback(f"❌ [LOAD] Erro na carga de histórico: {e}")
            raise e

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, ciclo_alvo: str, log_callback=print):
        """
        Nascimento do Ciclo (Rateio Tático):
        1. Recebe a Previsão da IA (Nível SKU Nacional).
        2. Calcula Share de Clientes (Últimos 6 Meses).
        3. Fatiamento por Maior Resto.
        4. Grava o ciclo com PMV ZERADO — a precificação é etapa separada
           (executar_precificacao_ciclo), rodada logo em seguida no pipeline.
        """
        log_callback(f"⏳ [LOAD] Iniciando Rateio Tático (Share 6M) para o ciclo {ciclo_alvo}...")

        hoje = date.today()
        corte_6m = (hoje - relativedelta(months=6)).strftime("%Y-%m-%d")

        try:
            log_callback("   • Extraindo Matriz de Share (6M)...")
            # Apenas o share comercial. O PMV NÃO é mais calculado aqui.
            query = f"""
                SELECT v.sku, v.cgc, c.vendedor_nome, SUM(v.qt_pedido) as vol_cliente
                FROM fato_vendas v
                JOIN dim_clientes c ON v.cgc = c.cgc
                WHERE v.data_pedido >= '{corte_6m}'
                  AND UPPER(TRIM(COALESCE(c.bloqueado, 'ATIVO'))) != 'INATIVO'
                GROUP BY v.sku, v.cgc, c.vendedor_nome
            """
            df_hist = pd.read_sql(query, engine)

            if df_hist.empty:
                log_callback("⚠️ [LOAD] Nenhum histórico recente encontrado para rateio.")
                return

            # Calcular a % que cada cliente representa no SKU
            total_por_sku = df_hist.groupby('sku')['vol_cliente'].sum().reset_index()
            total_por_sku.rename(columns={'vol_cliente': 'vol_total_sku'}, inplace=True)
            df_hist = df_hist.merge(total_por_sku, on='sku')
            df_hist['share_pct'] = np.where(df_hist['vol_total_sku'] > 0, df_hist['vol_cliente'] / df_hist['vol_total_sku'], 0)

            dados_granulares = []
            df_forecast_pd = df_forecast.to_pandas()

            log_callback("   • Fatiando volumes com Método do Maior Resto (PMV nasce zerado)...")

            for _, row in df_forecast_pd.iterrows():
                sku = str(row['sku'])
                vol_ia = float(row['vol_ia_global'])
                modelo_vencedor = str(row.get('modelo_vencedor', 'Media_Simples'))
                acuracia_ia = float(row.get('acuracia_ia', 50.0))

                clientes_sku = df_hist[df_hist['sku'] == sku].copy()
                if clientes_sku.empty:
                    continue

                # Método do Maior Resto (Fatiar sem deixar casas decimais perdidas)
                clientes_sku['vol_dist'] = clientes_sku['share_pct'] * vol_ia
                clientes_sku['vol_arr'] = np.floor(clientes_sku['vol_dist']).astype(int)
                clientes_sku['fracao'] = clientes_sku['vol_dist'] - clientes_sku['vol_arr']

                sobra = int(round(vol_ia - clientes_sku['vol_arr'].sum()))
                if sobra > 0:
                    clientes_sku = clientes_sku.sort_values(by='fracao', ascending=False)
                    clientes_sku.iloc[:sobra, clientes_sku.columns.get_loc('vol_arr')] += 1

                # Todas as caixas nascem iguais com a meta da IA cravada; PMV = 0 (definido depois)
                for _, cli in clientes_sku.iterrows():
                    vol_valor = int(cli['vol_arr'])
                    dados_granulares.append({
                        "ciclo_sop": ciclo_alvo,
                        "mes_projetado": row['mes_projetado'],
                        "sku": sku,
                        "cgc": cli['cgc'],
                        "vendedor_nome": cli['vendedor_nome'],
                        "vol_ia": vol_valor,
                        "vol_topdown": vol_valor,
                        "vol_bottomup": vol_valor,
                        "vol_supply": vol_valor,
                        "vol_meta": vol_valor,
                        "vol_final": vol_valor,
                        "pmv_aplicado": 0.0,   # <-- PMV definido na etapa de precificação
                        "modelo_vencedor": modelo_vencedor,
                        "acuracia_ia": round(acuracia_ia, 2)
                    })

            log_callback(f"   • Gravando {len(dados_granulares)} linhas atômicas no PostgreSQL...")

            with SessionLocal() as db:
                # 1. Garante que o ciclo nasce virgem e limpo, sem produtos fantasmas
                db.execute(text("DELETE FROM fato_ibp_granular WHERE ciclo_sop = :c"), {"c": ciclo_alvo})

                # 2. Injeta as linhas em velocidade Bulk
                lote_size = 1000
                for i in range(0, len(dados_granulares), lote_size):
                    stmt = pg_insert(FatoIbpGranular).values(dados_granulares[i:i+lote_size])
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['ciclo_sop', 'mes_projetado', 'sku', 'cgc'],
                        set_={
                            'vol_ia': stmt.excluded.vol_ia,
                            'vol_topdown': stmt.excluded.vol_topdown,
                            'vol_bottomup': stmt.excluded.vol_bottomup,
                            'vol_supply': stmt.excluded.vol_supply,
                            'vol_meta': stmt.excluded.vol_meta,
                            'vol_final': stmt.excluded.vol_final,
                            'pmv_aplicado': stmt.excluded.pmv_aplicado,
                            'modelo_vencedor': stmt.excluded.modelo_vencedor,
                            'acuracia_ia': stmt.excluded.acuracia_ia
                        }
                    )
                    db.execute(stmt)
                db.commit()
                log_callback("✅ [LOAD] Ciclo criado (volumes rateados, PMV pendente).")

        except Exception as e:
            log_callback(f"❌ [LOAD] Erro Crítico no Loader: {str(e)}")
            log_callback(traceback.format_exc())
            raise e

    def executar_precificacao_ciclo(self, ciclo_alvo: str, log_callback=print):
        """
        BASE DE PRECIFICAÇÃO GLOBAL. Roda uma única vez após o nascimento do
        ciclo. Preenche pmv_aplicado; NENHUM router recalcula preço a partir daqui.

        Passo 1 — par exato cgc×sku: PMV da ÚLTIMA venda válida (SUM(vl)/SUM(qt)
                  da data mais recente), sem limite temporal.
        Passo 2 — fallback: linhas que sobraram sem preço (par nunca vendeu)
                  recebem o PMV da última venda do SKU em QUALQUER cliente.

        'Venda válida' = qt_pedido > 0 E vl_pedido > 0 (exclui bonificação e
        devolução, que não são sinal de preço). Idempotente: pode reprecificar.
        SKU de NPD (sem venda em lugar nenhum) fica intocado — preserva o preço
        que o Marketing definiu na injeção.
        """
        log_callback(f"⏳ [PMV] Precificando ciclo {ciclo_alvo} pela última venda...")
        try:
            with SessionLocal() as db:
                # PASSO 1 — última venda válida do par cgc×sku
                r1 = db.execute(text("""
                    WITH ultima_data AS (
                        SELECT cgc, sku, MAX(data_pedido) AS data_ult
                        FROM fato_vendas
                        WHERE qt_pedido > 0 AND vl_pedido > 0
                        GROUP BY cgc, sku
                    ),
                    pmv_par AS (
                        SELECT v.cgc, v.sku,
                               SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0) AS pmv
                        FROM fato_vendas v
                        JOIN ultima_data u
                          ON u.cgc = v.cgc AND u.sku = v.sku
                         AND u.data_ult = v.data_pedido
                        WHERE v.qt_pedido > 0 AND v.vl_pedido > 0
                        GROUP BY v.cgc, v.sku
                    )
                    UPDATE fato_ibp_granular f
                    SET pmv_aplicado = ROUND(p.pmv::numeric, 2)
                    FROM pmv_par p
                    WHERE f.ciclo_sop = :ciclo
                      AND f.cgc = p.cgc
                      AND f.sku = p.sku
                """), {"ciclo": ciclo_alvo})

                # PASSO 2 — fallback: última venda do SKU em qualquer cliente,
                # só para linhas que continuaram sem preço (pmv = 0).
                r2 = db.execute(text("""
                    WITH ultima_data_sku AS (
                        SELECT sku, MAX(data_pedido) AS data_ult
                        FROM fato_vendas
                        WHERE qt_pedido > 0 AND vl_pedido > 0
                        GROUP BY sku
                    ),
                    pmv_sku AS (
                        SELECT v.sku,
                               SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0) AS pmv
                        FROM fato_vendas v
                        JOIN ultima_data_sku u
                          ON u.sku = v.sku AND u.data_ult = v.data_pedido
                        WHERE v.qt_pedido > 0 AND v.vl_pedido > 0
                        GROUP BY v.sku
                    )
                    UPDATE fato_ibp_granular f
                    SET pmv_aplicado = ROUND(p.pmv::numeric, 2)
                    FROM pmv_sku p
                    WHERE f.ciclo_sop = :ciclo
                      AND f.sku = p.sku
                      AND COALESCE(f.pmv_aplicado, 0) = 0
                """), {"ciclo": ciclo_alvo})

                db.commit()
                log_callback(
                    f"✅ [PMV] Passo 1 (par cgc×sku): {r1.rowcount} linhas | "
                    f"Passo 2 (fallback SKU): {r2.rowcount} linhas."
                )
        except Exception as e:
            log_callback(f"❌ [PMV] Erro na precificação do ciclo: {e}")
            log_callback(traceback.format_exc())
            raise e

    def executar_carga_clientes(self, df_clientes: pl.DataFrame, log_callback=print,
                                limiar_seguranca: float = 0.5):
        """
        Sincroniza a Dimensão de Clientes via UPSERT (sem purga, sem DELETE).
        A FK cgc é NO ACTION: qualquer DELETE que remova um cgc referenciado em
        fato_vendas/fato_ibp_granular quebra o pipeline. O UPSERT nunca desafia
        a FK. Cliente que sai do ERP apenas deixa de ser atualizado.
        """
        log_callback("⏳ [LOAD] Sincronizando Dimensão de Clientes (UPSERT)...")
        try:
            if df_clientes is None or df_clientes.is_empty():
                log_callback("⚠️ [LOAD] Extração de clientes vazia — dimensão preservada.")
                return

            df_pd = df_clientes.to_pandas()
            df_pd = df_pd.rename(columns={'cod': 'cod_cliente', 'cliente_razaosocial': 'razaosocial'})
            if 'regional' not in df_pd.columns:
                df_pd['regional'] = "N/A"

            colunas_permitidas = ['cgc', 'cod_cliente', 'loja', 'razaosocial', 'regional',
                                  'bloqueado', 'vendedor_nome', 'gerente_nome', 'supervisor_nome']
            df_pd = df_pd[[c for c in colunas_permitidas if c in df_pd.columns]]
            registros = df_pd.to_dict(orient='records')

            # DEDUP GLOBAL POR CGC, PRIORIZANDO ATIVO (cgc é PK; a 188 repete CNPJ/CPF).
            def _peso_ativo(r):
                return 1 if str(r.get("bloqueado") or "").strip().upper() == "ATIVO" else 0
            registros.sort(key=_peso_ativo)  # sort estável: ATIVO por último -> vence no dict

            deduplicados = {}
            for r in registros:
                cgc = str(r.get("cgc") or "").strip()
                if not cgc or cgc.upper() in ("SEM_CGC", "NULL", "NAN", "NONE"):
                    continue
                r["cgc"] = cgc
                deduplicados[cgc] = r
            registros = list(deduplicados.values())

            if not registros:
                log_callback("⚠️ [LOAD] Nenhum cliente com CGC válido — dimensão preservada.")
                return

            with SessionLocal() as db:
                # 🛡️ TRAVA ANTI-WIPE: barra 188 truncada de encolher a base.
                existentes = db.execute(text("SELECT COUNT(*) FROM dim_clientes")).scalar() or 0
                if existentes > 0 and len(registros) < existentes * limiar_seguranca:
                    log_callback(
                        f"🛑 [LOAD] Carga ABORTADA por segurança: {len(registros)} clientes "
                        f"contra {existentes} cadastrados (< {int(limiar_seguranca*100)}%). "
                        f"Verifique a extração da 188."
                    )
                    return

                lote = 1000
                for i in range(0, len(registros), lote):
                    bloco = registros[i:i+lote]
                    stmt = pg_insert(DimCliente).values(bloco)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['cgc'],
                        set_={c: getattr(stmt.excluded, c) for c in bloco[0].keys() if c != 'cgc'}
                    )
                    db.execute(stmt)
                db.commit()

            log_callback(f"✅ [LOAD] Dimensão sincronizada: {len(registros)} clientes (UPSERT).")
        except Exception as e:
            log_callback(f"❌ [LOAD] Erro na carga de clientes: {e}")
            raise e

    def executar_carga_orcamento(self, df_orc: pl.DataFrame, log_callback=print):
        log_callback("⏳ [LOAD] Substituindo Metas do Orçamento Financeiro...")
        try:
            if df_orc is None or df_orc.is_empty():
                log_callback("⚠️ [LOAD] Nenhum dado de orçamento recebido.")
                return

            registros = df_orc.to_dicts()

            with SessionLocal() as db:
                # 1. Apaga totalmente a tabela de Orçamento
                db.execute(text("DELETE FROM fato_orcamento"))

                # 2. Insere a planilha nova por cima
                lote_size = 5000
                for i in range(0, len(registros), lote_size):
                    lote = registros[i:i+lote_size]
                    db.bulk_insert_mappings(FatoOrcamento, lote)

                db.commit()
            log_callback("✅ [LOAD] Tabela de Orçamento substituída (Drop & Replace) com sucesso.")
        except Exception as e:
            log_callback(f"❌ [LOAD] Erro na carga de orçamento: {e}")
            raise e

    def executar_carga_estoque(self, df_estoque: pl.DataFrame, log_callback=print):
        """Fotografia Diária: Apaga o estoque de ontem e grava a posição de hoje."""
        log_callback("⏳ [LOAD] Atualizando Posição de Estoque D0 (Armazém)...")
        try:
            if df_estoque is None or df_estoque.is_empty():
                log_callback("⚠️ [LOAD] Nenhum dado de estoque recebido.")
                return

            registros = df_estoque.to_dicts()

            with SessionLocal() as db:
                # 1. Apaga a fotografia anterior inteira
                db.execute(text("DELETE FROM fato_estoque_d0"))

                # 2. Insere o novo cenário real
                lote_size = 5000
                for i in range(0, len(registros), lote_size):
                    lote = registros[i:i+lote_size]
                    db.bulk_insert_mappings(FatoEstoqueD0, lote)

                db.commit()
            log_callback("✅ [LOAD] Estoque D0 (API 90) sincronizado com sucesso.")
        except Exception as e:
            log_callback(f"❌ [LOAD] Erro na carga de estoque: {e}")
            raise e