from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from pydantic import BaseModel

from app.core.database import get_db
from app.models.domain_models import FatoIbpGranular, DimProduto
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_previous_cycle, get_working_window_months, 
    get_truth_query, check_global_lock, check_origin_lock, registrar_log_auditoria,
    parse_date_safe
)

router = APIRouter(prefix="/api/v1/consensus", tags=["Consenso Top-Down"])

# --- SCHEMAS DE ENTRADA (MANTIDOS PARA NÃO QUEBRAR O FRONT) ---
class AjusteTopDown(BaseModel):
    sku: str
    mes: str
    novo_volume: int

class PayloadSalvarTopDown(BaseModel):
    ajustes: List[AjusteTopDown]

# =====================================================================
# ROTA GET: ÁRVORE HIERÁRQUICA E DOSSIÊ 360º
# =====================================================================
@router.get("/macro")
def obter_hierarquia_topdown(db: Session = Depends(get_db), usuario=Depends(get_current_user)):
    """
    Retorna a estrutura expansiva: Categoria -> SKU -> Meses (M2-M4).
    Cruza com o ciclo passado para FVA e traz a acurácia da IA.
    """
    if usuario['funcao'] not in ['Administrador', 'Marketing', 'Comercial']:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    ciclo_atual = get_current_cycle(db)
    ciclo_anterior = get_previous_cycle(db)
    meses_trabalho = get_working_window_months(db)
    
    data_ini, data_fim = meses_trabalho[0], meses_trabalho[-1]

    # 1. Buscar a base de verdade do CICLO ATUAL (Agrupando Categoria e SKU)
    query_atual = get_truth_query(db, ciclo_atual, data_ini, data_fim)
    dados_atual = query_atual.with_entities(
        DimProduto.categoria,
        DimProduto.sku,
        DimProduto.descricao,
        DimProduto.modelo_vencedor,
        DimProduto.acuracia_ia,
        FatoIbpGranular.mes_projetado,
        func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
        func.sum(FatoIbpGranular.vol_topdown).label('vol_td'),
        func.sum(FatoIbpGranular.vol_bottomup).label('vol_bu'),
        func.sum(FatoIbpGranular.vol_supply).label('vol_sp'),
        func.sum(FatoIbpGranular.vol_final).label('vol_final'),
        func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_medio')
    ).group_by(
        DimProduto.categoria, DimProduto.sku, DimProduto.descricao, 
        DimProduto.modelo_vencedor, DimProduto.acuracia_ia, FatoIbpGranular.mes_projetado
    ).all()

    # 2. Buscar o CICLO ANTERIOR para o cálculo da "Ponte de Ciclo" (Cycle-over-Cycle)
    query_anterior = get_truth_query(db, ciclo_anterior, data_ini, data_fim)
    dados_anterior = query_anterior.with_entities(
        DimProduto.sku,
        FatoIbpGranular.mes_projetado,
        func.sum(FatoIbpGranular.vol_final).label('vol_final_ant')
    ).group_by(DimProduto.sku, FatoIbpGranular.mes_projetado).all()

    # Dicionário de acesso rápido O(1) para o volume antigo
    dict_anterior = {(d.sku, d.mes_projetado): d.vol_final_ant for d in dados_anterior}

    # 3. Construir a Árvore JSON (Hierarquia)
    categorias_dict = {}

    for row in dados_atual:
        cat_nome = row.categoria or "OUTROS"
        sku = row.sku
        mes = row.mes_projetado
        
        # Inicia a Categoria
        if cat_nome not in categorias_dict:
            categorias_dict[cat_nome] = {"nome": cat_nome, "skus": {}}
            
        # Inicia o SKU dentro da Categoria
        if sku not in categorias_dict[cat_nome]["skus"]:
            categorias_dict[cat_nome]["skus"][sku] = {
                "sku": sku,
                "descricao": row.descricao,
                "modelo_vencedor": row.modelo_vencedor or "Ensemble Estatístico",
                "acuracia_ia": float(row.acuracia_ia) if row.acuracia_ia else 0.0,
                "meses": []
            }
            
        vol_antigo = dict_anterior.get((sku, mes), 0)
        
        # Popula o Mês
        categorias_dict[cat_nome]["skus"][sku]["meses"].append({
            "mes": mes.strftime("%Y-%m-%d"),
            "vol_ia": int(row.vol_ia or 0),
            "vol_td": int(row.vol_td or 0),
            "vol_bu": int(row.vol_bu or 0),
            "vol_sp": int(row.vol_sp or 0),
            "vol_final": int(row.vol_final or 0),
            "pmv_medio": float(row.pmv_medio or 0.0),
            "vol_ciclo_anterior": int(vol_antigo)
        })

    # 4. Formatar e ordenar para o Front-End
    hierarquia_final = []
    for cat_nome, cat_data in categorias_dict.items():
        lista_skus = list(cat_data["skus"].values())
        for s in lista_skus:
            s["meses"].sort(key=lambda x: x["mes"]) # Garante a ordem cronológica M2, M3, M4
            
        hierarquia_final.append({
            "nome": cat_nome,
            "skus": sorted(lista_skus, key=lambda x: x["descricao"])
        })
        
    hierarquia_final.sort(key=lambda x: x["nome"])

    return {
        "status": "success",
        "ciclo_ativo": ciclo_atual,
        "meses_janela": [m.strftime("%Y-%m-%d") for m in meses_trabalho],
        "hierarquia": hierarquia_final
    }

