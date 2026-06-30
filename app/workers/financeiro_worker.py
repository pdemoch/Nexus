import os
import sys
import datetime
import pandas as pd
import numpy as np
import asyncio
import aiohttp
from sqlalchemy import create_engine, text

# ==========================================
# INJEÇÃO DINÂMICA DE ROTA (PARA O CRON E SUBPROCESSOS)
# ==========================================
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv('/nexus_backend/.env')
from app.core.config import settings

# ==========================================
# CONFIGURAÇÕES AWS S3 E APIs
# ==========================================
AWS_STORAGE_OPTIONS = {
    "key": "AKIA4VPN43D6KCHKRYM5",
    "secret": "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",
    "client_kwargs": {"region_name": "us-east-1"}
}

S3_BUCKET = "nexus-datalake-linea-prd"

# Note que agora apontamos para pastas que serão particionadas por mês
S3_PREFIX_PMR_SILVER = f"s3://{S3_BUCKET}/financeiro/pmr"
S3_PREFIX_PMP_SILVER = f"s3://{S3_BUCKET}/financeiro/pmp"

# Camada Ouro (Agregada por Cliente) para Risco de Estoque (Lida em Milissegundos)
S3_PREFIX_PMR_GOLD = f"s3://{S3_BUCKET}/financeiro/pmr/pmr_clientes_gold.parquet"

API_GOBI_BASE = "https://gobi-api.lineaalimentos.com.br/v1/reports"
TOKEN_GOBI = settings.GOBI_TOKEN if hasattr(settings, 'GOBI_TOKEN') and settings.GOBI_TOKEN else "TQWZ7G4UeRzu6zvmyt4b"

# ==========================================
# HELPERS DE EXTRAÇÃO ASSÍNCRONA
# ==========================================
async def _fetch_json_with_retry(session: aiohttp.ClientSession, url: str, params: dict, retries: int = 4):
    headers = {"Authorization": TOKEN_GOBI}
    for tentativa in range(retries):
        try:
            async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data if isinstance(data, list) else []
                elif response.status == 429:
                    print(f"      [!] Rate Limit (429). Aguardando {3 ** tentativa}s...")
                    await asyncio.sleep(3 ** tentativa)
                elif response.status == 401:
                    headers["Authorization"] = f"Bearer {TOKEN_GOBI}"
                else:
                    print(f"      ❌ Erro da Gobi {response.status} em {url}")
                    return None
        except Exception as e:
            if tentativa < retries - 1: await asyncio.sleep(2 ** tentativa)
    return None

async def _fetch_dados_dia(session: aiohttp.ClientSession, relatorio_id: int, dia: datetime.date) -> list:
    limit, offset, registros_dia = 5000, 0, []
    dia_str = dia.strftime("%Y-%m-%d")
    
    while True:
        params = {"start_date": dia_str, "end_date": dia_str, "streaming": "true", "format": "json", "limit": limit, "offset": offset}
        dados = await _fetch_json_with_retry(session, f"{API_GOBI_BASE}/{relatorio_id}/data", params)
        if not dados or len(dados) == 0: break
        registros_dia.extend(dados)
        offset += len(dados)
        if len(dados) < limit: break
            
    return registros_dia

async def extrair_dados_financeiros(session: aiohttp.ClientSession, relatorio_id: int, nome_relatorio: str) -> pd.DataFrame:
    registros_totais = []
    hoje = datetime.date.today()
    data_inicio = hoje - datetime.timedelta(days=180)
    lista_dias = [data_inicio + datetime.timedelta(days=x) for x in range(181)]
    
    print(f"   -> Iniciando varredura diária de 180 dias - {nome_relatorio} (ID {relatorio_id})...")
    
    chunk_size = 5
    for i in range(0, len(lista_dias), chunk_size):
        chunk_dias = lista_dias[i:i + chunk_size]
        tasks = [_fetch_dados_dia(session, relatorio_id, dia) for dia in chunk_dias]
        resultados = await asyncio.gather(*tasks)
        for lote in resultados:
            if lote: registros_totais.extend(lote)
        print(f"      Progresso: {min(i + chunk_size, len(lista_dias))}/{len(lista_dias)} dias processados...")
        await asyncio.sleep(0.3) 
        
    return pd.DataFrame(registros_totais)

