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
# ROTAS DE LISTAGEM E GRÁFICO (O S&OE NA TRINCHEIRA)
# =====================================================================

@router.get("")
async def listar_micro(nivel: str, chave: str, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    try:
        m2, m4 = get_projection_window()
        ciclo = get_current_cycle()
        query_base = get_truth_query(db, ciclo, m2, m4)
        
        # Filtro de Segurança / Contexto
        if nivel == 'vendedor':
            query_base = query_base.filter(FatoIbpGranular.vendedor_nome == chave)
        elif nivel == 'regional' and usuario_logado['funcao'] in ['Administrador', 'Gerente']:
            query_base = query_base.filter(DimCliente.regional == chave)

        # O SEGREDO DO PMV PONDERADO: Agrupamos por Razão Social + SKU
        projecoes = query_base.with_entities(
            DimCliente.razaosocial, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'), # Meta que a diretoria mandou
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), # Meta que o vendedor está editando
            # PMV Ponderado: (Soma do Faturamento) / (Soma do Volume)
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('receita_ia'),
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento
        ).group_by(
            DimCliente.razaosocial, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento
        ).all()

        mapa = defaultdict(lambda: {"meses": []})
        for r in projecoes:
            chave_matriz = f"{r.razaosocial}|{r.sku}"
            p = mapa[chave_matriz]
            
            if not p.get('chave_matriz'):
                p.update({
                    "chave_matriz": chave_matriz, "razaosocial": r.razaosocial, "sku": r.sku,
                    "descricao": r.descricao, "categoria": r.categoria, "segmento": r.segmento
                })
            
            vol_ia = int(r.v_ia or 0)
            vol_td = int(r.v_td or 0)
            vol_bu = int(r.v_bu or 0)
            rec_ia = float(r.receita_ia or 0)
            
            # Se não tem volume para ponderar, usamos 0. 
            pmv_real = (rec_ia / vol_ia) if vol_ia > 0 else 0
            
            p["meses"].append({
                "mes_banco": str(r.mes_projetado),
                "mes_str": r.mes_projetado.strftime("%b/%y").capitalize(),
                "vol_ia": vol_ia,
                "vol_td": vol_td,
                "vol_ajustado": vol_bu,
                "pmv": pmv_real,
                "receita": vol_bu * pmv_real
            })

        return {"status": "success", "dados": list(mapa.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_micro(chave: str, db: Session = Depends(get_db)):
    try:
        # A chave que o React manda é "RAZÃO SOCIAL|SKU"
        razao_alvo, sku_alvo = chave.split('|')
        
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_atual = get_current_cycle()
        ciclo_anterior = get_previous_cycle()
        m2, m4 = get_projection_window()
        
        # 1. Puxamos o histórico filtrando pelas Lojas daquela Razão Social
        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), 
            func.sum(FatoVendas.qt_pedido).label('vol_real')
        ).join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
         .filter(
            FatoVendas.data_pedido >= hoje - relativedelta(years=2), 
            FatoVendas.sku == sku_alvo,
            DimCliente.razaosocial == razao_alvo
        ).group_by('mes_ano').all()

        q_ant = db.query(
            FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_bottomup).label('vol_ant')
        ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(
            FatoIbpGranular.ciclo_sop == ciclo_anterior, 
            FatoIbpGranular.sku == sku_alvo,
            DimCliente.razaosocial == razao_alvo
        ).group_by(FatoIbpGranular.mes_projetado).all()
            
        q_proj = db.query(
            FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), 
            func.sum(FatoIbpGranular.vol_bottomup).label('vol_consenso')
        ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(
            FatoIbpGranular.ciclo_sop == ciclo_atual, 
            FatoIbpGranular.sku == sku_alvo,
            DimCliente.razaosocial == razao_alvo
        ).group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist}
        ant_dict = {str(a.mes_projetado): int(a.vol_ant or 0) for a in q_ant}

        timeline = []

        # Fase A: Passado
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str = dt.strftime('%Y-%m')
            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(),
                "data_iso": dt.strftime("%Y-%m-%d"),
                "Realizado": hist_dict.get(mes_str, 0),
                "IA": None, "Consenso": None, "CicloAnterior": None
            })

        # Fase B: S&OE (Mês Corrente)
        mes_atual_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        proj_atual = next((p for p in q_proj if str(p.mes_projetado) == mes_atual_iso), None)
        
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)",
            "data_iso": mes_atual_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": int(proj_atual.vol_ia) if proj_atual else None,   
            "Consenso": None, # Blindagem: Meta comercial some no mês corrente
            "CicloAnterior": ant_dict.get(mes_atual_iso, None)
        })

        # Fase C: Futuro
        for p in q_proj:
            p_date = parse_date_safe(p.mes_projetado)
            if p_date <= mes_atual_inicio: continue 
            
            p_iso = str(p_date)
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(),
                "data_iso": p_iso,
                "Realizado": None,
                "IA": int(p.vol_ia or 0),
                "Consenso": int(p.vol_consenso or 0) if p_date >= m2 else None,
                "CicloAnterior": ant_dict.get(p_iso, None)
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# O MOTOR DE RATEIO REVERSO (SALVAR)
# =====================================================================

@router.post("/congelar")
async def congelar_micro(payload: PayloadCongelarBU, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle()
        check_global_lock(db, ciclo)
        
        # Histórico de 12 meses para calcular o peso das lojas (CGC)
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        for ajuste in payload.ajustes:
            razao_alvo, sku_alvo = ajuste.chave.split('|')
            data_alvo = parse_date_safe(ajuste.mes_projetado)

            # Puxa todas as lojas atômicas que formam essa Razão Social para esse SKU
            linhas_atomicas = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                .filter(
                    FatoIbpGranular.ciclo_sop == ciclo,
                    FatoIbpGranular.mes_projetado == data_alvo,
                    FatoIbpGranular.sku == sku_alvo,
                    DimCliente.razaosocial == razao_alvo
                ).all()

            if not linhas_atomicas: continue

            # Descobre quanto a matriz inteira comprou desse SKU no passado
            soma_hist_matriz = db.query(func.sum(FatoVendas.qt_pedido))\
                .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                .filter(
                    FatoVendas.sku == sku_alvo, 
                    DimCliente.razaosocial == razao_alvo,
                    FatoVendas.data_pedido >= data_limite_str
                ).scalar() or 0
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            # Rateia o volume total para cada loja baseada no peso histórico dela
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
                
                # Apenas o Bottom-Up é atualizado. O Top-Down (Diretoria) fica intacto.
                linha.vol_bottomup = rateado
                # A Demanda Irrestrita Final passa a ser o Consenso do Vendedor
                linha.vol_final = rateado

        db.commit()
        return {"status": "success"}
    except HTTPException as he: raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))