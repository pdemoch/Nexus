from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.core.state import AppState

router = APIRouter(prefix="/api/v1/mtrix", tags=["Canal Indireto (Dossiê MTRIX)"])

@router.get("/dossier/{sku}")
async def obter_dossie_cpfr(sku: str, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)) -> Dict[str, Any]:
    try:
        ciclo_atual = getattr(AppState, 'ciclo_ativo', '07/2026')

        # 1. Busca os KPIs Globais Agregados para este SKU
        query_kpis = text("""
            SELECT 
                SUM(estoque_atual_caixas) as estoque_total,
                SUM(sellout_m1_caixas) as sellout_m1_total,
                MAX(previsao_sellout_m0) as previsao_ia_m0
            FROM fato_mtrix_snapshot
            WHERE sku = :sku AND ciclo_sop = :ciclo
        """)
        row_kpi = db.execute(query_kpis, {"sku": sku, "ciclo": ciclo_atual}).fetchone()
        
        estoque_total = float(row_kpi.estoque_total) if row_kpi and row_kpi.estoque_total else 0.0
        sellout_m1 = float(row_kpi.sellout_m1_total) if row_kpi and row_kpi.sellout_m1_total else 0.0
        previsao_ia = float(row_kpi.previsao_ia_m0) if row_kpi and row_kpi.previsao_ia_m0 else 0.0
        
        dias_cobertura = (estoque_total / sellout_m1) * 30 if sellout_m1 > 0 else 999.0
        
        if dias_cobertura < 15: status = "RISCO RUPTURA"
        elif dias_cobertura > 60: status = "EXCESSO"
        else: status = "SAUDÁVEL"

        # 2. Busca o Histórico de Vendas para o Gráfico (Eixo X)
        query_grafico = text("""
            SELECT mes_ano, SUM(volume_sellout) as volume
            FROM fato_mtrix_historico_mensal
            WHERE sku = :sku AND ciclo_sop = :ciclo
            GROUP BY mes_ano
            ORDER BY mes_ano ASC
        """)
        rows_grafico = db.execute(query_grafico, {"sku": sku, "ciclo": ciclo_atual}).fetchall()
        
        grafico_temporal = [{"mes": r.mes_ano, "sellout_real": float(r.volume), "sellout_ia": None} for r in rows_grafico]
        
        # Anexa a previsão da IA no final do gráfico temporal
        if previsao_ia > 0 and len(grafico_temporal) > 0:
            from datetime import datetime
            from dateutil.relativedelta import relativedelta
            ultimo_mes = datetime.strptime(grafico_temporal[-1]["mes"], "%Y-%m")
            mes_futuro = (ultimo_mes + relativedelta(months=1)).strftime("%Y-%m")
            grafico_temporal.append({"mes": mes_futuro, "sellout_real": None, "sellout_ia": previsao_ia})

        # 3. Raio-X Granular por Distribuidor (Para a Tabela Inferior)
        query_cnpjs = text("""
            SELECT 
                s.cgc,
                c.razaosocial,
                s.estoque_atual_caixas,
                s.sellout_m1_caixas,
                s.dias_cobertura
            FROM fato_mtrix_snapshot s
            LEFT JOIN dim_clientes c ON s.cgc = c.cgc
            WHERE s.sku = :sku AND s.ciclo_sop = :ciclo
            ORDER BY s.estoque_atual_caixas DESC
        """)
        rows_cnpjs = db.execute(query_cnpjs, {"sku": sku, "ciclo": ciclo_atual}).fetchall()
        
        tabela_cnpjs = []
        for r in rows_cnpjs:
            tabela_cnpjs.append({
                "cgc": r.cgc,
                "cliente": r.razaosocial or "Distribuidor Não Cadastrado",
                "estoque_caixas": float(r.estoque_atual_caixas),
                "sellout_m1": float(r.sellout_m1_caixas),
                "dias_cobertura": float(r.dias_cobertura)
            })

        # Montagem do JSON Final Finalizado e Robusto
        return {
            "kpis": {
                "estoque_atual": estoque_total,
                "sellout_m1": sellout_m1,
                "dias_cobertura": dias_cobertura,
                "status_estoque": status,
                "previsao_ia": previsao_ia
            },
            "grafico_temporal": grafico_temporal,
            "distribuidores": tabela_cnpjs
        }

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))