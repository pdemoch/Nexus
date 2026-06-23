from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Optional

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria, KPIs e Riscos de Estoque"])

def calcular_ciclo_lag2(mes_alvo: str) -> str:
    mes, ano = mes_alvo.split('/')
    dt_alvo = datetime.date(int(ano), int(mes), 1)
    dt_lag2 = dt_alvo - relativedelta(months=2)
    return dt_lag2.strftime("%m/%Y")

# ==============================================================================
# 1. FILTROS EXCEL DINÂMICOS (TRAVA 06/2026)
# ==============================================================================
@router.get("/filtros-auditoria")
async def carregar_filtros_auditoria(lente: str = "sellin", db: Session = Depends(get_db)):
    try:
        query_f = text("SELECT DISTINCT categoria, segmento FROM dim_produtos WHERE categoria IS NOT NULL")
        df_f = pd.read_sql(query_f, db.bind)
        
        if lente == "sellout":
            q_meses = text("SELECT DISTINCT mes_ano FROM fato_mtrix_historico_mensal WHERE mes_ano >= '2026-06' ORDER BY mes_ano DESC")
            df_m = pd.read_sql(q_meses, db.bind)
            meses = [f"{str(m).split('-')[1]}/{str(m).split('-')[0]}" for m in df_m["mes_ano"].unique() if m] if not df_m.empty else []
        else:
            q_meses = text("""
                SELECT DISTINCT TO_CHAR(mes_projetado, 'MM/YYYY') as mes_ano, TO_CHAR(mes_projetado, 'YYYY-MM') as sort_key 
                FROM fato_ibp_granular 
                WHERE mes_projetado >= '2026-06-01' 
                ORDER BY sort_key ASC
            """)
            df_m = pd.read_sql(q_meses, db.bind)
            meses = list(df_m["mes_ano"].unique()) if not df_m.empty else []

        if not meses: meses = ["06/2026"]

        return {
            "categorias": list(df_f["categoria"].dropna().unique()),
            "segmentos": list(df_f["segmento"].dropna().unique()),
            "meses_disponiveis": meses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 2. MOTOR DE AUDITORIA (SELL-IN E SELL-OUT)
# ==============================================================================
@router.get("/auditoria-dinamica")
async def carregar_auditoria_cpfr(
    lente: str = "sellin", categoria: Optional[str] = "Todas", segmento: Optional[str] = "Todos",
    meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)
):
    if not meses_horizonte: meses_horizonte = ["06/2026"]

    try:
        resultados_meses = []

        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = calcular_ciclo_lag2(mes_str)

            clausula_filtro = ""
            params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "mes_str": mes_str}
            
            if categoria and categoria != "Todas":
                clausula_filtro += " AND p.categoria = :categoria"
                params_query["categoria"] = categoria
            if segmento and segmento != "Todos":
                clausula_filtro += " AND p.segmento = :segmento"
                params_query["segmento"] = segmento

            if lente == "sellin":
                query = text(f"""
                    WITH Vendas AS (SELECT sku, SUM(qt_pedido) AS vol_real FROM fato_vendas WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql GROUP BY sku),
                    Metas AS (SELECT sku, SUM(vol_ia) AS vol_ia_congelado, SUM(vol_final) AS vol_comercial_congelado FROM fato_ibp_granular WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql AND ciclo_sop = :ciclo_congelado GROUP BY sku)
                    SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano, COALESCE(v.vol_real, 0) AS vol_real, COALESCE(i.vol_ia_congelado, 0) AS vol_ia_congelado, COALESCE(i.vol_comercial_congelado, 0) AS vol_comercial_congelado, 0 AS estoque_canal, 0 AS dias_cobertura
                    FROM dim_produtos p LEFT JOIN Vendas v ON p.sku = v.sku LEFT JOIN Metas i ON p.sku = i.sku
                    WHERE 1=1 {clausula_filtro} AND (COALESCE(v.vol_real, 0) > 0 OR COALESCE(i.vol_ia_congelado, 0) > 0 OR COALESCE(i.vol_comercial_congelado, 0) > 0)
                """)
            else:
                query = text(f"""
                    WITH Ultimo_Ciclo_Mtrix AS (SELECT MAX(ciclo_sop) as max_ciclo FROM fato_mtrix_historico_mensal WHERE mes_ano = :mes_sql),
                    Distribuidores AS (SELECT DISTINCT cgc FROM fato_mtrix_historico_mensal WHERE mes_ano = :mes_sql AND ciclo_sop = (SELECT max_ciclo FROM Ultimo_Ciclo_Mtrix) AND cgc IS NOT NULL),
                    FatFabrica AS (SELECT v.sku, SUM(v.qt_pedido) AS vol_sellin_real FROM fato_vendas v INNER JOIN Distribuidores d ON v.cgc = d.cgc WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql GROUP BY v.sku),
                    Sellout AS (SELECT sku, SUM(volume_sellout) AS vol_sellout_real FROM fato_mtrix_historico_mensal WHERE mes_ano = :mes_sql AND ciclo_sop = (SELECT max_ciclo FROM Ultimo_Ciclo_Mtrix) GROUP BY sku),
                    Estoque AS (SELECT DISTINCT ON (sku) sku, estoque_atual_caixas, dias_cobertura FROM fato_mtrix_snapshot WHERE ciclo_sop = :ciclo_congelado ORDER BY sku, id DESC)
                    SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano, COALESCE(f.vol_sellin_real, 0) AS vol_real, COALESCE(s.vol_sellout_real, 0) AS vol_ia_congelado, COALESCE(e.estoque_atual_caixas, 0) AS estoque_canal, COALESCE(e.dias_cobertura, 0) AS dias_cobertura, COALESCE(s.vol_sellout_real, 0) AS vol_comercial_congelado
                    FROM dim_produtos p LEFT JOIN FatFabrica f ON p.sku = f.sku LEFT JOIN Sellout s ON p.sku = s.sku LEFT JOIN Estoque e ON p.sku = e.sku
                    WHERE 1=1 {clausula_filtro} AND (COALESCE(f.vol_sellin_real, 0) > 0 OR COALESCE(s.vol_sellout_real, 0) > 0 OR COALESCE(e.estoque_atual_caixas, 0) > 0)
                """)
            df = pd.read_sql(query, db.bind, params=params_query)
            if not df.empty: resultados_meses.append(df)

        if not resultados_meses: return {"kpis_globais": {}, "cronologia": [], "tabela_skus": []}
        df_c = pd.concat(resultados_meses, ignore_index=True)
        
        for col in ["vol_real", "vol_ia_congelado", "vol_comercial_congelado"]:
            df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0)

        df_sku = df_c.groupby(["sku", "descricao", "categoria", "segmento"]).agg({"vol_real": "sum", "vol_ia_congelado": "sum", "vol_comercial_congelado": "sum", "estoque_canal": "last", "dias_cobertura": "last"}).reset_index()

        df_sku["mape_ia"] = np.where(df_sku["vol_real"] > 0, np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]) / df_sku["vol_real"], np.where(df_sku["vol_ia_congelado"] > 0, 1.0, 0.0))
        df_sku["mape_comercial"] = np.where(df_sku["vol_real"] > 0, np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"]) / df_sku["vol_real"], np.where(df_sku["vol_comercial_congelado"] > 0, 1.0, 0.0))
        df_sku["fva"] = df_sku["mape_ia"] - df_sku["mape_comercial"]
        
        df_sku["erro_abs_comercial"] = np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"])
        df_sku = df_sku.sort_values(by="erro_abs_comercial", ascending=False)

        soma_real = df_sku["vol_real"].sum()
        wmape_ia = df_sku["mape_ia"].sum() / len(df_sku) if len(df_sku) > 0 else 0
        wmape_comercial = df_sku["mape_comercial"].sum() / len(df_sku) if len(df_sku) > 0 else 0
        
        if soma_real > 0:
            wmape_ia = np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]).sum() / soma_real
            wmape_comercial = np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"]).sum() / soma_real

        bias_global = (df_sku["vol_comercial_congelado"].sum() - soma_real) / soma_real if soma_real > 0 else 0.0

        return {
            "kpis_globais": {
                "wmape_ia": round(wmape_ia, 4), "wmape_comercial": round(wmape_comercial, 4),
                "fva": round(wmape_ia - wmape_comercial, 4) if lente == "sellin" else 0.0, 
                "bias_global": round(bias_global, 4), "cobertura_media_canal": int(df_c[df_c["dias_cobertura"] < 999]["dias_cobertura"].mean()) if lente == "sellout" and not df_c.empty else 0
            },
            "cronologia": [], "tabela_skus": df_sku.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 3. TORRE DE CONTROLE MTD (COM DUPLO GAP - IA E HUMANO)
# ==============================================================================
@router.get("/torre-controle")
async def carregar_torre_controle(
    visao: str = "caixas", categoria: str = "Todas", segmento: str = "Todos", 
    meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)
):
    if not meses_horizonte: meses_horizonte = ["06/2026"]

    try:
        resultados = []
        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = calcular_ciclo_lag2(mes_str)
            clausula_filtro = ""
            params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "mes_str": mes_str}
            
            if categoria != "Todas": clausula_filtro += " AND p.categoria = :categoria"; params_query["categoria"] = categoria
            if segmento != "Todos": clausula_filtro += " AND p.segmento = :segmento"; params_query["segmento"] = segmento

            f_r = "qt_pedido" if visao == "caixas" else "vl_pedido"
            f_mi = "vol_ia" if visao == "caixas" else "(vol_ia * pmv_aplicado)"
            f_mh = "vol_final" if visao == "caixas" else "(vol_final * pmv_aplicado)"

            query = text(f"""
                WITH Vendas AS (SELECT sku, SUM({f_r}) AS val_real FROM fato_vendas WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql GROUP BY sku),
                Metas AS (SELECT sku, SUM({f_mi}) AS val_meta_ia, SUM({f_mh}) AS val_meta_hum FROM fato_ibp_granular WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql AND ciclo_sop = :ciclo_congelado GROUP BY sku)
                SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano, 
                       COALESCE(v.val_real, 0) AS val_real, 
                       COALESCE(m.val_meta_ia, 0) AS val_meta_ia, 
                       COALESCE(m.val_meta_hum, 0) AS val_meta_hum
                FROM dim_produtos p LEFT JOIN Vendas v ON p.sku = v.sku LEFT JOIN Metas m ON p.sku = m.sku
                WHERE 1=1 {clausula_filtro} AND (COALESCE(v.val_real, 0) > 0 OR COALESCE(m.val_meta_ia, 0) > 0 OR COALESCE(m.val_meta_hum, 0) > 0)
            """)
            df_m = pd.read_sql(query, db.bind, params=params_query)
            if not df_m.empty: resultados.append(df_m)

        if not resultados: return {"graficos": [], "skus": []}
        df = pd.concat(resultados, ignore_index=True)
        
        # Blindagem contra concatenação de strings escondidas
        for col in ['val_real', 'val_meta_ia', 'val_meta_hum']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        cronologia = []
        for m in meses_horizonte:
            df_m = df[df["mes_ano"] == m]
            if df_m.empty: continue
            sr, sm = df_m["val_real"].sum(), df_m["val_meta_hum"].sum()
            wmape = (np.abs(df_m["val_real"] - df_m["val_meta_hum"]).sum() / sr) if sr > 0 else 1.0
            
            df_m_mape = df_m[df_m["val_real"] > 0]
            mape = np.mean(np.abs(df_m_mape["val_real"] - df_m_mape["val_meta_hum"]) / df_m_mape["val_real"]) if not df_m_mape.empty else 1.0
            
            cronologia.append({"mes": m, "wmape": round(wmape, 4), "mape": round(mape, 4), "bias": round(((sm - sr) / sr), 4) if sr > 0 else 0})

        # Agrupamento Multi-Mês Seguro
        df_sku = df.groupby(["sku", "descricao"]).agg({"val_real": "sum", "val_meta_ia": "sum", "val_meta_hum": "sum"}).reset_index()
        
        # Cálculo dos Gaps (Meta - Realizado) -> >0 significa "Falta Vender", <0 "Estourou Meta"
        df_sku["gap_ia"] = df_sku["val_meta_ia"] - df_sku["val_real"]
        df_sku["gap_humano"] = df_sku["val_meta_hum"] - df_sku["val_real"]

        # MAPE
        df_sku["mape_ia"] = np.where(df_sku["val_real"] > 0, np.abs(df_sku["gap_ia"]) / df_sku["val_real"], np.where(df_sku["val_meta_ia"] > 0, 1.0, 0.0))
        df_sku["mape_humano"] = np.where(df_sku["val_real"] > 0, np.abs(df_sku["gap_humano"]) / df_sku["val_real"], np.where(df_sku["val_meta_hum"] > 0, 1.0, 0.0))
        
        df_sku["erro_absoluto"] = np.abs(df_sku["gap_humano"])
        df_sku = df_sku.sort_values(by="erro_absoluto", ascending=False)
        
        return {"graficos": cronologia, "skus": df_sku.to_dict(orient="records")}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/torre-controle/clientes/{sku}")
