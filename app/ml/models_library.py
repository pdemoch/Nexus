import numpy as np
import pandas as pd
import xgboost 
import lightgbm
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.forecasting.theta import ThetaModel
import warnings

# Novas Importações
try:
    from prophet import Prophet
except ImportError:
    pass

try:
    import pmdarima as pm
except ImportError:
    pass

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

class ProphetModel:
    """O Rei dos Picos: Algoritmo da Meta/Facebook excelente para capturar sazonalidades agressivas."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 6:
                return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            
            df_p = pd.DataFrame({'ds': train_series.index, 'y': train_series.values})
            # Desliga logs chatos do Prophet
            import logging
            logging.getLogger('prophet').setLevel(logging.ERROR)
            
            m = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
            m.fit(df_p)
            future = m.make_future_dataframe(periods=steps_ahead, freq='MS')
            forecast = m.predict(future)
            preds = forecast['yhat'].iloc[-steps_ahead:].values
            return np.maximum(0, preds)
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class AutoArimaModel:
    """O Padrão Ouro Estatístico (SARIMAX): Testa várias combinações e acha o melhor ajuste sazonal."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 12:
                return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            
            # seasonal=True e m=12 diz ao modelo para procurar padrões anuais
            model = pm.auto_arima(train_series.values, seasonal=True, m=12, stepwise=True, suppress_warnings=True, error_action="ignore")
            preds = model.predict(n_periods=steps_ahead)
            return np.maximum(0, preds)
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

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
    """O Caçador de Ondas Clássico: Captura Nível, Tendência e Sazonalidade (Ciclos de 12 meses)."""
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 12:
                return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
                
            model = ExponentialSmoothing(
                train_series.values, 
                trend='add', 
                seasonal='add', 
                seasonal_periods=12,
                initialization_method="estimated"
            ).fit()
            
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
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

class GlobalMLTrainer:
    """
    Construtor de Features para os modelos globais (XGBoost e LightGBM).
    Foi anabolizado para capturar picos ensinando aceleração e volatilidade à máquina.
    """
    @staticmethod
    def gerar_features_globais(df_historico_completo: pd.DataFrame, lags=[1, 2, 3, 6, 12]):
        dfs_processados = []
        
        for sku, group in df_historico_completo.groupby('produto'):
            g = group.copy().sort_values('mes_ano_dt')

            g['mes'] = g['mes_ano_dt'].dt.month
            g['mes_sin'] = np.sin(2 * np.pi * g['mes'] / 12)
            g['mes_cos'] = np.cos(2 * np.pi * g['mes'] / 12)
            
            for lag in lags:
                g[f'lag_{lag}'] = g['total_qtpedido'].shift(lag)
                
            g['media_movel_3'] = g['total_qtpedido'].shift(1).rolling(window=3).mean()
            
            # --- NOVAS FEATURES DE VOLATILIDADE E ACELERAÇÃO ---
            # Desvio Padrão: Ensina à arvore se o produto costuma ter saltos agressivos
            g['volatilidade_3m'] = g['total_qtpedido'].shift(1).rolling(window=3).std().fillna(0)
            
            # Aceleração (Variação Relativa): Ensina à arvore se estamos a entrar numa rampa de subida ou descida
            g['diff_1'] = g['lag_1'] - g['lag_2']
            g['diff_2'] = g['lag_2'] - g['lag_3']
            
            dfs_processados.append(g)
            
        df_feat = pd.concat(dfs_processados)
        df_feat = df_feat.dropna(subset=[f'lag_{lags[-1]}']).copy()
        
        cols_cat = ['bu', 'categoria', 'segmento', 'curva_2026']
        for col in cols_cat:
            if col in df_feat.columns:
                df_feat[col] = df_feat[col].astype('category').cat.codes
                
        return df_feat