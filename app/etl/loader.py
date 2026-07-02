import polars as pl
import pandas as pd
from sqlalchemy import text
from app.core.database import SessionLocal

class NexusLoader:
    def executar_carga_historico(self, df_historico: pl.DataFrame, log_callback=print):
        """
        Insere/Atualiza os dados reais extraídos do ERP Gobi.
        Apenas insere clientes novos ou atualiza inativos via UPSERT, 
        depois injeta as vendas reais.
        """
        log_callback("⏳ [LOAD] Sincronizando com PostgreSQL via SQLAlchemy (Orçamento, Segmentos e Metas)...")
        
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models.domain_models import DimCliente, FatoVenda

        with SessionLocal() as db:
            # 1. UPSERT de Clientes
            df_clientes = df_historico.select(["cgc", "razaosocial", "loja", "cod_cliente", "vendedor_nome", "regional"]).unique("cgc").to_dicts()
            
            for i in range(0, len(df_clientes), 5000):
                lote = df_clientes[i:i+5000]
                stmt = pg_insert(DimCliente).values(lote)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['cgc'],
                    set_={
                        'razaosocial': stmt.excluded.razaosocial,
                        'loja': stmt.excluded.loja,
                        'vendedor_nome': stmt.excluded.vendedor_nome,
                        'regional': stmt.excluded.regional,
                    }
                )
                db.execute(stmt)

            # 2. UPSERT de Vendas
            df_vendas = df_historico.select([
                "pedido", "data_pedido", "sku", "cgc", "vendedor_nome", 
                "qt_pedido", "vl_pedido", "qtfatura", "qtcorte", "vlfatura", "vlcorte"
            ]).to_dicts()
            
            for i in range(0, len(df_vendas), 5000):
                lote = df_vendas[i:i+5000]
                stmt = pg_insert(FatoVenda).values(lote)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['pedido', 'sku', 'cgc'],
                    set_={
                        'data_pedido': stmt.excluded.data_pedido,
                        'vendedor_nome': stmt.excluded.vendedor_nome,
                        'qt_pedido': stmt.excluded.qt_pedido,
                        'vl_pedido': stmt.excluded.vl_pedido,
                        'qtfatura': stmt.excluded.qtfatura,
                        'qtcorte': stmt.excluded.qtcorte,
                        'vlfatura': stmt.excluded.vlfatura,
                        'vlcorte': stmt.excluded.vlcorte,
                    }
                )
                db.execute(stmt)

            db.commit()
            log_callback(f"✅ [LOAD] Histórico Sincronizado! {len(df_vendas)} Vendas e {len(df_clientes)} Clientes atualizados.")

    def executar_carga_forecast(self, df_forecast: pl.DataFrame, ciclo_alvo: str, log_callback=print):
        """
        Salva as Medalhas da IA. Apenas atualiza a tabela de acurácia.
        O Rateio tático agora pertence exclusivamente ao distributor.py.
        """
        log_callback("⏳ [LOAD] Registrando performance e vencedores da IA no Banco...")
        
        with SessionLocal() as db:
            try:
                # Agrupa por SKU pegando o primeiro modelo vencedor e acurácia que o forecaster cuspiu
                df_modelos = df_forecast.group_by("sku").agg([
                    pl.col("modelo_vencedor").first(), 
                    pl.col("acuracia_ia").first()
                ]).to_dicts()
                
                # Faz o Update na fato_ibp_granular
                for d in df_modelos:
                    db.execute(text("""
                        UPDATE fato_ibp_granular 
                        SET modelo_vencedor = :mod, acuracia_ia = :acu 
                        WHERE sku = :sku AND ciclo_sop = :ciclo
                    """), {
                        "mod": d['modelo_vencedor'], 
                        "acu": d['acuracia_ia'], 
                        "sku": d['sku'], 
                        "ciclo": ciclo_alvo
                    })
                
                db.commit()
                log_callback("✅ [LOAD] Inteligência de Auditoria salva com sucesso.")
            except Exception as e:
                db.rollback()
                log_callback(f"❌ [LOAD] Erro Crítico ao salvar acurácia: {e}")
                raise e