import numpy as np
import pandas as pd
import warnings
import traceback

warnings.filterwarnings("ignore")

# Tenta importar os motores avançados, se não houver, ignora silenciosamente.
try:
    import xgboost as xgb
except ImportError:
    pass

try:
    import lightgbm as lgb
except ImportError:
    pass

try:
    from sklearn.ensemble import RandomForestRegressor
except ImportError:
    pass

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
except ImportError:
    pass

try:
    from statsmodels.tsa.forecasting.theta import ThetaModel
except ImportError:
    pass

try:
    from prophet import Prophet
except ImportError:
    pass

try:
    import pmdarima as pm
except ImportError:
    pass

# =========================================================================
# FUNÇÕES CORE DE AVALIAÇÃO E PREPARAÇÃO
# =========================================================================

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
    acuracia = max(0.0, 100.0 - (wmape * 100))
    return acuracia

def limpar_falsos_zeros(df_treino: pd.DataFrame, col_data: str, col_volume: str) -> pd.DataFrame:
    """
    CURA MATEMÁTICA 1: Corte de Ciclo de Vida.
    Encontra a primeira vez que o produto vendeu. Todos os zeros (e NaNs) ANTES dessa data são 
    apagados para não enviesar a IA de que o produto fracassou durante 1 ano antes do lançamento.
    """
    df = df_treino.copy().sort_values(by=col_data).reset_index(drop=True)
    
    # Preenche NaNs com 0 para segurança
    df[col_volume] = df[col_volume].fillna(0)
    
    # Encontra o índice da primeira venda > 0
    primeira_venda_idx = df[df[col_volume] > 0].index.min()
    
    if pd.isna(primeira_venda_idx):
        # Se NUNCA vendeu na vida, devolve o dataframe vazio ou só zeros
        return df
        
    return df.iloc[primeira_venda_idx:].copy().reset_index(drop=True)


# =========================================================================
# MOTORES ESTATÍSTICOS CLÁSSICOS (UNIVARIADOS)
# Excelentes para o Motor Alpha (Padrões Suaves e Sazonais)
# =========================================================================

class AutoArimaModel:
    def __init__(self):
        self.model = None

    def predict(self, serie: pd.Series, horizon=5, exogenous=None):
        if len(serie) < 12: return np.full(horizon, serie.mean())
        if serie.sum() == 0: return np.zeros(horizon)
        
        try:
            self.model = pm.auto_arima(
                serie, seasonal=True, m=12, suppress_warnings=True, 
                error_action="ignore", stepwise=True
            )
            # O AutoARIMA suporta exógenas! Se o Motor Beta enviar o estoque, ele usa.
            if exogenous is not None and not exogenous.empty:
                # Nota: Na predição, precisaríamos conhecer as exógenas futuras.
                # Como a estratégia é Direct, esta versão clássica pode não usar o exógeno no futuro de forma simples.
                # Vamos forçar regressão pura sem exógenas para este modelo clássico por segurança.
                pass
            return np.maximum(0, self.model.predict(n_periods=horizon))
        except:
            return np.full(horizon, serie.mean())

class HoltWintersModel:
    def __init__(self):
        pass

    def predict(self, serie: pd.Series, horizon=5, exogenous=None):
        if len(serie) < 24: return np.full(horizon, serie.mean())
        if serie.sum() == 0: return np.zeros(horizon)
        try:
            model = ExponentialSmoothing(serie, trend='add', seasonal='add', seasonal_periods=12).fit()
            return np.maximum(0, model.forecast(horizon))
        except:
            return np.full(horizon, serie.mean())


# =========================================================================
# O ESPECIALISTA EM INTERMITÊNCIA (O "Deserto")
# Fundamental para o Motor Beta (SKUs com muitas rupturas e zeros)
# =========================================================================

class CrostonModel:
    def __init__(self):
        pass
        
    def predict(self, serie: pd.Series, horizon=5, exogenous=None):
        """
        Calcula a demanda intermitente separando a probabilidade de venda do tamanho da venda.
        """
        arr = serie.values
        if np.sum(arr) == 0: return np.zeros(horizon)
        
        # Encontra os índices onde houve venda
        vendas_idxs = np.where(arr > 0)[0]
        if len(vendas_idxs) < 2: return np.full(horizon, arr.mean())
        
        # Calcula intervalos entre vendas
        intervalos = np.diff(vendas_idxs)
        media_intervalo = np.mean(intervalos)
        
        # Calcula a média do volume QUANDO VENDE
        vendas = arr[vendas_idxs]
        media_venda = np.mean(vendas)
        
        # A previsão é o Volume / Intervalo
        previsao_plana = media_venda / media_intervalo if media_intervalo > 0 else media_venda
        return np.full(horizon, previsao_plana)


