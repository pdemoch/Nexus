from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case
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

# --- ROTA 1: SINCRONIZAÇÃO DE ESTOQUE (Mantida a sua versão que já funciona) ---
@router.post("/sync-stock")
async def sincronizar_estoque_api90(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Puxa a API 90 com paginação e mapeia 'arm' para 'qtd_dispo'."""
    if usuario['funcao'] not in ['Administrador', 'Planejador S&OP', 'Lideranca']:
        raise HTTPException(status_code=403, detail="Sem permissão.")

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
                if response.status != 200: break
                dados = await response.json(content_type=None)
                if not dados or not isinstance(dados, list): break
                if lote_anterior and dados[0] == lote_anterior[0]: break
                
                for row in dados:
                    if str(row.get('arm', '')).strip() == "05":
                        sku = str(row.get('produto', '')).replace(".0", "").strip()
                        try: qtd = float(row.get('quantidade', 0))
                        except: qtd = 0.0
                        if sku: estoque_novo[sku] = estoque_novo.get(sku, 0) + qtd
                
                lote_anterior = dados
                offset += len(dados)
                if len(dados) < limit: break

    try:
        db.query(FatoEstoqueD0).delete()
        agora = datetime.datetime.utcnow()
        novos_registros = [FatoEstoqueD0(sku=sku, qtd_dispo=qtd, data_atualizacao=agora) for sku, qtd in estoque_novo.items()]
        if novos_registros: db.bulk_save_objects(novos_registros)
        db.commit()
        return {"status": "success", "count": len(novos_registros)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# --- ROTA 2: RADAR S&OE 2.0 (BOTTOM-UP: CLIENTE -> SKU) ---
@router.get("/radar")
async def carregar_radar_soe(db: Session = Depends(get_db)):
    """Constrói o Radar consolidando a nível Cliente/Razão Social e depois agrupando no SKU."""
    hoje = datetime.date.today()
    primeiro_dia = hoje.replace(day=1)
    mes_ref = hoje.strftime("%m/%Y")
    
    # 1. Busca Metas do Mês Agrupadas por SKU + Razão Social
    metas_query = db.query(
        FatoIbpGranular.sku,
        DimCliente.razaosocial,
        DimCliente.regional,
        DimCliente.vendedor_nome,
        func.sum(FatoIbpGranular.vol_final).label('vol_sop'),
        func.sum(FatoIbpGranular.vol_meta).label('vol_meta'),
        # O cálculo financeiro perfeito: Volume x PMV linha a linha
        func.sum(FatoIbpGranular.vol_meta * FatoIbpGranular.pmv_aplicado).label('val_meta'),
        func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_medio')
    ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
     .filter(FatoIbpGranular.ciclo_sop == mes_ref, FatoIbpGranular.mes_projetado == primeiro_dia)\
     .group_by(FatoIbpGranular.sku, DimCliente.razaosocial, DimCliente.regional, DimCliente.vendedor_nome).all()

    # 2. Busca Execução (Vendas) Agrupadas por SKU + Razão Social
    vendas_query = db.query(
        FatoVendas.sku,
        DimCliente.razaosocial,
        func.sum(FatoVendas.qt_pedido).label('pedidos'),
        func.sum(FatoVendas.qtfatura).label('faturado'),
        func.sum(FatoVendas.qtcorte).label('corte')
    ).join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
     .filter(FatoVendas.data_pedido >= primeiro_dia)\
     .group_by(FatoVendas.sku, DimCliente.razaosocial).all()

    # Mapeamento Rápido das Vendas: vendas_map[sku][razaosocial] = dados
    vendas_map = {}
    for v in vendas_query:
        if v.sku not in vendas_map: vendas_map[v.sku] = {}
        vendas_map[v.sku][v.razaosocial] = v

    # 3. Estoque e Inbound
    estoque_map = {r.sku: r.qtd_dispo for r in db.query(FatoEstoqueD0.sku, FatoEstoqueD0.qtd_dispo).all()}
    inbound_map = {r.sku: r.vol for r in db.query(FatoInboundProducao.sku, func.sum(FatoInboundProducao.vol_caixas).label('vol')).filter(FatoInboundProducao.data_entrada >= hoje).group_by(FatoInboundProducao.sku).all()}

    # 4. Construção da Árvore (Bottom-Up)
    sku_master = {}
    
    # Inicializa com os produtos do cadastro para garantir descrições
    for p in db.query(DimProduto).all():
        sku_master[p.sku] = {
            "sku": p.sku, "descricao": p.descricao, "curva": p.curva, "pmv": 0,
            "metas": {"vol_sop": 0, "vol_meta": 0, "val_meta": 0.0},
            "execucao": {"pedidos_qtd": 0, "faturado_qtd": 0, "faturado_val": 0.0, "corte_qtd": 0, "corte_val": 0.0},
            "clientes_pendentes": [], "clientes_superados": []
        }

    # Processa as Metas e cruza com as Vendas (Cliente a Cliente)
    for m in metas_query:
        sku = m.sku
        cliente = m.razaosocial
        if sku not in sku_master: continue
        
        venda_cli = vendas_map.get(sku, {}).get(cliente)
        pedidos = float(venda_cli.pedidos or 0) if venda_cli else 0
        faturado = float(venda_cli.faturado or 0) if venda_cli else 0
        corte = float(venda_cli.corte or 0) if venda_cli else 0
        
        pmv_cli = float(m.pmv_medio or 0)
        vol_meta = float(m.vol_meta or 0)
        
        # Consolida no SKU
        node = sku_master[sku]
        node["pmv"] = pmv_cli # Serve como base de PMV para esse SKU
        node["metas"]["vol_sop"] += float(m.vol_sop or 0)
        node["metas"]["vol_meta"] += vol_meta
        node["metas"]["val_meta"] += float(m.val_meta or 0)
        
        node["execucao"]["pedidos_qtd"] += pedidos
        node["execucao"]["faturado_qtd"] += faturado
        node["execucao"]["faturado_val"] += (faturado * pmv_cli)
        node["execucao"]["corte_qtd"] += corte
        node["execucao"]["corte_val"] += (corte * pmv_cli)

        # Regra de Classificação do Cliente
        cliente_obj = {
            "cliente": cliente,
            "vendedor": m.vendedor_nome,
            "regional": m.regional,
            "meta_vol": vol_meta,
            "pedidos_qtd": pedidos,
            "faturado_qtd": faturado,
            "gap_caixas": vol_meta - pedidos if vol_meta > pedidos else 0,
            "gap_financeiro": (vol_meta - pedidos) * pmv_cli if vol_meta > pedidos else 0
        }
        
        if pedidos < vol_meta:
            node["clientes_pendentes"].append(cliente_obj)
        else:
            node["clientes_superados"].append(cliente_obj)

    # 5. Fechamento da Matemática (Logística e Backlog)
    resultado = []
    for sku, data in sku_master.items():
        if data["metas"]["vol_meta"] == 0 and data["execucao"]["pedidos_qtd"] == 0 and estoque_map.get(sku, 0) == 0:
            continue # Ignora SKUs mortos sem estoque, sem meta e sem pedido
            
        estoque = estoque_map.get(sku, 0)
        inbound = float(inbound_map.get(sku, 0))
        
        # A conta infalível: O que venderam, menos o que faturaram e cortaram
        backlog = data["execucao"]["pedidos_qtd"] - (data["execucao"]["faturado_qtd"] + data["execucao"]["corte_qtd"])
        
        saldo_proj = (estoque + inbound) - backlog
        
        data["execucao"]["pacing"] = (data["execucao"]["faturado_qtd"] / data["metas"]["vol_sop"] * 100) if data["metas"]["vol_sop"] > 0 else 0
        data["logistica"] = {
            "estoque_d0": estoque,
            "backlog_total": backlog,
            "saldo_projetado": saldo_proj,
            "fat_em_risco": abs(saldo_proj * data["pmv"]) if saldo_proj < 0 else 0
        }
        
        # Ordena os clientes pendentes do maior GAP para o menor
        data["clientes_pendentes"].sort(key=lambda x: -x["gap_financeiro"])
        resultado.append(data)

    return {"mes": mes_ref, "dia": hoje.day, "dados": sorted(resultado, key=lambda x: x['curva'])}

# --- ROTA 3: CHURN GLOBAL (Visão Comercial Consolidada) ---
@router.get("/oportunidades")
async def carregar_oportunidades_venda(db: Session = Depends(get_db)):
    """Calcula quem não comprou nada (por Razão Social)."""
    hoje = datetime.date.today()
    mes_ref = hoje.strftime("%m/%Y")
    
    metas = db.query(
        DimCliente.razaosocial, DimCliente.regional,
        func.sum(FatoIbpGranular.vol_meta).label('meta_vol'),
        func.sum(FatoIbpGranular.vol_meta * FatoIbpGranular.pmv_aplicado).label('val_meta')
    ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
     .filter(FatoIbpGranular.ciclo_sop == mes_ref)\
     .group_by(DimCliente.razaosocial, DimCliente.regional).all()

    vendas_mes = {v.razaosocial for v in db.query(DimCliente.razaosocial).join(FatoVendas, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= hoje.replace(day=1)).group_by(DimCliente.razaosocial).all()}
    
    # Frequência usando a Data do último pedido associado à Razão Social
    ultimas = {u.razaosocial: u.dta for u in db.query(DimCliente.razaosocial, func.max(FatoVendas.data_pedido).label('dta')).join(FatoVendas, FatoVendas.cgc == DimCliente.cgc).group_by(DimCliente.razaosocial).all()}

    res = []
    for m in metas:
        if m.razaosocial not in vendas_mes:
            last_date = ultimas.get(m.razaosocial)
            atraso = (hoje - last_date).days if last_date else 99
            res.append({
                "cliente": m.razaosocial,
                "regional": m.regional,
                "meta_vol": float(m.meta_vol or 0),
                "valor_estimado": float(m.val_meta or 0),
                "dias_sem_compra": atraso,
                "status": "Crítico" if atraso > 15 else "Alerta"
            })
            
    return sorted(res, key=lambda x: -x['valor_estimado'])