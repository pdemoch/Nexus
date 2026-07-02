from fastapi import APIRouter, HTTPException, Query
import pandas as pd
import numpy as np
import os

router = APIRouter(prefix="/api/v1/ccc", tags=["Ciclo de Conversão de Caixa"])

AWS_STORAGE_OPTIONS = {
    "key": os.getenv("AWS_ACCESS_KEY_ID"),
    "secret": os.getenv("AWS_SECRET_ACCESS_KEY"),
    "client_kwargs": {"region_name": "us-east-1"}
}
S3_BUCKET = "nexus-datalake-linea-prd"

@router.get("/pmr")
async def obter_pmr(data_inicio: str = Query(...), data_fim: str = Query(...)):
    try:
        caminho = f"s3://{S3_BUCKET}/financeiro/pmr/pmr_titulos.parquet"
        df = pd.read_parquet(caminho, storage_options=AWS_STORAGE_OPTIONS)
        
        # Ajusta nomes de colunas com espaço gerados na extração
        if 'razao social' in df.columns:
            df = df.rename(columns={"razao social": "razao_social"})
        
        # Filtro de datas Dinâmico
        df['e1_emissao'] = pd.to_datetime(df['e1_emissao'])
        mascara = (df['e1_emissao'] >= pd.to_datetime(data_inicio)) & (df['e1_emissao'] <= pd.to_datetime(data_fim))
        df = df[mascara]

        if df.empty: return {"kpis": {}, "tendencia": [], "dados": []}

        # KPIs Globais Exatos (Soma Ponderada / Soma Valores)
        t_val = df['e1_valor'].sum()
        kpis = {
            "pmr_base": round(df['peso_base'].sum() / t_val, 1) if t_val else 0,
            "pmr_real": round(df['peso_real'].sum() / t_val, 1) if t_val else 0,
        }
        kpis["delta"] = round(kpis["pmr_real"] - kpis["pmr_base"], 1)

        # Gráfico de Tendência Mensal
        df['mes'] = df['e1_emissao'].dt.strftime('%m/%Y')
        tr = df.groupby('mes').agg({'e1_valor':'sum', 'peso_base':'sum', 'peso_real':'sum'}).reset_index()
        tr['pmr_base'] = np.where(tr['e1_valor']>0, tr['peso_base']/tr['e1_valor'], 0).round(1)
        tr['pmr_real'] = np.where(tr['e1_valor']>0, tr['peso_real']/tr['e1_valor'], 0).round(1)
        tr['dt_sort'] = pd.to_datetime(tr['mes'], format='%m/%Y')
        tr = tr.sort_values('dt_sort')
        
        # Tabela Plana para OLAP Drill-down (Regional -> Razao -> CNPJ)
        df['cnpj_loja'] = df['e1_cliente'].astype(str) + "-" + df['e1_loja'].astype(str)
        grp = df.groupby(['regional', 'razao_social', 'cnpj_loja']).agg({'e1_valor':'sum', 'peso_base':'sum', 'peso_real':'sum'}).reset_index()
        grp['pmr_base'] = np.where(grp['e1_valor']>0, grp['peso_base']/grp['e1_valor'], 0).round(1)
        grp['pmr_real'] = np.where(grp['e1_valor']>0, grp['peso_real']/grp['e1_valor'], 0).round(1)
        grp['delta'] = (grp['pmr_real'] - grp['pmr_base']).round(1)
        
        return {"kpis": kpis, "tendencia": tr[['mes', 'pmr_base', 'pmr_real']].to_dict('records'), "dados": grp.to_dict('records')}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pmp")
async def obter_pmp(data_inicio: str = Query(...), data_fim: str = Query(...)):
    try:
        caminho = f"s3://{S3_BUCKET}/financeiro/pmp/pmp_titulos.parquet"
        df = pd.read_parquet(caminho, storage_options=AWS_STORAGE_OPTIONS)
        
        df['e2_emissao'] = pd.to_datetime(df['e2_emissao'])
        mascara = (df['e2_emissao'] >= pd.to_datetime(data_inicio)) & (df['e2_emissao'] <= pd.to_datetime(data_fim))
        df = df[mascara]

        if df.empty: return {"kpis": {}, "tendencia": [], "dados": []}

        t_val = df['e2_valor'].sum()
        kpis = {
            "pmp_base": round(df['peso_base'].sum() / t_val, 1) if t_val else 0,
            "pmp_real": round(df['peso_real'].sum() / t_val, 1) if t_val else 0,
        }
        kpis["delta"] = round(kpis["pmp_real"] - kpis["pmp_base"], 1)

        # Gráfico de Tendência Mensal
        df['mes'] = df['e2_emissao'].dt.strftime('%m/%Y')
        tr = df.groupby('mes').agg({'e2_valor':'sum', 'peso_base':'sum', 'peso_real':'sum'}).reset_index()
        tr['pmp_base'] = np.where(tr['e2_valor']>0, tr['peso_base']/tr['e2_valor'], 0).round(1)
        tr['pmp_real'] = np.where(tr['e2_valor']>0, tr['peso_real']/tr['e2_valor'], 0).round(1)
        tr['dt_sort'] = pd.to_datetime(tr['mes'], format='%m/%Y')
        tr = tr.sort_values('dt_sort')
        
        # Tabela Plana para OLAP Drill-down (Tipo -> Fornecedor)
        mapa = {"MP": "Matéria-Prima", "EM": "Embalagem", "SI": "Material de Consumo/Serviço"}
        df['tipo_desc'] = df['d1_tp'].map(mapa).fillna(df['d1_tp'])
        
        grp = df.groupby(['tipo_desc', 'a2_nome']).agg({'e2_valor':'sum', 'peso_base':'sum', 'peso_real':'sum'}).reset_index()
        grp['pmp_base'] = np.where(grp['e2_valor']>0, grp['peso_base']/grp['e2_valor'], 0).round(1)
        grp['pmp_real'] = np.where(grp['e2_valor']>0, grp['peso_real']/grp['e2_valor'], 0).round(1)
        grp['delta'] = (grp['pmp_real'] - grp['pmp_base']).round(1)
        
        return {"kpis": kpis, "tendencia": tr[['mes', 'pmp_base', 'pmp_real']].to_dict('records'), "dados": grp.to_dict('records')}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))