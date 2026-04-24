from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd
from fastapi.responses import StreamingResponse
from collections import defaultdict

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, Usuario, FatoVendas
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

router = APIRouter(prefix="/api/v1/consensus/gerenciamento", tags=["Consenso Gerenciamento"])

# =====================================================================
# PERMISSÕES E PROTEÇÕES
# =====================================================================
def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")
    return usuario

def verificar_vendedor_online(db: Session, vendedor_nome: str):
    vendedor_user = db.query(Usuario).filter(func.upper(func.trim(Usuario.nome_vendedor)) == vendedor_nome.strip().upper()).first()
    if vendedor_user and vendedor_user.ultima_atividade and (datetime.datetime.utcnow() - vendedor_user.ultima_atividade).total_seconds() < 60:
        raise HTTPException(status_code=403, detail=f"⚠️ CONCORRÊNCIA: O executivo {vendedor_nome} está online e com a plataforma aberta neste exato momento.")

# =====================================================================
# SCHEMAS
# =====================================================================
class AjusteGerente(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGerente(BaseModel):
    ajustes: List[AjusteGerente]

class PayloadLockAll(BaseModel):
    gerente_nome: str = ""
    acao: str

class PayloadToggleLock(BaseModel):
    origem: str

# =====================================================================
# ROTAS DE FILTROS E LISTAGEM
# =====================================================================

@router.get("/filtros")
async def obter_filtros_gerencia(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    q = db.query(DimCliente)
    if usuario['funcao'] == 'Gerente':
        q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }

@router.get("/vendedores")
async def listar_gerenciamento(nivel_filtro: str = None, valor_filtro: str = None, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()
        query_base = get_truth_query(db, ciclo, m2, m4)
        
        # Filtro de Hierarquia do Gerente Logado
        if usuario['funcao'] == 'Gerente':
            query_base = query_base.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())
            
        # Filtros Adicionais da UI
        if nivel_filtro == 'vendedor' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == valor_filtro.strip().upper())
        elif nivel_filtro == 'regional' and valor_filtro:
            query_base = query_base.filter(func.upper(func.trim(DimCliente.regional)) == valor_filtro.strip().upper())

        resultados = query_base.with_entities(
            DimCliente.vendedor_nome, DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, 
            FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu')
        ).group_by(
            DimCliente.vendedor_nome, DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        status_dict = {c.origem.strip().upper(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all() if c.origem}
        
        # AQUI FOI A CORREÇÃO PRINCIPAL: Estrutura alinhada com o Bottom-Up (sem vol_td)
        arvore = defaultdict(lambda: {
            "id": "", "nome": "", "tipo": "vendedor", "status": "Aberto", 
            "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0}), 
            "clientes": defaultdict(lambda: {
                "id": "", "nome": "", "tipo": "cliente", 
                "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0}), 
                "produtos": defaultdict(lambda: {
                    "id": "", "nome": "", "tipo": "produto", 
                    "meses": defaultdict(lambda: {"vol_ia":0, "vol_ajustado":0, "receita":0})
                })
            })
        })
        
        for r in resultados:
            v = str(r.vendedor_nome or "SEM VENDEDOR").strip()
            rz = str(r.razaosocial or "DESC").strip()
            sku, ms = r.sku, str(r.mes_projetado)
            rec = float(r.rec_bu or 0)

            arvore[v]["id"] = arvore[v]["nome"] = v
            arvore[v]["status"] = status_dict.get(v.upper(), "Aberto")
            arvore[v]["clientes"][rz]["id"] = f"{v}|{rz}"
            arvore[v]["clientes"][rz]["nome"] = rz
            arvore[v]["clientes"][rz]["produtos"][sku]["id"] = f"{v}|{rz}|{sku}"
            arvore[v]["clientes"][rz]["produtos"][sku]["nome"] = r.descricao
            
            for t in [arvore[v]["meses"][ms], arvore[v]["clientes"][rz]["meses"][ms], arvore[v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                t["vol_ia"] += int(r.v_ia or 0)
                t["vol_ajustado"] += int(r.v_bu or 0)
                t["receita"] += rec

        dados = [
            {
                "id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], "status": v["status"], 
                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], 
                "subRows": [
                    {
                        "id": c["id"], "chave_matriz": c["id"], "nome": c["nome"], "tipo": c["tipo"], 
                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in c["meses"].items()], 
                        "subRows": [
                            {
                                "id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "tipo": p["tipo"], 
                                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in p["meses"].items()]
                            } for p in c["produtos"].values()
                        ]
                    } for c in v["clientes"].values()
                ]
            } for v in arvore.values()
        ]
        return {"status": "success", "dados": dados}
    except Exception as e: 
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_gerenciamento(chave_matriz: str, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        p = chave_matriz.split('|')
        vendedor_alvo = p[0]
        cliente_alvo = p[1] if len(p) > 1 else None
        sku_alvo = p[2] if len(p) > 2 else None
        
        m2_str, _ = get_projection_window()
        m2_date = parse_date_safe(m2_str)
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_ant, ciclo_atual = get_previous_cycle(), get_current_cycle()

        # 1. Base Queries
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol'))\
                   .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                   .filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))
        
        q_ant = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_bottomup).label('vol'))\
                  .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                  .filter(FatoIbpGranular.ciclo_sop == ciclo_ant)
        
        q_proj = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_bottomup).label('bu'))\
                   .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                   .filter(FatoIbpGranular.ciclo_sop == ciclo_atual)

        # 2. Aplica Filtros Hierárquicos (EXPLICITAMENTE)
        if usuario['funcao'] == 'Gerente':
            g_nome = usuario['gerente_nome'].strip().upper()
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome)
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome)
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome)

        if vendedor_alvo: 
            v_nome = vendedor_alvo.strip().upper()
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == v_nome)
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == v_nome)
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == v_nome)
            
        if cliente_alvo: 
            c_nome = cliente_alvo.strip().upper()
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.razaosocial)) == c_nome)
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.razaosocial)) == c_nome)
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.razaosocial)) == c_nome)
            
        if sku_alvo: 
            s_nome = sku_alvo.strip()
            # Histórico usa SKU da FatoVendas, Projeção usa SKU da FatoIbp
            q_hist = q_hist.filter(FatoVendas.sku == s_nome)
            q_ant = q_ant.filter(FatoIbpGranular.sku == s_nome)
            q_proj = q_proj.filter(FatoIbpGranular.sku == s_nome)

        # 3. Executa e Mapeia
        hist_dict = {h.mes_ano: int(h.vol or 0) for h in q_hist.group_by('mes_ano').all()}
        ant_dict = {str(a.mes_projetado): int(a.vol or 0) for a in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        proj_res = q_proj.group_by(FatoIbpGranular.mes_projetado).all()

        timeline = []
        # Passado (24 meses)
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(), "data_iso": dt.strftime("%Y-%m-%d"),
                "Realizado": hist_dict.get(dt.strftime('%Y-%m'), 0), "IA": None, "Consenso": None, "CicloAnterior": None
            })
        
        # Mês Corrente (S&OE)
        curr_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        p_atual = next((p for p in proj_res if str(p.mes_projetado) == curr_iso), None)
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)", "data_iso": curr_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": int(p_atual.ia) if p_atual else None, "Consenso": None, "CicloAnterior": ant_dict.get(curr_iso)
        })

        # Futuro
        for p in sorted(proj_res, key=lambda x: str(x.mes_projetado)):
            p_date = p.mes_projetado if isinstance(p.mes_projetado, datetime.date) else parse_date_safe(p.mes_projetado)
            if p_date <= mes_atual_inicio: continue
            p_iso = str(p_date)
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(), "data_iso": p_iso,
                "Realizado": None, "IA": int(p.ia or 0), 
                "Consenso": int(p.bu or 0) if p_date >= m2_date else None, 
                "CicloAnterior": ant_dict.get(p_iso)
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/aprovar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)

        # =================================================================
        # CORREÇÃO FASE 2: Exige que a Fase 1 (Top-Down) esteja fechada
        # =================================================================
        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A estratégia macro ainda não foi liberada pela Diretoria (Fase 1). Aguarde.")
        
        vendedores_afetados = list({a.chave.split('|')[0].strip() for a in payload.ajustes})
        for v in vendedores_afetados: 
            verificar_vendedor_online(db, v)

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            vendedor_alvo = partes[0].strip()
            
            query = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo)).filter(
                func.upper(func.trim(DimCliente.vendedor_nome)) == vendedor_alvo.upper()
            )

            if ajuste.nivel in ['cliente', 'produto']: 
                query = query.filter(func.upper(func.trim(DimCliente.razaosocial)) == partes[1].strip().upper())
            if ajuste.nivel == 'produto': 
                query = query.filter(FatoIbpGranular.sku == partes[2].strip())

            linhas = query.all()
            if not linhas: continue

            total_base = sum([l.vol_bottomup for l in linhas]) 
            soma_dist = 0
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = int(ajuste.novo_volume) - soma_dist
                else:
                    rateado = int(round(int(ajuste.novo_volume) * (l.vol_bottomup / total_base if total_base > 0 else 1.0 / len(linhas))))
                    soma_dist += rateado
                l.vol_bottomup = rateado
                l.vol_final = rateado

        db.commit()
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/toggle-lock")
async def toggle_lock_gerenciamento(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        verificar_vendedor_online(db, payload.origem)
        
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)) == payload.origem.strip().upper()).first()
        if reg: 
            reg.status = 'Aberto' if reg.status == 'Fechado' else 'Fechado'
        else: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=payload.origem, status='Fechado'))
        db.commit()
        return {"status": "success"}
    except HTTPException as e: raise e
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/lock-all")
async def lock_all(payload: PayloadLockAll, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)

        # =================================================================
        # CORREÇÃO FASE 2: Exige que a Fase 1 (Top-Down) esteja fechada
        # =================================================================
        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A estratégia macro ainda não foi liberada pela Diretoria (Fase 1). Aguarde.")
        
        m2, m4 = get_projection_window()
        
        q = get_truth_query(db, ciclo, m2, m4).with_entities(FatoIbpGranular.vendedor_nome).filter(FatoIbpGranular.vendedor_nome.isnot(None))
        filtro = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else payload.gerente_nome
        if filtro: 
            q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == filtro.strip().upper())
            
        vendedores = {v[0].strip() for v in q.distinct().all() if v[0]}
        for v in vendedores: 
            verificar_vendedor_online(db, v)
        
        status_alvo = 'Fechado' if payload.acao == 'Trancar' else 'Aberto'
        for v in vendedores:
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, func.upper(func.trim(ControleCiclo.origem)) == v.upper()).first()
            if reg: 
                reg.status = status_alvo
            else: 
                db.add(ControleCiclo(ciclo_sop=ciclo, origem=v, status=status_alvo))
        db.commit()
        return {"status": "success"}
    except HTTPException as he: 
        raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))