from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from app.core.database import get_db
from app.models.domain_models import FatoIbpGranular, DimProduto, FatoVendas, ControleCiclo
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_previous_cycle, get_working_window_months, 
    get_truth_query, check_global_lock, check_origin_lock, registrar_log_auditoria,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus", tags=["Consenso Top-Down"])

# --- SCHEMAS (Contratos de Dados) ---
class AjusteTopDown(BaseModel):
    sku: str
    mes: str
    novo_volume: int

class PayloadSalvarTopDown(BaseModel):
    ajustes: List[AjusteTopDown]

class PayloadCongelar(BaseModel):
    origem_ajuste: str
    ajustes: List[dict]

# =====================================================================
# SERVIÇOS DE APOIO (Lógica de Negócio Isolada - SOLID)
# =====================================================================
class TopDownService:
    @staticmethod
    def get_sku_history(db: Session, sku: str):
        """Busca o realizado de vendas dos últimos 12 meses."""
        hoje = date.today().replace(day=1)
        inicio = hoje - relativedelta(months=12)
        
        vendas = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).filter(FatoVendas.sku == sku, FatoVendas.data_pedido >= inicio)\
         .group_by('mes').order_by('mes').all()
        
        return {v.mes: int(v.realizado) for v in vendas}

# =====================================================================
# ROTAS DE CONSULTA (GET)
# =====================================================================

@router.get("/macro")
def obter_hierarquia_topdown(db: Session = Depends(get_db), usuario=Depends(get_current_user)):
    """Retorna a Árvore Hierárquica: Categoria -> SKU -> Meses (M2-M4)."""
    ciclo_atual = get_current_cycle(db)
    ciclo_anterior = get_previous_cycle(db)
    meses_trabalho = get_working_window_months(db)
    data_ini, data_fim = meses_trabalho[0], meses_trabalho[-1]

    # 1. Dados do Ciclo Atual
    query_atual = get_truth_query(db, ciclo_atual, data_ini, data_fim)
    dados_atual = query_atual.with_entities(
        DimProduto.categoria, DimProduto.sku, DimProduto.descricao,
        DimProduto.modelo_vencedor, DimProduto.acuracia_ia,
        FatoIbpGranular.mes_projetado,
        func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
        func.sum(FatoIbpGranular.vol_topdown).label('vol_td'),
        func.sum(FatoIbpGranular.vol_final).label('vol_final'),
        func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_medio')
    ).group_by(
        DimProduto.categoria, DimProduto.sku, DimProduto.descricao, 
        DimProduto.modelo_vencedor, DimProduto.acuracia_ia, FatoIbpGranular.mes_projetado
    ).all()

    # 2. Dados do Ciclo Anterior (Ponte Cycle-over-Cycle)
    query_anterior = get_truth_query(db, ciclo_anterior, data_ini, data_fim)
    dados_anterior = query_anterior.with_entities(
        FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
        func.sum(FatoIbpGranular.vol_final).label('vol_ant')
    ).group_by(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado).all()
    
    dict_ant = {(d.sku, d.mes_projetado): d.vol_ant for d in dados_anterior}

    # 3. Montagem da Árvore
    tree = {}
    for r in dados_atual:
        cat = r.categoria or "SEM CATEGORIA"
        if cat not in tree: tree[cat] = {"nome": cat, "skus": {}}
        
        if r.sku not in tree[cat]["skus"]:
            tree[cat]["skus"][r.sku] = {
                "sku": r.sku, "descricao": r.descricao,
                "modelo_vencedor": r.modelo_vencedor or "Ensemble",
                "acuracia_ia": float(r.acuracia_ia or 0.0),
                "meses": []
            }
        
        vol_antigo = dict_ant.get((r.sku, r.mes_projetado), 0)
        tree[cat]["skus"][r.sku]["meses"].append({
            "mes": r.mes_projetado.strftime("%Y-%m-%d"),
            "vol_ia": int(r.vol_ia or 0),
            "vol_td": int(r.vol_td or 0),
            "vol_final": int(r.vol_final or 0),
            "pmv_medio": float(r.pmv_medio or 0.0),
            "vol_ciclo_anterior": int(vol_antigo)
        })

    # Formatação final para o Front-End
    hierarquia = []
    for cat_nome, content in tree.items():
        skus_list = list(content["skus"].values())
        for s in skus_list: s["meses"].sort(key=lambda x: x["mes"])
        hierarquia.append({"nome": cat_nome, "skus": sorted(skus_list, key=lambda x: x["descricao"])})

    return {"status": "success", "ciclo_ativo": ciclo_atual, "meses_janela": [m.strftime("%Y-%m-%d") for m in meses_trabalho], "hierarquia": sorted(hierarquia, key=lambda x: x["nome"])}

