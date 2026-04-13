from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session, Query
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd
from fastapi.responses import StreamingResponse
import traceback
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, FatoVendas, ControleCiclo, Usuario
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/consensus", tags=["Consenso S&OP"])

# =====================================================================
# 1. UTILITÁRIOS E HELPERS (DRY)
# =====================================================================
def get_current_cycle() -> str:
    return datetime.date.today().strftime("%m/%Y")

def get_previous_cycle() -> str:
    return (datetime.date.today().replace(day=1) - relativedelta(months=1)).strftime("%m/%Y")

def get_projection_window() -> tuple[str, str]:
    hoje = datetime.date.today().replace(day=1)
    return (hoje + relativedelta(months=2)).strftime('%Y-%m-%d'), (hoje + relativedelta(months=4)).strftime('%Y-%m-%d')

def parse_date_safe(date_input) -> datetime.date:
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()

# =====================================================================
# 2. MIDDLEWARES E VALIDAÇÕES
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito à Diretoria/Gerência.")
    return usuario

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso Restrito ao Administrador.")
    return usuario

def check_global_lock(db: Session):
    if db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == 'S&OP-Final', ControleCiclo.status == 'Fechado').first():
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global publicado.")

def verificar_vendedor_online(db: Session, vendedor_nome: str):
    vendedor_user = db.query(Usuario).filter(func.trim(Usuario.nome_vendedor) == vendedor_nome.strip()).first()
    if vendedor_user and vendedor_user.ultima_atividade and (datetime.datetime.utcnow() - vendedor_user.ultima_atividade).total_seconds() < 60:
        raise HTTPException(status_code=403, detail=f"⚠️ CONCORRÊNCIA: O executivo {vendedor_nome} está com a plataforma aberta neste exato momento.")

# =====================================================================
# 3. REPOSITÓRIOS
# =====================================================================
class QueryRepository:
    @staticmethod
    def get_base_ibp_query(db: Session, m_plus_2: str, m_plus_4: str, ciclo: str) -> Query:
        return db.query(FatoIbpGranular, DimCliente, DimProduto)\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
            .join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
            .filter(
                FatoIbpGranular.mes_projetado >= m_plus_2,
                FatoIbpGranular.mes_projetado <= m_plus_4,
                func.upper(func.coalesce(DimCliente.bloqueado, 'ATIVO')) != 'INATIVO',
                FatoIbpGranular.ciclo_sop == ciclo
            )

# =====================================================================
# 4. SERVIÇOS DE NEGÓCIO
# =====================================================================
class RateioService:
    @staticmethod
    def aplicar_historico_amplo(db: Session, linhas_atomicas: List[FatoIbpGranular], volume_total: int, nivel_rateio: str):
        if not linhas_atomicas: return

        skus = list({l.sku for l in linhas_atomicas if l.sku})
        cgcs = list({l.cgc for l in linhas_atomicas if l.cgc})
        if not skus or not cgcs: return
        
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')
        historico = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_hist'))\
            .filter(FatoVendas.sku.in_(skus), FatoVendas.cgc.in_(cgcs), FatoVendas.data_pedido >= data_limite_str)\
            .group_by(FatoVendas.sku, FatoVendas.cgc).all()
            
        dict_hist = {f"{h.sku}_{h.cgc}": float(h.vol_hist or 0) for h in historico}
        soma_hist = sum([dict_hist.get(f"{l.sku}_{l.cgc}", 0) for l in linhas_atomicas])
        
        soma_dist = 0
        for i, l in enumerate(linhas_atomicas):
            if i == len(linhas_atomicas) - 1:
                rateado = volume_total - soma_dist 
            else:
                peso = dict_hist.get(f"{l.sku}_{l.cgc}", 0) / soma_hist if soma_hist > 0 else 1.0 / len(linhas_atomicas)
                rateado = int(round(volume_total * peso))
                soma_dist += rateado
                
            if nivel_rateio == 'Top-Down':
                l.vol_topdown = l.vol_bottomup = l.vol_final = rateado
            elif nivel_rateio == 'Bottom-Up':
                l.vol_bottomup = l.vol_final = rateado