# =====================================================================
# ROTA POST: GRAVAR EDIÇÕES DO TOP-DOWN E RATEAR
# =====================================================================
@router.post("/save")
def salvar_ajustes_topdown(payload: PayloadSalvarTopDown, db: Session = Depends(get_db), usuario=Depends(get_current_user)):
    """Recebe as edições da tela, faz o rateio para os clientes e gera auditoria."""
    if usuario['funcao'] not in ['Administrador', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    ciclo = get_current_cycle(db)
    check_global_lock(db, ciclo)
    check_origin_lock(db, ciclo, 'Top-Down')

    skus_afetados = list(set([a.sku for a in payload.ajustes]))
    
    # Validação M2-M4: Impede edição fora da janela permitida
    meses_validos = get_working_window_months(db)

    for ajuste in payload.ajustes:
        mes_dt = parse_date_safe(ajuste.mes)
        if mes_dt not in meses_validos:
            continue # Ignora se tentar gravar M1 ou M5 via API

        # RATEIO PROPORCIONAL: Busca como as caixas estão divididas entre os clientes
        linhas = db.query(FatoIbpGranular).filter(
            FatoIbpGranular.ciclo_sop == ciclo,
            FatoIbpGranular.sku == ajuste.sku,
            FatoIbpGranular.mes_projetado == mes_dt
        ).all()

        if not linhas: continue

        vol_atual_total = sum(l.vol_topdown for l in linhas)
        if vol_atual_total == ajuste.novo_volume: continue 

        diferenca = ajuste.novo_volume - vol_atual_total

        # Ordena para garantir que a sobra do arredondamento vá para o maior cliente
        linhas.sort(key=lambda x: x.vol_topdown, reverse=True)

        for idx, linha in enumerate(linhas):
            peso = linha.vol_topdown / vol_atual_total if vol_atual_total > 0 else 1 / len(linhas)
            incremento = int(round(diferenca * peso))
            
            # Se for o último cliente da lista, ele absorve a quebra de arredondamento
            if idx == len(linhas) - 1:
                incremento = diferenca - sum(int(round(diferenca * (l.vol_topdown / vol_atual_total if vol_atual_total > 0 else 1/len(linhas)))) for l in linhas[:-1])

            vol_antigo = linha.vol_topdown
            linha.vol_topdown = max(0, vol_antigo + incremento)
            linha.vol_final = linha.vol_topdown # Cascata direta

            registrar_log_auditoria(db, ciclo, 'Top-Down', usuario['nome'], ajuste.sku, linha.cgc, mes_dt, vol_antigo, linha.vol_topdown)

    db.commit()
    return {"status": "success", "message": f"{len(skus_afetados)} SKUs atualizados e rateados com sucesso."}