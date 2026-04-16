from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

# O Cérebro Mestre
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_projection_window,
    get_truth_query,
    check_global_lock,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/micro", tags=["Consenso Bottom-Up"])

# =====================================================================
# SCHEMAS
# =====================================================================
class AjusteBottomUp(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadCongelarBU(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteBottomUp]

# =====================================================================
# ENDPOINTS PRINCIPAIS (A VISÃO DA TRINCHEIRA / EXECUTIVO)
# =====================================================================
@router.get("/filtros")
async def obter_filtros_busca(gerente_nome: str = None, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    q = db.query(DimCliente)
    filtro_gerente = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else gerente_nome
    if filtro_gerente: 
        q = q.filter(func.trim(DimCliente.gerente_nome) == filtro_gerente.strip())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }


@router.get("")  # <-- CORREÇÃO 2: Sem a barra!
async def listar_micro(nivel_hierarquia: str, nome_responsavel: str, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()
        
        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        query_base = get_truth_query(db, ciclo, m2, m4)
        
        if filtro_vend: 
            query_base = query_base.filter(func.trim(FatoIbpGranular.vendedor_nome) == filtro_vend.strip())
        if filtro_reg: 
            query_base = query_base.filter(func.trim(DimCliente.regional) == filtro_reg.strip())
        if usuario['funcao'] == 'Gerente': 
            query_base = query_base.filter(func.trim(DimCliente.gerente_nome) == usuario['gerente_nome'].strip())

        resultados = query_base.with_entities(
            DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'), 
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv'),
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu')
        ).group_by(
            DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()
        
        arvore = defaultdict(lambda: {
            "id": "", "nome": "", "tipo": "cliente", 
            "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0}), 
            "produtos": defaultdict(lambda: {
                "id": "", "nome": "", "tipo": "produto", 
                "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0})
            })
        })
        
        for r in resultados:
            rz, sku, ms = str(r.razaosocial or "DESC"), r.sku, str(r.mes_projetado)
            rec = float(r.rec_bu or 0)
            
            arvore[rz]["id"] = arvore[rz]["nome"] = rz
            arvore[rz]["produtos"][sku]["id"] = f"{rz}|{sku}"
            arvore[rz]["produtos"][sku]["nome"] = r.descricao
            
            for target in [arvore[rz]["meses"][ms], arvore[rz]["produtos"][sku]["meses"][ms]]:
                target["vol_ia"] += int(r.v_ia or 0)
                target["vol_ajustado"] += int(r.v_bu or 0)
                target["receita"] += rec
                target["pmv"] = float(r.pmv or 0)

        dados = [
            {
                "id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], 
                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], 
                "subRows": [
                    {
                        "id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "produto": pk, "tipo": p["tipo"], 
                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in p["meses"].items()]
                    } for pk, p in v["produtos"].items()
                ]
            } for v in arvore.values()
        ]
        return {"status": "success", "dados": dados}
    except Exception as e: 
        raise HTTPException(500, repr(e))


@router.post("/congelar")
async def congelar_micro(nome_responsavel: str, payload: PayloadCongelarBU, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    origem = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            
            query = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo)).filter(
                func.trim(FatoIbpGranular.vendedor_nome) == origem.strip()
            )

            # Define se a alteração foi no Cliente Raiz ou num Produto específico do Cliente
            if ajuste.nivel in ['cliente', 'produto']: 
                query = query.filter(func.trim(DimCliente.razaosocial) == partes[0].strip())
            if ajuste.nivel == 'produto': 
                query = query.filter(FatoIbpGranular.sku == partes[1].strip())

            linhas = query.all()
            if not linhas: continue

            # Rateio Histórico Local da Trincheira
            skus = list({l.sku for l in linhas if l.sku})
            cgcs = list({l.cgc for l in linhas if l.cgc})
            
            historico = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_hist'))\
                .filter(FatoVendas.sku.in_(skus), FatoVendas.cgc.in_(cgcs), FatoVendas.data_pedido >= data_limite_str)\
                .group_by(FatoVendas.sku, FatoVendas.cgc).all()
                
            dict_hist = {f"{h.sku}_{h.cgc}": float(h.vol_hist or 0) for h in historico}
            soma_hist = sum([dict_hist.get(f"{l.sku}_{l.cgc}", 0) for l in linhas])
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    peso = dict_hist.get(f"{l.sku}_{l.cgc}", 0) / soma_hist if soma_hist > 0 else 1.0 / len(linhas)
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                
                # Como a alteração é feita pelo Comercial, gravamos em Bottom-Up e Final.
                # A meta Top-Down fica intacta para podermos medir a variação (Aderência) depois.
                l.vol_bottomup = rateado
                l.vol_final = rateado

        # Trava a carteira do Vendedor
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == origem).first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=origem, status='Fechado'))
        else: 
            registro.status = 'Fechado'
            
        db.commit()
        return {"status": "success"}
    except HTTPException as he: 
        raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_micro(chave_matriz: str, nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        p = chave_matriz.split('|')
        razao, sku = p[0], p[1] if len(p) > 1 else None
        vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        
        m2, m4 = get_projection_window()
        hoje = datetime.date.today()
        ciclo_anterior, ciclo_atual = get_previous_cycle(), get_current_cycle()
        
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol_real'))\
            .join(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))
        q_ant = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_bottomup).label('vol_ant'))\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(FatoIbpGranular.ciclo_sop == ciclo_anterior)
        q_proj = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), func.sum(FatoIbpGranular.vol_bottomup).label('vol_consenso'))\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(FatoIbpGranular.ciclo_sop == ciclo_atual)

        if vend: q_hist = q_hist.filter(func.trim(FatoVendas.vendedor_nome) == vend.strip()); q_ant = q_ant.filter(func.trim(FatoIbpGranular.vendedor_nome) == vend.strip()); q_proj = q_proj.filter(func.trim(FatoIbpGranular.vendedor_nome) == vend.strip())
        if razao: q_hist = q_hist.filter(func.trim(DimCliente.razaosocial) == razao.strip()); q_ant = q_ant.filter(func.trim(DimCliente.razaosocial) == razao.strip()); q_proj = q_proj.filter(func.trim(DimCliente.razaosocial) == razao.strip())
        if sku: q_hist = q_hist.filter(FatoVendas.sku == sku.strip()); q_ant = q_ant.filter(FatoIbpGranular.sku == sku.strip()); q_proj = q_proj.filter(FatoIbpGranular.sku == sku.strip())

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist.group_by(func.to_char(FatoVendas.data_pedido, 'YYYY-MM')).all()}
        ant_dict = {p.mes_projetado.strftime('%Y-%m-%d') if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado): int(p.vol_ant or 0) for p in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        projecoes = q_proj.group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = [{"name": (hoje - relativedelta(months=i)).strftime("%b/%y").capitalize(), "data_iso": (hoje - relativedelta(months=i)).replace(day=1).strftime("%Y-%m-%d"), "Realizado": hist_dict.get((hoje - relativedelta(months=i)).strftime('%Y-%m'), 0), "IA": None, "Consenso": None, "CicloAnterior": None} for i in range(24, 0, -1)]
        
        for p in projecoes:
            p_str = p.mes_projetado.strftime('%Y-%m-%d') if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado)
            timeline.append({
                "name": p.mes_projetado.strftime("%b/%y").capitalize() if isinstance(p.mes_projetado, datetime.date) else str(p.mes_projetado), 
                "data_iso": p_str, "Realizado": None, "IA": int(p.vol_ia or 0), 
                "Consenso": int(p.vol_consenso or 0) if m2 <= p_str <= m4 else None, 
                "CicloAnterior": ant_dict.get(p_str, None)
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))