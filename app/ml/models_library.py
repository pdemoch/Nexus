import numpy as np
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

# =========================================================================
# IMPORTAÇÕES GRACIOSAS (Só falha no modelo específico se faltar a biblioteca)
# =========================================================================
try: import xgboost as xgb
except ImportError: xgb = None

try: import lightgbm as lgb
except ImportError: lgb = None

try: from sklearn.ensemble import RandomForestRegressor
except ImportError: RandomForestRegressor = None

try: from statsmodels.tsa.holtwinters import ExponentialSmoothing
except ImportError: ExponentialSmoothing = None

try: from statsmodels.tsa.forecasting.theta import ThetaModel
except ImportError: ThetaModel = None

try: from prophet import Prophet
except ImportError: Prophet = None

try: import pmdarima as pm
except ImportError: pm = None

# =========================================================================
# FUNÇÕES CORE DE AVALIAÇÃO E PREPARAÇÃO
# =========================================================================

def limpar_falsos_zeros(df, coluna_data='mes_data', coluna_volume='volume'):
    """Expurga meses antigos antes do lançamento oficial do SKU."""
    if df.empty: return df
    primeira_venda_idx = df[df[coluna_volume] > 0].index.min()
    if pd.isna(primeira_venda_idx): return df
    return df.loc[primeira_venda_idx:].copy().reset_index(drop=True)

def calcular_score_torneio(y_true, y_pred):
    """
    Penaliza WMAPE Alto + Penaliza BIAS (Viés Sistemático de Erro).
    Quanto menor o score, melhor o modelo (Score 0 é a perfeição).
    """
    y_true, y_pred = np.array(y_true), np.array(y_pred)
    y_pred = np.maximum(y_pred, 0) # Corta projeções negativas surreais
    
    soma_real = np.sum(y_true)
    if soma_real == 0: soma_real = 1e-5 # Evita divisão por zero
        
    erro_absoluto = np.sum(np.abs(y_true - y_pred))
    erro_vies = np.sum(y_pred - y_true) # >0 indica excesso, <0 indica falta
    
    wmape = (erro_absoluto / soma_real) * 100
    bias_pct = (erro_vies / soma_real) * 100
    
    # Score = WMAPE + 50% da gravidade do Viés
    score = wmape + (0.5 * abs(bias_pct))
    return score

# =========================================================================
# ARSENAL DE MODELOS (PADRÃO WRAPPER UNIVERSAL)
# =========================================================================

class AutoArimaModel:
    def fit(self, df):
        if pm is None: raise ImportError("pmdarima não instalado")
        self.model = pm.auto_arima(df['volume'].values, seasonal=False, suppress_warnings=True, error_action="ignore")
    def predict(self, df, horizon):
        return self.model.predict(n_periods=horizon).values if hasattr(self.model.predict(n_periods=horizon), 'values') else self.model.predict(n_periods=horizon)

class HoltWintersModel:
    def fit(self, df):
        if ExponentialSmoothing is None: raise ImportError("statsmodels não instalado")
        y = df['volume'].values
        seasonal = 'add' if len(y) >= 24 else None # Só aciona sazonalidade com 2 anos
        sp = 12 if seasonal else None
        self.model = ExponentialSmoothing(y, trend='add', seasonal=seasonal, seasonal_periods=sp, initialization_method="estimated").fit()
    def predict(self, df, horizon):
        return self.model.forecast(horizon).values if hasattr(self.model.forecast(horizon), 'values') else self.model.forecast(horizon)

class ThetaModelWrapper:
    def fit(self, df):
        if ThetaModel is None: raise ImportError("statsmodels não instalado")
        self.model = ThetaModel(df['volume'].values).fit()
    def predict(self, df, horizon):
        return self.model.forecast(horizon).values if hasattr(self.model.forecast(horizon), 'values') else self.model.forecast(horizon)

class ProphetModel:
    def fit(self, df):
        if Prophet is None: raise ImportError("prophet não instalado")
        self.model = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
        df_p = pd.DataFrame({'ds': df['mes_data'], 'y': df['volume']})
        self.model.fit(df_p)
    def predict(self, df, horizon):
        future = self.model.make_future_dataframe(periods=horizon, freq='MS')
        forecast = self.model.predict(future)
        return forecast['yhat'].iloc[-horizon:].values

class CrostonModel:
    def fit(self, df): pass
    def predict(self, df, horizon):
        y = df['volume'].values
        non_zero = y[y > 0]
        if len(non_zero) == 0: return np.zeros(horizon)
        
        intervals = np.diff(np.where(y > 0)[0], prepend=-1)
        mean_demand = np.mean(non_zero)
        mean_interval = np.mean(intervals)
        
        forecast = mean_demand / mean_interval if mean_interval > 0 else 0
        return np.full(horizon, forecast)

class LocalMLAutoregressive:
    def __init__(self, model_type='lgb'):
        self.model_type = model_type
        
    def _create_features(self, df):
        """Cria memória de curto prazo (Lags) e injeta PMV Financeiro."""
        df_feat = df.copy()
        df_feat['mes'] = df_feat['mes_data'].dt.month
        df_feat['lag_1'] = df_feat['volume'].shift(1)
        df_feat['lag_2'] = df_feat['volume'].shift(2)
        df_feat['lag_3'] = df_feat['volume'].shift(3)
        df_feat['media_movel_3'] = df_feat['volume'].rolling(3).mean()
        
        if 'pmv' in df_feat.columns:
            df_feat['pmv_lag_1'] = df_feat['pmv'].shift(1)
            
        return df_feat.dropna()

    def fit(self, df):
        df_feat = self._create_features(df)
        X = df_feat.drop(columns=['sku', 'mes_data', 'volume'], errors='ignore')
        y = df_feat['volume']
        
        if self.model_type == 'xgb' and xgb:
            self.model = xgb.XGBRegressor(n_estimators=100, max_depth=3, random_state=42)
        elif self.model_type == 'lgb' and lgb:
            self.model = lgb.LGBMRegressor(n_estimators=100, max_depth=3, random_state=42, verbose=-1)
        elif self.model_type == 'rf' and RandomForestRegressor:
            self.model = RandomForestRegressor(n_estimators=100, max_depth=3, random_state=42)
        else:
            self.model = None
            return
            
        if len(X) > 0: self.model.fit(X, y)
        else: self.model = None

    def predict(self, df, horizon):
        if getattr(self, 'model', None) is None:
            return np.full(horizon, df['volume'].mean() if len(df) else 0)
            
        current_history = df.copy()
        future_preds = []
        
        for _ in range(horizon):
            next_date = current_history['mes_data'].max() + pd.offsets.MonthBegin(1)
            new_row = pd.DataFrame({'mes_data': [next_date], 'volume': [np.nan]})
            
            if 'sku' in current_history.columns: new_row['sku'] = current_history['sku'].iloc[0]
            if 'pmv' in current_history.columns: new_row['pmv'] = current_history['pmv'].iloc[-1]
            
            current_history = pd.concat([current_history, new_row], ignore_index=True)
            df_feat = self._create_features(current_history)
            
            X_future = df_feat.drop(columns=['sku', 'mes_data', 'volume'], errors='ignore').iloc[-1:]
            pred = max(0, self.model.predict(X_future)[0])
            
            current_history.loc[current_history.index[-1], 'volume'] = pred
            future_preds.append(pred)
            
        return np.array(future_preds)