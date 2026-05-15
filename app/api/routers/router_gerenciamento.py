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
    get_working_window_months,
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
    origem: str

class PayloadLockAll(BaseModel):
    gerente_nome: str = ""
    acao: str

# =====================================================================
# FUNÇÕES DE APOIO
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    return usuario

def verificar_concorrencia(db: Session, alvo: str):
    if alvo in ["SEM COORDENADOR", "SEM VENDEDOR"]: return
    user = db.query(Usuario).filter(
        or_(
            func.upper(func.trim(Usuario.nome_vendedor)) == alvo.upper(),
            func.upper(func.trim(Usuario.supervisor_nome)) == alvo.upper()
        )
    ).first()
    if user and user.ultima_atividade and (datetime.datetime.utcnow() - user.ultima_atividade).total_seconds() < 60:
        raise HTTPException(status_code=403, detail=f"O usuário {alvo} está com a plataforma aberta neste momento.")

def coord_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.supervisor_nome), ''), func.nullif(func.trim(DimCliente.gerente_nome), ''), 'SEM COORDENADOR')

def vend_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.vendedor_nome), ''), 'SEM VENDEDOR')

def cli_expr():
    return func.coalesce(func.nullif(func.trim(DimCliente.razaosocial), ''), 'CLIENTE NAO IDENTIFICADO')

# =====================================================================
# ROTA 1: FILTROS (DROPDOWN DA TELA)
# =====================================================================
@router.get("/filtros")
async def obter_filtros(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    q = db.query(DimCliente)
    if usuario['funcao'] == 'Gerente':
        q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "coordenadores": sorted({str(c.supervisor_nome).strip() for c in q.distinct(DimCliente.supervisor_nome).all() if c.supervisor_nome}) + ["SEM COORDENADOR"],
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome}) + ["SEM VENDEDOR"]
    }

