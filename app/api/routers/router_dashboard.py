from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, FatoVendas
from app.api.routers.router_auth import get_current_user

from app.api.routers.shared_ibp import get_current_cycle, get_previous_cycle, get_projection_window, get_truth_query, parse_date_safe

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
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente', 'Supply Chain']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria, Gerência ou Supply Chain.")
        
    try:
        ciclo = get_current_cycle()
        m2, m4 = get_projection_window()

        query = get_truth_query(db, ciclo, m2, m4).with_entities(
            DimProduto.categoria, FatoIbpGranular.sku, DimProduto.descricao, DimCliente.razaosocial, 
            FatoIbpGranular.mes_projetado, FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, 
            FatoIbpGranular.vol_bottomup, FatoIbpGranular.vol_supply, FatoIbpGranular.vol_final, 
            FatoIbpGranular.vol_meta, FatoIbpGranular.pmv_aplicado,
            (FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('rec_ia'),
            (FatoIbpGranular.vol_topdown * FatoIbpGranular.pmv_aplicado).label('rec_td'),
            (FatoIbpGranular.vol_bottomup * FatoIbpGranular.pmv_aplicado).label('rec_bu'),
            (FatoIbpGranular.vol_supply * FatoIbpGranular.pmv_aplicado).label('rec_supply'),
            (FatoIbpGranular.vol_final * FatoIbpGranular.pmv_aplicado).label('rec_final')
        )

        resultados = query.all()
        
        dados_formatados = []
        for r in resultados:
            dados_formatados.append({
                "categoria": r.categoria,
                "sku": r.sku,
                "descricao": r.descricao,
                "razaosocial": r.razaosocial,
                "mes_projetado": str(r.mes_projetado),
                "vol_ia": int(r.vol_ia or 0),
                "vol_topdown": int(r.vol_topdown or 0),
                "vol_bottomup": int(r.vol_bottomup or 0),
                "vol_supply": int(r.vol_supply or 0),
                "vol_final": int(r.vol_final or 0),
                "vol_meta": int(r.vol_meta or 0),
                "pmv_aplicado": float(r.pmv_aplicado or 0),
                "rec_ia": float(r.rec_ia or 0),
                "rec_td": float(r.rec_td or 0),
                "rec_bu": float(r.rec_bu or 0),
                "rec_supply": float(r.rec_supply or 0),
                "rec_final": float(r.rec_final or 0)
            })
        
        reg = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()
        is_locked = reg.status == 'Fechado' if reg else False
        
        reg_sp = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Supply Review').first()
        if not is_locked and (not reg_sp or reg_sp.status != 'Fechado'):
            return {
                "status": "success", "is_locked": True, 
                "lock_message": "Aguardando encerramento do Supply Review (Fase 3)", "dados": dados_formatados
            }

        return {
            "status": "success", "is_locked": is_locked, 
            "lock_message": "Demanda Irrestrita Publicada" if is_locked else "Plano Aberto para Aprovação Final", 
            "dados": dados_formatados
        }
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.get("/grafico")
async def grafico_global(chave_matriz: str, nivel_hierarquia: str = 'categoria', db: Session = Depends(get_db)):
    try:
        partes = chave_matriz.split('|')
        categoria = partes[0] if len(partes) > 0 else None
        sku = partes[1] if len(partes) > 1 else None
        cliente = partes[2] if len(partes) > 2 else None

        m2_str, _ = get_projection_window()
        m2_date = parse_date_safe(m2_str)
        hoje = datetime.date.today()
        mes_atual_inicio = hoje.replace(day=1)
        ciclo_ant, ciclo_atual = get_previous_cycle(), get_current_cycle()

        q_hist = db.query(func.to_char(FatoVendas.data_pedido, 'YYYY-MM').label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol'))\
                   .join(DimProduto, FatoVendas.sku == DimProduto.sku)\
                   .join(DimCliente, FatoVendas.cgc == DimCliente.cgc)\
                   .filter(FatoVendas.data_pedido >= hoje - relativedelta(years=2))

        q_ant = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_final).label('vol'))\
                  .join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
                  .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                  .filter(FatoIbpGranular.ciclo_sop == ciclo_ant)

        q_proj = db.query(
                    FatoIbpGranular.mes_projetado, 
                    func.sum(FatoIbpGranular.vol_ia).label('ia'), 
                    func.sum(FatoIbpGranular.vol_bottomup).label('bu'),
                    func.sum(FatoIbpGranular.vol_supply).label('sp'),
                    func.sum(FatoIbpGranular.vol_final).label('final')
                   )\
                   .join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
                   .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                   .filter(FatoIbpGranular.ciclo_sop == ciclo_atual)

        if categoria:
            q_hist = q_hist.filter(DimProduto.categoria == categoria.strip())
            q_ant = q_ant.filter(DimProduto.categoria == categoria.strip())
            q_proj = q_proj.filter(DimProduto.categoria == categoria.strip())
        
        if sku:
            q_hist = q_hist.filter(FatoVendas.sku == sku.strip())
            q_ant = q_ant.filter(FatoIbpGranular.sku == sku.strip())
            q_proj = q_proj.filter(FatoIbpGranular.sku == sku.strip())

        if cliente:
            q_hist = q_hist.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente.strip().upper())
            q_ant = q_ant.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente.strip().upper())
            q_proj = q_proj.filter(func.upper(func.trim(DimCliente.razaosocial)) == cliente.strip().upper())

        hist_dict = {h.mes_ano: int(h.vol or 0) for h in q_hist.group_by('mes_ano').all()}
        ant_dict = {str(a.mes_projetado): int(a.vol or 0) for a in q_ant.group_by(FatoIbpGranular.mes_projetado).all()}
        proj_res = q_proj.group_by(FatoIbpGranular.mes_projetado).all()

        timeline = []
        for i in range(24, 0, -1):
            dt = mes_atual_inicio - relativedelta(months=i)
            timeline.append({
                "name": dt.strftime("%b/%y").capitalize(), "data_iso": dt.strftime("%Y-%m-%d"),
                "Realizado": hist_dict.get(dt.strftime('%Y-%m'), 0), 
                "IA": None, "Comercial": None, "Supply": None, "Final": None, "CicloAnterior": None
            })

        curr_iso = mes_atual_inicio.strftime('%Y-%m-%d')
        p_atual = next((p for p in proj_res if str(p.mes_projetado) == curr_iso), None)
        timeline.append({
            "name": hoje.strftime("%b/%y").capitalize() + " (S&OE)", "data_iso": curr_iso,
            "Realizado": hist_dict.get(hoje.strftime('%Y-%m'), 0), 
            "IA": int(p_atual.ia) if p_atual else None, 
            "Comercial": None, "Supply": None, "Final": None,
            "CicloAnterior": ant_dict.get(curr_iso)
        })

        for p in sorted(proj_res, key=lambda x: str(x.mes_projetado)):
            p_date = p.mes_projetado if isinstance(p.mes_projetado, datetime.date) else parse_date_safe(p.mes_projetado)
            if p_date <= mes_atual_inicio: continue
            p_iso = str(p_date)
            
            timeline.append({
                "name": p_date.strftime("%b/%y").capitalize(), "data_iso": p_iso,
                "Realizado": None, 
                "IA": int(p.ia or 0), 
                "Comercial": int(p.bu or 0) if p_date >= m2_date else None, 
                "Supply": int(p.sp or 0) if p_date >= m2_date else None, 
                "Final": int(p.final or 0) if p_date >= m2_date else None, 
                "CicloAnterior": ant_dict.get(p_iso)
            })

        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(500, repr(e))

@router.post("/aprovar")
async def aprovar_global(payload: PayloadAprovarGlobal, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    if usuario['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Apenas a Diretoria pode publicar o Plano Final.")

    try:
        ciclo = get_current_cycle()
        
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

            total_base = sum([float(l.vol_final or 0) for l in linhas])
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
        ciclo = get_current_cycle()
        m2, m4 = get_projection_window()
        
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

        dados = []
        for r in resultados:
            dados.append({
                "Categoria": r.categoria, "SKU": r.sku, "Produto": r.descricao, "Cliente": r.razaosocial,
                "Mês": r.mes_projetado.strftime("%m/%Y") if isinstance(r.mes_projetado, datetime.date) else str(r.mes_projetado), 
                "Sinal IA": int(r.vol_ia or 0),
                "Meta Gerencial": int(r.vol_topdown or 0),
                "Proposta Comercial": int(r.vol_bottomup or 0),
                "Capacidade Fábrica": int(r.vol_supply or 0),
                "Demanda Irrestrita": int(r.vol_final or 0),
                "Meta Oficial": int(r.vol_meta or 0),
                "Receita Prevista (R$)": float(r.rec_final or 0)
            })

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