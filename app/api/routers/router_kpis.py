from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Optional
import aiohttp

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.core.config import settings
from app.models.domain_models import FatoEstoqueD0

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria, KPIs e Riscos de Estoque"])

def obter_ciclo_meta_seguro(db: Session, mes_alvo: str) -> str:
    try:
        mes, ano = mes_alvo.split('/')
        dt_alvo = datetime.date(int(ano), int(mes), 1)
        dt_lag2 = dt_alvo - relativedelta(months=2)
        ciclo_ideal = dt_lag2.strftime("%m/%Y")
        
        existe = db.execute(text("SELECT 1 FROM fato_ibp_granular WHERE ciclo_sop = :c LIMIT 1"), {"c": ciclo_ideal}).scalar()
        if existe:
            return ciclo_ideal
            
        max_ciclo = db.execute(text("SELECT MAX(ciclo_sop) FROM fato_ibp_granular")).scalar()
        return max_ciclo or "06/2026"
    except:
        return "06/2026"

# ==============================================================================
# 1. FILTROS EXCEL DINÂMICOS (CORRIGIDOS PARA MTRIX)
# ==============================================================================
@router.get("/filtros-auditoria")
async def carregar_filtros_auditoria(lente: str = "kpis", db: Session = Depends(get_db)):
    try:
        query_f = text("SELECT DISTINCT categoria, segmento FROM dim_produtos WHERE categoria IS NOT NULL AND segmento IS NOT NULL")
        df_f = pd.read_sql(query_f, db.bind)
        
        if lente == "sellout":
            q_meses = text("SELECT DISTINCT mes_ano FROM fato_mtrix_historico_mensal ORDER BY mes_ano DESC")
            df_m = pd.read_sql(q_meses, db.bind)
            meses = [f"{str(m).split('-')[1]}/{str(m).split('-')[0]}" for m in df_m["mes_ano"].unique() if m] if not df_m.empty else []
            
            query_cli = text("""
                SELECT DISTINCT c.razaosocial 
                FROM fato_mtrix_historico_mensal m
                JOIN dim_clientes c ON m.cgc = c.cgc
                WHERE c.razaosocial IS NOT NULL 
                ORDER BY c.razaosocial
            """)
            df_cli = pd.read_sql(query_cli, db.bind)
        else:
            q_meses = text("""
                SELECT DISTINCT TO_CHAR(mes_projetado, 'MM/YYYY') as mes_ano, TO_CHAR(mes_projetado, 'YYYY-MM') as sort_key 
                FROM fato_ibp_granular 
                WHERE mes_projetado >= '2026-06-01' 
                ORDER BY sort_key ASC
            """)
            df_m = pd.read_sql(q_meses, db.bind)
            meses = list(df_m["mes_ano"].unique()) if not df_m.empty else []
            
            query_cli = text("SELECT DISTINCT razaosocial FROM dim_clientes WHERE razaosocial IS NOT NULL ORDER BY razaosocial")
            df_cli = pd.read_sql(query_cli, db.bind)

        if not meses: meses = ["06/2026"]

        return {
            "pares_cat_seg": df_f.to_dict(orient="records") if not df_f.empty else [],
            "categorias": sorted(df_f["categoria"].unique().tolist()) if not df_f.empty else [],
            "segmentos": sorted(df_f["segmento"].unique().tolist()) if not df_f.empty else [],
            "clientes": df_cli["razaosocial"].tolist() if not df_cli.empty else [],
            "meses_disponiveis": meses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 2. MOTOR DE AUDITORIA (SELL-OUT MTRIX)
# ==============================================================================
@router.get("/auditoria-dinamica")
async def carregar_auditoria_cpfr(
    lente: str = "sellout", categoria: str = "Todas", segmento: str = "Todos", razaosocial: str = "Todos",
    meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)
):
    if not meses_horizonte: meses_horizonte = ["06/2026"]

    try:
        resultados_meses = []

        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = obter_ciclo_meta_seguro(db, mes_str)

            clausula_filtro = ""
            filtro_cliente = ""
            params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "mes_str": mes_str}
            
            if categoria != "Todas":
                clausula_filtro += " AND p.categoria = :categoria"
                params_query["categoria"] = categoria
            if segmento != "Todos":
                clausula_filtro += " AND p.segmento = :segmento"
                params_query["segmento"] = segmento
            if razaosocial != "Todos":
                filtro_cliente = " AND c.razaosocial = :razaosocial"
                params_query["razaosocial"] = razaosocial

            query = text(f"""
                WITH Ultimo_Ciclo_Mtrix AS (SELECT MAX(ciclo_sop) as max_ciclo FROM fato_mtrix_historico_mensal WHERE mes_ano = :mes_sql),
                Distribuidores AS (SELECT DISTINCT cgc FROM fato_mtrix_historico_mensal WHERE mes_ano = :mes_sql AND ciclo_sop = (SELECT max_ciclo FROM Ultimo_Ciclo_Mtrix) AND cgc IS NOT NULL),
                Sellout AS (
                    SELECT s.sku, SUM(s.volume_sellout) AS vol_sellout_real 
                    FROM fato_mtrix_historico_mensal s
                    LEFT JOIN dim_clientes c ON s.cgc = c.cgc
                    WHERE s.mes_ano = :mes_sql AND s.ciclo_sop = (SELECT max_ciclo FROM Ultimo_Ciclo_Mtrix) {filtro_cliente}
                    GROUP BY s.sku
                ),
                Metas_Pareadas AS (
                    SELECT i.sku, SUM(i.vol_ia) AS vol_ia_congelado, SUM(i.vol_final) AS vol_comercial_congelado 
                    FROM fato_ibp_granular i 
                    INNER JOIN Distribuidores d ON i.cgc = d.cgc 
                    LEFT JOIN dim_clientes c ON i.cgc = c.cgc
                    WHERE TO_CHAR(i.mes_projetado, 'YYYY-MM') = :mes_sql AND i.ciclo_sop = :ciclo_congelado {filtro_cliente}
                    GROUP BY i.sku
                ),
                Estoque AS (
                    SELECT e.sku, SUM(e.estoque_atual_caixas) AS estoque_atual_caixas, AVG(e.dias_cobertura) AS dias_cobertura 
                    FROM fato_mtrix_snapshot e
                    LEFT JOIN dim_clientes c ON e.cgc = c.cgc
                    WHERE e.ciclo_sop = (SELECT MAX(ciclo_sop) FROM fato_mtrix_snapshot) {filtro_cliente}
                    GROUP BY e.sku
                )
                SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano, COALESCE(s.vol_sellout_real, 0) AS vol_real, COALESCE(m.vol_ia_congelado, 0) AS vol_ia_congelado, COALESCE(m.vol_comercial_congelado, 0) AS vol_comercial_congelado, COALESCE(e.estoque_atual_caixas, 0) AS estoque_canal, COALESCE(e.dias_cobertura, 0) AS dias_cobertura
                FROM dim_produtos p 
                LEFT JOIN Sellout s ON p.sku = s.sku 
                LEFT JOIN Metas_Pareadas m ON p.sku = m.sku 
                LEFT JOIN Estoque e ON p.sku = e.sku
                WHERE 1=1 {clausula_filtro} AND (COALESCE(s.vol_sellout_real, 0) > 0 OR COALESCE(m.vol_ia_congelado, 0) > 0 OR COALESCE(m.vol_comercial_congelado, 0) > 0 OR COALESCE(e.estoque_atual_caixas, 0) > 0)
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
        soma_ia = df_sku["vol_ia_congelado"].sum()
        soma_comercial = df_sku["vol_comercial_congelado"].sum()

        wmape_ia = df_sku["mape_ia"].sum() / len(df_sku) if len(df_sku) > 0 else 0
        wmape_comercial = df_sku["mape_comercial"].sum() / len(df_sku) if len(df_sku) > 0 else 0
        
        if soma_real > 0:
            wmape_ia = np.abs(df_sku["vol_real"] - df_sku["vol_ia_congelado"]).sum() / soma_real
            wmape_comercial = np.abs(df_sku["vol_real"] - df_sku["vol_comercial_congelado"]).sum() / soma_real

        bias_ia = (soma_ia - soma_real) / soma_real if soma_real > 0 else 0.0
        bias_humano = (soma_comercial - soma_real) / soma_real if soma_real > 0 else 0.0

        return {
            "kpis_globais": {
                "wmape_ia": round(wmape_ia, 4), "wmape_comercial": round(wmape_comercial, 4),
                "fva": round(wmape_ia - wmape_comercial, 4), 
                "bias_ia": round(bias_ia, 4), "bias_humano": round(bias_humano, 4), 
                "cobertura_media_canal": int(df_c[df_c["dias_cobertura"] < 999]["dias_cobertura"].mean()) if not df_c.empty else 0
            },
            "cronologia": [], "tabela_skus": df_sku.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 3. TORRE DE CONTROLE MTD 
# ==============================================================================
@router.get("/torre-controle")
async def carregar_torre_controle(
    visao: str = "caixas", categoria: str = "Todas", segmento: str = "Todos", razaosocial: str = "Todos",
    meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)
):
    if not meses_horizonte: meses_horizonte = ["06/2026"]

    try:
        resultados = []
        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = obter_ciclo_meta_seguro(db, mes_str)
            
            clausula_filtro = ""
            filtro_cliente = ""
            params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "mes_str": mes_str}
            
            if categoria != "Todas": clausula_filtro += " AND p.categoria = :categoria"; params_query["categoria"] = categoria
            if segmento != "Todos": clausula_filtro += " AND p.segmento = :segmento"; params_query["segmento"] = segmento
            if razaosocial != "Todos": filtro_cliente = " AND c.razaosocial = :razaosocial"; params_query["razaosocial"] = razaosocial

            f_r = "v.qt_pedido" if visao == "caixas" else "v.vl_pedido"
            f_mi = "m.vol_ia" if visao == "caixas" else "(m.vol_ia * m.pmv_aplicado)"
            f_mh = "m.vol_final" if visao == "caixas" else "(m.vol_final * m.pmv_aplicado)"

            query = text(f"""
                WITH Vendas AS (
                    SELECT v.sku, SUM({f_r}) AS val_real 
                    FROM fato_vendas v
                    LEFT JOIN dim_clientes c ON v.cgc = c.cgc
                    WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql {filtro_cliente}
                    GROUP BY v.sku
                ),
                Metas AS (
                    SELECT m.sku, SUM({f_mi}) AS val_meta_ia, SUM({f_mh}) AS val_meta_hum 
                    FROM fato_ibp_granular m
                    LEFT JOIN dim_clientes c ON m.cgc = c.cgc
                    WHERE TO_CHAR(m.mes_projetado, 'YYYY-MM') = :mes_sql AND m.ciclo_sop = :ciclo_congelado {filtro_cliente}
                    GROUP BY m.sku
                )
                SELECT p.sku, p.descricao, p.categoria, p.segmento, :mes_str AS mes_ano, 
                       COALESCE(v.val_real, 0) AS val_real, 
                       COALESCE(m.val_meta_ia, 0) AS val_meta_ia, 
                       COALESCE(m.val_meta_hum, 0) AS val_meta_hum
                FROM dim_produtos p 
                LEFT JOIN Vendas v ON p.sku = v.sku 
                LEFT JOIN Metas m ON p.sku = m.sku
                WHERE 1=1 {clausula_filtro} AND (COALESCE(v.val_real, 0) > 0 OR COALESCE(m.val_meta_ia, 0) > 0 OR COALESCE(m.val_meta_hum, 0) > 0)
            """)
            df_m = pd.read_sql(query, db.bind, params=params_query)
            if not df_m.empty: resultados.append(df_m)

        if not resultados: return {"graficos": [], "skus": [], "kpis_globais": {}}
        df = pd.concat(resultados, ignore_index=True)
        
        for col in ['val_real', 'val_meta_ia', 'val_meta_hum']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        cronologia = []
        for m in meses_horizonte:
            df_m = df[df["mes_ano"] == m]
            if df_m.empty: continue
            sr, sm_ia, sm_hum = df_m["val_real"].sum(), df_m["val_meta_ia"].sum(), df_m["val_meta_hum"].sum()

            wmape_ia = (np.abs(df_m["val_real"] - df_m["val_meta_ia"]).sum() / sr) if sr > 0 else 1.0
            wmape_humano = (np.abs(df_m["val_real"] - df_m["val_meta_hum"]).sum() / sr) if sr > 0 else 1.0
            bias_ia = ((sm_ia - sr) / sr) if sr > 0 else 0
            bias_humano = ((sm_hum - sr) / sr) if sr > 0 else 0
            
            cronologia.append({"mes": m, "wmape_ia": round(wmape_ia, 4), "wmape_humano": round(wmape_humano, 4), "bias_ia": round(bias_ia, 4), "bias_humano": round(bias_humano, 4)})

        sr_total, sm_ia_total, sm_hum_total = df["val_real"].sum(), df["val_meta_ia"].sum(), df["val_meta_hum"].sum()
        wmape_ia_g = (np.abs(df["val_real"] - df["val_meta_ia"]).sum() / sr_total) if sr_total > 0 else 1.0
        wmape_hum_g = (np.abs(df["val_real"] - df["val_meta_hum"]).sum() / sr_total) if sr_total > 0 else 1.0
        
        kpis_globais = {
            "wmape_ia": round(wmape_ia_g, 4), "wmape_comercial": round(wmape_hum_g, 4),
            "fva": round(wmape_ia_g - wmape_hum_g, 4), "bias_ia": round(((sm_ia_total - sr_total) / sr_total) if sr_total > 0 else 0, 4),
            "bias_humano": round(((sm_hum_total - sr_total) / sr_total) if sr_total > 0 else 0, 4)
        }

        df_sku = df.groupby(["sku", "descricao"]).agg({"val_real": "sum", "val_meta_ia": "sum", "val_meta_hum": "sum"}).reset_index()
        df_sku["gap_ia"] = df_sku["val_meta_ia"] - df_sku["val_real"]
        df_sku["gap_humano"] = df_sku["val_meta_hum"] - df_sku["val_real"]
        df_sku["mape_ia"] = np.where(df_sku["val_real"] > 0, np.abs(df_sku["gap_ia"]) / df_sku["val_real"], np.where(df_sku["val_meta_ia"] > 0, 1.0, 0.0))
        df_sku["mape_humano"] = np.where(df_sku["val_real"] > 0, np.abs(df_sku["gap_humano"]) / df_sku["val_real"], np.where(df_sku["val_meta_hum"] > 0, 1.0, 0.0))
        df_sku["erro_absoluto"] = np.abs(df_sku["gap_humano"])
        
        return {"graficos": cronologia, "skus": df_sku.sort_values(by="erro_absoluto", ascending=False).to_dict(orient="records"), "kpis_globais": kpis_globais}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/torre-controle/clientes/{sku}")
async def drilldown_clientes_kpis(sku: str, visao: str = "caixas", meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)):
    if not meses_horizonte: meses_horizonte = ["06/2026"]
    try:
        resultados = []
        for mes_str in meses_horizonte:
            mes_sql = f"{mes_str.split('/')[1]}-{mes_str.split('/')[0]}" 
            ciclo_congelado = obter_ciclo_meta_seguro(db, mes_str)
            
            f_r = "qt_pedido" if visao == "caixas" else "vl_pedido"
            f_i = "vol_ia" if visao == "caixas" else "(vol_ia * pmv_aplicado)"
            f_m = "vol_final" if visao == "caixas" else "(vol_final * pmv_aplicado)"

            query = text(f"""
                WITH Vendas AS (SELECT cgc, SUM({f_r}) AS val_real FROM fato_vendas WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :mes_sql AND sku = :sku GROUP BY cgc),
                Metas AS (SELECT cgc, SUM({f_i}) AS val_meta_ia, SUM({f_m}) AS val_meta_hum FROM fato_ibp_granular WHERE TO_CHAR(mes_projetado, 'YYYY-MM') = :mes_sql AND ciclo_sop = :ciclo_congelado AND sku = :sku GROUP BY cgc),
                CliGeral AS (SELECT COALESCE(v.cgc, m.cgc) as cgc, COALESCE(v.val_real, 0) as val_real, COALESCE(m.val_meta_ia, 0) as val_meta_ia, COALESCE(m.val_meta_hum, 0) as val_meta_hum FROM Vendas v FULL OUTER JOIN Metas m ON v.cgc = m.cgc)
                SELECT cg.cgc, c.razaosocial, cg.val_real, cg.val_meta_ia, cg.val_meta_hum, (cg.val_meta_ia - cg.val_real) AS gap_ia, (cg.val_meta_hum - cg.val_real) AS gap_humano 
                FROM CliGeral cg LEFT JOIN dim_clientes c ON cg.cgc = c.cgc WHERE cg.val_real > 0 OR cg.val_meta_ia > 0 OR cg.val_meta_hum > 0 
            """)
            df = pd.read_sql(query, db.bind, params={"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "sku": sku})
            if not df.empty: resultados.append(df)
        if not resultados: return []
        df_final = pd.concat(resultados)
        for col in ['val_real', 'val_meta_ia', 'val_meta_hum', 'gap_ia', 'gap_humano']: df_final[col] = pd.to_numeric(df_final[col], errors='coerce').fillna(0)
        return df_final.groupby(["cgc", "razaosocial"]).sum().reset_index().sort_values(by="gap_humano", ascending=False).fillna("Não Cadastrado").to_dict(orient="records")
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 4. INTELIGÊNCIA DE ESTOQUE E DEMANDA OCULTA (Demand Sensing S&OP)
# ==============================================================================
@router.post("/sync-stock")
async def sincronizar_estoque_api90(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        if usuario.get('funcao') not in ['Administrador', 'Supply Chain', 'Gerente', 'C-Level']:
            raise HTTPException(status_code=403, detail="Sem permissão.")

        headers = {"Authorization": f"Bearer {settings.GOBI_TOKEN}"}
        base_url = "https://gobi-api.lineaalimentos.com.br/v1/reports/90/data"
        limit = 5000
        offset = 0
        lote_anterior = []
        estoque_novo = {}

        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    params = {"streaming": "true", "format": "json", "limit": limit, "offset": offset}
                    async with session.get(base_url, headers=headers, params=params, timeout=5) as response:
                        if response.status != 200: break
                        dados = await response.json(content_type=None)
                        if not dados or not isinstance(dados, list): break
                        if lote_anterior and dados[0] == lote_anterior[0]: break
                        
                        for row in dados:
                            if str(row.get('arm', '')).strip() == "05":
                                sku = str(row.get('produto', '')).replace(".0", "").strip()
                                try: qtd = float(row.get('quantidade', 0))
                                except: qtd = 0.0
                                if sku: estoque_novo[sku] = estoque_novo.get(sku, 0) + qtd
                        
                        lote_anterior = dados
                        offset += len(dados)
                        if len(dados) < limit: break
        except Exception as e:
            print(f"API Gobi indisponível. Recorrendo à Simulação Nexus para demonstração. Erro: {e}")

        db.execute(text("TRUNCATE TABLE fato_estoque_d0"))
        agora = datetime.datetime.utcnow()
        novos_registros = [FatoEstoqueD0(sku=sku, qtd_dispo=qtd, data_atualizacao=agora) for sku, qtd in estoque_novo.items()]
        
        if novos_registros: 
            db.bulk_save_objects(novos_registros)
            msg = "Estoque Sincronizado com Sucesso via ERP Gobi API."
        else:
            mock_query = text("""
                INSERT INTO fato_estoque_d0 (sku, qtd_dispo, data_atualizacao)
                SELECT sku, ROUND(SUM(vol_final) * (0.6 + (RANDOM() * 0.8))), NOW()
                FROM fato_ibp_granular
                WHERE ciclo_sop = (SELECT MAX(ciclo_sop) FROM fato_ibp_granular)
                GROUP BY sku
            """)
            db.execute(mock_query)
            msg = "Conexão Gobi ERP Falhou. Sistema acionou o Modo de Simulação Nexus (Estoque Matemático Injetado)."

        db.commit()
        return {"status": "success", "count": len(novos_registros), "message": msg}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/riscos-estoque")
async def carregar_riscos_estoque(
    categoria: str = "Todas", segmento: str = "Todos", razaosocial: str = "Todos",
    meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)
):
    if not meses_horizonte: meses_horizonte = ["06/2026"]
    try:
        mes_alvo = meses_horizonte[0] 
        mes_sql = f"{mes_alvo.split('/')[1]}-{mes_alvo.split('/')[0]}"
        ciclo_congelado = obter_ciclo_meta_seguro(db, mes_alvo)
        
        clausula_filtro = ""
        filtro_cliente = ""
        params_query = {"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado}
        if categoria != "Todas": clausula_filtro += " AND p.categoria = :categoria"; params_query["categoria"] = categoria
        if segmento != "Todos": clausula_filtro += " AND p.segmento = :segmento"; params_query["segmento"] = segmento
        if razaosocial != "Todos": filtro_cliente = " AND c.razaosocial = :razaosocial"; params_query["razaosocial"] = razaosocial

        # O MOTOR DE DEMAND SENSING (Trava de 80% sobre a Meta S&OP)
        query_sku = text(f"""
            WITH Estoque AS (SELECT sku, SUM(qtd_dispo) as estoque_atual FROM fato_estoque_d0 GROUP BY sku),
            Vendas AS (
                SELECT v.sku, SUM(v.qt_pedido) as vendas_mtd, SUM(v.qtfatura) as faturado_mtd, SUM(v.qtcorte) as corte_mtd,
                       SUM(v.vl_pedido) as vl_pedido, SUM(v.vlfatura) as vl_faturado, SUM(v.vlcorte) as vl_corte
                FROM fato_vendas v LEFT JOIN dim_clientes c ON v.cgc = c.cgc
                WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql {filtro_cliente} GROUP BY v.sku
            ),
            Metas_Clientes AS (
                SELECT m.sku, c.regional, c.razaosocial, SUM(m.vol_final) as meta_vol
                FROM fato_ibp_granular m JOIN dim_clientes c ON m.cgc = c.cgc
                WHERE TO_CHAR(m.mes_projetado, 'YYYY-MM') = :mes_sql AND m.ciclo_sop = :ciclo_congelado {filtro_cliente}
                GROUP BY m.sku, c.regional, c.razaosocial
            ),
            MTD_Clientes AS (
                SELECT v.sku, c.regional, c.razaosocial, SUM(v.qt_pedido) as mtd_vol
                FROM fato_vendas v JOIN dim_clientes c ON v.cgc = c.cgc
                WHERE TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql {filtro_cliente}
                GROUP BY v.sku, c.regional, c.razaosocial
            ),
            Previsao_Clientes AS (
                SELECT 
                    COALESCE(m.sku, v.sku) as sku,
                    COALESCE(m.meta_vol, 0) as meta_vol,
                    COALESCE(v.mtd_vol, 0) as mtd_vol,
                    CASE 
                        WHEN COALESCE(v.mtd_vol, 0) >= COALESCE(m.meta_vol, 0) * 0.8 THEN 0 
                        ELSE GREATEST(0, COALESCE(m.meta_vol, 0) - COALESCE(v.mtd_vol, 0))
                    END as previsao_vol
                FROM Metas_Clientes m
                FULL OUTER JOIN MTD_Clientes v ON m.sku = v.sku AND m.regional = v.regional AND m.razaosocial = v.razaosocial
            ),
            Previsao_SKU AS (
                SELECT sku, SUM(previsao_vol) as previsao_entrada_vol FROM Previsao_Clientes GROUP BY sku
            ),
            Metas AS (
                SELECT m.sku, SUM(m.vol_final) as meta_mes, 
                       COALESCE(SUM(m.vol_final * m.pmv_aplicado) / NULLIF(SUM(m.vol_final), 0), MAX(m.pmv_aplicado)) as pmv 
                FROM fato_ibp_granular m LEFT JOIN dim_clientes c ON m.cgc = c.cgc
                WHERE TO_CHAR(m.mes_projetado, 'YYYY-MM') = :mes_sql AND m.ciclo_sop = :ciclo_congelado {filtro_cliente}
                GROUP BY m.sku
            )
            SELECT p.sku, p.descricao, p.categoria, COALESCE(e.estoque_atual, 0) as estoque_atual, COALESCE(v.vendas_mtd, 0) as vendas_mtd_vol, 
                   COALESCE(v.faturado_mtd, 0) as faturado_mtd_vol, COALESCE(v.corte_mtd, 0) as corte_mtd_vol, COALESCE(v.vl_pedido, 0) as vl_pedido,
                   COALESCE(v.vl_faturado, 0) as vl_faturado, COALESCE(v.vl_corte, 0) as vl_corte, COALESCE(prev.previsao_entrada_vol, 0) as previsao_entrada_vol,
                   COALESCE(m.meta_mes, 0) as meta_mes_vol, COALESCE(m.pmv, 0) as pmv
            FROM dim_produtos p 
            LEFT JOIN Estoque e ON p.sku = e.sku 
            LEFT JOIN Vendas v ON p.sku = v.sku 
            LEFT JOIN Previsao_SKU prev ON p.sku = prev.sku
            LEFT JOIN Metas m ON p.sku = m.sku
            WHERE 1=1 {clausula_filtro} AND (COALESCE(e.estoque_atual, 0) > 0 OR COALESCE(v.vendas_mtd, 0) > 0 OR COALESCE(m.meta_mes, 0) > 0 OR COALESCE(prev.previsao_entrada_vol, 0) > 0)
        """)
        
        df_sku = pd.read_sql(query_sku, db.bind, params=params_query)
        if df_sku.empty: 
            return {
                "estoque_sku": [], 
                "kpis_globais": {
                    "total_ruptura_rs": 0, "total_sobra_rs": 0, "total_ruptura_vol": 0, "total_sobra_vol": 0, "meta_caixas": 0, "realizado_caixas": 0
                }
            }

        for col in ['meta_mes_vol', 'vendas_mtd_vol', 'faturado_mtd_vol', 'corte_mtd_vol', 'estoque_atual', 'pmv', 'vl_pedido', 'vl_faturado', 'vl_corte', 'previsao_entrada_vol']: 
            df_sku[col] = pd.to_numeric(df_sku[col], errors='coerce').fillna(0)
            
        # Espelhamento Financeiro
        df_sku['estoque_rs'] = df_sku['estoque_atual'] * df_sku['pmv']
        df_sku['meta_mes_rs'] = df_sku['meta_mes_vol'] * df_sku['pmv']
        
        df_sku['carteira_aberto_vol'] = np.maximum(0, df_sku['vendas_mtd_vol'] - df_sku['faturado_mtd_vol'] - df_sku['corte_mtd_vol'])
        df_sku['carteira_aberto_rs'] = np.maximum(0, df_sku['vl_pedido'] - df_sku['vl_faturado'] - df_sku['vl_corte'])
        
        df_sku['previsao_entrada_rs'] = df_sku['previsao_entrada_vol'] * df_sku['pmv']
        
        # A Projeção agora é o Realizado + O que ainda falta entrar base no S&OP
        df_sku['projecao_fim_mes_vol'] = df_sku['vendas_mtd_vol'] + df_sku['previsao_entrada_vol']
        df_sku['projecao_fim_mes_rs'] = df_sku['vl_pedido'] + df_sku['previsao_entrada_rs']
        
        df_sku['gap_meta_vol'] = df_sku['projecao_fim_mes_vol'] - df_sku['meta_mes_vol']
        df_sku['gap_meta_rs'] = df_sku['projecao_fim_mes_rs'] - df_sku['meta_mes_rs']

        # CÁLCULO DE RISCO DE PRODUÇÃO: Demanda Futura = Carteira + Previsão de Entrada
        df_sku['demanda_futura_vol'] = df_sku['carteira_aberto_vol'] + df_sku['previsao_entrada_vol']
        df_sku['ruptura_vol'] = np.maximum(0, df_sku['demanda_futura_vol'] - df_sku['estoque_atual'])
        df_sku['ruptura_rs'] = df_sku['ruptura_vol'] * df_sku['pmv']
        
        df_sku['sobra_vol'] = np.maximum(0, df_sku['estoque_atual'] - df_sku['demanda_futura_vol'])
        df_sku['sobra_rs'] = df_sku['sobra_vol'] * df_sku['pmv']

        kpis = {
            "total_ruptura_rs": float(df_sku['ruptura_rs'].sum()), 
            "total_ruptura_vol": float(df_sku['ruptura_vol'].sum()), 
            "total_sobra_rs": float(df_sku['sobra_rs'].sum()), 
            "total_sobra_vol": float(df_sku['sobra_vol'].sum()),
            "meta_caixas": float(df_sku['meta_mes_vol'].sum()), 
            "realizado_caixas": float(df_sku['vendas_mtd_vol'].sum())
        }

        return {"estoque_sku": df_sku.to_dict(orient="records"), "kpis_globais": kpis}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/riscos-estoque/drilldown-clientes/{sku}")
async def drilldown_riscos_clientes(sku: str, meses_horizonte: List[str] = Query(None), db: Session = Depends(get_db)):
    if not meses_horizonte: meses_horizonte = ["06/2026"]
    mes_alvo = meses_horizonte[0] 
    mes_sql = f"{mes_alvo.split('/')[1]}-{mes_alvo.split('/')[0]}"
    ciclo_congelado = obter_ciclo_meta_seguro(db, mes_alvo)
    
    query = text("""
        WITH Metas_Clientes AS (
            SELECT c.regional, c.razaosocial, SUM(m.vol_final) as meta_vol
            FROM fato_ibp_granular m JOIN dim_clientes c ON m.cgc = c.cgc
            WHERE m.sku = :sku AND TO_CHAR(m.mes_projetado, 'YYYY-MM') = :mes_sql AND m.ciclo_sop = :ciclo_congelado
            GROUP BY c.regional, c.razaosocial
        ),
        MTD_Clientes AS (
            SELECT c.regional, c.razaosocial, SUM(v.qt_pedido) as mtd_vol
            FROM fato_vendas v JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE v.sku = :sku AND TO_CHAR(v.data_pedido, 'YYYY-MM') = :mes_sql
            GROUP BY c.regional, c.razaosocial
        ),
        Freq_Clientes AS (
            SELECT c.regional, c.razaosocial, COUNT(DISTINCT TO_CHAR(v.data_pedido, 'YYYY-MM')) as freq_meses
            FROM fato_vendas v JOIN dim_clientes c ON v.cgc = c.cgc
            WHERE v.sku = :sku AND v.data_pedido >= TO_DATE(:mes_sql, 'YYYY-MM') - INTERVAL '6 months' AND v.data_pedido < TO_DATE(:mes_sql, 'YYYY-MM')
            GROUP BY c.regional, c.razaosocial
        )
        SELECT 
            COALESCE(m.regional, v.regional, f.regional) as regional, 
            COALESCE(m.razaosocial, v.razaosocial, f.razaosocial) as razaosocial,
            COALESCE(m.meta_vol, 0) as meta_vol, 
            COALESCE(v.mtd_vol, 0) as mtd_vol,
            COALESCE(f.freq_meses, 0) as freq_meses,
            CASE 
                WHEN COALESCE(v.mtd_vol, 0) >= COALESCE(m.meta_vol, 0) * 0.8 THEN 0 
                ELSE GREATEST(0, COALESCE(m.meta_vol, 0) - COALESCE(v.mtd_vol, 0))
            END as previsao_vol
        FROM Metas_Clientes m
        FULL OUTER JOIN MTD_Clientes v ON m.regional = v.regional AND m.razaosocial = v.razaosocial
        FULL OUTER JOIN Freq_Clientes f ON COALESCE(m.regional, v.regional) = f.regional AND COALESCE(m.razaosocial, v.razaosocial) = f.razaosocial
        WHERE GREATEST(0, COALESCE(m.meta_vol, 0) - COALESCE(v.mtd_vol, 0)) > 0 OR COALESCE(v.mtd_vol, 0) > 0 OR COALESCE(m.meta_vol, 0) > 0
        ORDER BY previsao_vol DESC, mtd_vol DESC
    """)
    df = pd.read_sql(query, db.bind, params={"mes_sql": mes_sql, "ciclo_congelado": ciclo_congelado, "sku": sku})
    return df.to_dict(orient="records")