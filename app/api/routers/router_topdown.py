import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.api.routers.shared_ibp import get_current_cycle, get_projection_window
from datetime import datetime
from dateutil.relativedelta import relativedelta

router = APIRouter(prefix="/api/v1/topdown", tags=["TopDown"])

def obter_ciclo_anterior(ciclo: str) -> str:
    try:
        m, y = ciclo.split('/')
        d = datetime(int(y), int(m), 1) - relativedelta(months=1)
        return d.strftime("%m/%Y")
    except Exception:
        return ciclo

@router.get("/dados")
def get_topdown_dados(db: Session = Depends(get_db)):
    ciclo_atual = get_current_cycle(db)
    ciclo_anterior = obter_ciclo_anterior(ciclo_atual)
    
    # Captura a janela tática correta (M2, M3, M4)
    meses_proj = get_projection_window(db, ciclo_atual)
    m2, m3, m4 = meses_proj[0], meses_proj[1], meses_proj[2]
    
    # Parâmetros de início do histórico (24 meses atrás) e fim da projeção para a linha do tempo do gráfico
    dt_ciclo_base = datetime.strptime(ciclo_atual, "%m/%Y")
    dt_inicio_grafico = (dt_ciclo_base - relativedelta(months=24)).strftime("%Y-%m-%01")
    dt_fim_grafico = m4
    
    status_etapa = db.execute(text("""
        SELECT status FROM controle_ciclos 
        WHERE ciclo_sop = :ciclo AND origem = 'TopDown'
    """), {"ciclo": ciclo_atual}).scalar() or "ABERTO"
    
    query = text("""
        WITH base_skus AS (
            SELECT DISTINCT f.sku, p.descricao 
            FROM fato_ibp_granular f
            JOIN dim_produtos p ON f.sku = p.sku
            WHERE f.ciclo_sop = :ciclo_atual
        ),
        -- Eixo temporal completo para evitar cortes nos gráficos das pontas
        eixo_tempo AS (
            SELECT DISTINCT DATE_TRUNC('month', d)::DATE as mes
            FROM generate_series(CAST(:inicio_grafico AS DATE), CAST(:fim_grafico AS DATE), '1 month'::interval) d
        ),
        dados_grid AS (
            SELECT 
                f.sku,
                f.mes_projetado,
                SUM(f.vol_topdown) as vol_topdown,
                COALESCE(SUM(f.vol_ia * f.pmv_aplicado) / NULLIF(SUM(f.vol_ia), 0), AVG(f.pmv_aplicado)) as pmv,
                SUM(COALESCE(o.receita_orcamento, 0)) as rec_orcada
            FROM fato_ibp_granular f
            LEFT JOIN fato_orcamento o ON f.sku = o.sku AND f.mes_projetado = o.mes_projetado
            WHERE f.ciclo_sop = :ciclo_atual 
              AND f.mes_projetado IN (:m2, :m3, :m4)
            GROUP BY f.sku, f.mes_projetado
        ),
        historico AS (
            SELECT sku, DATE_TRUNC('month', data_pedido)::DATE as mes, SUM(qt_pedido) as vol_real
            FROM fato_vendas
            WHERE data_pedido >= CAST(:inicio_grafico AS DATE)
            GROUP BY sku, DATE_TRUNC('month', data_pedido)::DATE
        ),
        lag1 AS (
            SELECT sku, mes_projetado as mes, SUM(vol_final) as vol_lag1
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo_anterior
            GROUP BY sku, mes_projetado
        ),
        ia_hist AS (
            SELECT sku, mes_projetado as mes, SUM(vol_ia) as vol_ia
            FROM fato_ibp_granular
            WHERE 
                (mes_projetado >= :m2 AND ciclo_sop = :ciclo_atual) OR
                (mes_projetado <= '2026-06-01' AND ciclo_sop = '04/2026') OR
                (mes_projetado > '2026-06-01' AND mes_projetado < :m2 
                 AND ciclo_sop = TO_CHAR(mes_projetado - INTERVAL '2 month', 'MM/YYYY'))
            GROUP BY sku, mes_projetado
        ),
        topdown_curva AS (
            SELECT sku, mes_projetado as mes, SUM(vol_topdown) as vol_td_hist
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo_atual
            GROUP BY sku, mes_projetado
        )
        SELECT 
            b.sku, 
            b.descricao,
            t.mes,
            g.vol_topdown,
            g.pmv,
            g.rec_orcada,
            h.vol_real,
            l.vol_lag1,
            ia.vol_ia,
            tc.vol_td_hist
        FROM base_skus b
        CROSS JOIN eixo_tempo t
        LEFT JOIN dados_grid g ON b.sku = g.sku AND t.mes = g.mes_projetado
        LEFT JOIN historico h ON b.sku = h.sku AND t.mes = h.mes
        LEFT JOIN lag1 l ON b.sku = l.sku AND t.mes = l.mes
        LEFT JOIN ia_hist ia ON b.sku = ia.sku AND t.mes = ia.mes
        LEFT JOIN topdown_curva tc ON b.sku = tc.sku AND t.mes = tc.mes
        ORDER BY b.sku, t.mes
    """)
    
    result = db.execute(query, {
        "ciclo_atual": ciclo_atual, "ciclo_anterior": ciclo_anterior,
        "m2": m2, "m3": m3, "m4": m4,
        "inicio_grafico": dt_inicio_grafico, "fim_grafico": dt_fim_grafico
    }).fetchall()
    
    df = pd.DataFrame(result, columns=["sku", "descricao", "mes", "vol_topdown", "pmv", "rec_orcada", "vol_real", "vol_lag1", "vol_ia", "vol_td_hist"])
    df['mes'] = pd.to_datetime(df['mes'])
    
    output = []
    for (sku, desc), group in df.groupby(['sku', 'descricao']):
        grid_df = group[group['mes'].isin([pd.to_datetime(m2), pd.to_datetime(m3), pd.to_datetime(m4)])]
        
        meses_grid = []
        for mes_target in [m2, m3, m4]:
            row = grid_df[grid_df['mes'] == pd.to_datetime(mes_target)]
            if not row.empty:
                r = row.iloc[0]
                vol_td = float(r['vol_topdown'] or 0)
                pmv = float(r['pmv'] or 0)
                rec_orc = float(r['rec_orcada'] or 0)
            else:
                vol_td, pmv, rec_orc = 0.0, 0.0, 0.0
                
            rec_td = vol_td * pmv
            var_perc = ((rec_td - rec_orc) / rec_orc) if rec_orc > 0 else 0.0
            
            meses_grid.append({
                "mes_banco": str(mes_target),
                "mes_str": pd.to_datetime(mes_target).strftime('%b/%y').capitalize(),
                "vol_topdown": vol_td,
                "pmv": pmv,
                "rec_topdown": rec_td,
                "rec_orcada": rec_orc,
                "variacao": var_perc
            })
            
        grafico_df = group.sort_values('mes')
        
        output.append({
            "sku": sku,
            "descricao": desc,
            "meses": meses_grid,
            "grafico": {
                "labels": [m.strftime('%b/%y').capitalize() for m in grafico_df['mes']],
                "realizado": [float(v) if pd.notnull(v) else None for v in grafico_df['vol_real']],
                "ia": [float(v) if pd.notnull(v) else None for v in grafico_df['vol_ia']],
                "lag1": [float(v) if pd.notnull(v) else None for v in grafico_df['vol_lag1']],
                "topdown": [float(v) if pd.notnull(v) else None for v in grafico_df['vol_td_hist']]
            }
        })
        
    return {
        "ciclo_ativo": ciclo_atual,
        "status_etapa": status_etapa,
        "dados": sorted(output, key=lambda x: x["descricao"])
    }