class GraphService:
    @staticmethod
    def build_timeline(db: Session, filters: dict):
        m_plus_2, m_plus_4 = get_projection_window()
        hoje = datetime.date.today()
        ciclo_anterior, ciclo_atual = get_previous_cycle(), get_current_cycle()
        
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol_real'))\
            .join(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))
        
        q_ant = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_final if filters.get('use_final') else FatoIbpGranular.vol_bottomup).label('vol_ant'))\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(FatoIbpGranular.ciclo_sop == ciclo_anterior)
            
        q_proj = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), func.sum(FatoIbpGranular.vol_bottomup if filters.get('use_bu') else FatoIbpGranular.vol_topdown).label('vol_consenso'))\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(FatoIbpGranular.ciclo_sop == ciclo_atual)

        if filters.get('vendedor'): q_hist = q_hist.filter(func.trim(FatoVendas.vendedor_nome) == filters['vendedor'])
        if filters.get('razaosocial'): q_hist = q_hist.filter(func.trim(DimCliente.razaosocial) == filters['razaosocial'])
        if filters.get('sku'): q_hist = q_hist.filter(FatoVendas.sku == filters['sku'])

        if filters.get('vendedor'): q_ant = q_ant.filter(func.trim(FatoIbpGranular.vendedor_nome) == filters['vendedor'])
        if filters.get('razaosocial'): q_ant = q_ant.filter(func.trim(DimCliente.razaosocial) == filters['razaosocial'])
        if filters.get('sku'): q_ant = q_ant.filter(FatoIbpGranular.sku == filters['sku'])

        if filters.get('vendedor'): q_proj = q_proj.filter(func.trim(FatoIbpGranular.vendedor_nome) == filters['vendedor'])
        if filters.get('razaosocial'): q_proj = q_proj.filter(func.trim(DimCliente.razaosocial) == filters['razaosocial'])
        if filters.get('sku'): q_proj = q_proj.filter(FatoIbpGranular.sku == filters['sku'])

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist.group_by(func.to_char(FatoVendas.data_pedido, 'YYYY-MM')).all()}
        ant_dict = {p.mes_projetado.strftime('%Y-%m-%d') if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado): int(p.vol_ant or 0) for p in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        projecoes = q_proj.group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = [{"name": (hoje - relativedelta(months=i)).strftime("%b/%y").capitalize(), "data_iso": (hoje - relativedelta(months=i)).replace(day=1).strftime("%Y-%m-%d"), "Realizado": hist_dict.get((hoje - relativedelta(months=i)).strftime('%Y-%m'), 0), "IA": None, "Consenso": None, "CicloAnterior": None} for i in range(24, 0, -1)]
        for p in projecoes:
            p_str = p.mes_projetado.strftime('%Y-%m-%d') if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado)
            timeline.append({"name": p.mes_projetado.strftime("%b/%y").capitalize() if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado), "data_iso": p_str, "Realizado": None, "IA": int(p.vol_ia or 0), "Consenso": int(p.vol_consenso or 0) if m_plus_2 <= p_str <= m_plus_4 else None, "CicloAnterior": ant_dict.get(p_str, None)})
            
        return timeline

