import datetime
from typing import List, Optional
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.models.domain_models import FatoIbpGranular

from app.api.routers.shared_ibp import (
    get_current_cycle,
    registrar_log_auditoria
)

router = APIRouter(prefix="/api/v1/consensus/macro", tags=["Consenso Top-Down"])

class AjusteTopDown(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarTopDown(BaseModel):
    ajustes: List[AjusteTopDown]
    finalizar_etapa: bool = False

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get('funcao') not in ['Administrador', 'Diretoria', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    return usuario

@router.get("/status")
def obter_status_topdown(ciclo: Optional[str] = None, db: Session = Depends(get_db)):
    """Informa o React se a tela está trancada."""
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)
            
        # [VACINA DO CADEADO]: TRIM e LIKE ignoram espaços ocultos gravados no banco
        query = text("SELECT status FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%'")
        trava = db.execute(query, {"c": ciclo}).fetchone()
        
        is_locked = (trava is not None and trava[0].strip().lower() == 'fechado')
        return {"ciclo": ciclo, "isLocked": is_locked, "locked": is_locked}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/grafico")
def obter_grafico_topdown(ciclo: Optional[str] = None, db: Session = Depends(get_db)):
    """Alimenta o gráfico Recharts (Timeline) do topo da tela."""
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)
            
        ciclo_date = datetime.datetime.strptime(ciclo, "%m/%Y")
        hoje = datetime.date.today()

        query_hist = text("""
            SELECT TO_CHAR(data_pedido, 'YYYY-MM-01') as mes, SUM(COALESCE(qt_pedido, 0)) as qtd
            FROM fato_vendas
            WHERE data_pedido >= CURRENT_DATE - INTERVAL '6 months' AND data_pedido < :limite
            GROUP BY TO_CHAR(data_pedido, 'YYYY-MM-01')
        """)
        dados_hist = db.execute(query_hist, {"limite": ciclo_date.date()}).fetchall()

        query_futuro = text("""
            SELECT TO_CHAR(mes_projetado, 'YYYY-MM-01') as mes, 
                   SUM(COALESCE(vol_topdown, 0)) as td, 
                   SUM(COALESCE(vol_ia, 0)) as ia,
                   SUM(COALESCE(vol_meta, 0)) as ant
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo
            GROUP BY TO_CHAR(mes_projetado, 'YYYY-MM-01')
        """)
        dados_futuro = db.execute(query_futuro, {"ciclo": ciclo}).fetchall()

        calendario = defaultdict(lambda: {"Realizado": 0.0, "IA": 0.0, "TopDown": 0.0, "CicloAnterior": 0.0})
        
        for r in dados_hist: calendario[r[0]]["Realizado"] = float(r[1])
        for r in dados_futuro:
            calendario[r[0]]["TopDown"] = float(r[1])
            calendario[r[0]]["IA"] = float(r[2])
            calendario[r[0]]["CicloAnterior"] = float(r[3])

        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        timeline = []
        
        for ms in sorted(calendario.keys()):
            dt = datetime.datetime.strptime(ms, '%Y-%m-%d').date()
            timeline.append({
                "name": f"{meses_pt[dt.month-1]}/{dt.strftime('%y')}",
                "data_iso": ms,
                "Realizado": round(calendario[ms]["Realizado"]) if dt < hoje else None,
                "IA": round(calendario[ms]["IA"]) if dt >= hoje.replace(day=1) else None,
                "Top-Down": round(calendario[ms]["TopDown"]) if dt >= hoje.replace(day=1) else None,
                "CicloAnterior": round(calendario[ms]["CicloAnterior"]) if dt >= hoje.replace(day=1) else None
            })
            
        return timeline
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("")
def obter_visao_topdown(
    ciclo: Optional[str] = None, 
    db: Session = Depends(get_db),
    usuario: dict = Depends(require_admin)
):
    """Gera a árvore da Matriz para o DataGrid do Frontend."""
    nome_usuario = usuario.get('nome') or usuario.get('username') or "Usuário"
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)

        print(f"\n🧭 [TOP-DOWN] Utilizador Executivo '{nome_usuario}' acedeu ao ciclo {ciclo}.")

        query_trava = text("SELECT status FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%'")
        trava = db.execute(query_trava, {"c": ciclo}).fetchone()
        is_locked = (trava is not None and trava[0].strip().lower() == 'fechado')

        # [VACINA DO JOIN]: O LTRIM garante que SKUs como '00123' e '123' cruzem perfeitamente
        query_matriz = text("""
            SELECT 
                f.sku,
                MAX(p.descricao) as descricao,
                MAX(p.categoria) as categoria,
                MAX(p.segmento) as segmento,
                f.mes_projetado,
                SUM(COALESCE(f.vol_topdown, 0)) as vol_td,
                SUM(COALESCE(f.vol_ia, 0)) as vol_ia,
                AVG(COALESCE(f.pmv_aplicado, 0)) as pmv_medio,
                SUM(COALESCE(f.vol_meta, 0)) as vol_anterior
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON ltrim(f.sku::text, '0') = ltrim(p.sku::text, '0')
            WHERE f.ciclo_sop = :ciclo
            GROUP BY f.sku, f.mes_projetado
        """)

        dados_banco = db.execute(query_matriz, {"ciclo": ciclo}).fetchall()
        
        hierarquia = defaultdict(lambda: {"segmentos": defaultdict(lambda: {"skus": defaultdict(lambda: {"descricao": "", "meses": {}})})})
        meses_dinamicos = set()

        for row in dados_banco:
            sku = row[0]
            descricao = row[1] or "Sem Descrição"
            cat = row[2] or "SEM CATEGORIA"
            seg = row[3] or "SEM SEGMENTO"
            mes_date = row[4]
            
            if isinstance(mes_date, str):
                mes_date = datetime.datetime.strptime(mes_date, "%Y-%m-%d").date()
                
            # [VACINA DO INDEFINIDO]: Garantimos a devolução da chave no formato YYYY-MM que o React exige
            mes_str = mes_date.strftime("%Y-%m")
            meses_dinamicos.add(mes_str)

            vol_td = float(row[5] or 0)
            vol_ia = float(row[6] or 0)
            pmv = float(row[7] or 0)

            hierarquia[cat]["segmentos"][seg]["skus"][sku]["descricao"] = descricao
            hierarquia[cat]["segmentos"][seg]["skus"][sku]["meses"][mes_str] = {
                "vol": vol_td,
                "fat": vol_td * pmv,
                "ia": vol_ia,
                "pmv": pmv,
                "orc": vol_ia * pmv * 1.05 
            }

        dados_arvore = []
        for cat_nome, cat_data in hierarquia.items():
            segmentos_list = []
            for seg_nome, seg_data in cat_data["segmentos"].items():
                skus_list = [
                    {"sku": k, "descricao": v["descricao"], "meses": v["meses"]} 
                    for k, v in seg_data["skus"].items()
                ]
                segmentos_list.append({"segmento": seg_nome, "skus": skus_list})
            dados_arvore.append({"categoria": cat_nome, "segmentos": segmentos_list})

        return {
            "isLocked": is_locked,
            "locked": is_locked,
            "mesesColunas": sorted(list(meses_dinamicos)),
            "dados": dados_arvore
        }
    except Exception as e:
        print(f"❌ ERRO MATRIX: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/salvar")
