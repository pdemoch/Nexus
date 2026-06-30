import os
import sys
import datetime
import pandas as pd
import numpy as np
import asyncio
import aiohttp
import json
from botocore.exceptions import ClientError
from typing import Dict, List, Optional, Any
import boto3

# ==========================================
# INJEÇÃO DINÂMICA DE ROTA (PARA O CRON E SUBPROCESSOS)
# ==========================================
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ==========================================
# BLINDAGEM DO AMBIENTE (O Padrão Dotenv)
# ==========================================
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
S3_PREFIX_PMR = f"s3://{S3_BUCKET}/financeiro/pmr/pmr_titulos.parquet"
S3_PREFIX_PMP = f"s3://{S3_BUCKET}/financeiro/pmp/pmp_titulos.parquet"

# Caminho da Camada Gold para o Router KPIs ler em milissegundos
S3_PREFIX_PMR_GOLD = f"s3://{S3_BUCKET}/financeiro/pmr/pmr_clientes_gold.parquet"

API_GOBI_BASE = "https://gobi-api.lineaalimentos.com.br/v1/reports"

# Tenta usar o Token do .env, senão cai para o Token fixo extraído do PowerBI
TOKEN_GOBI = settings.GOBI_TOKEN if hasattr(settings, 'GOBI_TOKEN') and settings.GOBI_TOKEN else "TQWZ7G4UeRzu6zvmyt4b"

# ==========================================
# HELPERS DE EXTRAÇÃO ASSÍNCRONA (MODO TAGARELA)
# ==========================================
async def _fetch_json_with_retry(session: aiohttp.ClientSession, url: str, params: dict, retries: int = 4) -> Optional[Any]:
    # Algumas rotas da Gobi aceitam Bearer, outras exigem apenas o token. Tenta primeiro puro.
    headers = {"Authorization": TOKEN_GOBI}
    
    for tentativa in range(retries):
        try:
            async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data if isinstance(data, list) else []
                elif response.status == 429:
                    print(f"      [!] Gobi Rate Limit (429). Aguardando {3 ** tentativa}s...")
                    await asyncio.sleep(3 ** tentativa)
                else:
                    text_erro = await response.text()
                    print(f"      ❌ Erro da Gobi {response.status} em {url}: {text_erro[:100]}")
                    
                    # Fallback: Se der Unauthorized, tenta com "Bearer " na frente na próxima rodada
                    if response.status == 401:
                        headers["Authorization"] = f"Bearer {TOKEN_GOBI}"
                        
        except Exception as e:
            print(f"      [!] Timeout/Falha na tentativa {tentativa+1} ({url}): {e}")
            if tentativa < retries - 1:
                await asyncio.sleep(2 ** tentativa)
    return None

async def extrair_cadastro_clientes(session: aiohttp.ClientSession) -> pd.DataFrame:
    print("   -> 📥 Baixando Cadastro de Clientes (API 188)...")
    limit, offset = 5000, 0
    lote_anterior, clientes = [], []
    
    while True:
        params = {"streaming": "true", "format": "json", "limit": limit, "offset": offset}
        dados = await _fetch_json_with_retry(session, f"{API_GOBI_BASE}/188/data", params)
        
        if not dados or len(dados) == 0: break
        if lote_anterior and dados[0] == lote_anterior[0]: break
            
        clientes.extend(dados)
        lote_anterior = dados
        offset += len(dados)
        if len(dados) < limit: break
            
    print(f"   -> ✅ Cadastro concluído: {len(clientes)} clientes na memória.")
    return pd.DataFrame(clientes)

async def _fetch_dados_dia(session: aiohttp.ClientSession, relatorio_id: int, dia: datetime.date) -> list:
    limit = 5000
    offset = 0
    registros_dia = []
    dia_str = dia.strftime("%Y-%m-%d")
    
    while True:
        # ATENÇÃO CIRÚRGICA: 'start_date' e 'end_date' extraídos do PowerBI!
        params = {
            "start_date": dia_str, 
            "end_date": dia_str,
            "streaming": "true", 
            "format": "json", 
            "limit": limit, 
            "offset": offset
        }
        
        dados = await _fetch_json_with_retry(session, f"{API_GOBI_BASE}/{relatorio_id}/data", params)
        if not dados or len(dados) == 0: break
            
        registros_dia.extend(dados)
        offset += len(dados)
        if len(dados) < limit: break
            
    return registros_dia

