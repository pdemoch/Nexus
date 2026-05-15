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

# O Cérebro Mestre do S&OP
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_projection_window,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

# A ROTA FOI AJUSTADA PARA BATER 100% COM O FRONTEND (/micro)
router = APIRouter(prefix="/api/v1/consensus/micro", tags=["Consenso Gerenciamento"])

# =====================================================================
# PERMISSÕES
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito. Nível Gerencial Exigido.")
    return usuario

# =====================================================================
# SCHEMAS DE DADOS
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

# =====================================================================
# ROTAS PRINCIPAIS (APIs)
# =====================================================================

@router.get("/filtros")
async def obter_filtros_gerencia(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    """Devolve a lista de filtros disponíveis para o cockpit gerencial."""
    q = db.query(DimCliente)
    if usuario['funcao'] == 'Gerente':
        q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "coordenadores": sorted({str(c.supervisor_nome).strip() for c in q.distinct(DimCliente.supervisor_nome).all() if c.supervisor_nome}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }

@router.get("")
async def listar_gerenciamento(nivel_filtro: str = None, valor_filtro: str = None, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    """Busca a árvore Bottom-Up, resgatando clientes e SKUs sem coordenação direta."""
    try:
        m2, m4 = get_projection_window(db)
        ciclo = get_current_cycle(db)
        query_base = get_truth_query(db, ciclo, m2, m4)
        
        # Garante que o Gerente vê a sua equipa OU tudo o que for "Órfão" (Meta Global)
        if usuario['funcao'] == 'Gerente':
            query_base = query_base.filter(
                or_(
                    func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper(),
                    DimCliente.gerente_nome == None,
                    DimCliente.gerente_nome == ''
                )
            )
            
        if nivel_filtro == 'coordenador' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == valor_filtro.strip().upper())
        elif nivel_filtro == 'vendedor' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == valor_filtro.strip().upper())
        elif nivel_filtro == 'regional' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.regional)) == valor_filtro.strip().upper())

        # Adicionado o V_TD (Top Down Target) na recolha de dados
        resultados = query_base.with_entities(
            DimCliente.supervisor_nome, DimCliente.vendedor_nome, DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, 
            FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(
            DimCliente.supervisor_nome, DimCliente.vendedor_nome, DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        status_dict = {c.origem.strip().upper(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all() if c.origem}
        
        arvore = defaultdict(lambda: {
            "id": "", "nome": "", "tipo": "coordenador", "status": "Aberto", 
            "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv": 0}), 
            "vendedores": defaultdict(lambda: {
                "id": "", "nome": "", "tipo": "vendedor", "status": "Aberto", 
                "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv": 0}), 
                "clientes": defaultdict(lambda: {
                    "id": "", "nome": "", "tipo": "cliente", 
                    "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv": 0}), 
                    "produtos": defaultdict(lambda: {
                        "id": "", "nome": "", "tipo": "produto", 
                        "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv": 0})
                    })
                })
            })
        })
        
        # Inserção nas Categorias de Resgate
        for r in resultados:
            c = str(r.supervisor_nome or "SEM COORDENADOR").strip()
            v = str(r.vendedor_nome or "SEM VENDEDOR").strip()
            rz = str(r.razaosocial or "CLIENTE NÃO IDENTIFICADO").strip()
            sku = str(r.sku or "SEM SKU").strip()
            desc = str(r.descricao or "PRODUTO NÃO CADASTRADO").strip()
            ms = str(r.mes_projetado)
            
            v_ia = int(r.v_ia or 0)
            v_td = int(r.v_td or 0)
            v_bu = int(r.v_bu or 0)
            rec = float(r.rec_bu or 0)
            pmv = float(r.pmv or 0)

            arvore[c]["id"] = c
            arvore[c]["nome"] = c
            arvore[c]["status"] = status_dict.get(c.upper(), "Aberto")

            arvore[c]["vendedores"][v]["id"] = f"{c}|{v}"
            arvore[c]["vendedores"][v]["nome"] = v
            arvore[c]["vendedores"][v]["status"] = status_dict.get(v.upper(), "Aberto")
            
            arvore[c]["vendedores"][v]["clientes"][rz]["id"] = f"{c}|{v}|{rz}"
            arvore[c]["vendedores"][v]["clientes"][rz]["nome"] = rz
            
            arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["id"] = f"{c}|{v}|{rz}|{sku}"
            arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["nome"] = desc
            
            for t in [arvore[c]["meses"][ms], arvore[c]["vendedores"][v]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                t["vol_ia"] += v_ia
                t["vol_td"] += v_td
                t["vol_ajustado"] += v_bu
                t["receita"] += rec
                t["pmv"] = pmv

        dados = [
            {
                "id": c["id"], "chave_matriz": c["id"], "nome": c["nome"], "tipo": c["tipo"], "status": c["status"], 
                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in c["meses"].items()], 
                "subRows": [
                    {
                        "id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], "status": v["status"], 
                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], 
                        "subRows": [
                            {
                                "id": cl["id"], "chave_matriz": cl["id"], "nome": cl["nome"], "tipo": cl["tipo"], 
                                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in cl["meses"].items()], 
                                "subRows": [
                                    {
                                        "id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "tipo": p["tipo"], "produto": pk,
                                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in p["meses"].items()]
                                    } for pk, p in cl["produtos"].items()
                                ]
                            } for cl in v["clientes"].values()
                        ]
                    } for v in c["vendedores"].values()
                ]
            } for c in arvore.values()
        ]
        return {"status": "success", "dados": dados}
    except Exception as e: 
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def salvar_e_congelar(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    """Salva os ajustes efetuados e rateia para baixo. Se não enviar ajustes, aprova/tranca a tela."""
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A estratégia macro ainda não foi liberada pela Diretoria. Aguarde o ciclo.")
        
        # Caso o botão clicado seja o "Aprovar Ciclo" puro (sem novas edições)
        if not payload.ajustes:
            nome_alvo = usuario.get('gerente_nome', 'COORDENACAO_GERAL')
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == nome_alvo).first()
            if reg: reg.status = 'Fechado'
            else: db.add(ControleCiclo(ciclo_sop=ciclo, origem=nome_alvo, status='Fechado'))
            db.commit()
            return {"status": "success", "message": "Ciclo aprovado e trancado."}

        # Rateio Dinâmico Inteligente
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo)

            # Filtros dinâmicos respeitando os contentores genéricos criados ("SEM COORDENADOR" -> IS NULL)
            if len(partes) >= 1:
                if partes[0] == "SEM COORDENADOR": query = query.filter(or_(DimCliente.supervisor_nome == None, DimCliente.supervisor_nome == ''))
                else: query = query.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == partes[0].upper())
            
            if len(partes) >= 2:
                if partes[1] == "SEM VENDEDOR": query = query.filter(or_(DimCliente.vendedor_nome == None, DimCliente.vendedor_nome == ''))
                else: query = query.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == partes[1].upper())
            
            if len(partes) >= 3:
                if partes[2] in ["CLIENTE NÃO CADASTRADO", "CLIENTE NÃO IDENTIFICADO", "DESC"]: query = query.filter(or_(DimCliente.razaosocial == None, DimCliente.razaosocial == ''))
                else: query = query.filter(func.upper(func.trim(DimCliente.razaosocial)) == partes[2].upper())
            
            if len(partes) >= 4:
                if partes[3] == "SEM SKU": query = query.filter(or_(FatoIbpGranular.sku == None, FatoIbpGranular.sku == ''))
                else: query = query.filter(FatoIbpGranular.sku == partes[3].strip())

            linhas = query.all()
            if not linhas: continue

            total_base = sum([l.vol_bottomup for l in linhas]) 
            soma_dist = 0
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = int(ajuste.novo_volume) - soma_dist
                else:
                    peso = l.vol_bottomup / total_base if total_base > 0 else 1.0 / len(linhas)
                    rateado = int(round(int(ajuste.novo_volume) * peso))
                    soma_dist += rateado
                
                l.vol_bottomup = rateado
                l.vol_supply = rateado
                l.vol_final = rateado

        db.commit()
        return {"status": "success", "message": "Ajustes salvos e consolidados."}
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/destrancar")
async def destrancar_bases(payload: PayloadDestrancar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)
        
        m2, m4 = get_projection_window(db)
        q = get_truth_query(db, ciclo, m2, m4).with_entities(DimCliente.vendedor_nome, DimCliente.supervisor_nome)
        
        if payload.regional and payload.regional != "TODAS":
            q = q.filter(func.upper(func.trim(DimCliente.regional)) == payload.regional.strip().upper())
            
        dados_ativos = q.distinct().all()
        vendedores = {v[0].strip().upper() for v in dados_ativos if v[0]}
        coordenadores = {c[1].strip().upper() for c in dados_ativos if c[1]}
        todos_alvos = list(vendedores) + list(coordenadores)
        
        travas = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)).in_(todos_alvos)).all()
        for t in travas: t.status = 'Aberto'
            
        db.commit()
        return {"status": "success", "message": "Bases destrancadas com sucesso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_gerenciamento(chave_matriz: str, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    """Constrói o gráfico de Lag Forecast respeitando a busca em profundidade."""
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

        # Realizado
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

        # Projeções
        q_proj = db.query(
            FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_bottomup).label('bu'),
            func.sum(FatoIbpGranular.vol_final).label('final')
        ).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)

        if coord_alvo: q_proj = q_proj.filter(func.upper(func.trim(DimCliente.supervisor_nome)) == coord_alvo.upper())
        elif p[0] == "SEM COORDENADOR": q_proj = q_proj.filter(or_(DimCliente.supervisor_nome == None, DimCliente.supervisor_nome == ''))
        if vendedor_alvo: q_proj = q_proj.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == vendedor_alvo.upper())
        elif len(p) > 1 and p[1] == "SEM VENDEDOR": q_proj = q_proj.filter(or_(DimCliente.vendedor_nome == None, DimCliente.vendedor_nome == ''))
        if cliente_alvo: q_proj = q_proj.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente_alvo.upper())
        elif len(p) > 2 and (p[2] in ["CLIENTE NÃO CADASTRADO", "CLIENTE NÃO IDENTIFICADO", "DESC"]): q_proj = q_proj.filter(or_(DimCliente.razaosocial == None, DimCliente.razaosocial == ''))
        if sku_alvo: q_proj = q_proj.filter(FatoIbpGranular.sku == sku_alvo)
        elif len(p) > 3 and p[3] == "SEM SKU": q_proj = q_proj.filter(or_(FatoIbpGranular.sku == None, FatoIbpGranular.sku == ''))

        projecoes = q_proj.group_by(FatoIbpGranular.mes_projetado, FatoIbpGranular.ciclo_sop).all()
        proj_por_mes = defaultdict(dict)
        for row in projecoes:
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
                target_dt = datetime.datetime.strptime(ciclo_alvo, '%m/%Y')
                best_c = available_cycles[-1]
                for c in reversed(available_cycles):
                    if datetime.datetime.strptime(c, '%m/%Y') <= target_dt:
                        best_c = c
                        break
                proj = proj_por_mes[mes_str][best_c]
                
            calendario[mes_str]["IA"] = int(proj["ia"] or 0)
            calendario[mes_str]["Comercial"] = int(proj["bu"] or 0)
            calendario[mes_str]["Final"] = int(proj["final"] or 0)
            if diff_months >= 2 and ciclo_anterior in proj_por_mes[mes_str]:
                calendario[mes_str]["CicloAnterior"] = int(proj_por_mes[mes_str][ciclo_anterior]["final"] or 0)

        timeline = []
        for mes_str, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(mes_str, '%Y-%m').date()
            real = v["Realizado"]
            if mes_dt < hoje and real is None: real = 0
            
            timeline.append({
                "name": mes_str, "data_iso": f"{mes_str}-01",
                "Realizado": real, "IA": v["IA"], "Consenso": v["Comercial"],
                "Final": v["Final"], "CicloAnterior": v["CicloAnterior"]
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))