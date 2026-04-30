from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date, and_, desc

# Adapte as importações de acordo com a estrutura exata do Nexus
from app.core.database import get_db
from app.models.domain_models import FatoIbpGranular, FatoVendas
# from app.core.auth import get_current_user_admin # Seu middleware de segurança

router = APIRouter(prefix="/api/v1/kpis", tags=["Auditoria S&OP"])

def get_subquery_vendas_reais(db: Session):
    """
    Cria uma subquery reutilizável que agrupa a FatoVendas por SKU e Mês.
    Usamos func.date_trunc para garantir que a data de venda bata com o mes_projetado.
    """
    return db.query(
        FatoVendas.sku,
        func.date_trunc('month', FatoVendas.data_pedido).cast(Date).label("mes_venda"),
        func.sum(FatoVendas.qt_pedido).label("real_caixas"),
        func.sum(FatoVendas.vl_pedido).label("real_financeiro")
    ).group_by(
        FatoVendas.sku,
        func.date_trunc('month', FatoVendas.data_pedido).cast(Date)
    ).subquery()


@router.get("/macro")
def get_kpis_macro(
    ciclo: str = Query(..., description="Ciclo S&OP (ex: 04/2026)"),
    db: Session = Depends(get_db)
    # current_user = Depends(get_current_user_admin)
):
    """
    Alimenta os 3 Cards Superiores da tela.
    Calcula o WMAPE Global (Caixas) e FVA financeiro.
    """
    vendas_sub = get_subquery_vendas_reais(db)

    # Coalesce garante que, se não houver venda ainda (mês futuro), assumimos 0 para o banco não dar erro de Null
    real_cx = func.coalesce(vendas_sub.c.real_caixas, 0)
    
    # Só calculamos erro se o mês já tiver venda (real_cx > 0). Mês futuro não entra no WMAPE do ciclo.
    is_mes_fechado = real_cx > 0

    resultados = db.query(
        func.sum(FatoIbpGranular.vol_ia).label("tot_ia"),
        func.sum(FatoIbpGranular.vol_bottomup).label("tot_bu"),
        func.sum(FatoIbpGranular.vol_final).label("tot_final"),
        func.sum(real_cx).filter(is_mes_fechado).label("tot_real_fechado"),
        
        # Erros Absolutos apenas para os meses que já possuem vendas
        func.sum(func.abs(FatoIbpGranular.vol_ia - real_cx)).filter(is_mes_fechado).label("err_ia"),
        func.sum(func.abs(FatoIbpGranular.vol_bottomup - real_cx)).filter(is_mes_fechado).label("err_bu"),
        func.sum(func.abs(FatoIbpGranular.vol_final - real_cx)).filter(is_mes_fechado).label("err_final"),
    ).outerjoin(
        vendas_sub, 
        and_(
            FatoIbpGranular.sku == vendas_sub.c.sku,
            FatoIbpGranular.mes_projetado == vendas_sub.c.mes_venda
        )
    ).filter(FatoIbpGranular.ciclo_sop == ciclo).first()

    # Proteção contra divisão por zero e formatação do JSON
    tot_real = float(resultados.tot_real_fechado or 0)
    
    acuracia_ia = max(0, 1 - (float(resultados.err_ia or 0) / tot_real)) * 100 if tot_real > 0 else 0
    acuracia_bu = max(0, 1 - (float(resultados.err_bu or 0) / tot_real)) * 100 if tot_real > 0 else 0
    acuracia_final = max(0, 1 - (float(resultados.err_final or 0) / tot_real)) * 100 if tot_real > 0 else 0

    return {
        "acuracia_ia": round(acuracia_ia, 1),
        "acuracia_bu": round(acuracia_bu, 1),
        "acuracia_final": round(acuracia_final, 1),
        "fva_bu": round(acuracia_bu - acuracia_ia, 1), # Se Vendas piorou a IA, será negativo
        "fva_final": round(acuracia_final - acuracia_ia, 1) # Se S&OP melhorou, será positivo
    }