# ==========================================
# PIPELINE PMP (CONTAS A PAGAR)
# ==========================================
async def processar_pmp(session: aiohttp.ClientSession):
    print("\n🔄 Processando PMP (Contas a Pagar) - Regra Fiscal Aplicada...")
    
    # 1. Títulos a Pagar (SE2 - API 590)
    df_se2 = await extrair_dados_financeiros(session, 590, "SE2 Títulos a Pagar")
    if df_se2.empty: return
    df_se2.columns = [c.lower().strip() for c in df_se2.columns]
    
    # 2. Notas Fiscais Entrada (SF1 - API 589)
    df_sf1 = await extrair_dados_financeiros(session, 589, "SF1 Notas de Entrada")
    if df_sf1.empty: return
    df_sf1.columns = [c.lower().strip() for c in df_sf1.columns]
    
    # Filtro Fiscal MP/SI/EM (Matéria Prima, Serviço Industrial, Embalagem)
    df_sf1 = df_sf1[df_sf1['d1_tp'].isin(['MP', 'SI', 'EM'])]
    df_sf1['chave'] = df_sf1['f1_fornece'].astype(str) + "-" + df_sf1['f1_loja'].astype(str) + "-" + df_sf1['f1_doc'].astype(str)
    df_sf1 = df_sf1.drop_duplicates(subset=['chave'])
    
    # Criar chave no Financeiro e fazer o Inner Join com a Nota Limpa
    df_se2['chave'] = df_se2['e2_fornece'].astype(str) + "-" + df_se2['e2_loja'].astype(str) + "-" + df_se2['e2_num'].astype(str)
    df_pmp = pd.merge(df_se2, df_sf1[['chave', 'd1_tp']], on='chave', how='inner')
    
    # Matemática do Tempo (Dias Reais e Base)
    for col in ['e2_emissao', 'e2_vencto', 'e2_vencrea']:
        df_pmp[col] = pd.to_datetime(df_pmp[col], errors='coerce')
        
    df_pmp = df_pmp.dropna(subset=['e2_emissao'])
    df_pmp['base_dias'] = (df_pmp['e2_vencto'] - df_pmp['e2_emissao']).dt.days.fillna(0).astype(int)
    df_pmp['real_dias'] = (df_pmp['e2_vencrea'] - df_pmp['e2_emissao']).dt.days.fillna(0).astype(int)
    
    df_pmp['e2_valor'] = pd.to_numeric(df_pmp['e2_valor'], errors='coerce').fillna(0)
    df_pmp['peso_base'] = df_pmp['base_dias'] * df_pmp['e2_valor']
    df_pmp['peso_real'] = df_pmp['real_dias'] * df_pmp['e2_valor']
    
    # Particionamento por Mês
    df_pmp['mes_competencia'] = df_pmp['e2_emissao'].dt.strftime('%Y-%m')
    
    colunas_finais = ['e2_filial', 'e2_num', 'e2_tipo', 'e2_fornece', 'e2_loja', 'a2_nome', 'e2_emissao', 'e2_vencto', 'e2_vencrea', 'e2_valor', 'd1_tp', 'base_dias', 'real_dias', 'peso_base', 'peso_real', 'mes_competencia']
    df_export = df_pmp[[c for c in colunas_finais if c in df_pmp.columns]]
    
    print(f"🚀 Enviando PMP (Silver) para S3 com Particionamento ({len(df_export)} Registros Válidos)...")
    df_export.to_parquet(S3_PREFIX_PMP_SILVER, engine='pyarrow', partition_cols=['mes_competencia'], existing_data_behavior='delete_matching', storage_options=AWS_STORAGE_OPTIONS)