async def extrair_dados_financeiros(session: aiohttp.ClientSession, relatorio_id: int) -> pd.DataFrame:
    registros_totais = []
    hoje = datetime.date.today()
    data_inicio = hoje - datetime.timedelta(days=180)
    lista_dias = [data_inicio + datetime.timedelta(days=x) for x in range(181)]
    
    print(f"   -> Iniciando varredura diária para os últimos 180 dias (Relatório {relatorio_id})...")
    
    chunk_size = 5
    for i in range(0, len(lista_dias), chunk_size):
        chunk_dias = lista_dias[i:i + chunk_size]
        tasks = [_fetch_dados_dia(session, relatorio_id, dia) for dia in chunk_dias]
        resultados = await asyncio.gather(*tasks)
        
        for lote in resultados:
            if lote:
                registros_totais.extend(lote)
                
        print(f"   -> Progresso: {min(i + chunk_size, len(lista_dias))}/{len(lista_dias)} dias processados. Registos: {len(registros_totais)}")
        await asyncio.sleep(0.5) 
        
    return pd.DataFrame(registros_totais)

# ==========================================
# PROCESSAMENTO DE NEGÓCIO: CCC
# ==========================================
async def processar_pmr():
    print("\n🔄 Iniciando Processamento do PMR (Contas a Receber)...")
    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        
        try:
            df_clientes = await extrair_cadastro_clientes(session)
        except Exception as e:
            print(f"[ERRO] Falha de conexão API 188: {e}")
            df_clientes = None
            
        # ========================================================
        # 🛡️ BLINDAGEM DO DATAFRAME DE CLIENTES
        # ========================================================
        if df_clientes is None or df_clientes.empty:
            print("⚠️ [AVISO] A Tabela de Clientes (API 188) está vazia.")
            df_clientes = pd.DataFrame(columns=['cod_loja', 'razaosocial', 'regional', 'cgc'])
        else:
            df_clientes.columns = [str(x).lower().strip() for x in df_clientes.columns]
            
            mapa_colunas = {
                'codigo': 'cod', 'a1_cod': 'cod',
                'cnpj': 'cgc', 'cgc_cpf': 'cgc', 'a1_cgc': 'cgc',
                'razao_social': 'razaosocial', 'nome': 'razaosocial', 'a1_nome': 'razaosocial',
                'regiao': 'regional'
            }
            df_clientes.rename(columns=mapa_colunas, inplace=True)
            
            if 'cod' not in df_clientes.columns: df_clientes['cod'] = '000000'
            if 'loja' not in df_clientes.columns: df_clientes['loja'] = '00'
            if 'razaosocial' not in df_clientes.columns: df_clientes['razaosocial'] = 'Não Identificado'
            if 'regional' not in df_clientes.columns: df_clientes['regional'] = 'Não Identificada'
            if 'cgc' not in df_clientes.columns: df_clientes['cgc'] = '00000000000000'
            
            df_clientes['cod_loja'] = df_clientes['cod'].astype(str) + "-" + df_clientes['loja'].astype(str)
            df_clientes['cgc'] = df_clientes['cgc'].astype(str).str.replace(r'\D', '', regex=True)
            
            df_clientes = df_clientes[['cod_loja', 'razaosocial', 'regional', 'cgc']].drop_duplicates()

        # ========================================================
        # BAIXAR E PROCESSAR TÍTULOS PMR
        # ========================================================
        print("📥 Baixando Relatório 596 (PMR)...")
        df_pmr = await extrair_dados_financeiros(session, 596)
        
        if df_pmr.empty:
            print("⚠️ Sem dados de PMR retornados da GOBI.")
            return

        df_pmr.columns = [c.lower().strip() for c in df_pmr.columns]
        
        if 'e1_emissao' in df_pmr.columns:
            df_pmr['e1_emissao'] = pd.to_datetime(df_pmr['e1_emissao'], errors='coerce')
        
        for col in ['e1_valor', 'e1_saldo', 'e1_prazob', 'e1_prazor']:
            if col not in df_pmr.columns: df_pmr[col] = 0.0
            df_pmr[col] = pd.to_numeric(df_pmr[col], errors='coerce').fillna(0)
                
        if 'e1_cliente' in df_pmr.columns and 'e1_loja' in df_pmr.columns:
            df_pmr['cod_loja'] = df_pmr['e1_cliente'].astype(str) + "-" + df_pmr['e1_loja'].astype(str)
            df_final = pd.merge(df_pmr, df_clientes, on='cod_loja', how='left')
            df_final['razao_social'] = df_final['razaosocial'].fillna('Não Identificado')
            df_final['regional'] = df_final['regional'].fillna('Não Identificada')
            df_final['cgc'] = df_final['cgc'].fillna('00000000000000')
        else:
             df_final = df_pmr
             df_final['razao_social'] = 'Não Identificado'
             df_final['regional'] = 'Não Identificada'
             df_final['cgc'] = '00000000000000'
             
        df_final['peso_base'] = df_final['e1_valor'] * df_final['e1_prazob']
        df_final['peso_real'] = df_final['e1_valor'] * df_final['e1_prazor']
        
        cols_export = ['e1_filial', 'e1_num', 'e1_tipo', 'e1_cliente', 'e1_loja', 'cgc', 'e1_emissao', 'e1_vencrea', 'e1_baixa', 
                       'e1_valor', 'e1_saldo', 'e1_prazob', 'e1_prazor', 'razao_social', 'regional', 'peso_base', 'peso_real']
        
        cols_export_existentes = [c for c in cols_export if c in df_final.columns]
        df_silver = df_final[cols_export_existentes]

        print(f"🚀 Enviando Títulos PMR (Silver) para AWS ({len(df_silver)} registos)...")
        df_silver.to_parquet(S3_PREFIX_PMR, engine='pyarrow', compression='snappy', storage_options=AWS_STORAGE_OPTIONS)

        print("🥇 Calculando Camada Gold de PMR por Cliente...")
        df_gold = df_final.groupby('cgc').agg({'e1_valor': 'sum', 'peso_real': 'sum'}).reset_index()
        df_gold['pmr_dias'] = np.where(df_gold['e1_valor'] > 0, df_gold['peso_real'] / df_gold['e1_valor'], 0).round(1)
        
        df_gold = df_gold[['cgc', 'pmr_dias']]
        print(f"🚀 Enviando PMR Clientes (Gold) para AWS ({len(df_gold)} clientes únicos)...")
        df_gold.to_parquet(S3_PREFIX_PMR_GOLD, engine='pyarrow', compression='snappy', storage_options=AWS_STORAGE_OPTIONS)