def salvar_ajustes_topdown(
    payload: PayloadAprovarTopDown,
    ciclo: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario: dict = Depends(require_admin)
):
    nome_usuario = usuario.get('nome') or usuario.get('username') or "Usuário"
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)

        query_trava = text("SELECT status FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%'")
        trava = db.execute(query_trava, {"c": ciclo}).fetchone()
        if trava and trava[0].strip().lower() == 'fechado':
            raise HTTPException(status_code=400, detail="Este ciclo já se encontra encerrado.")

        updates_para_banco = []

        for aj in payload.ajustes:
            mes_str_banco = aj.mes_projetado
            if len(mes_str_banco) == 7:
                mes_str_banco += "-01"
                
            novo_volume_macro = aj.novo_volume
            sku = aj.sku
            
            query_linhas = text("""
                WITH Historico AS (
                    SELECT cgc, SUM(COALESCE(qt_pedido, 0)) as vol_hist
                    FROM fato_vendas
                    WHERE sku = :s AND data_pedido >= CURRENT_DATE - INTERVAL '6 months'
                    GROUP BY cgc
                )
                SELECT f.id, COALESCE(h.vol_hist, 0) as vol_hist, COALESCE(f.vol_ia, 0) as vol_ia
                FROM fato_ibp_granular f
                LEFT JOIN Historico h ON f.cgc = h.cgc
                WHERE f.ciclo_sop = :c AND f.sku = :s AND f.mes_projetado = :m
            """)
            
            linhas_sku = db.execute(query_linhas, {"c": ciclo, "s": sku, "m": mes_str_banco}).fetchall()
            
            if not linhas_sku:
                continue 
            
            total_hist = sum(r[1] for r in linhas_sku)
            total_ia = sum(r[2] for r in linhas_sku)
            soma_alocada = 0
            fracoes = []

            if total_hist > 0:
                for r in linhas_sku:
                    cota = (r[1] / total_hist) * novo_volume_macro
                    fracoes.append({"id": r[0], "vol": int(cota), "resto": cota - int(cota)})
                    soma_alocada += int(cota)
            elif total_ia > 0:
                for r in linhas_sku:
                    cota = (r[2] / total_ia) * novo_volume_macro
                    fracoes.append({"id": r[0], "vol": int(cota), "resto": cota - int(cota)})
                    soma_alocada += int(cota)
            else:
                cota = novo_volume_macro / len(linhas_sku)
                for r in linhas_sku:
                    fracoes.append({"id": r[0], "vol": int(cota), "resto": cota - int(cota)})
                    soma_alocada += int(cota)

            faltam = novo_volume_macro - soma_alocada
            fracoes.sort(key=lambda x: x["resto"], reverse=True) 
            for i in range(faltam):
                if i < len(fracoes): fracoes[i]["vol"] += 1 

            for f in fracoes:
                updates_para_banco.append({"id": f["id"], "vol_topdown": f["vol"]})

        if updates_para_banco:
            db.bulk_update_mappings(FatoIbpGranular, updates_para_banco)

        if payload.finalizar_etapa:
            db.execute(text("DELETE FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%'"), {"c": ciclo})
            db.execute(text("INSERT INTO controle_ciclos (ciclo_sop, origem, status, data_fechamento) VALUES (:c, 'Top-Down Arena', 'Fechado', CURRENT_TIMESTAMP)"), {"c": ciclo})
            db.execute(text("UPDATE fato_ibp_granular SET vol_bottomup = vol_topdown, vol_meta = vol_topdown, vol_final = vol_topdown WHERE ciclo_sop = :c"), {"c": ciclo})
            registrar_log_auditoria(db, usuario.get('id', 1), "Finalizar Etapa", f"Ciclo {ciclo} trancado.")
            
        db.commit()
        return {"msg": "Salvo com sucesso!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))