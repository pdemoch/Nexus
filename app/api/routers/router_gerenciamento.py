import datetime
import math
from typing import List
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe,
    ratear_maior_resto,
    propagar_para_jusante,
    check_imutabilidade_mes,
    ETAPA_TOPDOWN, ETAPA_BOTTOMUP, STATUS_CONGELADO
)

router = APIRouter(prefix="/api/v1/consensus/gerenciamento", tags=["Consenso Gerenciamento"])

class AjusteGerente(BaseModel):
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGerente(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteGerente]

def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gerência Comercial.")
    return usuario

def check_topdown_lock(db: Session, ciclo: str):
    # A etapa anterior (TopDown) precisa estar CONGELADA para liberar o Bottom-Up.
    # Antes procurava origem='Top-Down Arena' e status='Fechado' — nomes que o
    # banco nao usa (banco: 'TopDown'/'CONGELADO'). O resultado era que o
    # check NUNCA encontrava o lock e bloqueava o Bottom-Up permanentemente.
    td = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        func.upper(func.trim(ControleCiclo.origem)) == ETAPA_TOPDOWN.upper()
    ).first()
    if not td or str(td.status).strip().upper() != STATUS_CONGELADO:
        raise HTTPException(
            status_code=403, 
            detail="Fase Comercial Bloqueada: A Diretoria ainda não liberou o ciclo para a modelagem Bottom-Up comercial."
        )

def check_demand_lock(db: Session, ciclo: str):
    # A propria etapa Bottom-Up (banco: 'BottomUP'/'CONGELADO').
    dr = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        func.upper(func.trim(ControleCiclo.origem)) == ETAPA_BOTTOMUP.upper()
    ).first()
    if dr and str(dr.status).strip().upper() == STATUS_CONGELADO:
        raise HTTPException(
            status_code=403, 
            detail="Ciclo Fechado: O portfólio Bottom-Up já foi congelado e enviado para a etapa de Supply."
        )

