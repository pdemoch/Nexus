import datetime
from typing import List, Optional
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe,
    escrever_volume_rateado,
    propagar_para_jusante,
    ETAPA_METAS, ETAPA_SUPPLY, STATUS_CONGELADO
)

router = APIRouter(prefix="/api/v1/consensus/supply", tags=["Consenso Supply Review"])

# =====================================================================
# GOVERNANÇA E MODELOS
# =====================================================================
def require_supply_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Supply Chain']:
        raise HTTPException(status_code=403, detail="Acesso Restrito ao time de Supply Chain.")
    return usuario

class AjusteSupply(BaseModel):
    produto: str
    mes_projetado: str
    novo_volume: int
    justificativa: Optional[str] = None

class PayloadCongelarSupply(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteSupply]

# =====================================================================
# RADAR DE STATUS DA FASE (INTEGRAÇÃO COMERCIAL -> SUPPLY)
# =====================================================================
@router.get("/status")
async def checar_status_supply(db: Session = Depends(get_db)):
    try:
        ciclo = get_current_cycle(db)

        # Nomes/status alinhados ao banco (Supply/Metas/CONGELADO).
        tranca_sup = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo,
            func.upper(func.trim(ControleCiclo.origem)) == ETAPA_SUPPLY.upper()
        ).first()
        supply_fechado = str(tranca_sup.status).strip().upper() == STATUS_CONGELADO if tranca_sup else False

        tranca_comercial = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo,
            func.upper(func.trim(ControleCiclo.origem)) == ETAPA_METAS.upper()
        ).first()
        comercial_fechado = str(tranca_comercial.status).strip().upper() == STATUS_CONGELADO if tranca_comercial else False

        return {
            "status": "success", 
            "fechado": supply_fechado,
            "comercial_fechado": comercial_fechado
        }
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# ENDPOINT PRINCIPAL: ÁRVORE DA FÁBRICA (AGRUPADO POR SKU)
# =====================================================================
@router.get("")
async def listar_supply_review(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    try:
        ciclo = get_current_cycle(db)
        _mes_str, _ano_str = ciclo.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        
        data_ini = hoje + relativedelta(months=2)
        data_fim = hoje + relativedelta(months=4)
        meses_alvo = [(hoje + relativedelta(months=i)).strftime("%Y-%m-%d") for i in range(2, 5)]

        # Busca do Orçamento Top-Down Financeiro
        orc_query = db.execute(text("""
            SELECT sku, mes_projetado, receita_orcamento 
            FROM fato_orcamento 
            WHERE mes_projetado >= :m2 AND mes_projetado <= :m4
        """), {"m2": data_ini, "m4": data_fim}).fetchall()
        
        orc_dict = {}
        for o in orc_query:
            orc_dict[f"{o.sku}|{o.mes_projetado}"] = float(o.receita_orcamento or 0)

        q = get_truth_query(db, ciclo, data_ini, data_fim)

        # 🔥 BLINDAGEM SSOT: Cálculo Múltiplo de Faturamento Ponderado direto no Banco
        resultados = q.with_entities(
            FatoIbpGranular.sku,
            DimProduto.descricao.label('prod_desc'),
            DimProduto.categoria.label('prod_cat'),
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_meta).label('v_meta'), 
            func.sum(FatoIbpGranular.vol_supply).label('v_sup'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_avg'),
            func.sum(FatoIbpGranular.vol_meta * FatoIbpGranular.pmv_aplicado).label('rec_meta'),
            func.sum(FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_sup')
        ).group_by(
            FatoIbpGranular.sku, DimProduto.descricao, DimProduto.categoria, FatoIbpGranular.mes_projetado
        ).all()

        arvore = {}
        def criar_meses():
            return {"vol_topdown":0, "receita_meta":0, "vol_ia":0, "vol_comercial":0, "vol_supply":0, "receita_comercial":0, "pmv":0.0}

        for r in resultados:
            sk = str(r.sku).strip()
            de = str(r.prod_desc).strip()
            cat = str(r.prod_cat).strip()
            ms = str(r.mes_projetado)

            if cat not in arvore:
                arvore[cat] = {"nome": cat, "tipo": "categoria", "meses": {m: criar_meses() for m in meses_alvo}, "produtos": {}}
            
            if sk not in arvore[cat]["produtos"]:
                arvore[cat]["produtos"][sk] = {"id": sk, "nome": de, "tipo": "produto", "meses": {m: criar_meses() for m in meses_alvo}}

            if ms in meses_alvo:
                v_td = int(r.v_td or 0)
                v_ia = int(r.v_ia or 0)
                v_meta = int(r.v_meta or 0)
                v_sup = int(r.v_sup or 0)
                
                r_meta = float(r.rec_meta or 0)
                r_sup = float(r.rec_sup or 0)
                pmv_avg = float(r.pmv_avg or 0)
                
                v_orcamento_rec = orc_dict.get(f"{sk}|{ms}", 0.0)

                # Se o Supply ainda não interveio, a fábrica enxerga a Meta do Vendedor como alvo inicial
                vol_exibicao_supply = v_sup if v_sup > 0 else v_meta
                rec_exibicao_supply = r_sup if v_sup > 0 else r_meta

                for nivel in [arvore[cat]["meses"][ms], arvore[cat]["produtos"][sk]["meses"][ms]]:
                    nivel["vol_topdown"] += v_td
                    nivel["receita_meta"] += v_orcamento_rec
                    nivel["vol_ia"] += v_ia
                    nivel["vol_comercial"] += v_meta
                    nivel["vol_supply"] += vol_exibicao_supply
                    nivel["receita_comercial"] += rec_exibicao_supply
                    
                    # Cálculo Ponderado do PMV para o Frontend
                    if nivel["vol_supply"] > 0:
                        nivel["pmv"] = nivel["receita_comercial"] / nivel["vol_supply"]
                    elif nivel["vol_comercial"] > 0:
                        nivel["pmv"] = nivel["receita_comercial"] / nivel["vol_comercial"]
                    else:
                        nivel["pmv"] = pmv_avg

        final = []
        for cat_k, cat_v in arvore.items():
            prods = []
            for sk_k, sk_v in cat_v["produtos"].items():
                prods.append({
                    "id": sk_k, 
                    "nome": sk_v["nome"], 
                    "tipo": "produto", 
                    "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in sk_v["meses"].items()]
                })
            final.append({
                "id": cat_k, 
                "nome": cat_k, 
                "tipo": "categoria", 
                "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cat_v["meses"].items()], 
                "subRows": sorted(prods, key=lambda x: x["nome"])
            })

        return {"status": "success", "dados": sorted(final, key=lambda x: x["nome"])}
    except Exception as e:
        raise HTTPException(500, repr(e))

# =====================================================================
# MOTOR DE RATEIO FAIR-SHARE (CORTE OU INJEÇÃO JUSTA)
# =====================================================================
@router.post("/congelar")
async def aprovar_supply(payload: PayloadCongelarSupply, db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    """
    Rateia a decisao da fabrica em vol_supply e, ao congelar, propaga para o
    Final APENAS se o Final ainda nao foi publicado.

    Correcao central (o Padrao B nasceu aqui):
      Antes, cada linha fazia 'l.vol_supply = rateado' E 'l.vol_final = rateado'
      no mesmo passo. Ou seja, congelar o Supply SOBRESCREVIA o vol_final mesmo
      que a Diretoria ja tivesse publicado o S&OP Global — foi o que apagou os
      ajustes de 05/2026. Agora:
        1. escrever_volume_rateado grava SO vol_supply, com trava de balanco
           (soma == digitado) e zero explicito.
        2. propagar_para_jusante entrega ao Final SOMENTE se o Final nao estiver
           CONGELADO. Publicacao da Diretoria e respeitada.
      A justificativa do Supply e gravada em separado, para todas as linhas do
      grupo.
    """
    try:
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        registro = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo,
            func.upper(func.trim(ControleCiclo.origem)) == ETAPA_SUPPLY.upper()
        ).first()
        if registro and str(registro.status).strip().upper() == STATUS_CONGELADO:
            raise HTTPException(423, "Fase de Supply já está fechada.")

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            sku = str(ajuste.produto)

            # 1. Grava vol_supply com escopo completo + balanco + zero explicito.
            #    Peso: vol_meta (a venda comercial); na falta, vol_ia.
            #    check_imutabilidade_mes roda dentro de escrever_volume_rateado.
            escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=sku, mes=data_alvo,
                volume_alvo=int(ajuste.novo_volume), campo_destino="vol_supply",
                campos_peso=["vol_meta", "vol_ia"],
            )

            # 2. Justificativa da fabrica, para todas as linhas do grupo.
            if ajuste.justificativa is not None:
                db.execute(text("""
                    UPDATE fato_ibp_granular
                    SET justificativa_supply = :j
                    WHERE ciclo_sop = :c AND sku = :s AND mes_projetado = :m
                """), {"j": ajuste.justificativa, "c": ciclo, "s": sku, "m": data_alvo})

        # 3. Marca a etapa como CONGELADA (nome/status do banco).
        if not registro:
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=ETAPA_SUPPLY, status=STATUS_CONGELADO))
        else:
            registro.status = STATUS_CONGELADO

        db.flush()

        # 4. Propaga para o Final SO se ele nao estiver publicado. Substitui o
        #    'l.vol_final = rateado' cego que causava o Padrao B.
        propagar_para_jusante(db, ciclo, ETAPA_SUPPLY, "vol_supply")

        db.commit()
        return {"status": "success"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/destrancar")
async def destrancar_supply(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    ciclo = get_current_cycle(db)
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        func.upper(func.trim(ControleCiclo.origem)) == ETAPA_SUPPLY.upper()
    ).first()
    if registro:
        registro.status = 'Aberto'
        db.commit()
    return {"status": "success"}

# =====================================================================
# ENDPOINT DO DOSSIÊ GRÁFICO TÁTICO
# =====================================================================
@router.get("/grafico")
async def grafico_supply(produto_id: str, db: Session = Depends(get_db)):
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        _mes_str, _ano_str = ciclo_atual.split('/')
        hoje = datetime.date(int(_ano_str), int(_mes_str), 1)
        
        inicio_hist = hoje - relativedelta(months=24)
        m2_comercial = hoje + relativedelta(months=2)

        calendario = {}
        curr = inicio_hist
        while curr <= hoje + relativedelta(months=4):
            mes_str = curr.strftime('%Y-%m')
            calendario[mes_str] = {"Realizado": None, "Topdown": None, "IA": None, "Comercial": None, "Supply": None}
            curr += relativedelta(months=1)

        q_hist = db.query(
            func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('realizado')
        ).filter(FatoVendas.data_pedido >= inicio_hist, FatoVendas.sku == produto_id)

        for row in q_hist.group_by('mes_ano').all():
            if row.mes_ano in calendario:
                calendario[row.mes_ano]["Realizado"] = int(row.realizado or 0)

        q_proj = db.query(
            func.to_char(FatoIbpGranular.mes_projetado, 'YYYY-MM').label('mes_ano'),
            FatoIbpGranular.ciclo_sop,
            func.sum(FatoIbpGranular.vol_topdown).label('td'),
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_meta).label('meta'), 
            func.sum(FatoIbpGranular.vol_supply).label('sup')
        ).filter(FatoIbpGranular.sku == produto_id)

        proj_por_mes = defaultdict(dict)
        for row in q_proj.group_by('mes_ano', FatoIbpGranular.ciclo_sop).all():
            proj_por_mes[row.mes_ano][row.ciclo_sop] = {
                "td": int(row.td or 0),
                "ia": int(row.ia or 0), 
                "meta": int(row.meta or 0),
                "sup": int(row.sup or 0)
            }

        ciclo_base_zero = datetime.datetime.strptime('04/2026', '%m/%Y').date()
        ciclo_atual_dt = datetime.datetime.strptime(ciclo_atual, '%m/%Y').date()

        for mes_str in calendario.keys():
            mes_dt = datetime.datetime.strptime(mes_str, '%Y-%m').date()
            if mes_str not in proj_por_mes: continue

            alvo_ia_dt = mes_dt - relativedelta(months=2)
            if alvo_ia_dt < ciclo_base_zero: alvo_ia_dt = ciclo_base_zero
            if alvo_ia_dt > ciclo_atual_dt: alvo_ia_dt = ciclo_atual_dt
            ciclo_ia_str = alvo_ia_dt.strftime('%m/%Y')
            
            if ciclo_ia_str in proj_por_mes[mes_str]:
                calendario[mes_str]["IA"] = proj_por_mes[mes_str][ciclo_ia_str]["ia"]
                
            if ciclo_atual in proj_por_mes[mes_str]:
                calendario[mes_str]["Topdown"] = proj_por_mes[mes_str][ciclo_atual]["td"]
                calendario[mes_str]["Comercial"] = proj_por_mes[mes_str][ciclo_atual]["meta"]
                sup_val = proj_por_mes[mes_str][ciclo_atual]["sup"]
                calendario[mes_str]["Supply"] = sup_val if sup_val > 0 else proj_por_mes[mes_str][ciclo_atual]["meta"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            timeline.append({
                "name": ms,
                "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt > hoje else (v["Realizado"] or 0),
                "Topdown": v["Topdown"] if mes_dt >= m2_comercial else None,
                "IA": v["IA"],
                "Comercial": v["Comercial"] if mes_dt >= m2_comercial else None,
                "Supply": v["Supply"] if mes_dt >= m2_comercial else None
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))