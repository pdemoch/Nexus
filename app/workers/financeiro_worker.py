import os
import sys
import datetime
import pandas as pd
import numpy as np
import asyncio
import aiohttp
import json
from botocore.exceptions import ClientError
from typing import Dict, List, Optional
import boto3

# ==========================================
# BLINDAGEM DO AMBIENTE (O Padrão Dotenv)
# ==========================================
# Força o Python a injetar as variáveis do ficheiro .env para este ambiente isolado
# O caminho é absoluto para a raiz do container Docker
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

API_GOBI_BASE = "https://gobi-api.lineaalimentos.com.br/v1/reports"

# ==========================================
# HELPERS DE EXTRAÇÃO ASSÍNCRONA
# ==========================================
async def _fetch_json_with_retry(session: aiohttp.ClientSession, url: str, params: dict, retries: int = 4) -> Optional[Any]:
    headers = {"Authorization": f"Bearer {settings.GOBI_TOKEN}"}
    for tentativa in range(retries):
        try:
            async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=300)) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data if isinstance(data, list) else []
                elif response.status == 429:
                    await asyncio.sleep(3 ** tentativa)
                else:
                    text_erro = await response.text()
                    print(f"❌ Erro {response.status} em {url}: {text_erro[:100]}")
        except Exception as e:
            if tentativa < retries - 1:
                await asyncio.sleep(2 ** tentativa)
    return None

async def extrair_cadastro_clientes(session: aiohttp.ClientSession) -> pd.DataFrame:
    """Extrai os dados da API 188 (Clientes) para cruzamento de Razão Social e Regional"""
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
            
    return pd.DataFrame(clientes)

async def _fetch_bloco_financeiro(session: aiohttp.ClientSession, relatorio_id: int, offset: int, limit: int = 10000):
    params = {"streaming": "true", "format": "json", "limit": limit, "offset": offset}
    return await _fetch_json_with_retry(session, f"{API_GOBI_BASE}/{relatorio_id}/data", params)

async def extrair_dados_financeiros(session: aiohttp.ClientSession, relatorio_id: int) -> pd.DataFrame:
    """Extrai grandes volumes de dados dividindo o trabalho em blocos paralelos"""
    limit = 10000
    # Como não sabemos o total, faremos uma amostragem paralela otimista para os primeiros N blocos
    # Para o financeiro (PMR/PMP) vamos assumir que o volume cabe na RAM e iterar até acabar
    registros = []
    offset = 0
    lote_anterior = []
    
    # Vamos extrair em blocos de 5 chamadas simultâneas (50.000 registos por onda)
    while True:
        offsets = [offset + (i * limit) for i in range(5)]
        tasks = [_fetch_bloco_financeiro(session, relatorio_id, o, limit) for o in offsets]
        resultados = await asyncio.gather(*tasks)
        
        for lote in resultados:
            if lote:
                registros.extend(lote)
        
        # Se algum dos lotes voltou vazio, ou o último lote foi menor que o limite, terminamos
        if not resultados[-1] or len(resultados[-1]) < limit:
            break
            
        offset += (5 * limit)
        print(f"   -> Foram lidos {len(registros)} registos...")
        
    return pd.DataFrame(registros)