@router.post("/salvar")
def topdown_salvar(payload: dict, db: Session = Depends(get_db)):
    ciclo_atual = get_current_cycle(db)
    alteracoes = payload.get("alteracoes", [])
    
    for alt in alteracoes:
        sku = alt.get("sku")
        mes_banco = alt.get("mes_banco")
        novo_vol = float(alt.get("novo_vol", 0))
        
        linhas = db.execute(text("""
            SELECT id, vol_topdown, vol_ia 
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo AND sku = :sku AND mes_projetado = :mes
        """), {"ciclo": ciclo_atual, "sku": sku, "mes": mes_banco}).fetchall()
        
        if not linhas:
            continue
            
        vol_total_atual = sum(float(l.vol_topdown or 0) for l in linhas)
        vol_ia_total = sum(float(l.vol_ia or 0) for l in linhas)
        
        for l in linhas:
            if vol_total_atual > 0:
                peso = float(l.vol_topdown or 0) / vol_total_atual
            elif vol_ia_total > 0:
                peso = float(l.vol_ia or 0) / vol_ia_total
            else:
                peso = 1.0 / len(linhas)
                
            fatia = novo_vol * peso
            db.execute(text("""
                UPDATE fato_ibp_granular SET vol_topdown = :val WHERE id = :id_linha
            """), {"val": fatia, "id_linha": l.id})
            
    db.commit()
    return {"status": "sucesso", "mensagem": "Rascunho do Top-Down salvo com sucesso."}

@router.post("/congelar")
def topdown_congelar(db: Session = Depends(get_db)):
    ciclo_atual = get_current_cycle(db)
    
    db.execute(text("""
        UPDATE fato_ibp_granular
        SET 
            vol_supply = vol_topdown,
            vol_final = vol_topdown,
            vol_meta = vol_topdown,
            vol_bottomup = vol_topdown
        WHERE ciclo_sop = :ciclo
    """), {"ciclo": ciclo_atual})
    
    # Busca usando a tabela correta 'controle_ciclos' e coluna 'origem'
    id_controle = db.execute(text("""
        SELECT id FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'TopDown'
    """), {"ciclo": ciclo_atual}).scalar()
    
    if id_controle:
        db.execute(text("""
            UPDATE controle_ciclos SET status = 'CONGELADO' WHERE id = :id_ctrl
        """), {"id_ctrl": id_controle})
    else:
        db.execute(text("""
            INSERT INTO controle_ciclos (ciclo_sop, origem, status) 
            VALUES (:ciclo, 'TopDown', 'CONGELADO')
        """), {"ciclo": ciclo_atual})
        
    db.commit()
    return {"status": "sucesso", "mensagem": "Etapa Top-Down CONGELADA."}