import datetime
import math
from typing import List, Optional
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import pandas as pd
import numpy as np

# Ajuste os imports para o caminho exato do seu projeto
from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, ControleCiclo, FatoVendas, DimCliente
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import get_previous_cycle

router = APIRouter(
    prefix="/api/v1/consensus/micro", 
    tags=["Consenso Carteira (Bottom-Up)"]
)

class AjusteCarteira(BaseModel):
    chave: str
    mes_projetado: str
    novo_volume: int 

class PayloadAprovarCarteira(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteCarteira]

# ==========================================
# CORREÇÃO DEFINITIVA: Buscar Sempre o Ciclo Mais Recente na Fato
# Evita ler ciclos "Fantasmas" da tabela de configuração
# ==========================================
def obter_ciclo_real_fato(engine):
    with engine.connect() as conn:
        ciclo = conn.execute(text("SELECT MAX(ciclo_sop) FROM fato_ibp_granular")).scalar()
        return ciclo or "06/2026"

@router.get("/filtros")
async def filtros_micro(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        sql = """
            SELECT DISTINCT 
                TRIM(c.supervisor_nome) AS coordenador, 
                TRIM(f.vendedor_nome) AS vendedor
            FROM fato_ibp_granular f
            INNER JOIN dim_clientes c ON TRIM(f.cgc) = TRIM(c.cgc)
            WHERE f.ciclo_sop = (SELECT MAX(ciclo_sop) FROM fato_ibp_granular)
              AND c.supervisor_nome IS NOT NULL 
              AND f.vendedor_nome IS NOT NULL;
        """
        engine = db.get_bind()
        df = pd.read_sql(text(sql), engine)
        coordenadores = sorted(df['coordenador'].dropna().unique().tolist())
        vendedores = sorted(df['vendedor'].dropna().unique().tolist())
        return {"vendedores": vendedores, "coordenadores": coordenadores}
    except Exception as e:
        return {"vendedores": [], "coordenadores": []}

@router.get("")
def get_dados_carteira(
    nome_responsavel: Optional[str] = Query(None), 
    db: Session = Depends(get_db),
    usuario: dict = Depends(get_current_user)
):
    try:
        engine = db.get_bind()
        ciclo_atual = obter_ciclo_real_fato(engine)

        sql = """
            WITH pmv_historico_4m AS (
                SELECT 
                    TRIM(cgc) AS cgc, TRIM(sku) AS sku, 
                    SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv_real_4m
                FROM fato_vendas
                WHERE data_pedido >= CURRENT_DATE - INTERVAL '4 months'
                GROUP BY TRIM(cgc), TRIM(sku)
            )
            SELECT 
                COALESCE(NULLIF(TRIM(c.supervisor_nome), ''), NULLIF(TRIM(c.gerente_nome), ''), 'SEM COORDENADOR') AS coordenador,
                COALESCE(NULLIF(TRIM(c.vendedor_nome), ''), NULLIF(TRIM(f.vendedor_nome), ''), 'SEM VENDEDOR') AS vendedor,
                COALESCE(NULLIF(TRIM(c.razaosocial), ''), 'SEM RAZAO SOCIAL') AS razao_social,
                TRIM(f.sku) AS sku,
                COALESCE(NULLIF(TRIM(p.categoria), ''), 'SEM CATEGORIA') AS categoria,
                COALESCE(NULLIF(TRIM(p.segmento), ''), 'SEM SEGMENTO') AS segmento,
                TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes_banco,
                COALESCE(f.vol_meta, 0) AS vol_meta,
                COALESCE(hist.pmv_real_4m, f.pmv_aplicado, 0) AS pmv_aplicado
            FROM fato_ibp_granular f
            LEFT JOIN dim_clientes c ON TRIM(f.cgc) = TRIM(c.cgc)
            LEFT JOIN dim_produtos p ON TRIM(f.sku) = TRIM(p.sku)
            LEFT JOIN pmv_historico_4m hist ON hist.cgc = TRIM(f.cgc) AND hist.sku = TRIM(f.sku)
            WHERE f.ciclo_sop = :ciclo_atual
              AND f.sku IS NOT NULL AND TRIM(f.sku) != ''
              AND f.cgc IS NOT NULL AND TRIM(f.cgc) != ''
        """
        
        df = pd.read_sql(text(sql), engine, params={"ciclo_atual": ciclo_atual})

        if df.empty: return {"dados": [], "is_fechado": False, "is_portfolio_fechado": True}

        if nome_responsavel:
            df = df[(df['vendedor'] == nome_responsavel) | (df['coordenador'] == nome_responsavel)]

        df['receita_bruta'] = df['vol_meta'] * df['pmv_aplicado']

        df_grouped = df.groupby(['coordenador', 'vendedor', 'razao_social', 'categoria', 'segmento', 'sku', 'mes_banco']).agg(
            vol_meta=pd.NamedAgg(column='vol_meta', aggfunc='sum'),
            receita_total=pd.NamedAgg(column='receita_bruta', aggfunc='sum')
        ).reset_index()

        df_grouped['pmv_ponderado'] = np.where(df_grouped['vol_meta'] > 0, df_grouped['receita_total'] / df_grouped['vol_meta'], 0)
        df_grouped['rec_meta'] = df_grouped['vol_meta'] * df_grouped['pmv_ponderado']
        df_grouped['vol_sim'] = df_grouped['vol_meta']

        meses_nomes = {'01':'Jan', '02':'Fev', '03':'Mar', '04':'Abr', '05':'Mai', '06':'Jun', '07':'Jul', '08':'Ago', '09':'Set', '10':'Out', '11':'Nov', '12':'Dez'}

        arvore = []
        for coord_name, df_coord in df_grouped.groupby('coordenador'):
            no_coord = {"chave_matriz": f"C|{coord_name}", "nome": coord_name, "tipo": "coordenador", "subRows": []}
            for vend_name, df_vend in df_coord.groupby('vendedor'):
                no_vend = {"chave_matriz": f"V|{coord_name}|{vend_name}", "nome": vend_name, "tipo": "vendedor", "subRows": []}
                for razao_name, df_razao in df_vend.groupby('razao_social'):
                    no_cliente = {"chave_matriz": f"R|{coord_name}|{vend_name}|{razao_name}", "nome": razao_name, "tipo": "cliente", "subRows": []}
                    
                    for sku_name, df_sku in df_razao.groupby('sku'):
                        cat_val = df_sku['categoria'].iloc[0]
                        seg_val = df_sku['segmento'].iloc[0]
                        meses_list = []
                        for _, row in df_sku.iterrows():
                            ano, mes = str(row['mes_banco']).split('-')
                            meses_list.append({
                                "mes_banco": row['mes_banco'], "mes_str": f"{meses_nomes.get(mes, mes)}/{ano[2:]}", 
                                "pmv": float(row['pmv_ponderado']), "rec_meta": float(row['rec_meta']),
                                "vol_meta": float(row['vol_meta']), "vol_sim": float(row['vol_sim'])
                            })
                        
                        no_cliente["subRows"].append({
                            "chave_matriz": f"P|{coord_name}|{vend_name}|{razao_name}|{sku_name}",
                            "nome": sku_name, "produto": sku_name, "tipo": "produto",
                            "categoria": cat_val, "segmento": seg_val,
                            "peso_fat": float(df_sku['receita_total'].sum()), "meses": meses_list
                        })
                    no_vend["subRows"].append(no_cliente)
                no_coord["subRows"].append(no_vend)
            arvore.append(no_coord)

        is_portfolio_fechado = True  
        lock_carteira = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == 'Carteira_BottomUp', ControleCiclo.status == 'Fechado').first()
        is_fechado = True if lock_carteira else False

        return {"dados": arvore, "is_fechado": is_fechado, "is_portfolio_fechado": is_portfolio_fechado}
    except Exception as e:
        print(f"Erro no GET Carteira: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao extrair a matriz comercial.")

