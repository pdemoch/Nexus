import os
import sys
import datetime
import pandas as pd
import numpy as np
import asyncio
import aiohttp
from sqlalchemy import create_engine
from dotenv import load_dotenv

# ==========================================
# INJEÇÃO DINÂMICA DE ROTA (PARA O CRON E SUBPROCESSOS)
# ==========================================
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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

# [ALTERADO] Caminhos da Camada Prata (Silver) limpos, sem a pasta /historico
S3_PREFIX_PMR_SILVER = f"s3://{S3_BUCKET}/financeiro/pmr"
S3_PREFIX_PMP_SILVER = f"s3://{S3_BUCKET}/financeiro/pmp"

# Caminho da Camada Gold para o Router KPIs ler em milissegundos
S3_PREFIX_PMR_GOLD = f"s3://{S3_BUCKET}/financeiro/pmr/pmr_clientes_gold.parquet"

API_GOBI_BASE = "https://gobi-api.lineaalimentos.com.br/v1/reports"

# Tenta usar o Token do .env, senão cai para o Token fixo extraído do PowerBI
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
                    text_erro = await response.text()
                    print(f"      ❌ Erro da Gobi {response.status} em {url}: {text_erro[:100]}")
                    return None
        except Exception as e:
            print(f"      [!] Timeout/Falha na tentativa {tentativa+1} ({url}): {e}")
            if tentativa < retries - 1: await asyncio.sleep(2 ** tentativa)
    return None

async def extrair_cadastro_clientes_188(session: aiohttp.ClientSession) -> pd.DataFrame:
    """A 'Pedra de Roseta': Transforma o código do Protheus num CNPJ real."""
    print("   -> 📥 Puxando API 188 (Tradutor Cliente/Loja para CGC)...")
    limit, offset, clientes = 2500, 0, []
    
    while True:
        print(f"      - Baixando lote de clientes a partir da linha {offset}...")
        dados = await _fetch_json_with_retry(session, f"{API_GOBI_BASE}/188/data", {"streaming": "true", "format": "json", "limit": limit, "offset": offset})
        
        if not dados or len(dados) == 0: break
        clientes.extend(dados)
        offset += len(dados)
        if len(dados) < limit: break
            
    print(f"   -> ✅ API 188 concluída! {len(clientes)} clientes carregados.")
        
    df = pd.DataFrame(clientes)
    if not df.empty:
        df.columns = [str(x).lower().strip() for x in df.columns]
        mapa = {'codigo': 'cod', 'a1_cod': 'cod', 'cnpj': 'cgc', 'cgc_cpf': 'cgc', 'a1_cgc': 'cgc'}
        df.rename(columns=mapa, inplace=True)
        if 'cod' not in df.columns: df['cod'] = '000000'
        if 'loja' not in df.columns: df['loja'] = '00'
        if 'cgc' not in df.columns: df['cgc'] = '00000000000000'
        
        df['chave_cl'] = df['cod'].astype(str).str.strip() + "-" + df['loja'].astype(str).str.strip()
        df['cgc'] = df['cgc'].astype(str).str.replace(r'\D', '', regex=True)
        return df[['chave_cl', 'cgc']].drop_duplicates()
    return pd.DataFrame(columns=['chave_cl', 'cgc'])

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
    
    print(f"   -> Varredura Diária: {nome_relatorio} (ID {relatorio_id})...")
    chunk_size = 5
    for i in range(0, len(lista_dias), chunk_size):
        chunk_dias = lista_dias[i:i + chunk_size]
        tasks = [_fetch_dados_dia(session, relatorio_id, dia) for dia in chunk_dias]
        resultados = await asyncio.gather(*tasks)
        for lote in resultados:
            if lote: registros_totais.extend(lote)
        print(f"      Progresso: {min(i + chunk_size, len(lista_dias))}/{len(lista_dias)} dias. Títulos: {len(registros_totais)}")
        await asyncio.sleep(0.3) 
    return pd.DataFrame(registros_totais)

