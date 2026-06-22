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
from app.core.state import AppState

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria e KPIs"])

def calcular_ciclo_lag2(mes_alvo: str) -> str:
    """Retrocede 2 meses a partir do horizonte para buscar a previsão congelada."""
    mes, ano = mes_alvo.split('/')
    dt_alvo = date(int(ano), int(mes), 1)
    dt_lag2 = dt_alvo - relativedelta(months=2)
    return dt_lag2.strftime("%m/%Y")

@router.get("/auditoria-dinamica")
async def carregar_auditoria_cpfr(
    lente: str = "sellin", # "sellin" ou "sellout"
    categoria: Optional[str] = "Todas",
    segmento: Optional[str] = "Todos",
    db: Session = Depends(get_db), 
    usuario: dict = Depends(get_current_user)
):
    try:
        # Definição dos horizontes de análise contínuos a partir do congelamento (07/2026 em diante)
        meses_horizonte = ["07/2026", "08/2026", "09/2026", "10/2026"]
        resultados_meses = []

        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" # Formato YYYY-MM
            ciclo_congelado = calcular_ciclo_lag2(mes_str)

            # Filtros dinâmicos inseridos cirurgicamente na query
            clausula_filtro = ""
            params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "mes_str": mes_str}
            
            if categoria and categoria != "Todas":
                clausula_filtro += " AND p.categoria = :categoria"
                params_query["categoria"] = categoria
            if segmento and segmento != "Todos":
                clausula_filtro += " AND p.segmento = :segmento"
                params_query["segmento"] = segmento

            if lente == "sellin":
                # LENTE FÁBRICA: Faturamento Gobi vs Previsões Congeladas M-2
                query = text(f"""
                    SELECT 
                        v.sku,
                        p.descricao,
                        p.categoria,
                        p.segmento,
                        :mes_str AS mes_ano,
                        COALESCE(SUM(v.qt_pedido), 0) AS vol_real,
                        COALESCE(SUM(i.vol_ia), 0) AS vol_ia_congelado,
                        COALESCE(SUM(i.vol_bottomup), 0) AS vol_comercial_congelado
                    FROM dim_produtos p
                    LEFT JOIN fato_vendas v ON p.sku = v.sku AND TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql
                    LEFT JOIN fato_ibp_granular i ON p.sku = i.sku AND TO_CHAR(i.mes_projetado, 'YYYY-MM') = :mes_sql AND i.ciclo_sop = :ciclo_congelado
                    WHERE 1=1 {clausula_filtro}
                    GROUP BY v.sku, p.descricao, p.categoria, p.segmento
                    HAVING SUM(v.qt_pedido) > 0 OR SUM(i.vol_ia) > 0 OR SUM(i.vol_bottomup) > 0
                """)
            else:
                # LENTE CANAL (SELL-OUT): Transacional Parquet Histórico vs Snapshot Congelada M-2
                query = text(f"""
                    SELECT 
                        h.sku,
                        p.descricao,
                        p.categoria,
                        p.segmento,
                        :mes_str AS mes_ano,
                        COALESCE(SUM(h.volume_sellout), 0) AS vol_real,
                        COALESCE(MAX(s.previsao_sellout_m0), 0) AS vol_ia_congelado,
                        COALESCE(SUM(s.estoque_atual_caixas), 0) AS estoque_canal,
                        COALESCE(AVG(s.dias_cobertura), 0) AS dias_cobertura
                    FROM dim_produtos p
                    LEFT JOIN fato_mtrix_historico_mensal h ON p.sku = h.sku AND h.mes_ano = :mes_sql
                    LEFT JOIN fato_mtrix_snapshot s ON p.sku = s.sku AND s.ciclo_sop = :ciclo_congelado
                    WHERE 1=1 {clausula_filtro}
                    GROUP BY h.sku, p.descricao, p.categoria, p.segmento
                    HAVING SUM(h.volume_sellout) > 0 OR MAX(s.previsao_sellout_m0) > 0
                """)

            df_mes = pd.read_sql(query, db.bind, params=params_query)
            if not df_mes.empty:
                resultados_meses.append(df_mes)

        if not resultados_meses:
            return {"kpis_globais": {}, "cronologia": [], "tabela_skus": []}

        # Une a série temporal completa
        df_completo = pd.concat(resultados_meses, ignore_index=True)

        # 2. SEGREGAÇÃO FINANCEIRA E AGREGADOS DE AUDITORIA
        if lente == "sellin":
            soma_real = df_completo["vol_real"].sum()
            soma_ia = df_completo["vol_ia_congelado"].sum()
            soma_com = df_completo["vol_comercial_congelado"].sum()
            
            err_ia = df_completo.groupby("sku").apply(lambda x: np.abs(x["vol_real"].sum() - x["vol_ia_congelado"].sum())).sum()
            err_com = df_completo.groupby("sku").apply(lambda x: np.abs(x["vol_real"].sum() - x["vol_comercial_congelado"].sum())).sum()
            
            acc_ia = max(0.0, 1.0 - (err_ia / soma_real)) if soma_real > 0 else 1.0
            acc_com = max(0.0, 1.0 - (err_com / soma_real)) if soma_real > 0 else 1.0
            bias_global = (soma_com - soma_real) / soma_real if soma_real > 0 else 0.0

            # Cronologia para o gráfico Tracking Signal
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

            # Dados atomizados por SKU compilando o acumulado total do horizonte
            df_sku = df_completo.groupby(["sku", "descricao", "categoria", "segmento"]).agg({
                "vol_real": "sum",
                "vol_ia_congelado": "sum",
                "vol_comercial_congelado": "sum"
            }).reset_index()

            df_sku["acc_ia"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
            df_sku["acc_comercial"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
            df_sku["fva"] = df_sku["acc_comercial"] - df_sku["acc_ia"]
            df_sku["estoque_canal"] = 0
            df_sku["dias_cobertura"] = 0

        else:
            # LENTE SELL-OUT: Trata métricas com foco em Estoque e Giro
            soma_real = df_completo["vol_real"].sum()
            soma_ia = df_completo["vol_ia_congelado"].sum()
            err_ia = df_completo.groupby("sku").apply(lambda x: np.abs(x["vol_real"].sum() - x["vol_ia_congelado"].sum())).sum()
            
            acc_ia = max(0.0, 1.0 - (err_ia / soma_real)) if soma_real > 0 else 1.0
            acc_com = acc_ia # Sem variação humana no sell-out ponta
            bias_global = (soma_ia - soma_real) / soma_real if soma_real > 0 else 0.0

            cronologia = []
            for m in meses_horizonte:
                df_m = df_completo[df_completo["mes_ano"] == m]
                r_m = df_m["vol_real"].sum()
                ia_m = df_m["vol_ia_congelado"].sum()
                cronologia.append({
                    "mes": m, "bias_ia": round(((ia_m - r_m) / r_m) * 100, 2) if r_m > 0 else 0, "bias_comercial": 0
                })

            df_sku = df_completo.groupby(["sku", "descricao", "categoria", "segmento"]).agg({
                "vol_real": "sum",
                "vol_ia_congelado": "sum",
                "estoque_canal": "last",
                "dias_cobertura": "mean"
            }).reset_index()
            df_sku["vol_comercial_congelado"] = df_sku["vol_ia_congelado"]
            df_sku["acc_ia"] = np.where(df_sku["vol_real"] > 0, 1.0 - (np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]) / df_sku["vol_real"]), 1.0).clip(0, 1)
            df_sku["acc_comercial"] = df_sku["acc_ia"]
            df_sku["fva"] = 0.0

        # Filtro de portfólio dinâmico para popular os seletores em cascata do React
        categorias_disponiveis = list(df_completo["categoria"].unique())
        segmentos_disponiveis = list(df_completo["segmento"].unique())

        return {
            "kpis_globais": {
                "acc_ia": round(acc_ia, 4),
                "acc_comercial": round(acc_com, 4),
                "fva": round(acc_com - acc_ia, 4),
                "bias_global": round(bias_global, 4),
                "cobertura_media_canal": int(df_completo["dias_cobertura"].mean()) if "dias_cobertura" in df_completo.columns else 0
            },
            "cronologia": cronologia,
            "tabela_skus": df_sku.to_dict(orient="records"),
            "filtros_cascata": {
                "categorias": categorias_disponiveis,
                "segmentos": segmentos_disponiveis
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))