@router.get("/status")
async def obter_status_gerenciamento(db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)
        tranca_ger = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo,
            func.upper(func.trim(ControleCiclo.origem)) == ETAPA_BOTTOMUP.upper()
        ).first()
        is_fechado = str(tranca_ger.status).strip().upper() == STATUS_CONGELADO if tranca_ger else False
        return {"status": "success", "fechado": is_fechado}
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.get("")
async def listar_gerenciamento(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)

        td_reg = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo,
            func.upper(func.trim(ControleCiclo.origem)) == ETAPA_TOPDOWN.upper()
        ).first()
        is_topdown_fechado = True if (td_reg and str(td_reg.status).strip().upper() == STATUS_CONGELADO) else False

        dr_reg = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo,
            func.upper(func.trim(ControleCiclo.origem)) == ETAPA_BOTTOMUP.upper()
        ).first()
        is_demand_fechado = True if (dr_reg and str(dr_reg.status).strip().upper() == STATUS_CONGELADO) else False

        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        data_hist_inicio = hoje - relativedelta(months=4)
        q_peso = db.query(FatoVendas.sku, func.sum(FatoVendas.qt_pedido).label('v')).filter(FatoVendas.data_pedido >= data_hist_inicio).group_by(FatoVendas.sku).all()
        peso_hist_dict = {str(r.sku).strip(): int(r.v or 0) for r in q_peso}

        orc_query = db.execute(text("""
            SELECT sku, mes_projetado, receita_orcamento 
            FROM fato_orcamento 
            WHERE mes_projetado >= :m2 AND mes_projetado <= :m4
        """), {"m2": data_ini, "m4": data_fim}).fetchall()
        
        orc_dict = {}
        for o in orc_query:
            orc_dict[f"{o.sku}|{o.mes_projetado}"] = float(o.receita_orcamento or 0)

        q_port = get_truth_query(db, ciclo, data_ini, data_fim)
        
        # 🔥 BLINDAGEM PMV (SSOT): A Receita é calculada pelo banco lendo ESTRITAMENTE o pmv_aplicado cravado pelo Pipeline
        resultados_port = q_port.with_entities(
            DimProduto.categoria.label('cat'), DimProduto.segmento.label('seg'),
            FatoIbpGranular.sku, DimProduto.descricao.label('prod_desc'),
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'),
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_avg')
        ).group_by(DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all()

        def criar_meses():
            return {"vol_td":0, "receita_td":0, "vol_bu":0, "receita_bu":0, "vol_ia":0, "receita_ia":0, "receita_meta":0, "pmv":0.0}

        arvore_port = {}
        for p in db.query(DimProduto).all():
            cat, seg, sk, de = p.categoria or 'SEM CATEGORIA', p.segmento or 'SEM SEGMENTO', p.sku, p.descricao or 'SEM NOME'
            if cat not in arvore_port:
                arvore_port[cat] = {"nome": cat, "tipo": "categoria", "meses": {m: criar_meses() for m in meses_alvo}, "segmentos": {}}
            if seg not in arvore_port[cat]["segmentos"]:
                arvore_port[cat]["segmentos"][seg] = {"nome": seg, "tipo": "segmento", "meses": {m: criar_meses() for m in meses_alvo}, "produtos": {}}
            if sk not in arvore_port[cat]["segmentos"][seg]["produtos"]:
                arvore_port[cat]["segmentos"][seg]["produtos"][sk] = {"id": sk, "nome": de, "produto": sk, "tipo": "produto", "meses": {m: criar_meses() for m in meses_alvo}, "peso_hist": peso_hist_dict.get(sk, 0)}

        for r in resultados_port:
            cat, seg, sk, ms = r.cat or 'SEM CATEGORIA', r.seg or 'SEM SEGMENTO', r.sku, str(r.mes_projetado)
            if ms in meses_alvo:
                v_td, r_td = int(r.v_td or 0), float(r.rec_td or 0)
                v_bu, r_bu = int(r.v_bu or 0), float(r.rec_bu or 0)
                v_ia, r_ia = int(r.v_ia or 0), float(r.rec_ia or 0)
                pmv_avg = float(r.pmv_avg or 0)
                
                v_orc_rec = orc_dict.get(f"{sk}|{ms}", 0.0)

                for nivel in [arvore_port[cat]["meses"][ms], arvore_port[cat]["segmentos"][seg]["meses"][ms], arvore_port[cat]["segmentos"][seg]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_td"] += v_td
                    nivel["receita_td"] += r_td
                    nivel["vol_bu"] += v_bu
                    nivel["receita_bu"] += r_bu
                    nivel["vol_ia"] += v_ia
                    nivel["receita_ia"] += r_ia
                    nivel["receita_meta"] += v_orc_rec
                    
                    # Agregação Visual do Preço Médio Ponderado para as Categorias/Segmentos
                    if nivel["vol_bu"] > 0:
                        nivel["pmv"] = nivel["receita_bu"] / nivel["vol_bu"]
                    elif nivel["vol_td"] > 0:
                        nivel["pmv"] = nivel["receita_td"] / nivel["vol_td"]
                    else:
                        nivel["pmv"] = pmv_avg

        final_portfolio = []
        meses_nomes = {'01':'Jan', '02':'Fev', '03':'Mar', '04':'Abr', '05':'Mai', '06':'Jun', '07':'Jul', '08':'Ago', '09':'Set', '10':'Out', '11':'Nov', '12':'Dez'}
        
        for cat_k, cat_v in arvore_port.items():
            segs = []
            for seg_k, seg_v in cat_v["segmentos"].items():
                prods = [{"id": p["id"], "nome": p["nome"], "produto": p["produto"], "tipo": "produto", "peso_hist": p["peso_hist"], "meses": [{"mes_banco": k, "mes_str": f"{meses_nomes.get(k.split('-')[1], k.split('-')[1])}/{k.split('-')[0][2:]}", **v} for k,v in p["meses"].items()]} for p in seg_v["produtos"].values()]
                segs.append({"id": f"{cat_k}|{seg_k}", "nome": seg_k, "tipo": "segmento", "meses": [{"mes_banco": k, "mes_str": f"{meses_nomes.get(k.split('-')[1], k.split('-')[1])}/{k.split('-')[0][2:]}", **v} for k,v in seg_v["meses"].items()], "subRows": sorted(prods, key=lambda x: x["nome"])})
            final_portfolio.append({"id": cat_k, "nome": cat_k, "tipo": "categoria", "meses": [{"mes_banco": k, "mes_str": f"{meses_nomes.get(k.split('-')[1], k.split('-')[1])}/{k.split('-')[0][2:]}", **v} for k,v in cat_v["meses"].items()], "subRows": sorted(segs, key=lambda x: x["nome"])})

        return {
            "status": "success", 
            "is_topdown_fechado": is_topdown_fechado, 
            "is_demand_fechado": is_demand_fechado, 
            "dados": {"portfolio": sorted(final_portfolio, key=lambda x: x["nome"])}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=repr(e))


@router.post("/salvar")
async def salvar_rascunho_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    """
    Ajusta vol_bottomup por categoria / segmento / SKU e, ao PUBLICAR, propaga
    para as etapas de jusante nao congeladas.

    Correcoes:
      - Rateio canonico (ratear_maior_resto): a soma gravada e EXATAMENTE o
        volume digitado, garantido por assertiva que aborta a transacao. Zero
        digitado zera todas as linhas do grupo (antes o round acumulado jogava
        a sobra no ultimo cliente e o zero podia nao zerar).
      - Imutabilidade: mes ja realizado nao aceita ajuste.
      - Nomes de lock alinhados ao banco (TopDown/BottomUP/CONGELADO).
      - Propagacao segura ao publicar (antes o Bottom-Up nao propagava nada).
    """
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        check_topdown_lock(db, ciclo)
        check_demand_lock(db, ciclo)

        hoje = datetime.date.today()
        data_hist = hoje - relativedelta(months=4)

        if not payload.ajustes:
            return {"status": "success", "message": "Nenhuma alteração enviada."}

        q_vendas = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('v')).filter(FatoVendas.data_pedido >= data_hist).group_by(FatoVendas.sku, FatoVendas.cgc).all()
        peso_hist = {(r.sku, r.cgc): int(r.v or 0) for r in q_vendas}

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)

            # Passado e imutavel — nem publicacao reescreve mes fechado.
            check_imutabilidade_mes(data_alvo, ajuste.chave, contexto="Bottom-Up")

            partes = ajuste.chave.split('|')
            q = get_truth_query(db, ciclo, data_alvo, data_alvo)
            
            if len(partes) == 1:
                q = q.filter(DimProduto.categoria == partes[0])
            elif len(partes) == 2:
                q = q.filter(DimProduto.categoria == partes[0], DimProduto.segmento == partes[1])
            else:
                q = q.filter(FatoIbpGranular.sku == ajuste.chave)
                
            linhas = q.all()
            if not linhas: continue

            vol_alvo = int(ajuste.novo_volume)

            # Peso: primeiro vol_topdown (etapa anterior); na falta, historico de
            # venda; por ultimo, igualitario. ratear_maior_resto faz o resto.
            pesos_td = [max(0.0, float(l.vol_topdown or 0)) for l in linhas]
            if sum(pesos_td) > 0:
                pesos = pesos_td
            else:
                pesos_h = [float(peso_hist.get((l.sku, l.cgc), 0)) for l in linhas]
                pesos = pesos_h if sum(pesos_h) > 0 else [1.0] * len(linhas)

            partes_vol = ratear_maior_resto(vol_alvo, pesos)

            if sum(partes_vol) != vol_alvo:
                raise HTTPException(
                    status_code=500,
                    detail=(f"Falha de balanço no Bottom-Up da chave {ajuste.chave} "
                            f"em {data_alvo.strftime('%m/%Y')}: digitado {vol_alvo}, "
                            f"rateado {sum(partes_vol)}.")
                )

            # 🔥 BLINDAGEM PMV (SSOT): grava APENAS O VOLUME (vol_bottomup). Zero
            # e gravado explicitamente (nao pulado). O PMV jamais e tocado.
            for l, parte in zip(linhas, partes_vol):
                l.vol_bottomup = int(parte)

        if payload.origem_ajuste == 'PUBLICAR':
            registro = db.query(ControleCiclo).filter(
                ControleCiclo.ciclo_sop == ciclo,
                func.upper(func.trim(ControleCiclo.origem)) == ETAPA_BOTTOMUP.upper()
            ).first()
            if not registro:
                db.add(ControleCiclo(ciclo_sop=ciclo, origem=ETAPA_BOTTOMUP, status=STATUS_CONGELADO))
            else:
                registro.status = STATUS_CONGELADO

            db.flush()
            # Entrega o Bottom-Up as etapas de jusante que ainda nao publicaram.
            propagar_para_jusante(db, ciclo, ETAPA_BOTTOMUP, "vol_bottomup")

        db.commit()
        return {"status": "success", "message": "Proposta S&OP Bottom-Up salva com sucesso!"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=repr(e))


@router.get("/grafico")
async def grafico_gerenciamento(produto_id: str, db: Session = Depends(get_db)):
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
            calendario[mes_str] = {"Realizado": None, "IA": None, "CicloAnterior": None, "Comercial": None, "Topdown": None}
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
            func.sum(FatoIbpGranular.vol_bottomup).label('bu'),
            func.sum(FatoIbpGranular.vol_topdown).label('td'),
            func.sum(FatoIbpGranular.vol_final).label('final')
        ).filter(FatoIbpGranular.sku == produto_id)

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {
                "ia": int(row.ia or 0), 
                "bu": int(row.bu or 0),
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
                    calendario[ms]["Comercial"] = proj_por_mes[ms][ciclo_atual]["bu"]
                
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
                "Topdown": v["Topdown"] if mes_dt >= m2_comercial else None,
                "Comercial": v["Comercial"] if mes_dt >= m2_comercial else None
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))