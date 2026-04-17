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
from app.api.routers.shared_ibp import (
    get_current_cycle, get_previous_cycle, get_projection_window, 
    get_truth_query, check_global_lock, parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus/micro", tags=["Consenso Bottom-Up"])

class AjusteBottomUp(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadCongelarBU(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteBottomUp]

# =====================================================================
# ROTAS DE FILTROS E LISTAGEM
# =====================================================================

@router.get("/filtros")
async def obter_filtros_busca(gerente_nome: str = None, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        q = db.query(DimCliente)
        filtro_gerente = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else gerente_nome
        if filtro_gerente: 
            q = q.filter(func.trim(DimCliente.gerente_nome) == filtro_gerente.strip())
        
        regionais = sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional})
        vendedores = sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
        
        return {"regionais": regionais, "vendedores": vendedores}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("")
async def listar_micro(nivel: str, chave: str, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()
        query_base = get_truth_query(db, ciclo, m2, m4)
        
        if nivel == 'vendedor':
            query_base = query_base.filter(func.trim(FatoIbpGranular.vendedor_nome) == chave.strip())
        elif nivel == 'regional' and usuario_logado['funcao'] in ['Administrador', 'Gerente']:
            query_base = query_base.filter(func.trim(DimCliente.regional) == chave.strip())

        resultados = query_base.with_entities(
            DimCliente.razaosocial, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'), 
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('receita_ia'),
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('receita_bu'),
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento
        ).group_by(
            DimCliente.razaosocial, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento
        ).all()

        # CONSTRÓI A ÁRVORE HIERÁRQUICA (CLIENTE -> PRODUTOS)
        arvore = defaultdict(lambda: {
            "id": "", "chave_matriz": "", "nome": "", "tipo": "cliente",
            "meses_agg": defaultdict(lambda: {"v_ia": 0, "v_td": 0, "v_bu": 0, "rec_ia": 0.0, "rec_bu": 0.0}),
            "produtos": defaultdict(lambda: {
                "id": "", "chave_matriz": "", "nome": "", "produto": "", "tipo": "produto",
                "descricao": "", "categoria": "", "segmento": "",
                "meses_agg": defaultdict(lambda: {"v_ia": 0, "v_td": 0, "v_bu": 0, "rec_ia": 0.0, "rec_bu": 0.0})
            })
        })

        for r in resultados:
            rz = str(r.razaosocial or "DESC")
            sku = r.sku
            ms = str(r.mes_projetado)

            vol_ia = int(r.v_ia or 0)
            vol_td = int(r.v_td or 0)
            vol_bu = int(r.v_bu or 0)
            rec_ia = float(r.receita_ia or 0)
            rec_bu = float(r.receita_bu or 0)

            # Nó do Cliente
            cli_node = arvore[rz]
            cli_node["id"] = cli_node["chave_matriz"] = cli_node["nome"] = rz
            cli_mes = cli_node["meses_agg"][ms]
            cli_mes["v_ia"] += vol_ia
            cli_mes["v_td"] += vol_td
            cli_mes["v_bu"] += vol_bu
            cli_mes["rec_ia"] += rec_ia
            cli_mes["rec_bu"] += rec_bu

            # Nó do Produto
            prod_node = cli_node["produtos"][sku]
            prod_node["id"] = prod_node["chave_matriz"] = f"{rz}|{sku}"
            prod_node["nome"] = r.descricao
            prod_node["produto"] = sku
            prod_node["descricao"] = r.descricao
            prod_node["categoria"] = r.categoria
            prod_node["segmento"] = r.segmento
            
            prod_mes = prod_node["meses_agg"][ms]
            prod_mes["v_ia"] += vol_ia
            prod_mes["v_td"] += vol_td
            prod_mes["v_bu"] += vol_bu
            prod_mes["rec_ia"] += rec_ia
            prod_mes["rec_bu"] += rec_bu

        # FORMATANDO PARA O REACT TABLE
        dados = []
        for rz, cli_data in arvore.items():
            cli_meses = []
            for ms, m_data in cli_data["meses_agg"].items():
                pmv_real = (m_data["rec_ia"] / m_data["v_ia"]) if m_data["v_ia"] > 0 else 0
                cli_meses.append({
                    "mes_banco": ms, "mes_str": parse_date_safe(ms).strftime("%b/%y").capitalize(),
                    "vol_ia": m_data["v_ia"], "vol_td": m_data["v_td"], "vol_ajustado": m_data["v_bu"],
                    "pmv": pmv_real, "receita": m_data["v_bu"] * pmv_real
                })

            sub_rows = []
            for sku, prod_data in cli_data["produtos"].items():
                prod_meses = []
                for ms, m_data in prod_data["meses_agg"].items():
                    pmv_real = (m_data["rec_ia"] / m_data["v_ia"]) if m_data["v_ia"] > 0 else 0
                    prod_meses.append({
                        "mes_banco": ms, "mes_str": parse_date_safe(ms).strftime("%b/%y").capitalize(),
                        "vol_ia": m_data["v_ia"], "vol_td": m_data["v_td"], "vol_ajustado": m_data["v_bu"],
                        "pmv": pmv_real, "receita": m_data["v_bu"] * pmv_real
                    })
                sub_rows.append({
                    "id": prod_data["id"], "chave_matriz": prod_data["chave_matriz"],
                    "nome": prod_data["nome"], "produto": prod_data["produto"], "tipo": prod_data["tipo"],
                    "descricao": prod_data["descricao"], "categoria": prod_data["categoria"], "segmento": prod_data["segmento"],
                    "meses": prod_meses
                })

            dados.append({
                "id": cli_data["id"], "chave_matriz": cli_data["chave_matriz"],
                "nome": cli_data["nome"], "razaosocial": cli_data["nome"], "tipo": cli_data["tipo"],
                "meses": cli_meses, "subRows": sub_rows
            })

        return {"status": "success", "dados": dados}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_micro(chave: str, db: Session = Depends(get_db)):
    try:
        # Flexibilidade: Pode ser só "Cliente" ou "Cliente|SKU"
        partes = chave.split('|')
        razao_alvo = partes[0].strip()
        sku_alvo = partes[1].strip() if len(partes) > 1 else None
        
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_atual = get_current_cycle()
        ciclo_anterior = get_previous_cycle()
        m2, m4 = get_projection_window()
        
        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), 
            func.sum(FatoVendas.qt_pedido).label('vol_real')
        ).join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
         .filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2), DimCliente.razaosocial == razao_alvo)

        q_ant = db.query(
            FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_bottomup).label('vol_ant')
        ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(FatoIbpGranular.ciclo_sop == ciclo_anterior, DimCliente.razaosocial == razao_alvo)
            
        q_proj = db.query(
            FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), func.sum(FatoIbpGranular.vol_bottomup).label('vol_consenso')
        ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(FatoIbpGranular.ciclo_sop == ciclo_atual, DimCliente.razaosocial == razao_alvo)

        # Se tiver SKU, filtra mais fundo
        if sku_alvo:
            q_hist = q_hist.filter(FatoVendas.sku == sku_alvo)
            q_ant = q_ant.filter(FatoIbpGranular.sku == sku_alvo)
            q_proj = q_proj.filter(FatoIbpGranular.sku == sku_alvo)

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist.group_by('mes_ano').all()}
        ant_dict = {str(a.mes_projetado): int(a.vol_ant or 0) for a in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        projecoes = q_proj.group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = []

        # Fase A: Passado
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str = dt.strftime('%Y-%m')
            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(), "data_iso": dt.strftime("%Y-%m-%d"),
                "Realizado": hist_dict.get(mes_str, 0), "IA": None, "Consenso": None, "CicloAnterior": None
            })

        # Fase B: S&OE (Mês Corrente)
        mes_atual_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        proj_atual = next((p for p in projecoes if str(p.mes_projetado) == mes_atual_iso), None)
        
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)", "data_iso": mes_atual_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": int(proj_atual.vol_ia) if proj_atual else None,   
            "Consenso": None, "CicloAnterior": ant_dict.get(mes_atual_iso, None)
        })

        # Fase C: Futuro
        for p in projecoes:
            p_date = parse_date_safe(p.mes_projetado)
            if p_date <= mes_atual_inicio: continue 
            
            p_iso = str(p_date)
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(), "data_iso": p_iso,
                "Realizado": None, "IA": int(p.vol_ia or 0),
                "Consenso": int(p.vol_consenso or 0) if p_date >= m2 else None,
                "CicloAnterior": ant_dict.get(p_iso, None)
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# O MOTOR DE RATEIO REVERSO E FLEXÍVEL (SALVAR)
# =====================================================================

