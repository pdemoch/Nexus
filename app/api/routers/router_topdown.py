import datetime
from dateutil.relativedelta import relativedelta
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
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria.")
    return usuario


@router.get("/status")
def obter_status_topdown(ciclo: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        if not ciclo: ciclo = get_current_cycle(db)
        query = text("SELECT status FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%' ORDER BY id DESC")
        trava = db.execute(query, {"c": ciclo}).fetchone()
        is_fechado = (trava is not None and trava[0].strip().lower() == 'fechado')
        return {"is_fechado": is_fechado}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/grafico")
def obter_grafico_topdown(
    chave_matriz: str, 
    nivel_hierarquia: str, 
    ciclo: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    try:
        if not ciclo: ciclo = get_current_cycle(db)
        ciclo_date = datetime.datetime.strptime(ciclo, "%m/%Y")
        hoje = datetime.date.today()

        hist_where = " v.data_pedido >= :limite - INTERVAL '24 months' AND v.data_pedido < :limite "
        futuro_where = " f.ciclo_sop = :ciclo "
        params = {"ciclo": ciclo, "limite": ciclo_date.date()}

        if nivel_hierarquia == 'categoria':
            hist_where += " AND p.categoria = :cat "
            futuro_where += " AND p.categoria = :cat "
            params["cat"] = chave_matriz
        elif nivel_hierarquia == 'segmento':
            parts = chave_matriz.split('|')
            hist_where += " AND p.categoria = :cat AND p.segmento = :seg "
            futuro_where += " AND p.categoria = :cat AND p.segmento = :seg "
            params["cat"] = parts[0]
            params["seg"] = parts[1]
        elif nivel_hierarquia == 'produto':
            sku = chave_matriz.split('|')[-1]
            hist_where += " AND p.sku = :sku "
            futuro_where += " AND f.sku = :sku "
            params["sku"] = sku

        query_hist = text(f"""
            SELECT TO_CHAR(v.data_pedido, 'YYYY-MM-01') as mes, SUM(COALESCE(v.qt_pedido, 0)) as qtd
            FROM fato_vendas v
            LEFT JOIN dim_produtos p ON ltrim(v.sku::text, '0') = ltrim(p.sku::text, '0')
            WHERE {hist_where}
            GROUP BY TO_CHAR(v.data_pedido, 'YYYY-MM-01')
        """)
        dados_hist = db.execute(query_hist, params).fetchall()

        query_futuro = text(f"""
            SELECT TO_CHAR(f.mes_projetado, 'YYYY-MM-01') as mes, 
                   SUM(COALESCE(f.vol_topdown, 0)) as td, SUM(COALESCE(f.vol_ia, 0)) as ia, SUM(COALESCE(f.vol_meta, 0)) as ant
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON ltrim(f.sku::text, '0') = ltrim(p.sku::text, '0')
            WHERE {futuro_where}
            GROUP BY TO_CHAR(f.mes_projetado, 'YYYY-MM-01')
        """)
        dados_futuro = db.execute(query_futuro, params).fetchall()

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
        return {"dados": timeline}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def obter_visao_topdown(ciclo: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        if not ciclo: ciclo = get_current_cycle(db)

        ciclo_date = datetime.datetime.strptime(ciclo, "%m/%Y")
        
        m2_date = (ciclo_date + relativedelta(months=2)).date()
        m4_date = (ciclo_date + relativedelta(months=4)).date()

        # FIX 1: Buscar o Orçamento Genuíno na tabela fato_orcamento (Igual ao Dashboard)
        orc_query = db.execute(text("""
            SELECT sku, TO_CHAR(mes_projetado, 'YYYY-MM-01') as mes_banco, SUM(receita_orcamento) as receita_orcamento
            FROM fato_orcamento
            WHERE mes_projetado >= :m2 AND mes_projetado <= :m4
            GROUP BY sku, TO_CHAR(mes_projetado, 'YYYY-MM-01')
        """), {"m2": m2_date, "m4": m4_date}).fetchall()

        orc_dict = {}
        for o in orc_query:
            orc_dict[f"{o.sku}|{o.mes_banco}"] = float(o.receita_orcamento or 0)

        # FIX 2: Cálculo Matemático Preciso do Faturamento Linha-a-Linha no SQL
        query_matriz = text("""
            SELECT 
                f.sku, MAX(p.descricao) as descricao, MAX(p.categoria) as categoria, MAX(p.segmento) as segmento,
                TO_CHAR(f.mes_projetado, 'YYYY-MM-01') as mes_banco,
                SUM(COALESCE(f.vol_topdown, 0)) as vol_td,
                SUM(COALESCE(f.vol_ia, 0)) as vol_ia,
                SUM(COALESCE(f.vol_meta, 0)) as vol_ant,
                SUM(COALESCE(f.vol_topdown, 0) * COALESCE(f.pmv_aplicado, 0)) as rec_td,
                SUM(COALESCE(f.vol_ia, 0) * COALESCE(f.pmv_aplicado, 0)) as rec_ia,
                AVG(COALESCE(f.pmv_aplicado, 0)) as pmv_fallback
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON ltrim(f.sku::text, '0') = ltrim(p.sku::text, '0')
            WHERE f.ciclo_sop = :ciclo
            GROUP BY f.sku, TO_CHAR(f.mes_projetado, 'YYYY-MM-01')
        """)
        dados_banco = db.execute(query_matriz, {"ciclo": ciclo}).fetchall()
        
        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        meses_unicos = {}
        tree = {}

        for row in dados_banco:
            sku, desc, cat, seg, mes_banco, vol_td, vol_ia, vol_ant, rec_td, rec_ia, pmv_fallback = row
            cat = cat or "Sem Categoria"
            seg = seg or "Sem Segmento"
            desc = desc or "Sem Descrição"

            mes_date = datetime.datetime.strptime(mes_banco, '%Y-%m-%d').date()

            if m2_date <= mes_date <= m4_date:
                meses_unicos[mes_banco] = f"{meses_pt[mes_date.month-1]}/{mes_date.strftime('%y')}"

                if cat not in tree:
                    tree[cat] = {
                        "chave_matriz": cat, "nome": cat, "tipo": "categoria", "subRows": {}, 
                        "meses_dict": defaultdict(lambda: {"vol_ia": 0.0, "vol_anterior": 0.0, "receita_orcamento": 0.0, "vol_ajustado": 0.0, "fat": 0.0})
                    }
                if seg not in tree[cat]["subRows"]:
                    tree[cat]["subRows"][seg] = {
                        "chave_matriz": f"{cat}|{seg}", "nome": seg, "tipo": "segmento", "subRows": {},
                        "meses_dict": defaultdict(lambda: {"vol_ia": 0.0, "vol_anterior": 0.0, "receita_orcamento": 0.0, "vol_ajustado": 0.0, "fat": 0.0})
                    }
                if sku not in tree[cat]["subRows"][seg]["subRows"]:
                    tree[cat]["subRows"][seg]["subRows"][sku] = {
                        "chave_matriz": f"{cat}|{seg}|{sku}", "nome": desc, "produto": sku, 
                        "tipo": "produto", "meses": []
                    }

                v_td = float(vol_td)
                v_ia = float(vol_ia)
                v_ant = float(vol_ant)
                r_td = float(rec_td)
                r_ia = float(rec_ia)
                
                # PMV calculado pela ponderação financeira real, eliminando desvios decimais
                if v_td > 0:
                    v_pmv = r_td / v_td
                elif v_ia > 0:
                    v_pmv = r_ia / v_ia
                else:
                    v_pmv = float(pmv_fallback)
                
                # Consumo do Orçamento Real da Base
                sku_orcamento = orc_dict.get(f"{sku}|{mes_banco}", 0.0)
                sku_fat = r_td

                tree[cat]["subRows"][seg]["subRows"][sku]["meses"].append({
                    "mes_banco": mes_banco, "mes_str": meses_unicos[mes_banco],
                    "vol_ajustado": v_td, "vol_ia": v_ia, "vol_anterior": v_ant,
                    "pmv": v_pmv, "receita_orcamento": sku_orcamento
                })

                c_agg = tree[cat]["meses_dict"][mes_banco]
                c_agg["vol_ia"] += v_ia
                c_agg["vol_anterior"] += v_ant
                c_agg["receita_orcamento"] += sku_orcamento
                c_agg["vol_ajustado"] += v_td
                c_agg["fat"] += sku_fat

                s_agg = tree[cat]["subRows"][seg]["meses_dict"][mes_banco]
                s_agg["vol_ia"] += v_ia
                s_agg["vol_anterior"] += v_ant
                s_agg["receita_orcamento"] += sku_orcamento
                s_agg["vol_ajustado"] += v_td
                s_agg["fat"] += sku_fat

        dados_arvore = []
        for cat_key, cat_node in tree.items():
            cat_meses = []
            for m_banco in sorted(meses_unicos.keys()):
                agg = cat_node["meses_dict"][m_banco]
                pmv_agg = agg["fat"] / agg["vol_ajustado"] if agg["vol_ajustado"] > 0 else 0.0
                cat_meses.append({
                    "mes_banco": m_banco, "mes_str": meses_unicos[m_banco],
                    "vol_ajustado": agg["vol_ajustado"], "vol_ia": agg["vol_ia"], "vol_anterior": agg["vol_anterior"],
                    "pmv": pmv_agg, "receita_orcamento": agg["receita_orcamento"]
                })
            cat_node["meses"] = cat_meses
            cat_node.pop("meses_dict", None)

            seg_list = []
            for seg_key, seg_node in cat_node["subRows"].items():
                seg_meses = []
                for m_banco in sorted(meses_unicos.keys()):
                    agg = seg_node["meses_dict"][m_banco]
                    pmv_agg = agg["fat"] / agg["vol_ajustado"] if agg["vol_ajustado"] > 0 else 0.0
                    seg_meses.append({
                        "mes_banco": m_banco, "mes_str": meses_unicos[m_banco],
                        "vol_ajustado": agg["vol_ajustado"], "vol_ia": agg["vol_ia"], "vol_anterior": agg["vol_anterior"],
                        "pmv": pmv_agg, "receita_orcamento": agg["receita_orcamento"]
                    })
                seg_node["meses"] = seg_meses
                seg_node.pop("meses_dict", None)
                seg_node["subRows"] = list(seg_node["subRows"].values())
                seg_list.append(seg_node)

            cat_node["subRows"] = seg_list
            dados_arvore.append(cat_node)

        return {"dados": dados_arvore}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/salvar")
def salvar_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db)):
    return executar_rateio_e_salvar(payload, db, finalizar=False)

@router.post("/congelar")
def congelar_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    return executar_rateio_e_salvar(payload, db, finalizar=True, user_id=usuario.get('id', 1))

def executar_rateio_e_salvar(payload, db, finalizar: bool, user_id: int = 1):
    try:
        ciclo = get_current_cycle(db)
        
        trava = db.execute(text("SELECT status FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%'"), {"c": ciclo}).fetchone()
        if trava and trava[0].strip().lower() == 'fechado':
            raise HTTPException(status_code=400, detail="Este ciclo já se encontra encerrado.")

        updates_para_banco = []

        for aj in payload.ajustes:
            mes_banco = aj.mes_projetado
            if len(mes_banco) == 7: mes_banco += "-01"
                
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
            linhas = db.execute(query_linhas, {"c": ciclo, "s": aj.sku, "m": mes_banco}).fetchall()
            if not linhas: continue 
            
            t_hist = sum(r[1] for r in linhas)
            t_ia = sum(r[2] for r in linhas)
            soma_alocada = 0
            fracoes = []

            for r in linhas:
                if t_hist > 0: cota = (r[1] / t_hist) * aj.novo_volume
                elif t_ia > 0: cota = (r[2] / t_ia) * aj.novo_volume
                else: cota = aj.novo_volume / len(linhas)
                
                fracoes.append({"id": r[0], "vol": int(cota), "resto": cota - int(cota)})
                soma_alocada += int(cota)

            fracoes.sort(key=lambda x: x["resto"], reverse=True) 
            for i in range(aj.novo_volume - soma_alocada): 
                if i < len(fracoes): fracoes[i]["vol"] += 1 

            for f in fracoes: updates_para_banco.append({"id": f["id"], "vol_topdown": f["vol"]})

        if updates_para_banco:
            db.bulk_update_mappings(FatoIbpGranular, updates_para_banco)

        if finalizar:
            db.execute(text("DELETE FROM controle_ciclos WHERE TRIM(ciclo_sop) = TRIM(:c) AND origem LIKE '%Top-Down%'"), {"c": ciclo})
            db.execute(text("INSERT INTO controle_ciclos (ciclo_sop, origem, status, data_fechamento) VALUES (:c, 'Top-Down Arena', 'Fechado', CURRENT_TIMESTAMP)"), {"c": ciclo})
            db.execute(text("UPDATE fato_ibp_granular SET vol_bottomup = vol_topdown, vol_meta = vol_topdown, vol_final = vol_topdown WHERE ciclo_sop = :c"), {"c": ciclo})
            registrar_log_auditoria(db, user_id, "Finalizar Etapa", f"Ciclo {ciclo} trancado (Top-Down).")
            
        db.commit()
        return {"msg": "Operação concluída com sucesso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))