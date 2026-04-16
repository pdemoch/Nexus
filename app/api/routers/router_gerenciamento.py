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
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, Usuario
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

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso Restrito.")
    return usuario

def verificar_vendedor_online(db: Session, vendedor_nome: str):
    vendedor_user = db.query(Usuario).filter(func.trim(Usuario.nome_vendedor) == vendedor_nome.strip()).first()
    if vendedor_user and vendedor_user.ultima_atividade and (datetime.datetime.utcnow() - vendedor_user.ultima_atividade).total_seconds() < 60:
        raise HTTPException(status_code=403, detail=f"⚠️ CONCORRÊNCIA: O executivo {vendedor_nome} está com a plataforma aberta neste exato momento.")

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
# ENDPOINTS PRINCIPAIS (A VISÃO DO GERENTE)
# =====================================================================
@router.get("/vendedores")
async def listar_gerenciamento(gerente_nome: str = None, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()

        # 1. Busca a Verdade Absoluta
        query_base = get_truth_query(db, ciclo, m2, m4)
        
        # 2. Filtra estritamente por quem tem vendedor associado
        query_base = query_base.filter(FatoIbpGranular.vendedor_nome.isnot(None))
            
        filtro_gerente = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else gerente_nome
        if filtro_gerente: 
            query_base = query_base.filter(func.trim(DimCliente.gerente_nome) == filtro_gerente.strip())

        # 3. Calcula atómicamente a receita no banco (Fim do Efeito Mix!)
        resultados = query_base.with_entities(
            FatoIbpGranular.vendedor_nome, 
            FatoIbpGranular.sku, 
            FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'), 
            DimCliente.razaosocial, 
            DimProduto.descricao
        ).group_by(
            FatoIbpGranular.vendedor_nome, DimCliente.razaosocial, FatoIbpGranular.sku, 
            DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        status_dict = {c.origem.strip(): c.status for c in db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo).all() if c.origem}
        
        arvore = defaultdict(lambda: {
            "id": "", "nome": "", "tipo": "vendedor", "status": "Aberto", 
            "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0}), 
            "clientes": defaultdict(lambda: {
                "id": "", "nome": "", "tipo": "cliente", 
                "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0}), 
                "produtos": defaultdict(lambda: {
                    "id": "", "nome": "", "tipo": "produto", 
                    "meses": defaultdict(lambda: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0})
                })
            })
        })
        
        for r in resultados:
            v, rz, sku, ms = str(r.vendedor_nome).strip(), str(r.razaosocial or "DESC"), r.sku, str(r.mes_projetado)
            rec = float(r.rec_bu or 0)

            arvore[v]["id"] = arvore[v]["nome"] = v
            arvore[v]["status"] = status_dict.get(v, "Aberto")
            arvore[v]["clientes"][rz]["id"] = f"{v}|{rz}"
            arvore[v]["clientes"][rz]["nome"] = rz
            arvore[v]["clientes"][rz]["produtos"][sku]["id"] = f"{v}|{rz}|{sku}"
            arvore[v]["clientes"][rz]["produtos"][sku]["nome"] = r.descricao
            
            for t in [arvore[v]["meses"][ms], arvore[v]["clientes"][rz]["meses"][ms], arvore[v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                t["vol_ia"] += int(r.v_ia or 0)
                t["vol_td"] += int(r.v_td or 0)
                t["vol_ajustado"] += int(r.v_bu or 0)
                t["receita"] += rec

        dados = [
            {
                "id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], "status": v["status"], 
                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], 
                "subRows": [
                    {
                        "id": c["id"], "chave_matriz": c["id"], "nome": c["nome"], "tipo": c["tipo"], "status": v["status"], 
                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in c["meses"].items()], 
                        "subRows": [
                            {
                                "id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "tipo": p["tipo"], "status": v["status"], 
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

@router.post("/aprovar")
async def aprovar_gerencia(payload: PayloadAprovarGerente, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        
        vendedores_afetados = list({a.chave.split('|')[0].strip() for a in payload.ajustes})
        for v in vendedores_afetados: 
            verificar_vendedor_online(db, v)

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            vendedor_alvo = partes[0].strip()
            
            query = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo)).filter(
                func.trim(FatoIbpGranular.vendedor_nome) == vendedor_alvo
            )

            if ajuste.nivel in ['cliente', 'produto']: 
                query = query.filter(func.trim(DimCliente.razaosocial) == partes[1].strip())
            if ajuste.nivel == 'produto': 
                query = query.filter(FatoIbpGranular.sku == partes[2].strip())

            linhas = query.all()
            if not linhas: continue

            # O Gerente ajusta sobre a base que o vendedor construiu (vol_bottomup)
            total_base = sum([l.vol_bottomup for l in linhas]) 
            soma_dist = 0
            for i, l in enumerate(linhas):
                rateado = int(ajuste.novo_volume) - soma_dist if i == len(linhas) - 1 else int(round(int(ajuste.novo_volume) * (l.vol_bottomup / total_base if total_base > 0 else 1.0 / len(linhas))))
                soma_dist += rateado
                l.vol_bottomup = rateado
                l.vol_final = rateado

        db.commit()
        return {"status": "success"}
    except HTTPException as he: 
        raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/toggle-lock")
async def toggle_lock_gerenciamento(payload: PayloadToggleLock, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        verificar_vendedor_online(db, payload.origem)
        
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == payload.origem).first()
        if reg: 
            reg.status = 'Aberto' if reg.status == 'Fechado' else 'Fechado'
        else: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=payload.origem, status='Fechado'))
        db.commit()
        return {"status": "success"}
    except HTTPException as e: 
        raise e
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/lock-all")
async def lock_all(payload: PayloadLockAll, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        m2, m4 = get_projection_window()
        
        q = get_truth_query(db, ciclo, m2, m4).with_entities(FatoIbpGranular.vendedor_nome).filter(FatoIbpGranular.vendedor_nome.isnot(None))
        filtro = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else payload.gerente_nome
        if filtro: 
            q = q.filter(func.trim(DimCliente.gerente_nome) == filtro.strip())
            
        vendedores = {v[0].strip() for v in q.distinct().all() if v[0]}
        for v in vendedores: 
            verificar_vendedor_online(db, v)
        
        status_alvo = 'Fechado' if payload.acao == 'Trancar' else 'Aberto'
        for v in vendedores:
            reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == v).first()
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

@router.get("/export/bottom-up")
async def exportar_bottom_up(db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    try:
        m2, m4 = get_projection_window()
        query = get_truth_query(db, get_current_cycle(), m2, m4).with_entities(
            FatoIbpGranular.vendedor_nome, FatoIbpGranular.cgc, DimCliente.razaosocial, 
            DimCliente.cod_cliente, DimCliente.loja, FatoIbpGranular.sku, DimProduto.descricao, 
            FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, 
            FatoIbpGranular.vol_bottomup, FatoIbpGranular.vol_final, FatoIbpGranular.pmv_aplicado
        ).all()

        if not query: raise HTTPException(404, "Sem dados para exportar.")
        
        df = pd.DataFrame([{
            "Vendedor": r.vendedor_nome, "Cód. Cliente": r.cod_cliente, "Loja": r.loja, "CGC": r.cgc, 
            "Cliente": r.razaosocial, "SKU": r.sku, "Produto": r.descricao, 
            "Mês Projetado": parse_date_safe(r.mes_projetado).strftime("%m/%Y"), 
            "Volume IA": r.vol_ia, "Vol Top-Down": r.vol_topdown, "Vol Bottom-Up": r.vol_bottomup, 
            "Vol Final": r.vol_final, "PMV": float(r.pmv_aplicado or 0), 
            "Receita": r.vol_final * float(r.pmv_aplicado or 0)
        } for r in query])
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: 
            df.to_excel(writer, index=False, sheet_name='Base Granular')
        buffer.seek(0)
        return StreamingResponse(
            buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            headers={"Content-Disposition": "attachment; filename=consenso_granular.xlsx"}
        )
    except Exception as e: 
        raise HTTPException(500, repr(e))