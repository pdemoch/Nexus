import numpy as np
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
from sklearn.ensemble import RandomForestRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.forecasting.theta import ThetaModel
import warnings

warnings.filterwarnings("ignore")

try:
    from prophet import Prophet
except ImportError:
    pass

try:
    import pmdarima as pm
except ImportError:
    pass

try:
    from catboost import CatBoostRegressor
except ImportError:
    pass


def calcular_acuracia(y_true, y_pred, pesos=None):
    """Calcula a Acurácia baseada no WMAPE ponderado taticamente."""
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    
    if pesos is None:
        pesos = np.ones(len(y_true))
    else:
        pesos = np.array(pesos)[:len(y_true)]

    soma_real = np.sum(y_true * pesos)
    if soma_real == 0:
        return 0.0 if np.sum(y_pred * pesos) > 0 else 100.0
        
    erro_abs = np.abs(y_true - y_pred) * pesos
    wmape = np.sum(erro_abs) / soma_real
    return max(0.0, 100.0 * (1 - wmape))


# =========================================================================
# BIBLIOTECA DE ESTATÍSTICA CLÁSSICA
# =========================================================================

class MovingAverageModel:
    def __init__(self, window=3):
        self.window = window
        
    def fit_predict(self, serie, horizon):
        val = serie.iloc[-self.window:].mean() if len(serie) >= self.window else serie.mean()
        return [val] * horizon

class HoltModel:
    def fit_predict(self, serie, horizon):
        if len(serie) < 4: return [serie.iloc[-1]]*horizon
        model = ExponentialSmoothing(serie, trend='add', seasonal=None, initialization_method='estimated').fit()
        return model.forecast(horizon).tolist()

class HoltWintersModel:
    def fit_predict(self, serie, horizon):
        if len(serie) < 14: return HoltModel().fit_predict(serie, horizon)
        model = ExponentialSmoothing(serie, trend='add', seasonal='add', seasonal_periods=12, initialization_method='estimated').fit()
        return model.forecast(horizon).tolist()

class ThetaModelWrapper:
    def fit_predict(self, serie, horizon):
        if len(serie) < 4: return [serie.iloc[-1]]*horizon
        res = ThetaModel(serie).fit()
        return res.forecast(horizon).tolist()

class CrostonModel:
    def fit_predict(self, serie, horizon):
        y = serie.values
        if len(y) < 3: return [y[-1]] * horizon
        a = 0.1
        z, p, q = np.zeros(len(y)), np.zeros(len(y)), 1
        z[0], p[0] = y[0], 1
        for t in range(1, len(y)):
            if y[t] > 0:
                z[t] = a * y[t] + (1 - a) * z[t-1]
                p[t] = a * q + (1 - a) * p[t-1]
                q = 1
            else:
                z[t], p[t] = z[t-1], p[t-1]
                q += 1
        pred = z[-1] / max(p[-1], 0.1)
        return [pred] * horizon

class ProphetModel:
    def fit_predict(self, serie, horizon):
        if 'Prophet' not in globals() or len(serie) < 5:
            return [serie.iloc[-1]]*horizon
        df = serie.reset_index()
        df.columns = ['ds', 'y']
        m = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
        m.fit(df)
        future = m.make_future_dataframe(periods=horizon, freq='MS')
        fcst = m.predict(future)
        return fcst['yhat'].iloc[-horizon:].clip(lower=0).tolist()

class AutoArimaModel:
    def fit_predict(self, serie, horizon):
        if 'pm' not in globals() or len(serie) < 14:
            return [serie.iloc[-1]]*horizon
        model = pm.auto_arima(serie, seasonal=True, m=12, suppress_warnings=True, error_action='ignore')
        return model.predict(n_periods=horizon).clip(lower=0).tolist()

class DeepLearningForecaster:
    def __init__(self, model_type):
        self.model_type = model_type
    def fit_predict(self, serie, horizon):
        return [serie.iloc[-1]]*horizon


# =========================================================================
# A MÁGICA: LocalMLAutoregressive com Sinais Temporais (Fim da Linearidade)
# =========================================================================
class LocalMLAutoregressive:
    def __init__(self, model_type='xgb'):
        self.model_type = model_type

    def _create_features(self, serie: pd.Series):
        """Vetoriza a série histórica em variáveis ricas de Sazonalidade e Aceleração."""
        df = serie.reset_index()
        df.columns = ['data', 'y']
        
        # 1. Decomposição de Fourier (O Ciclo Sazonal)
        df['mes'] = df['data'].dt.month
        df['mes_sin'] = np.sin(2 * np.pi * df['mes'] / 12)
        df['mes_cos'] = np.cos(2 * np.pi * df['mes'] / 12)
        
        # 2. Lags (A Memória do Passado)
        for i in range(1, 4):
            df[f'lag_{i}'] = df['y'].shift(i)
            
        # 3. Suavização e Volatilidade (O Comportamento das Ondas)
        df['mm_3'] = df['y'].shift(1).rolling(window=3, min_periods=1).mean()
        df['volatilidade_3m'] = df['y'].shift(1).rolling(window=3, min_periods=1).std().fillna(0)
        
        # 4. Tendência (O Momentum Financeiro)
        df['tendencia'] = df['lag_1'] - df['lag_2']
        
        return df

    def fit_predict(self, serie: pd.Series, horizon: int):
        df = self._create_features(serie)
        # Ao usar shift(3), perdemos as primeiras 3 linhas para treino. O dropna limpa a base.
        train_df = df.dropna()
        
        if len(train_df) < 4:
            # Se for NPI ou tiver pouco histórico, recua para uma média segura.
            return [max(0, float(serie.iloc[-1]))] * horizon

        features = [c for c in train_df.columns if c not in ['data', 'y', 'mes']]
        X = train_df[features]
        y = train_df['y']
        
        if self.model_type == 'xgb':
            model = xgb.XGBRegressor(n_estimators=60, max_depth=3, learning_rate=0.1, random_state=42, objective='reg:tweedie')
        elif self.model_type == 'lgb':
            model = lgb.LGBMRegressor(n_estimators=60, max_depth=3, learning_rate=0.1, random_state=42, verbose=-1)
        elif self.model_type == 'cat' and 'CatBoostRegressor' in globals():
            model = CatBoostRegressor(n_estimators=60, depth=3, learning_rate=0.1, random_state=42, verbose=0)
        else:
            model = RandomForestRegressor(n_estimators=60, max_depth=3, random_state=42)
            
        model.fit(X, y)
        
        # A PREDIÇÃO RECURSIVA:
        # A IA avança mês a mês. O que ela previu para M1 torna-se o histórico (Lag) para prever M2!
        preds = []
        current_serie = serie.copy()
        
        for i in range(horizon):
            next_date = current_serie.index[-1] + pd.DateOffset(months=1)
            current_serie.loc[next_date] = 0.0 # Placeholder
            
            df_future = self._create_features(current_serie)
            next_X = df_future.iloc[[-1]][features]
            
            pred = float(model.predict(next_X)[0])
            pred = max(0.0, pred) # Bloqueia valores negativos
            preds.append(pred)
            
            # Retroalimenta a máquina: A previsão atual vira o passado do próximo mês
            current_serie.loc[next_date] = pred
            
        return preds