class AdjustmentService:
    @staticmethod
    def processar_ajustes(db: Session, ajustes: list, ciclo: str, nivel_rateio: str, origem_real: str = 'Top-Down', is_gerente: bool = False):
        check_global_lock(db)
        
        if is_gerente:
            vendedores_afetados = list({a.chave.split('|')[0].strip() for a in ajustes})
            for v in vendedores_afetados: verificar_vendedor_online(db, v)

        for ajuste in ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            query = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(
                FatoIbpGranular.mes_projetado == data_alvo, 
                func.upper(func.coalesce(DimCliente.bloqueado, 'ATIVO')) != 'INATIVO',
                FatoIbpGranular.ciclo_sop == ciclo
            )

            if nivel_rateio == 'Top-Down':
                query = query.filter(FatoIbpGranular.sku == str(ajuste.produto))
            else:
                partes = ajuste.chave.split('|')
                query = query.filter(func.trim(FatoIbpGranular.vendedor_nome) == (partes[0].strip() if is_gerente else origem_real.strip()))
                if ajuste.nivel in ['cliente', 'produto']: query = query.filter(func.trim(DimCliente.razaosocial) == (partes[1].strip() if is_gerente else partes[0].strip()))
                if ajuste.nivel == 'produto': query = query.filter(FatoIbpGranular.sku == (partes[2].strip() if is_gerente else partes[1].strip()))

            linhas = query.all()
            if not linhas: continue

            if is_gerente:
                total_base = sum([l.vol_bottomup for l in linhas]) 
                soma_dist = 0
                for i, l in enumerate(linhas):
                    rateado = int(ajuste.novo_volume) - soma_dist if i == len(linhas) - 1 else int(round(int(ajuste.novo_volume) * (l.vol_bottomup / total_base if total_base > 0 else 1.0 / len(linhas))))
                    soma_dist += rateado
                    l.vol_bottomup = l.vol_final = rateado
            else:
                RateioService.aplicar_historico_amplo(db, linhas, int(ajuste.novo_volume), nivel_rateio)

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == origem_real).first()
        if not registro: db.add(ControleCiclo(ciclo_sop=ciclo, origem=origem_real, status='Fechado'))
        else: registro.status = 'Fechado'
        db.commit()

# =====================================================================
# 5. SCHEMAS (PYDANTIC)
# =====================================================================
class AjusteBase(BaseModel):
    mes_projetado: str
    novo_volume: int

class AjusteTopDown(AjusteBase): produto: str
class AjusteBottomUp(AjusteBase): nivel: str; chave: str; pmv_aplicado: float = 0.0
class AjusteGerente(AjusteBase): nivel: str; chave: str

class PayloadCongelar(BaseModel): origem_ajuste: str; ajustes: List[AjusteTopDown]
class PayloadCongelarBU(BaseModel): origem_ajuste: str; ajustes: List[AjusteBottomUp]
class PayloadAprovarGerente(BaseModel): ajustes: List[AjusteGerente]
class PayloadLockAll(BaseModel): gerente_nome: str = ""; acao: str
class PayloadToggleLock(BaseModel): origem: str
class PayloadFecharCiclo(BaseModel): origem: str

# =====================================================================
# 6. ENDPOINTS
# =====================================================================
@router.get("/status")
async def checar_status_ciclo(origem: str, db: Session = Depends(get_db)):
    c = get_current_cycle()
    status_dict = {ctrl.origem: ctrl.status == 'Fechado' for ctrl in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == c).all()}
    return {"is_fechado": status_dict.get(origem, False), "is_topdown_fechado": status_dict.get('Top-Down', False), "is_global_fechado": status_dict.get('S&OP-Final', False)}

@router.get("/filtros")
async def obter_filtros_busca(gerente_nome: str = None, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    q = db.query(DimCliente)
    filtro_gerente = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else gerente_nome
    if filtro_gerente: q = q.filter(func.trim(DimCliente.gerente_nome) == filtro_gerente.strip())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }

@router.post("/fechar")
async def fechar_ciclo(payload: PayloadFecharCiclo, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] == 'Executivo' and payload.origem != usuario['nome_vendedor']:
        raise HTTPException(status_code=403, detail="Você só pode fechar a sua própria carteira.")
    check_global_lock(db)
    
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == payload.origem).first()
    if not reg: db.add(ControleCiclo(ciclo_sop=get_current_cycle(), origem=payload.origem, status='Fechado'))
    else: reg.status = 'Fechado'
    db.commit()
    return {"status": "success"}

