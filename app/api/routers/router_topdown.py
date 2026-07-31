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
    registrar_log_auditoria,
    escrever_volume_rateado,
    propagar_para_jusante,
    check_imutabilidade_mes,
    ETAPA_TOPDOWN, STATUS_CONGELADO
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
        # Nome/status alinhados ao banco (TopDown/CONGELADO). Antes procurava
        # '%Top-Down%'/'fechado' e NUNCA reconhecia o congelamento real, porque
        # o banco grava origem='TopDown' e status='CONGELADO'.
        query = text("""
            SELECT status FROM controle_ciclos
            WHERE TRIM(ciclo_sop) = TRIM(:c) AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
            ORDER BY id DESC
        """)
        trava = db.execute(query, {"c": ciclo, "o": ETAPA_TOPDOWN}).fetchone()
        is_fechado = (trava is not None and str(trava[0]).strip().upper() == STATUS_CONGELADO)
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
        
        hist_where = " v.data_pedido >= :limite - INTERVAL '24 months' AND v.data_pedido < :limite + INTERVAL '1 month' "
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
        ancora_grafico = ciclo_date.date()
        
        for ms in sorted(calendario.keys()):
            dt = datetime.datetime.strptime(ms, '%Y-%m-%d').date()
            timeline.append({
                "name": f"{meses_pt[dt.month-1]}/{dt.strftime('%y')}",
                "data_iso": ms,
                "Realizado": round(calendario[ms]["Realizado"]) if dt <= ancora_grafico else None,
                "IA": round(calendario[ms]["IA"]) if dt >= ancora_grafico else None,
                "TopDown": round(calendario[ms]["TopDown"]) if dt >= ancora_grafico else None, # CORREÇÃO: Chave exata sem hífen
                "CicloAnterior": round(calendario[ms]["CicloAnterior"]) if dt >= ancora_grafico else None
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

        orc_query = db.execute(text("""
            SELECT sku, TO_CHAR(mes_projetado, 'YYYY-MM-01') as mes_banco, SUM(receita_orcamento) as receita_orcamento
            FROM fato_orcamento
            WHERE mes_projetado >= :m2 AND mes_projetado <= :m4
            GROUP BY sku, TO_CHAR(mes_projetado, 'YYYY-MM-01')
        """), {"m2": m2_date, "m4": m4_date}).fetchall()

        orc_dict = {}
        for o in orc_query:
            orc_dict[f"{o.sku}|{o.mes_banco}"] = float(o.receita_orcamento or 0)

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
                
                if v_td > 0:
                    v_pmv = r_td / v_td
                elif v_ia > 0:
                    v_pmv = r_ia / v_ia
                else:
                    v_pmv = float(pmv_fallback)
                
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
    """
    Rateia o volume digitado pela Diretoria em vol_topdown e, ao congelar,
    propaga para as etapas de jusante que AINDA nao publicaram.

    Correcoes aplicadas:
      - Rateio canonico unico (escrever_volume_rateado): a soma gravada e
        EXATAMENTE o total digitado, ou a transacao aborta. Zero digitado zera
        todas as linhas do grupo (antes o rateio manual podia nao fechar e o
        zero era "pulado").
      - Nome/status de lock alinhados ao banco (TopDown/CONGELADO). Antes
        gravava 'Top-Down Arena'/'Fechado', divergente do banco, e por isso a
        propria trava desta etapa nunca reconhecia o congelamento.
      - Propagacao que RESPEITA etapa ja congelada (propagar_para_jusante), no
        lugar do UPDATE cego que sobrescrevia vol_final do ciclo inteiro — a
        causa do Padrao B que corrompeu 05/2026 e 06/2026.
      - Imutabilidade: mes ja realizado nao aceita ajuste (validado dentro de
        escrever_volume_rateado).
    """
    try:
        ciclo = get_current_cycle(db)

        trava = db.execute(text("""
            SELECT status FROM controle_ciclos
            WHERE TRIM(ciclo_sop) = TRIM(:c) AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
        """), {"c": ciclo, "o": ETAPA_TOPDOWN}).fetchone()
        if trava and str(trava[0]).strip().upper() == STATUS_CONGELADO:
            raise HTTPException(status_code=400, detail="Este ciclo já se encontra encerrado.")

        total_linhas = 0
        for aj in payload.ajustes:
            mes_banco = aj.mes_projetado
            if len(mes_banco) == 7:
                mes_banco += "-01"

            # Escopo completo do grupo + trava de balanco + zero explicito.
            # Peso = vol_ia (o share historico ja foi rateado no nascimento do
            # ciclo pelo distributor). O total digitado e sempre respeitado.
            total_linhas += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=mes_banco,
                volume_alvo=int(aj.novo_volume), campo_destino="vol_topdown",
                campos_peso=["vol_ia"],
            )

        if finalizar:
            # Nome/status corretos (antes 'Top-Down Arena'/'Fechado').
            db.execute(text("""
                DELETE FROM controle_ciclos
                WHERE TRIM(ciclo_sop) = TRIM(:c) AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
            """), {"c": ciclo, "o": ETAPA_TOPDOWN})
            db.execute(text("""
                INSERT INTO controle_ciclos (ciclo_sop, origem, status, data_fechamento)
                VALUES (:c, :o, :st, CURRENT_TIMESTAMP)
            """), {"c": ciclo, "o": ETAPA_TOPDOWN, "st": STATUS_CONGELADO})

            # Propagacao segura: so para etapas de jusante NAO congeladas e so
            # em meses >= mes corrente. Substitui o UPDATE cego do ciclo inteiro.
            propagar_para_jusante(db, ciclo, ETAPA_TOPDOWN, "vol_topdown")

        db.commit()
        return {"msg": "Operação concluída com sucesso.", "linhas_rateadas": total_linhas}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))