@router.get("/macro/status")
def obter_status_topdown(db: Session = Depends(get_db)):
    """Verifica se as travas globais ou de departamento estão ativas."""
    ciclo = get_current_cycle(db)
    travas = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        and_(ControleCiclo.origem.in_(['Top-Down', 'S&OP-Final']), ControleCiclo.status == 'Fechado')
    ).first()
    return {"status": "success", "ciclo": ciclo, "is_topdown_fechado": travas is not None}

@router.get("/macro/grafico")
def obter_timeline_dossie(produto: str, db: Session = Depends(get_db)):
    """Gera a timeline 'Passado + Futuro' para o dossiê do SKU."""
    historico = TopDownService.get_sku_history(db, produto)
    ciclo = get_current_cycle(db)
    
    # Futuro (Próximos 4 meses para o gráfico ser completo)
    hoje = date.today().replace(day=1)
    fim_grafico = hoje + relativedelta(months=5)
    
    projeções = db.query(
        func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes'),
        func.sum(FatoIbpGranular.vol_ia).label('ia'),
        func.sum(FatoIbpGranular.vol_topdown).label('td'),
        func.sum(FatoIbpGranular.vol_final).label('final')
    ).filter(FatoIbpGranular.sku == produto, FatoIbpGranular.ciclo_sop == ciclo)\
     .group_by('mes').all()

    timeline = []
    # Mesclar Passado
    for mes, vol in historico.items():
        timeline.append({"name": mes, "Realizado": vol})
    
    # Mesclar Futuro
    for p in projeções:
        timeline.append({
            "name": p.mes,
            "IA": int(p.ia or 0),
            "Comercial": int(p.td or 0),
            "Final": int(p.final or 0)
        })
    
    return {"status": "success", "dados": timeline}

# =====================================================================
# ROTAS DE AÇÃO (POST)
# =====================================================================

@router.post("/save")
def salvar_topdown(payload: PayloadSalvarTopDown, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Aplica o rateio das edições de Marketing nos clientes."""
    ciclo = get_current_cycle(db)
    check_global_lock(db, ciclo)
    check_origin_lock(db, ciclo, 'Top-Down')
    
    for aj in payload.ajustes:
        mes_dt = parse_date_safe(aj.mes)
        linhas = db.query(FatoIbpGranular).filter(
            FatoIbpGranular.ciclo_sop == ciclo, FatoIbpGranular.sku == aj.sku, FatoIbpGranular.mes_projetado == mes_dt
        ).all()
        
        if not linhas: continue
        total_atual = sum(l.vol_topdown for l in linhas)
        if total_atual == aj.novo_volume: continue
        
        diff = aj.novo_volume - total_atual
        linhas.sort(key=lambda x: x.vol_topdown, reverse=True)
        
        for idx, linha in enumerate(linhas):
            peso = linha.vol_topdown / total_atual if total_atual > 0 else 1/len(linhas)
            inc = diff - sum(int(round(diff * (l.vol_topdown/total_atual if total_atual > 0 else 1/len(linhas)))) for l in linhas[:-1]) if idx == len(linhas)-1 else int(round(diff * peso))
            
            v_ant = linha.vol_topdown
            linha.vol_topdown = max(0, v_ant + inc)
            linha.vol_final = linha.vol_topdown
            registrar_log_auditoria(db, ciclo, 'Top-Down', user['nome'], aj.sku, linha.cgc, mes_dt, v_ant, linha.vol_topdown)
            
    db.commit()
    return {"status": "success"}

@router.post("/macro/congelar")
def congelar_ciclo_topdown(payload: PayloadCongelar, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Tranca as edições do Top-Down para este ciclo."""
    if user['funcao'] not in ['Administrador', 'Marketing']:
        raise HTTPException(status_code=403, detail="Permissão negada.")
    
    ciclo = get_current_cycle(db)
    trava = ControleCiclo(ciclo_sop=ciclo, origem='Top-Down', status='Fechado', data_fechamento=datetime.now())
    db.add(trava)
    db.commit()
    return {"status": "success", "message": "Ciclo Top-Down congelado."}