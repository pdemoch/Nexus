import pandas as pd
import numpy as np
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
import time

from app.core.database import SessionLocal 
from app.ml.models_library import (
    AutoArimaModel, HoltWintersModel, CrostonModel, 
    LocalMLAutoregressive, calcular_acuracia, limpar_falsos_zeros
)

class NexusForecaster:
    def __init__(self):
        self.forecast_horizon = 5 # M0, M1, M2, M3, M4
        self.validation_size = 3  # Meses escondidos para o teste cego (Cross-Validation)
        
        # ---------------------------------------------------------
        # ARSENAL DE MODELOS
        # ---------------------------------------------------------
        self.modelos_disponiveis = {
            'XGBoost_Multivariado': LocalMLAutoregressive('xgb'),
            'LightGBM_Multivariado': LocalMLAutoregressive('lgb'),
            'AutoARIMA_Sazonal': AutoArimaModel(),
            'HoltWinters_Sazonal': HoltWintersModel(),
            'Croston_Intermitente': CrostonModel() # Especialista em Zeros/Rupturas
        }

    def _obter_dados_alpha(self, db, data_corte: str) -> pd.DataFrame:
        """
        MOTOR ALPHA (SELL-IN): Lê o histórico de faturamento da fábrica (Gobi).
        Visão Nacional por SKU. Univariado (apenas tempo e volume).
        """
        query = text("""
            SELECT 
                sku, 
                DATE_TRUNC('month', data_pedido) AS mes_data,
                SUM(qt_pedido) AS volume
            FROM fato_vendas
            WHERE data_pedido >= :data_corte
            GROUP BY sku, DATE_TRUNC('month', data_pedido)
            ORDER BY sku, mes_data ASC
        """)
        df = pd.read_sql(query, db.bind, params={"data_corte": data_corte})
        df['mes_data'] = pd.to_datetime(df['mes_data'])
        return df

    def _obter_dados_beta(self, db) -> pd.DataFrame:
        """
        MOTOR BETA (SELL-OUT): Lê o histórico do Canal Indireto (MTRIX).
        Visão Nacional por SKU. Multivariado (Tempo, Volume e Variação de Estoque).
        """
        query = text("""
            SELECT 
                sku, 
                TO_DATE(mes_ano, 'YYYY-MM') AS mes_data,
                SUM(volume_sellout) AS volume
            FROM fato_mtrix_historico_mensal
            GROUP BY sku, mes_ano
            ORDER BY sku, mes_data ASC
        """)
        df = pd.read_sql(query, db.bind)
        df['mes_data'] = pd.to_datetime(df['mes_data'])
        
        # O modelo deteta automaticamente esta coluna se futuramente for preenchida
        if 'estoque_mensal' in df.columns:
            df.rename(columns={'estoque_mensal': 'estoque'}, inplace=True)
            
        return df

    def _torneio_modelos(self, df_sku: pd.DataFrame, is_beta: bool):
        """
        O CORAÇÃO DA IA: Treina, testa às cegas, escolhe o Campeão e prevê o futuro.
        """
        # 1. Cura Matemática: Remove Zeros antes do lançamento do produto
        df_limpo = limpar_falsos_zeros(df_sku, 'mes_data', 'volume')
        
        if len(df_limpo) < 6:
            media = df_limpo['volume'].mean() if len(df_limpo) > 0 else 0.0
            return np.full(self.forecast_horizon, media), "Media_Movel_Fallback", 0.0

        # O índice tem de ser a data para as regressões temporais
        df_limpo.set_index('mes_data', inplace=True)
        
        # 2. Divisão de Treino vs Teste (Esconde os últimos meses)
        df_treino = df_limpo.iloc[:-self.validation_size]
        df_teste = df_limpo.iloc[-self.validation_size:]
        
        y_real = df_teste['volume'].values
        
        melhor_modelo_nome = "Media_Movel_Fallback"
        menor_erro_wmape = 999.0
        
        # 3. A Batalha (Champion/Challenger)
        for nome_modelo, motor in self.modelos_disponiveis.items():
            try:
                # Regras táticas de eliminação
                if is_beta and nome_modelo == "AutoARIMA_Sazonal": 
                    continue # ARIMA sofre com muitos zeros do Sell-out
                if not is_beta and nome_modelo == "Croston_Intermitente":
                    continue # Croston é exclusivo para o canal ponta (Beta)
                    
                if hasattr(motor, '_create_features'):
                    preds_teste = motor.predict(df_treino, horizon=self.validation_size)
                else:
                    preds_teste = motor.predict(df_treino['volume'], horizon=self.validation_size)
                
                acuracia = calcular_acuracia(y_real, preds_teste)
                erro_wmape = 100.0 - acuracia
                
                if erro_wmape < menor_erro_wmape:
                    menor_erro_wmape = erro_wmape
                    melhor_modelo_nome = nome_modelo
            except Exception as e:
                pass 
                
        # 4. A Previsão Oficial (Refit do Campeão com 100% dos dados)
        motor_campeao = self.modelos_disponiveis.get(melhor_modelo_nome)
        
        try:
            if hasattr(motor_campeao, '_create_features'):
                futuro = motor_campeao.predict(df_limpo, horizon=self.forecast_horizon)
            else:
                futuro = motor_campeao.predict(df_limpo['volume'], horizon=self.forecast_horizon)
        except:
            futuro = np.full(self.forecast_horizon, df_limpo['volume'].mean())
            
        acuracia_final = max(0.0, 100.0 - menor_erro_wmape)
        return futuro, melhor_modelo_nome, acuracia_final

    def executar_arena(self, ciclo_alvo: str, log_callback=print):
        """
        Orquestra a execução independente dos Motores Alpha e Beta.
        """
        from datetime import datetime
        
        # 1. TRADUZ O CICLO DO ADMIN PARA O "MÊS ZERO" DA IA
        try:
            mes_str, ano_str = ciclo_alvo.split('/')
            data_ancora = date(int(ano_str), int(mes_str), 1)
        except Exception:
            data_ancora = date.today().replace(day=1)
            
        # Janela de treino (últimos 3 anos a partir da âncora)
        data_corte = (data_ancora - relativedelta(years=3)).strftime("%Y-%m-%d")
        
        db = SessionLocal()
        resultados_alpha = []
        
        try:
            # =========================================================
            # 🧠 TREINAMENTO DO MOTOR ALPHA (FÁBRICA / SELL-IN)
            # =========================================================
            log_callback(f"      🔬 [MOTOR ALPHA] Puxando histórico Gobi ERP (Ancorado em {ciclo_alvo})...")
            df_alpha = self._obter_dados_alpha(db, data_corte)
            skus_alpha = df_alpha['sku'].unique()
            
            log_callback(f"      🔬 [MOTOR ALPHA] Iniciando Torneio de Algoritmos para {len(skus_alpha)} SKUs...")
            
            for sku in skus_alpha:
                df_sku = df_alpha[df_alpha['sku'] == sku].copy()
                
                # Previsão, Quem Ganhou, e Acurácia
                previsao, campeao, acuracia = self._torneio_modelos(df_sku, is_beta=False)
                
                # Guarda resultados respeitando o esquema que o distributor.py espera
                for i in range(self.forecast_horizon):
                    mes_proj = data_ancora + relativedelta(months=i)
                    resultados_alpha.append({
                        "ciclo_sop": ciclo_alvo,
                        "produto": sku,
                        "mes_projetado": mes_proj,
                        "vol_ia_global": round(previsao[i], 2),
                        "pmv_aplicado": 0.0, # Necessário para o Rateio
                        "modelo_vencedor": campeao,
                        "acuracia": round(acuracia, 2)
                    })

            # =========================================================
            # 🧠 TREINAMENTO DO MOTOR BETA (CANAL / SELL-OUT)
            # =========================================================
            log_callback("      🔬 [MOTOR BETA] Puxando histórico de Distribuição (MTRIX)...")
            df_beta = self._obter_dados_beta(db)
            skus_beta = df_beta['sku'].unique()
            
            if len(skus_beta) > 0:
                log_callback(f"      🔬 [MOTOR BETA] Iniciando Torneio de Sell-out para {len(skus_beta)} SKUs...")
                
                for sku in skus_beta:
                    df_sku = df_beta[df_beta['sku'] == sku].copy()
                    
                    previsao, campeao, acuracia = self._torneio_modelos(df_sku, is_beta=True)
                    
                    # Atualiza diretamente a Snapshot do MTRIX com a previsão M0 (Mês atual)
                    db.execute(text("""
                        UPDATE fato_mtrix_snapshot 
                        SET previsao_sellout_m0 = :prev 
                        WHERE sku = :sku AND ciclo_sop = :ciclo
                    """), {"prev": round(previsao[0], 2), "sku": sku, "ciclo": ciclo_alvo})
                
                db.commit()
                log_callback("      ✅ [MOTOR BETA] Previsões de esgotamento de prateleira salvas no Dossiê.")
            else:
                log_callback("      ⚠️ [MOTOR BETA] Sem dados de Canal Indireto para treinar. Ignorado.")

            # Retorna o Alpha como Polars DataFrame para o Pipeline fazer o Rateio Tático
            return pl.DataFrame(resultados_alpha)

        except Exception as e:
            db.rollback()
            log_callback(f"❌ [ERRO ML] Falha catastrófica na IA: {str(e)}")
            raise e
        finally:
            db.close()