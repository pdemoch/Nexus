import datetime
import math
from typing import List
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas, DimCliente
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import get_current_cycle, parse_date_safe

router = APIRouter(prefix="/api/v1/consensus/micro", tags=["Consenso Carteira (Bottom-Up)"])

class AjusteCarteira(BaseModel):
    chave: str
    mes_projetado: str
    novo_volume: int 

class PayloadAprovarCarteira(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteCarteira]

@router.get("/filtros")
async def filtros_micro(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    funcao = usuario.get('funcao')
    nome = usuario.get('nome')
    
    q = db.query(DimCliente.coordenador, DimCliente.vendedor).filter(DimCliente.coordenador.isnot(None), DimCliente.vendedor.isnot(None))
    
    if funcao == 'Gerente':
        q = q.filter(DimCliente.gerente == nome)
    elif funcao == 'Coordenador':
        q = q.filter(DimCliente.coordenador == nome)
    elif funcao == 'Vendedor':
        q = q.filter(DimCliente.vendedor == nome)
        
    res = q.distinct().all()
    coords = sorted(list(set([r.coordenador for r in res])))
    vends = sorted(list(set([r.vendedor for r in res])))
    
    return {"coordenadores": coords, "vendedores": vends}

@router.get("")
async def carregar_carteira(nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)
        funcao = usuario.get('funcao')
        nome_usuario = usuario.get('nome')

        hoje = datetime.date.today()
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        # 1. Pega o Histórico de FATURAMENTO (R$) dos últimos 4 meses para Rateio Proporcional
        data_hist_inicio = hoje - relativedelta(months=4)
        q_hist = db.query(
            FatoVendas.cgc, FatoVendas.sku, 
            func.sum(FatoVendas.qt_pedido * FatoVendas.vl_pedido).label('fat_hist')
        ).filter(FatoVendas.data_pedido >= data_hist_inicio).group_by(FatoVendas.cgc, FatoVendas.sku).all()
        peso_fat_dict = {f"{r.cgc}|{r.sku}": float(r.fat_hist or 0) for r in q_hist}

        # 2. Query Principal: Teto (vol_meta) e Base Atual (vol_bottomup)
        query = db.query(
            DimCliente.gerente, DimCliente.coordenador, DimCliente.vendedor, DimCliente.razaosocial, DimCliente.cgc,
            FatoIbpGranular.sku, DimProduto.descricao,
            FatoIbpGranular.mes_projetado,
            FatoIbpGranular.vol_meta,     
            FatoIbpGranular.vol_bottomup, 
            FatoIbpGranular.pmv_aplicado
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(FatoIbpGranular.ciclo_sop == ciclo, FatoIbpGranular.mes_projetado >= data_ini, FatoIbpGranular.mes_projetado <= data_fim)

        # RLS (Row Level Security) e Filtro da Tela
        if funcao == 'Gerente':
            query = query.filter(DimCliente.gerente == nome_usuario)
            if nome_responsavel: query = query.filter(DimCliente.coordenador == nome_responsavel)
        elif funcao == 'Coordenador':
            query = query.filter(DimCliente.coordenador == nome_usuario)
            if nome_responsavel: query = query.filter(DimCliente.vendedor == nome_responsavel)
        elif funcao == 'Vendedor':
            query = query.filter(DimCliente.vendedor == nome_usuario)

        resultados = query.all()

        # 3. Construção da Árvore Mutante (Adapta-se ao Perfil logado)
        arvore = {}
        for r in resultados:
            coord = r.coordenador or 'SEM COORDENADOR'
            vend = r.vendedor or 'SEM VENDEDOR'
            cli = r.razaosocial or 'CLIENTE INDEFINIDO'
            cgc = r.cgc
            sku = r.sku
            ms = str(r.mes_projetado)
            peso_fat = peso_fat_dict.get(f"{cgc}|{sku}", 0)

            niveis = []

            if funcao in ['Administrador', 'Diretoria', 'Gerente']:
                if coord not in arvore: arvore[coord] = {"id": coord, "chave_matriz": coord, "nome": coord, "tipo": "coordenador", "meses_map": {}, "subRows": {}}
                n1 = arvore[coord]
                
                k2 = f"{coord}|{vend}"
                if k2 not in n1["subRows"]: n1["subRows"][k2] = {"id": k2, "chave_matriz": k2, "nome": vend, "tipo": "vendedor", "meses_map": {}, "subRows": {}}
                n2 = n1["subRows"][k2]
                
                k3 = f"{k2}|{cgc}"
                if k3 not in n2["subRows"]: n2["subRows"][k3] = {"id": k3, "chave_matriz": k3, "nome": cli, "tipo": "cliente", "meses_map": {}, "subRows": {}}
                n3 = n2["subRows"][k3]
                
                k4 = f"{k3}|{sku}"
                if k4 not in n3["subRows"]: n3["subRows"][k4] = {"id": k4, "chave_matriz": k4, "nome": r.descricao, "produto": sku, "tipo": "produto", "meses_map": {}, "peso_fat": peso_fat}
                n4 = n3["subRows"][k4]
                
                niveis = [n1, n2, n3, n4]

            elif funcao == 'Coordenador':
                if vend not in arvore: arvore[vend] = {"id": vend, "chave_matriz": vend, "nome": vend, "tipo": "vendedor", "meses_map": {}, "subRows": {}}
                n1 = arvore[vend]
                
                k2 = f"{vend}|{cgc}"
                if k2 not in n1["subRows"]: n1["subRows"][k2] = {"id": k2, "chave_matriz": k2, "nome": cli, "tipo": "cliente", "meses_map": {}, "subRows": {}}
                n2 = n1["subRows"][k2]
                
                k3 = f"{k2}|{sku}"
                if k3 not in n2["subRows"]: n2["subRows"][k3] = {"id": k3, "chave_matriz": k3, "nome": r.descricao, "produto": sku, "tipo": "produto", "meses_map": {}, "peso_fat": peso_fat}
                n3 = n2["subRows"][k3]
                
                niveis = [n1, n2, n3]

            else: # Vendedor
                cli_id = f"{cgc}"
                if cli_id not in arvore: arvore[cli_id] = {"id": cli_id, "chave_matriz": cli_id, "nome": cli, "tipo": "cliente", "meses_map": {}, "subRows": {}}
                n1 = arvore[cli_id]
                
                k2 = f"{cli_id}|{sku}"
                if k2 not in n1["subRows"]: n1["subRows"][k2] = {"id": k2, "chave_matriz": k2, "nome": r.descricao, "produto": sku, "tipo": "produto", "meses_map": {}, "peso_fat": peso_fat}
                n2 = n1["subRows"][k2]
                
                niveis = [n1, n2]

            vol_meta = int(r.vol_meta or 0)
            vol_sim = int(r.vol_bottomup or 0)
            pmv = float(r.pmv_aplicado or 0)

            for n in niveis:
                if ms not in n["meses_map"]:
                    n["meses_map"][ms] = {"mes_banco": ms, "vol_meta": 0, "rec_meta": 0, "vol_sim": 0, "rec_sim": 0, "pmv": pmv}
                
                n["meses_map"][ms]["vol_meta"] += vol_meta
                n["meses_map"][ms]["rec_meta"] += (vol_meta * pmv)
                n["meses_map"][ms]["vol_sim"] += vol_sim
                n["meses_map"][ms]["rec_sim"] += (vol_sim * pmv)
                
                if n["meses_map"][ms]["vol_sim"] > 0:
                    n["meses_map"][ms]["pmv"] = n["meses_map"][ms]["rec_sim"] / n["meses_map"][ms]["vol_sim"]

        def processar_no(node):
            node["meses"] = sorted(list(node["meses_map"].values()), key=lambda x: x["mes_banco"])
            del node["meses_map"]
            if "subRows" in node:
                node["subRows"] = list(node["subRows"].values())
                for sub in node["subRows"]: processar_no(sub)

        for raiz in arvore.values(): processar_no(raiz)

        # Travas de Etapa
        reg_demand = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Demand-Review').first()
        is_portfolio_fechado = reg_demand.status == 'Fechado' if reg_demand else False
        
        reg_carteira = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Carteira-Vendas').first()
        is_carteira_fechada = reg_carteira.status == 'Fechado' if reg_carteira else False

        return {"status": "success", "is_portfolio_fechado": is_portfolio_fechado, "is_fechado": is_carteira_fechada, "dados": list(arvore.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/salvar")
async def salvar_rascunho_carteira(payload: PayloadAprovarCarteira, db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)
        
        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            sku = ajuste.chave.split('|')[-1].strip()
            
            # Cuidado: a chave no backend de cliente é sempre pelo CGC que está antes do SKU na string
            # Formatos possíveis de folha: coord|vend|cgc|sku, vend|cgc|sku, cgc|sku
            partes = ajuste.chave.split('|')
            cgc = partes[-2].strip() if len(partes) >= 2 else None

            query = db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo, FatoIbpGranular.mes_projetado == dt, FatoIbpGranular.sku == sku, FatoIbpGranular.cgc == cgc).first()
            
            if query and query.vol_bottomup != ajuste.novo_volume:
                query.vol_bottomup = ajuste.novo_volume

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_carteira(payload: PayloadAprovarCarteira, db: Session = Depends(get_db)):
    try:
        if payload.ajustes: await salvar_rascunho_carteira(payload, db)
        ciclo = get_current_cycle(db)
        
        db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo).update({
            FatoIbpGranular.vol_supply: FatoIbpGranular.vol_bottomup,
            FatoIbpGranular.vol_final: FatoIbpGranular.vol_bottomup
        }, synchronize_session=False)
            
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Carteira-Vendas').first()
        if not reg: db.add(ControleCiclo(ciclo_sop=ciclo, origem='Carteira-Vendas', status='Fechado'))
        else: reg.status = 'Fechado'
        
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))