# ==========================================
# PROCESSAMENTO DE NEGÓCIO: CCC
# ==========================================
async def processar_pmr():
    print("\n🔄 Iniciando Processamento do PMR (Contas a Receber)...")
    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        
        # 1. Puxar o Cadastro de Clientes (API 188)
        try:
            df_clientes = await extrair_cadastro_clientes(session)
        except Exception as e:
            print(f"[ERRO] Falha de conexão API 188: {e}")
            df_clientes = None
            
        # Blindagem Anti-Crash
        if df_clientes is None or df_clientes.empty:
            print("⚠️ [AVISO] A Tabela de Clientes (API 188) está vazia. Prosseguindo sem dados regionais...")
            df_clientes = pd.DataFrame(columns=['cod', 'loja', 'razaosocial', 'regional', 'cod_loja'])
        else:
            if 'codigo' in df_clientes.columns:
                df_clientes.rename(columns={'codigo': 'cod'}, inplace=True)
            df_clientes['cod_loja'] = df_clientes['cod'].astype(str) + "-" + df_clientes['loja'].astype(str)
            df_clientes = df_clientes[['cod_loja', 'razaosocial', 'regional']].drop_duplicates()

        # 2. Puxar Títulos a Receber (API 596 - PMR)
        print("📥 Baixando Relatório 596 (PMR)...")
        df_pmr = await extrair_dados_financeiros(session, 596)
        
        if df_pmr.empty:
            print("⚠️ Sem dados de PMR retornados da GOBI.")
            return

        # Limpeza e Cruzamento de Dados
        df_pmr.columns = [c.lower().strip() for c in df_pmr.columns]
        
        # A data de emissão costuma vir com espaços ou como string
        if 'e1_emissao' in df_pmr.columns:
            df_pmr['e1_emissao'] = pd.to_datetime(df_pmr['e1_emissao'], errors='coerce')
        
        # Converter valores para numérico
        cols_numericas = ['e1_valor', 'e1_saldo', 'e1_prazob', 'e1_prazor']
        for col in cols_numericas:
            if col in df_pmr.columns:
                df_pmr[col] = pd.to_numeric(df_pmr[col], errors='coerce').fillna(0)
                
        # Criar a chave para cruzar com o cadastro de clientes
        if 'e1_cliente' in df_pmr.columns and 'e1_loja' in df_pmr.columns:
            df_pmr['cod_loja'] = df_pmr['e1_cliente'].astype(str) + "-" + df_pmr['e1_loja'].astype(str)
            
            # Cruzamento
            df_final = pd.merge(df_pmr, df_clientes, on='cod_loja', how='left')
            df_final['razao_social'] = df_final['razaosocial'].fillna('Não Identificado')
            df_final['regional'] = df_final['regional'].fillna('Não Identificada')
        else:
             df_final = df_pmr
             df_final['razao_social'] = 'Não Identificado'
             df_final['regional'] = 'Não Identificada'
             
        # Cálculo dos Pesos para a Média Ponderada
        df_final['peso_base'] = df_final['e1_valor'] * df_final['e1_prazob']
        df_final['peso_real'] = df_final['e1_valor'] * df_final['e1_prazor']
        
        # Filtrar Apenas o que Importa para o Parquet (Economia de Storage)
        cols_export = ['e1_filial', 'e1_num', 'e1_tipo', 'e1_cliente', 'e1_loja', 'e1_emissao', 'e1_vencrea', 'e1_baixa', 
                       'e1_valor', 'e1_saldo', 'e1_prazob', 'e1_prazor', 'razao_social', 'regional', 'peso_base', 'peso_real']
        
        cols_export_existentes = [c for c in cols_export if c in df_final.columns]
        df_final = df_final[cols_export_existentes]

        # Escrever para S3
        print(f"🚀 Enviando PMR para Data Lake AWS ({len(df_final)} registos)...")
        df_final.to_parquet(S3_PREFIX_PMR, engine='pyarrow', compression='snappy', storage_options=AWS_STORAGE_OPTIONS)

async def processar_pmp():
    print("\n🔄 Iniciando Processamento do PMP (Contas a Pagar)...")
    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        
        # 1. Puxar Títulos a Pagar (API 595 - PMP)
        print("📥 Baixando Relatório 595 (PMP)...")
        df_pmp = await extrair_dados_financeiros(session, 595)
        
        if df_pmp.empty:
            print("⚠️ Sem dados de PMP retornados da GOBI.")
            return

        # Limpeza
        df_pmp.columns = [c.lower().strip() for c in df_pmp.columns]
        
        if 'e2_emissao' in df_pmp.columns:
            df_pmp['e2_emissao'] = pd.to_datetime(df_pmp['e2_emissao'], errors='coerce')
            
        cols_numericas = ['e2_valor', 'e2_saldo', 'e2_prazob', 'e2_prazor']
        for col in cols_numericas:
            if col in df_pmp.columns:
                df_pmp[col] = pd.to_numeric(df_pmp[col], errors='coerce').fillna(0)
                
        # Cálculo dos Pesos para a Média Ponderada
        df_pmp['peso_base'] = df_pmp['e2_valor'] * df_pmp['e2_prazob']
        df_pmp['peso_real'] = df_pmp['e2_valor'] * df_pmp['e2_prazor']
        
        # Exportação
        cols_export = ['e2_filial', 'e2_num', 'e2_tipo', 'e2_fornece', 'e2_loja', 'e2_emissao', 'e2_vencrea', 'e2_baixa', 
                       'e2_valor', 'e2_saldo', 'e2_prazob', 'e2_prazor', 'a2_nome', 'd1_tp', 'peso_base', 'peso_real']
                       
        cols_export_existentes = [c for c in cols_export if c in df_pmp.columns]
        df_pmp = df_pmp[cols_export_existentes]

        # Escrever para S3
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