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
    check_origin_lock, # <-- AQUI ESTÁ A NOVA BLINDAGEM IMPORTADA
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
        q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == filtro_gerente.strip().upper())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }

@router.get("") 
async def listar_micro(nivel_hierarquia: str, nome_responsavel: str, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()
        
        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        query_base = get_truth_query(db, ciclo, m2, m4)
        
        if filtro_vend: 
            query_base = query_base.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
        if filtro_reg: 
            query_base = query_base.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
        if usuario['funcao'] == 'Gerente': 
            query_base = query_base.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())

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

# =====================================================================
# ROTA DE STATUS DE TRAVA (NOVA)
# =====================================================================
@router.get("/status")
async def verificar_status_micro(nivel_hierarquia: str = 'vendedor', nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle()
        origem_trava = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        
        if not origem_trava:
            return {"is_fechado": False}
            
        registro = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo, 
            func.upper(func.trim(ControleCiclo.origem)) == origem_trava.strip().upper()
        ).first()
        
        return {"is_fechado": registro.status == 'Fechado' if registro else False}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_micro(nivel_hierarquia: str, nome_responsavel: str, payload: PayloadCongelarBU, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        
        # AQUI O BANCO DE DADOS É BLINDADO CONTRA SALVAMENTOS DE CARTEIRAS TRANCADAS
        origem_trava = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        check_origin_lock(db, ciclo, origem_trava)
        
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            
            query = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo))

            if filtro_vend: query = query.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
            if filtro_reg: query = query.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
            if usuario['funcao'] == 'Gerente': query = query.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())

            if ajuste.nivel in ['cliente', 'produto']: 
                query = query.filter(func.trim(DimCliente.razaosocial) == partes[0].strip())
            if ajuste.nivel == 'produto': 
                query = query.filter(FatoIbpGranular.sku == partes[1].strip())

            linhas = query.all()
            if not linhas: continue

            skus = list({l.sku for l in linhas if l.sku})
            cgcs = list({l.cgc for l in linhas if l.cgc})
            
            historico_query = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_hist'))\
                .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                .filter(FatoVendas.sku.in_(skus), FatoVendas.cgc.in_(cgcs), FatoVendas.data_pedido >= data_limite_str)
            
            if filtro_vend: historico_query = historico_query.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())

            historico = historico_query.group_by(FatoVendas.sku, FatoVendas.cgc).all()
                
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
                
                l.vol_bottomup = rateado
                l.vol_final = rateado

        origem_trava = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == origem_trava).first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=origem_trava, status='Fechado'))
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
async def grafico_micro(chave_matriz: str, nivel_hierarquia: str = 'vendedor', nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        p = chave_matriz.split('|')
        razao, sku = p[0], p[1] if len(p) > 1 else None
        
        m2_str, m4_str = get_projection_window()
        m2_date = parse_date_safe(m2_str) if isinstance(m2_str, str) else m2_str
        
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_anterior, ciclo_atual = get_previous_cycle(), get_current_cycle()
        
        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        # Desacopla do get_truth_query para evitar choques de GROUP BY nativos
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol_real'))\
            .join(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))
            
        q_ant = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_bottomup).label('vol_ant'))\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(FatoIbpGranular.ciclo_sop == ciclo_anterior)
            
        q_proj = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), func.sum(FatoIbpGranular.vol_bottomup).label('vol_consenso'))\
            .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(FatoIbpGranular.ciclo_sop == ciclo_atual)

        # Filtro Central na Dimensão (Garante os mesmos números da Tabela)
        if filtro_vend: 
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
        if filtro_reg: 
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
        if usuario['funcao'] == 'Gerente':
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())

        if razao: 
            q_hist = q_hist.filter(func.trim(DimCliente.razaosocial) == razao.strip())
            q_ant = q_ant.filter(func.trim(DimCliente.razaosocial) == razao.strip())
            q_proj = q_proj.filter(func.trim(DimCliente.razaosocial) == razao.strip())
        if sku: 
            q_hist = q_hist.filter(FatoVendas.sku == sku.strip())
            q_ant = q_ant.filter(FatoIbpGranular.sku == sku.strip())
            q_proj = q_proj.filter(FatoIbpGranular.sku == sku.strip())

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist.group_by('mes_ano').all()}
        ant_dict = {str(a.mes_projetado): int(a.vol_ant or 0) for a in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        projecoes = q_proj.group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = []
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str = dt.strftime('%Y-%m')
            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(), "data_iso": dt.strftime("%Y-%m-%d"),
                "Realizado": hist_dict.get(mes_str, 0), "IA": None, "Consenso": None, "CicloAnterior": None
            })

        mes_atual_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        proj_atual = next((p for p in projecoes if str(p.mes_projetado) == mes_atual_iso), None)
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)", "data_iso": mes_atual_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": int(proj_atual.vol_ia) if proj_atual else None,   
            "Consenso": None, "CicloAnterior": ant_dict.get(mes_atual_iso, None)
        })

        for p in projecoes:
            p_date = p.mes_projetado if isinstance(p.mes_projetado, datetime.date) else parse_date_safe(p.mes_projetado)
            if p_date <= mes_atual_inicio: continue 
            
            p_iso = str(p_date)
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(), "data_iso": p_iso,
                "Realizado": None, "IA": int(p.vol_ia or 0),
                "Consenso": int(p.vol_consenso or 0) if p_date >= m2_date else None,
                "CicloAnterior": ant_dict.get(p_iso, None)
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))