@router.get("/curva")
def get_kpis_curva(
    ciclo: str = Query(..., description="Ciclo S&OP (ex: 04/2026)"),
    db: Session = Depends(get_db)
):
    """
    Alimenta o Gráfico Recharts (Evolução ao longo dos meses projetados).
    """
    vendas_sub = get_subquery_vendas_reais(db)

    resultados = db.query(
        FatoIbpGranular.mes_projetado,
        func.sum(FatoIbpGranular.vol_ia).label("ia"),
        func.sum(FatoIbpGranular.vol_bottomup).label("bu"),
        func.sum(FatoIbpGranular.vol_supply).label("sp"),
        func.sum(FatoIbpGranular.vol_final).label("final"),
        func.sum(func.coalesce(vendas_sub.c.real_caixas, 0)).label("real")
    ).outerjoin(
        vendas_sub, 
        and_(
            FatoIbpGranular.sku == vendas_sub.c.sku,
            FatoIbpGranular.mes_projetado == vendas_sub.c.mes_venda
        )
    ).filter(
        FatoIbpGranular.ciclo_sop == ciclo
    ).group_by(
        FatoIbpGranular.mes_projetado
    ).order_by(
        FatoIbpGranular.mes_projetado
    ).all()

    # Formata para o Array exato que o Recharts espera
    return {
        "dados": [
            {
                "name": row.mes_projetado.strftime("%m/%Y"), # Ex: '04/2026'
                "IA": float(row.ia or 0),
                "Comercial": float(row.bu or 0),
                "Supply": float(row.sp or 0),
                "Final": float(row.final or 0),
                # Se não houver venda real, retorna None para a linha do gráfico não despencar para o zero
                "Realizado": float(row.real) if row.real > 0 else None 
            } for row in resultados
        ]
    }


@router.get("/ofensores")
def get_kpis_ofensores(
    ciclo: str = Query(..., description="Ciclo S&OP (ex: 04/2026)"),
    visao: str = Query("caixas", description="'caixas' ou 'financeiro'"),
    db: Session = Depends(get_db)
):
    """
    Alimenta a tabela de "Top 10 Ofensores" calculando e ordenando tudo nativamente no Postgres.
    """
    vendas_sub = get_subquery_vendas_reais(db)
    
    real_cx = func.coalesce(vendas_sub.c.real_caixas, 0)
    real_fin = func.coalesce(vendas_sub.c.real_financeiro, 0)
    
    # Filtro: Apenas avalia ofensores em meses que já tiveram vendas (não pune meses futuros)
    is_mes_fechado = real_cx > 0

    if visao == "financeiro":
        calc_erro = func.abs((FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado) - real_fin)
    else:
        calc_erro = func.abs(FatoIbpGranular.vol_final - real_cx)

    ofensores = db.query(
        FatoIbpGranular.cgc,
        FatoIbpGranular.sku,
        FatoIbpGranular.vendedor_nome,
        func.sum(calc_erro).label("tamanho_erro")
    ).outerjoin(
        vendas_sub, 
        and_(
            FatoIbpGranular.sku == vendas_sub.c.sku,
            FatoIbpGranular.mes_projetado == vendas_sub.c.mes_venda
        )
    ).filter(
        FatoIbpGranular.ciclo_sop == ciclo,
        is_mes_fechado
    ).group_by(
        FatoIbpGranular.cgc,
        FatoIbpGranular.sku,
        FatoIbpGranular.vendedor_nome
    ).order_by(
        desc("tamanho_erro")
    ).limit(10).all()

    return {
        "ofensores": [
            {
                "cgc": row.cgc,
                "sku": row.sku,
                "responsavel": row.vendedor_nome,
                "erro": float(row.tamanho_erro)
            } for row in ofensores
        ]
    }