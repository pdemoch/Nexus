import numpy as np
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.forecasting.theta import ThetaModel
import warnings

# Ignorar avisos de convergência e do LightGBM para manter a consola limpa
warnings.filterwarnings("ignore")

def calcular_acuracia(y_true, y_pred):
    """
    Calcula a Acurácia (0 a 100%) baseada no WMAPE (Weighted Mean Absolute Percentage Error).
    Se a base for 0 e a previsão for 0, acurácia é 100%.
    """
    soma_real = np.sum(y_true)
    if soma_real == 0:
        return 0.0 if np.sum(y_pred) > 0 else 100.0
        
    wmape = np.sum(np.abs(y_true - y_pred)) / soma_real
    acuracia = max(0.0, (1.0 - wmape) * 100)
    return round(acuracia, 2)

# ==========================================
# OS ESPECIALISTAS (Modelos Estatísticos Locais)
# ==========================================

class HoltModel:
    """Modelo de Suavização Exponencial de Holt (captura nível e tendência, sem sazonalidade)."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 3:
                return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
                
            model = ExponentialSmoothing(
                train_series.values, 
                trend='add', 
                seasonal=None, 
                initialization_method="estimated"
            ).fit()
            
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class HoltWintersModel:
    """O Caçador de Ondas: Captura Nível, Tendência e Sazonalidade (Ciclos de 12 meses)."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            # Requer pelo menos 2 anos completos (24 meses) para encontrar padrões sazonais perfeitos, 
            # mas deixamos rodar a partir de 12 para captar o mínimo de ciclo.
            if len(train_series) < 12:
                return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
                
            model = ExponentialSmoothing(
                train_series.values, 
                trend='add', 
                seasonal='add', # 'add' é mais seguro que 'mul' quando existem zeros nas vendas
                seasonal_periods=12, # O tamanho da onda (1 ano)
                initialization_method="estimated"
            ).fit()
            
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
            # Se a matemática falhar (ex: série muito flat), cai graciosamente
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)


class ThetaModelWrapper:
    """Modelo Theta de Assimakopoulos & Nikolopoulos (excelente desempenho geral)."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 4:
                return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
                
            model = ThetaModel(train_series.values).fit()
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)


class CrostonModel:
    """Método de Croston Clássico: O melhor para Procura Intermitente (Muitos zeros na série)."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int, alpha=0.1):
        y = train_series.values
        if len(y) == 0 or np.sum(y) == 0:
            return np.zeros(steps_ahead)
            
        z = np.zeros(len(y))  
        p = np.zeros(len(y))  
        
        last_p = 1
        for i in range(len(y)):
            if y[i] > 0:
                z[i] = y[i]
                p[i] = last_p
                last_p = 1
            else:
                z[i] = z[i-1] if i > 0 else 0
                p[i] = p[i-1] if i > 0 else 1
                last_p += 1
                
        z_hat = np.zeros(len(y) + 1)
        p_hat = np.zeros(len(y) + 1)
        z_hat[0], p_hat[0] = z[0], p[0]
        
        for i in range(len(y)):
            z_hat[i+1] = alpha * z[i] + (1 - alpha) * z_hat[i]
            p_hat[i+1] = alpha * p[i] + (1 - alpha) * p_hat[i]
            
        p_hat = np.maximum(p_hat, 1.0) 
        forecast = z_hat[-1] / p_hat[-1]
        
        return np.full(steps_ahead, max(0, forecast))


class MovingAverageModel:
    """Baseline Clássico: Média Móvel Simples."""
    def __init__(self, window=3):
        self.window = window
        
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        if len(train_series) == 0:
            return np.zeros(steps_ahead)
            
        hist_y = list(train_series.values)
        preds = []
        
        for _ in range(steps_ahead):
            pred = np.mean(hist_y[-self.window:]) if len(hist_y) >= self.window else np.mean(hist_y)
            preds.append(max(0, pred))
            hist_y.append(pred) 
            
        return np.array(preds)


# ==========================================
# OS TITÃS (Preparação para Machine Learning Global)
# ==========================================

class GlobalMLTrainer:
    """
    Construtor de Features para os modelos globais (XGBoost e LightGBM).
    Cria a matriz que junta o comportamento de TODOS os SKUs.
    """
    @staticmethod
    def gerar_features_globais(df_historico_completo: pd.DataFrame, lags=[1, 2, 3, 6, 12]):
        dfs_processados = []
        
        for sku, group in df_historico_completo.groupby('produto'):
            g = group.copy().sort_values('mes_ano_dt')
            
            # Sazonalidade Cíclica (O Segredo do ML)
            g['mes'] = g['mes_ano_dt'].dt.month
            
            # Seno e Cosseno transformam os 12 meses num relógio circular perfeito
            # garantindo que a IA entende que Dezembro (12) e Janeiro (1) estão conectados.
            g['mes_sin'] = np.sin(2 * np.pi * g['mes'] / 12)
            g['mes_cos'] = np.cos(2 * np.pi * g['mes'] / 12)
            
            # Criação dos Lags (Vendas nos meses anteriores)
            for lag in lags:
                g[f'lag_{lag}'] = g['total_qtpedido'].shift(lag)
                
            # Criação de features adicionais (ex: Média móvel dos últimos 3 meses)
            g['media_movel_3'] = g['total_qtpedido'].shift(1).rolling(window=3).mean()
            
            dfs_processados.append(g)
            
        # Junta tudo novamente
        df_feat = pd.concat(dfs_processados)
        
        # Remove os meses iniciais que ficaram com Lags nulos
        df_feat = df_feat.dropna(subset=[f'lag_{lags[-1]}']).copy()
        
        # Otimização de Memória e Tratamento Categórico para as Árvores de Decisão
        cols_cat = ['bu', 'categoria', 'segmento', 'curva_2026']
        for col in cols_cat:
            if col in df_feat.columns:
                df_feat[col] = df_feat[col].astype('category').cat.codes
                
        return df_feat