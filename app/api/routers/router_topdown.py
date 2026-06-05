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

from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe,
    registrar_log_auditoria
)

router = APIRouter(prefix="/api/v1/consensus/macro", tags=["Consenso Top-Down"])

class AjusteTopDown(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarTopDown(BaseModel):
    ajustes: List[AjusteTopDown]

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria.")
    return usuario

@router.get("")
async def listar_topdown(categoria_filtro: str = None, valor_filtro: str = None, db: Session = Depends(get_db)):
    """Constrói a árvore de decisão macro: Categoria -> Segmento -> SKU"""
    try:
        ciclo = get_current_cycle(db)
        
        query = db.query(
            DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('td'),
            func.sum(FatoIbpGranular.vol_anterior).label('ant'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .filter(FatoIbpGranular.ciclo_sop == ciclo)

        if categoria_filtro and valor_filtro:
            if categoria_filtro == 'categoria': query = query.filter(DimProduto.categoria == valor_filtro)
            elif categoria_filtro == 'segmento': query = query.filter(DimProduto.segmento == valor_filtro)

        resultados = query.group_by(
            DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        arvore = {}
        for r in resultados:
            cat = r.categoria or 'SEM CATEGORIA'
            seg = r.segmento or 'SEM SEGMENTO'
            sku = r.sku
            
            if cat not in arvore:
                arvore[cat] = {"id": cat, "chave_matriz": cat, "nome": cat, "tipo": "categoria", "subRows": {}}
            
            if seg not in arvore[cat]["subRows"]:
                arvore[cat]["subRows"][seg] = {"id": f"{cat}|{seg}", "chave_matriz": f"{cat}|{seg}", "nome": seg, "tipo": "segmento", "subRows": {}}
                
            if sku not in arvore[cat]["subRows"][seg]["subRows"]:
                arvore[cat]["subRows"][seg]["subRows"][sku] = {
                    "id": f"{cat}|{seg}|{sku}", "chave_matriz": f"{cat}|{seg}|{sku}", "nome": r.descricao, "produto": sku, "tipo": "produto", "meses": []
                }
            
            # Nota: a lógica do "if r.td is not None" garante que o ZERO será lido como ZERO legítimo, e não falso
            arvore[cat]["subRows"][seg]["subRows"][sku]["meses"].append({
                "mes_banco": r.mes_projetado.strftime('%Y-%m-01') if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado),
                "mes_str": r.mes_projetado.strftime('%m/%Y') if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado),
                "vol_ia": int(r.ia or 0),
                "vol_anterior": int(r.ant or 0),
                "vol_ajustado": int(r.td) if r.td is not None else int(r.ia or 0),
                "pmv": float(r.pmv or 0)
            })

        for c in arvore.values():
            for s in c["subRows"].values():
                s["subRows"] = list(s["subRows"].values())
            c["subRows"] = list(c["subRows"].values())

        return {"status": "success", "dados": list(arvore.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/status")
async def topdown_status(db: Session = Depends(get_db)):
    """Verifica se a fase Top-Down (Diretoria) já foi ratificada."""
    ciclo = get_current_cycle(db)
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
    return {"is_fechado": reg.status == 'Fechado' if reg else False}

@router.post("/salvar")
async def salvar_rascunho_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Salva as decisões executivas apenas na coluna vol_topdown, com Rateio Inteligente para não multiplicar volumes."""
    check_global_lock(db)
    try:
        ciclo = get_current_cycle(db)
        
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            volume_alvo = int(ajuste.novo_volume)
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == ajuste.sku)
            linhas = query.all()
            if not linhas: continue

            # MOTOR DE RATEIO INTELIGENTE APLICADO AO RASCUNHO (Fim do Bug de Multiplicação Fantasma)
            base_total_bu = sum([float(l.vol_bottomup or 0) for l in linhas])
            base_total_sp = sum([float(l.vol_supply or 0) for l in linhas])
            
            usar_base_sp = base_total_bu <= 0
            total_base = base_total_sp if usar_base_sp else base_total_bu
            
            soma_dist = 0
            total_clientes = len(linhas)
            
            for i, l in enumerate(linhas):
                if i == total_clientes - 1:
                    rateado = volume_alvo - soma_dist 
                else:
                    vol_referencia = float(l.vol_supply or 0) if usar_base_sp else float(l.vol_bottomup or 0)
                    peso = vol_referencia / total_base if total_base > 0 else 1.0 / total_clientes
                    rateado = int(round(volume_alvo * peso))
                    soma_dist += rateado
                
                # RASCUNHO: Atualiza APENAS a coluna vol_topdown, preservando o Rateio oficial intacto até a aprovação final
                l.vol_topdown = rateado

        db.commit()
        return {"status": "success", "message": "Rascunho salvo com sucesso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_ratear_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    """Publica a estratégia Macro, rateia os volumes aprovados pela Diretoria e tranca a fase."""
    check_global_lock(db)
    
    try:
        ciclo = get_current_cycle(db)
        
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            volume_alvo = int(ajuste.novo_volume)
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == ajuste.sku)
            linhas = query.all()
            if not linhas: continue

            total_base_antigo = sum([float(l.vol_final or 0) for l in linhas])

            # MOTOR DE RATEIO INTELIGENTE (Proporcional ao Comercial ou Supply)
            base_total_bu = sum([float(l.vol_bottomup or 0) for l in linhas])
            base_total_sp = sum([float(l.vol_supply or 0) for l in linhas])
            
            usar_base_sp = base_total_bu <= 0
            total_base = base_total_sp if usar_base_sp else base_total_bu
            
            soma_dist = 0
            total_clientes = len(linhas)
            
            for i, l in enumerate(linhas):
                if i == total_clientes - 1:
                    rateado = volume_alvo - soma_dist 
                else:
                    vol_referencia = float(l.vol_supply or 0) if usar_base_sp else float(l.vol_bottomup or 0)
                    peso = vol_referencia / total_base if total_base > 0 else 1.0 / total_clientes
                    rateado = int(round(volume_alvo * peso))
                    soma_dist += rateado
                
                # Efeito Cascata Estratégico: O Top-Down sobrepõe as intenções de todas as áreas a partir de agora
                l.vol_topdown = rateado
                l.vol_bottomup = rateado
                l.vol_supply = rateado
                l.vol_final = rateado
                l.vol_meta = rateado

            nome_user = usuario.get('nome', usuario.get('email', 'Desconhecido'))
            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Top-Down Arena",
                usuario=nome_user, sku=ajuste.sku, cliente="TODOS_OS_CLIENTES",
                mes=data_alvo, v_antigo=int(total_base_antigo), v_novo=volume_alvo
            )

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
        if not registro:
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down Arena', status='Fechado'))
        else:
            registro.status = 'Fechado'

        db.commit()
        return {"status": "success", "message": "Estratégia Macro Congelada com Sucesso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_tatico_topdown(chave_matriz: str, db: Session = Depends(get_db)):
    """Constrói a visualização tática comparando a IA, o Histórico e a Proposta atual da Diretoria."""
    try:
        sku_alvo = chave_matriz.split('|')[-1]
        
        hoje = datetime.date.today()
        ciclo_atual_dt = hoje.replace(day=1)
        ciclo_atual = ciclo_atual_dt.strftime('%m/%Y')
        ciclo_anterior = (ciclo_atual_dt - relativedelta(months=1)).strftime('%m/%Y')
        
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol'))\
                   .filter(FatoVendas.sku == sku_alvo, FatoVendas.data_pedido >= hoje - relativedelta(years=2))
        
        q_ibp = db.query(
            FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_topdown).label('td'), func.sum(FatoIbpGranular.vol_final).label('final')
        ).filter(FatoIbpGranular.sku == sku_alvo)

        hist_dict = {h.mes_ano: int(h.vol or 0) for h in q_hist.group_by('mes_ano').all()}
        ibp_res = q_ibp.group_by(FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado).all()

        proj_por_mes = defaultdict(lambda: defaultdict(dict))
        for r in ibp_res:
            ms = r.mes_projetado.strftime('%Y-%m')
            proj_por_mes[ms][r.ciclo_sop] = {
                'ia': int(r.ia or 0), 'td': int(r.td) if r.td is not None else int(r.ia or 0), 'final': int(r.final or 0)
            }

        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        
        inicio_grafico = ciclo_atual_dt - relativedelta(months=12)
        fim_grafico = ciclo_atual_dt + relativedelta(months=5)
        
        calendario = {}
        curr = inicio_grafico
        while curr <= fim_grafico:
            mes_str = curr.strftime('%Y-%m')
            calendario[mes_str] = {"Realizado": hist_dict.get(mes_str, 0), "IA": None, "CicloAnterior": None, "TopDown": None}
            curr += relativedelta(months=1)

        ciclo_base_zero = datetime.date(2026, 4, 1)

        for ms in calendario.keys():
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            
            alvo_ia_dt = mes_dt - relativedelta(months=2)
            if alvo_ia_dt < ciclo_base_zero: alvo_ia_dt = ciclo_base_zero
            if alvo_ia_dt > ciclo_atual_dt: alvo_ia_dt = ciclo_atual_dt
            ciclo_ia_str = alvo_ia_dt.strftime('%m/%Y')
            
            if ciclo_ia_str in proj_por_mes[ms]:
                calendario[ms]["IA"] = proj_por_mes[ms][ciclo_ia_str]["ia"]

            if ciclo_anterior in proj_por_mes[ms]:
                calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_anterior]["final"]

            if ciclo_atual in proj_por_mes[ms]:
                calendario[ms]["TopDown"] = proj_por_mes[ms][ciclo_atual]["td"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            
            timeline.append({
                "name": f"{meses_pt[mes_dt.month - 1]}/{mes_dt.strftime('%y')}",
                "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt > hoje else (v["Realizado"] or 0),
                "IA": v["IA"],
                "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"] if mes_dt >= ciclo_atual_dt else None
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))