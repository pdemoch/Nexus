import pandas as pd
import numpy as np
import polars as pl
import os
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
        self.validation_size = 3  # Meses escondidos para o teste cego
        
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
        MOTOR ALPHA (SELL-IN): Lê o histórico de faturamento da Indústria.
        Agora enriquecido com cálculo de PMV e Blindagem de Portfólio.
        """
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

        # =========================================================================
        # 🛡️ BLINDAGEM CONTRA DADOS FANTASMAS (Filtro Segmentos.xlsx)
        # =========================================================================
        caminho_segmentos = os.path.join("app", "etl", "Segmentos.xlsx")
        if os.path.exists(caminho_segmentos):
            try:
                df_seg = pl.read_excel(caminho_segmentos)
                
                # Identifica a coluna correta de produto (ignora case sensitive)
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
                print(f"🛡️ [MOTOR ALPHA] Blindagem Ativa: {tamanho_antes - tamanho_depois} SKUs fantasmas eliminados da memória da IA.")
            except Exception as e:
                print(f"⚠️ [MOTOR ALPHA] Aviso: Falha ao ler Segmentos.xlsx para blindagem. Erro: {e}")
        else:
            print("⚠️ [MOTOR ALPHA] Arquivo Segmentos.xlsx não encontrado no caminho padrão. IA treinando sem blindagem de portfólio.")

        return df

    def _obter_dados_beta(self, db, data_corte: str) -> pd.DataFrame:
        """MOTOR BETA (SELL-OUT): Lê o histórico MTRIX com Estoque do Canal."""
        query = f"""
            SELECT 
                sku, 
                DATE_TRUNC('month', mes_referencia) AS mes_data, 
                SUM(qty_conv2) AS volume,
                SUM(estoque_qty_conv2) AS estoque
            FROM fato_mtrix_historico_mensal
            WHERE mes_referencia < '{data_corte}'
            GROUP BY sku, DATE_TRUNC('month', mes_referencia)
            ORDER BY sku, mes_data;
        """
        return pd.read_sql(query, db.bind)

    def _torneio_modelos(self, df_sku: pd.DataFrame, is_beta=False) -> tuple:
        """A Arena de Batalha: Testa todos os modelos cegamente e escolhe o Campeão."""
        df_limpo = limpar_falsos_zeros(df_sku, coluna_data='mes_data', coluna_volume='volume')
        
        if len(df_limpo) < (self.validation_size + 2):
            media = df_limpo['volume'].mean() if len(df_limpo) > 0 else 0.0
            return np.full(self.forecast_horizon, media), "Media_Simples", 50.0

        df_treino = df_limpo.iloc[:-self.validation_size]
        df_validacao = df_limpo.iloc[-self.validation_size:]
        y_real = df_validacao['volume'].values

        melhor_modelo_nome = None
        menor_erro_wmape = float('inf')

        # 1. Batalha (Treino Cego)
        for nome, modelo in self.modelos_disponiveis.items():
            if is_beta and nome == 'Croston_Intermitente': continue
            try:
                if hasattr(modelo, '_create_features'):
                    modelo.fit(df_treino)
                else:
                    modelo.fit(df_treino['volume'])
                
                y_pred = modelo.predict(df_treino, horizon=self.validation_size) if hasattr(modelo, '_create_features') else modelo.predict(df_treino['volume'], horizon=self.validation_size)
                
                # Previne erros de conversão do Pandas em predições
                if isinstance(y_pred, pd.Series): y_pred = y_pred.values

                # 2. Avaliação de Erro (Acurácia)
                wmape = calcular_acuracia(y_real, y_pred)
                
                if wmape < menor_erro_wmape:
                    menor_erro_wmape = wmape
                    melhor_modelo_nome = nome
            except Exception as e:
                continue

        if not melhor_modelo_nome:
            melhor_modelo_nome = "Media_Simples"
            menor_erro_wmape = 50.0
            
        # 3. A Previsão Oficial (Refit do Campeão com 100% dos dados)
        motor_campeao = self.modelos_disponiveis.get(melhor_modelo_nome)
        
        try:
            if hasattr(motor_campeao, '_create_features'):
                futuro = motor_campeao.predict(df_limpo, horizon=self.forecast_horizon)
            else:
                futuro = motor_campeao.predict(df_limpo['volume'], horizon=self.forecast_horizon)
                
            # [CORREÇÃO] Garante que a previsão seja um array limpo do numpy (Evita KeyError: 0)
            if isinstance(futuro, pd.Series):
                futuro = futuro.values
        except:
            futuro = np.full(self.forecast_horizon, df_limpo['volume'].mean())
            
        acuracia_final = max(0.0, 100.0 - menor_erro_wmape)
        return futuro, melhor_modelo_nome, acuracia_final

    def executar_arena(self, ciclo_alvo: str, log_callback=print):
        mes_ano = ciclo_alvo.split('/')
        data_inicio_proj = date(int(mes_ano[1]), int(mes_ano[0]), 1)
        data_corte_str = data_inicio_proj.strftime("%Y-%m-%d")

        resultados_alpha = []
        db = SessionLocal()
        
        try:
            log_callback(f"   -> [ML] Lendo histórico Gobi ERP (Ancorado em {ciclo_alvo})...")
            df_alpha = self._obter_dados_alpha(db, data_corte_str)
            skus_alpha = df_alpha['sku'].unique()
            
            if len(skus_alpha) == 0:
                raise ValueError("Nenhum histórico Alpha encontrado no banco.")

            log_callback(f"   -> [ML] Iniciando Torneio de Algoritmos para {len(skus_alpha)} SKUs...")
            
            # --- MOTOR ALPHA (SELL-IN) ---
            for sku in skus_alpha:
                df_sku = df_alpha[df_alpha['sku'] == sku].copy()
                
                # IA prevê o futuro bruto
                previsao, melhor_modelo_nome, acuracia_final = self._torneio_modelos(df_sku, is_beta=False)
                
                # =========================================================================
                # 🛡️ TRAVA DE BOM SENSO (CLIPPING ESTATÍSTICO)
                # =========================================================================
                historico_recente = df_sku[df_sku['volume'] > 0].tail(12)['volume']
                
                if len(historico_recente) >= 3:
                    piso = historico_recente.quantile(0.05)
                    teto = historico_recente.quantile(0.95)
                    teto_ajustado = teto * 1.15 # Teto ganha 15% de margem para acomodar crescimento
                    
                    # Corta picos absurdos ou quedas irreais gerados pela matemática
                    previsao = np.clip(previsao, piso, teto_ajustado)

                # Grava os 5 meses projetados
                for i in range(self.forecast_horizon):
                    mes_proj = data_inicio_proj + relativedelta(months=i)
                    resultados_alpha.append({
                        "ciclo_sop": ciclo_alvo,
                        "mes_projetado": mes_proj,
                        "sku": sku,
                        "vol_ia_global": round(previsao[i], 2), # O bug do KeyError: 0 morre aqui!
                        "modelo_vencedor": melhor_modelo_nome,
                        "acuracia_ia": round(acuracia_final, 2)
                    })

            log_callback(f"   -> [ML] Motor Alpha concluído. Preparando Motor Beta (Sell-out)...")

            # --- MOTOR BETA (SELL-OUT) ---
            df_beta = self._obter_dados_beta(db, data_corte_str)
            skus_beta = df_beta['sku'].unique()
            
            if len(skus_beta) > 0:
                log_callback(f"      🔬 [MOTOR BETA] Iniciando Torneio de Sell-out para {len(skus_beta)} SKUs...")
                
                for sku in skus_beta:
                    df_sku_b = df_beta[df_beta['sku'] == sku].copy()
                    previsao_b, campeao_b, acuracia_b = self._torneio_modelos(df_sku_b, is_beta=True)
                    
                    db.execute(text("""
                        UPDATE fato_mtrix_snapshot 
                        SET previsao_sellout_m0 = :prev 
                        WHERE sku = :sku AND ciclo_sop = :ciclo
                    """), {"prev": round(previsao_b[0], 2), "sku": sku, "ciclo": ciclo_alvo})
                
                db.commit()
                log_callback("      ✅ [MOTOR BETA] Previsões de esgotamento de prateleira salvas no Dossiê.")
            else:
                log_callback("      ⚠️ [MOTOR BETA] Sem dados de Canal Indireto para treinar. Ignorado.")

            return pl.DataFrame(resultados_alpha)

        except Exception as e:
            db.rollback()
            log_callback(f"❌ [ERRO ML] Falha catastrófica na IA: {e}")
            raise e
        finally:
            db.close()