import pandas as pd
import numpy as np
import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from app.core.database import engine 
from app.ml.models_library import (
    HoltModel, HoltWintersModel, ThetaModelWrapper, CrostonModel, MovingAverageModel, 
    ProphetModel, AutoArimaModel, calcular_acuracia, LocalMLAutoregressive, DeepLearningForecaster
)

class NexusForecaster:
    def __init__(self):
        self.forecast_horizon = 5
        self.cv_folds = 3 
        
        # Pesos Táticos Corporativos: Errar o longo prazo (M2 a M4) pune o algoritmo severamente.
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
        
        # Definir o ciclo S&OP imediatamente anterior para uso no Fallback Humano
        ciclo_anterior_dt = hoje - relativedelta(months=1)
        ciclo_anterior = ciclo_anterior_dt.strftime("%m/%Y")
        
        log_callback("📥 [ENGINE] Extraindo Catálogo Completo e Histórico de Vendas (INCLUINDO NPIs)...")
        
        # LEFT JOIN: Garante que absolutamente TODOS os SKUs entrem na Arena, mesmo com 0 vendas
        query_historico = text("""
            SELECT 
                p.sku AS produto,
                p.descricao,
                TO_CHAR(v.data_pedido, 'YYYY-MM') AS mes_ano,
                SUM(v.qt_pedido) AS total_qtpedido,
                CASE 
                    WHEN SUM(v.qt_pedido) = 0 THEN 0 
                    ELSE SUM(v.vl_pedido) / SUM(v.qt_pedido) 
                END AS pmv
            FROM dim_produtos p
            LEFT JOIN fato_vendas v ON p.sku = v.sku AND TO_CHAR(v.data_pedido, 'YYYY-MM') < :mes_atual
            GROUP BY 
                p.sku, p.descricao, TO_CHAR(v.data_pedido, 'YYYY-MM')
            ORDER BY produto, mes_ano;
        """)
        
        df = pd.read_sql(query_historico, engine, params={"mes_atual": mes_atual_str})
        
        if df.empty: 
            log_callback("❌ [ENGINE] Banco vazio. Abortando IA.")
            return pl.DataFrame()

        # =========================================================================
        # PROXY HUMANO: Resgate da inteligência do mês passado
        # =========================================================================
        log_callback(f"🧠 [ENGINE] Resgatando Proxy Humano (Ciclo {ciclo_anterior}) para proteção anti-falhas...")
        query_human = text("""
            SELECT sku, mes_projetado, SUM(vol_final) as vol_humano
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo_ant
            GROUP BY sku, mes_projetado
        """)
        df_human = pd.read_sql(query_human, engine, params={"ciclo_ant": ciclo_anterior})
        
        # Dicionário rápido: human_dict['PRD001'][data_alvo] = volume
        human_dict = {}
        for _, r in df_human.iterrows():
            sku = r['sku']
            # Garante formato Datetime.Date para bater com a chave na hora da injeção
            mes_proj = r['mes_projetado'] if isinstance(r['mes_projetado'], date) else r['mes_projetado'].date()
            if sku not in human_dict: 
                human_dict[sku] = {}
            human_dict[sku][mes_proj] = float(r['vol_humano'])

        log_callback("⚙️ [ENGINE] Formatando cronologia e tapando buracos de demanda nula...")
        df['mes_ano_dt'] = pd.to_datetime(df['mes_ano'])
        
        data_max = hoje.replace(day=1) - pd.DateOffset(months=1)
        datas_validas = df['mes_ano_dt'].dropna()
        data_min = datas_validas.min() if not datas_validas.empty else data_max - pd.DateOffset(months=12)
        idx_completo = pd.date_range(start=data_min, end=data_max, freq='MS')
        skus = df['produto'].unique()
        
        df_completo_list = []
        for sku in skus:
            df_sku = df[(df['produto'] == sku) & (df['mes_ano'].notna())].set_index('mes_ano_dt')
            df_reidx = df_sku.reindex(idx_completo)
            df_reidx['produto'] = sku
            df_reidx['total_qtpedido'] = df_reidx['total_qtpedido'].fillna(0)
            
            if 'pmv' in df_reidx.columns: 
                df_reidx['pmv'] = df_reidx['pmv'].replace(0, np.nan).ffill().bfill()
            df_reidx['pmv'] = df_reidx['pmv'].fillna(0)
            
            df_reidx = df_reidx.reset_index().rename(columns={'index': 'mes_ano_dt'})
            df_completo_list.append(df_reidx)
            
        df_global = pd.concat(df_completo_list)

        log_callback(f"⚔️ [ENGINE] Iniciando Arena Blindada para {len(skus)} SKUs...")
        resultados_forecast = []
        contador = 0
        data_inicio_previsao = data_max + pd.DateOffset(months=1)

        for sku in skus:
            df_sku = df_global[df_global['produto'] == sku].sort_values('mes_ano_dt')
            serie = pd.Series(df_sku['total_qtpedido'].values, index=df_sku['mes_ano_dt'])
            ultimo_pmv = df_sku.iloc[-1].get('pmv', 0)
            
            tamanho_serie = len(serie)
            avaliacoes_cv = {nome: [] for nome in self.especialistas.keys()}
            
            sucesso_ml = False
            previsao_final = []
            melhor_modelo_nome = "Proxy_Humano_Herdado"
            maior_acuracia_media = 100.0

            # REGRA MESTRA: Tenta aplicar o ML se houver o MÍNIMO de histórico de vendas
            if serie.sum() > 0:
                folds_aplicaveis = min(self.cv_folds, max(1, tamanho_serie - self.forecast_horizon - 2))
                
                # SE TIVER DADOS, TENTA CROSS-VALIDATION
                if folds_aplicaveis >= 1:
                    for fold in range(folds_aplicaveis):
                        corte_teste = self.forecast_horizon + fold
                        treino_cv = serie.iloc[:-corte_teste]
                        teste_real_cv = serie.iloc[-corte_teste : -corte_teste + self.forecast_horizon] if fold > 0 else serie.iloc[-corte_teste:]
                        
                        if len(treino_cv) < 3: continue

                        # Batalha Universal
                        for nome, modelo in self.especialistas.items():
                            try:
                                preds_cv = modelo.fit_predict(treino_cv, self.forecast_horizon)
                                acc_cv = calcular_acuracia(teste_real_cv.values, preds_cv, pesos=self.pesos_taticos)
                                avaliacoes_cv[nome].append(acc_cv)
                            except Exception:
                                # BLINDAGEM: Ignora este modelo em caso de erro matemático neste fold
                                pass 
                else:
                    # Poucos dados para Viagem no Tempo: Força todos a tentarem direto
                    for nome in self.especialistas.keys():
                        avaliacoes_cv[nome] = [1.0]

                # CONSOLIDANDO O RANKING DA ARENA
                ranking = []
                for nome, acc_lista in avaliacoes_cv.items():
                    if acc_lista:
                        ranking.append((nome, np.mean(acc_lista)))
                
                ranking.sort(key=lambda item: item[1], reverse=True)

                # BATALHA DE SOBREVIVÊNCIA: Tenta prever usando o melhor. Se o campeão falhar, tenta o próximo.
                for nome_modelo, acc_media in ranking:
                    try:
                        modelo_candidato = self.especialistas[nome_modelo]
                        projecao_tentativa = modelo_candidato.fit_predict(serie, self.forecast_horizon)
                        
                        if len(projecao_tentativa) == self.forecast_horizon:
                            previsao_final = projecao_tentativa
                            melhor_modelo_nome = nome_modelo
                            maior_acuracia_media = acc_media
                            sucesso_ml = True
                            break # Encontramos um modelo estatístico que rodou sem erros! Saímos do loop.
                    except Exception:
                        continue # Modelo gerou erro. Apenas ignora e tenta o próximo.

            # =========================================================================
            # INJEÇÃO DA VERDADE (ML OU PROXY HUMANO BLINDADO COM FORWARD FILL)
            # =========================================================================
            for i in range(self.forecast_horizon):
                data_proj = (data_inicio_previsao + pd.DateOffset(months=i)).to_pydatetime().date()
                
                if sucesso_ml:
                    # O ML conseguiu prever sem falhas
                    vol_proj = max(0, previsao_final[i])
                else:
                    # PROXY HUMANO BLINDADO
                    dict_sku = human_dict.get(sku, {})
                    vol_proj = dict_sku.get(data_proj)
                    
                    # Se o mês novo (ex: M4) não existia no ciclo passado, espelha o último mês conhecido!
                    if vol_proj is None:
                        if dict_sku:
                            ultima_data = max(dict_sku.keys())
                            vol_proj = dict_sku[ultima_data]
                        else:
                            vol_proj = 0.0
                
                resultados_forecast.append({
                    "ciclo_sop": ciclo_atual, 
                    "produto": sku, 
                    "mes_projetado": data_proj,
                    "vol_ia_global": round(vol_proj, 2), 
                    "pmv_aplicado": round(ultimo_pmv, 2),
                    "modelo_vencedor": melhor_modelo_nome, 
                    "acuracia": round(maior_acuracia_media, 4)  
                })
            
            contador += 1
            if contador % 50 == 0: log_callback(f"   ⏳ Processados {contador}/{len(skus)} SKUs...")

        df_resultados = pl.DataFrame(resultados_forecast)
        log_callback(f"✅ [ENGINE] {df_resultados.height} projeções geradas com sucesso (IA Robusta + Proxy Humano)!")
        return df_resultados