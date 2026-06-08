import datetime
import math
from typing import List
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, Usuario, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/gerenciamento", tags=["Consenso Gerenciamento"])

# =====================================================================
# PAYLOADS
# =====================================================================
class AjusteGerente(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGerente(BaseModel):
    origem_ajuste: str
    visao: str = 'carteira'
    ajustes: List[AjusteGerente]

class PayloadToggleLock(BaseModel):
    regional: str
    status: str

# =====================================================================
# EXPRESSÕES E GOVERNANÇA
# =====================================================================
def get_coord_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.supervisor_nome), ''), func.nullif(func.trim(DimCliente.gerente_nome), ''), 'SEM COORDENADOR')

def get_vend_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.vendedor_nome), ''), 'SEM VENDEDOR')

def get_cli_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.razaosocial), ''), DimCliente.cgc)

def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente Comercial', 'Coordenador Comercial']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gestão Comercial.")
    return usuario

def check_topdown_lock(db: Session, ciclo: str):
    td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
    if not td or td.status != 'Fechado':
        raise HTTPException(status_code=403, detail="Fase Comercial Bloqueada: A Diretoria (Top-Down) ainda não ratificou os volumes do ciclo.")

# =====================================================================
# GET: ÁRVORE DUPLA (CARTEIRA E PORTFÓLIO)
# =====================================================================
@router.get("")
async def listar_gerenciamento(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        td_reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
        is_topdown_fechado = True if (td_reg and td_reg.status == 'Fechado') else False
        
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)

        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        cx, vx, clx = get_coord_expr(), get_vend_expr(), get_cli_expr()
        q = get_truth_query(db, ciclo, data_ini, data_fim)

        if usuario['funcao'] == 'Coordenador Comercial':
            q = q.filter(func.upper(cx) == usuario['nome'].strip().upper())
        elif usuario['funcao'] == 'Gerente Comercial':
            user_db = db.query(Usuario).filter(Usuario.id == usuario['id']).first()
            filiais = [f.strip().upper() for f in (user_db.filiais_permissao or "").split(",") if f.strip()]
            if filiais:
                q = q.filter(func.upper(func.trim(DimCliente.filial)).in_(filiais))

        resultados = q.with_entities(
            cx.label('coord'), vx.label('vend'), clx.label('cli'),
            FatoIbpGranular.sku, DimProduto.descricao.label('prod_desc'),
            DimProduto.categoria.label('cat'), DimProduto.segmento.label('seg'),
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(cx, vx, clx, FatoIbpGranular.sku, DimProduto.descricao, DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.mes_projetado).all()

        ant_dict = defaultdict(lambda: defaultdict(int))
        skus_encontrados = list({r.sku for r in resultados if r.sku})
        clientes_encontrados = list({r.cli for r in resultados if r.cli}) 
        
        if skus_encontrados and clientes_encontrados:
            q_ant = get_truth_query(db, ciclo_anterior, data_ini, data_fim)
            res_ant = q_ant.filter(
                FatoIbpGranular.sku.in_(skus_encontrados), clx.in_(clientes_encontrados)
            ).with_entities(
                clx.label('cli'), FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_final).label('v_ant')
            ).group_by(clx, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado).all()
            
            for ra in res_ant:
                ant_dict[(str(ra.cli).strip(), str(ra.sku).strip())][str(ra.mes_projetado)] = int(ra.v_ant or 0)

        trancas_reg = {c.origem: c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all()}

        def criar_meses():
            return {m: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0.0, "vol_anterior": 0} for m in meses_alvo}

        # ÁRVORE DE CARTEIRA
        arvore_carteira = {}
        for r in resultados:
            co, ve, cl, sk, de, ms = str(r.coord).strip(), str(r.vend).strip(), str(r.cli).strip(), str(r.sku).strip(), str(r.prod_desc).strip(), str(r.mes_projetado)

            if co not in arvore_carteira: arvore_carteira[co] = {"nome": co, "status": trancas_reg.get(co, "Aberto"), "meses": criar_meses(), "vendedores": {}}
            if ve not in arvore_carteira[co]["vendedores"]: arvore_carteira[co]["vendedores"][ve] = {"nome": ve, "meses": criar_meses(), "clientes": {}}
            if cl not in arvore_carteira[co]["vendedores"][ve]["clientes"]: arvore_carteira[co]["vendedores"][ve]["clientes"][cl] = {"nome": cl, "meses": criar_meses(), "produtos": {}}
            if sk not in arvore_carteira[co]["vendedores"][ve]["clientes"][cl]["produtos"]: arvore_carteira[co]["vendedores"][ve]["clientes"][cl]["produtos"][sk] = {"nome": de, "produto": sk, "meses": criar_meses()}

            if ms in meses_alvo:
                v_bu = int(r.v_bu or 0)
                pmv_b = float(r.pmv or 0)
                v_ant = ant_dict[(cl, sk)][ms]

                for nivel in [arvore_carteira[co]["meses"][ms], arvore_carteira[co]["vendedores"][ve]["meses"][ms], arvore_carteira[co]["vendedores"][ve]["clientes"][cl]["meses"][ms], arvore_carteira[co]["vendedores"][ve]["clientes"][cl]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_ia"] += int(r.v_ia or 0)
                    nivel["vol_ajustado"] += v_bu
                    nivel["receita"] += (v_bu * pmv_b)
                    nivel["vol_anterior"] += v_ant
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_b

        final_carteira = []
        for co_k, co_v in arvore_carteira.items():
            vends = []
            for ve_k, ve_v in co_v["vendedores"].items():
                clis = []
                for cl_k, cl_v in ve_v["clientes"].items():
                    prods = []
                    for sk_k, sk_v in cl_v["produtos"].items():
                        prods.append({"id": f"{co_k}|{ve_k}|{cl_k}|{sk_k}", "chave_matriz": f"{co_k}|{ve_k}|{cl_k}|{sk_k}", "nome": sk_v["nome"], "produto": sk_k, "tipo": "produto", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in sk_v["meses"].items()]})
                    clis.append({"id": f"{co_k}|{ve_k}|{cl_k}", "chave_matriz": f"{co_k}|{ve_k}|{cl_k}", "nome": cl_k, "tipo": "cliente", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cl_v["meses"].items()], "subRows": prods})
                vends.append({"id": f"{co_k}|{ve_k}", "chave_matriz": f"{co_k}|{ve_k}", "nome": ve_k, "tipo": "vendedor", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in ve_v["meses"].items()], "subRows": clis})
            final_carteira.append({"id": co_k, "chave_matriz": co_k, "nome": co_k, "tipo": "coordenador", "status": co_v["status"], "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in co_v["meses"].items()], "subRows": vends})

        # ÁRVORE DE PORTFÓLIO
        arvore_port = {}
        todos_produtos = db.query(DimProduto).all()
        
        for p in todos_produtos:
            cat, seg, sk, de = p.categoria or 'SEM CATEGORIA', p.segmento or 'SEM SEGMENTO', p.sku, p.descricao or 'SEM NOME'
            if cat not in arvore_port: arvore_port[cat] = {"nome": cat, "tipo": "categoria", "meses": criar_meses(), "segmentos": {}}
            if seg not in arvore_port[cat]["segmentos"]: arvore_port[cat]["segmentos"][seg] = {"nome": seg, "tipo": "segmento", "meses": criar_meses(), "produtos": {}}
            if sk not in arvore_port[cat]["segmentos"][seg]["produtos"]:
                arvore_port[cat]["segmentos"][seg]["produtos"][sk] = {"nome": de, "produto": sk, "tipo": "produto", "meses": criar_meses()}

        for r in resultados:
            cat, seg, sk, ms = r.cat or 'SEM CATEGORIA', r.seg or 'SEM SEGMENTO', str(r.sku).strip(), str(r.mes_projetado)
            
            if ms in meses_alvo and cat in arvore_port and seg in arvore_port[cat]["segmentos"] and sk in arvore_port[cat]["segmentos"][seg]["produtos"]:
                v_bu = int(r.v_bu or 0)
                pmv_b = float(r.pmv or 0)
                v_ant = sum(ant_dict[(c, sk)][ms] for c in clientes_encontrados if (c, sk) in ant_dict and ms in ant_dict[(c, sk)])

                for nivel in [arvore_port[cat]["meses"][ms], arvore_port[cat]["segmentos"][seg]["meses"][ms], arvore_port[cat]["segmentos"][seg]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_ia"] += int(r.v_ia or 0)
                    nivel["vol_ajustado"] += v_bu
                    nivel["receita"] += (v_bu * pmv_b)
                    nivel["vol_anterior"] += v_ant
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_b

        final_portfolio = []
        for cat_k, cat_v in arvore_port.items():
            segs = []
            for seg_k, seg_v in cat_v["segmentos"].items():
                prods = []
                for sk_k, sk_v in seg_v["produtos"].items():
                    prods.append({"id": f"{cat_k}|{seg_k}|{sk_k}", "chave_matriz": f"{cat_k}|{seg_k}|{sk_k}", "nome": sk_v["nome"], "produto": sk_k, "tipo": "produto", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in sk_v["meses"].items()]})
                segs.append({"id": f"{cat_k}|{seg_k}", "chave_matriz": f"{cat_k}|{seg_k}", "nome": seg_k, "tipo": "segmento", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in seg_v["meses"].items()], "subRows": prods})
            final_portfolio.append({"id": cat_k, "chave_matriz": cat_k, "nome": cat_k, "tipo": "categoria", "status": "Aberto", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cat_v["meses"].items()], "subRows": segs})

        return {
            "status": "success", 
            "is_topdown_fechado": is_topdown_fechado,
            "dados": {
                "carteira": sorted(final_carteira, key=lambda x: x["nome"]),
                "portfolio": sorted(final_portfolio, key=lambda x: x["nome"])
            }
        }
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# ROTA SALVAR RASCUNHO (BLINDADA)
# =====================================================================
@router.post("/salvar")
async def salvar_rascunho_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        check_topdown_lock(db, ciclo)

        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        data_hist = hoje - relativedelta(months=12)

        cx, vx, clx = get_coord_expr(), get_vend_expr(), get_cli_expr()

        # BLINDAGEM DE RATEIO: Descobre regionais trancadas para ignorá-las matematicamente
        locked_coords = [c.origem.upper() for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.status == 'Fechado').all()]

        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            p = ajuste.chave.split('|')
            q = get_truth_query(db, ciclo, dt, dt)

            if usuario['funcao'] == 'Coordenador Comercial': q = q.filter(func.upper(cx) == usuario['nome'].strip().upper())
            elif usuario['funcao'] == 'Gerente Comercial':
                user_db = db.query(Usuario).filter(Usuario.id == usuario['id']).first()
                filiais = [f.strip().upper() for f in (user_db.filiais_permissao or "").split(",") if f.strip()]
                if filiais: q = q.filter(func.upper(func.trim(DimCliente.filial)).in_(filiais))

            # Ignora as linhas cuja regional (coordenador) já foi assinada
            if locked_coords:
                q = q.filter(~func.upper(cx).in_(locked_coords))

            if ajuste.nivel == 'carteira':
                if len(p) >= 1: q = q.filter(func.upper(cx) == p[0].upper())
                if len(p) >= 2: q = q.filter(func.upper(vx) == p[1].upper())
                if len(p) >= 3: q = q.filter(func.upper(clx) == p[2].upper())
                if len(p) >= 4: q = q.filter(FatoIbpGranular.sku == p[3].strip())
            elif ajuste.nivel == 'portfolio':
                sku_alvo = p[-1]
                q = q.filter(FatoIbpGranular.sku == sku_alvo.strip())

            linhas = q.all()
            if not list(linhas): continue 

            skus_alvo = list({l.sku for l in linhas if l.sku})
            cgcs_alvo = list({l.cgc for l in linhas if l.cgc})

            hist_data = db.query(
                FatoVendas.cgc, FatoVendas.sku, func.sum(FatoVendas.qt_pedido).label('vol_hist')
            ).filter(
                FatoVendas.sku.in_(skus_alvo), FatoVendas.cgc.in_(cgcs_alvo), FatoVendas.data_pedido >= data_hist
            ).group_by(FatoVendas.cgc, FatoVendas.sku).all()

            peso_map = {}
            total_hist_no = sum(float(r.vol_hist or 0) for r in hist_data if r.vol_hist)

            for r in hist_data:
                v_h = float(r.vol_hist or 0)
                if v_h > 0: peso_map[(r.cgc, r.sku)] = v_h / total_hist_no

            total_bu_atual = sum(l.vol_bottomup for l in linhas)
            delta = ajuste.novo_volume - total_bu_atual

            linhas.sort(key=lambda x: peso_map.get((x.cgc, x.sku), 0), reverse=True)

            if delta != 0:
                for i, l in enumerate(linhas):
                    peso = peso_map.get((l.cgc, l.sku), 1/len(linhas) if total_hist_no == 0 else 0)
                    inc = int(round(delta * peso))
                    
                    if i == len(linhas) - 1:
                        inc = delta - sum(int(round(delta * peso_map.get((x.cgc, x.sku), 1/len(linhas) if total_hist_no == 0 else 0))) for x in linhas[:-1])
                    
                    novo_v = max(0, l.vol_bottomup + inc)
                    l.vol_bottomup = novo_v

        db.commit()
        return {"status": "success", "message": "Rascunho salvo e rateado com sucesso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

# =====================================================================
# ROTA CONGELAR CARTEIRA GLOBAL
# =====================================================================
@router.post("/congelar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        if payload.ajustes:
            await salvar_rascunho_gerencia(payload, db, usuario)
        
        ciclo = get_current_cycle(db)
        
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        data_ini = hoje + relativedelta(months=2)

        cx = get_coord_expr()
        q_coords = get_truth_query(db, ciclo, data_ini, data_ini)
        
        if usuario['funcao'] == 'Coordenador Comercial':
            q_coords = q_coords.filter(func.upper(cx) == usuario['nome'].strip().upper())
        elif usuario['funcao'] == 'Gerente Comercial':
            user_db = db.query(Usuario).filter(Usuario.id == usuario['id']).first()
            filiais = [f.strip().upper() for f in (user_db.filiais_permissao or "").split(",") if f.strip()]
            if filiais: q_coords = q_coords.filter(func.upper(func.trim(DimCliente.filial)).in_(filiais))

        # Descobre as regionais sob a gestão deste utilizador que ainda estão abertas
        coords_acessiveis = [r[0] for r in q_coords.with_entities(cx).distinct().all() if r[0]]

        for coord in coords_acessiveis:
            db.query(FatoIbpGranular).filter(
                FatoIbpGranular.ciclo_sop == ciclo,
                func.upper(cx) == coord.upper()
            ).update({
                FatoIbpGranular.vol_supply: FatoIbpGranular.vol_bottomup,
                FatoIbpGranular.vol_final: FatoIbpGranular.vol_bottomup
            }, synchronize_session=False)
            
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == coord).first()
            if reg: reg.status = 'Fechado'
            else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=coord, status='Fechado'))

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

