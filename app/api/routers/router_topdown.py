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
    """Constrói a árvore de decisão macro limitando estritamente aos meses M2, M3 e M4."""
    try:
        ciclo = get_current_cycle(db)
        ciclo_ant = get_previous_cycle(db)
        
        mes_c, ano_c = map(int, ciclo.split('/'))
        ciclo_atual_dt = datetime.date(ano_c, mes_c, 1)
        
        data_ini = ciclo_atual_dt + relativedelta(months=2) # Inicia no M2
        data_fim = ciclo_atual_dt + relativedelta(months=4) # Termina no M4
        
        query = db.query(
            DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('td'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .filter(
             FatoIbpGranular.ciclo_sop == ciclo,
             FatoIbpGranular.mes_projetado >= data_ini,
             FatoIbpGranular.mes_projetado <= data_fim
         )

        if categoria_filtro and valor_filtro:
            if categoria_filtro == 'categoria': query = query.filter(DimProduto.categoria == valor_filtro)
            elif categoria_filtro == 'segmento': query = query.filter(DimProduto.segmento == valor_filtro)

        resultados = query.group_by(
            DimProduto.categoria, DimProduto.segmento, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        query_ant = db.query(
            FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_final).label('ant')
        ).filter(
            FatoIbpGranular.ciclo_sop == ciclo_ant,
            FatoIbpGranular.mes_projetado >= data_ini,
            FatoIbpGranular.mes_projetado <= data_fim
        )
        
        if categoria_filtro and valor_filtro:
            query_ant = query_ant.join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)
            if categoria_filtro == 'categoria': query_ant = query_ant.filter(DimProduto.categoria == valor_filtro)
            elif categoria_filtro == 'segmento': query_ant = query_ant.filter(DimProduto.segmento == valor_filtro)
            
        resultados_ant = query_ant.group_by(FatoIbpGranular.sku, FatoIbpGranular.mes_projetado).all()
        
        mapa_ant = {}
        for r in resultados_ant:
            ms_iso = str(r.mes_projetado).split()[0]
            ms_padrao = ms_iso[:-2] + "01"
            mapa_ant[f"{r.sku}|{ms_padrao}"] = int(r.ant or 0)

        arvore = {}
        for r in resultados:
            cat = r.categoria or 'SEM CATEGORIA'
            seg = r.segmento or 'SEM SEGMENTO'
            sku = r.sku
            
            if cat not in arvore:
                arvore[cat] = {"id": cat, "chave_matriz": cat, "nome": cat, "tipo": "categoria", "subRows": {}, "meses_map": {}}
            
            if seg not in arvore[cat]["subRows"]:
                arvore[cat]["subRows"][seg] = {"id": f"{cat}|{seg}", "chave_matriz": f"{cat}|{seg}", "nome": seg, "tipo": "segmento", "subRows": {}, "meses_map": {}}
                
            if sku not in arvore[cat]["subRows"][seg]["subRows"]:
                arvore[cat]["subRows"][seg]["subRows"][sku] = {
                    "id": f"{cat}|{seg}|{sku}", "chave_matriz": f"{cat}|{seg}|{sku}", "nome": r.descricao, "produto": sku, "tipo": "produto", "meses_map": {}
                }
            
            mes_str_iso = str(r.mes_projetado).split()[0]
            mes_banco_str = mes_str_iso[:-2] + "01"
            mes_pt_str = r.mes_projetado.strftime('%m/%Y') if isinstance(r.mes_projetado, datetime.date) else mes_str_iso
            
            vol_ant_val = mapa_ant.get(f"{sku}|{mes_banco_str}", 0)
            vol_ia = int(r.ia or 0)
            vol_td = int(r.td) if r.td is not None else vol_ia
            pmv = float(r.pmv or 0)

            arvore[cat]["subRows"][seg]["subRows"][sku]["meses_map"][mes_banco_str] = {
                "mes_banco": mes_banco_str, "mes_str": mes_pt_str, "vol_ia": vol_ia, 
                "vol_anterior": vol_ant_val, "vol_ajustado": vol_td, "pmv": pmv
            }

            s_map = arvore[cat]["subRows"][seg]["meses_map"]
            if mes_banco_str not in s_map:
                s_map[mes_banco_str] = {"mes_banco": mes_banco_str, "mes_str": mes_pt_str, "vol_ia": 0, "vol_anterior": 0, "vol_ajustado": 0, "receita": 0}
            s_map[mes_banco_str]["vol_ia"] += vol_ia
            s_map[mes_banco_str]["vol_anterior"] += vol_ant_val
            s_map[mes_banco_str]["vol_ajustado"] += vol_td
            s_map[mes_banco_str]["receita"] += (vol_td * pmv)
            s_map[mes_banco_str]["pmv"] = s_map[mes_banco_str]["receita"] / s_map[mes_banco_str]["vol_ajustado"] if s_map[mes_banco_str]["vol_ajustado"] > 0 else 0

            c_map = arvore[cat]["meses_map"]
            if mes_banco_str not in c_map:
                c_map[mes_banco_str] = {"mes_banco": mes_banco_str, "mes_str": mes_pt_str, "vol_ia": 0, "vol_anterior": 0, "vol_ajustado": 0, "receita": 0}
            c_map[mes_banco_str]["vol_ia"] += vol_ia
            c_map[mes_banco_str]["vol_anterior"] += vol_ant_val
            c_map[mes_banco_str]["vol_ajustado"] += vol_td
            c_map[mes_banco_str]["receita"] += (vol_td * pmv)
            c_map[mes_banco_str]["pmv"] = c_map[mes_banco_str]["receita"] / c_map[mes_banco_str]["vol_ajustado"] if c_map[mes_banco_str]["vol_ajustado"] > 0 else 0

        for c in arvore.values():
            c["meses"] = sorted(list(c["meses_map"].values()), key=lambda x: x["mes_banco"])
            del c["meses_map"]
            for s in c["subRows"].values():
                s["meses"] = sorted(list(s["meses_map"].values()), key=lambda x: x["mes_banco"])
                del s["meses_map"]
                for sk in s["subRows"].values():
                    sk["meses"] = sorted(list(sk["meses_map"].values()), key=lambda x: x["mes_banco"])
                    del sk["meses_map"]
                s["subRows"] = list(s["subRows"].values())
            c["subRows"] = list(c["subRows"].values())

        return {"status": "success", "dados": list(arvore.values())}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/status")
async def topdown_status(db: Session = Depends(get_db)):
    ciclo = get_current_cycle(db)
    reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down Arena').first()
    return {"is_fechado": reg.status == 'Fechado' if reg else False}

@router.post("/salvar")
async def salvar_rascunho_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    check_global_lock(db, get_current_cycle(db))
    try:
        ciclo = get_current_cycle(db)
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            volume_alvo = int(ajuste.novo_volume)
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == ajuste.sku)
            linhas = query.all()
            if not linhas: continue

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
                
                # A CASCATA: Rascunho também passa o bastão para as fases seguintes
                l.vol_topdown = rateado
                l.vol_bottomup = rateado
                l.vol_supply = rateado
                l.vol_final = rateado
                l.vol_meta = rateado

        db.commit()
        return {"status": "success", "message": "Rascunho salvo com sucesso."}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_ratear_topdown(payload: PayloadAprovarTopDown, db: Session = Depends(get_db), usuario: dict = Depends(require_admin)):
    ciclo = get_current_cycle(db)
    check_global_lock(db, ciclo)
    try:
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            volume_alvo = int(ajuste.novo_volume)
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == ajuste.sku)
            linhas = query.all()
            if not linhas: continue

            total_base_antigo = sum([float(l.vol_final or 0) for l in linhas])
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
                
                # A CASCATA: Congelar garante a transferência da Meta
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
async def grafico_tatico_topdown(chave_matriz: str, nivel_hierarquia: str = 'produto', db: Session = Depends(get_db)):
    try:
        partes = chave_matriz.split('|')
        categoria = None
        segmento = None
        sku_alvo = None

        if nivel_hierarquia == 'categoria':
            categoria = partes[0]
        elif nivel_hierarquia == 'segmento':
            categoria = partes[0]
            segmento = partes[1] if len(partes) > 1 else None
        else:
            sku_alvo = partes[-1]
        
        hoje = datetime.date.today()
        ciclo_atual_dt = hoje.replace(day=1)
        ciclo_atual = ciclo_atual_dt.strftime('%m/%Y')
        ciclo_anterior = (ciclo_atual_dt - relativedelta(months=1)).strftime('%m/%Y')
        
        # 1. Histórico base (2 Anos)
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol'))\
                   .join(DimProduto, FatoVendas.sku == DimProduto.sku)\
                   .filter(FatoVendas.data_pedido >= ciclo_atual_dt - relativedelta(years=2))
        
        # 2. Toda a base IBP
        q_ibp = db.query(
            FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('ia'), 
            func.sum(FatoIbpGranular.vol_topdown).label('td'), 
            func.sum(FatoIbpGranular.vol_final).label('final')
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)

        if sku_alvo:
            q_hist = q_hist.filter(FatoVendas.sku == sku_alvo)
            q_ibp = q_ibp.filter(FatoIbpGranular.sku == sku_alvo)
        elif segmento:
            q_hist = q_hist.filter(DimProduto.categoria == categoria, DimProduto.segmento == segmento)
            q_ibp = q_ibp.filter(DimProduto.categoria == categoria, DimProduto.segmento == segmento)
        elif categoria:
            q_hist = q_hist.filter(DimProduto.categoria == categoria)
            q_ibp = q_ibp.filter(DimProduto.categoria == categoria)

        hist_dict = {h.mes_ano: int(h.vol or 0) for h in q_hist.group_by('mes_ano').all()}
        ibp_res = q_ibp.group_by(FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado).all()

        proj_por_mes = defaultdict(lambda: defaultdict(dict))
        for r in ibp_res:
            ms = r.mes_projetado.strftime('%Y-%m')
            proj_por_mes[ms][r.ciclo_sop] = {
                'ia': int(r.ia or 0), 
                'td': int(r.td) if r.td is not None else int(r.ia or 0), 
                'final': int(r.final or 0)
            }

        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        
        inicio_grafico = ciclo_atual_dt - relativedelta(months=24)
        fim_grafico = ciclo_atual_dt + relativedelta(months=5)
        
        calendario = {}
        curr = inicio_grafico
        while curr <= fim_grafico:
            mes_str = curr.strftime('%Y-%m')
            calendario[mes_str] = {"Realizado": hist_dict.get(mes_str, 0), "IA": None, "CicloAnterior": None, "TopDown": None}
            curr += relativedelta(months=1)

        for ms in calendario.keys():
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            
            if ciclo_atual in proj_por_mes[ms]:
                calendario[ms]["IA"] = proj_por_mes[ms][ciclo_atual]["ia"]
                calendario[ms]["TopDown"] = proj_por_mes[ms][ciclo_atual]["td"]

            if mes_dt >= ciclo_atual_dt:
                if ciclo_anterior in proj_por_mes[ms]:
                    calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_anterior]["final"]
            else:
                ciclo_congelamento_dt = mes_dt - relativedelta(months=2)
                ciclo_congelamento_str = ciclo_congelamento_dt.strftime('%m/%Y')
                
                if ciclo_congelamento_str in proj_por_mes[ms]:
                    calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_congelamento_str]["final"]
                else:
                    ciclo_fallback_str = (mes_dt - relativedelta(months=1)).strftime('%m/%Y')
                    if ciclo_fallback_str in proj_por_mes[ms]:
                        calendario[ms]["CicloAnterior"] = proj_por_mes[ms][ciclo_fallback_str]["final"]

        timeline = []
        for ms, v in sorted(calendario.items()):
            mes_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            
            timeline.append({
                "name": f"{meses_pt[mes_dt.month - 1]}/{mes_dt.strftime('%y')}",
                "data_iso": f"{ms}-01",
                "Realizado": None if mes_dt >= ciclo_atual_dt else (v["Realizado"] or 0),
                "IA": v["IA"],
                "CicloAnterior": v["CicloAnterior"],
                "TopDown": v["TopDown"]
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))