# ==========================================
# PIPELINE PMR (CONTAS A RECEBER)
# ==========================================
async def processar_pmr(session: aiohttp.ClientSession, engine):
    print("\n🔄 Processando PMR (Contas a Receber) - Master Data e Regra Fiscal...")
    
    # 1. Puxar Master Data Oficial do Postgres Nexus
    with engine.connect() as conn:
        df_dim_clientes = pd.read_sql("SELECT cod_cliente, cod_loja, razaosocial, regional, segmento, cgc FROM dim_clientes", conn)
    df_dim_clientes['chave_cl'] = df_dim_clientes['cod_cliente'].astype(str).str.strip() + "-" + df_dim_clientes['cod_loja'].astype(str).str.strip()
    
    # 2. Notas Fiscais Saída (SF2 - API 595) - Filtro de Exceções
    df_sf2 = await extrair_dados_financeiros(session, 595, "SF2 Notas de Saída")
    if df_sf2.empty: return
    df_sf2.columns = [c.lower().strip() for c in df_sf2.columns]
    
    df_sf2 = df_sf2[df_sf2['f2_filial'].astype(str).str.strip() != '0107']
    df_sf2['chave_cl'] = df_sf2['f2_cliente'].astype(str).str.strip() + "-" + df_sf2['f2_loja'].astype(str).str.strip()
    
    # Mergir com Dimensão do Banco para herdar Regional, CGC e Segmento
    df_sf2 = pd.merge(df_sf2, df_dim_clientes[['chave_cl', 'regional', 'segmento', 'cgc', 'razaosocial']], on='chave_cl', how='left')
    
    # Aplicar Limpeza do PowerBI
    df_sf2 = df_sf2[~df_sf2['segmento'].astype(str).str.contains('AGENCIAS E FORNECEDORES', na=False, case=False)]
    df_sf2 = df_sf2[~df_sf2['regional'].astype(str).str.contains('EIC', na=False, case=False)]
    
    # Criar chave complexa NF
    df_sf2['chave'] = df_sf2['f2_cliente'].astype(str).str.strip() + "-" + df_sf2['f2_loja'].astype(str).str.strip() + "-" + df_sf2['f2_doc'].astype(str).str.strip() + "-" + df_sf2['f2_serie'].astype(str).str.strip()
    df_sf2 = df_sf2.drop_duplicates(subset=['chave'])
    
    # 3. Títulos a Receber (SE1 - API 596)
    df_se1 = await extrair_dados_financeiros(session, 596, "SE1 Títulos a Receber")
    if df_se1.empty: return
    df_se1.columns = [c.lower().strip() for c in df_se1.columns]
    
    df_se1['chave'] = df_se1['e1_cliente'].astype(str).str.strip() + "-" + df_se1['e1_loja'].astype(str).str.strip() + "-" + df_se1['e1_num'].astype(str).str.strip() + "-" + df_se1['e1_prefixo'].astype(str).str.strip()
    
    # O Grande Cruzamento Fisco-Financeiro (Inner Join elimina lixo)
    df_pmr = pd.merge(df_se1, df_sf2[['chave', 'regional', 'cgc', 'razaosocial', 'segmento']], on='chave', how='inner')
    
    # Matemática do Tempo
    for col in ['e1_emissao', 'e1_vencto', 'e1_vencrea']:
        df_pmr[col] = pd.to_datetime(df_pmr[col], errors='coerce')
        
    df_pmr = df_pmr.dropna(subset=['e1_emissao'])
    df_pmr['base_dias'] = (df_pmr['e1_vencto'] - df_pmr['e1_emissao']).dt.days.fillna(0).astype(int)
    df_pmr['real_dias'] = (df_pmr['e1_vencrea'] - df_pmr['e1_emissao']).dt.days.fillna(0).astype(int)
    
    df_pmr['e1_valor'] = pd.to_numeric(df_pmr['e1_valor'], errors='coerce').fillna(0)
    df_pmr['peso_base'] = df_pmr['base_dias'] * df_pmr['e1_valor']
    df_pmr['peso_real'] = df_pmr['real_dias'] * df_pmr['e1_valor']
    
    df_pmr['mes_competencia'] = df_pmr['e1_emissao'].dt.strftime('%Y-%m')
    
    # A. Salvar Camada Silver (Detalhe de Títulos)
    cols_silver = ['e1_filial', 'e1_num', 'e1_prefixo', 'e1_cliente', 'e1_loja', 'cgc', 'razaosocial', 'regional', 'segmento', 'e1_emissao', 'e1_vencto', 'e1_vencrea', 'e1_valor', 'base_dias', 'real_dias', 'peso_base', 'peso_real', 'mes_competencia']
    df_silver = df_pmr[[c for c in cols_silver if c in df_pmr.columns]]
    
    print(f"🚀 Enviando PMR (Silver) para S3 Particionado ({len(df_silver)} Registros Válidos)...")
    df_silver.to_parquet(S3_PREFIX_PMR_SILVER, engine='pyarrow', partition_cols=['mes_competencia'], existing_data_behavior='delete_matching', storage_options=AWS_STORAGE_OPTIONS)
    
    # B. Salvar Camada Gold (Hierarquia para Risco de Estoque)
    print("🥇 Calculando Camada Gold PMR (Master Data)...")
    df_gold = df_pmr.groupby(['cgc', 'regional']).agg({'e1_valor': 'sum', 'peso_real': 'sum'}).reset_index()
    df_gold['pmr_dias'] = np.where(df_gold['e1_valor'] > 0, df_gold['peso_real'] / df_gold['e1_valor'], 0).round(1)
    
    df_gold[['cgc', 'regional', 'pmr_dias']].to_parquet(S3_PREFIX_PMR_GOLD, engine='pyarrow', compression='snappy', storage_options=AWS_STORAGE_OPTIONS)

# ==========================================
# MASTER RUNNER
# ==========================================
async def main():
    print("==================================================")
    print("💰 INICIANDO ROTINA FINANCEIRA (LAKEHOUSE)")
    print("==================================================")
    engine = create_engine(settings.DATABASE_URL)
    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
    
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            await processar_pmr(session, engine)
            await processar_pmp(session)
        print("\n✅ Concluído! Engenharia de Caixa e Particionamento OK.")
    except Exception as e:
        print(f"\n❌ Falha fatal no pipeline: {e}")

if __name__ == "__main__":
    asyncio.run(main())