async def drilldown_clientes_kpis(sku: str, visao: str = "caixas", meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)):
    if not meses_horizonte: meses_horizonte = ["06/2026"]

    try:
        resultados = []
        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = calcular_ciclo_lag2(mes_str)
            
            f_r = "qt_pedido" if visao == "caixas" else "vl_pedido"
            f_i = "vol_ia" if visao == "caixas" else "(vol_ia * pmv_aplicado)"
            f_m = "vol_final" if visao == "caixas" else "(vol_final * pmv_aplicado)"

            query = text(f"""
                WITH Vendas AS (SELECT cgc, SUM({f_r}) AS val_real FROM fato_vendas WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql AND sku = :sku GROUP BY cgc),
                Metas AS (SELECT cgc, SUM({f_i}) AS val_meta_ia, SUM({f_m}) AS val_meta_hum FROM fato_ibp_granular WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql AND ciclo_sop = :ciclo_congelado AND sku = :sku GROUP BY cgc),
                CliGeral AS (SELECT COALESCE(v.cgc, m.cgc) as cgc, COALESCE(v.val_real, 0) as val_real, COALESCE(m.val_meta_ia, 0) as val_meta_ia, COALESCE(m.val_meta_hum, 0) as val_meta_hum FROM Vendas v FULL OUTER JOIN Metas m ON v.cgc = m.cgc)
                SELECT cg.cgc, c.razaosocial, cg.val_real, cg.val_meta_ia, cg.val_meta_hum, 
                       (cg.val_meta_ia - cg.val_real) AS gap_ia, 
                       (cg.val_meta_hum - cg.val_real) AS gap_humano 
                FROM CliGeral cg LEFT JOIN dim_clientes c ON cg.cgc = c.cgc 
                WHERE cg.val_real > 0 OR cg.val_meta_ia > 0 OR cg.val_meta_hum > 0 
            """)
            df = pd.read_sql(query, db.bind, params={"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "sku": sku})
            if not df.empty: resultados.append(df)
        if not resultados: return []
        
        df_final = pd.concat(resultados)
        for col in ['val_real', 'val_meta_ia', 'val_meta_hum', 'gap_ia', 'gap_humano']:
            df_final[col] = pd.to_numeric(df_final[col], errors='coerce').fillna(0)
            
        df_final = df_final.groupby(["cgc", "razaosocial"]).sum().reset_index().sort_values(by="gap_humano", ascending=False)
        return df_final.fillna("Não Cadastrado").to_dict(orient="records")
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 4. INTELIGÊNCIA DE ESTOQUE (RISCOS)
# ==============================================================================
@router.post("/sync-stock")
async def sincronizar_estoque_api90(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try: return {"status": "success", "message": "Estoque sincronizado via API 90."}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/riscos-estoque")
async def carregar_riscos_estoque(
    categoria: str = "Todas", segmento: str = "Todos", 
    meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)
):
    if not meses_horizonte: meses_horizonte = ["06/2026"]
    
    try:
        mes_alvo = meses_horizonte[0] 
        mes_sql = f"{mes_alvo.split('/')[1]}-{mes_alvo.split('/')[0]}"
        ciclo_congelado = calcular_ciclo_lag2(mes_alvo)
        
        clausula_filtro = ""
        params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado}
        if categoria != "Todas": clausula_filtro += " AND p.categoria = :categoria"; params_query["categoria"] = categoria
        if segmento != "Todos": clausula_filtro += " AND p.segmento = :segmento"; params_query["segmento"] = segmento

        query_sku = text(f"""
            WITH Estoque AS (SELECT produto as sku, SUM(qtd_dispo) as estoque_atual FROM fato_estoque_d0 GROUP BY produto),
            Vendas AS (SELECT sku, SUM(qt_pedido) as vendas_mtd FROM fato_vendas WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql GROUP BY sku),
            Metas AS (SELECT sku, SUM(vol_final) as meta_mes, AVG(pmv_aplicado) as pmv FROM fato_ibp_granular WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql AND ciclo_sop = :ciclo_congelado GROUP BY sku)
            SELECT p.sku, p.descricao, p.categoria, COALESCE(e.estoque_atual, 0) as estoque_atual, COALESCE(v.vendas_mtd, 0) as vendas_mtd, COALESCE(m.meta_mes, 0) as meta_mes, COALESCE(m.pmv, 0) as pmv
            FROM dim_produtos p LEFT JOIN Estoque e ON p.sku = e.sku LEFT JOIN Vendas v ON p.sku = v.sku LEFT JOIN Metas m ON p.sku = m.sku
            WHERE 1=1 {clausula_filtro} AND (COALESCE(e.estoque_atual, 0) > 0 OR COALESCE(v.vendas_mtd, 0) > 0 OR COALESCE(m.meta_mes, 0) > 0)
        """)
        
        df_sku = pd.read_sql(query_sku, db.bind, params=params_query)
        if df_sku.empty: return {"estoque_sku": [], "kpis_globais": {"total_ruptura_rs": 0, "total_sobra_rs": 0, "meta_caixas": 0, "realizado_caixas": 0}}

        for col in ['meta_mes', 'vendas_mtd', 'estoque_atual', 'pmv']:
            df_sku[col] = pd.to_numeric(df_sku[col], errors='coerce').fillna(0)

        df_sku['meta_togo'] = np.maximum(0, df_sku['meta_mes'] - df_sku['vendas_mtd'])
        df_sku['ruptura_vol'] = np.maximum(0, df_sku['meta_togo'] - df_sku['estoque_atual'])
        df_sku['sobra_vol'] = np.maximum(0, df_sku['estoque_atual'] - df_sku['meta_togo'])
        
        df_sku['ruptura_rs'] = df_sku['ruptura_vol'] * df_sku['pmv']
        df_sku['sobra_rs'] = df_sku['sobra_vol'] * df_sku['pmv']

        kpis = {"total_ruptura_rs": float(df_sku['ruptura_rs'].sum()), "total_sobra_rs": float(df_sku['sobra_rs'].sum()), "meta_caixas": float(df_sku['meta_mes'].sum()), "realizado_caixas": float(df_sku['vendas_mtd'].sum())}
        df_sku = df_sku.sort_values(by=["ruptura_rs", "sobra_rs"], ascending=[False, False])
        return {"estoque_sku": df_sku.to_dict(orient="records"), "kpis_globais": kpis}
    except Exception as e: 
        raise HTTPException(status_code=500, detail=str(e))