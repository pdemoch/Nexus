import pandas as pd
import numpy as np
import polars as pl
import os
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from app.core.database import SessionLocal 
from app.ml.models_library import (
    AutoArimaModel, HoltWintersModel, CrostonModel, ThetaModelWrapper,
    ProphetModel, LocalMLAutoregressive, calcular_score_torneio, limpar_falsos_zeros
)

class NexusForecaster:
    def __init__(self):
        self.forecast_horizon = 5 # M0 a M4
        self.validation_size = 5  # Janela Profunda de 5 Meses (Teste de Stress Real)
        
        # ---------------------------------------------------------
        # A ARENA DE MODELOS (Agora com TODOS os algoritmos!)
        # ---------------------------------------------------------
        self.modelos_disponiveis = {
            'XGBoost_MachineLearning': LocalMLAutoregressive('xgb'),
            'LightGBM_MachineLearning': LocalMLAutoregressive('lgb'),
            'RandomForest_MachineLearning': LocalMLAutoregressive('rf'),
            'Prophet_Sazonal_Avancado': ProphetModel(),
            'AutoARIMA_Estatistico': AutoArimaModel(),
            'HoltWinters_Exponencial': HoltWintersModel(),
            'Theta_Estatistico': ThetaModelWrapper(),
            'Croston_Intermitente': CrostonModel() # Especialista em Rupturas
        }

    def _obter_dados_alpha(self, db, data_corte: str) -> pd.DataFrame:
        """Motor Alpha (Sell-in) com cálculo embutido do PMV e Filtragem de Portfólio"""
        query = f"""
            SELECT 
                sku, 
                DATE_TRUNC('month', data_pedido) AS mes_data, 
                SUM(qt_pedido) AS volume,
                COALESCE(SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0), 0) AS pmv
            FROM fato_vendas
            WHERE data_pedido < '{data_corte}'
            GROUP BY sku, DATE_TRUNC('month', data_pedido)
            ORDER BY sku, mes_data;
        """
        df = pd.read_sql(query, db.bind)

        caminho_segmentos = os.path.join("app", "etl", "Segmentos.xlsx")
        if os.path.exists(caminho_segmentos):
            try:
                df_seg = pl.read_excel(caminho_segmentos)
                col_produto = [c for c in df_seg.columns if c.lower() == 'produto'][0]
                
                skus_validos = df_seg.with_columns([
                    pl.col(col_produto).cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                    pl.col("2026").cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
                ]).filter(
                    (pl.col("2026").str.contains(r"^[A-Z]{1,2}$")) | 
                    (pl.col("2026") == "LANÇAMENTO")
                ).get_column(col_produto).to_list()

                tamanho_antes = len(df['sku'].unique())
                df = df[df['sku'].isin(skus_validos)]
                tamanho_depois = len(df['sku'].unique())
                print(f"🛡️ [MOTOR ALPHA] Blindagem Ativa: {tamanho_antes - tamanho_depois} SKUs fantasmas mortos eliminados da IA.")
            except Exception as e:
                print(f"⚠️ [MOTOR ALPHA] Falha na blindagem Segmentos.xlsx: {e}")

        return df

    def _obter_dados_beta(self, db, data_corte: str) -> pd.DataFrame:
        query = f"""
            SELECT sku, DATE_TRUNC('month', mes_referencia) AS mes_data, SUM(qty_conv2) AS volume
            FROM fato_mtrix_historico_mensal
            WHERE mes_referencia < '{data_corte}'
            GROUP BY sku, DATE_TRUNC('month', mes_referencia)
            ORDER BY sku, mes_data;
        """
        return pd.read_sql(query, db.bind)

    def _torneio_modelos(self, df_sku: pd.DataFrame, is_beta=False) -> tuple:
        """A Batalha dos Algoritmos avaliando WMAPE + BIAS."""
        df_limpo = limpar_falsos_zeros(df_sku, coluna_data='mes_data', coluna_volume='volume')
        
        if len(df_limpo) < (self.validation_size + 3):
            media = df_limpo['volume'].mean() if len(df_limpo) > 0 else 0.0
            return np.full(self.forecast_horizon, media), "Media_Simples_Fallback", 50.0

        df_treino = df_limpo.iloc[:-self.validation_size]
        df_validacao = df_limpo.iloc[-self.validation_size:]
        y_real = df_validacao['volume'].values

        melhor_modelo_nome = None
        menor_score = float('inf')

        # 1. Batalha (Treino Cego) - Graças aos wrappers, o loop ficou puríssimo!
        for nome, modelo in self.modelos_disponiveis.items():
            if is_beta and 'Croston' in nome: continue 
            try:
                modelo.fit(df_treino)
                y_pred = modelo.predict(df_treino, horizon=self.validation_size)
                
                # Garante vetorização limpa
                if isinstance(y_pred, pd.Series): y_pred = y_pred.values

                # Score Penalizador de Viés (Quanto menor, melhor)
                score = calcular_score_torneio(y_real, y_pred)
                
                if score < menor_score:
                    menor_score = score
                    melhor_modelo_nome = nome
            except Exception as e:
                continue

        if not melhor_modelo_nome:
            melhor_modelo_nome = "Media_Simples_Fallback"
            menor_score = 100.0
            
        # 3. A Previsão Oficial (Refit do Campeão com 100% da base)
        motor_campeao = self.modelos_disponiveis.get(melhor_modelo_nome)
        
        try:
            motor_campeao.fit(df_limpo)
            futuro = motor_campeao.predict(df_limpo, horizon=self.forecast_horizon)
            if isinstance(futuro, pd.Series): futuro = futuro.values
        except:
            futuro = np.full(self.forecast_horizon, df_limpo['volume'].mean())
            
        # Acurácia de apresentação (100% menos o score combinado)
        acuracia_final = max(0.0, 100.0 - menor_score)
        return futuro, melhor_modelo_nome, acuracia_final

    def executar_arena(self, ciclo_alvo: str, log_callback=print):
        mes_ano = ciclo_alvo.split('/')
        data_inicio_proj = date(int(mes_ano[1]), int(mes_ano[0]), 1)
        data_corte_str = data_inicio_proj.strftime("%Y-%m-%d")

        resultados_alpha = []
        db = SessionLocal()
        
        try:
            log_callback(f"   -> [ML] Lendo histórico PostgreSQL Enriquecido (Ancorado em {ciclo_alvo})...")
            df_alpha = self._obter_dados_alpha(db, data_corte_str)
            skus_alpha = df_alpha['sku'].unique()
            
            if len(skus_alpha) == 0: raise ValueError("Nenhum histórico Alpha (Sell-in) encontrado.")

            log_callback(f"   -> [ML] Iniciando Torneio de Algoritmos em {len(skus_alpha)} SKUs (Teste de {self.validation_size} Meses)...")
            
            # --- MOTOR ALPHA (SELL-IN) ---
            for sku in skus_alpha:
                df_sku = df_alpha[df_alpha['sku'] == sku].copy()
                previsao, melhor_modelo_nome, acuracia_final = self._torneio_modelos(df_sku, is_beta=False)
                
                # =========================================================================
                # 🛡️ TRAVA DE BOM SENSO (CLIPPING ESTATÍSTICO OUTLIERS)
                # =========================================================================
                historico_recente = df_sku[df_sku['volume'] > 0].tail(12)['volume']
                if len(historico_recente) >= 3:
                    piso = historico_recente.quantile(0.05)
                    teto = historico_recente.quantile(0.95)
                    previsao = np.clip(previsao, piso, teto * 1.15) # Margem 15% acima do Teto

                for i in range(self.forecast_horizon):
                    mes_proj = data_inicio_proj + relativedelta(months=i)
                    resultados_alpha.append({
                        "ciclo_sop": ciclo_alvo, "mes_projetado": mes_proj, "sku": sku,
                        "vol_ia_global": round(previsao[i], 2),
                        "modelo_vencedor": melhor_modelo_nome, "acuracia_ia": round(acuracia_final, 2)
                    })

            log_callback(f"   -> [ML] Motor Alpha concluído. Preparando Motor Beta (Sell-out)...")

            # --- MOTOR BETA (SELL-OUT) ---
            df_beta = self._obter_dados_beta(db, data_corte_str)
            skus_beta = df_beta['sku'].unique()
            
            if len(skus_beta) > 0:
                log_callback(f"      🔬 [MOTOR BETA] Torneio para {len(skus_beta)} SKUs Distribuidores...")
                for sku in skus_beta:
                    df_sku_b = df_beta[df_beta['sku'] == sku].copy()
                    previsao_b, campeao_b, acuracia_b = self._torneio_modelos(df_sku_b, is_beta=True)
                    
                    db.execute(text("""
                        UPDATE fato_mtrix_snapshot SET previsao_sellout_m0 = :prev 
                        WHERE sku = :sku AND ciclo_sop = :ciclo
                    """), {"prev": round(previsao_b[0], 2), "sku": sku, "ciclo": ciclo_alvo})
                db.commit()
            
            return pl.DataFrame(resultados_alpha)

        except Exception as e:
            db.rollback()
            log_callback(f"❌ [ERRO ML] Falha catastrófica na IA: {e}")
            raise e
        finally:
            db.close()