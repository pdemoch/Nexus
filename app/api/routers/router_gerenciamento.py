from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
import datetime
import math
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, Usuario, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_working_window_months,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/micro", tags=["Consenso Gerenciamento"])

# =====================================================================
# PERMISSÕES E PROTEÇÕES
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")
    return usuario

def verificar_vendedor_online(db: Session, alvo_nome: str):
    user = db.query(Usuario).filter(
        func.or_(
            func.upper(func.trim(Usuario.nome_vendedor)) == alvo_nome.strip().upper(),
            func.upper(func.trim(Usuario.supervisor_nome)) == alvo_nome.strip().upper()
        )
    ).first()
    if user and user.ultima_atividade and (datetime.datetime.utcnow() - user.ultima_atividade).total_seconds() < 60:
        raise HTTPException(status_code=403, detail=f"⚠️ CONCORRÊNCIA: O utilizador {alvo_nome} está online e com a plataforma aberta neste exato momento.")

# =====================================================================
# SCHEMAS
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
    origem: str

class PayloadLockAll(BaseModel):
    gerente_nome: str = ""
    acao: str

# =====================================================================
# ROTAS PRINCIPAIS
# =====================================================================
@router.get("/filtros")
async def obter_filtros_gerencia(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    q = db.query(DimCliente)
    if usuario['funcao'] == 'Gerente':
        q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
    
    res = {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "coordenadores": sorted({str(c.supervisor_nome).strip() for c in q.distinct(DimCliente.supervisor_nome).all() if c.supervisor_nome}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }
    res["coordenadores"].append("SEM COORDENADOR")
    res["vendedores"].append("SEM VENDEDOR")
    return res

@router.get("")
async def listar_gerenciamento(nivel_filtro: str = None, valor_filtro: str = None, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        meses_janela = get_working_window_months(db)
        data_ini, data_fim = meses_janela[0], meses_janela[-1]
        
        query_base = get_truth_query(db, ciclo, data_ini, data_fim)
        
        if usuario['funcao'] == 'Gerente':
            g_nome = usuario['gerente_nome'].strip().upper()
            query_base = query_base.filter(or_(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome, DimCliente.gerente_nome == None))
            
        if nivel_filtro == 'coordenador' and valor_filtro:
            if valor_filtro == "SEM COORDENADOR": query_base = query_base.filter(or_(DimCliente.supervisor_nome == None, DimCliente.supervisor_nome == ''))
            else: query_base = query_base.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == valor_filtro.strip().upper())
        elif nivel_filtro == 'vendedor' and valor_filtro:
            if valor_filtro == "SEM VENDEDOR": query_base = query_base.filter(or_(DimCliente.vendedor_nome == None, DimCliente.vendedor_nome == ''))
            else: query_base = query_base.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == valor_filtro.strip().upper())
        elif nivel_filtro == 'regional' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.regional)) == valor_filtro.strip().upper())

        resultados = query_base.with_entities(
            DimCliente.supervisor_nome, DimCliente.vendedor_nome, DimCliente.razaosocial, 
            FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(
            DimCliente.supervisor_nome, DimCliente.vendedor_nome, DimCliente.razaosocial, 
            FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        status_dict = {c.origem.strip().upper(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all() if c.origem}
        
        arvore = defaultdict(lambda: {
            "nome": "", "tipo": "coordenador", "status": "Aberto", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0}),
            "vendedores": defaultdict(lambda: {
                "nome": "", "tipo": "vendedor", "status": "Aberto", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0}),
                "clientes": defaultdict(lambda: {
                    "nome": "", "tipo": "cliente", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0}),
                    "produtos": defaultdict(lambda: {
                        "nome": "", "tipo": "produto", "sku": "", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0})
                    })
                })
            })
        })

        for r in resultados:
            c = str(r.supervisor_nome or "SEM COORDENADOR").strip()
            v = str(r.vendedor_nome or "SEM VENDEDOR").strip()
            rz = str(r.razaosocial or "CLIENTE NÃO IDENTIFICADO").strip()
            sku, ms = str(r.sku or "SEM SKU").strip(), str(r.mes_projetado)
            
            v_ia, v_td, v_bu, pmv_base = int(r.v_ia or 0), int(r.v_td or 0), int(r.v_bu or 0), float(r.pmv or 0)
            rec = v_bu * pmv_base

            arvore[c]["nome"] = c
            arvore[c]["status"] = status_dict.get(c.upper(), "Aberto")
            arvore[c]["vendedores"][v]["nome"] = v
            arvore[c]["vendedores"][v]["status"] = status_dict.get(v.upper(), "Aberto")
            arvore[c]["vendedores"][v]["clientes"][rz]["nome"] = rz
            arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["nome"] = r.descricao or "PRODUTO SEM CADASTRO"
            arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["sku"] = sku
            
            for n in [arvore[c]["meses"][ms], arvore[c]["vendedores"][v]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                n["vol_ia"] += v_ia
                n["vol_td"] += v_td
                n["vol_ajustado"] += v_bu
                n["receita"] += rec
                # PMV Ponderado: Receita Total / Volume Ajustado Total
                n["pmv"] = (n["receita"] / n["vol_ajustado"]) if n["vol_ajustado"] > 0 else pmv_base

        dados_finais = []
        for c_key, c_val in arvore.items():
            dados_finais.append({
                "id": c_key, "chave_matriz": c_key, "nome": c_val["nome"], "tipo": "coordenador", "status": c_val["status"],
                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%m/%y"), **v} for k, v in c_val["meses"].items()],
                "subRows": [
                    {
                        "id": f"{c_key}|{v_key}", "chave_matriz": f"{c_key}|{v_key}", "nome": v_val["nome"], "tipo": "vendedor", "status": v_val["status"],
                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%m/%y"), **v} for k, v in v_val["meses"].items()],
                        "subRows": [
                            {
                                "id": f"{c_key}|{v_key}|{cl_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}", "nome": cl_val["nome"], "tipo": "cliente",
                                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%m/%y"), **v} for k, v in cl_val["meses"].items()],
                                "subRows": [
                                    {
                                        "id": f"{c_key}|{v_key}|{cl_key}|{p_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}|{p_key}", 
                                        "nome": p_val["nome"], "produto": p_key, "tipo": "produto",
                                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%m/%y"), **v} for k, v in p_val["meses"].items()]
                                    } for p_key, p_val in cl_val["produtos"].items()
                                ]
                            } for cl_key, cl_val in v_val["clientes"].items()
                        ]
                    } for v_key, v_val in c_val["vendedores"].items()
                ]
            })

        return {"status": "success", "dados": sorted(dados_finais, key=lambda x: x["nome"])}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A estratégia macro ainda não foi liberada pela Diretoria.")
        
        alvos_afetados = list({a.chave.split('|')[0].strip() for a in payload.ajustes if a.chave.split('|')[0] != "SEM COORDENADOR"})
        for alvo in alvos_afetados: verificar_vendedor_online(db, alvo)

        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            p = ajuste.chave.split('|')
            q = get_truth_query(db, ciclo, dt, dt)

            if len(p) >= 1:
                if p[0] == "SEM COORDENADOR": q = q.filter(or_(DimCliente.supervisor_nome == None, DimCliente.supervisor_nome == ''))
                else: q = q.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == p[0].upper())
            if len(p) >= 2:
                if p[1] == "SEM VENDEDOR": q = q.filter(or_(DimCliente.vendedor_nome == None, DimCliente.vendedor_nome == ''))
                else: q = q.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == p[1].upper())
            if len(p) >= 3:
                if p[2] == "CLIENTE NÃO IDENTIFICADO": q = q.filter(or_(DimCliente.razaosocial == None, DimCliente.razaosocial == ''))
                else: q = q.filter(func.upper(func.trim(DimCliente.razaosocial)) == p[2].upper())
            if len(p) >= 4:
                q = q.filter(FatoIbpGranular.sku == p[3].strip())

            linhas = q.all()
            if not linhas: continue

            total_bu = sum(l.vol_bottomup for l in linhas)
            diff = ajuste.novo_volume - total_bu
            if diff == 0: continue

            linhas.sort(key=lambda x: x.vol_bottomup, reverse=True)
            for i, l in enumerate(linhas):
                peso = l.vol_bottomup / total_bu if total_bu > 0 else 1/len(linhas)
                inc = int(round(diff * peso))
                if i == len(linhas) - 1: inc = diff - sum(int(round(diff * (x.vol_bottomup/total_bu if total_bu > 0 else 1/len(linhas)))) for x in linhas[:-1])
                
                l.vol_bottomup = max(0, l.vol_bottomup + inc)
                l.vol_supply = l.vol_bottomup
                l.vol_final = l.vol_bottomup

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/destrancar")
async def destrancar_bases(payload: PayloadDestrancar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        
        meses = get_working_window_months(db)
        q = get_truth_query(db, ciclo, meses[0], meses[-1]).with_entities(DimCliente.vendedor_nome, DimCliente.supervisor_nome)
        
        if payload.regional and payload.regional != "TODAS":
            q = q.filter(func.upper(func.trim(DimCliente.regional)) == payload.regional.strip().upper())
            
        dados_ativos = q.distinct().all()
        vendedores = {v[0].strip().upper() for v in dados_ativos if v[0]}
        coordenadores = {c[1].strip().upper() for c in dados_ativos if c[1]}
        todos_alvos = list(vendedores) + list(coordenadores)
        
        travas = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)).in_(todos_alvos)).all()
        for t in travas: t.status = 'Aberto'
            
        db.commit()
        return {"status": "success", "message": "Bases destrancadas."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/toggle-lock")
async def toggle_lock_gerenciamento(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        if payload.origem not in ["SEM COORDENADOR", "SEM VENDEDOR"]:
            verificar_vendedor_online(db, payload.origem)
        
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)) == payload.origem.strip().upper()).first()
        if reg: reg.status = 'Aberto' if reg.status == 'Fechado' else 'Fechado'
        else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=payload.origem, status='Fechado'))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/lock-all")