@router.post("/congelar")
async def congelar_carteira(payload: PayloadAprovarCarteira, db: Session = Depends(get_db)):
    try:
        engine = db.get_bind()
        ciclo_atual = obter_ciclo_real_fato(engine)

        for ajuste in payload.ajustes:
            partes = ajuste.chave.split('|')
            if len(partes) != 5: continue
            razao_social, sku, mes_banco, novo_volume_total_razao = partes[3], partes[4], ajuste.mes_projetado, int(ajuste.novo_volume)

            query_cgc = text("""
                WITH cgc_historico AS (
                    SELECT f.id AS fato_id, f.cgc, COALESCE(SUM(v.qt_pedido), 0) AS peso_historico
                    FROM fato_ibp_granular f
                    JOIN dim_clientes c ON TRIM(f.cgc) = TRIM(c.cgc)
                    LEFT JOIN fato_vendas v ON TRIM(v.cgc) = TRIM(f.cgc) AND TRIM(v.sku) = TRIM(f.sku) AND v.data_pedido >= CURRENT_DATE - INTERVAL '4 months'
                    WHERE f.ciclo_sop = :ciclo AND TO_CHAR(f.mes_projetado, 'YYYY-MM') = :mes AND TRIM(f.sku) = :sku AND TRIM(c.razaosocial) = :razao
                    GROUP BY f.id, f.cgc
                )
                SELECT fato_id, cgc, peso_historico, SUM(peso_historico) OVER() AS peso_total_razao, COUNT(*) OVER() AS qtd_lojas FROM cgc_historico
            """)

            with engine.connect() as conn:
                result = conn.execute(query_cgc, {"ciclo": ciclo_atual, "mes": mes_banco, "sku": sku, "razao": razao_social}).fetchall()

            if not result: continue

            volume_restante = novo_volume_total_razao
            atualizacoes = []

            for idx, row in enumerate(result):
                fato_id, peso_historico, peso_total_razao, qtd_lojas = row[0], row[2], row[3], row[4]
                if idx == len(result) - 1:
                    vol_cnpj = volume_restante
                else:
                    proporcao = peso_historico / peso_total_razao if peso_total_razao > 0 else 1.0 / qtd_lojas
                    vol_cnpj = round(novo_volume_total_razao * proporcao)
                    volume_restante -= vol_cnpj
                atualizacoes.append({"b_id": fato_id, "b_vol": vol_cnpj})

            if atualizacoes:
                stmt = text("UPDATE fato_ibp_granular SET vol_bottomup = :b_vol WHERE id = :b_id")
                with engine.begin() as conn:
                    for upd in atualizacoes: conn.execute(stmt, upd)

        check_lock = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == 'Carteira_BottomUp').first()
        if not check_lock: db.add(ControleCiclo(ciclo_sop=ciclo_atual, origem='Carteira_BottomUp', status='Fechado'))
        else: check_lock.status = 'Fechado'
        db.commit()

        return {"status": "success", "mensagem": "Convertido em caixas e consolidado nas lojas com sucesso!"}
    except Exception as e:
        db.rollback()
        print(f"Erro no POST Congelar: {e}")
        raise HTTPException(500, detail="Falha no rateio por CNPJ.")

