from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import (
    get_current_cycle, 
    get_previous_cycle, 
    get_projection_window, 
    get_truth_query, 
    parse_date_safe,
    registrar_log_auditoria
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["S&OP Global Dashboard"])

class AjusteGlobal(BaseModel):
    nivel: str
    chave: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGlobal(BaseModel):
    ajustes: List[AjusteGlobal]

# =====================================================================
# FASE 1: O SUPER MOTOR S&OP (ENRIQUECIDO PARA LLM E DASHBOARDS)
# =====================================================================

@router.get("/global")
async def carregar_dashboard_global(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    perfis_permitidos = ['Administrador', 'Gerente', 'Supply Chain', 'Marketing', 'C-Level', 'Diretoria']
    if usuario_logado.get('funcao') not in perfis_permitidos:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria, Gerência ou Supply Chain.")
        
    try:
        ciclo_atual = get_current_cycle(db)
        ciclo_anterior = get_previous_cycle(db)
        m2_str, m4_str = get_projection_window(db)
        m2_date = parse_date_safe(m2_str)
        
        # 1. BASE HISTÓRICA (M-3 a M-1) - Para Crescimento e Variação de PMV
        inicio_hist = m2_date - relativedelta(months=3)
        hist_query = db.query(
            FatoVendas.sku,
            FatoVendas.cgc,
            func.sum(FatoVendas.qt_pedido).label('vol_total_3m'),
            func.sum(FatoVendas.vl_pedido).label('rec_total_3m')
        ).filter(
            FatoVendas.data_pedido >= inicio_hist,
            FatoVendas.data_pedido < m2_date
        ).group_by(FatoVendas.sku, FatoVendas.cgc).all()

        hist_map = {}
        for h in hist_query:
            vol_medio = float(h.vol_total_3m or 0) / 3.0
            rec_media = float(h.rec_total_3m or 0) / 3.0
            pmv_medio = rec_media / vol_medio if vol_medio > 0 else 0
            hist_map[f"{h.sku}|{h.cgc}"] = {
                "vol_hist_media": vol_medio,
                "pmv_hist_media": pmv_medio
            }

        # 1.1 ASSERTIVIDADE IA (M-1) - Vol IA do Ciclo Anterior vs Venda Real
        mes_passado = m2_date - relativedelta(months=1)
        ia_m1_query = db.query(
            FatoIbpGranular.sku, FatoIbpGranular.cgc, func.sum(FatoIbpGranular.vol_ia).label('vol_ia_m1')
        ).filter(
            FatoIbpGranular.ciclo_sop == ciclo_anterior,
            FatoIbpGranular.mes_projetado == mes_passado
        ).group_by(FatoIbpGranular.sku, FatoIbpGranular.cgc).all()
        
        vendas_m1_query = db.query(
            FatoVendas.sku, FatoVendas.cgc, func.sum(FatoVendas.qt_pedido).label('vol_real_m1')
        ).filter(
            FatoVendas.data_pedido >= mes_passado,
            FatoVendas.data_pedido < m2_date
        ).group_by(FatoVendas.sku, FatoVendas.cgc).all()

        assertividade_map = {}
        vendas_m1_dict = {f"{v.sku}|{v.cgc}": float(v.vol_real_m1 or 0) for v in vendas_m1_query}
        for ia in ia_m1_query:
            chave = f"{ia.sku}|{ia.cgc}"
            v_real = vendas_m1_dict.get(chave, 0)
            v_ia = float(ia.vol_ia_m1 or 0)
            if v_real > 0 and v_ia > 0:
                erro_abs = abs(v_ia - v_real)
                acc = max(0, 100 - ((erro_abs / v_real) * 100))
            else:
                acc = 0 if v_real > 0 else 100 if v_ia == 0 else 0
            assertividade_map[chave] = acc

        # 2. SOMBRA DO CICLO ANTERIOR - Para medir Volatilidade (Nervosismo do Plano)
        prev_query = db.query(
            FatoIbpGranular.sku, FatoIbpGranular.cgc, FatoIbpGranular.mes_projetado,
            FatoIbpGranular.vol_final, FatoIbpGranular.pmv_aplicado
        ).filter(
            FatoIbpGranular.ciclo_sop == ciclo_anterior,
            FatoIbpGranular.mes_projetado >= m2_date,
            FatoIbpGranular.mes_projetado <= parse_date_safe(m4_str)
        ).all()

        prev_map = {}
        for p in prev_query:
            mes_str = str(p.mes_projetado)
            prev_map[f"{p.sku}|{p.cgc}|{mes_str}"] = {
                "vol_anterior": float(p.vol_final or 0),
                "pmv_anterior": float(p.pmv_aplicado or 0)
            }

        # 3. EXTRAÇÃO DO CICLO ATUAL (Incluindo justificativa de Supply)
        query = get_truth_query(db, ciclo_atual, m2_str, m4_str).with_entities(
            DimProduto.categoria, FatoIbpGranular.sku, DimProduto.descricao, DimCliente.razaosocial, DimCliente.cgc,
            FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, 
            FatoIbpGranular.vol_bottomup, FatoIbpGranular.vol_supply, FatoIbpGranular.vol_final, 
            FatoIbpGranular.vol_meta, FatoIbpGranular.pmv_aplicado,
            
            FatoIbpGranular.justificativa_supply,
            
            (FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            (FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            (FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            (FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_supply'),
            (FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado).label('rec_final')
        )

        resultados = query.all()
        dados_enriquecidos = []
        
        # 4. ENRIQUECIMENTO E CÁLCULOS MATEMÁTICOS PARA A LLM / FRONTEND
        for r in resultados:
            cgc = r.cgc
            sku = r.sku
            mes_str = str(r.mes_projetado)
            chave_hist = f"{sku}|{cgc}"
            chave_prev = f"{sku}|{cgc}|{mes_str}"

            vol_ia = float(r.vol_ia or 0)
            vol_final = float(r.vol_final or 0)
            vol_bu = float(r.vol_bottomup or 0)
            vol_sp = float(r.vol_supply or 0)
            pmv_atual = float(r.pmv_aplicado or 0)
            
            historico = hist_map.get(chave_hist, {"vol_hist_media": 0, "pmv_hist_media": 0})
            anterior = prev_map.get(chave_prev, {"vol_anterior": vol_final, "pmv_anterior": pmv_atual})
            assertividade = assertividade_map.get(chave_hist, 0.0)

            corte_supply_vol = max(0, vol_bu - vol_sp)
            unmet_demand_brl = corte_supply_vol * pmv_atual
            
            delta_ciclo_vol = vol_final - anterior["vol_anterior"]
            
            pmv_hist = historico["pmv_hist_media"]
            var_pmv_pct = ((pmv_atual / pmv_hist) - 1) * 100 if pmv_hist > 0 else 0
            
            vol_hist = historico["vol_hist_media"]
            crescimento_hist_pct = ((vol_final / vol_hist) - 1) * 100 if vol_hist > 0 else 100 if vol_final > 0 else 0

            delta_ia_comercial_vol = vol_ia - vol_bu
            impacto_financeiro_ia_brl = delta_ia_comercial_vol * pmv_atual

            dados_enriquecidos.append({
                "categoria": r.categoria,
                "sku": sku,
                "descricao": r.descricao,
                "cgc": cgc,
                "razaosocial": r.razaosocial,
                "mes_projetado": mes_str,
                
                "vol_ia": int(vol_ia),
                "vol_topdown": int(r.vol_topdown or 0),
                "vol_bottomup": int(vol_bu),
                "vol_supply": int(vol_sp),
                "vol_final": int(vol_final),
                "vol_meta": int(r.vol_meta or 0),
                
                "pmv_aplicado": pmv_atual,
                "rec_ia": float(r.rec_ia or 0),
                "rec_td": float(r.rec_td or 0),
                "rec_bu": float(r.rec_bu or 0),
                "rec_supply": float(r.rec_supply or 0),
                "rec_final": float(r.rec_final or 0),

                "justificativa_supply": getattr(r, 'justificativa_supply', '') or "",
                
                "delta_ia_comercial_vol": int(delta_ia_comercial_vol),
                "impacto_financeiro_ia_brl": round(impacto_financeiro_ia_brl, 2),
                
                "vol_hist_media": float(vol_hist),
                "pmv_hist_media": float(pmv_hist),
                "crescimento_hist_pct": round(crescimento_hist_pct, 2),
                "assertividade_ia": round(assertividade, 1),
                
                "vol_anterior": int(anterior["vol_anterior"]),
                "delta_ciclo_vol": int(delta_ciclo_vol),
                
                "corte_supply_vol": int(corte_supply_vol),
                "unmet_demand_brl": round(unmet_demand_brl, 2),
                
                "var_pmv_pct": round(var_pmv_pct, 2)
            })
        
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == 'S&OP-Final').first()
        is_locked = reg.status == 'Fechado' if reg else False
        
        reg_sp = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == 'Supply Review').first()
        
        if not is_locked and (not reg_sp or reg_sp.status != 'Fechado'):
            return {"status": "success", "is_locked": True, "lock_message": "Aguardando encerramento do Supply Review (Fase 3)", "dados": dados_enriquecidos}

        return {"status": "success", "is_locked": is_locked, "lock_message": "Demanda Irrestrita Publicada" if is_locked else "Plano Aberto para Aprovação Final", "dados": dados_enriquecidos}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_global(chave_matriz: str, nivel_hierarquia: str = 'categoria', db: Session = Depends(get_db)):
    """Constrói a linha do tempo mesclando Histórico Real e Projeções (S&OE)"""
    try:
        partes = chave_matriz.split('|')
        categoria = None
        sku = None
        cliente = None
        
        # Correção da leitura da chave para Clientes não aparecerem zerados
        if nivel_hierarquia == 'cliente':
            cliente = partes[0]
            if len(partes) > 1:
                sku = partes[1]
        else:
            categoria = partes[0]
            if len(partes) > 1:
                sku = partes[1]

        m2_str, _ = get_projection_window(db)
        m2_date = parse_date_safe(m2_str)
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_ant, ciclo_atual = get_previous_cycle(db), get_current_cycle(db)

        # 1. Histórico Real de Vendas
        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol'))\
                   .join(DimProduto, FatoVendas.sku == DimProduto.sku)\
                   .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                   .filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))

        # 2. Histórico Mestre IBP
        q_all_ibp = db.query(
            FatoIbpGranular.ciclo_sop,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('ia'),
            func.sum(FatoIbpGranular.vol_bottomup).label('bu'),
            func.sum(FatoIbpGranular.vol_supply).label('sp'),
            func.sum(FatoIbpGranular.vol_final).label('final')
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)

        # Filtros Dinâmicos
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
            if d_iso not in ibp_map: 
                ibp_map[d_iso] = {}
            ibp_map[d_iso][r.ciclo_sop] = {
                'ia': int(r.ia or 0), 'bu': int(r.bu or 0), 'sp': int(r.sp or 0), 'final': int(r.final or 0)
            }

        timeline = []
        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

        # 3. Construção do Passado (S&OE)
        for i in range(12, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            mes_str = dt.strftime('%Y-%m')
            dt_iso = dt.strftime('%Y-%m-%d')
            ciclo_do_mes = dt.strftime('%m/%Y')
            ciclo_mes_passado = (dt - relativedelta(months=1)).strftime('%m/%Y')
            
            dados_mes = ibp_map.get(dt_iso, {}).get(ciclo_do_mes, {})
            dados_lag1 = ibp_map.get(dt_iso, {}).get(ciclo_mes_passado, {})
            
            # Formatação padronizada "Jan/26"
            mes_label = f"{meses_pt[dt.month - 1]}/{dt.strftime('%y')}"
            
            timeline.append({
                "name": mes_label,
                "data_iso": dt_iso,
                "Realizado": hist_dict.get(mes_str, 0), 
                "IA": dados_mes.get('ia', None),
                "Comercial": None, 
                "Supply": None, 
                "Final": None, 
                "CicloAnterior": dados_lag1.get('final', None)
            })

        # 4. Presente (M0)
        curr_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        dados_atual_m0 = ibp_map.get(curr_iso, {}).get(ciclo_atual, {})
        mes_label_m0 = f"{meses_pt[mes_atual_inicio.month - 1]}/{mes_atual_inicio.strftime('%y')} (S&OE)"
        
        timeline.append({
            "name": mes_label_m0, "data_iso": curr_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": dados_atual_m0.get('ia', None), 
            "Comercial": None, "Supply": None, "Final": None,
            "CicloAnterior": ibp_map.get(curr_iso, {}).get(ciclo_ant, {}).get('final', None)
        })

        # 5. Futuro (Projeção e Sombra do Ciclo Anterior até M4)
        for i in range(1, 6):
            dt = mes_atual_inicio + relativedelta(months=i)
            p_iso = dt.strftime('%Y-%m-%d')
            dados_futuro = ibp_map.get(p_iso, {}).get(ciclo_atual, {})
            dados_futuro_ant = ibp_map.get(p_iso, {}).get(ciclo_ant, {})
            
            is_projection = dt >= m2_date
            mes_label_futuro = f"{meses_pt[dt.month - 1]}/{dt.strftime('%y')}"
            
            timeline.append({
                "name": mes_label_futuro, "data_iso": p_iso,
                "Realizado": None, 
                "IA": dados_futuro.get('ia', None), 
                "Comercial": dados_futuro.get('bu', None) if is_projection else None, 
                "Supply": dados_futuro.get('sp', None) if is_projection else None, 
                "Final": dados_futuro.get('final', None) if is_projection else None, 
                "CicloAnterior": dados_futuro_ant.get('final', None)
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/aprovar")
async def aprovar_global(payload: PayloadAprovarGlobal, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Aprova o plano S&OP e aplica o Rateio (Top-Down) aos ajustes realizados no dashboard."""
    if usuario.get('funcao') not in ['Administrador', 'Diretoria', 'C-Level']:
        raise HTTPException(status_code=403, detail="Apenas Administradores e C-Level podem publicar o Plano Final.")

    try:
        ciclo = get_current_cycle(db)
        
        for ajuste in payload.ajustes:
            data_alvo = parse_date_safe(ajuste.mes_projetado)
            partes = ajuste.chave.split('|')
            
            query = get_truth_query(db, ciclo, data_alvo, data_alvo)

            if ajuste.nivel == 'cliente':
                query = query.filter(FatoIbpGranular.sku == partes[1], DimCliente.razaosocial == partes[2])
            else:
                query = query.filter(FatoIbpGranular.sku == partes[1])

            linhas = query.all()
            if not linhas: continue

            total_base_antigo = sum([float(l.vol_final or 0) for l in linhas])
            total_base = total_base_antigo
            
            soma_dist = 0
            volume_alvo = int(ajuste.novo_volume)
            
            for i, l in enumerate(linhas):
                if i == len(linhas) - 1:
                    rateado = volume_alvo - soma_dist 
                else:
                    peso = float(l.vol_final or 0) / total_base if total_base > 0 else 1.0 / len(linhas)
                    rateado = int(round(volume_alvo * peso))
                    soma_dist += rateado
                
                l.vol_final = rateado
                l.vol_meta = rateado

            nome_user = usuario.get('nome', usuario.get('email', 'Desconhecido'))
            sku_log = partes[1] if len(partes) > 1 else "MULTIPLOS_SKUS"
            cliente_log = partes[2] if len(partes) > 2 else "TODOS_OS_CLIENTES"

            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="S&OP Global (Dashboard Final)",
                usuario=nome_user, sku=sku_log, cliente=cliente_log,
                mes=data_alvo, v_antigo=int(total_base_antigo), v_novo=ajuste.novo_volume
            )

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()
        if not registro:
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='S&OP-Final', status='Fechado'))
        else:
            registro.status = 'Fechado'

        db.query(FatoIbpGranular).filter(FatoIbpGranular.ciclo_sop == ciclo).update({
            FatoIbpGranular.vol_meta: FatoIbpGranular.vol_final
        }, synchronize_session=False)

        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))

@router.get("/export")
async def exportar_oficial(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        ciclo = get_current_cycle(db)
        m2, m4 = get_projection_window(db)
        
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

        if not resultados:
            raise HTTPException(404, detail="Sem dados para exportar.")

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
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='SOP_Nexus_Oficial')
        
        buffer.seek(0)
        return StreamingResponse(
            buffer, 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=Nexus_Plano_Oficial_{ciclo.replace('/','_')}.xlsx"}
        )
    except Exception as e:
        raise HTTPException(500, repr(e))