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
# EXPRESSÕES SQL DE AGRUPAMENTO (FALLBACK BLINDADO)
# =====================================================================
def get_coord_expr():
    # Se supervisor for nulo/vazio, pega o gerente. Se ambos nulos, "SEM COORDENADOR"
    return func.coalesce(func.nullif(func.trim(DimCliente.supervisor_nome), ''), func.nullif(func.trim(DimCliente.gerente_nome), ''), 'SEM COORDENADOR')

def get_vend_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.vendedor_nome), ''), 'SEM VENDEDOR')

def get_cli_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.razaosocial), ''), 'CLIENTE NÃO IDENTIFICADO')

# =====================================================================
# PERMISSÕES E PROTEÇÕES
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")
    return usuario

def verificar_vendedor_online(db: Session, alvo_nome: str):
    if alvo_nome in ["SEM COORDENADOR", "SEM VENDEDOR"]: return
    user = db.query(Usuario).filter(
        func.or_(
            func.upper(func.trim(Usuario.nome_vendedor)) == alvo_nome.strip().upper(),
            func.upper(func.trim(Usuario.supervisor_nome)) == alvo_nome.strip().upper()
        )
    ).first()
    if user and user.ultima_atividade and (datetime.datetime.utcnow() - user.ultima_atividade).total_seconds() < 60:
        raise HTTPException(status_code=403, detail=f"⚠️ O utilizador {alvo_nome} está com a plataforma aberta neste exato momento.")

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
        
        # Garante a janela desde o mês atual (M0) para capturar as vendas mais próximas
        hoje = datetime.date.today().replace(day=1)
        data_ini = hoje
        data_fim = hoje + relativedelta(months=4)
        
        meses_lista = []
        curr = data_ini
        while curr <= data_fim:
            meses_lista.append(curr.strftime("%Y-%m-%d"))
            curr += relativedelta(months=1)

        query_base = get_truth_query(db, ciclo, data_ini, data_fim)
        
        coord_expr = get_coord_expr()
        vend_expr = get_vend_expr()
        cli_expr = get_cli_expr()

        if usuario['funcao'] == 'Gerente':
            g_nome = usuario['gerente_nome'].strip().upper()
            query_base = query_base.filter(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome)
            
        if nivel_filtro == 'coordenador' and valor_filtro:
            query_base = query_base.filter(func.upper(coord_expr) == valor_filtro.strip().upper())
        elif nivel_filtro == 'vendedor' and valor_filtro:
            query_base = query_base.filter(func.upper(vend_expr) == valor_filtro.strip().upper())
        elif nivel_filtro == 'regional' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.regional)) == valor_filtro.strip().upper())

        resultados = query_base.with_entities(
            coord_expr.label('coord_final'),
            vend_expr.label('vend_final'),
            cli_expr.label('cli_final'),
            FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(
            coord_expr, vend_expr, cli_expr, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        status_dict = {c.origem.strip().upper(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all() if c.origem}
        
        arvore = {}
        def criar_meses():
            return {m: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0.0} for m in meses_lista}

        for r in resultados:
            c = str(r.coord_final)
            v = str(r.vend_final)
            rz = str(r.cli_final)
            sku = str(r.sku).strip() if r.sku else "SEM SKU"
            desc = str(r.descricao).strip() if r.descricao else "PRODUTO SEM CADASTRO"
            ms = str(r.mes_projetado)
            
            v_ia = int(r.v_ia) if r.v_ia is not None else 0
            v_td = int(r.v_td) if r.v_td is not None else 0
            v_bu = int(r.v_bu) if r.v_bu is not None else 0
            pmv_base = float(r.pmv) if r.pmv is not None else 0.0
            rec = v_bu * pmv_base

            if c not in arvore: arvore[c] = {"nome": c, "status": status_dict.get(c.upper(), "Aberto"), "meses": criar_meses(), "vendedores": {}}
            if v not in arvore[c]["vendedores"]: arvore[c]["vendedores"][v] = {"nome": v, "status": status_dict.get(v.upper(), "Aberto"), "meses": criar_meses(), "clientes": {}}
            if rz not in arvore[c]["vendedores"][v]["clientes"]: arvore[c]["vendedores"][v]["clientes"][rz] = {"nome": rz, "meses": criar_meses(), "produtos": {}}
            if sku not in arvore[c]["vendedores"][v]["clientes"][rz]["produtos"]: arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku] = {"nome": desc, "sku": sku, "meses": criar_meses()}

            if ms in meses_lista:
                for t in [arvore[c]["meses"][ms], arvore[c]["vendedores"][v]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                    t["vol_ia"] += v_ia
                    t["vol_td"] += v_td
                    t["vol_ajustado"] += v_bu
                    t["receita"] += rec
                    t["pmv"] = (t["receita"] / t["vol_ajustado"]) if t["vol_ajustado"] > 0 else pmv_base

        # Conversão estruturada para Array exigido pelo React Table
        dados_finais = []
        for c_key, c_val in arvore.items():
            sub_vendedores = []
            for v_key, v_val in c_val["vendedores"].items():
                sub_clientes = []
                for cl_key, cl_val in v_val["clientes"].items():
                    sub_produtos = []
                    for p_key, p_val in cl_val["produtos"].items():
                        sub_produtos.append({
                            "id": f"{c_key}|{v_key}|{cl_key}|{p_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}|{p_key}", 
                            "nome": p_val["nome"], "produto": p_key, "tipo": "produto",
                            "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in p_val["meses"].items()]
                        })
                    sub_clientes.append({
                        "id": f"{c_key}|{v_key}|{cl_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}", "nome": cl_val["nome"], "tipo": "cliente",
                        "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cl_val["meses"].items()],
                        "subRows": sorted(sub_produtos, key=lambda x: x["nome"])
                    })
                sub_vendedores.append({
                    "id": f"{c_key}|{v_key}", "chave_matriz": f"{c_key}|{v_key}", "nome": v_val["nome"], "tipo": "vendedor", "status": v_val["status"],
                    "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in v_val["meses"].items()],
                    "subRows": sorted(sub_clientes, key=lambda x: x["nome"])
                })
            dados_finais.append({
                "id": c_key, "chave_matriz": c_key, "nome": c_val["nome"], "tipo": "coordenador", "status": c_val["status"],
                "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in c_val["meses"].items()],
                "subRows": sorted(sub_vendedores, key=lambda x: x["nome"])
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
        
        coord_expr = get_coord_expr()
        vend_expr = get_vend_expr()
        cli_expr = get_cli_expr()

        alvos_afetados = list({a.chave.split('|')[0].strip() for a in payload.ajustes if a.chave.split('|')[0] != "SEM COORDENADOR"})
        for alvo in alvos_afetados: verificar_vendedor_online(db, alvo)

        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            p = ajuste.chave.split('|')
            q = get_truth_query(db, ciclo, dt, dt)

            if len(p) >= 1: q = q.filter(func.upper(coord_expr) == p[0].upper())
            if len(p) >= 2: q = q.filter(func.upper(vend_expr) == p[1].upper())
            if len(p) >= 3: q = q.filter(func.upper(cli_expr) == p[2].upper())
            if len(p) >= 4: q = q.filter(FatoIbpGranular.sku == p[3].strip())

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
        cliente_alvo = p[2] if len(p) > 2 and p[2] != "CLIENTE NÃO IDENTIFICADO" else None
        sku_alvo = p[3] if len(p) > 3 and p[3] != "SEM SKU" else None

        ciclo_atual = get_current_cycle(db)
        hoje = datetime.date.today().replace(day=1)
        inicio_hist = hoje - relativedelta(months=24)
        
        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            calendario[curr.strftime('%Y-%m')] = {"Realizado": None, "IA": None, "Comercial": None, "Final": None, "CicloAnterior": None}
            curr += relativedelta(months=1)

        coord_expr = get_coord_expr()
        vend_expr = get_vend_expr()
        cli_expr = get_cli_expr()

        # Histórico de Vendas
        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).outerjoin(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= inicio_hist)
        
        if coord_alvo: q_hist = q_hist.filter(func.upper(coord_expr) == coord_alvo.upper())
        elif p[0] == "SEM COORDENADOR": q_hist = q_hist.filter(coord_expr == "SEM COORDENADOR")
        if vendedor_alvo: q_hist = q_hist.filter(func.upper(vend_expr) == vendedor_alvo.upper())
        elif len(p) > 1 and p[1] == "SEM VENDEDOR": q_hist = q_hist.filter(vend_expr == "SEM VENDEDOR")
        if cliente_alvo: q_hist = q_hist.filter(func.upper(cli_expr) == cliente_alvo.upper())
        elif len(p) > 2 and p[2] == "CLIENTE NÃO IDENTIFICADO": q_hist = q_hist.filter(cli_expr == "CLIENTE NÃO IDENTIFICADO")
        if sku_alvo: q_hist = q_hist.filter(FatoVendas.sku == sku_alvo)

        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario: calendario[row.mes_ano]["Realizado"] = int(row.realizado)

        # Projeções S&OP
        q_proj = db.query(
            FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_bottomup).label('bu'), func.sum(FatoIbpGranular.vol_final).label('final')
        ).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)

        if coord_alvo: q_proj = q_proj.filter(func.upper(coord_expr) == coord_alvo.upper())
        elif p[0] == "SEM COORDENADOR": q_proj = q_proj.filter(coord_expr == "SEM COORDENADOR")
        if vendedor_alvo: q_proj = q_proj.filter(func.upper(vend_expr) == vendedor_alvo.upper())
        elif len(p) > 1 and p[1] == "SEM VENDEDOR": q_proj = q_proj.filter(vend_expr == "SEM VENDEDOR")
        if cliente_alvo: q_proj = q_proj.filter(func.upper(cli_expr) == cliente_alvo.upper())
        elif len(p) > 2 and p[2] == "CLIENTE NÃO IDENTIFICADO": q_proj = q_proj.filter(cli_expr == "CLIENTE NÃO IDENTIFICADO")
        if sku_alvo: q_proj = q_proj.filter(FatoIbpGranular.sku == sku_alvo)

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