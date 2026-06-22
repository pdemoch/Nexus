from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
from datetime import date
from dateutil.relativedelta import relativedelta
from typing import List, Optional

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria e KPIs"])

def calcular_ciclo_baseline(mes_alvo: str) -> str:
    """
    Mapeamento de Congelamento S&OP (Regra de Negócio Linea):
    - 04/2026, 05/2026 e 06/2026 congelados no Baseline (04/2026)
    - 07/2026 em diante = Lag 2 (Mês Alvo - 2 Meses)
    """
    mes, ano = mes_alvo.split('/')
    dt_alvo = date(int(ano), int(mes), 1)
    dt_limite_baseline = date(2026, 6, 1)

    if dt_alvo <= dt_limite_baseline:
        return "04/2026"
    else:
        dt_lag2 = dt_alvo - relativedelta(months=2)
        return dt_lag2.strftime("%m/%Y")

@router.get("/auditoria-dinamica")
async def carregar_auditoria_cpfr(
    lente: str = "sellin",
    categoria: Optional[str] = "Todas",
    segmento: Optional[str] = "Todos",
    meses_horizonte: List[str] = Query(default=["04/2026", "05/2026", "06/2026", "07/2026", "08/2026"]),
    db: Session = Depends(get_db), 
    usuario: dict = Depends(get_current_user)
):
    try:
        resultados_meses = []

        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" # "2026-04"
            ciclo_congelado = calcular_ciclo_baseline(mes_str)

            clausula_filtro = ""
            params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "mes_str": mes_str}
            
            if categoria and categoria != "Todas":
                clausula_filtro += " AND p.categoria = :categoria"
                params_query["categoria"] = categoria
            if segmento and segmento != "Todos":
                clausula_filtro += " AND p.segmento = :segmento"
                params_query["segmento"] = segmento

            if lente == "sellin":
                # BLINDAGEM DE VOLUMES COM CTEs (Sem espaços nos nomes!)
                query = text(f"""
                    WITH Vendas AS (
                        SELECT sku, SUM(qt_pedido) AS vol_real
                        FROM fato_vendas
                        WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql
                        GROUP BY sku
                    ),
                    Planejamento AS (
                        SELECT sku, SUM(vol_ia) AS vol_ia_congelado, SUM(vol_final) AS vol_comercial_congelado
                        FROM fato_ibp_granular
                        WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql 
                          AND ciclo_sop = :ciclo_congelado
                        GROUP BY sku
                    )
                    SELECT 
                        p.sku, p.descricao, p.categoria, p.segmento,
                        :mes_str AS mes_ano,
                        COALESCE(v.vol_real, 0) AS vol_real,
                        COALESCE(i.vol_ia_congelado, 0) AS vol_ia_congelado,
                        COALESCE(i.vol_comercial_congelado, 0) AS vol_comercial_congelado
                    FROM dim_produtos p
                    LEFT JOIN Vendas v ON p.sku = v.sku
                    LEFT JOIN Planejamento i ON p.sku = i.sku
                    WHERE (v.vol_real > 0 OR i.vol_ia_congelado > 0 OR i.vol_comercial_congelado > 0)
                """)
            else:
                # BLINDAGEM DE VOLUMES MTRIX (Sem espaços nos nomes!)
                query = text(f"""
                    WITH Sellout AS (
                        SELECT sku, SUM(volume_sellout) AS vol_real
                        FROM fato_mtrix_historico_mensal
                        WHERE mes_ano = :mes_sql
                        GROUP BY sku
                    ),
                    Snapshot AS (
                        SELECT sku, MAX(previsao_sellout_m0) AS vol_ia_congelado, SUM(estoque_atual_caixas) AS estoque_canal, AVG(dias_cobertura) AS dias_cobertura
                        FROM fato_mtrix_snapshot
                        WHERE ciclo_sop = :ciclo_congelado
                        GROUP BY sku
                    )
                    SELECT 
                        p.sku, p.descricao, p.categoria, p.segmento,
                        :mes_str AS mes_ano,
                        COALESCE(h.vol_real, 0) AS vol_real,
                        COALESCE(s.vol_ia_congelado, 0) AS vol_ia_congelado,
                        COALESCE(s.estoque_canal, 0) AS estoque_canal,
                        COALESCE(s.dias_cobertura, 0) AS dias_cobertura
                    FROM dim_produtos p
                    LEFT JOIN Sellout h ON p.sku = h.sku
                    LEFT JOIN Snapshot s ON p.sku = s.sku
                    WHERE 1=1 {clausula_filtro}
                      AND (COALESCE(h.vol_real, 0) > 0 OR COALESCE(s.vol_ia_congelado, 0) > 0 OR COALESCE(s.estoque_canal, 0) > 0)
                """)

            df_mes = pd.read_sql(query, db.bind, params=params_query)
            if not df_mes.empty:
                resultados_meses.append(df_mes)

        if not resultados_meses:
            return {"kpis_globais": {}, "cronologia": [], "tabela_skus": [], "filtros_cascata": {"categorias": [], "segmentos": []}}

        df_completo = pd.concat(resultados_meses, ignore_index=True)

        if lente == "sellin":
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
                "vol_real": "sum", "vol_ia_congelado": "sum", "vol_comercial_congelado": "sum"
            }).reset_index()

            df_sku["acc_ia"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
            df_sku["acc_comercial"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
            df_sku["fva"] = df_sku["acc_comercial"] - df_sku["acc_ia"]
            df_sku["estoque_canal"] = 0
            df_sku["dias_cobertura"] = 0

        else:
            soma_real = df_completo["vol_real"].sum()
            soma_ia = df_completo["vol_ia_congelado"].sum()
            err_ia = df_completo.groupby("sku").apply(lambda x: np.abs(x["vol_real"].sum() - x["vol_ia_congelado"].sum())).sum()
            
            acc_ia = max(0.0, 1.0 - (err_ia / soma_real)) if soma_real > 0 else 1.0
            acc_com = acc_ia 
            bias_global = (soma_ia - soma_real) / soma_real if soma_real > 0 else 0.0

            cronologia = []
            for m in meses_horizonte:
                df_m = df_completo[df_completo["mes_ano"] == m]
                r_m = df_m["vol_real"].sum()
                ia_m = df_m["vol_ia_congelado"].sum()
                cronologia.append({"mes": m, "bias_ia": round(((ia_m - r_m) / r_m) * 100, 2) if r_m > 0 else 0, "bias_comercial": 0})

            df_sku = df_completo.groupby(["sku", "descricao", "categoria", "segmento"]).agg({
                "vol_real": "sum", "vol_ia_congelado": "sum", "estoque_canal": "last", "dias_cobertura": "mean"
            }).reset_index()
            
            df_sku["vol_comercial_congelado"] = df_sku["vol_ia_congelado"]
            df_sku["acc_ia"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
            df_sku["acc_comercial"] = df_sku["acc_ia"]
            df_sku["fva"] = 0.0

        return {
            "kpis_globais": {
                "acc_ia": round(acc_ia, 4), "acc_comercial": round(acc_com, 4),
                "fva": round(acc_com - acc_ia, 4), "bias_global": round(bias_global, 4),
                "cobertura_media_canal": int(df_completo["dias_cobertura"].mean()) if "dias_cobertura" in df_completo.columns else 0
            },
            "cronologia": cronologia,
            "tabela_skus": df_sku.to_dict(orient="records"),
            "filtros_cascata": {
                "categorias": list(df_completo["categoria"].unique()),
                "segmentos": list(df_completo["segmento"].unique())
            }
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))