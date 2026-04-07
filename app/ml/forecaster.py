import pandas as pd
import numpy as np
import polars as pl
from datetime import date
import xgboost as xgb
import lightgbm as lgb
from app.ml.models_library import (
    HoltModel, HoltWintersModel, ThetaModelWrapper, CrostonModel, MovingAverageModel, 
    GlobalMLTrainer, calcular_acuracia
)

class NexusForecaster:
    def __init__(self):
        self.holdout_months = 6
        self.forecast_horizon = 5
        self.especialistas = {
            'HoltWinters_Sazonal': HoltWintersModel(),
            'Holt_Trend': HoltModel(),
            'Theta': ThetaModelWrapper(),
            'Croston_Intermitente': CrostonModel(),
            'MediaMovel_3M': MovingAverageModel(window=3)
        }

    def executar_arena(self, df_ia_polars: pl.DataFrame, log_callback=print) -> pl.DataFrame:
        hoje = date.today()
        ciclo_atual = hoje.strftime("%m/%Y")
        
        if df_ia_polars.is_empty(): 
            log_callback(f"❌ [ENGINE] Nenhum dado encontrado na base IA para o ciclo {ciclo_atual}.")
            return pl.DataFrame()

        df = df_ia_polars.to_pandas()
        mes_atual_str = hoje.strftime("%Y-%m")
        df = df[df['mes_ano'] < mes_atual_str].copy()
        
        log_callback("⚙️ [ENGINE] Formatando cronologia e matriz global...")
        df['mes_ano_dt'] = pd.to_datetime(df['mes_ano'])
        data_min, data_max = df['mes_ano_dt'].min(), df['mes_ano_dt'].max()
        data_corte_holdout = data_max - pd.DateOffset(months=self.holdout_months - 1)
        idx_completo = pd.date_range(start=data_min, end=data_max, freq='MS')
        skus = df['produto'].unique()
        
        df_completo_list = []
        for sku in skus:
            df_sku = df[df['produto'] == sku].set_index('mes_ano_dt')
            df_reidx = df_sku.reindex(idx_completo)
            df_reidx['produto'] = sku
            df_reidx['total_qtpedido'] = df_reidx['total_qtpedido'].fillna(0)
            for col in ['descricao', 'bu', 'categoria', 'segmento', 'curva_2026']:
                if col in df_reidx.columns: df_reidx[col] = df_reidx[col].ffill().bfill()
            if 'pmv' in df_reidx.columns: df_reidx['pmv'] = df_reidx['pmv'].replace(0, np.nan).ffill().bfill()
            df_reidx = df_reidx.reset_index().rename(columns={'index': 'mes_ano_dt'})
            df_completo_list.append(df_reidx)
            
        df_global = pd.concat(df_completo_list)
        
        log_callback("🦾 [ENGINE] Treinando os Titãs (XGBoost e LightGBM)...")
        df_feat = GlobalMLTrainer.gerar_features_globais(df_global)
        df_treino_global = df_feat[df_feat['mes_ano_dt'] < data_corte_holdout]

        features = ['mes', 'mes_sin', 'mes_cos', 'lag_1', 'lag_2', 'lag_3', 'lag_6', 'lag_12', 'media_movel_3']

        for c in ['bu', 'categoria', 'segmento', 'curva_2026']:
            if c in df_treino_global.columns: features.append(c)
        
        lgb_model = lgb.LGBMRegressor(n_estimators=100, learning_rate=0.05, verbose=-1, random_state=42)
        xgb_model = xgb.XGBRegressor(n_estimators=100, learning_rate=0.05, random_state=42)
        
        if not df_treino_global.empty:
            lgb_model.fit(df_treino_global[features], df_treino_global['total_qtpedido'])
            xgb_model.fit(df_treino_global[features], df_treino_global['total_qtpedido'])

        log_callback(f"⚔️ [ENGINE] Iniciando a Arena para {len(skus)} SKUs...")
        resultados_forecast = []
        contador = 0
        data_inicio_previsao = data_max + pd.DateOffset(months=1)

        for sku in skus:
            df_sku = df_global[df_global['produto'] == sku].sort_values('mes_ano_dt')
            serie = pd.Series(df_sku['total_qtpedido'].values, index=df_sku['mes_ano_dt'])
            info = df_sku.iloc[-1]
            ultimo_pmv = info.get('pmv', 0)
            
            treino_local = serie.iloc[:-self.holdout_months]
            holdout_real = serie.iloc[-self.holdout_months:]
            melhor_modelo = 'MediaMovel_3M'
            maior_acuracia = -1.0
            
            df_holdout_sku = df_holdout_global = df_feat[(df_feat['produto'] == sku) & (df_feat['mes_ano_dt'] >= data_corte_holdout)]
            
            if len(df_holdout_sku) == self.holdout_months:
                pred_lgb = np.maximum(0, lgb_model.predict(df_holdout_sku[features]))
                pred_xgb = np.maximum(0, xgb_model.predict(df_holdout_sku[features]))
                acc_lgb = calcular_acuracia(holdout_real.values, pred_lgb)
                acc_xgb = calcular_acuracia(holdout_real.values, pred_xgb)
                if acc_lgb > maior_acuracia: maior_acuracia, melhor_modelo = acc_lgb, 'LightGBM_Global'
                if acc_xgb > maior_acuracia: maior_acuracia, melhor_modelo = acc_xgb, 'XGBoost_Global'

            if len(treino_local) >= 12:
                for nome, modelo in self.especialistas.items():
                    try:
                        preds = modelo.fit_predict(treino_local, self.holdout_months)
                        acc = calcular_acuracia(holdout_real.values, preds)
                        if acc > maior_acuracia: maior_acuracia, melhor_modelo = acc, nome
                    except: continue

            modelo_final = self.especialistas.get(melhor_modelo, self.especialistas['MediaMovel_3M'])
            try:
                previsao_futura = modelo_final.fit_predict(serie, self.forecast_horizon)
            except:
                previsao_futura = self.especialistas['MediaMovel_3M'].fit_predict(serie, self.forecast_horizon)
            
            for i, vol_proj in enumerate(previsao_futura):
                data_proj = (data_inicio_previsao + pd.DateOffset(months=i)).to_pydatetime().date()
                
                resultados_forecast.append({
                    "ciclo_sop": ciclo_atual,
                    "produto": sku,
                    "mes_projetado": data_proj,
                    "vol_ia_global": round(max(0, vol_proj), 2),
                    "pmv_aplicado": round(ultimo_pmv, 2),
                    "modelo_vencedor": melhor_modelo,
                    "acuracia": round(maior_acuracia, 4)  
                })
            
            contador += 1
            if contador % 50 == 0: log_callback(f"   ⏳ Arena analisou {contador}/{len(skus)} SKUs...")

        df_resultados = pl.DataFrame(resultados_forecast)
        log_callback(f"✅ [ENGINE] {df_resultados.height} projeções geradas com sucesso!")
        return df_resultados