# ==========================================
# PIPELINE PMP (CONTAS A PAGAR)
# ==========================================
async def processar_pmp(session: aiohttp.ClientSession):
    print("\n🔄 Processando PMP (Contas a Pagar) - Regra Fiscal Aplicada...")
    
    df_se2 = await extrair_dados_financeiros(session, 590, "SE2 Títulos a Pagar")
    if df_se2.empty: return
    df_se2.columns = [c.lower().strip() for c in df_se2.columns]
    
    df_sf1 = await extrair_dados_financeiros(session, 589, "SF1 Notas de Entrada")
    if df_sf1.empty: return
    df_sf1.columns = [c.lower().strip() for c in df_sf1.columns]
    
    # Filtro Fiscal MP/SI/EM
    if 'd1_tp' in df_sf1.columns:
        df_sf1 = df_sf1[df_sf1['d1_tp'].isin(['MP', 'SI', 'EM'])]
    
    df_sf1['chave_nf'] = df_sf1.get('f1_fornece', '').astype(str).str.strip() + "-" + df_sf1.get('f1_loja', '').astype(str).str.strip() + "-" + df_sf1.get('f1_doc', '').astype(str).str.strip()
    df_sf1 = df_sf1.drop_duplicates(subset=['chave_nf'])
    
    df_se2['chave_nf'] = df_se2.get('e2_fornece', '').astype(str).str.strip() + "-" + df_se2.get('e2_loja', '').astype(str).str.strip() + "-" + df_se2.get('e2_num', '').astype(str).str.strip()
    
    # Cruzamento Fiscal x Financeiro
    df_pmp = pd.merge(df_se2, df_sf1[['chave_nf', 'd1_tp']], on='chave_nf', how='inner')
    
    for col in ['e2_emissao', 'e2_vencto', 'e2_vencrea']:
        if col in df_pmp.columns: df_pmp[col] = pd.to_datetime(df_pmp[col], errors='coerce')
        
    df_pmp = df_pmp.dropna(subset=['e2_emissao'])
    if 'e2_vencto' in df_pmp.columns: df_pmp['base_dias'] = (df_pmp['e2_vencto'] - df_pmp['e2_emissao']).dt.days.fillna(0).astype(int)
    else: df_pmp['base_dias'] = 0
    if 'e2_vencrea' in df_pmp.columns: df_pmp['real_dias'] = (df_pmp['e2_vencrea'] - df_pmp['e2_emissao']).dt.days.fillna(0).astype(int)
    else: df_pmp['real_dias'] = 0
    
    df_pmp['e2_valor'] = pd.to_numeric(df_pmp.get('e2_valor', 0), errors='coerce').fillna(0)
    df_pmp['peso_base'] = df_pmp['base_dias'] * df_pmp['e2_valor']
    df_pmp['peso_real'] = df_pmp['real_dias'] * df_pmp['e2_valor']
    
    df_pmp['mes_competencia'] = df_pmp['e2_emissao'].dt.strftime('%Y-%m')
    
    colunas_finais = ['e2_filial', 'e2_num', 'e2_tipo', 'e2_fornece', 'e2_loja', 'a2_nome', 'e2_emissao', 'e2_vencto', 'e2_vencrea', 'e2_valor', 'd1_tp', 'base_dias', 'real_dias', 'peso_base', 'peso_real', 'mes_competencia']
    df_export = df_pmp[[c for c in colunas_finais if c in df_pmp.columns]]
    
    print(f"🚀 Enviando PMP (Silver) para S3 ({len(df_export)} Registros Válidos)...")
    if not df_export.empty:
        df_export.to_parquet(S3_PREFIX_PMP_SILVER, engine='pyarrow', partition_cols=['mes_competencia'], existing_data_behavior='delete_matching', storage_options=AWS_STORAGE_OPTIONS)

