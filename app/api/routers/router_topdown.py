import datetime
import math
from typing import List, Optional
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas, DimCliente
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe,
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
    if usuario['funcao'] not in ['Administrador', 'Diretoria', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria e Marketing.")
    return usuario

@router.get("/status")
async def obter_status_topdown(db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)
        td_reg = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo, 
            ControleCiclo.origem == 'Top-Down Arena'
        ).first()
        is_fechado = td_reg.status == 'Fechado' if td_reg else False
        return {"status": "success", "fechado": is_fechado}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("")
async def listar_topdown(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)
        
        td_reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
        is_topdown_fechado = True if (td_reg and td_reg.status == 'Fechado') else False
        
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        # 🔥 BLINDAGEM PMV (SSOT): A Receita TD e IA são calculadas pelo banco lendo ESTRITAMENTE o pmv_aplicado cravado pelo Pipeline
        q_port = get_truth_query(db, ciclo, data_ini, data_fim)
        resultados_port = q_port.with_entities(
            DimProduto.categoria.label('cat'), DimProduto.segmento.label('seg'),
            FatoIbpGranular.sku, DimProduto.descricao.label('prod_desc'),
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_avg')
        ).group_by(DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all()

        def criar_meses():
            return {"vol_ia":0, "receita_ia":0, "vol_td":0, "receita_td":0, "vol_meta_real":0, "receita_simulada":0, "pmv":0.0}

        arvore_port = {}
        for p in db.query(DimProduto).all():
            cat, seg, sk, de = p.categoria or 'SEM CATEGORIA', p.segmento or 'SEM SEGMENTO', p.sku, p.descricao or 'SEM NOME'
            if cat not in arvore_port:
                arvore_port[cat] = {"nome": cat, "tipo": "categoria", "meses": {m: criar_meses() for m in meses_alvo}, "segmentos": {}}
            if seg not in arvore_port[cat]["segmentos"]:
                arvore_port[cat]["segmentos"][seg] = {"nome": seg, "tipo": "segmento", "meses": {m: criar_meses() for m in meses_alvo}, "produtos": {}}
            if sk not in arvore_port[cat]["segmentos"][seg]["produtos"]:
                arvore_port[cat]["segmentos"][seg]["produtos"][sk] = {"id": sk, "nome": de, "produto": sk, "tipo": "produto", "meses": {m: criar_meses() for m in meses_alvo}}

        for r in resultados_port:
            cat, seg, sk, ms = r.cat or 'SEM CATEGORIA', r.seg or 'SEM SEGMENTO', r.sku, str(r.mes_projetado)
            if ms in meses_alvo:
                v_ia, r_ia = int(r.v_ia or 0), float(r.rec_ia or 0)
                v_td, r_td = int(r.v_td or 0), float(r.rec_td or 0)
                pmv_avg = float(r.pmv_avg or 0)

                # A visão do Top-Down parte da IA (Baseline). Se já foi editado, mostra o vol_topdown.
                vol_exibicao_macro = v_td if v_td > 0 else v_ia

                for nivel in [arvore_port[cat]["meses"][ms], arvore_port[cat]["segmentos"][seg]["meses"][ms], arvore_port[cat]["segmentos"][seg]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_ia"] += v_ia
                    nivel["receita_ia"] += r_ia
                    nivel["vol_td"] += v_td
                    nivel["receita_td"] += r_td
                    nivel["vol_meta_real"] += vol_exibicao_macro
                    nivel["receita_simulada"] += (vol_exibicao_macro * pmv_avg) # Base aproximada para níveis macro
                    
                    if nivel["vol_td"] > 0:
                        nivel["pmv"] = nivel["receita_td"] / nivel["vol_td"]
                    elif nivel["vol_ia"] > 0:
                        nivel["pmv"] = nivel["receita_ia"] / nivel["vol_ia"]
                    else:
                        nivel["pmv"] = pmv_avg

        final_portfolio = []
        meses_nomes = {'01':'Jan', '02':'Fev', '03':'Mar', '04':'Abr', '05':'Mai', '06':'Jun', '07':'Jul', '08':'Ago', '09':'Set', '10':'Out', '11':'Nov', '12':'Dez'}
        
        for cat_k, cat_v in arvore_port.items():
            segs = []
            for seg_k, seg_v in cat_v["segmentos"].items():
                prods = [{"id": p["id"], "nome": p["nome"], "produto": p["produto"], "tipo": "produto", "meses": [{"mes_banco": k, "mes_str": f"{meses_nomes.get(k.split('-')[1], k.split('-')[1])}/{k.split('-')[0][2:]}", **v} for k,v in p["meses"].items()]} for p in seg_v["produtos"].values()]
                segs.append({"id": f"{cat_k}|{seg_k}", "nome": seg_k, "tipo": "segmento", "meses": [{"mes_banco": k, "mes_str": f"{meses_nomes.get(k.split('-')[1], k.split('-')[1])}/{k.split('-')[0][2:]}", **v} for k,v in seg_v["meses"].items()], "subRows": sorted(prods, key=lambda x: x["nome"])})
            final_portfolio.append({"id": cat_k, "nome": cat_k, "tipo": "categoria", "meses": [{"mes_banco": k, "mes_str": f"{meses_nomes.get(k.split('-')[1], k.split('-')[1])}/{k.split('-')[0][2:]}", **v} for k,v in cat_v["meses"].items()], "subRows": sorted(segs, key=lambda x: x["nome"])})

        return {
            "status": "success", 
            "is_topdown_fechado": is_topdown_fechado, 
            "dados": {"portfolio": sorted(final_portfolio, key=lambda x: x["nome"])}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=repr(e))

@router.post("/salvar")
async def salvar_rascunho_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
        if registro and registro.status == 'Fechado':
            raise HTTPException(423, "Etapa Top-Down já está encerrada.")

        if not payload.ajustes:
            return {"status": "success", "message": "Nenhuma alteração enviada."}

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            
            linhas = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == ajuste.sku).all()
            if not linhas: continue

            # A distribuição macro Top-Down usa a Inteligência Artificial (vol_ia) como peso para ratear aos clientes
            total_ia_antigo = sum([float(l.vol_ia or 0) for l in linhas])
            soma_dist = 0
            volume_alvo = int(ajuste.novo_volume)
            total_clientes = len(linhas)
            
            for i, l in enumerate(linhas):
                if i == total_clientes - 1:
                    rateado = volume_alvo - soma_dist
                else:
                    peso = float(l.vol_ia or 0) / total_ia_antigo if total_ia_antigo > 0 else 1.0 / total_clientes
                    rateado = int(round(volume_alvo * peso))
                    soma_dist += rateado
                
                # 🔥 Apenas o volume Top-Down é gravado! PMV não é tocado.
                l.vol_topdown = rateado

        if payload.finalizar_etapa:
            if not registro: 
                db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down Arena', status='Fechado'))
            else: 
                registro.status = 'Fechado'

        db.commit()
        return {"status": "success", "message": "Proposta S&OP Top-Down salva com sucesso!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=repr(e))

@router.get("/grafico")
async def grafico_topdown(produto_id: str, db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        _mes_str, _ano_str = ciclo_atual.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        
        inicio_projeto = datetime.date(2026, 4, 1)
        inicio_hist = hoje - relativedelta(months=24)
        m2_comercial = hoje + relativedelta(months=2)

        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            mes_str = curr.strftime('%Y-%m')
            calendario[mes_str] = {"Realizado": None, "IA": None, "CicloAnterior": None, "Topdown": None}
            curr += relativedelta(months=1)

        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).filter(FatoVendas.data_pedido >= inicio_hist, FatoVendas.sku == produto_id)

        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario:
                calendario[row.mes_ano]["Realizado"] = int(row.realizado or 0)

        q_proj = db.query(
            func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes_ano'),
            FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('td'),
            func.sum(FatoIbpGranular.vol_final).label('final')
        ).filter(FatoIbpGranular.sku == produto_id)

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {
                "ia": int(row.ia or 0), 
                "td": int(row.td or 0),
                "final": int(row.final or 0)
            }

        ciclo_base_zero = datetime.datetime.strptime('04/2026', '%m/%Y').date()
        ciclo_atual_dt = datetime.datetime.strptime(ciclo_atual, '%m/%Y').date()

        for ms in calendario.keys():
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            if ms not in proj_por_mes: continue

            if mes_dt >= m2_comercial:
                if ciclo_atual in proj_por_mes[ms]:
                    calendario[ms]["IA"] = proj_por_mes[ms][ciclo_atual]["ia"]
                    calendario[ms]["Topdown"] = proj_por_mes[ms][ciclo_atual]["td"]
                
                if ciclo_anterior in proj_por_mes[ms]:
                    calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_anterior]["final"]
            else:
                if mes_dt >= inicio_projeto:
                    alvo_ia_dt = mes_dt - relativedelta(months=2)
                    if alvo_ia_dt >= inicio_projeto:
                        ciclo_ia_str = f"{alvo_ia_dt.month:02d}/{alvo_ia_dt.year}"
                        
                        if ciclo_ia_str in proj_por_mes[ms]:
                            calendario[ms]["IA"] = proj_por_mes[ms][ciclo_ia_str]["ia"]
                            calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_ia_str]["final"]
                    else:
                        ciclo_origem_dt = inicio_projeto
                        ciclo_origem_str = f"{ciclo_origem_dt.month:02d}/{ciclo_origem_dt.year}"
                        
                        if ciclo_origem_str in proj_por_mes[ms]:
                            calendario[ms]["IA"] = proj_por_mes[ms][ciclo_origem_str]["ia"]
                            calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_origem_str]["final"]

        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            mostrar_ia_lag = mes_dt >= inicio_projeto
            
            nome_formatado = f"{meses_pt[mes_dt.month - 1]}/{mes_dt.strftime('%y')}"
            
            timeline.append({
                "name": nome_formatado,
                "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt >= hoje else (v["Realizado"] or 0),
                "IA": v["IA"] if mostrar_ia_lag else None,
                "CicloAnterior": v["CicloAnterior"] if mostrar_ia_lag else None,
                "Topdown": v["Topdown"] if mes_dt >= m2_comercial else None
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))