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
# MODELOS DE ENTRADA (PAYLOADS)
# =====================================================================
class AjusteGerente(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGerente(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteGerente]

class PayloadDestrancar(BaseModel):
    regional: str

class PayloadToggleLock(BaseModel):
    regional: str
    status: str

# =====================================================================
# EXPRESSÕES DE HIERARQUIA COALESCE (AGRUPAMENTO POR RAZÃO SOCIAL)
# =====================================================================
def get_coord_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.supervisor_nome), ''), func.nullif(func.trim(DimCliente.gerente_nome), ''), 'SEM COORDENADOR')

def get_vend_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.vendedor_nome), ''), 'SEM VENDEDOR')

def get_cli_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.razaosocial), ''), DimCliente.cgc)

# =====================================================================
# GOVERNANÇA E SEGURANÇA DE ESCOPO
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente Comercial', 'Coordenador Comercial']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gestão Comercial.")
    return usuario

def verificar_concorrencia(db: Session, supervisor_alvo: str):
    reg = db.query(ControleCiclo).filter(
        ControleCiclo.origem == supervisor_alvo,
        ControleCiclo.status == 'Fechado'
    ).first()
    if reg:
        raise HTTPException(status_code=423, detail=f"Base de {supervisor_alvo} está travada por outra operação de aprovação.")

