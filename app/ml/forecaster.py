import pandas as pd
import numpy as np
import polars as pl
from datetime import date
from sqlalchemy import text
from app.core.database import engine 
from app.ml.models_library import (
    HoltModel, HoltWintersModel, ThetaModelWrapper, CrostonModel, MovingAverageModel, 
    ProphetModel, AutoArimaModel, calcular_acuracia, LocalMLAutoregressive, DeepLearningForecaster
)

class NexusForecaster:
    def __init__(self):
        self.forecast_horizon = 5
        self.cv_folds = 3 # Número de simulações de "viagem no tempo" (Rolling Origin)
        
        # Pesos Táticos: M1(0.5x), M2(2.0x), M3(2.0x), M4(2.0x), M5(0.5x)
        self.pesos_taticos = np.array([0.5, 2.0, 2.0, 2.0, 0.5])
        
        self.especialistas = {
            'XGBoost_Direct': LocalMLAutoregressive('xgb'),
            'LightGBM_Direct': LocalMLAutoregressive('lgb'),
            'CatBoost_Direct': LocalMLAutoregressive('cat'),
            'RandomForest_Direct': LocalMLAutoregressive('rf'),
            'Prophet_Agressivo': ProphetModel(),
            'AutoARIMA_Sazonal': AutoArimaModel(),
            'TiDE_DeepLearning': DeepLearningForecaster('tide'),
            'TFT_DeepLearning': DeepLearningForecaster('tft'),   
            'HoltWinters_Sazonal': HoltWintersModel(),
            'Holt_Trend': HoltModel(),
            'Theta': ThetaModelWrapper(),
            'Croston_Intermitente': CrostonModel(),
            'MediaMovel_Fallback': MovingAverageModel(window=3)
        }

    def executar_arena(self, log_callback=print) -> pl.DataFrame:
        hoje = date.today()
        ciclo_atual = hoje.strftime("%m/%Y")
        mes_atual_str = hoje.strftime("%Y-%m")
        
        log_callback("📥 [ENGINE] Extraindo matriz histórica consolidada do PostgreSQL...")
        
        query = text("""
            SELECT 
                TO_CHAR(v.data_pedido, 'YYYY-MM') AS mes_ano,
                v.sku AS produto,
                p.descricao,
                SUM(v.qt_pedido) AS total_qtpedido,
                CASE 
                    WHEN SUM(v.qt_pedido) = 0 THEN 0 
                    ELSE SUM(v.vl_pedido) / SUM(v.qt_pedido) 
                END AS pmv
            FROM fato_vendas v
            JOIN dim_produtos p ON v.sku = p.sku
            WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') < :mes_atual
            GROUP BY 
                TO_CHAR(v.data_pedido, 'YYYY-MM'),
                v.sku, p.descricao
            ORDER BY produto, mes_ano;
        """)
        
        df = pd.read_sql(query, engine, params={"mes_atual": mes_atual_str})
        
        if df.empty: 
            log_callback(f"❌ [ENGINE] Nenhum histórico de vendas encontrado no banco para o ciclo {ciclo_atual}.")
            return pl.DataFrame()

        log_callback("⚙️ [ENGINE] Formatando cronologia contínua e tratando buracos (NaT resolvido)...")
        df['mes_ano_dt'] = pd.to_datetime(df['mes_ano'])
        data_min, data_max = df['mes_ano_dt'].min(), df['mes_ano_dt'].max()
        idx_completo = pd.date_range(start=data_min, end=data_max, freq='MS')
        skus = df['produto'].unique()
        
        df_completo_list = []
        for sku in skus:
            df_sku = df[df['produto'] == sku].set_index('mes_ano_dt')
            df_reidx = df_sku.reindex(idx_completo)
            df_reidx['produto'] = sku
            df_reidx['total_qtpedido'] = df_reidx['total_qtpedido'].fillna(0)
            if 'pmv' in df_reidx.columns: df_reidx['pmv'] = df_reidx['pmv'].replace(0, np.nan).ffill().bfill()
            df_reidx = df_reidx.reset_index().rename(columns={'index': 'mes_ano_dt'})
            df_completo_list.append(df_reidx)
            
        df_global = pd.concat(df_completo_list)

        log_callback(f"⚔️ [ENGINE] Iniciando Arena Time-Series CV (Rolling Origin) para {len(skus)} SKUs...")
        resultados_forecast = []
        contador = 0
        data_inicio_previsao = data_max + pd.DateOffset(months=1)

        for sku in skus:
            df_sku = df_global[df_global['produto'] == sku].sort_values('mes_ano_dt')
            serie = pd.Series(df_sku['total_qtpedido'].values, index=df_sku['mes_ano_dt'])
            ultimo_pmv = df_sku.iloc[-1].get('pmv', 0)
            
            tamanho_serie = len(serie)
            avaliacoes_cv = {nome: [] for nome in self.especialistas.keys()}
            
            # =========================================================================
            # A NOVA ARENA: CROSS-VALIDATION TEMPORAL (ROLLING ORIGIN)
            # =========================================================================
            # Se o SKU tiver pouco histórico (ex: lançamento), reduzimos as janelas de CV
            folds_aplicaveis = min(self.cv_folds, max(1, tamanho_serie - self.forecast_horizon - 6))
            
            if folds_aplicaveis >= 1:
                for fold in range(folds_aplicaveis):
                    # O corte desliza para trás no tempo. Fold 0 = Teste recente. Fold 1 = Teste 1 mês mais antigo.
                    corte_teste = self.forecast_horizon + fold
                    
                    treino_cv = serie.iloc[:-corte_teste]
                    teste_real_cv = serie.iloc[-corte_teste : -corte_teste + self.forecast_horizon] if fold > 0 else serie.iloc[-corte_teste:]
                    
                    # Se o treino ficar muito pequeno, ignora este fold
                    if len(treino_cv) < 6:
                        continue

                    # Batalha dos Modelos nesta janela temporal específica
                    for nome, modelo in self.especialistas.items():
                        try:
                            preds_cv = modelo.fit_predict(treino_cv, self.forecast_horizon)
                            # Acurácia com PESOS TÁTICOS (Força os modelos a acertarem M2, M3 e M4)
                            acc_cv = calcular_acuracia(teste_real_cv.values, preds_cv, pesos=self.pesos_taticos)
                            avaliacoes_cv[nome].append(acc_cv)
                        except Exception:
                            avaliacoes_cv[nome].append(0.0)
            
            # =========================================================================
            # CONSOLIDAÇÃO DO RANKING E PREVISÃO REAL DO FUTURO
            # =========================================================================
            ranking = []
            for nome, acc_lista in avaliacoes_cv.items():
                if acc_lista:
                    # A nota final do modelo é a MÉDIA de como ele sobreviveu nas várias viagens no tempo
                    media_acc = np.mean(acc_lista)
                    ranking.append((nome, media_acc))
            
            if not ranking:
                ranking = [('MediaMovel_Fallback', 0.0)]
                
            # Ordena do melhor para o pior
            ranking.sort(key=lambda item: item[1], reverse=True)
            melhor_modelo_nome = ranking[0][0]
            maior_acuracia_media = ranking[0][1]

            # Treina o modelo vencedor com 100% dos dados para prever o futuro real
            modelo_campeao = self.especialistas.get(melhor_modelo_nome, self.especialistas['MediaMovel_Fallback'])
            previsao_futura = modelo_campeao.fit_predict(serie, self.forecast_horizon)
            
            for i, vol_proj in enumerate(previsao_futura):
                data_proj = (data_inicio_previsao + pd.DateOffset(months=i)).to_pydatetime().date()
                resultados_forecast.append({
                    "ciclo_sop": ciclo_atual, 
                    "produto": sku, 
                    "mes_projetado": data_proj,
                    "vol_ia_global": round(max(0, vol_proj), 2), 
                    "pmv_aplicado": round(ultimo_pmv, 2),
                    "modelo_vencedor": melhor_modelo_nome, 
                    "acuracia": round(maior_acuracia_media, 4)  
                })
            
            contador += 1
            if contador % 50 == 0: log_callback(f"   ⏳ Arena processou {contador}/{len(skus)} SKUs com Rolling Origin...")

        df_resultados = pl.DataFrame(resultados_forecast)
        log_callback(f"✅ [ENGINE] {df_resultados.height} projeções geradas com sucesso via Time-Series CV!")
        return df_resultados