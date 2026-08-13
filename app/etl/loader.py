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

    def executar_carga_produtos(self, df_silver, df_seg, log_callback=print):
        """
        Sincroniza dim_produtos: UNIAO dos SKUs vendidos (fato_vendas manda) com
        os SKUs do Segmentos (portfolio), enriquecidos e MARCADOS por status.

        PRINCIPIOS:
          1. A dimensao e IMORTAL: nunca DELETE. SKU que ja foi vendido ou
             planejado permanece para sempre (FK de fato_vendas e fato_ibp).
          2. O que muda e o STATUS (coluna ativo):
               ativo = TRUE  -> curva valida no Segmentos (ABC 1-2 letras ou
                                LANCAMENTO). E o que o S&OP PLANEJA.
               ativo = FALSE -> DESCONTINUADO / ECOMMERCE / EXPORTACAO / sem
                                curva / fora do Segmentos. Existe para o
                                historico e a auditoria, mas nao entra no plano.
          3. A cada pipeline o Segmentos e a "lista de chamada": quem tem curva
             valida LIGA; todos os demais DESLIGAM. Descontinuar no Excel
             desliga aqui sozinho no proximo pipeline.
        """
        log_callback("⏳ [LOAD] Sincronizando Dimensão de Produtos (vendas ∪ Segmentos + status ativo)...")
        try:
            import re as _re

            # ------------------------------------------------------------
            # 0. AUTO-MIGRACAO: garante a coluna ativo (idempotente).
            # ------------------------------------------------------------
            with SessionLocal() as db:
                db.execute(text("ALTER TABLE dim_produtos ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE"))
                db.commit()

            # ------------------------------------------------------------
            # 1. SKUs vendidos (df_silver) — o pai da dimensao..
            # ------------------------------------------------------------
            skus_vendas = set()
            desc_reais = {}  # sku -> descricao REAL do produto (vinda da API 150)
            if df_silver is not None and not (hasattr(df_silver, "is_empty") and df_silver.is_empty()):
                df_v = df_silver.to_pandas() if hasattr(df_silver, "to_pandas") else df_silver.copy()
                if "sku" in df_v.columns:
                    df_v["_sku"] = df_v["sku"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
                    skus_vendas = {
                        s for s in df_v["_sku"].unique()
                        if s and s.upper() not in ("NAN", "NONE", "")
                    }
                    # A DESCRICAO REAL vem das VENDAS (API 150) — o Segmentos nao
                    # tem descricao. Nunca usar categoria como descricao (bug que
                    # sobrescreveu os nomes reais dos produtos nas telas).
                    if "descricao" in df_v.columns:
                        d = df_v.dropna(subset=["descricao"])
                        d = d[d["descricao"].astype(str).str.strip() != ""]
                        desc_reais = d.groupby("_sku")["descricao"].first().astype(str).str.strip().to_dict()

            # ------------------------------------------------------------
            # 2. Enriquecimento + status a partir do Segmentos COMPLETO.
            # ------------------------------------------------------------
            def _curva_ativa(v: str) -> bool:
                v = (v or "").strip().upper()
                return bool(_re.match(r"^[A-Z]{1,2}$", v)) or v in ("LANÇAMENTO", "LANCAMENTO")

            enrich = {}
            skus_ativos = set()
            if df_seg is not None and not (hasattr(df_seg, "is_empty") and df_seg.is_empty()):
                seg_pd = df_seg.to_pandas() if hasattr(df_seg, "to_pandas") else df_seg.copy()
                col_prod = next((c for c in seg_pd.columns if str(c).lower() == "produto"), None)
                if col_prod:
                    def _col(nome):
                        return next((c for c in seg_pd.columns if str(c).lower() == nome), None)
                    c_bu, c_cat, c_seg, c_curva = _col("bu"), _col("categoria"), _col("segmento"), _col("2026")
                    seg_pd["_sku"] = seg_pd[col_prod].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
                    for _, r in seg_pd.iterrows():
                        sku = str(r["_sku"]).strip()
                        if not sku or sku.upper() in ("NAN", "NONE", ""):
                            continue
                        curva = (str(r[c_curva]).strip().upper() if c_curva and pd.notna(r[c_curva]) else "")
                        enrich[sku] = {
                            "bu": (str(r[c_bu]).strip() if c_bu and pd.notna(r[c_bu]) else None),
                            "categoria": (str(r[c_cat]).strip() if c_cat and pd.notna(r[c_cat]) else None),
                            "segmento": (str(r[c_seg]).strip() if c_seg and pd.notna(r[c_seg]) else None),
                            "curva": curva or None,
                        }
                        if _curva_ativa(curva):
                            skus_ativos.add(sku)

            # UNIAO: vendas + Segmentos (LANCAMENTO pode nunca ter vendido e
            # precisa existir para a FK do fato_ibp quando for planejado).
            todos_skus = skus_vendas | set(enrich.keys())
            if not todos_skus:
                log_callback("⚠️ [LOAD] Nenhum SKU para sincronizar.")
                return

            registros = []
            for sku in todos_skus:
                e = enrich.get(sku, {})
                registros.append({
                    "sku": sku,
                    # Descricao REAL (da venda/API 150). None quando o SKU nao
                    # vendeu na janela — e ai o UPSERT PRESERVA a descricao que
                    # ja existe no banco (nunca rebaixa para categoria/codigo).
                    "descricao": desc_reais.get(sku),
                    "categoria": e.get("categoria") or "SEM CATEGORIA",
                    "segmento": e.get("segmento") or "SEM SEGMENTO",
                    "bu": e.get("bu") or "SEM BU",
                    "curva": e.get("curva") or "SEM_CURVA",
                    "ativo": sku in skus_ativos,
                })

            with SessionLocal() as db:
                cols_existentes = {row[0] for row in db.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'dim_produtos'
                """)).fetchall()}

                novos = 0
                for reg in registros:
                    campos = {k: v for k, v in reg.items() if k in cols_existentes}
                    if "sku" not in campos:
                        continue
                    cols = list(campos.keys())
                    # descricao tem tratamento especial nos dois lados:
                    #   INSERT: COALESCE(:descricao, :sku) — novo SKU sem venda
                    #           entra com o codigo (melhor que NULL).
                    #   UPDATE: COALESCE(:descricao, dim_produtos.descricao) —
                    #           so troca quando chega descricao REAL; nunca
                    #           sobrescreve a existente com nada pior.
                    set_parts = []
                    val_parts = []
                    for c in cols:
                        if c == "descricao":
                            val_parts.append("COALESCE(:descricao, :sku)")
                            set_parts.append("descricao = COALESCE(:descricao, dim_produtos.descricao)")
                        else:
                            val_parts.append(f":{c}")
                            if c != "sku":
                                set_parts.append(f"{c} = EXCLUDED.{c}")
                    set_clause = ", ".join(set_parts)
                    col_names = ", ".join(cols)
                    placeholders = ", ".join(val_parts)
                    sql = f"""
                        INSERT INTO dim_produtos ({col_names})
                        VALUES ({placeholders})
                        ON CONFLICT (sku) DO UPDATE SET {set_clause}
                    """ if set_clause else f"""
                        INSERT INTO dim_produtos ({col_names})
                        VALUES ({placeholders})
                        ON CONFLICT (sku) DO NOTHING
                    """
                    db.execute(text(sql), campos)
                    novos += 1

                # ----------------------------------------------------
                # 3. LISTA DE CHAMADA: desliga quem NAO tem curva valida
                # no Segmentos atual (descontinuados, ecommerce, exportacao,
                # sem curva, fora do Segmentos). Liga quem tem.
                # Nunca remove ninguem — so muda o status.
                # ----------------------------------------------------
                if "ativo" in cols_existentes or True:  # coluna garantida pela auto-migracao
                    lista_ativos = list(skus_ativos) if skus_ativos else ["__nenhum__"]
                    db.execute(text("UPDATE dim_produtos SET ativo = (sku = ANY(:lst))"),
                               {"lst": lista_ativos})
                db.commit()

            log_callback(
                f"✅ [LOAD] Dimensão de Produtos: {novos} SKU(s) sincronizados "
                f"({len(skus_ativos)} ativos no portfólio, {novos - len([s for s in todos_skus if s in skus_ativos])} inativos/históricos)."
            )
        except Exception as e:
            log_callback(f"⚠️ [LOAD] Erro ao sincronizar dim_produtos: {e}")
            # Defensivo: nao derruba o pipeline. O filtro no loader de vendas protege a FK.

    def executar_carga_silver(self, df_silver: pl.DataFrame, data_inicio: date,
                              log_callback=print, recarga_total: bool = False):
        if recarga_total:
            log_callback("⏳ [LOAD] RECARGA TOTAL — apagando TODA a fato_vendas (TRUNCATE) e reinserindo...")
        else:
            log_callback(f"⏳ [LOAD] Apagando vendas a partir de {data_inicio} e substituindo pelos dados extraídos...")
        try:
            # 🔥 FILTRO DE INTEGRALIDADE: Garante apenas os campos existentes na fato_vendas
            colunas_vendas = ["pedido", "data_pedido", "sku", "cgc", "vendedor_nome", "qt_pedido", "vl_pedido", "qtfatura", "qtcorte", "vlfatura", "vlcorte"]
            df_vendas = df_silver.select(colunas_vendas).to_dicts()
            if not df_vendas:
                log_callback("⚠️ [LOAD] Nenhum dado encontrado para carga.")
                return

            with SessionLocal() as db:
                # ============================================================
                # BLINDAGEM DE FK: descarta vendas cujo SKU nao existe na
                # dim_produtos. O filtro de portfolio (Segmentos.xlsx) pode
                # deixar passar SKUs que nao estao na dim_produtos (fontes
                # distintas), e a FK fato_vendas_sku_fkey barraria a carga
                # inteira. Descartar essas linhas orfas mantem a carga viva;
                # elas nao pertencem ao portfolio oficial de qualquer forma.
                # ============================================================
                skus_validos = {r[0] for r in db.execute(text("SELECT sku FROM dim_produtos")).fetchall()}
                total_antes = len(df_vendas)
                df_vendas_ok = [v for v in df_vendas if v["sku"] in skus_validos]
                descartadas = total_antes - len(df_vendas_ok)
                if descartadas > 0:
                    orfaos = sorted({v["sku"] for v in df_vendas if v["sku"] not in skus_validos})
                    amostra = ", ".join(orfaos[:10])
                    log_callback(
                        f"⚠️ [LOAD] {descartadas} venda(s) de {len(orfaos)} SKU(s) fora da dim_produtos "
                        f"foram descartadas (nao pertencem ao portfolio). SKUs: {amostra}"
                    )
                df_vendas = df_vendas_ok
                if not df_vendas:
                    log_callback("⚠️ [LOAD] Apos filtro de portfolio, nenhuma venda restou para carga.")
                    return

                # 1. Deleção — TRUNCATE na recarga total, DELETE janelado no modo normal
                if recarga_total:
                    # TRUNCATE é ordens de magnitude mais rápido que DELETE total
                    # e reseta os índices. CASCADE cobre FKs dependentes se houver.
                    db.execute(text("TRUNCATE TABLE fato_vendas"))
                    log_callback("🗑️ [LOAD] TRUNCATE executado — fato_vendas limpa.")
                else:
                    db.execute(text("DELETE FROM fato_vendas WHERE data_pedido >= :dt"), {"dt": data_inicio})

                # 2. Inserção direta e bruta (bulk insert) muito mais rápida que o UPSERT
                lote_size = 5000
                for i in range(0, len(df_vendas), lote_size):
                    lote = df_vendas[i:i+lote_size]
                    db.bulk_insert_mappings(FatoVendas, lote)

                db.commit()
            modo_label = "RECARGA TOTAL" if recarga_total else "Drop & Replace"
            log_callback(f"✅ [LOAD] Histórico recarregado ({modo_label}) — {len(df_vendas)} vendas.")
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
        BASE DE PRECIFICAÇÃO DO CICLO ATIVO. Preenche pmv_aplicado com o preço
        mais representativo da realidade recente, em CASCATA de 4 níveis.

        Roda SOMENTE no ciclo_alvo (o ciclo ativo selecionado no Admin). Ciclos
        passados são imutáveis e nunca são tocados aqui (o WHERE fixa o ciclo).

        Janela: últimos 3 meses de venda (preço atual, não histórico velho).
        Todos os níveis usam MÉDIA PONDERADA POR VOLUME = SUM(vl)/SUM(qt), que
        dá peso natural a quem compra mais (o preço efetivo do SKU), nunca a
        média simples de PMVs (que superestima onde há dispersão de preço).

        Cascata (cada nível só preenche o que o anterior deixou em pmv=0):
          1. CNPJ×SKU (3m)      — o preço do próprio cliente para o próprio SKU.
          2. REGIONAL×SKU (3m)  — preço médio ponderado da regional do cliente.
          3. SKU geral (3m)     — preço médio ponderado do SKU (todas regionais).
          4. Última venda do SKU (qualquer data) — rede de segurança final.

        'Venda válida' = qt_pedido > 0 E vl_pedido > 0 (exclui bonificação e
        devolução). SKU de NPD sem venda alguma fica intocado (preserva o preço
        que o Marketing definiu na injeção). Idempotente.
        """
        log_callback(f"⏳ [PMV] Precificando ciclo {ciclo_alvo} (cascata 4 níveis, janela 3m)...")
        try:
            with SessionLocal() as db:
                # ----------------------------------------------------------------
                # RESET — Opção A (recalcular tudo). Zera o PMV do ciclo ativo
                # ANTES da cascata. Sem isto, numa REPRECIFICAÇÃO as linhas já têm
                # preço antigo (pmv>0), e os níveis de fallback (que preenchem só
                # onde pmv=0) nunca rodam — deixando clientes sem venda recente com
                # preço velho em vez do fallback regional/SKU. O reset garante que
                # a cascata recalcula do zero, cada nível cobrindo o que o anterior
                # não alcançou. Só toca o ciclo ativo; passados são imutáveis.
                r0 = db.execute(text("""
                    UPDATE fato_ibp_granular
                    SET pmv_aplicado = 0
                    WHERE ciclo_sop = :ciclo
                """), {"ciclo": ciclo_alvo})
                log_callback(f"   [PMV] Reset: {r0.rowcount} linhas do ciclo zeradas para recálculo.")

                # ----------------------------------------------------------------
                # NÍVEL 1 — CNPJ×SKU, últimos 3 meses (ponderado por volume).
                # ----------------------------------------------------------------
                r1 = db.execute(text("""
                    WITH pmv_cliente AS (
                        SELECT cgc, sku,
                               SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv
                        FROM fato_vendas
                        WHERE qt_pedido > 0 AND vl_pedido > 0
                          AND data_pedido >= (CURRENT_DATE - INTERVAL '3 months')
                        GROUP BY cgc, sku
                    )
                    UPDATE fato_ibp_granular f
                    SET pmv_aplicado = ROUND(p.pmv::numeric, 2)
                    FROM pmv_cliente p
                    WHERE f.ciclo_sop = :ciclo
                      AND f.cgc = p.cgc
                      AND f.sku = p.sku
                """), {"ciclo": ciclo_alvo})

                # ----------------------------------------------------------------
                # NÍVEL 2 — REGIONAL×SKU, últimos 3 meses (ponderado).
                # Para linhas sem preço próprio: usa o preço médio ponderado da
                # regional do cliente (via dim_clientes) para aquele SKU.
                # ----------------------------------------------------------------
                r2 = db.execute(text("""
                    WITH pmv_regional AS (
                        SELECT c.regional, v.sku,
                               SUM(v.vl_pedido) / NULLIF(SUM(v.qt_pedido), 0) AS pmv
                        FROM fato_vendas v
                        JOIN dim_clientes c ON c.cgc = v.cgc
                        WHERE v.qt_pedido > 0 AND v.vl_pedido > 0
                          AND v.data_pedido >= (CURRENT_DATE - INTERVAL '3 months')
                        GROUP BY c.regional, v.sku
                    )
                    UPDATE fato_ibp_granular f
                    SET pmv_aplicado = ROUND(p.pmv::numeric, 2)
                    FROM dim_clientes fc, pmv_regional p
                    WHERE f.ciclo_sop = :ciclo
                      AND fc.cgc = f.cgc
                      AND p.regional = fc.regional
                      AND p.sku = f.sku
                      AND COALESCE(f.pmv_aplicado, 0) = 0
                """), {"ciclo": ciclo_alvo})

                # ----------------------------------------------------------------
                # NÍVEL 3 — SKU geral, últimos 3 meses (ponderado, todas regionais).
                # ----------------------------------------------------------------
                r3 = db.execute(text("""
                    WITH pmv_sku AS (
                        SELECT sku,
                               SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv
                        FROM fato_vendas
                        WHERE qt_pedido > 0 AND vl_pedido > 0
                          AND data_pedido >= (CURRENT_DATE - INTERVAL '3 months')
                        GROUP BY sku
                    )
                    UPDATE fato_ibp_granular f
                    SET pmv_aplicado = ROUND(p.pmv::numeric, 2)
                    FROM pmv_sku p
                    WHERE f.ciclo_sop = :ciclo
                      AND f.sku = p.sku
                      AND COALESCE(f.pmv_aplicado, 0) = 0
                """), {"ciclo": ciclo_alvo})

                # ----------------------------------------------------------------
                # NÍVEL 4 — última venda conhecida do SKU (qualquer data).
                # Rede de segurança: SKU que não vendeu nos últimos 3 meses mas
                # tem histórico. Evita receita zero silenciosa.
                # ----------------------------------------------------------------
                r4 = db.execute(text("""
                    WITH ultima_data_sku AS (
                        SELECT sku, MAX(data_pedido) AS data_ult
                        FROM fato_vendas
                        WHERE qt_pedido > 0 AND vl_pedido > 0
                        GROUP BY sku
                    ),
                    pmv_ultima AS (
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
                    FROM pmv_ultima p
                    WHERE f.ciclo_sop = :ciclo
                      AND f.sku = p.sku
                      AND COALESCE(f.pmv_aplicado, 0) = 0
                """), {"ciclo": ciclo_alvo})

                db.commit()
                log_callback(
                    f"✅ [PMV] Cascata concluída no ciclo {ciclo_alvo}: "
                    f"N1 cliente(3m)={r1.rowcount} | N2 regional(3m)={r2.rowcount} | "
                    f"N3 SKU(3m)={r3.rowcount} | N4 última venda={r4.rowcount} linhas."
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