# =====================================================================
# GRAFICOS, TRAVAS E FILTROS (MANTIDOS INTACTOS)
# =====================================================================
@router.get("/grafico")
async def grafico_gerenciamento(chave_matriz: str, nivel_hierarquia: str = 'produto', visao: str = 'carteira', db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        _mes_str, _ano_str = ciclo_atual.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        
        inicio_hist = hoje - relativedelta(months=24)
        m2_comercial = hoje + relativedelta(months=2)

        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            mes_str = curr.strftime('%Y-%m')
            calendario[mes_str] = {"Realizado": None, "IA": None, "CicloAnterior": None, "TopDown": None}
            curr += relativedelta(months=1)

        def apply_branch_filter(query, model_fact, is_portfolio=False):
            p = chave_matriz.split('|')
            if is_portfolio:
                if len(p) >= 1 and p[0]: query = query.filter(DimProduto.categoria == p[0])
                if len(p) >= 2 and p[1]: query = query.filter(DimProduto.segmento == p[1])
                if len(p) >= 3 and p[2]: query = query.filter(model_fact.sku == p[2].strip())
            else:
                cx, vx, clx = get_coord_expr(), get_vend_expr(), get_cli_expr()
                if len(p) >= 1 and p[0]: query = query.filter(func.upper(cx) == p[0].upper())
                if len(p) >= 2 and p[1]: query = query.filter(func.upper(vx) == p[1].upper())
                if len(p) >= 3 and p[2]: query = query.filter(func.upper(clx) == p[2].upper())
                if len(p) >= 4 and p[3]: query = query.filter(model_fact.sku == p[3].strip())
            return query

        is_port = visao == 'portfolio'

        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('realizado')).outerjoin(DimCliente, FatoVendas.cgc == DimCliente.cgc).join(DimProduto, FatoVendas.sku == DimProduto.sku).filter(FatoVendas.data_pedido >= inicio_hist)
        q_hist = apply_branch_filter(q_hist, FatoVendas, is_port)
        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario: calendario[row.mes_ano]["Realizado"] = int(row.realizado or 0)

        q_proj = db.query(func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes_ano'), FatoIbpGranular.ciclo_sop, func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_final).label('final'), func.sum(FatoIbpGranular.vol_bottomup).label('bu')).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)
        q_proj = apply_branch_filter(q_proj, FatoIbpGranular, is_port)

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {"ia": int(row.ia or 0), "final": int(row.final or 0), "bu": int(row.bu or 0)}

        for mes_str in calendario.keys():
            if mes_str in proj_por_mes:
                if ciclo_atual in proj_por_mes[mes_str]:
                    calendario[mes_str]["IA"] = proj_por_mes[mes_str][ciclo_atual]["ia"]
                    calendario[mes_str]["TopDown"] = proj_por_mes[mes_str][ciclo_atual]["bu"]
                if ciclo_anterior in proj_por_mes[mes_str]:
                    calendario[mes_str]["CicloAnterior"] = proj_por_mes[mes_str][ciclo_anterior]["final"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            timeline.append({
                "name": ms, "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt > hoje else (v["Realizado"] or 0),
                "IA": v["IA"], "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"] if mes_dt >= m2_comercial else None 
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/toggle-lock")
async def toggle_lock_regional(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    ciclo = get_current_cycle(db)
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == payload.regional).first()
    if reg: reg.status = payload.status
    else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=payload.regional, status=payload.status))
    db.commit()
    return {"status": "success"}

@router.post("/lock-all")
async def lock_all_regionais(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    ciclo = get_current_cycle(db)
    cx = get_coord_expr()
    regionais = db.query(cx).distinct().all()
    for reg_nome in [r[0] for r in regionais if r[0]]:
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == reg_nome).first()
        if reg: reg.status = 'Fechado'
        else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=reg_nome, status='Fechado'))
    db.commit()
    return {"status": "success"}

@router.get("/filtros")
async def listar_filtros_gerenciamento(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        cx = get_coord_expr()
        q = db.query(cx)
        if usuario['funcao'] == 'Coordenador Comercial': q = q.filter(func.upper(cx) == usuario['nome'].strip().upper())
        elif usuario['funcao'] == 'Gerente Comercial':
            user_db = db.query(Usuario).filter(Usuario.id == usuario['id']).first()
            filiais = [f.strip().upper() for f in (user_db.filiais_permissao or "").split(",") if f.strip()]
            if filiais: q = q.filter(func.upper(func.trim(DimCliente.filial)).in_(filiais))
        regionais = q.distinct().all()
        return {"status": "success", "dados": {"regionais": [r[0] for r in regionais if r[0]]}}
    except Exception as e:
        raise HTTPException(500, repr(e))