# =========================================================================
# MOTORES MACHINE LEARNING COM SUPORTE MULTIVARIADO E DIRECT MULTI-STEP
# Estes serão os "Cérebros" reais de ambos os Motores (Alpha e Beta)
# =========================================================================

class LocalMLAutoregressive:
    def __init__(self, model_type='xgb'):
        self.model_type = model_type
        # Guardaremos um modelo DIFERENTE para cada mês do horizonte (Direct Multi-step)
        self.models_per_step = {} 

    def _create_features(self, df_input: pd.DataFrame, step: int, tem_estoque: bool) -> pd.DataFrame:
        """
        Cria as variáveis explicativas (Features).
        CURA MATEMÁTICA 4: O "step" garante que para prever M+2, usamos o Estoque de Hoje (M-1), e não o Estoque de M+1 (que não existe).
        """
        df = df_input.copy()
        
        # O index deve ser datetime
        df['mes'] = df.index.month
        df['trimestre'] = df.index.quarter
        
        # Lags da Variável Alvo (Vendas)
        df[f'venda_lag_{step}'] = df['volume'].shift(step)
        df[f'venda_lag_{step+1}'] = df['volume'].shift(step + 1)
        df[f'venda_lag_{step+2}'] = df['volume'].shift(step + 2)
        
        # Lags Exógenos (Se o Motor for o Beta)
        if tem_estoque and 'estoque' in df.columns:
            # O Estoque de hoje impacta o mês do horizonte
            df[f'estoque_lag_{step}'] = df['estoque'].shift(step)
            
            # Feature de engenharia: A Relação Venda/Estoque do passado (Giro)
            df[f'giro_lag_{step}'] = np.where(df[f'venda_lag_{step}'] > 0, df[f'estoque_lag_{step}'] / df[f'venda_lag_{step}'], 0)
        
        return df.dropna()

    def predict(self, df_completo: pd.DataFrame, horizon=5) -> np.ndarray:
        """
        df_completo: DataFrame do Pandas indexado por Data.
        Deve ter a coluna 'volume'. Pode ou não ter a coluna 'estoque' (Motor Beta vs Alpha).
        """
        if len(df_completo) < horizon + 3:
            return np.full(horizon, df_completo['volume'].mean())
        if df_completo['volume'].sum() == 0:
            return np.zeros(horizon)

        tem_estoque = 'estoque' in df_completo.columns
        predicoes = []

        # CURA MATEMÁTICA 2: Direct Multi-Step Forecasting
        # Treinamos 1 modelo isolado para CADA mês que queremos prever.
        for h in range(1, horizon + 1): # h=1 é o próximo mês (M0), h=2 é M1, etc.
            
            # Cria a matriz de treino específica para este salto (step)
            df_train = self._create_features(df_completo, step=h, tem_estoque=tem_estoque)
            
            if len(df_train) < 5: # Poucos dados após o dropna, falha segura
                predicoes.append(df_completo['volume'].mean())
                continue
                
            y = df_train['volume'].values
            X = df_train.drop(columns=['volume', 'estoque'] if tem_estoque else ['volume']).values

            # Escolhe a arma
            if self.model_type == 'xgb' and 'xgb' in globals():
                model = xgb.XGBRegressor(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42, objective='reg:tweedie')
            elif self.model_type == 'lgb' and 'lgb' in globals():
                model = lgb.LGBMRegressor(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42, verbose=-1)
            else:
                model = RandomForestRegressor(n_estimators=100, max_depth=3, random_state=42)
                
            model.fit(X, y)
            
            # O cenário futuro: pegamos a ÚLTIMA linha do dataframe original
            # e calculamos os lags dela com o nosso `step` para prever exatamente aquele horizonte
            
            # Truque: Criamos um dataframe "esticado" até hoje para o _create_features puxar o shift correto
            df_future_base = df_completo.copy()
            df_futuro_features = self._create_features(df_future_base, step=h, tem_estoque=tem_estoque)
            
            # A última linha dessas features calculadas é a que nos dará a previsão do Mês H
            X_future = df_futuro_features.drop(columns=['volume', 'estoque'] if tem_estoque else ['volume']).iloc[-1].values.reshape(1, -1)
            
            pred = max(0, model.predict(X_future)[0])
            predicoes.append(pred)

        return np.array(predicoes)