from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case, text
from pydantic import BaseModel
from typing import List, Optional
import datetime
from dateutil.relativedelta import relativedelta
import aiohttp

from app.core.database import get_db
from app.models.domain_models import (
    FatoVendas, DimProduto, DimCliente, FatoIbpGranular, 
    FatoEstoqueD0, FatoInboundProducao
)
from app.api.routers.router_auth import get_current_user
from app.core.config import settings

router = APIRouter(prefix="/api/v1/soe", tags=["S&OE - Execução Tática"])

# --- ROTA 1: SINCRONIZAÇÃO DE ESTOQUE (API 90) COM PAGINAÇÃO E MAPEAMENTO M ---
@router.post("/sync-stock")
async def sincronizar_estoque_api90(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Puxa a API 90 com paginação, mapeia 'arm' e 'quantidade' para gravar em 'qtd_dispo'."""
    if usuario['funcao'] not in ['Administrador', 'Planejador S&OP', 'Lideranca']:
        raise HTTPException(status_code=403, detail="Sem permissão para sincronizar estoque.")

    headers = {"Authorization": f"Bearer {settings.GOBI_TOKEN}"}
    base_url = "https://gobi-api.lineaalimentos.com.br/v1/reports/90/data"
    
    limit = 5000
    offset = 0
    lote_anterior = []
    estoque_novo = {}

    async with aiohttp.ClientSession() as session:
        while True:
            params = {"streaming": "true", "format": "json", "limit": limit, "offset": offset}
            async with session.get(base_url, headers=headers, params=params) as response:
                if response.status != 200:
                    break
                
                dados = await response.json(content_type=None)
                if not dados or not isinstance(dados, list): break
                if lote_anterior and dados[0] == lote_anterior[0]: break
                
                for row in dados:
                    # Mapeamento do Script M: campo [arm] para o armazém
                    arm = str(row.get('arm', '')).strip()
                    if arm == "05":
                        sku = str(row.get('produto', '')).replace(".0", "").strip()
                        # Mapeamento do Script M: campo [quantidade] para o volume real
                        try:
                            qtd = float(row.get('quantidade', 0))
                        except:
                            qtd = 0.0
                        
                        if sku:
                            estoque_novo[sku] = estoque_novo.get(sku, 0) + qtd
                
                lote_anterior = dados
                offset += len(dados)
                if len(dados) < limit: break

    try:
        db.query(FatoEstoqueD0).delete()
        agora = datetime.datetime.utcnow()
        
        # O campo no banco de dados Nexus é qtd_dispo
        novos_registros = [
            FatoEstoqueD0(sku=sku, qtd_dispo=qtd, data_atualizacao=agora)
            for sku, qtd in estoque_novo.items()
        ]
        
        if novos_registros:
            db.bulk_save_objects(novos_registros)
        db.commit()
        return {"status": "success", "count": len(novos_registros)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# --- ROTA 2: RADAR S&OE (SAÚDE DO SKU - MÊS CALENDÁRIO) ---
@router.get("/radar")
async def carregar_radar_soe(db: Session = Depends(get_db)):
    """Visão 360º do SKU no mês atual, independente do ciclo da máquina do tempo."""
    hoje = datetime.date.today()
    primeiro_dia = hoje.replace(day=1)
    mes_ref = hoje.strftime("%m/%Y")
    
    # 1. Metas do Mês Atual
    forecast = db.query(
        FatoIbpGranular.sku,
        func.sum(FatoIbpGranular.vol_final).label('vol_sop'), # <- CORRIGIDO PARA vol_final
        func.sum(FatoIbpGranular.vol_meta).label('vol_meta'),
        func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
    ).filter(
        FatoIbpGranular.ciclo_sop == mes_ref,
        FatoIbpGranular.mes_projetado == primeiro_dia
    ).group_by(FatoIbpGranular.sku).all()
    
    f_map = {r.sku: r for r in forecast}

    # 2. Execução Real (Vendas, Faturamento e Cortes)
    vendas = db.query(
        FatoVendas.sku,
        func.sum(case((FatoVendas.data_pedido >= primeiro_dia, FatoVendas.qt_pedido), else_=0)).label('pedidos'),
        func.sum(case((FatoVendas.data_pedido >= primeiro_dia, FatoVendas.qtfatura), else_=0)).label('faturado'),
        func.sum(case((FatoVendas.data_pedido >= primeiro_dia, FatoVendas.qtcorte), else_=0)).label('corte'),
        func.sum(case((FatoVendas.data_pedido >= primeiro_dia, FatoVendas.vl_pedido), else_=0)).label('valor_pedidos')
    ).group_by(FatoVendas.sku).all()
    
    v_map = {r.sku: r for r in vendas}

    # 3. Estoque e Inbound
    estoque_map = {r.sku: r.qtd_dispo for r in db.query(FatoEstoqueD0.sku, FatoEstoqueD0.qtd_dispo).all()}
    inbound_map = {r.sku: r.vol for r in db.query(FatoInboundProducao.sku, func.sum(FatoInboundProducao.vol_caixas).label('vol')).filter(FatoInboundProducao.data_entrada >= hoje).group_by(FatoInboundProducao.sku).all()}

    resultado = []
    for p in db.query(DimProduto).all():
        f = f_map.get(p.sku)
        v = v_map.get(p.sku)
        
        sop = float(f.vol_sop or 0) if f else 0
        pmv = float(f.pmv or 0) if f else 0
        faturado = float(v.faturado or 0) if v else 0
        pedidos = float(v.pedidos or 0) if v else 0
        corte = float(v.corte or 0) if v else 0
        estoque = estoque_map.get(p.sku, 0)
        
        # Backlog: Pedidos que ainda não faturaram nem cortaram
        backlog = pedidos - (faturado + corte)
        saldo_proj = (estoque + float(inbound_map.get(p.sku, 0))) - backlog
        
        resultado.append({
            "sku": p.sku,
            "descricao": p.descricao,
            "curva": p.curva,
            "pmv": pmv,
            "metas": {
                "vol_sop": sop,
                "vol_meta": float(f.vol_meta or 0) if f else 0,
                "val_meta": (float(f.vol_meta or 0) if f else 0) * pmv
            },
            "execucao": {
                "pedidos_qtd": pedidos,
                "faturado_qtd": faturado,
                "faturado_val": faturado * pmv,
                "corte_qtd": corte,
                "corte_val": corte * pmv,
                "pacing": (faturado / sop * 100) if sop > 0 else 0
            },
            "logistica": {
                "estoque_d0": estoque,
                "backlog_total": backlog,
                "saldo_projetado": saldo_proj,
                "fat_em_risco": abs(saldo_proj * pmv) if saldo_proj < 0 else 0
            }
        })

    return {"mes": mes_ref, "dados": sorted(resultado, key=lambda x: x['curva'])}

# --- ROTA 3: OPORTUNIDADES (CLIENTES SEM COMPRA NO MÊS) ---
@router.get("/oportunidades")
async def carregar_oportunidades_venda(db: Session = Depends(get_db)):
    """Clientes com meta mas sem pedidos no mês atual."""
    hoje = datetime.date.today()
    mes_ref = hoje.strftime("%m/%Y")
    
    # 1. Clientes com Meta no Ciclo
    metas = db.query(
        FatoIbpGranular.cgc, DimCliente.razaosocial, DimCliente.regional,
        func.sum(FatoIbpGranular.vol_meta).label('meta_vol'),
        func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
    ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
     .filter(FatoIbpGranular.ciclo_sop == mes_ref).group_by(FatoIbpGranular.cgc, DimCliente.razaosocial, DimCliente.regional).all()

    # 2. Vendas do Mês
    vendas_mes = {v.cgc for v in db.query(FatoVendas.cgc).filter(FatoVendas.data_pedido >= hoje.replace(day=1)).group_by(FatoVendas.cgc).all()}
    
    # 3. Frequência (Última Compra)
    ultimas = {u.cgc: u.dta for u in db.query(FatoVendas.cgc, func.max(FatoVendas.data_pedido).label('dta')).group_by(FatoVendas.cgc).all()}

    res = []
    for m in metas:
        if m.cgc not in vendas_mes:
            last_date = ultimas.get(m.cgc)
            atraso = (hoje - last_date).days if last_date else 99
            res.append({
                "cliente": m.razaosocial,
                "regional": m.regional,
                "meta_vol": float(m.meta_vol or 0),
                "valor_estimado": float(m.meta_vol or 0) * float(m.pmv or 0),
                "dias_sem_compra": atraso,
                "status": "Crítico" if atraso > 15 else "Alerta"
            })
            
    return sorted(res, key=lambda x: -x['valor_estimado'])