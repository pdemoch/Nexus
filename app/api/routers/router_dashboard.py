from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle, get_previous_cycle, get_projection_window, 
    get_truth_query, parse_date_safe, registrar_log_auditoria
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["S&OP Global Dashboard"])

class AjusteGlobal(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGlobal(BaseModel):
    ajustes: List[AjusteGlobal]


@router.get("/global")
async def carregar_dashboard_global(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    perfis_permitidos = ['Administrador', 'Gerente', 'Supply Chain', 'Marketing', 'C-Level', 'Diretoria']
    if usuario_logado.get('funcao') not in perfis_permitidos:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria, Gerência ou Supply Chain.")
        
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        
        # CORREÇÃO 1: Adaptação para a nova inteligência da janela de projeção
        meses_proj = get_projection_window(db, ciclo_atual)
        m2_str, m4_str = meses_proj[0], meses_proj[-1] 
        m2_date = parse_date_safe(m2_str)
        
        # 1. ORÇAMENTO FINANCEIRO
        orc_query = db.execute(text("""
            SELECT sku, mes_projetado, receita_orcamento 
            FROM fato_orcamento 
            WHERE mes_projetado >= :m2 AND mes_projetado <= :m4
        """), {"m2": m2_date, "m4": parse_date_safe(m4_str)}).fetchall()
        
        orc_dict = {}
        for o in orc_query:
            sku = o.sku
            mes_str = str(o.mes_projetado)
            if sku not in orc_dict: orc_dict[sku] = {}
            orc_dict[sku][mes_str] = float(o.receita_orcamento or 0)

        # 2. JANELA DE ASSERTIVIDADE (M-1)
        mes_passado = m2_date - relativedelta(months=1)
        ia_m1_query = db.query(FatoIbpGranular.sku, FatoIbpGranular.cgc, func.sum(FatoIbpGranular.vol_ia).label('vol_ia_m1')).filter(FatoIbpGranular.ciclo_sop == ciclo_anterior, FatoIbpGranular.mes_projetado == mes_passado).group_by(FatoIbpGranular.sku, FatoIbpGranular.cgc).all()
        vendas_m1_query = db.query(FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_real_m1')).filter(FatoVendas.data_pedido >= mes_passado, FatoVendas.data_pedido < m2_date).group_by(FatoVendas.sku, FatoVendas.cgc).all()

        assertividade_map = {}
        vendas_m1_dict = {f"{v.sku}|{v.cgc}": float(v.vol_real_m1 or 0) for v in vendas_m1_query}
        for ia in ia_m1_query:
            chave, v_real, v_ia = f"{ia.sku}|{ia.cgc}", vendas_m1_dict.get(f"{ia.sku}|{ia.cgc}", 0), float(ia.vol_ia_m1 or 0)
            assertividade_map[chave] = max(0, 100 - ((abs(v_ia - v_real) / v_real) * 100)) if v_real > 0 and v_ia > 0 else (0 if v_real > 0 else 100 if v_ia == 0 else 0)

        # 3. COMPARAÇÃO COM CICLO ANTERIOR
        prev_query = db.query(FatoIbpGranular.sku, FatoIbpGranular.cgc, FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_final).filter(FatoIbpGranular.ciclo_sop == ciclo_anterior, FatoIbpGranular.mes_projetado >= m2_date, FatoIbpGranular.mes_projetado <= parse_date_safe(m4_str)).all()
        prev_map = {f"{p.sku}|{p.cgc}|{str(p.mes_projetado)}": float(p.vol_final or 0) for p in prev_query}

        # 4. CONSULTA ATUAL S&OP GLOBAL - AJUSTADA CIRURGICAMENTE
        query = get_truth_query(db, ciclo_atual, m2_date, parse_date_safe(m4_str)).with_entities(
            DimProduto.categoria, FatoIbpGranular.sku, DimProduto.descricao, DimCliente.razaosocial, DimCliente.cgc,
            FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, 
            FatoIbpGranular.vol_bottomup, FatoIbpGranular.vol_supply, FatoIbpGranular.vol_final, 
            FatoIbpGranular.vol_meta, FatoIbpGranular.pmv_aplicado, FatoIbpGranular.justificativa_supply,
            (FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            (FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            (FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            (FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_supply'),
            (FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado).label('rec_final')
        )

        dados_enriquecidos = []
        for r in query.all():
            cgc, sku, mes_str = r.cgc, r.sku, str(r.mes_projetado)
            chave_hist, chave_prev = f"{sku}|{cgc}", f"{sku}|{cgc}|{mes_str}"

            vol_ia, vol_final, vol_bu, vol_sp, pmv_atual = float(r.vol_ia or 0), float(r.vol_final or 0), float(r.vol_bottomup or 0), float(r.vol_supply or 0), float(r.pmv_aplicado or 0)
            vol_anterior = prev_map.get(chave_prev, vol_final)
            
            dados_enriquecidos.append({
                "categoria": r.categoria, "sku": sku, "descricao": r.descricao, "cgc": cgc, "razaosocial": r.razaosocial, "mes_projetado": mes_str,
                "vol_ia": int(vol_ia), "vol_topdown": int(r.vol_topdown or 0), "vol_bottomup": int(vol_bu), "vol_supply": int(vol_sp), "vol_final": int(vol_final), "vol_meta": int(r.vol_meta or 0),
                "pmv_aplicado": pmv_atual, "rec_ia": float(r.rec_ia or 0), "rec_td": float(r.rec_td or 0), "rec_bu": float(r.rec_bu or 0), "rec_supply": float(r.rec_supply or 0), "rec_final": float(r.rec_final or 0),
                "justificativa_supply": getattr(r, 'justificativa_supply', '') or "", "delta_ia_comercial_vol": int(vol_ia - vol_bu), "impacto_financeiro_ia_brl": round((vol_ia - vol_bu) * pmv_atual, 2),
                "vol_hist_media": 0.0, "pmv_hist_media": 0.0, "crescimento_hist_pct": 0.0, "assertividade_ia": round(assertividade_map.get(chave_hist, 0.0), 1),
                "vol_anterior": int(vol_anterior), "delta_ciclo_vol": int(vol_final - vol_anterior), "corte_supply_vol": int(max(0, vol_bu - vol_sp)), "unmet_demand_brl": round(max(0, vol_bu - vol_sp) * pmv_atual, 2),
                "var_pmv_pct": 0.0
            })
        
        # CORREÇÃO 2: Bypass do ORM substituído por raw SQL para evitar erros de UndefinedTable
        reg_status = db.execute(text("SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'S&OP-Final'"), {"c": ciclo_atual}).scalar()
        is_locked = (reg_status == 'Fechado')
        
        reg_sp_status = db.execute(text("SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Supply Review'"), {"c": ciclo_atual}).scalar()
        
        if not is_locked and (reg_sp_status != 'Fechado'):
            return {"status": "success", "is_locked": True, "lock_message": "Aguardando encerramento do Supply Review (Fase 3)", "dados": dados_enriquecidos, "orcamento": orc_dict}

        return {"status": "success", "is_locked": is_locked, "lock_message": "Demanda Irrestrita Publicada" if is_locked else "Plano Aberto para Approvação Final", "dados": dados_enriquecidos, "orcamento": orc_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/grafico")
async def grafico_global(chave_matriz: str, nivel_hierarquia: str = 'categoria', db: Session = Depends(get_db)):
    try:
        partes = chave_matriz.split('|')
        categoria, sku, cliente = None, None, None
        
        if nivel_hierarquia == 'cliente':
            cliente = partes[0]
            if len(partes) > 1: sku = partes[1]
        else:
            categoria = partes[0]
            if len(partes) > 1: sku = partes[1]

        ciclo_atual = get_current_cycle(db)
        ciclo_ant = get_previous_cycle(db)
        
        meses_proj = get_projection_window(db, ciclo_atual)
        m2_str = meses_proj[0]
        m2_date = parse_date_safe(m2_str)
        
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)

        inicio_hist = hoje - relativedelta(years=2)

        orc_graf_query = db.execute(text("""
            SELECT o.mes_projetado, SUM(o.receita_orcamento) as rec
            FROM fato_orcamento o
            JOIN dim_produtos p ON o.sku = p.sku
            WHERE o.mes_projetado >= :inicio
            AND (:categoria IS NULL OR p.categoria = :categoria)
            AND (:sku IS NULL OR o.sku = :sku)
            GROUP BY o.mes_projetado
        """), {"inicio": inicio_hist, "categoria": categoria, "sku": sku}).fetchall()
        
        orc_graf_map = {str(o.mes_projetado): float(o.rec) for o in orc_graf_query}

        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol')).join(DimProduto, FatoVendas.sku == DimProduto.sku).join(DimCliente, FatoVendas.cgc == DimCliente.cgc).filter(FatoVendas.data_pedido >= inicio_hist)
        q_all_ibp = db.query(FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('ia'), func.sum(FatoIbpGranular.vol_bottomup).label('bu'), func.sum(FatoIbpGranular.vol_supply).label('sp'), func.sum(FatoIbpGranular.vol_final).label('final')).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)

        if cliente:
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente.strip().upper())
            q_all_ibp = q_all_ibp.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente.strip().upper())
        elif categoria:
            q_hist = q_hist.filter(DimProduto.categoria == categoria.strip())
            q_all_ibp = q_all_ibp.filter(DimProduto.categoria == categoria.strip())
            
        if sku:
            q_hist = q_hist.filter(FatoVendas.sku == sku.strip())
            q_all_ibp = q_all_ibp.filter(FatoIbpGranular.sku == sku.strip())

        hist_dict = {h.mes_ano: int(h.vol or 0) for h in q_hist.group_by('mes_ano').all()}
        all_ibp_res = q_all_ibp.group_by(FatoIbpGranular.ciclo_sop, FatoIbpGranular.mes_projetado).all()

        ibp_map = {}
        for r in all_ibp_res:
            d_iso = str(r.mes_projetado)
            if d_iso not in ibp_map: ibp_map[d_iso] = {}
            ibp_map[d_iso][r.ciclo_sop] = {'ia': int(r.ia or 0), 'bu': int(r.bu or 0), 'sp': int(r.sp or 0), 'final': int(r.final or 0)}

        timeline = []
        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

        for i in range(12, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str, dt_iso = dt.strftime('%Y-%m'), dt.strftime('%Y-%m-%d')
            ciclo_do_mes, ciclo_mes_passado = dt.strftime('%m/%Y'), (dt - relativedelta(months=1)).strftime('%m/%Y')
            
            dados_mes, dados_lag1 = ibp_map.get(dt_iso, {}).get(ciclo_do_mes, {}), ibp_map.get(dt_iso, {}).get(ciclo_mes_passado, {})
            timeline.append({"name": f"{meses_pt[dt.month - 1]}/{dt.strftime('%y')}", "data_iso": dt_iso, "Realizado": hist_dict.get(mes_str, 0), "IA": dados_mes.get('ia', None), "Comercial": None, "Supply": None, "Final": None, "CicloAnterior": dados_lag1.get('final', None), "Orcamento": orc_graf_map.get(dt_iso, None)})

        curr_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        dados_atual_m0 = ibp_map.get(curr_iso, {}).get(ciclo_atual, {})
        timeline.append({"name": f"{meses_pt[mes_atual_inicio.month - 1]}/{mes_atual_inicio.strftime('%y')} (S&OE)", "data_iso": curr_iso, "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), "IA": dados_atual_m0.get('ia', None), "Comercial": None, "Supply": None, "Final": None, "CicloAnterior": ibp_map.get(curr_iso, {}).get(ciclo_ant, {}).get('final', None), "Orcamento": orc_graf_map.get(curr_iso, None)})

        for i in range(1, 6):
            dt = mes_atual_inicio + relativedelta(months=i)
            p_iso = dt.strftime('%Y-%m-%d')
            dados_futuro, dados_futuro_ant = ibp_map.get(p_iso, {}).get(ciclo_atual, {}), ibp_map.get(p_iso, {}).get(ciclo_ant, {})
            is_projection = dt >= m2_date
            timeline.append({"name": f"{meses_pt[dt.month - 1]}/{dt.strftime('%y')}", "data_iso": p_iso, "Realizado": None, "IA": dados_futuro.get('ia', None), "Comercial": dados_futuro.get('bu', None) if is_projection else None, "Supply": dados_futuro.get('sp', None) if is_projection else None, "Final": dados_futuro.get('final', None) if is_projection else None, "CicloAnterior": dados_futuro_ant.get('final', None), "Orcamento": orc_graf_map.get(p_iso, None)})

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/aprovar")
async def aprovar_global(payload: PayloadAprovarGlobal, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    if usuario.get('funcao') not in ['Administrador', 'Diretoria', 'C-Level']:
        raise HTTPException(status_code=403, detail="Apenas Administradores e C-Level podem publicar o Plano Final.")

    try:
        ciclo = get_current_cycle(db)
        
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            sku = ajuste.chave 
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo).filter(FatoIbpGranular.sku == sku)

            linhas = query.all()
            if not linhas: continue

            total_base_antigo = sum([float(l.vol_final or 0) for l in linhas])
            total_base_meta = sum([float(l.vol_meta or 0) for l in linhas])
            
            soma_dist, volume_alvo, total_clientes = 0, int(ajuste.novo_volume), len(linhas)
            
            for i, l in enumerate(linhas):
                if i == total_clientes - 1: 
                    rateado = volume_alvo - soma_dist 
                else:
                    vol_referencia = float(l.vol_meta or 0)
                    peso = vol_referencia / total_base_meta if total_base_meta > 0 else 1.0 / total_clientes
                    rateado = int(round(volume_alvo * peso))
                    soma_dist += rateado
                
                l.vol_final = rateado

            nome_user = usuario.get('nome', usuario.get('email', 'Desconhecido'))
            registrar_log_auditoria(db=db, ciclo=ciclo, origem="S&OP Global (Dashboard Final)", usuario=nome_user, sku=sku, cliente="TODOS_OS_CLIENTES", mes=data_alvo, v_antigo=int(total_base_antigo), v_novo=volume_alvo)

        # CORREÇÃO 3: Proteção ao atualizar tabela de controle de ciclos no banco (Aprovação)
        id_controle = db.execute(text("SELECT id FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'S&OP-Final'"), {"c": ciclo}).scalar()
        if id_controle:
            db.execute(text("UPDATE controle_ciclos SET status = 'Fechado' WHERE id = :id"), {"id": id_controle})
        else:
            db.execute(text("INSERT INTO controle_ciclos (ciclo_sop, origem, status) VALUES (:c, 'S&OP-Final', 'Fechado')"), {"c": ciclo})

        db.commit()
        return {"status": "success", "message": "S&OP Consolidado e Metas travadas com sucesso!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/export")
async def exportar_oficial(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)
        
        # CORREÇÃO 4: Aplicação da nova chamada da Janela para o Excel
        meses_proj = get_projection_window(db, ciclo)
        m2 = parse_date_safe(meses_proj[0])
        m4 = parse_date_safe(meses_proj[-1])
        
        resultados = get_truth_query(db, ciclo, m2, m4).with_entities(
            DimProduto.categoria, FatoIbpGranular.sku, DimProduto.descricao, DimCliente.razaosocial, 
            FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, 
            FatoIbpGranular.vol_bottomup, FatoIbpGranular.vol_supply, FatoIbpGranular.vol_final, 
            FatoIbpGranular.vol_meta, FatoIbpGranular.pmv_aplicado,
            (FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            (FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            (FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            (FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_supply'),
            (FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado).label('rec_final')
        ).all()

        if not resultados: raise HTTPException(404, detail="Sem dados para exportar.")

        dados = [
            {
                "Categoria": r.categoria, "SKU": r.sku, "Produto": r.descricao, "Cliente": r.razaosocial,
                "Mês": r.mes_projetado.strftime("%m/%Y") if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado), 
                "Sinal IA": int(r.vol_ia or 0), "Meta Gerencial": int(r.vol_topdown or 0),
                "Proposta Comercial": int(r.vol_bottomup or 0), "Capacidade Fábrica": int(r.vol_supply or 0),
                "Demanda Irrestrita": int(r.vol_final or 0), "Meta Oficial": int(r.vol_meta or 0),
                "Receita Prevista (R$)": float(r.rec_final or 0)
            } for r in resultados
        ]

        df = pd.DataFrame(dados)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='SOP_Nexus_Oficial')
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=Nexus_Plano_Oficial_{ciclo.replace('/','_')}.xlsx"})
    except Exception as e:
        raise HTTPException(500, repr(e))