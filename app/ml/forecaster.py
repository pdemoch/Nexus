import pandas as pd
import numpy as np
import polars as pl
import os
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from app.core.database import SessionLocal 
from app.ml.models_library import (
    AutoArimaModel, HoltWintersModel, CrostonModel, ThetaModelWrapper,
    ProphetModel, LocalMLAutoregressive, calcular_score_torneio, limpar_falsos_zeros
)

class NexusForecaster:
    def __init__(self):
        self.forecast_horizon = 5 
        self.validation_size = 5  
        self.modelos_disponiveis = {
            'XGBoost_ML': LocalMLAutoregressive('xgb'),
            'LightGBM_ML': LocalMLAutoregressive('lgb'),
            'RandomForest_ML': LocalMLAutoregressive('rf'),
            'Prophet_Sazonal': ProphetModel(),
            'AutoARIMA': AutoArimaModel(),
            'HoltWinters': HoltWintersModel(),
            'Theta': ThetaModelWrapper(),
            'Croston_Intermitente': CrostonModel()
        }

    def _obter_dados_alpha(self, db, data_corte: str) -> pd.DataFrame:
        query = f"""
            SELECT 
                sku, DATE_TRUNC('month', data_pedido) AS mes_data, SUM(qt_pedido) AS volume,
                COALESCE(SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0), 0) AS pmv
            FROM fato_vendas
            WHERE data_pedido < '{data_corte}'
            GROUP BY sku, DATE_TRUNC('month', data_pedido)
            ORDER BY sku, mes_data;
        """
        df = pd.read_sql(query, db.bind)
        caminho_segmentos = os.path.join("app", "etl", "Segmentos.xlsx")
        if os.path.exists(caminho_segmentos):
            try:
                df_seg = pl.read_excel(caminho_segmentos)
                col_produto = [c for c in df_seg.columns if c.lower() == 'produto'][0]
                skus_validos = df_seg.with_columns([
                    pl.col(col_produto).cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                    pl.col("2026").cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
                ]).filter(
                    (pl.col("2026").str.contains(r"^[A-Z]{1,2}$")) | (pl.col("2026") == "LANÇAMENTO")
                ).get_column(col_produto).to_list()
                df = df[df['sku'].isin(skus_validos)]
            except Exception as e:
                pass
        return df

    def _torneio_modelos(self, df_sku: pd.DataFrame) -> tuple:
        df_limpo = limpar_falsos_zeros(df_sku, coluna_data='mes_data', coluna_volume='volume')
        num_folds, horizonte = 3, self.validation_size
        if len(df_limpo) < (horizonte + 3):
            return np.full(self.forecast_horizon, df_limpo['volume'].mean() if len(df_limpo) else 0.0), "Media_Simples", 50.0
        while len(df_limpo) < ((num_folds * horizonte) + 3) and num_folds > 1: num_folds -= 1

        scores_modelos = {nome: [] for nome in self.modelos_disponiveis.keys()}
        for fold in range(num_folds):
            corte_fim = len(df_limpo) - (fold * horizonte)
            corte_inicio = corte_fim - horizonte
            df_treino, df_validacao = df_limpo.iloc[:corte_inicio], df_limpo.iloc[corte_inicio:corte_fim]
            for nome, modelo in self.modelos_disponiveis.items():
                try:
                    modelo.fit(df_treino)
                    y_pred = modelo.predict(df_treino, horizon=horizonte)
                    if isinstance(y_pred, pd.Series): y_pred = y_pred.values
                    scores_modelos[nome].append(calcular_score_torneio(df_validacao['volume'].values, y_pred))
                except: scores_modelos[nome].append(float('inf'))

        melhor_modelo_nome, menor_score = None, float('inf')
        for nome, scores in scores_modelos.items():
            validos = [s for s in scores if s != float('inf')]
            if len(validos) == num_folds and (sum(validos) / len(validos)) < menor_score:
                menor_score, melhor_modelo_nome = sum(validos) / len(validos), nome
                
        if not melhor_modelo_nome: melhor_modelo_nome, menor_score = "Media_Simples", 100.0

        motor_campeao = self.modelos_disponiveis.get(melhor_modelo_nome)
        try:
            motor_campeao.fit(df_limpo)
            futuro = motor_campeao.predict(df_limpo, horizon=self.forecast_horizon)
            if isinstance(futuro, pd.Series): futuro = futuro.values
        except: futuro = np.full(self.forecast_horizon, df_limpo['volume'].mean())
            
        return futuro, melhor_modelo_nome, max(0.0, 100.0 - menor_score)

    def executar_arena(self, ciclo_alvo: str, log_callback=print):
        mes_ano = ciclo_alvo.split('/')
        data_inicio_proj = date(int(mes_ano[1]), int(mes_ano[0]), 1)
        db = SessionLocal()
        resultados_ia = []
        
        try:
            df_alpha = self._obter_dados_alpha(db, data_inicio_proj.strftime("%Y-%m-%d"))
            skus_ativos = df_alpha['sku'].unique()
            log_callback(f"   -> Prevendo {len(skus_ativos)} SKUs...")

            for sku in skus_ativos:
                df_sku = df_alpha[df_alpha['sku'] == sku].copy()
                previsao, campeao, acuracia = self._torneio_modelos(df_sku)
                
                # Trava de Outlier (15% acima do P95)
                hist_recente = df_sku[df_sku['volume'] > 0].tail(12)['volume']
                if len(hist_recente) >= 3:
                    previsao = np.clip(previsao, hist_recente.quantile(0.05), hist_recente.quantile(0.95) * 1.15)

                for i in range(self.forecast_horizon):
                    resultados_ia.append({
                        "ciclo_sop": ciclo_alvo, "mes_projetado": data_inicio_proj + relativedelta(months=i),
                        "sku": sku, "vol_ia_global": round(previsao[i], 2),
                        "modelo_vencedor": campeao, "acuracia_ia": round(acuracia, 2)
                    })
            return pl.DataFrame(resultados_ia)
        finally: db.close()