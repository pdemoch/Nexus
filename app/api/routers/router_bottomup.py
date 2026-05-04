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
    get_current_cycle,
    get_previous_cycle,
    get_projection_window,
    get_truth_query,
    check_global_lock,
    check_origin_lock,
    parse_date_safe
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

@router.get("/filtros")
async def obter_filtros_busca(gerente_nome: str = None, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    q = db.query(DimCliente)
    filtro_gerente = usuario.get('gerente_nome') if usuario['funcao'] == 'Gerente' else gerente_nome
    if filtro_gerente: 
        q = q.filter(func.upper(func.trim(DimCliente.gerente_nome)) == filtro_gerente.strip().upper())
    
    return {
        "regionais": sorted({str(r.regional).strip() for r in q.distinct(DimCliente.regional).all() if r.regional}),
        "vendedores": sorted({str(v.vendedor_nome).strip() for v in q.distinct(DimCliente.vendedor_nome).all() if v.vendedor_nome})
    }

@router.get("") 
async def listar_micro(nivel_hierarquia: str, nome_responsavel: str, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        # ATUALIZAÇÃO: Passando 'db' para as funções de tempo
        m2, m4 = get_projection_window(db)
        ciclo = get_current_cycle(db)
        
        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        query_base = get_truth_query(db, ciclo, m2, m4)
        
        if filtro_vend: query_base = query_base.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
        if filtro_reg: query_base = query_base.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
        if usuario['funcao'] == 'Gerente': query_base = query_base.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())

        resultados = query_base.with_entities(
            DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado, 
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'), 
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv'),
            func.sum(FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu')
        ).group_by(
            DimCliente.razaosocial, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()
        
        arvore = defaultdict(lambda: {
            "id": "", "nome": "", "tipo": "cliente", 
            "meses": defaultdict(lambda: {"vol_ia":0, "vol_td": 0, "vol_ajustado":0, "receita":0}), 
            "produtos": defaultdict(lambda: {
                "id": "", "nome": "", "tipo": "produto", 
                "meses": defaultdict(lambda: {"vol_ia":0, "vol_td": 0, "vol_ajustado":0, "receita":0})
            })
        })
        
        for r in resultados:
            rz = str(r.razaosocial or "DESC").strip()
            sku = r.sku
            ms = str(r.mes_projetado)
            rec = float(r.rec_bu or 0)
            
            arvore[rz]["id"] = arvore[rz]["nome"] = rz
            arvore[rz]["produtos"][sku]["id"] = f"{rz}|{sku}"
            arvore[rz]["produtos"][sku]["nome"] = r.descricao
            
            for target in [arvore[rz]["meses"][ms], arvore[rz]["produtos"][sku]["meses"][ms]]:
                target["vol_ia"] += int(r.v_ia or 0)
                target["vol_td"] += int(r.v_td or 0)
                target["vol_ajustado"] += int(r.v_bu or 0)
                target["receita"] += rec
                target["pmv"] = float(r.pmv or 0)

        dados = [
            {
                "id": v["id"], "chave_matriz": v["id"], "nome": v["nome"], "tipo": v["tipo"], 
                "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in v["meses"].items()], 
                "subRows": [
                    {
                        "id": p["id"], "chave_matriz": p["id"], "nome": p["nome"], "produto": pk, "tipo": p["tipo"], 
                        "meses": [{"mes_banco": k, "mes_str": parse_date_safe(k).strftime("%b/%y").capitalize(), **mv} for k, mv in p["meses"].items()]
                    } for pk, p in v["produtos"].items()
                ]
            } for v in arvore.values()
        ]
        return {"status": "success", "dados": dados}
    except Exception as e: 
        raise HTTPException(500, repr(e))

@router.get("/status")
async def verificar_status_micro(nivel_hierarquia: str = 'vendedor', nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        # ATUALIZAÇÃO: Passando 'db'
        ciclo = get_current_cycle(db)
        origem_trava = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        
        if not origem_trava:
            return {"is_fechado": False}
            
        registro = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo, 
            func.upper(func.trim(ControleCiclo.origem)) == origem_trava.strip().upper()
        ).first()
        
        return {"is_fechado": registro.status == 'Fechado' if registro else False}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/congelar")
async def congelar_micro(nivel_hierarquia: str, nome_responsavel: str, payload: PayloadCongelarBU, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        # ATUALIZAÇÃO: Passando 'db'
        ciclo = get_current_cycle(db)
        check_global_lock(db, ciclo)

        reg_td = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not reg_td or reg_td.status != 'Fechado':
             raise HTTPException(status_code=403, detail="A estratégia macro ainda não foi liberada pela Diretoria (Fase 1). Aguarde.")
        
        origem_trava = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        check_origin_lock(db, ciclo, origem_trava)
        
        data_limite_str = (datetime.date.today() - relativedelta(months=12)).strftime('%Y-%m-%d')

        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            
            query = get_truth_query(db, ciclo, str(data_alvo), str(data_alvo))

            if filtro_vend: query = query.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())
            if filtro_reg: query = query.filter(func.upper(func.trim(DimCliente.regional)) == filtro_reg.strip().upper())
            if usuario['funcao'] == 'Gerente': query = query.filter(func.upper(func.trim(DimCliente.gerente_nome)) == usuario['gerente_nome'].strip().upper())

            if ajuste.nivel in ['cliente', 'produto']: 
                query = query.filter(func.trim(DimCliente.razaosocial) == partes[0].strip())
            if ajuste.nivel == 'produto': 
                query = query.filter(FatoIbpGranular.sku == partes[1].strip())

            linhas = query.all()
            if not linhas: continue

            skus = list({l.sku for l in linhas if l.sku})
            cgcs = list({l.cgc for l in linhas if l.cgc})
            
            historico_query = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_hist'))\
                .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                .filter(FatoVendas.sku.in_(skus), FatoVendas.cgc.in_(cgcs), FatoVendas.data_pedido >= data_limite_str)
            
            if filtro_vend: historico_query = historico_query.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == filtro_vend.strip().upper())

            historico = historico_query.group_by(FatoVendas.sku, FatoVendas.cgc).all()
                
            dict_hist = {f"{h.sku}_{h.cgc}": float(h.vol_hist or 0) for h in historico}
            soma_hist = sum([dict_hist.get(f"{l.sku}_{l.cgc}", 0) for l in linhas])
            
            soma_dist = 0
            volume_total = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_total - soma_dist 
                else:
                    peso = dict_hist.get(f"{l.sku}_{l.cgc}", 0) / soma_hist if soma_hist > 0 else 1.0 / len(linhas)
                    rateado = int(round(volume_total * peso))
                    soma_dist += rateado
                
                # A CASCATA DE HERANÇA CORRIGIDA:
                l.vol_bottomup = rateado
                l.vol_supply = rateado
                l.vol_final = rateado
                l.vol_meta = rateado

        origem_trava = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else nome_responsavel
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == origem_trava).first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=origem_trava, status='Fechado'))
        else: 
            registro.status = 'Fechado'
            
        db.commit()
        return {"status": "success"}
    except HTTPException as he: 
        raise he
    except Exception as e: 
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_micro(chave_matriz: str, nivel_hierarquia: str = 'vendedor', nome_responsavel: str = '', db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        p = chave_matriz.split('|')
        razao, sku = p[0], p[1] if len(p) > 1 else None
        
        # ATUALIZAÇÃO: Passando 'db'
        m2_str, m4_str = get_projection_window(db)
        m2_date = parse_date_safe(m2_str) if isinstance(m2_str, str) else m2_str
        
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_ant = get_previous_cycle(db)
        ciclo_atual = get_current_cycle(db)
        
        filtro_vend = usuario['nome_vendedor'] if usuario['funcao'] == 'Executivo' else (nome_responsavel if nivel_hierarquia == 'vendedor' else None)
        filtro_reg = nome_responsavel if nivel_hierarquia != 'vendedor' and usuario['funcao'] != 'Executivo' else None

        # 1. Histórico Real de Vendas
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol_real'))\
            .join(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))
            
        # 2. Histórico Mestre IBP (Super Query do Bottom-Up)
        q_all_ibp = db.query(
            FatoIbpGranular.ciclo_sop,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_bottomup).label('bu')
        ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)

        # Filtros
        if filtro_vend: 
            v_nome = filtro_vend.strip().upper()
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == v_nome)
            q_all_ibp = q_all_ibp.filter(func.upper(func.trim(DimCliente.vendedor_nome)) == v_nome)
        if filtro_reg: 
            r_nome = filtro_reg.strip().upper()
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.regional)) == r_nome)
            q_all_ibp = q_all_ibp.filter(func.upper(func.trim(DimCliente.regional)) == r_nome)
        if usuario['funcao'] == 'Gerente':
            g_nome = usuario['gerente_nome'].strip().upper()
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome)
            q_all_ibp = q_all_ibp.filter(func.upper(func.trim(DimCliente.gerente_nome)) == g_nome)

        if razao: 
            rz = razao.strip()
            q_hist = q_hist.filter(func.trim(DimCliente.razaosocial) == rz)
            q_all_ibp = q_all_ibp.filter(func.trim(DimCliente.razaosocial) == rz)
        if sku: 
            s = sku.strip()
            q_hist = q_hist.filter(FatoVendas.sku == s)
            q_all_ibp = q_all_ibp.filter(FatoIbpGranular.sku == s)

        # Execução e Mapeamento 3D
        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in q_hist.group_by('mes_ano').all()}
        all_ibp_res = q_all_ibp.group_by(FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado).all()

        ibp_map = {}
        for r in all_ibp_res:
            d_iso = str(r.mes_projetado)
            if d_iso not in ibp_map: ibp_map[d_iso] = {}
            ibp_map[d_iso][r.ciclo_sop] = {'ia': int(r.ia or 0), 'bu': int(r.bu or 0)}

        timeline = []

        # 3. CONSTRUÇÃO DA TIMELINE S&OE (O PASSADO)
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str = dt.strftime('%Y-%m')
            dt_iso = dt.strftime('%Y-%m-%d')
            
            ciclo_do_mes = dt.strftime('%m/%Y')
            ciclo_mes_passado = (dt - relativedelta(months=1)).strftime('%m/%Y')
            
            dados_mes = ibp_map.get(dt_iso, {}).get(ciclo_do_mes, {})
            dados_lag1 = ibp_map.get(dt_iso, {}).get(ciclo_mes_passado, {})

            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(), 
                "data_iso": dt_iso,
                "Realizado": hist_dict.get(mes_str, 0), 
                "IA": dados_mes.get('ia', None), # IA Pura sem mascaramento
                "Consenso": None, 
                "CicloAnterior": dados_lag1.get('bu', None)
            })

        # 4. O PRESENTE M0 E O FUTURO
        curr_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        dados_atual_m0 = ibp_map.get(curr_iso, {}).get(ciclo_atual, {})
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)", "data_iso": curr_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": dados_atual_m0.get('ia', None), 
            "Consenso": None,
            "CicloAnterior": ibp_map.get(curr_iso, {}).get(ciclo_ant, {}).get('bu', None)
        })

        futuros = [d for d in ibp_map.keys() if ciclo_atual in ibp_map[d] and parse_date_safe(d) > mes_atual_inicio]
        futuros.sort()

        for p_iso in futuros:
            p_date = parse_date_safe(p_iso)
            dados_futuro = ibp_map.get(p_iso, {}).get(ciclo_atual, {})
            
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(), "data_iso": p_iso,
                "Realizado": None, 
                "IA": dados_futuro.get('ia', None), 
                "Consenso": dados_futuro.get('bu', None) if p_date >= m2_date else None, 
                "CicloAnterior": ibp_map.get(p_iso, {}).get(ciclo_ant, {}).get('bu', None)
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))