# ==========================================
# PIPELINE PMR (CONTAS A RECEBER)
# ==========================================
async def processar_pmr(session: aiohttp.ClientSession, engine):
    print("\n🔄 Processando PMR (Contas a Receber)...")
    
    # 1. Tabela 188 (A Ponte: Cliente/Loja -> CGC)
    df_188 = await extrair_cadastro_clientes_188(session)
    
    # 2. Master Data (A Verdade Absoluta: CGC -> Regional/Segmento)
    print("   -> 📥 Lendo Base Quente (PostgreSQL) para amarrações oficiais...")
    with engine.connect() as conn:
        df_dim = pd.read_sql("SELECT cgc, razaosocial, regional, segmento FROM dim_clientes", conn)
    
    # 3. SF2 (Notas de Saída)
    df_sf2 = await extrair_dados_financeiros(session, 595, "SF2 Notas de Saída")
    if df_sf2.empty: return
    df_sf2.columns = [c.lower().strip() for c in df_sf2.columns]
    
    # Filtros Fiscais PowerBI
    if 'f2_filial' in df_sf2.columns: df_sf2 = df_sf2[df_sf2['f2_filial'].astype(str).str.strip() != '0107']
    df_sf2['chave_cl'] = df_sf2.get('f2_cliente', '').astype(str).str.strip() + "-" + df_sf2.get('f2_loja', '').astype(str).str.strip()
    
    # Enriquecimento com 188 e Postgres ANTES do JOIN Financeiro
    df_sf2 = pd.merge(df_sf2, df_188, on='chave_cl', how='left') # Ganha o CGC
    df_sf2 = pd.merge(df_sf2, df_dim, on='cgc', how='left') # Ganha Regional/Segmento
    
    # Filtros de Segmento/Regional (Idêntico ao PowerBI)
    df_sf2 = df_sf2[~df_sf2['segmento'].astype(str).str.contains('AGENCIAS E FORNECEDORES', na=False, case=False)]
    df_sf2 = df_sf2[~df_sf2['regional'].astype(str).str.contains('EIC', na=False, case=False)]
    
    df_sf2['chave_nf'] = df_sf2.get('f2_cliente','').astype(str).str.strip() + "-" + df_sf2.get('f2_loja','').astype(str).str.strip() + "-" + df_sf2.get('f2_doc','').astype(str).str.strip() + "-" + df_sf2.get('f2_serie','').astype(str).str.strip()
    df_sf2 = df_sf2.drop_duplicates(subset=['chave_nf'])
    
    # 4. SE1 (Títulos a Receber)
    df_se1 = await extrair_dados_financeiros(session, 596, "SE1 Títulos a Receber")
    if df_se1.empty: return
    df_se1.columns = [c.lower().strip() for c in df_se1.columns]
    
    df_se1['chave_nf'] = df_se1.get('e1_cliente','').astype(str).str.strip() + "-" + df_se1.get('e1_loja','').astype(str).str.strip() + "-" + df_se1.get('e1_num','').astype(str).str.strip() + "-" + df_se1.get('e1_prefixo','').astype(str).str.strip()
    
    # 5. Inner Join Fisco-Financeiro
    df_pmr = pd.merge(df_se1, df_sf2[['chave_nf', 'regional', 'cgc', 'razaosocial', 'segmento']], on='chave_nf', how='inner')
    
    # 6. Matemática
    for col in ['e1_emissao', 'e1_vencto', 'e1_vencrea']:
        if col in df_pmr.columns: df_pmr[col] = pd.to_datetime(df_pmr[col], errors='coerce')
        
    df_pmr = df_pmr.dropna(subset=['e1_emissao'])
    if 'e1_vencto' in df_pmr.columns: df_pmr['base_dias'] = (df_pmr['e1_vencto'] - df_pmr['e1_emissao']).dt.days.fillna(0).astype(int)
    else: df_pmr['base_dias'] = 0
    if 'e1_vencrea' in df_pmr.columns: df_pmr['real_dias'] = (df_pmr['e1_vencrea'] - df_pmr['e1_emissao']).dt.days.fillna(0).astype(int)
    else: df_pmr['real_dias'] = 0
    
    df_pmr['e1_valor'] = pd.to_numeric(df_pmr.get('e1_valor', 0), errors='coerce').fillna(0)
    df_pmr['peso_base'] = df_pmr['base_dias'] * df_pmr['e1_valor']
    df_pmr['peso_real'] = df_pmr['real_dias'] * df_pmr['e1_valor']
    
    df_pmr['mes_competencia'] = df_pmr['e1_emissao'].dt.strftime('%Y-%m')
    
    # A. Salvar Silver
    cols_silver = ['e1_filial', 'e1_num', 'e1_prefixo', 'e1_cliente', 'e1_loja', 'cgc', 'razaosocial', 'regional', 'segmento', 'e1_emissao', 'e1_vencto', 'e1_vencrea', 'e1_valor', 'base_dias', 'real_dias', 'peso_base', 'peso_real', 'mes_competencia']
    df_silver = df_pmr[[c for c in cols_silver if c in df_pmr.columns]]
    
    print(f"🚀 Enviando PMR (Silver) para S3 ({len(df_silver)} Registros)...")
    if not df_silver.empty:
        df_silver.to_parquet(S3_PREFIX_PMR_SILVER, engine='pyarrow', partition_cols=['mes_competencia'], existing_data_behavior='delete_matching', storage_options=AWS_STORAGE_OPTIONS)
    
    # B. Salvar Gold (Risco Estoque)
    print("🥇 Calculando Camada Gold PMR (Hierarquia para Risco de Estoque)...")
    df_gold = df_pmr.groupby(['cgc', 'regional']).agg({'e1_valor': 'sum', 'peso_real': 'sum'}).reset_index()
    df_gold['pmr_dias'] = np.where(df_gold['e1_valor'] > 0, df_gold['peso_real'] / df_gold['e1_valor'], 0).round(1)
    
    if not df_gold.empty:
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
        print("\n✅ Concluído! Engenharia de Caixa Atualizada.")
    except Exception as e:
        print(f"\n❌ Falha fatal no pipeline: {e}")

if __name__ == "__main__":
    asyncio.run(main())