# --- MACRO (S&OP) ---
@router.get("/macro")
async def listar_macro(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        m2, m4 = get_projection_window()
        projecoes = QueryRepository.get_base_ibp_query(db, m2, m4, get_current_cycle())\
            .with_entities(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('v_ia'), func.sum(FatoIbpGranular.vol_topdown).label('v_td'), func.avg(FatoIbpGranular.pmv_aplicado).label('pmv'), func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec'), DimProduto.descricao, DimProduto.categoria, DimProduto.segmento, DimProduto.modelo_vencedor, DimProduto.acuracia_ia)\
            .group_by(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, DimProduto.descricao, DimProduto.categoria, DimProduto.segmento, DimProduto.modelo_vencedor, DimProduto.acuracia_ia).all()

        prod_map = defaultdict(lambda: {"meses": []})
        for r in projecoes:
            p = prod_map[r.sku]
            if not p.get('produto'): p.update({"produto": r.sku, "descricao": r.descricao, "categoria": r.categoria, "segmento": r.segmento, "modelo_vencedor": r.modelo_vencedor or "N/A", "acuracia_ia": float(r.acuracia_ia or 0)})
            p["meses"].append({"mes_banco": str(r.mes_projetado), "mes_str": r.mes_projetado.strftime("%b/%y").capitalize() if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado), "vol_ia": int(r.v_ia or 0), "vol_ajustado": int(r.v_td or 0), "pmv": float(r.rec/r.v_ia) if r.v_ia else float(r.pmv or 0)})
        return {"status": "success", "dados": list(prod_map.values())}
    except Exception as e: raise HTTPException(500, repr(e))

@router.get("/macro/grafico")
async def grafico_macro(produto: str, db: Session = Depends(get_db)):
    return {"status": "success", "dados": GraphService.build_timeline(db, {'sku': produto, 'use_bu': False})}

@router.post("/macro/congelar")
async def congelar_macro(payload: PayloadCongelar, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        AdjustmentService.processar_ajustes(db, payload.ajustes, get_current_cycle(), 'Top-Down')
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: db.rollback(); raise HTTPException(500, repr(e))

@router.post("/macro/reabrir")
async def reabrir_macro(db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        check_global_lock(db)
        if db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.status == 'Fechado', ControleCiclo.origem.notin_(['Top-Down', 'S&OP-Final'])).count():
            raise HTTPException(403, "Ordem Reversa Violada.")
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == 'Top-Down').first()
        if reg: reg.status = 'Aberto'; db.commit()
        return {"status": "success"}
    except HTTPException as e: raise e
    except Exception as e: db.rollback(); raise HTTPException(500, repr(e))

# --- MICRO (Executivo) ---
@router.get("/micro")
async def listar_micro(nivel_hierarquia: str, nome_responsavel: str, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None
        
        q = QueryRepository.get_base_ibp_query(db, *get_projection_window(), get_current_cycle())\
            .with_entities(DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('v_ia'), func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), func.avg(FatoIbpGranular.pmv_aplicado).label('pmv'))
        if filtro_vend: q = q.filter(func.trim(DimCliente.vendedor_nome) == filtro_vend.strip())
        if filtro_reg: q = q.filter(func.trim(DimCliente.regional) == filtro_reg.strip())
        if usuario['funcao'] == 'Gerente': q = q.filter(func.trim(DimCliente.gerente_nome) == usuario['gerente_nome'].strip())
        
        arvore = defaultdict(lambda: {"id": "", "nome": "", "tipo": "cliente", "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0}), "produtos": defaultdict(lambda: {"id": "", "nome": "", "tipo": "produto", "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0})})})
        for r in q.group_by(DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all():
            rz, sku, ms = str(r.razaosocial or "DESC"), r.sku, str(r.mes_projetado)
            rec = int(r.v_bu or 0) * float(r.pmv or 0)
            
            arvore[rz]["id"] = arvore[rz]["nome"] = rz
            arvore[rz]["produtos"][sku]["id"] = f"{rz}|{sku}"
            arvore[rz]["produtos"][sku]["nome"] = r.descricao
            
            for target in [arvore[rz]["meses"][ms], arvore[rz]["produtos"][sku]["meses"][ms]]:
                target["vol_ia"] += int(r.v_ia or 0); target["vol_ajustado"] += int(r.v_bu or 0); target["receita"] += rec; target["pmv"] = float(r.pmv or 0)

        dados = [{"id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], "subRows": [{"id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "produto": pk, "tipo": p["tipo"], "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in p["meses"].items()]} for pk, p in v["produtos"].items()]} for v in arvore.values()]
        return {"status": "success", "dados": dados}
    except Exception as e: raise HTTPException(500, repr(e))

@router.get("/micro/grafico")
async def grafico_micro(chave_matriz: str, nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    p = chave_matriz.split('|'); razao, sku = p[0], p[1] if len(p) > 1 else None
    vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
    return {"status": "success", "dados": GraphService.build_timeline(db, {'razaosocial': razao.strip(), 'sku': sku.strip() if sku else None, 'vendedor': vend.strip() if vend else None, 'use_bu': True, 'use_final': False})}

@router.post("/micro/congelar")
async def congelar_micro(nome_responsavel: str, payload: PayloadCongelarBU, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    origem = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
    try:
        AdjustmentService.processar_ajustes(db, payload.ajustes, get_current_cycle(), 'Bottom-Up', origem)
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: db.rollback(); raise HTTPException(500, repr(e))

# --- GERENCIAMENTO ---
@router.post("/gerenciamento/toggle-lock")
async def toggle_lock_gerenciamento(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        check_global_lock(db); verificar_vendedor_online(db, payload.origem)
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle(), ControleCiclo.origem == payload.origem).first()
        if reg: reg.status = 'Aberto' if reg.status == 'Fechado' else 'Fechado'
        else: db.add(ControleCiclo(ciclo_sop=get_current_cycle(), origem=payload.origem, status='Fechado'))
        db.commit()
        return {"status": "success"}
    except HTTPException as e: raise e
    except Exception as e: db.rollback(); raise HTTPException(500, repr(e))

@router.get("/gerenciamento/vendedores")
async def listar_gerenciamento(gerente_nome: str = None, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        # AQUI FOI ADICIONADA A COLUNA VOL_TOPDOWN ('v_td') PARA SER USADA COMO REFERÊNCIA NO FRONT
        q = QueryRepository.get_base_ibp_query(db, *get_projection_window(), get_current_cycle())\
            .with_entities(
                FatoIbpGranular.vendedor_nome, 
                FatoIbpGranular.sku, 
                FatoIbpGranular.mes_projetado, 
                func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
                func.sum(FatoIbpGranular.vol_topdown).label('v_td'),
                func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
                func.avg(FatoIbpGranular.pmv_aplicado).label('pmv'), 
                DimCliente.razaosocial, 
                DimProduto.descricao
            ).filter(FatoIbpGranular.vendedor_nome.isnot(None))
            
        filtro_gerente = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else gerente_nome
        if filtro_gerente: q = q.filter(func.trim(DimCliente.gerente_nome) == filtro_gerente.strip())

        status_dict = {c.origem.strip(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle()).all() if c.origem}
        
        arvore = defaultdict(lambda: {"id": "", "nome": "", "tipo": "vendedor", "status": "Aberto", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0}), "clientes": defaultdict(lambda: {"id": "", "nome": "", "tipo": "cliente", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0}), "produtos": defaultdict(lambda: {"id": "", "nome": "", "tipo": "produto", "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0})})})})
        
        for r in q.group_by(FatoIbpGranular.vendedor_nome, DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado).all():
            v, rz, sku, ms = str(r.vendedor_nome).strip(), str(r.razaosocial or "DESC"), r.sku, str(r.mes_projetado)
            rec = int(r.v_bu or 0) * float(r.pmv or 0)

            arvore[v]["id"] = arvore[v]["nome"] = v; arvore[v]["status"] = status_dict.get(v, "Aberto")
            arvore[v]["clientes"][rz]["id"] = f"{v}|{rz}"; arvore[v]["clientes"][rz]["nome"] = rz
            arvore[v]["clientes"][rz]["produtos"][sku]["id"] = f"{v}|{rz}|{sku}"; arvore[v]["clientes"][rz]["produtos"][sku]["nome"] = r.descricao
            
            for t in [arvore[v]["meses"][ms], arvore[v]["clientes"][rz]["meses"][ms], arvore[v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                t["vol_ia"] += int(r.v_ia or 0); t["vol_td"] += int(r.v_td or 0); t["vol_ajustado"] += int(r.v_bu or 0); t["receita"] += rec

        dados = [{"id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], "status": v["status"], "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], "subRows": [{"id": c["id"], "chave_matriz": c["id"], "nome": c["nome"], "tipo": c["tipo"], "status": v["status"], "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in c["meses"].items()], "subRows": [{"id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "tipo": p["tipo"], "status": v["status"], "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in p["meses"].items()]} for p in c["produtos"].values()]} for c in v["clientes"].values()]} for v in arvore.values()]
        return {"status": "success", "dados": dados}
    except Exception as e: raise HTTPException(500, repr(e))

@router.get("/gerenciamento/grafico")
async def grafico_gerencia(chave_matriz: str, db: Session = Depends(get_db)):
    p = chave_matriz.split('|')
    return {"status": "success", "dados": GraphService.build_timeline(db, {'vendedor': p[0].strip(), 'razaosocial': p[1].strip() if len(p)>1 else None, 'sku': p[2].strip() if len(p)>2 else None, 'use_bu': True, 'use_final': True})}

@router.post("/gerenciamento/aprovar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        AdjustmentService.processar_ajustes(db, payload.ajustes, get_current_cycle(), 'Bottom-Up', is_gerente=True)
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: db.rollback(); raise HTTPException(500, repr(e))

@router.post("/gerenciamento/lock-all")
async def lock_all(payload: PayloadLockAll, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        check_global_lock(db); c = get_current_cycle()
        q = QueryRepository.get_base_ibp_query(db, *get_projection_window(), c).with_entities(FatoIbpGranular.vendedor_nome).filter(FatoIbpGranular.vendedor_nome.isnot(None))
        filtro = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else payload.gerente_nome
        if filtro: q = q.filter(func.trim(DimCliente.gerente_nome) == filtro.strip())
            
        vendedores = {v[0].strip() for v in q.distinct().all() if v[0]}
        for v in vendedores: verificar_vendedor_online(db, v)
        
        status_alvo = 'Fechado' if payload.acao == 'Trancar' else 'Aberto'
        for v in vendedores:
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == c, ControleCiclo.origem == v).first()
            if reg: reg.status = status_alvo
            else: db.add(ControleCiclo(ciclo_sop=c, origem=v, status=status_alvo))
        db.commit()
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: db.rollback(); raise HTTPException(500, repr(e))

@router.get("/export/bottom-up")
async def exportar_bottom_up(db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        query = QueryRepository.get_base_ibp_query(db, *get_projection_window(), get_current_cycle())\
            .with_entities(FatoIbpGranular.vendedor_nome, FatoIbpGranular.cgc, DimCliente.razaosocial, DimCliente.cod_cliente, DimCliente.loja, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, FatoIbpGranular.vol_bottomup, FatoIbpGranular.vol_final, FatoIbpGranular.pmv_aplicado).all()

        if not query: raise HTTPException(404, "Sem dados para exportar.")
        
        df = pd.DataFrame([{"Vendedor": r.vendedor_nome, "Cód. Cliente": r.cod_cliente, "Loja": r.loja, "CGC": r.cgc, "Cliente": r.razaosocial, "SKU": r.sku, "Produto": r.descricao, "Mês Projetado": parse_date_safe(r.mes_projetado).strftime("%m/%Y"), "Volume IA": r.vol_ia, "Vol Top-Down": r.vol_topdown, "Vol Bottom-Up": r.vol_bottomup, "Vol Final": r.vol_final, "PMV": float(r.pmv_aplicado or 0), "Receita": r.vol_final * float(r.pmv_aplicado or 0)} for r in query])
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Base Granular')
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=consenso_granular.xlsx"})
    except Exception as e: raise HTTPException(500, repr(e))