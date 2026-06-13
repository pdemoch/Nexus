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
    novo_volume: int # O Frontend envia as Caixas exatas após calcular o rateio do dinheiro pelo PMV

class PayloadAprovarCarteira(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteCarteira]

@router.get("")
async def carregar_carteira(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)
        funcao = usuario.get('funcao')
        nome_usuario = usuario.get('nome')

        hoje = datetime.date.today()
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        # 1. Pega o Histórico de FATURAMENTO (R$) dos últimos 4 meses para Rateio Proporcional Financeiro
        data_hist_inicio = hoje - relativedelta(months=4)
        q_hist = db.query(
            FatoVendas.cgc, FatoVendas.sku, 
            func.sum(FatoVendas.qt_pedido * FatoVendas.vl_pedido).label('fat_hist')
        ).filter(FatoVendas.data_pedido >= data_hist_inicio).group_by(FatoVendas.cgc, FatoVendas.sku).all()
        peso_fat_dict = {f"{r.cgc}|{r.sku}": float(r.fat_hist or 0) for r in q_hist}

        # 2. Query Principal: Busca os volumes já consolidados na etapa Macro (vol_meta herdado)
        query = db.query(
            DimCliente.gerente, DimCliente.coordenador, DimCliente.vendedor, DimCliente.razaosocial, DimCliente.cgc,
            FatoIbpGranular.sku, DimProduto.descricao,
            FatoIbpGranular.mes_projetado,
            FatoIbpGranular.vol_meta,     # Teto Herdado do Gerenciamento
            FatoIbpGranular.vol_bottomup, # Simulação Atual do Vendedor
            FatoIbpGranular.pmv_aplicado
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(FatoIbpGranular.ciclo_sop == ciclo, FatoIbpGranular.mes_projetado >= data_ini, FatoIbpGranular.mes_projetado <= data_fim)

        # RLS (Row Level Security): Filtra a árvore para quem está logado
        if funcao == 'Gerente':
            query = query.filter(DimCliente.gerente == nome_usuario)
        elif funcao == 'Coordenador':
            query = query.filter(DimCliente.coordenador == nome_usuario)
        elif funcao == 'Vendedor':
            query = query.filter(DimCliente.vendedor == nome_usuario)

        resultados = query.all()

        # Construção da Árvore: Gerente -> Coordenador -> Vendedor -> Cliente -> SKU
        arvore = {}
        for r in resultados:
            ger = r.gerente or 'SEM GERENTE'
            coord = r.coordenador or 'SEM COORDENADOR'
            vend = r.vendedor or 'SEM VENDEDOR'
            cli = r.razaosocial or 'CLIENTE INDEFINIDO'
            cgc = r.cgc
            sku = r.sku
            ms = str(r.mes_projetado)

            peso_fat = peso_fat_dict.get(f"{cgc}|{sku}", 0)
            
            # Navegação da Hierarquia Baseada no RLS
            raiz_id = ger if funcao == 'Diretoria' or funcao == 'Administrador' else (ger if funcao == 'Gerente' else (coord if funcao == 'Coordenador' else vend))
            if funcao not in ['Gerente', 'Coordenador', 'Vendedor', 'Diretoria', 'Administrador']:
                raiz_id = ger # Fallback genérico

            if raiz_id not in arvore:
                tipo_raiz = 'gerente' if raiz_id == ger else ('coordenador' if raiz_id == coord else 'vendedor')
                arvore[raiz_id] = {"id": raiz_id, "chave_matriz": raiz_id, "nome": raiz_id, "tipo": tipo_raiz, "meses_map": {}, "subRows": {}}
            
            nivel_1 = arvore[raiz_id] # Ex: Gerente
            
            # Se a raiz é Gerente, o próximo nível é Coordenador
            if nivel_1["tipo"] == 'gerente':
                if coord not in nivel_1["subRows"]:
                    nivel_1["subRows"][coord] = {"id": f"{ger}|{coord}", "chave_matriz": coord, "nome": coord, "tipo": "coordenador", "meses_map": {}, "subRows": {}}
                nivel_2 = nivel_1["subRows"][coord]
                
                if vend not in nivel_2["subRows"]:
                    nivel_2["subRows"][vend] = {"id": f"{coord}|{vend}", "chave_matriz": vend, "nome": vend, "tipo": "vendedor", "meses_map": {}, "subRows": {}}
                nivel_3 = nivel_2["subRows"][vend]
            
            elif nivel_1["tipo"] == 'coordenador':
                if vend not in nivel_1["subRows"]:
                    nivel_1["subRows"][vend] = {"id": f"{coord}|{vend}", "chave_matriz": vend, "nome": vend, "tipo": "vendedor", "meses_map": {}, "subRows": {}}
                nivel_3 = nivel_1["subRows"][vend]
            else:
                nivel_3 = nivel_1 # Raiz já é o Vendedor

            # Nível Cliente
            if cli not in nivel_3["subRows"]:
                nivel_3["subRows"][cli] = {"id": f"{vend}|{cli}", "chave_matriz": f"{vend}|{cli}", "nome": cli, "tipo": "cliente", "meses_map": {}, "subRows": {}}
            nivel_4 = nivel_3["subRows"][cli]

            # Nível SKU
            if sku not in nivel_4["subRows"]:
                nivel_4["subRows"][sku] = {"id": f"{cli}|{sku}", "chave_matriz": f"{cli}|{sku}", "nome": r.descricao, "produto": sku, "tipo": "produto", "meses_map": {}, "peso_fat": peso_fat}
            nivel_5 = nivel_4["subRows"][sku]

            # Injeção de Dados (Meta e Simulado em CAIXAS, PMV como multiplicador)
            vol_meta = int(r.vol_meta or 0)
            vol_sim = int(r.vol_bottomup or 0)
            pmv = float(r.pmv_aplicado or 0)

            niveis_para_atualizar = [nivel_4, nivel_5]
            if nivel_1["tipo"] == 'gerente': niveis_para_atualizar.extend([nivel_1, nivel_2, nivel_3])
            elif nivel_1["tipo"] == 'coordenador': niveis_para_atualizar.extend([nivel_1, nivel_3])
            else: niveis_para_atualizar.append(nivel_1)

            for n in niveis_para_atualizar:
                if ms not in n["meses_map"]:
                    n["meses_map"][ms] = {"mes_banco": ms, "vol_meta": 0, "rec_meta": 0, "vol_sim": 0, "rec_sim": 0, "pmv": pmv}
                
                n["meses_map"][ms]["vol_meta"] += vol_meta
                n["meses_map"][ms]["rec_meta"] += (vol_meta * pmv)
                n["meses_map"][ms]["vol_sim"] += vol_sim
                n["meses_map"][ms]["rec_sim"] += (vol_sim * pmv)
                
                # PMV Ponderado nos Nós Agrupadores
                if n["meses_map"][ms]["vol_meta"] > 0:
                    n["meses_map"][ms]["pmv"] = n["meses_map"][ms]["rec_meta"] / n["meses_map"][ms]["vol_meta"]

        # Formatação Recursiva para a Tabela (Arrays)
        def processar_no(node):
            node["meses"] = sorted(list(node["meses_map"].values()), key=lambda x: x["mes_banco"])
            del node["meses_map"]
            if "subRows" in node:
                node["subRows"] = list(node["subRows"].values())
                for sub in node["subRows"]:
                    processar_no(sub)

        for raiz in arvore.values():
            processar_no(raiz)

        # Trava de Status
        reg_demand = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Demand-Review').first()
        is_fechado = reg_demand.status == 'Fechado' if reg_demand else False

        return {"status": "success", "is_fechado": is_fechado, "dados": list(arvore.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/salvar")
async def salvar_rascunho_carteira(payload: PayloadAprovarCarteira, db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)
        
        # O Frontend vai fazer a conta do Dinheiro -> Caixas e envia Caixas exatas para salvar
        for ajuste in payload.ajustes:
            dt = parse_date_safe(ajuste.mes_projetado)
            # A chave do SKU agora é {cli}|{sku}
            sku = ajuste.chave.split('|')[-1].strip()
            cliente_nome = ajuste.chave.split('|')[0].strip()

            # Busca a linha específica do cliente e SKU
            query = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                      .filter(FatoIbpGranular.ciclo_sop == ciclo, FatoIbpGranular.mes_projetado == dt, 
                              FatoIbpGranular.sku == sku, DimCliente.razaosocial == cliente_nome).first()
            
            if query and query.vol_bottomup != ajuste.novo_volume:
                query.vol_bottomup = ajuste.novo_volume

        db.commit()
        return {"status": "success", "message": "Simulação gravada com sucesso!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))