# =====================================================================
# ROTA 2: A ÁRVORE PRINCIPAL (LISTAGEM)
# =====================================================================
@router.get("")
async def listar_gerenciamento(nivel_filtro: str = None, valor_filtro: str = None, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        
        # Puxa 5 meses (M0 a M4) para garantir que Maio e Junho apareçam
        hoje = datetime.date.today().replace(day=1)
        data_ini = hoje
        data_fim = hoje + relativedelta(months=4)
        
        meses_str = []
        c = data_ini
        while c <= data_fim:
            meses_str.append(c.strftime("%Y-%m-%d"))
            c += relativedelta(months=1)

        q = get_truth_query(db, ciclo, data_ini, data_fim)
        
        cx, vx, clx = coord_expr(), vend_expr(), cli_expr()

        if usuario['funcao'] == 'Gerente':
            q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
            
        if nivel_filtro == 'coordenador' and valor_filtro:
            q = q.filter(func.upper(cx) == valor_filtro.strip().upper())
        elif nivel_filtro == 'vendedor' and valor_filtro:
            q = q.filter(func.upper(vx) == valor_filtro.strip().upper())
        elif nivel_filtro == 'regional' and valor_filtro:
            q = q.filter(func.upper(func.trim(DimCliente.regional)) == valor_filtro.strip().upper())

        resultados = q.with_entities(
            cx.label('coord'), vx.label('vend'), clx.label('cli'),
            FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(cx, vx, clx, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all()

        status_dict = {c.origem.strip().upper(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all() if c.origem}
        
        # Construtor do Dicionário (Zero colisões)
        arvore = {}
        def bloc_meses(): return {m: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0.0} for m in meses_str}

        for r in resultados:
            c_str = str(r.coord).strip()
            v_str = str(r.vend).strip()
            cl_str = str(r.cli).strip()
            sku_str = str(r.sku).strip() if r.sku else "SEM SKU"
            desc_str = str(r.descricao).strip() if r.descricao else "PRODUTO SEM CADASTRO"
            ms = str(r.mes_projetado)
            
            v_ia, v_td, v_bu = int(r.v_ia or 0), int(r.v_td or 0), int(r.v_bu or 0)
            pmv_b = float(r.pmv or 0.0)
            receita = v_bu * pmv_b

            if c_str not in arvore: arvore[c_str] = {"nome": c_str, "status": status_dict.get(c_str.upper(), "Aberto"), "meses": bloc_meses(), "vendedores": {}}
            if v_str not in arvore[c_str]["vendedores"]: arvore[c_str]["vendedores"][v_str] = {"nome": v_str, "status": status_dict.get(v_str.upper(), "Aberto"), "meses": bloc_meses(), "clientes": {}}
            if cl_str not in arvore[c_str]["vendedores"][v_str]["clientes"]: arvore[c_str]["vendedores"][v_str]["clientes"][cl_str] = {"nome": cl_str, "meses": bloc_meses(), "produtos": {}}
            if sku_str not in arvore[c_str]["vendedores"][v_str]["clientes"][cl_str]["produtos"]: arvore[c_str]["vendedores"][v_str]["clientes"][cl_str]["produtos"][sku_str] = {"nome": desc_str, "sku": sku_str, "meses": bloc_meses()}

            if ms in meses_str:
                for nivel in [arvore[c_str]["meses"][ms], arvore[c_str]["vendedores"][v_str]["meses"][ms], arvore[c_str]["vendedores"][v_str]["clientes"][cl_str]["meses"][ms], arvore[c_str]["vendedores"][v_str]["clientes"][cl_str]["produtos"][sku_str]["meses"][ms]]:
                    nivel["vol_ia"] += v_ia
                    nivel["vol_td"] += v_td
                    nivel["vol_ajustado"] += v_bu
                    nivel["receita"] += receita
                    nivel["pmv"] = (nivel["receita"] / nivel["vol_ajustado"]) if nivel["vol_ajustado"] > 0 else pmv_b

        # Formatação JSON p/ Frontend
        dados_finais = []
        for c_key, c_val in arvore.items():
            lista_vend = []
            for v_key, v_val in c_val["vendedores"].items():
                lista_cli = []
                for cl_key, cl_val in v_val["clientes"].items():
                    lista_prod = []
                    for p_key, p_val in cl_val["produtos"].items():
                        lista_prod.append({
                            "id": f"{c_key}|{v_key}|{cl_key}|{p_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}|{p_key}", 
                            "nome": p_val["nome"], "produto": p_key, "tipo": "produto",
                            "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in p_val["meses"].items()]
                        })
                    lista_cli.append({
                        "id": f"{c_key}|{v_key}|{cl_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}", "nome": cl_val["nome"], "tipo": "cliente",
                        "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cl_val["meses"].items()],
                        "subRows": sorted(lista_prod, key=lambda x: x["nome"])
                    })
                lista_vend.append({
                    "id": f"{c_key}|{v_key}", "chave_matriz": f"{c_key}|{v_key}", "nome": v_val["nome"], "tipo": "vendedor", "status": v_val["status"],
                    "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in v_val["meses"].items()],
                    "subRows": sorted(lista_cli, key=lambda x: x["nome"])
                })
            dados_finais.append({
                "id": c_key, "chave_matriz": c_key, "nome": c_val["nome"], "tipo": "coordenador", "status": c_val["status"],
                "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in c_val["meses"].items()],
                "subRows": sorted(lista_vend, key=lambda x: x["nome"])
            })

        return {"status": "success", "dados": sorted(dados_finais, key=lambda x: x["nome"])}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, repr(e))

# =====================================================================
# ROTA 3: RATEIO E SALVAMENTO (CONGELAR)
# =====================================================================
@router.post("/congelar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A diretoria (Top-Down) precisa fechar a estratégia primeiro.")
        
        cx, vx, clx = coord_expr(), vend_expr(), cli_expr()

        # Proteção contra concorrência
        for ajuste in payload.ajustes:
            alvo = ajuste.chave.split('|')[0]
            verificar_concorrencia(db, alvo)

        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            p = ajuste.chave.split('|')
            q = get_truth_query(db, ciclo, dt, dt)

            if len(p) >= 1: q = q.filter(func.upper(cx) == p[0].upper())
            if len(p) >= 2: q = q.filter(func.upper(vx) == p[1].upper())
            if len(p) >= 3: q = q.filter(func.upper(clx) == p[2].upper())
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
                if i == len(linhas) - 1: 
                    inc = diff - sum(int(round(diff * (x.vol_bottomup/total_bu if total_bu > 0 else 1/len(linhas)))) for x in linhas[:-1])
                
                l.vol_bottomup = max(0, l.vol_bottomup + inc)
                l.vol_supply = l.vol_bottomup
                l.vol_final = l.vol_bottomup

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

# =====================================================================
# ROTAS 4, 5 e 6: LOCKS E GRÁFICOS
# =====================================================================
@router.post("/destrancar")
async def destrancar_bases(payload: PayloadDestrancar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        meses = get_working_window_months(db)
        q = get_truth_query(db, ciclo, meses[0], meses[-1]).with_entities(DimCliente.vendedor_nome, DimCliente.supervisor_nome)
        if payload.regional and payload.regional != "TODAS":
            q = q.filter(func.upper(func.trim(DimCliente.regional)) == payload.regional.strip().upper())
            
        dados = q.distinct().all()
        alvos = {v[0].strip().upper() for v in dados if v[0]} | {c[1].strip().upper() for c in dados if c[1]}
        
        travas = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)).in_(alvos)).all()
        for t in travas: t.status = 'Aberto'
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/toggle-lock")
async def toggle_lock(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        verificar_concorrencia(db, payload.origem)
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
        meses = get_working_window_months(db)
        q = get_truth_query(db, ciclo, meses[0], meses[-1]).with_entities(DimCliente.vendedor_nome, DimCliente.supervisor_nome)
        filtro = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else payload.gerente_nome
        if filtro: q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == filtro.strip().upper())
            
        dados = q.distinct().all()
        alvos = list({v[0].strip() for v in dados if v[0]} | {c[1].strip() for c in dados if c[1]})
        for a in alvos: verificar_concorrencia(db, a)
        
        status_a = 'Fechado' if payload.acao == 'Trancar' else 'Aberto'
        for alvo in alvos:
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)) == alvo.upper()).first()
            if reg: reg.status = status_a
            else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=alvo, status=status_a))
        db.commit()
        return {"status": "success"}
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico(chave_matriz: str, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        p = chave_matriz.split('|')
        c_alvo = p[0] if len(p) > 0 else None
        v_alvo = p[1] if len(p) > 1 else None
        cl_alvo = p[2] if len(p) > 2 else None
        s_alvo = p[3] if len(p) > 3 else None

        ciclo_atual = get_current_cycle(db)
        hoje = datetime.date.today().replace(day=1)
        inicio_hist = hoje - relativedelta(months=24)
        
        calendario = { (inicio_hist + relativedelta(months=i)).strftime('%Y-%m'): {"Realizado": None, "IA": None, "Comercial": None, "Final": None, "CicloAnterior": None} for i in range(29) }

        cx, vx, clx = coord_expr(), vend_expr(), cli_expr()

        # Venda Realizada
        q_h = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('m'), func.sum(FatoVendas.qt_pedido).label('v')).outerjoin(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= inicio_hist)
        if c_alvo: q_h = q_h.filter(func.upper(cx) == c_alvo.upper())
        if v_alvo: q_h = q_h.filter(func.upper(vx) == v_alvo.upper())
        if cl_alvo: q_h = q_h.filter(func.upper(clx) == cl_alvo.upper())
        if s_alvo: q_h = q_h.filter(FatoVendas.sku == s_alvo)
        for r in q_h.group_by('m').all():
            if r.m in calendario: calendario[r.m]["Realizado"] = int(r.v)

        # Projeções
        q_p = db.query(FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop, func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_bottomup).label('bu'), func.sum(FatoIbpGranular.vol_final).label('f')).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)
        if c_alvo: q_p = q_p.filter(func.upper(cx) == c_alvo.upper())
        if v_alvo: q_p = q_p.filter(func.upper(vx) == v_alvo.upper())
        if cl_alvo: q_p = q_p.filter(func.upper(clx) == cl_alvo.upper())
        if s_alvo: q_p = q_p.filter(FatoIbpGranular.sku == s_alvo)

        proj = defaultdict(dict)
        for r in q_p.group_by(FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop).all():
            proj[r.mes_projetado.strftime('%Y-%m')][r.ciclo_sop] = {"ia": r.ia, "bu": r.bu, "f": r.f}

        c_ant = get_previous_cycle(db)
        for ms in calendario.keys():
            if ms not in proj: continue
            md = datetime.datetime.strptime(ms, '%Y-%m').date()
            diff = (md.year - hoje.year) * 12 + md.month - hoje.month
            
            c_alvo_dt = ciclo_atual if diff >= 2 else (hoje - relativedelta(months=math.ceil((2 - diff)/2))).strftime('%m/%Y')
            
            p_dados = proj[ms].get(c_alvo_dt)
            if not p_dados and proj[ms]:
                valid_cycles = [c for c in sorted(proj[ms].keys(), key=lambda x: datetime.datetime.strptime(x, '%m/%Y')) if datetime.datetime.strptime(c, '%m/%Y') <= datetime.datetime.strptime(c_alvo_dt, '%m/%Y')]
                p_dados = proj[ms][valid_cycles[-1]] if valid_cycles else {"ia":0, "bu":0, "f":0}
            elif not p_dados: p_dados = {"ia":0, "bu":0, "f":0}

            calendario[ms].update({"IA": int(p_dados["ia"] or 0), "Comercial": int(p_dados["bu"] or 0), "Final": int(p_dados["f"] or 0)})
            if diff >= 2 and c_ant in proj[ms]: calendario[ms]["CicloAnterior"] = int(proj[ms][c_ant]["f"] or 0)

        return {"status": "success", "dados": [{"name": ms, "data_iso": f"{ms}-01", **v} for ms, v in sorted(calendario.items())]}
    except Exception as e:
        raise HTTPException(500, repr(e))