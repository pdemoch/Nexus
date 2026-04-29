import numpy as np
import pandas as pd
import xgboost 
import lightgbm
from sklearn.ensemble import RandomForestRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.forecasting.theta import ThetaModel
import warnings

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
    """Calcula a Acurácia (0 a 100%) baseada no WMAPE."""
    soma_real = np.sum(y_true)
    if soma_real == 0:
        return 0.0 if np.sum(y_pred) > 0 else 100.0
        
    wmape = np.sum(np.abs(y_true - y_pred)) / soma_real
    acuracia = max(0.0, (1.0 - wmape) * 100)
    return round(acuracia, 2)

class ProphetModel:
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 6: return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            df_p = pd.DataFrame({'ds': train_series.index, 'y': train_series.values})
            import logging
            logging.getLogger('prophet').setLevel(logging.ERROR)
            
            # TUNING: Mais agressividade nas mudanças de tendência para pegar os Vales e Picos
            m = Prophet(
                yearly_seasonality=True, 
                weekly_seasonality=False, 
                daily_seasonality=False,
                changepoint_prior_scale=0.1,  # Mais sensível a quebras de padrão
                seasonality_prior_scale=10.0  # Sazonalidade mais forte
            )
            m.fit(df_p)
            future = m.make_future_dataframe(periods=steps_ahead, freq='MS')
            forecast = m.predict(future)
            preds = forecast['yhat'].iloc[-steps_ahead:].values
            return np.maximum(0, preds)
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class AutoArimaModel:
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 12: return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            model = pm.auto_arima(train_series.values, seasonal=True, m=12, stepwise=True, suppress_warnings=True, error_action="ignore")
            preds = model.predict(n_periods=steps_ahead)
            return np.maximum(0, preds)
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class HoltWintersModel:
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 12: return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            model = ExponentialSmoothing(train_series.values, trend='add', seasonal='add', seasonal_periods=12, initialization_method="estimated").fit()
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class HoltModel:
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 3: return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            model = ExponentialSmoothing(train_series.values, trend='add', seasonal=None, initialization_method="estimated").fit()
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class ThetaModelWrapper:
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        try:
            if len(train_series) < 4: return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)
            model = ThetaModel(train_series.values).fit()
            return np.maximum(0, model.forecast(steps_ahead))
        except Exception:
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

class CrostonModel:
    def fit_predict(self, train_series: pd.Series, steps_ahead: int, alpha=0.1):
        y = train_series.values
        if len(y) == 0 or np.sum(y) == 0: return np.zeros(steps_ahead)
        z, p = np.zeros(len(y)), np.zeros(len(y))  
        last_p = 1
        for i in range(len(y)):
            if y[i] > 0:
                z[i], p[i] = y[i], last_p
                last_p = 1
            else:
                z[i] = z[i-1] if i > 0 else 0
                p[i] = p[i-1] if i > 0 else 1
                last_p += 1
        z_hat, p_hat = np.zeros(len(y) + 1), np.zeros(len(y) + 1)
        z_hat[0], p_hat[0] = z[0], p[0]
        for i in range(len(y)):
            z_hat[i+1] = alpha * z[i] + (1 - alpha) * z_hat[i]
            p_hat[i+1] = alpha * p[i] + (1 - alpha) * p_hat[i]
        p_hat = np.maximum(p_hat, 1.0) 
        return np.full(steps_ahead, max(0, z_hat[-1] / p_hat[-1]))

class MovingAverageModel:
    def __init__(self, window=3):
        self.window = window
    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        if len(train_series) == 0: return np.zeros(steps_ahead)
        hist_y = list(train_series.values)
        preds = []
        for _ in range(steps_ahead):
            pred = np.mean(hist_y[-self.window:]) if len(hist_y) >= self.window else np.mean(hist_y)
            preds.append(max(0, pred))
            hist_y.append(pred) 
        return np.array(preds)

