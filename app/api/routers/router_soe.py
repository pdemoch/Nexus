from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, text, extract, and_, case
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
from app.api.routers.shared_ibp import get_current_cycle, parse_date_safe
from app.core.config import settings

router = APIRouter(prefix="/api/v1/soe", tags=["S&OE - Execução Tática"])

# --- MODELOS DE ENTRADA ---
class InboundEntry(BaseModel):
    sku: str
    data_entrada: str
    vol_caixas: int
    justificativa: Optional[str] = None

# --- ROTA 1: SINCRONIZAÇÃO DE ESTOQUE (API 90) ---
@router.post("/sync-stock")
async def sincronizar_estoque_api90(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Puxa a API 90, filtra Armazém 05 e atualiza a FatoEstoqueD0."""
    
    # URL atualizada com os parâmetros do seu código M
    url = "https://gobi-api.lineaalimentos.com.br/v1/reports/90/data?streaming=true&format=json"
    headers = {"Authorization": f"Bearer {settings.GOBI_TOKEN}"}
    
    try:
        print("⏳ [SYNC-STOCK] Iniciando sincronização com a API 90...")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    texto_erro = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"Erro ERP: {texto_erro}")
                
                dados = await resp.json(content_type=None)

        if isinstance(dados, dict) and "data" in dados:
            dados = dados["data"]
        if not isinstance(dados, list):
            dados = []

        print(f"✅ [SYNC-STOCK] Dados recebidos do ERP: {len(dados)} linhas de estoque lidas.")
        
        db.query(FatoEstoqueD0).delete()
        
        estoque_agrupado = {}
        linhas_processadas = 0

        for item in dados:
            if not isinstance(item, dict): continue 
            
            # 1. TRATAMENTO DO ARMAZÉM (Blinda contra "5", "05", 5, 5.0)
            arm_raw = str(item.get('arm', '')).strip()
            try:
                is_arm_05 = int(float(arm_raw)) == 5
            except ValueError:
                is_arm_05 = (arm_raw == '05' or arm_raw == '5')

            if is_arm_05:
                linhas_processadas += 1
                
                # 2. TRATAMENTO DO PRODUTO (Remove zeros à esquerda e decimais)
                prod_raw = item.get('produto', '')
                try:
                    sku = str(int(float(prod_raw)))
                except ValueError:
                    sku = str(prod_raw).strip()
                
                # 3. TRATAMENTO DA QUANTIDADE (Focado estritamente na qtd_dispo, conforme seu Power Query)
                qtd_raw = item.get('qtd_dispo', 0)
                # Garante que valores nulos ou vazios não quebrem a soma
                try:
                    qtd = float(qtd_raw) if qtd_raw else 0.0
                except (ValueError, TypeError):
                    qtd = 0.0
                
                estoque_agrupado[sku] = estoque_agrupado.get(sku, 0) + qtd
        
        print(f"🔎 [SYNC-STOCK] Linhas que passaram no filtro do Arm 05: {linhas_processadas}")

        novas_linhas = [
            FatoEstoqueD0(sku=sku, qtd_dispo=vol) 
            for sku, vol in estoque_agrupado.items()
        ]
        
        db.bulk_save_objects(novas_linhas)
        db.commit()
        print(f"✅ [SYNC-STOCK] Concluído: {len(novas_linhas)} SKUs agrupados e salvos no Armazém 05.")
        
        return {"status": "success", "mensagem": f"{len(novas_linhas)} SKUs atualizados no estoque."}
    
    except HTTPException as he:
        db.rollback()
        raise he
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
    
# --- ROTA 2: GESTÃO DE INBOUND (CALENDÁRIO DE FÁBRICA) ---
@router.get("/inbound")
async def listar_inbound(db: Session = Depends(get_db)):
    # Retorna o plano futuro de produção
    hoje = datetime.date.today()
    return db.query(FatoInboundProducao).filter(FatoInboundProducao.data_entrada >= hoje).all()

@router.post("/inbound")
async def salvar_inbound(payload: InboundEntry, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        nova_entrada = FatoInboundProducao(
            sku=payload.sku,
            data_entrada=parse_date_safe(payload.data_entrada),
            vol_caixas=payload.vol_caixas,
            justificativa=payload.justificativa,
            usuario_nome=usuario['nome']
        )
        db.add(nova_entrada)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/inbound/{id}")
async def deletar_inbound(id: int, db: Session = Depends(get_db)):
    db.query(FatoInboundProducao).filter(FatoInboundProducao.id == id).delete()
    db.commit()
    return {"status": "success"}

# --- ROTA 3: RADAR S&OE (A WAR ROOM) ---
@router.get("/radar")
async def carregar_radar_soe(db: Session = Depends(get_db)):
    """Calcula o Pacing, Backlog, Gap e Risco de Ruptura em tempo real."""
    hoje = datetime.date.today()
    ciclo_atual = get_current_cycle()
    primeiro_dia_mes = hoje.replace(day=1)
    
    # 1. BUSCAR FORECAST S&OP (VOL_FINAL)
    forecast_query = db.query(
        FatoIbpGranular.sku,
        func.sum(FatoIbpGranular.vol_final).label('vol_sop'),
        func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
    ).filter(
        FatoIbpGranular.ciclo_sop == ciclo_atual,
        FatoIbpGranular.mes_projetado == primeiro_dia_mes
    ).group_by(FatoIbpGranular.sku).all()
    
    forecast_map = {r.sku: {"sop": r.vol_sop, "pmv": r.pmv} for r in forecast_query}

    # 2. BUSCAR BACKLOG REAL (M-1 + M0)
    # Fórmula: qtpedido - (qtfatura + qtcorte) > 0
    backlog_query = db.query(
        FatoVendas.sku,
        func.sum(FatoVendas.qt_pedido - (FatoVendas.qtfatura + FatoVendas.qtcorte)).label('saldo_vendas'),
        func.sum(case((FatoVendas.data_pedido >= primeiro_dia_mes, FatoVendas.qt_pedido), else_=0)).label('pedidos_m0'),
        func.sum(case((FatoVendas.data_pedido >= primeiro_dia_mes, FatoVendas.qtfatura), else_=0)).label('faturado_m0'),
        func.sum(case((FatoVendas.data_pedido < primeiro_dia_mes, FatoVendas.qt_pedido - (FatoVendas.qtfatura + FatoVendas.qtcorte)), else_=0)).label('divida_m1')
    ).group_by(FatoVendas.sku).all()
    
    backlog_map = {r.sku: r for r in backlog_query}

    # 3. BUSCAR ESTOQUE ATUAL (API 90)
    estoque_query = db.query(FatoEstoqueD0.sku, FatoEstoqueD0.qtd_dispo).all()
    estoque_map = {r.sku: r.qtd_dispo for r in estoque_query}

    # 4. BUSCAR INBOUND (SUPPLY)
    inbound_query = db.query(
        FatoInboundProducao.sku, 
        func.sum(FatoInboundProducao.vol_caixas).label('vol_inbound')
    ).filter(FatoInboundProducao.data_entrada >= hoje).group_by(FatoInboundProducao.sku).all()
    inbound_map = {r.sku: r.vol_inbound for r in inbound_query}

    # 5. CONSOLIDAR TODOS OS SKUS (VISÃO TOTAL)
    todos_skus = db.query(DimProduto).all()
    resultado_final = []

    for p in todos_skus:
        f = forecast_map.get(p.sku, {"sop": 0, "pmv": 0})
        b = backlog_map.get(p.sku)
        
        sop = float(f['sop'] or 0)
        pmv = float(f['pmv'] or 0)
        
        # Métrica de Backlog
        saldo_vendas = float(b.saldo_vendas or 0) if b else 0
        pedidos_m0 = float(b.pedidos_m0 or 0) if b else 0
        faturado_m0 = float(b.faturado_m0 or 0) if b else 0
        divida_m1 = float(b.divida_m1 or 0) if b else 0
        
        # Métrica de Abastecimento
        estoque = estoque_map.get(p.sku, 0)
        inbound = float(inbound_map.get(p.sku, 0))
        
        # SALDO PROJETADO S&OE
        saldo_final = (estoque + inbound) - saldo_vendas
        
        # PACING (%)
        pacing_vendas = (pedidos_m0 / sop * 100) if sop > 0 else 0
        pacing_producao = ((faturado_m0 + estoque - divida_m1) / sop * 100) if sop > 0 else 0

        resultado_final.append({
            "sku": p.sku,
            "descricao": p.descricao,
            "curva": p.curva,
            "categoria": p.categoria,
            "forecast_sop": sop,
            "pedidos_m0": pedidos_m0,
            "faturado_m0": faturado_m0,
            "divida_m1": divida_m1,
            "backlog_total": saldo_vendas,
            "estoque_d0": estoque,
            "inbound_futuro": inbound,
            "saldo_projetado": saldo_final,
            "fat_em_risco": abs(saldo_final * pmv) if saldo_final < 0 else 0,
            "pacing_vendas": round(pacing_vendas, 1),
            "pacing_producao": round(pacing_producao, 1)
        })

    # Ordenação: Prioridade para Saldo Negativo em itens AA
    resultado_final.sort(key=lambda x: (x['saldo_projetado'] >= 0, x['curva'] not in ['AA', 'AB'], -x['fat_em_risco']))

    return {
        "ciclo": ciclo_atual,
        "dia_mes": hoje.day,
        "total_skus": len(resultado_final),
        "dados": resultado_final
    }