@router.get("/grafico")
def get_grafico_soe(chave_matriz: str, db: Session = Depends(get_db)):
    try:
        engine = db.get_bind()
        ciclo_atual = obter_ciclo_real_fato(engine)
        ciclo_anterior = get_previous_cycle(db) 

        partes = chave_matriz.split('|')
        tipo_no = partes[0]
        
        filtro_fato, filtro_vendas = "", ""
        params = {"ciclo_atual": ciclo_atual, "ciclo_anterior": ciclo_anterior}

        if tipo_no == 'C' and len(partes) >= 2:
            filtro_fato, filtro_vendas, params['coord'] = " AND TRIM(c.supervisor_nome) = :coord", " AND TRIM(c.supervisor_nome) = :coord", partes[1]
        elif tipo_no == 'V' and len(partes) >= 3:
            filtro_fato, filtro_vendas, params['coord'], params['vend'] = " AND TRIM(c.supervisor_nome) = :coord AND TRIM(f.vendedor_nome) = :vend", " AND TRIM(c.supervisor_nome) = :coord AND TRIM(v.vendedor_nome) = :vend", partes[1], partes[2]
        elif tipo_no == 'R' and len(partes) >= 4:
            filtro_fato, filtro_vendas, params['razao'] = " AND TRIM(c.razaosocial) = :razao", " AND TRIM(c.razaosocial) = :razao", partes[3]
        elif tipo_no == 'P' and len(partes) >= 5:
            filtro_fato, filtro_vendas, params['razao'], params['sku'] = " AND TRIM(c.razaosocial) = :razao AND TRIM(f.sku) = :sku", " AND TRIM(c.razaosocial) = :razao AND TRIM(v.sku) = :sku", partes[3], partes[4]

        sql = f"""
            WITH meses AS (
                SELECT TO_CHAR(meses, 'YYYY-MM') AS data_iso, TO_CHAR(meses, 'Mon') AS name
                FROM generate_series(DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '3 months', DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '3 months', INTERVAL '1 month') AS meses
            ),
            realizado AS (
                SELECT TO_CHAR(v.data_pedido, 'YYYY-MM') AS mes, SUM(v.qt_pedido) AS vol_real, SUM(v.vl_pedido) AS rec_real
                FROM fato_vendas v JOIN dim_clientes c ON TRIM(v.cgc) = TRIM(c.cgc) WHERE 1=1 {filtro_vendas} GROUP BY 1
            ),
            consenso_atual AS (
                SELECT TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes, 
                       SUM(f.vol_ia) AS vol_ia, SUM(f.vol_ia * f.pmv_aplicado) AS rec_ia,
                       SUM(f.vol_bottomup) AS vol_consenso, SUM(f.vol_bottomup * f.pmv_aplicado) AS rec_consenso,
                       SUM(f.vol_meta) AS vol_meta, SUM(f.vol_meta * f.pmv_aplicado) AS rec_meta
                FROM fato_ibp_granular f JOIN dim_clientes c ON TRIM(f.cgc) = TRIM(c.cgc) WHERE f.ciclo_sop = :ciclo_atual {filtro_fato} GROUP BY 1
            ),
            consenso_anterior AS (
                SELECT TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes, 
                       SUM(f.vol_final) AS vol_ant, SUM(f.vol_final * f.pmv_aplicado) AS rec_ant
                FROM fato_ibp_granular f JOIN dim_clientes c ON TRIM(f.cgc) = TRIM(c.cgc) WHERE f.ciclo_sop = :ciclo_anterior {filtro_fato} GROUP BY 1
            )
            SELECT 
                m.data_iso, m.name,
                r.vol_real AS "Realizado_CX", r.rec_real AS "Realizado_RS",
                ca.vol_ia AS "IA_CX", ca.rec_ia AS "IA_RS",
                ca.vol_meta AS "MetaBU_CX", ca.rec_meta AS "MetaBU_RS",
                ca.vol_consenso AS "Consenso_CX", ca.rec_consenso AS "Consenso_RS",
                can.vol_ant AS "CicloAnterior_CX", can.rec_ant AS "CicloAnterior_RS"
            FROM meses m
            LEFT JOIN realizado r ON m.data_iso = r.mes
            LEFT JOIN consenso_atual ca ON m.data_iso = ca.mes
            LEFT JOIN consenso_anterior can ON m.data_iso = can.mes
            ORDER BY m.data_iso;
        """
        
        df = pd.read_sql(text(sql), engine, params=params)
        df['name'] = df['name'].map({'Jan':'Jan', 'Feb':'Fev', 'Mar':'Mar', 'Apr':'Abr', 'May':'Mai', 'Jun':'Jun', 'Jul':'Jul', 'Aug':'Ago', 'Sep':'Set', 'Oct':'Out', 'Nov':'Nov', 'Dec':'Dez'}).fillna(df['name'])
        df = df.replace({np.nan: None})
        return {"dados": df.to_dict(orient="records")}
    except Exception as e:
        print(f"Erro no GET Grafico: {e}")
        return {"dados": []}