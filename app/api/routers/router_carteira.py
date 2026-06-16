import datetime
import io
import csv
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np

from app.core.database import get_db
from app.models.domain_models import ControleCiclo
from app.api.routers.router_auth import get_current_user

router = APIRouter(
    prefix="/api/v1/consensus/micro", 
    tags=["Metas da Equipe (Cascata)"]
)

class AjusteMeta(BaseModel):
    chave: str
    mes_projetado: str
    novo_volume: int 

class PayloadSalvarMetas(BaseModel):
    origem_ajuste: str
    finalizar_etapa: bool # Se True, trava o cadeado global. Se False, é só Rascunho.
    ajustes: List[AjusteMeta]

def obter_ciclo_real_fato(engine):
    with engine.connect() as conn:
        ciclo = conn.execute(text("SELECT MAX(ciclo_sop) FROM fato_ibp_granular")).scalar()
        return ciclo or "06/2026"

@router.get("")
def get_dados_metas(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    try:
        engine = db.get_bind()
        ciclo_atual = obter_ciclo_real_fato(engine)

        # SQL OTIMIZADO AWS: Traz a Árvore de Pessoas (Gerente > Coord > Vend > RS > SKU)
        # Lê o vol_bu (herança da fase anterior) para servir de base.
        sql = """
            WITH pmv_historico_4m AS (
                SELECT cgc, sku, SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv_real_4m
                FROM fato_vendas
                WHERE data_pedido >= CURRENT_DATE - INTERVAL '4 months'
                GROUP BY cgc, sku
            )
            SELECT 
                COALESCE(NULLIF(c.gerente_nome, ''), 'SEM GERENTE') AS gerente,
                COALESCE(NULLIF(c.supervisor_nome, ''), 'SEM COORDENADOR') AS coordenador,
                COALESCE(NULLIF(f.vendedor_nome, ''), 'SEM VENDEDOR') AS vendedor,
                COALESCE(NULLIF(c.razaosocial, ''), 'SEM RAZAO SOCIAL') AS razao_social,
                f.sku,
                COALESCE(NULLIF(p.categoria, ''), 'SEM CATEGORIA') AS categoria,
                COALESCE(NULLIF(p.segmento, ''), 'SEM SEGMENTO') AS segmento,
                TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes_banco,
                COALESCE(f.vol_bu, 0) AS vol_base_herdado,
                COALESCE(hist.pmv_real_4m, f.pmv_aplicado, 0) AS pmv_aplicado
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            JOIN dim_produtos p ON f.sku = p.sku
            LEFT JOIN pmv_historico_4m hist ON hist.cgc = f.cgc AND hist.sku = f.sku
            WHERE f.ciclo_sop = :ciclo_atual
              AND f.sku IS NOT NULL AND f.sku != ''
        """
        
        df = pd.read_sql(text(sql), engine, params={"ciclo_atual": ciclo_atual})
        if df.empty: return {"dados": [], "is_fechado": False}

        df['vol_base_herdado'] = pd.to_numeric(df['vol_base_herdado'], errors='coerce').fillna(0)
        df['receita_base'] = df['vol_base_herdado'] * df['pmv_aplicado']

        df_grouped = df.groupby(['gerente', 'coordenador', 'vendedor', 'razao_social', 'categoria', 'segmento', 'sku', 'mes_banco']).agg(
            vol_base_herdado=pd.NamedAgg(column='vol_base_herdado', aggfunc='sum'),
            receita_total=pd.NamedAgg(column='receita_base', aggfunc='sum')
        ).reset_index()

        df_grouped['pmv_ponderado'] = np.where(df_grouped['vol_base_herdado'] > 0, df_grouped['receita_total'] / df_grouped['vol_base_herdado'], 0)
        df_grouped.fillna(0, inplace=True)

        meses_nomes = {'01':'Jan', '02':'Fev', '03':'Mar', '04':'Abr', '05':'Mai', '06':'Jun', '07':'Jul', '08':'Ago', '09':'Set', '10':'Out', '11':'Nov', '12':'Dez'}

        arvore = []
        for ger_name, df_ger in df_grouped.groupby('gerente'):
            no_gerente = {"chave_matriz": f"G|{ger_name}", "nome": str(ger_name), "tipo": "gerente", "subRows": []}
            
            for coord_name, df_coord in df_ger.groupby('coordenador'):
                no_coord = {"chave_matriz": f"C|{ger_name}|{coord_name}", "nome": str(coord_name), "tipo": "coordenador", "subRows": []}
                
                for vend_name, df_vend in df_coord.groupby('vendedor'):
                    no_vend = {"chave_matriz": f"V|{ger_name}|{coord_name}|{vend_name}", "nome": str(vend_name), "tipo": "vendedor", "subRows": []}
                    
                    for razao_name, df_razao in df_vend.groupby('razao_social'):
                        no_cliente = {"chave_matriz": f"R|{ger_name}|{coord_name}|{vend_name}|{razao_name}", "nome": str(razao_name), "tipo": "cliente", "subRows": []}
                        
                        for sku_name, df_sku in df_razao.groupby('sku'):
                            meses_list = []
                            for _, row in df_sku.iterrows():
                                ano, mes = str(row['mes_banco']).split('-')
                                meses_list.append({
                                    "mes_banco": str(row['mes_banco']), 
                                    "mes_str": f"{meses_nomes.get(mes, mes)}/{ano[2:]}", 
                                    "pmv": float(row['pmv_ponderado']), 
                                    "rec_base": float(row['receita_total']),
                                    "vol_base": float(row['vol_base_herdado']), 
                                    "vol_meta": float(row['vol_base_herdado']) # Simulado começa igual à base
                                })
                            
                            no_cliente["subRows"].append({
                                "chave_matriz": f"P|{ger_name}|{coord_name}|{vend_name}|{razao_name}|{sku_name}",
                                "nome": str(sku_name), "produto": str(sku_name), "tipo": "produto",
                                "categoria": str(df_sku['categoria'].iloc[0]), "segmento": str(df_sku['segmento'].iloc[0]),
                                "peso_fat": float(df_sku['receita_total'].sum()), "meses": meses_list
                            })
                        no_vend["subRows"].append(no_cliente)
                    no_coord["subRows"].append(no_vend)
                no_gerente["subRows"].append(no_coord)
            arvore.append(no_gerente)

        lock_db = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == 'Metas_Equipe', ControleCiclo.status == 'Fechado').first()
        return {"dados": arvore, "is_fechado": True if lock_db else False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")

@router.post("/salvar")
async def salvar_metas(payload: PayloadSalvarMetas, db: Session = Depends(get_db)):
    try:
        engine = db.get_bind()
        ciclo_atual = obter_ciclo_real_fato(engine)

        for ajuste in payload.ajustes:
            partes = ajuste.chave.split('|')
            if len(partes) != 6: continue
            razao_social, sku, mes_banco, novo_volume = partes[4], partes[5], ajuste.mes_projetado, int(ajuste.novo_volume)

            # RATEIO EXPLOSÃO CNPJ
            query_cgc = text("""
                WITH cgc_historico AS (
                    SELECT f.id AS fato_id, f.cgc, COALESCE(SUM(v.qt_pedido), 0) AS peso_historico
                    FROM fato_ibp_granular f
                    JOIN dim_clientes c ON f.cgc = c.cgc
                    LEFT JOIN fato_vendas v ON v.cgc = f.cgc AND v.sku = f.sku AND v.data_pedido >= CURRENT_DATE - INTERVAL '4 months'
                    WHERE f.ciclo_sop = :ciclo AND TO_CHAR(f.mes_projetado, 'YYYY-MM') = :mes AND f.sku = :sku AND c.razaosocial = :razao
                    GROUP BY f.id, f.cgc
                )
                SELECT fato_id, cgc, peso_historico, SUM(peso_historico) OVER() AS peso_total_razao, COUNT(*) OVER() AS qtd_lojas FROM cgc_historico
            """)

            with engine.connect() as conn:
                result = conn.execute(query_cgc, {"ciclo": ciclo_atual, "mes": mes_banco, "sku": sku, "razao": razao_social}).fetchall()

            if not result: continue

            volume_restante = novo_volume
            atualizacoes = []

            for idx, row in enumerate(result):
                fato_id, peso_historico, peso_total_razao, qtd_lojas = row[0], row[2], row[3], row[4]
                if idx == len(result) - 1:
                    vol_cnpj = volume_restante
                else:
                    proporcao = peso_historico / peso_total_razao if peso_total_razao > 0 else 1.0 / qtd_lojas
                    vol_cnpj = round(novo_volume * proporcao)
                    volume_restante -= vol_cnpj
                atualizacoes.append({"b_id": fato_id, "b_vol": vol_cnpj})

            if atualizacoes:
                # GRAVAÇÃO OFICIAL NO VOL_META
                stmt = text("UPDATE fato_ibp_granular SET vol_meta = :b_vol WHERE id = :b_id")
                with engine.begin() as conn:
                    for upd in atualizacoes: conn.execute(stmt, upd)

        if payload.finalizar_etapa:
            check_lock = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == 'Metas_Equipe').first()
            if not check_lock: db.add(ControleCiclo(ciclo_sop=ciclo_atual, origem='Metas_Equipe', status='Fechado'))
            else: check_lock.status = 'Fechado'
            
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, detail="Falha ao gravar metas.")

