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

        # 3. Consolidação FULL OUTER JOIN (Garante que quem tem venda mas não tem projeção, e vice-versa, não escape do cálculo)
        df = pd.merge(df_prev, df_real, on=['sku', 'mes'], how='outer')

        # Limpeza severa: Tratar nulos, converter tipos e IMPEDIR previsões ou vendas negativas
        df['vol_real'] = pd.to_numeric(df['vol_real'], errors='coerce').fillna(0).clip(lower=0)
        df['vol_ia'] = pd.to_numeric(df['vol_ia'], errors='coerce').fillna(0).clip(lower=0)
        df['vol_comercial'] = pd.to_numeric(df['vol_comercial'], errors='coerce').fillna(0).clip(lower=0)

        # 4. Motor Matemático: Padrão APICS de IBP
        def calcular_metricas(df_group):
            soma_real = float(df_group['vol_real'].sum())
            soma_ia = float(df_group['vol_ia'].sum())
            soma_com = float(df_group['vol_comercial'].sum())
            
            # WMAPE (Volume Absoluto de Erro / Venda Real)
            erro_abs_ia = float(abs(df_group['vol_real'] - df_group['vol_ia']).sum())
            erro_abs_com = float(abs(df_group['vol_real'] - df_group['vol_comercial']).sum())
            
            wmape_ia = (erro_abs_ia / soma_real) if soma_real > 0 else (1.0 if soma_ia > 0 else 0.0)
            wmape_com = (erro_abs_com / soma_real) if soma_real > 0 else (1.0 if soma_com > 0 else 0.0)
            
            # Acurácia: Piso cravado em 0% (evita percentuais negativos de alucinação)
            acc_ia = max(0.0, 1.0 - wmape_ia)
            acc_comercial = max(0.0, 1.0 - wmape_com)
            
            # BIAS: Tendência de Superestimação (+) ou Subestimação (-)
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

        # --- A. MACRO KPIS ---
        macro_kpis = calcular_metricas(df).to_dict()

        # --- B. GRÁFICO (Evolução Temporal) ---
        df_grafico = df.groupby('mes').apply(calcular_metricas).reset_index()
        df_grafico = df_grafico.sort_values(by='mes')
        
        meses_com_venda = df[df['vol_real'] > 0]['mes'].unique()
        ultimo_mes_real = max(meses_com_venda) if len(meses_com_venda) > 0 else '2026-03'

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
        
        # Filtro de Limpeza: Remove itens mortos que não tiveram venda real E não tiveram projeção (Lixo do OUTER JOIN)
        df_tabela = df_tabela[(df_tabela['vol_real'] > 0) | (df_tabela['vol_comercial'] > 0) | (df_tabela['vol_ia'] > 0)]
        
        # Ordenar pelos piores GAPs absolutos do consenso S&OP
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
        import traceback
        traceback.print_exc()
        raise HTTPException(500, repr(e))