@router.post("/congelar")
async def congelar_micro(payload: PayloadCongelarBU, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        for ajuste in payload.ajustes:
            partes = ajuste.chave.split('|')
            razao_alvo = partes[0].strip()
            sku_alvo = partes[1].strip() if len(partes) > 1 else None
            data_alvo = parse_date_safe(ajuste.mes_projetado)

            linhas_query = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                .filter(FatoIbpGranular.ciclo_sop == ciclo, FatoIbpGranular.mes_projetado == data_alvo, DimCliente.razaosocial == razao_alvo)

            if sku_alvo: linhas_query = linhas_query.filter(FatoIbpGranular.sku == sku_alvo)
            linhas_atomicas = linhas_query.all()
            if not linhas_atomicas: continue

            # Puxa o histórico exato para saber o peso da loja/SKU na matriz
            soma_hist_matriz_query = db.query(func.sum(FatoVendas.qt_pedido))\
                .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                .filter(DimCliente.razaosocial == razao_alvo, FatoVendas.data_pedido >= data_limite_str)
            
            if sku_alvo: soma_hist_matriz_query = soma_hist_matriz_query.filter(FatoVendas.sku == sku_alvo)
            soma_hist_matriz = soma_hist_matriz_query.scalar() or 0
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, linha in enumerate(linhas_atomicas):
                if i == len(linhas_atomicas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    vol_loja = db.query(func.sum(FatoVendas.qt_pedido)).filter(
                        FatoVendas.sku == linha.sku, FatoVendas.cgc == linha.cgc,
                        FatoVendas.data_pedido >= data_limite_str
                    ).scalar() or 0
                    
                    peso = vol_loja / soma_hist_matriz if soma_hist_matriz > 0 else 1.0 / len(linhas_atomicas)
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                
                linha.vol_bottomup = rateado
                linha.vol_final = rateado

        db.commit()
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))