# =====================================================================
# ENDPOINT PRINCIPAL: ÁRVORE COMERCIAL CONSOLIDADA POR RAZÃO SOCIAL
# =====================================================================
@router.get("")
async def listar_gerenciamento(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        # MÁQUINA DO TEMPO: O "Hoje" respeita o relógio global
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
            cx.label('coord'),
            vx.label('vend'),
            clx.label('cli'),
            FatoIbpGranular.sku,
            DimProduto.descricao.label('prod_desc'),
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(cx, vx, clx, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all()

        ant_dict = defaultdict(lambda: defaultdict(int))
        skus_encontrados = list({r.sku for r in resultados if r.sku})
        clientes_encontrados = list({r.cli for r in resultados if r.cli}) 
        
        if skus_encontrados and clientes_encontrados:
            q_ant = get_truth_query(db, ciclo_anterior, data_ini, data_fim)
            res_ant = q_ant.filter(
                FatoIbpGranular.sku.in_(skus_encontrados),
                clx.in_(clientes_encontrados)
            ).with_entities(
                clx.label('cli'),
                FatoIbpGranular.sku,
                FatoIbpGranular.mes_projetado,
                func.sum(FatoIbpGranular.vol_final).label('v_ant')
            ).group_by(clx, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado).all()
            
            for ra in res_ant:
                ant_dict[(str(ra.cli).strip(), str(ra.sku).strip())][str(ra.mes_projetado)] = int(ra.v_ant or 0)

        trancas_reg = {c.origem: c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all()}

        arvore = {}
        def criar_meses():
            return {m: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0.0, "vol_anterior": 0} for m in meses_alvo}

        for r in resultados:
            co = str(r.coord).strip()
            ve = str(r.vend).strip()
            cl = str(r.cli).strip()
            sk = str(r.sku).strip()
            de = str(r.prod_desc).strip()
            ms = str(r.mes_projetado)

            if co not in arvore: arvore[co] = {"nome": co, "status": trancas_reg.get(co, "Aberto"), "meses": criar_meses(), "vendedores": {}}
            if ve not in arvore[co]["vendedores"]: arvore[co]["vendedores"][ve] = {"nome": ve, "meses": criar_meses(), "clientes": {}}
            if cl not in arvore[co]["vendedores"][ve]["clientes"]: arvore[co]["vendedores"][ve]["clientes"][cl] = {"nome": cl, "meses": criar_meses(), "produtos": {}}
            if sk not in arvore[co]["vendedores"][ve]["clientes"][cl]["produtos"]: arvore[co]["vendedores"][ve]["clientes"][cl]["produtos"][sk] = {"nome": de, "produto": sk, "meses": criar_meses()}

            if ms in meses_alvo:
                v_bu = int(r.v_bu or 0)
                pmv_b = float(r.pmv or 0)
                v_ant = ant_dict[(cl, sk)][ms]

                for nivel in [arvore[co]["meses"][ms], arvore[co]["vendedores"][ve]["meses"][ms], arvore[co]["vendedores"][ve]["clientes"][cl]["meses"][ms], arvore[co]["vendedores"][ve]["clientes"][cl]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_ia"] += int(r.v_ia or 0)
                    nivel["vol_td"] += v_bu
                    nivel["vol_ajustado"] += v_bu
                    nivel["receita"] += (v_bu * pmv_b)
                    nivel["vol_anterior"] += v_ant
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_b

        final = []
        for co_k, co_v in arvore.items():
            vends = []
            for ve_k, ve_v in co_v["vendedores"].items():
                clis = []
                for cl_k, cl_v in ve_v["clientes"].items():
                    prods = []
                    for sk_k, sk_v in cl_v["produtos"].items():
                        prods.append({"id": f"{co_k}|{ve_k}|{cl_k}|{sk_k}", "chave_matriz": f"{co_k}|{ve_k}|{cl_k}|{sk_k}", "nome": sk_v["nome"], "produto": sk_k, "tipo": "produto", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in sk_v["meses"].items()]})
                    clis.append({"id": f"{co_k}|{ve_k}|{cl_k}", "chave_matriz": f"{co_k}|{ve_k}|{cl_k}", "nome": cl_k, "tipo": "cliente", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cl_v["meses"].items()], "subRows": prods})
                vends.append({"id": f"{co_k}|{ve_k}", "chave_matriz": f"{co_k}|{ve_k}", "nome": ve_k, "tipo": "vendedor", "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in ve_v["meses"].items()], "subRows": clis})
            final.append({"id": co_k, "chave_matriz": co_k, "nome": co_k, "tipo": "coordenador", "status": co_v["status"], "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in co_v["meses"].items()], "subRows": vends})

        return {"status": "success", "dados": sorted(final, key=lambda x: x["nome"])}
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# MOTOR DE RATEIO E GRAVAÇÃO ATÔMICA MULTI-CNPJ
# =====================================================================
@router.post("/congelar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        # MÁQUINA DO TEMPO: O "Hoje" respeita o relógio global
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        data_hist = hoje - relativedelta(months=12)

        cx, vx, clx = get_coord_expr(), get_vend_expr(), get_cli_expr()

        for ajuste in payload.ajustes:
            alvo_supervisor = ajuste.chave.split('|')[0]
            verificar_concorrencia(db, alvo_supervisor)

        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            p = ajuste.chave.split('|')
            
            q = get_truth_query(db, ciclo, dt, dt)
            if len(p) >= 1: q = q.filter(func.upper(cx) == p[0].upper())
            if len(p) >= 2: q = q.filter(func.upper(vx) == p[1].upper())
            if len(p) >= 3: q = q.filter(func.upper(clx) == p[2].upper())
            if len(p) >= 4: q = q.filter(FatoIbpGranular.sku == p[3].strip())

            linhas = q.all()
            if not list(linhas): continue

            skus_alvo = list({l.sku for l in linhas if l.sku})
            cgcs_alvo = list({l.cgc for l in linhas if l.cgc})

            hist_data = db.query(
                FatoVendas.cgc,
                FatoVendas.sku,
                func.sum(FatoVendas.qt_pedido).label('vol_hist'),
                func.sum(FatoVendas.vl_pedido).label('rec_hist')
            ).filter(
                FatoVendas.sku.in_(skus_alvo),
                FatoVendas.cgc.in_(cgcs_alvo),
                FatoVendas.data_pedido >= data_hist
            ).group_by(FatoVendas.cgc, FatoVendas.sku).all()

            peso_map = {}
            pmv_map = {}
            total_hist_no = sum(float(r.vol_hist or 0) for r in hist_data if r.vol_hist)

            for r in hist_data:
                v_h = float(r.vol_hist or 0)
                r_h = float(r.rec_hist or 0)
                if v_h > 0:
                    peso_map[(r.cgc, r.sku)] = v_h / total_hist_no
                    pmv_map[(r.cgc, r.sku)] = r_h / v_h

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
                    l.vol_supply = novo_v
                    l.vol_final = novo_v

            for l in linhas:
                chave_par = (l.cgc, l.sku)
                if chave_par in pmv_map and pmv_map[chave_par] > 0:
                    l.pmv_aplicado = pmv_map[chave_par]

            sup_nome = p[0]
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == sup_nome).first()
            if reg: reg.status = 'Fechado'
            else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=sup_nome, status='Fechado'))

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

# =====================================================================
# ENDPOINT DO DOSSIÊ GRÁFICO (CONSOLIDADO POR RAZÃO SOCIAL)
# =====================================================================
@router.get("/grafico")
async def grafico_gerenciamento(chave_matriz: str, db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        # MÁQUINA DO TEMPO: O "Hoje" respeita o relógio global
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

        cx, vx, clx = get_coord_expr(), get_vend_expr(), get_cli_expr()

        def apply_branch_filter(query, model_fact):
            p = chave_matriz.split('|')
            if len(p) >= 1 and p[0]: query = query.filter(func.upper(cx) == p[0].upper())
            if len(p) >= 2 and p[1]: query = query.filter(func.upper(vx) == p[1].upper())
            if len(p) >= 3 and p[2]: query = query.filter(func.upper(clx) == p[2].upper())
            if len(p) >= 4 and p[3]: query = query.filter(model_fact.sku == p[3].strip())
            return query

        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).outerjoin(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= inicio_hist)
        q_hist = apply_branch_filter(q_hist, FatoVendas)

        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario:
                calendario[row.mes_ano]["Realizado"] = int(row.realizado or 0)

        q_proj = db.query(
            func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes_ano'),
            FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_final).label('final'),
            func.sum(FatoIbpGranular.vol_bottomup).label('bu')
        ).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)
        q_proj = apply_branch_filter(q_proj, FatoIbpGranular)

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {"ia": int(row.ia or 0), "final": int(row.final or 0), "bu": int(row.bu or 0)}

        ciclo_base_zero = datetime.datetime.strptime('04/2026', '%m/%Y').date()
        ciclo_atual_dt = datetime.datetime.strptime(ciclo_atual, '%m/%Y').date()

        for mes_str in calendario.keys():
            mes_dt = datetime.datetime.strptime(mes_str, '%Y-%m').date()
            if mes_str not in proj_por_mes: continue

            alvo_ia_dt = mes_dt - relativedelta(months=2)
            if alvo_ia_dt < ciclo_base_zero: alvo_ia_dt = ciclo_base_zero
            if alvo_ia_dt > ciclo_atual_dt: alvo_ia_dt = ciclo_atual_dt
            ciclo_ia_str = alvo_ia_dt.strftime('%m/%Y')
            
            if ciclo_ia_str in proj_por_mes[mes_str]:
                calendario[mes_str]["IA"] = proj_por_mes[mes_str][ciclo_ia_str]["ia"]
            if ciclo_anterior in proj_por_mes[mes_str]:
                calendario[mes_str]["CicloAnterior"] = proj_por_mes[mes_str][ciclo_anterior]["final"]
            if ciclo_atual in proj_por_mes[mes_str]:
                calendario[mes_str]["TopDown"] = proj_por_mes[mes_str][ciclo_atual]["bu"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            
            timeline.append({
                "name": ms,
                "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt > hoje else (v["Realizado"] or 0),
                "IA": v["IA"],
                "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"] if mes_dt >= m2_comercial else None 
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# ROTAS DE CONTROLE (CONSERVAÇÃO INTACTA)
# =====================================================================
@router.post("/destrancar")
async def destrancar_regional(payload: PayloadDestrancar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    ciclo = get_current_cycle(db)
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == payload.regional).first()
    if reg:
        reg.status = 'Aberto'
        db.commit()
    return {"status": "success"}

@router.post("/toggle-lock")
async def toggle_lock_regional(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    ciclo = get_current_cycle(db)
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == payload.regional).first()
    if reg:
        reg.status = payload.status
    else:
        db.add(ControleCiclo(ciclo_sop=ciclo, origem=payload.regional, status=payload.status))
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

# =====================================================================
# ROTA DE FILTROS (SUPORTE A COMPONENTES EXTERNOS/MENUS)
# =====================================================================
@router.get("/filtros")
async def listar_filtros_gerenciamento(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        cx = get_coord_expr()
        
        # O gestor só vê as regionais a que tem direito
        q = db.query(cx)
        if usuario['funcao'] == 'Coordenador Comercial':
            q = q.filter(func.upper(cx) == usuario['nome'].strip().upper())
        elif usuario['funcao'] == 'Gerente Comercial':
            user_db = db.query(Usuario).filter(Usuario.id == usuario['id']).first()
            filiais = [f.strip().upper() for f in (user_db.filiais_permissao or "").split(",") if f.strip()]
            if filiais:
                q = q.filter(func.upper(func.trim(DimCliente.filial)).in_(filiais))
                
        regionais = q.distinct().all()
        
        return {
            "status": "success", 
            "dados": {
                "regionais": [r[0] for r in regionais if r[0]]
            }
        }
    except Exception as e:
        raise HTTPException(500, repr(e))