class LocalMLAutoregressive:
    """
    NOVO MOTOR DIRECT MULTI-STEP:
    Abandona a recursividade. Treina um modelo independente para cada horizonte de tempo (h).
    Injeta o conhecimento de Picos e Vales via Z-Score.
    """
    def __init__(self, model_type='xgb'):
        self.model_type = model_type

    def fit_predict(self, train_series: pd.Series, steps_ahead: int):
        if len(train_series) < 15: 
            return np.full(steps_ahead, train_series.mean() if len(train_series) > 0 else 0)

        # 1. Feature Engineering com Memória de Picos
        df = pd.DataFrame({'y': train_series.values})
        for lag in [1, 2, 3, 6, 12]:
            df[f'lag_{lag}'] = df['y'].shift(lag)
            
        df['media_movel_3'] = df['y'].shift(1).rolling(3).mean()
        df['media_movel_6'] = df['y'].shift(1).rolling(6).mean()
        df['max_6m'] = df['y'].shift(1).rolling(6).max()
        
        # Detector de Ressaca / Trade Loading (Z-Score)
        std_6m = df['y'].shift(1).rolling(6).std().replace(0, 1)
        df['z_score'] = (df['y'].shift(1) - df['media_movel_6']) / std_6m
        df['is_pico'] = (df['z_score'] > 1.5).astype(int)
        df['picos_ultimos_3m'] = df['is_pico'].rolling(3).sum().fillna(0) # Flag de ressaca

        preds = []
        
        # O "Hoje": As features exatas no momento da previsão
        X_current = df.iloc[[-1]].drop(columns=['y', 'target'], errors='ignore')

        # 2. Estratégia DIRECT: Um modelo para cada horizonte
        for h in range(1, steps_ahead + 1):
            df_h = df.copy()
            # O alvo é a venda real que ocorreu 'h' meses depois dessa linha
            df_h['target'] = df_h['y'].shift(-h)
            
            df_train = df_h.dropna()
            
            # Se faltar dados pro horizonte longo, faz fallback inteligente
            if len(df_train) < 5:
                pred_fallback = np.mean(train_series.values[-3:])
                preds.append(pred_fallback)
                continue

            X_train = df_train.drop(columns=['y', 'target'])
            y_train = df_train['target']

            # Instancia o especialista daquele horizonte
            if self.model_type == 'xgb':
                model = xgboost.XGBRegressor(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42)
            elif self.model_type == 'lgb':
                model = lightgbm.LGBMRegressor(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42, verbose=-1)
            else:
                model = RandomForestRegressor(n_estimators=50, max_depth=5, random_state=42)

            model.fit(X_train, y_train)
            
            # Prevemos usando apenas as features conhecidas de HOJE
            pred_h = max(0, model.predict(X_current)[0])
            preds.append(pred_h)

        return np.array(preds)


class GlobalMLTrainer:
    """Construtor Avançado de Features Globais (Anti-Ressaca)"""
    @staticmethod
    def gerar_features_globais(df_historico_completo: pd.DataFrame, lags=[1, 2, 3, 6, 12]):
        dfs_processados = []
        for sku, group in df_historico_completo.groupby('produto'):
            g = group.copy().sort_values('mes_ano_dt')
            g['mes'] = g['mes_ano_dt'].dt.month
            g['mes_sin'] = np.sin(2 * np.pi * g['mes'] / 12)
            g['mes_cos'] = np.cos(2 * np.pi * g['mes'] / 12)
            
            for lag in lags: g[f'lag_{lag}'] = g['total_qtpedido'].shift(lag)
            
            g['media_movel_3'] = g['total_qtpedido'].shift(1).rolling(window=3).mean()
            g['media_movel_6'] = g['total_qtpedido'].shift(1).rolling(window=6).mean()
            g['volatilidade_3m'] = g['total_qtpedido'].shift(1).rolling(window=3).std().fillna(0)
            
            # Cálculo Global de Z-Score e Picos
            std_6 = g['total_qtpedido'].shift(1).rolling(window=6).std().replace(0, 1)
            g['z_score'] = (g['total_qtpedido'].shift(1) - g['media_movel_6']) / std_6
            g['is_pico'] = (g['z_score'] > 1.5).astype(int)
            g['picos_ultimos_3m'] = g['is_pico'].rolling(window=3).sum().fillna(0)
            
            g['diff_1'] = g['lag_1'] - g['lag_2']
            g['diff_2'] = g['lag_2'] - g['lag_3']
            dfs_processados.append(g)
            
        df_feat = pd.concat(dfs_processados).dropna(subset=[f'lag_{lags[-1]}']).copy()
        
        cols_cat = ['bu', 'categoria', 'segmento', 'curva_2026']
        for col in cols_cat:
            if col in df_feat.columns: df_feat[col] = df_feat[col].astype('category').cat.codes
                
        return df_feat