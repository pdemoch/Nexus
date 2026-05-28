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
        # 1. Busca Vendas Reais 
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

        # 2. Rolling Forecast: Pegando a última fotografia projetada para cada mês
        query_prev = text("""
            WITH ciclos_ranqueados AS (
                SELECT 
                    sku, 
                    TO_CHAR(mes_projetado, 'YYYY-MM') AS mes, 
                    ciclo_sop,
                    ROW_NUMBER() OVER(
                        PARTITION BY sku, TO_CHAR(mes_projetado, 'YYYY-MM')
                        ORDER BY TO_DATE(ciclo_sop, 'MM/YYYY') DESC
                    ) as rn
                FROM fato_ibp_granular
                WHERE mes_projetado >= '2026-04-01'
                GROUP BY sku, TO_CHAR(mes_projetado, 'YYYY-MM'), ciclo_sop
            )
            SELECT 
                g.sku, 
                TO_CHAR(g.mes_projetado, 'YYYY-MM') AS mes, 
                SUM(g.vol_ia) AS vol_ia, 
                SUM(g.vol_final) AS vol_comercial
            FROM fato_ibp_granular g
            JOIN ciclos_ranqueados c
              ON g.sku = c.sku 
             AND TO_CHAR(g.mes_projetado, 'YYYY-MM') = c.mes
             AND g.ciclo_sop = c.ciclo_sop
            WHERE c.rn = 1
            GROUP BY g.sku, TO_CHAR(g.mes_projetado, 'YYYY-MM')
        """)
        df_prev = pd.read_sql(query_prev, db.bind)

        if df_prev.empty and df_real.empty:
            return {"status": "success", "dados": {"macro": {}, "grafico": [], "tabela": []}}

        # 3. Consolidação FULL OUTER JOIN
        df = pd.merge(df_prev, df_real, on=['sku', 'mes'], how='outer')

        # Limpeza severa
        df['vol_real'] = pd.to_numeric(df['vol_real'], errors='coerce').fillna(0).clip(lower=0)
        df['vol_ia'] = pd.to_numeric(df['vol_ia'], errors='coerce').fillna(0).clip(lower=0)
        df['vol_comercial'] = pd.to_numeric(df['vol_comercial'], errors='coerce').fillna(0).clip(lower=0)

        # 4. Descobre o Teto Temporal (Último mês que possui venda real no Brasil)
        meses_com_venda = df[df['vol_real'] > 0]['mes'].unique()
        ultimo_mes_real = max(meses_com_venda) if len(meses_com_venda) > 0 else '2026-03'

        # CRÍTICO: Separa o dataframe! O cálculo de auditoria SÓ pode olhar para o que já aconteceu.
        df_historico = df[df['mes'] <= ultimo_mes_real].copy()

        def calcular_metricas(df_group):
            soma_real = float(df_group['vol_real'].sum())
            soma_ia = float(df_group['vol_ia'].sum())
            soma_com = float(df_group['vol_comercial'].sum())
            
            # WMAPE
            erro_abs_ia = float(abs(df_group['vol_real'] - df_group['vol_ia']).sum())
            erro_abs_com = float(abs(df_group['vol_real'] - df_group['vol_comercial']).sum())
            
            wmape_ia = (erro_abs_ia / soma_real) if soma_real > 0 else (1.0 if soma_ia > 0 else 0.0)
            wmape_com = (erro_abs_com / soma_real) if soma_real > 0 else (1.0 if soma_com > 0 else 0.0)
            
            # Acurácia
            acc_ia = max(0.0, 1.0 - wmape_ia)
            acc_comercial = max(0.0, 1.0 - wmape_com)
            
            # BIAS
            bias_ia = ((soma_ia - soma_real) / soma_real) if soma_real > 0 else (1.0 if soma_ia > 0 else 0.0)
            bias_com = ((soma_com - soma_real) / soma_real) if soma_real > 0 else (1.0 if soma_com > 0 else 0.0)

            return pd.Series({
                "vol_real": int(soma_real),
                "vol_ia": int(soma_ia),
                "vol_comercial": int(soma_com),
                "acc_ia": float(acc_ia),
                "acc_comercial": float(acc_comercial),
                "bias_ia": float(bias_ia),
                "bias_comercial": float(bias_com),
                "gap_absoluto_ia": int(abs(soma_real - soma_ia)),
                "gap_absoluto_com": int(abs(soma_real - soma_com))
            })

        # --- A. MACRO KPIS (Calculado EXCLUSIVAMENTE sobre o passado) ---
        if not df_historico.empty:
            macro_kpis = calcular_metricas(df_historico).to_dict()
        else:
            # Caso o projeto S&OP esteja no mês 0 e não haja passado para comparar
            macro_kpis = {
                "vol_real": 0, "vol_ia": 0, "vol_comercial": 0,
                "acc_ia": 0, "acc_comercial": 0, "bias_ia": 0, "bias_comercial": 0
            }

        # --- B. GRÁFICO (Evolução Temporal: Passado + Futuro) ---
        df_grafico = df.groupby('mes').apply(calcular_metricas).reset_index()
        df_grafico = df_grafico.sort_values(by='mes')
        
        grafico_dados = []
        for _, row in df_grafico.iterrows():
            mes_dt = pd.to_datetime(row['mes'])
            mes_str = mes_dt.strftime("%b/%y").capitalize()
            
            is_futuro = row['mes'] > ultimo_mes_real

            grafico_dados.append({
                "name": mes_str,
                "Realizado": None if is_futuro else int(row['vol_real']),
                "Projecao_IA": int(row['vol_ia']),
                "Proposta_Comercial": int(row['vol_comercial']),
                # Zera as métricas analíticas no tooltip se for um mês do futuro
                "acc_ia": None if is_futuro else row['acc_ia'],
                "acc_comercial": None if is_futuro else row['acc_comercial'],
                "bias_ia": None if is_futuro else row['bias_ia'],
                "bias_comercial": None if is_futuro else row['bias_comercial']
            })

        # --- C. MATRIZ DE OFENSORES (Por SKU - Exclusivamente sobre o passado) ---
        if not df_historico.empty:
            query_prods = text("SELECT sku, descricao FROM dim_produtos")
            df_prods = pd.read_sql(query_prods, db.bind)
            
            # Repare: o merge agora é feito no df_historico, não no df geral
            df_com_nomes = pd.merge(df_historico, df_prods, on='sku', how='left')
            
            df_tabela = df_com_nomes.groupby(['sku', 'descricao']).apply(calcular_metricas).reset_index()
            df_tabela = df_tabela[(df_tabela['vol_real'] > 0) | (df_tabela['vol_comercial'] > 0) | (df_tabela['vol_ia'] > 0)]
            df_tabela = df_tabela.sort_values(by='gap_absoluto_com', ascending=False).head(50)
            
            tabela_dados = df_tabela.to_dict(orient='records')
        else:
            tabela_dados = []

        return {
            "status": "success",
            "dados": {
                "macro": macro_kpis,
                "grafico": grafico_dados,
                "tabela": tabela_dados
            }
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, repr(e))