# ==========================================
# EXPORTAÇÃO CSV 
# ==========================================
@router.get("/exportar")
def exportar_csv(db: Session = Depends(get_db)):
    try:
        engine = db.get_bind()
        ciclo_atual = obter_ciclo_real_fato(engine)

        sql = """
            WITH pmv_historico_4m AS (
                SELECT cgc, sku, SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0) AS pmv_real_4m
                FROM fato_vendas
                WHERE data_pedido >= CURRENT_DATE - INTERVAL '4 months'
                GROUP BY cgc, sku
            )
            SELECT 
                f.ciclo_sop AS "Ciclo",
                TO_CHAR(f.mes_projetado, 'MM/YYYY') AS "Mes",
                c.cgc AS "CNPJ",
                c.gerente_nome AS "Gerente",
                c.supervisor_nome AS "Coordenador",
                f.vendedor_nome AS "Vendedor",
                c.razaosocial AS "Razao Social",
                f.sku AS "SKU",
                p.descricao AS "Descricao SKU",
                COALESCE(f.vol_meta, 0) AS "Vol Meta (CX)",
                COALESCE(hist.pmv_real_4m, f.pmv_aplicado, 0) AS "PMV",
                COALESCE(f.vol_meta, 0) * COALESCE(hist.pmv_real_4m, f.pmv_aplicado, 0) AS "Faturamento (R$)"
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            JOIN dim_produtos p ON f.sku = p.sku
            LEFT JOIN pmv_historico_4m hist ON hist.cgc = f.cgc AND hist.sku = f.sku
            WHERE f.ciclo_sop = :ciclo_atual
              AND f.vol_meta > 0
            ORDER BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome, c.razaosocial, f.sku
        """
        df = pd.read_sql(text(sql), engine, params={"ciclo_atual": ciclo_atual})
        
        stream = io.StringIO()
        df.to_csv(stream, index=False, sep=';', decimal=',', encoding='utf-8-sig')
        response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename=Metas_Equipe_{ciclo_atual.replace('/','_')}.csv"
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao exportar CSV.")