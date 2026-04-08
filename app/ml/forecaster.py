import pandas as pd
import numpy as np
import polars as pl
from datetime import date
import xgboost as xgb
import lightgbm as lgb
from app.ml.models_library import (
    HoltModel, HoltWintersModel, ThetaModelWrapper, CrostonModel, MovingAverageModel, 
    ProphetModel, AutoArimaModel, GlobalMLTrainer, calcular_acuracia, LocalMLAutoregressive
)

class NexusForecaster:
    def __init__(self):
        self.holdout_months = 6
        self.forecast_horizon = 5
        
        # A Nova Tropa de Elite (Clássicos + Machine Learning Local)
        self.especialistas = {
            'Prophet_Agressivo': ProphetModel(),
            'AutoARIMA_Sazonal': AutoArimaModel(),
            'XGBoost_Local': LocalMLAutoregressive('xgb'),
            'LightGBM_Local': LocalMLAutoregressive('lgb'),
            'RandomForest_Local': LocalMLAutoregressive('rf'),
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
        
        log_callback("🦾 [ENGINE] Treinando os Titãs Globais (Matriz Unificada)...")
        df_feat = GlobalMLTrainer.gerar_features_globais(df_global)
        df_treino_global = df_feat[df_feat['mes_ano_dt'] < data_corte_holdout]

        features = ['mes', 'mes_sin', 'mes_cos', 'lag_1', 'lag_2', 'lag_3', 'lag_6', 'lag_12', 'media_movel_3', 'volatilidade_3m', 'diff_1', 'diff_2']
        for c in ['bu', 'categoria', 'segmento', 'curva_2026']:
            if c in df_treino_global.columns: features.append(c)
        
        lgb_model = lgb.LGBMRegressor(n_estimators=100, learning_rate=0.05, verbose=-1, random_state=42)
        xgb_model = xgb.XGBRegressor(n_estimators=100, learning_rate=0.05, random_state=42)
        
        if not df_treino_global.empty:
            lgb_model.fit(df_treino_global[features], df_treino_global['total_qtpedido'])
            xgb_model.fit(df_treino_global[features], df_treino_global['total_qtpedido'])

        # =========================================================================
        # FUNÇÃO DE LOOP AUTOREGRESSIVO PARA OS MODELOS GLOBAIS (O "MATA-LINHA-RETA")
        # =========================================================================
        def projetar_ml_global_recursivo(nome_modelo, serie_historica, sku_atual):
            modelo = lgb_model if 'LightGBM' in nome_modelo else xgb_model
            preds = []
            hist = list(serie_historica.values)
            
            # Recupera as características estáticas codificadas daquele SKU
            df_feat_sku = df_feat[df_feat['produto'] == sku_atual]
            last_feat_row = df_feat_sku.iloc[-1] if not df_feat_sku.empty else {c: 0 for c in features}

            for i in range(self.forecast_horizon):
                dt_alvo = data_inicio_previsao + pd.DateOffset(months=i)
                mes_alvo = dt_alvo.month
                
                # O Segredo: Recalcular as features dinamicamente baseando-se no futuro simulado
                lag_1 = hist[-1] if len(hist) >= 1 else 0
                lag_2 = hist[-2] if len(hist) >= 2 else lag_1
                lag_3 = hist[-3] if len(hist) >= 3 else lag_2
                lag_6 = hist[-6] if len(hist) >= 6 else lag_3
                lag_12 = hist[-12] if len(hist) >= 12 else lag_6
                
                novo_dado = {}
                for col in features:
                    if col == 'mes': novo_dado[col] = mes_alvo
                    elif col == 'mes_sin': novo_dado[col] = np.sin(2 * np.pi * mes_alvo / 12)
                    elif col == 'mes_cos': novo_dado[col] = np.cos(2 * np.pi * mes_alvo / 12)
                    elif col == 'lag_1': novo_dado[col] = lag_1
                    elif col == 'lag_2': novo_dado[col] = lag_2
                    elif col == 'lag_3': novo_dado[col] = lag_3
                    elif col == 'lag_6': novo_dado[col] = lag_6
                    elif col == 'lag_12': novo_dado[col] = lag_12
                    elif col == 'media_movel_3': novo_dado[col] = np.mean(hist[-3:]) if len(hist)>=3 else lag_1
                    elif col == 'volatilidade_3m': novo_dado[col] = np.std(hist[-3:]) if len(hist)>=3 else 0
                    elif col == 'diff_1': novo_dado[col] = lag_1 - lag_2
                    elif col == 'diff_2': novo_dado[col] = lag_2 - lag_3
                    else: novo_dado[col] = last_feat_row.get(col, 0) # Categorias
                        
                df_pred = pd.DataFrame([novo_dado])[features] # Garante a ordem correta
                pred = max(0, modelo.predict(df_pred)[0])
                preds.append(pred)
                hist.append(pred) # Injeta o futuro de volta no passado!
                
            return np.array(preds)

        log_callback(f"⚔️ [ENGINE] Iniciando a Arena Híbrida (Global vs Local) para {len(skus)} SKUs...")
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
            
            avaliacoes_sku = {}
            
            # --- 1. Avalia Modelos Globais ---
            df_holdout_sku = df_feat[(df_feat['produto'] == sku) & (df_feat['mes_ano_dt'] >= data_corte_holdout)]
            if len(df_holdout_sku) == self.holdout_months:
                pred_lgb = np.maximum(0, lgb_model.predict(df_holdout_sku[features]))
                pred_xgb = np.maximum(0, xgb_model.predict(df_holdout_sku[features]))
                avaliacoes_sku['LightGBM_Global'] = {'acc': calcular_acuracia(holdout_real.values, pred_lgb), 'preds': pred_lgb}
                avaliacoes_sku['XGBoost_Global'] = {'acc': calcular_acuracia(holdout_real.values, pred_xgb), 'preds': pred_xgb}

            # --- 2. Avalia Especialistas Locais (ML e Estatística) ---
            if len(treino_local) >= 12:
                for nome, modelo in self.especialistas.items():
                    try:
                        preds = modelo.fit_predict(treino_local, self.holdout_months)
                        acc = calcular_acuracia(holdout_real.values, preds)
                        avaliacoes_sku[nome] = {'acc': acc, 'preds': preds}
                    except: continue
            
            if not avaliacoes_sku:
                fallback_pred = self.especialistas['MediaMovel_3M'].fit_predict(treino_local, self.holdout_months)
                avaliacoes_sku['MediaMovel_3M'] = {'acc': calcular_acuracia(holdout_real.values, fallback_pred), 'preds': fallback_pred}

            # --- 3. O ENSEMBLE (O Consenso da Máquina) ---
            ranking = sorted(avaliacoes_sku.items(), key=lambda item: item[1]['acc'], reverse=True)
            melhor_modelo_nome = ranking[0][0]
            maior_acuracia = ranking[0][1]['acc']
            
            if len(ranking) >= 2 and ranking[0][1]['acc'] > 30.0:
                nome_top1, dict_top1 = ranking[0]
                nome_top2, dict_top2 = ranking[1]
                preds_ensemble = (dict_top1['preds'] + dict_top2['preds']) / 2
                acc_ensemble = calcular_acuracia(holdout_real.values, preds_ensemble)
                
                if acc_ensemble > maior_acuracia:
                    melhor_modelo_nome = f"Ensemble_{nome_top1.split('_')[0]}+{nome_top2.split('_')[0]}"
                    maior_acuracia = acc_ensemble
                    avaliacoes_sku['__ENSEMBLE_INSTRUCTION__'] = (nome_top1, nome_top2)

            # --- 4. PREVISÃO OFICIAL DO FUTURO (COM AUTOREGRESSÃO GARANTIDA) ---
            if melhor_modelo_nome.startswith("Ensemble"):
                m1_name, m2_name = avaliacoes_sku['__ENSEMBLE_INSTRUCTION__']
                
                prev_m1 = projetar_ml_global_recursivo(m1_name, serie, sku) if "Global" in m1_name else self.especialistas[m1_name].fit_predict(serie, self.forecast_horizon)
                prev_m2 = projetar_ml_global_recursivo(m2_name, serie, sku) if "Global" in m2_name else self.especialistas[m2_name].fit_predict(serie, self.forecast_horizon)
                
                previsao_futura = (prev_m1 + prev_m2) / 2

            elif "Global" in melhor_modelo_nome:
                previsao_futura = projetar_ml_global_recursivo(melhor_modelo_nome, serie, sku)
            else:
                modelo_final = self.especialistas.get(melhor_modelo_nome, self.especialistas['MediaMovel_3M'])
                previsao_futura = modelo_final.fit_predict(serie, self.forecast_horizon)
            
            # --- 5. Gravação no Banco de Dados ---
            for i, vol_proj in enumerate(previsao_futura):
                data_proj = (data_inicio_previsao + pd.DateOffset(months=i)).to_pydatetime().date()
                resultados_forecast.append({
                    "ciclo_sop": ciclo_atual, "produto": sku, "mes_projetado": data_proj,
                    "vol_ia_global": round(max(0, vol_proj), 2), "pmv_aplicado": round(ultimo_pmv, 2),
                    "modelo_vencedor": melhor_modelo_nome, "acuracia": round(maior_acuracia, 4)  
                })
            
            contador += 1
            if contador % 50 == 0: log_callback(f"   ⏳ Arena analisou {contador}/{len(skus)} SKUs...")

        df_resultados = pl.DataFrame(resultados_forecast)
        log_callback(f"✅ [ENGINE] {df_resultados.height} projeções autoregressivas geradas com sucesso!")
        return df_resultados