async def lock_all(payload: PayloadLockAll, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A estratégia macro ainda não foi liberada.")
        
        meses = get_working_window_months(db)
        q = get_truth_query(db, ciclo, meses[0], meses[-1]).with_entities(DimCliente.vendedor_nome, DimCliente.supervisor_nome)
        
        filtro = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else payload.gerente_nome
        if filtro: q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == filtro.strip().upper())
            
        dados_ativos = q.distinct().all()
        vendedores = {v[0].strip() for v in dados_ativos if v[0]}
        coordenadores = {c[1].strip() for c in dados_ativos if c[1]}
        
        for v in vendedores: verificar_vendedor_online(db, v)
        for c in coordenadores: verificar_vendedor_online(db, c)
        
        status_alvo = 'Fechado' if payload.acao == 'Trancar' else 'Aberto'
        todos_alvos = list(vendedores) + list(coordenadores)
        for alvo in todos_alvos:
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)) == alvo.upper()).first()
            if reg: reg.status = status_alvo
            else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=alvo, status=status_alvo))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_gerenciamento(chave_matriz: str, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        p = chave_matriz.split('|')
        coord_alvo = p[0] if len(p) > 0 and p[0] != "SEM COORDENADOR" else None
        vendedor_alvo = p[1] if len(p) > 1 and p[1] != "SEM VENDEDOR" else None
        cliente_alvo = p[2] if len(p) > 2 and p[2] not in ["CLIENTE NÃO CADASTRADO", "CLIENTE NÃO IDENTIFICADO", "DESC"] else None
        sku_alvo = p[3] if len(p) > 3 and p[3] != "SEM SKU" else None

        ciclo_atual = get_current_cycle(db)
        hoje = datetime.date.today().replace(day=1)
        inicio_hist = hoje - relativedelta(months=24)
        
        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            calendario[curr.strftime('%Y-%m')] = {"Realizado": None, "IA": None, "Comercial": None, "Final": None, "CicloAnterior": None}
            curr += relativedelta(months=1)

        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).outerjoin(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= inicio_hist)
        
        if coord_alvo: q_hist = q_hist.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == coord_alvo.upper())
        elif p[0] == "SEM COORDENADOR": q_hist = q_hist.filter(or_(DimCliente.supervisor_nome == None, DimCliente.supervisor_nome == ''))
        if vendedor_alvo: q_hist = q_hist.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == vendedor_alvo.upper())
        elif len(p) > 1 and p[1] == "SEM VENDEDOR": q_hist = q_hist.filter(or_(DimCliente.vendedor_nome == None, DimCliente.vendedor_nome == ''))
        if cliente_alvo: q_hist = q_hist.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente_alvo.upper())
        elif len(p) > 2 and (p[2] in ["CLIENTE NÃO CADASTRADO", "CLIENTE NÃO IDENTIFICADO", "DESC"]): q_hist = q_hist.filter(or_(DimCliente.razaosocial == None, DimCliente.razaosocial == ''))
        if sku_alvo: q_hist = q_hist.filter(FatoVendas.sku == sku_alvo)
        elif len(p) > 3 and p[3] == "SEM SKU": q_hist = q_hist.filter(or_(FatoVendas.sku == None, FatoVendas.sku == ''))

        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario: calendario[row.mes_ano]["Realizado"] = int(row.realizado)

        q_proj = db.query(
            FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_bottomup).label('bu'), func.sum(FatoIbpGranular.vol_final).label('final')
        ).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)

        if coord_alvo: q_proj = q_proj.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == coord_alvo.upper())
        elif p[0] == "SEM COORDENADOR": q_proj = q_proj.filter(or_(DimCliente.supervisor_nome == None, DimCliente.supervisor_nome == ''))
        if vendedor_alvo: q_proj = q_proj.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == vendedor_alvo.upper())
        elif len(p) > 1 and p[1] == "SEM VENDEDOR": q_proj = q_proj.filter(or_(DimCliente.vendedor_nome == None, DimCliente.vendedor_nome == ''))
        if cliente_alvo: q_proj = q_proj.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente_alvo.upper())
        elif len(p) > 2 and (p[2] in ["CLIENTE NÃO CADASTRADO", "CLIENTE NÃO IDENTIFICADO", "DESC"]): q_proj = q_proj.filter(or_(DimCliente.razaosocial == None, DimCliente.razaosocial == ''))
        if sku_alvo: q_proj = q_proj.filter(FatoIbpGranular.sku == sku_alvo)
        elif len(p) > 3 and p[3] == "SEM SKU": q_proj = q_proj.filter(or_(FatoIbpGranular.sku == None, FatoIbpGranular.sku == ''))

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by(FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_projetado.strftime('%Y-%m')][row.ciclo_sop] = {"ia": row.ia, "bu": row.bu, "final": row.final}

        ciclo_anterior = get_previous_cycle(db)

        for mes_str in calendario.keys():
            mes_dt = datetime.datetime.strptime(mes_str, '%Y-%m').date()
            if mes_str not in proj_por_mes: continue
            
            diff_months = (mes_dt.year - hoje.year) * 12 + mes_dt.month - hoje.month
            if diff_months >= 2: ciclo_alvo = ciclo_atual
            else:
                offset = math.ceil((2 - diff_months) / 2)
                ciclo_alvo = (hoje - relativedelta(months=offset)).strftime('%m/%Y')
                
            if ciclo_alvo in proj_por_mes[mes_str]: proj = proj_por_mes[mes_str][ciclo_alvo]
            else:
                available_cycles = sorted(proj_por_mes[mes_str].keys(), key=lambda x: datetime.datetime.strptime(x, '%m/%Y'))
                if available_cycles:
                    target_dt = datetime.datetime.strptime(ciclo_alvo, '%m/%Y')
                    best_c = available_cycles[-1]
                    for c in reversed(available_cycles):
                        if datetime.datetime.strptime(c, '%m/%Y') <= target_dt:
                            best_c = c; break
                    proj = proj_por_mes[mes_str][best_c]
                else: proj = {"ia": 0, "bu": 0, "final": 0}
                
            calendario[mes_str]["IA"] = int(proj["ia"] or 0)
            calendario[mes_str]["Comercial"] = int(proj["bu"] or 0)
            calendario[mes_str]["Final"] = int(proj["final"] or 0)
            if diff_months >= 2 and ciclo_anterior in proj_por_mes[mes_str]:
                calendario[mes_str]["CicloAnterior"] = int(proj_por_mes[mes_str][ciclo_anterior]["final"] or 0)

        timeline = [{"name": ms, "data_iso": f"{ms}-01", "Realizado": v["Realizado"] if datetime.datetime.strptime(ms, '%Y-%m').date() >= hoje or v["Realizado"] is not None else 0, "IA": v["IA"], "Consenso": v["Comercial"], "Final": v["Final"], "CicloAnterior": v["CicloAnterior"]} for ms, v in sorted(calendario.items())]
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))