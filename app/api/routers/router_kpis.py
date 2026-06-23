from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from typing import List, Optional

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria e KPIs"])

def calcular_ciclo_lag2(mes_alvo: str) -> str:
    """
    Regra de Negócio CPFR:
    Aplica-se estritamente a regra do Lag 2 (Mês Alvo - 2 Meses).
    Exemplo: Mês de Horizonte 06/2026 foi planejado e congelado no Ciclo 04/2026.
    """
    mes, ano = mes_alvo.split('/')
    dt_alvo = date(int(ano), int(mes), 1)
    dt_lag2 = dt_alvo - relativedelta(months=2)
    return dt_lag2.strftime("%m/%Y")

# ==============================================================================
# ENDPOINT DE FILTROS DINÂMICOS (ALIMENTA O MENU EXCEL DO FRONTEND)
# ==============================================================================
@router.get("/filtros-auditoria")
async def carregar_filtros_auditoria(lente: str = "sellin", db: Session = Depends(get_db)):
    try:
        # Extração em cascata para preencher os seletores de Categoria e Segmento
        query_f = text("SELECT DISTINCT categoria, segmento FROM dim_produtos WHERE categoria IS NOT NULL")
        df_f = pd.read_sql(query_f, db.bind)
        
        if lente == "sellout":
            # MTRIX: Busca todos os meses históricos disponíveis reais gravados no PostgreSQL
            q_meses = text("SELECT DISTINCT mes_ano FROM fato_mtrix_historico_mensal ORDER BY mes_ano DESC")
            df_m = pd.read_sql(q_meses, db.bind)
            if not df_m.empty:
                meses = [f"{str(m).split('-')[1]}/{str(m).split('-')[0]}" for m in df_m["mes_ano"].unique() if m]
            else:
                # REVOLUÇÃO DINÂMICA (FALLBACK): Se o banco estiver vazio, gera os últimos 5 meses retroativos
                dt_atual = datetime.now()
                meses = [(dt_atual - relativedelta(months=i)).strftime("%m/%Y") for i in range(5)]
        else:
            # S&OP (Fábrica): Busca os horizontes futuros simulados ativos
            q_meses = text("""
                SELECT DISTINCT TO_CHAR(mes_projetado, 'MM/YYYY') as mes_ano, TO_CHAR(mes_projetado, 'YYYY-MM') as sort_key 
                FROM fato_ibp_granular WHERE mes_projetado >= '2026-06-01' ORDER BY sort_key ASC
            """)
            df_m = pd.read_sql(q_meses, db.bind)
            if not df_m.empty:
                meses = list(df_m["mes_ano"].unique())
            else:
                # REVOLUÇÃO DINÂMICA (FALLBACK): Se o banco estiver vazio, gera os próximos 5 meses táticos
                dt_atual = datetime.now()
                meses = [(dt_atual + relativedelta(months=i)).strftime("%m/%Y") for i in range(5)]

        return {
            "categorias": list(df_f["categoria"].dropna().unique()),
            "segmentos": list(df_f["segmento"].dropna().unique()),
            "meses_disponiveis": meses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# MOTOR CENTRAL DE ACURÁCIA S&OP VS MTRIX PAREADO
# ==============================================================================
@router.get("/auditoria-dinamica")
async def carregar_auditoria_cpfr(
    lente: str = "sellin",
    categoria: Optional[str] = "Todas",
    segmento: Optional[str] = "Todos",
    meses_horizonte: List[str] = Query(default=["06/2026"]),
    db: Session = Depends(get_db), 
    usuario: dict = Depends(get_current_user)
):
    try:
        resultados_meses = []
        ciclo_ativo_ref = "06/2026" 

        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = calcular_ciclo_lag2(mes_str)

            clausula_filtro = ""
            params_query = {
                "mes_sql": mes_sql, 
                "ciclo_congelado": ciclo_congelado, 
                "mes_str": mes_str, 
                "ciclo_ativo_ref": ciclo_ativo_ref
            }
            
            if categoria and categoria != "Todas":
                clausula_filtro += " AND p.categoria = :categoria"
                params_query["categoria"] = categoria
            if segmento and segmento != "Todos":
                clausula_filtro += " AND p.segmento = :segmento"
                params_query["segmento"] = segmento

            if lente == "sellin":
                # LENTE S&OP FÁBRICA: Faturamento Global da Planta vs Projeção Tática
                query = text(f"""
                    WITH Vendas_Agrupadas AS (
                        SELECT sku, SUM(qt_pedido) AS vol_real
                        FROM fato_vendas WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql GROUP BY sku
                    ),
                    Planejamento_Agrupado AS (
                        SELECT sku, SUM(vol_ia) AS vol_ia_congelado, SUM(vol_final) AS vol_comercial_congelado
                        FROM fato_ibp_granular WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql AND ciclo_sop = :ciclo_congelado GROUP BY sku
                    )
                    SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano,
                        COALESCE(v.vol_real, 0) AS vol_real, COALESCE(i.vol_ia_congelado, 0) AS vol_ia_congelado,
                        COALESCE(i.vol_comercial_congelado, 0) AS vol_comercial_congelado, 0 AS estoque_canal, 0 AS dias_cobertura
                    FROM dim_produtos p
                    LEFT JOIN Vendas_Agrupadas v ON p.sku = v.sku
                    LEFT JOIN Planejamento_Agrupado i ON p.sku = i.sku
                    WHERE 1=1 {clausula_filtro} AND (COALESCE(v.vol_real, 0) > 0 OR COALESCE(i.vol_ia_congelado, 0) > 0 OR COALESCE(i.vol_comercial_congelado, 0) > 0)
                """)
            else:
                # LENTE MTRIX: Faturamento PAREADO (Inner Join) estritamente pelos CGCs integrados
                query = text(f"""
                    WITH Distribuidores_Integrados AS (
                        SELECT DISTINCT cgc 
                        FROM fato_mtrix_historico_mensal 
                        WHERE ciclo_sop = :ciclo_ativo_ref AND cgc IS NOT NULL
                    ),
                    Faturamento_Fabrica AS (
                        SELECT v.sku, SUM(v.qt_pedido) AS vol_sellin_real
                        FROM fato_vendas v
                        INNER JOIN Distribuidores_Integrados d ON v.cgc = d.cgc
                        WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql
                        GROUP BY v.sku
                    ),
                    Sellout_Canal AS (
                        SELECT sku, SUM(volume_sellout) AS vol_sellout_real
                        FROM fato_mtrix_historico_mensal WHERE mes_ano = :mes_sql AND ciclo_sop = :ciclo_ativo_ref GROUP BY sku
                    ),
                    Estoque_Ultimo_Snapshot AS (
                        -- NÃO ADITIVO: Coleta rigorosamente a última fotografia de estoque do mês analisado
                        SELECT DISTINCT ON (sku) sku, estoque_atual_caixas, dias_cobertura
                        FROM fato_mtrix_snapshot WHERE ciclo_sop = :ciclo_congelado ORDER BY sku, id DESC
                    )
                    SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano,
                        COALESCE(f.vol_sellin_real, 0) AS vol_real, COALESCE(s.vol_sellout_real, 0) AS vol_ia_congelado,
                        COALESCE(e.estoque_atual_caixas, 0) AS estoque_canal, COALESCE(e.dias_cobertura, 0) AS dias_cobertura,
                        COALESCE(s.vol_sellout_real, 0) AS vol_comercial_congelado
                    FROM dim_produtos p
                    LEFT JOIN Faturamento_Fabrica f ON p.sku = f.sku
                    LEFT JOIN Sellout_Canal s ON p.sku = s.sku
                    LEFT JOIN Estoque_Ultimo_Snapshot e ON p.sku = e.sku
                    WHERE 1=1 {clausula_filtro} AND (COALESCE(f.vol_sellin_real, 0) > 0 OR COALESCE(s.vol_sellout_real, 0) > 0 OR COALESCE(e.estoque_atual_caixas, 0) > 0)
                """)

            df_mes = pd.read_sql(query, db.bind, params=params_query)
            if not df_mes.empty: 
                resultados_meses.append(df_mes)

        if not resultados_meses:
            return {"kpis_globais": {}, "cronologia": [], "tabela_skus": []}

        df_completo = pd.concat(resultados_meses, ignore_index=True)

        soma_real = df_completo["vol_real"].sum()
        soma_ia = df_completo["vol_ia_congelado"].sum()
        soma_com = df_completo["vol_comercial_congelado"].sum()
        
        err_ia = df_completo.groupby("sku").apply(lambda x: np.abs(x["vol_real"].sum() - x["vol_ia_congelado"].sum())).sum()
        err_com = df_completo.groupby("sku").apply(lambda x: np.abs(x["vol_real"].sum() - x["vol_comercial_congelado"].sum())).sum()
        
        acc_ia = max(0.0, 1.0 - (err_ia / soma_real)) if soma_real > 0 else 1.0
        acc_com = max(0.0, 1.0 - (err_com / soma_real)) if soma_real > 0 else 1.0
        bias_global = (soma_com - soma_real) / soma_real if soma_real > 0 else 0.0

        cronologia = []
        for m in meses_horizonte:
            df_m = df_completo[df_completo["mes_ano"] == m]
            r_m = df_m["vol_real"].sum()
            ia_m = df_m["vol_ia_congelado"].sum()
            co_m = df_m["vol_comercial_congelado"].sum()
            cronologia.append({
                "mes": m,
                "bias_ia": round(((ia_m - r_m) / r_m) * 100, 2) if r_m > 0 else 0,
                "bias_comercial": round(((co_m - r_m) / r_m) * 100, 2) if r_m > 0 else 0
            })

        df_sku = df_completo.groupby(["sku", "descricao", "categoria", "segmento"]).agg({
            "vol_real": "sum", "vol_ia_congelado": "sum", "vol_comercial_congelado": "sum",
            "estoque_canal": "last", "dias_cobertura": "last"
        }).reset_index()

        df_sku["acc_ia"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
        df_sku["acc_comercial"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
        df_sku["fva"] = df_sku["acc_comercial"] - df_sku["acc_ia"]

        return {
            "kpis_globais": {
                "acc_ia": round(acc_ia, 4), "acc_comercial": round(acc_com, 4),
                "fva": round(acc_com - acc_ia, 4) if lente == "sellin" else 0.0, 
                "bias_global": round(bias_global, 4),
                "cobertura_media_canal": int(df_completo[df_completo["dias_cobertura"] < 999]["dias_cobertura"].mean()) if lente == "sellout" and not df_completo.empty else 0
            },
            "cronologia": cronologia,
            "tabela_skus": df_sku.to_dict(orient="records")
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))