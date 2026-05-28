from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria e KPIs"])

@router.get("/auditoria")
async def carregar_auditoria(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        # 1. Busca Vendas Reais (Ponto de partida do projeto S&OP)
        query_real = text("""
            SELECT 
                sku, 
                TO_CHAR(data_pedido, 'YYYY-MM') AS mes, 
                SUM(qt_pedido) AS vol_real,
                SUM(vl_pedido) AS rec_real
            FROM fato_vendas
            WHERE data_pedido >= '2026-04-01'
            GROUP BY sku, TO_CHAR(data_pedido, 'YYYY-MM')
        """)
        df_real = pd.read_sql(query_real, db.bind)

        # 2. Rolling Forecast: Busca a versão mais atualizada da previsão de cada mês
        # O DISTINCT ON garante que se houver previsões em ciclos múltiplos para o mesmo mês, ele pegará a mais recente.
        query_prev = text("""
            SELECT DISTINCT ON (sku, TO_CHAR(mes_projetado, 'YYYY-MM'))
                sku, 
                TO_CHAR(mes_projetado, 'YYYY-MM') AS mes, 
                vol_ia, 
                vol_final AS vol_comercial, 
                pmv_aplicado, 
                ciclo_sop
            FROM fato_ibp_granular
            WHERE mes_projetado >= '2026-04-01'
            ORDER BY sku, TO_CHAR(mes_projetado, 'YYYY-MM'), ciclo_sop DESC
        """)
        df_prev = pd.read_sql(query_prev, db.bind)

        if df_prev.empty:
            return {"status": "success", "dados": {"macro": {}, "grafico": [], "tabela": []}}

        # 3. Consolidação dos Mundos (Previsão vs Realizado)
        df = pd.merge(df_prev, df_real, on=['sku', 'mes'], how='left')
        df['vol_real'] = df['vol_real'].fillna(0)
        df['rec_real'] = df['rec_real'].fillna(0)

        # 4. Motor Matemático: WMAPE e BIAS
        def calcular_metricas(df_group):
            soma_real = df_group['vol_real'].sum()
            soma_ia = df_group['vol_ia'].sum()
            soma_com = df_group['vol_comercial'].sum()
            
            # WMAPE = Soma dos Erros Absolutos / Soma do Real
            erro_abs_ia = abs(df_group['vol_real'] - df_group['vol_ia']).sum()
            erro_abs_com = abs(df_group['vol_real'] - df_group['vol_comercial']).sum()
            
            wmape_ia = (erro_abs_ia / soma_real) if soma_real > 0 else 0
            wmape_com = (erro_abs_com / soma_real) if soma_real > 0 else 0
            
            # BIAS = (Previsão - Real) / Real
            bias_ia = ((soma_ia - soma_real) / soma_real) if soma_real > 0 else 0
            bias_com = ((soma_com - soma_real) / soma_real) if soma_real > 0 else 0

            return pd.Series({
                "vol_real": int(soma_real),
                "vol_ia": int(soma_ia),
                "vol_comercial": int(soma_com),
                "acc_ia": round(max(0, 1 - wmape_ia), 4),
                "acc_comercial": round(max(0, 1 - wmape_com), 4),
                "bias_ia": round(bias_ia, 4),
                "bias_comercial": round(bias_com, 4),
                "gap_absoluto_ia": int(abs(soma_real - soma_ia)),
                "gap_absoluto_com": int(abs(soma_real - soma_com))
            })

        # --- A. MACRO KPIS ---
        macro_kpis = calcular_metricas(df).to_dict()

        # --- B. GRÁFICO (Evolução Temporal) ---
        df_grafico = df.groupby('mes').apply(calcular_metricas).reset_index()
        
        # Descobre qual o último mês com venda real para cortar a linha preta no futuro
        meses_com_venda = df[df['vol_real'] > 0]['mes'].unique()
        ultimo_mes_real = max(meses_com_venda) if len(meses_com_venda) > 0 else '2026-03'

        grafico_dados = []
        for _, row in df_grafico.iterrows():
            mes_dt = pd.to_datetime(row['mes'])
            mes_str = mes_dt.strftime("%b/%y").capitalize()
            
            # Máscara para não afundar a linha do Realizado para zero em meses futuros
            is_futuro = row['mes'] > ultimo_mes_real

            grafico_dados.append({
                "name": mes_str,
                "Realizado": None if is_futuro else row['vol_real'],
                "Projecao_IA": row['vol_ia'],
                "Proposta_Comercial": row['vol_comercial'],
                "acc_ia": row['acc_ia'],
                "acc_comercial": row['acc_comercial'],
                "bias_ia": row['bias_ia'],
                "bias_comercial": row['bias_comercial']
            })

        # --- C. MATRIZ DE OFENSORES (Por SKU) ---
        query_prods = text("SELECT sku, descricao FROM dim_produtos")
        df_prods = pd.read_sql(query_prods, db.bind)
        df_com_nomes = pd.merge(df, df_prods, on='sku', how='left')
        
        df_tabela = df_com_nomes.groupby(['sku', 'descricao']).apply(calcular_metricas).reset_index()
        
        # Ordenar pelos piores GAPs comerciais absolutos
        df_tabela = df_tabela.sort_values(by='gap_absoluto_com', ascending=False).head(50)
        
        tabela_dados = df_tabela.to_dict(orient='records')

        return {
            "status": "success",
            "dados": {
                "macro": macro_kpis,
                "grafico": grafico_dados,
                "tabela": tabela_dados
            }
        }

    except Exception as e:
        raise HTTPException(500, repr(e))