async def processar_pmp():
    print("\n🔄 Iniciando Processamento do PMP (Contas a Pagar)...")
    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        
        print("📥 Baixando Relatório 595 (PMP)...")
        df_pmp = await extrair_dados_financeiros(session, 595)
        
        if df_pmp.empty:
            print("⚠️ Sem dados de PMP retornados da GOBI.")
            return

        df_pmp.columns = [c.lower().strip() for c in df_pmp.columns]
        
        if 'e2_emissao' in df_pmp.columns:
            df_pmp['e2_emissao'] = pd.to_datetime(df_pmp['e2_emissao'], errors='coerce')
            
        for col in ['e2_valor', 'e2_saldo', 'e2_prazob', 'e2_prazor']:
            if col not in df_pmp.columns: df_pmp[col] = 0.0
            df_pmp[col] = pd.to_numeric(df_pmp[col], errors='coerce').fillna(0)
                
        df_pmp['peso_base'] = df_pmp['e2_valor'] * df_pmp['e2_prazob']
        df_pmp['peso_real'] = df_pmp['e2_valor'] * df_pmp['e2_prazor']
        
        cols_export = ['e2_filial', 'e2_num', 'e2_tipo', 'e2_fornece', 'e2_loja', 'e2_emissao', 'e2_vencrea', 'e2_baixa', 
                       'e2_valor', 'e2_saldo', 'e2_prazob', 'e2_prazor', 'a2_nome', 'd1_tp', 'peso_base', 'peso_real']
                       
        cols_export_existentes = [c for c in cols_export if c in df_pmp.columns]
        df_pmp = df_pmp[cols_export_existentes]

        print(f"🚀 Enviando PMP para Data Lake AWS ({len(df_pmp)} registos)...")
        df_pmp.to_parquet(S3_PREFIX_PMP, engine='pyarrow', compression='snappy', storage_options=AWS_STORAGE_OPTIONS)

async def main():
    print("==================================================")
    print("💰 INICIANDO ROTINA FINANCEIRA NEXUS (CCC)")
    print("==================================================")
    
    try:
        await processar_pmr()
        await processar_pmp()
        print("\n✅ Concluído! Data Lake de Engenharia de Caixa Atualizado.")
    except Exception as e:
        print(f"\n❌ Falha fatal no pipeline financeiro: {e}")

if __name__ == "__main__":
    asyncio.run(main())