import pandas as pd
import numpy as np
import datetime
from dateutil.relativedelta import relativedelta
import asyncio
import aiohttp
import time
import os

# ==========================================
# CONFIGURAÇÕES (AWS E GOBI)
# ==========================================
GOBI_TOKEN = "TQWZ7G4UeRzu6zvmyt4b"
GOBI_BASE_URL = "https://gobi-api.lineaalimentos.com.br"

AWS_STORAGE_OPTIONS = {
    "key": "AKIA4VPN43D6KCHKRYM5",
    "secret": "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",
    "client_kwargs": {"region_name": "us-east-1"}
}
S3_BUCKET = "nexus-datalake-linea-prd"

# ==========================================
# FUNÇÕES DE EXTRAÇÃO ASSÍNCRONA DA API
# ==========================================
async def fetch_gobi_report(session, report_id, start_date, end_date):
    """Busca um relatório da GOBI para um intervalo específico"""
    url = f"{GOBI_BASE_URL}/v1/reports/{report_id}/data"
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "streaming": "true",
        "format": "json"
    }
    headers = {"Authorization": GOBI_TOKEN}
    
    try:
        async with session.get(url, params=params, headers=headers, timeout=60) as response:
            if response.status == 200:
                data = await response.json(content_type=None)
                return data if isinstance(data, list) else []
            else:
                print(f"[ERRO] API {report_id} ({start_date} a {end_date}): Status {response.status}")
                return []
    except Exception as e:
        print(f"[ERRO] Falha de conexão API {report_id}: {e}")
        return []

async def fetch_report_6_months(report_id):
    """Divide a consulta de 6 meses em blocos mensais e executa simultaneamente"""
    hoje = datetime.date.today()
    meses_atras = hoje - relativedelta(months=6)
    data_inicio_global = datetime.date(meses_atras.year, meses_atras.month, 1)
    
    blocos = []
    data_atual = data_inicio_global
    while data_atual <= hoje:
        prox_mes = data_atual + relativedelta(months=1)
        fim_mes = prox_mes - datetime.timedelta(days=1)
        if fim_mes > hoje: fim_mes = hoje
        blocos.append((data_atual.strftime("%Y-%m-%d"), fim_mes.strftime("%Y-%m-%d")))
        data_atual = prox_mes

    print(f"📥 Baixando Relatório {report_id} em {len(blocos)} blocos paralelos...")
    
    async with aiohttp.ClientSession() as session:
        tarefas = [fetch_gobi_report(session, report_id, b[0], b[1]) for b in blocos]
        resultados = await asyncio.gather(*tarefas)
        
    dados_totais = [linha for bloco in resultados for linha in bloco]
    return pd.DataFrame(dados_totais)

async def fetch_clientes_188():
    """Busca a tabela inteira de Clientes (Sem data)"""
    async with aiohttp.ClientSession() as session:
        return pd.DataFrame(await fetch_gobi_report(session, 188, None, None))

# ==========================================
# PROCESSAMENTO PMR (RECEBIMENTO DE CLIENTES)
# ==========================================
async def processar_pmr():
    print("\n🔄 Iniciando Processamento do PMR (Contas a Receber)...")
    
    df_clientes = await fetch_clientes_188()
    df_se1 = await fetch_report_6_months(596) # Contas a Receber
    df_sf2 = await fetch_report_6_months(595) # Notas Fiscais Saída

    if df_se1.empty or df_sf2.empty:
        print("❌ Dados insuficientes para calcular PMR.")
        return

    # Tratamento Tabela Clientes
    df_clientes['cod_loja'] = df_clientes['cod'].astype(str) + "-" + df_clientes['loja'].astype(str)
    
    # Tratamento SF2 (Notas Fiscais)
    df_sf2 = df_sf2[df_sf2['f2_filial'] != '0107']
    df_sf2['cod_loja'] = df_sf2['f2_cliente'].astype(str) + "-" + df_sf2['f2_loja'].astype(str)
    
    # Merge SF2 com Clientes (Para filtrar sucatas/agências)
    df_sf2 = df_sf2.merge(df_clientes[['cod_loja', 'razao social', 'segmento', 'regional']], on='cod_loja', how='left')
    df_sf2 = df_sf2[~df_sf2['segmento'].str.contains("AGENCIAS E FORNECEDORES", na=False, case=False)]
    df_sf2 = df_sf2[~df_sf2['regional'].str.contains("EIC", na=False, case=False)]
    
    # Chave Única SF2
    df_sf2['chave'] = df_sf2['f2_cliente'].astype(str) + "-" + df_sf2['f2_loja'].astype(str) + "-" + df_sf2['f2_doc'].astype(str) + "-" + df_sf2['f2_serie'].astype(str)
    df_sf2 = df_sf2.drop_duplicates(subset=['chave'])

    # Chave Única SE1 (Contas a Receber)
    df_se1['chave'] = df_se1['e1_cliente'].astype(str) + "-" + df_se1['e1_loja'].astype(str) + "-" + df_se1['e1_num'].astype(str) + "-" + df_se1['e1_prefixo'].astype(str)

    # O "LeftOuter" (Cruzamento Fiscal x Financeiro)
    df_pmr = df_se1.merge(df_sf2[['chave', 'razao social', 'segmento', 'regional']], on='chave', how='inner')
    
    # Cálculos Financeiros (Datas e Valores)
    df_pmr['e1_emissao'] = pd.to_datetime(df_pmr['e1_emissao'], errors='coerce')
    df_pmr['e1_vencto'] = pd.to_datetime(df_pmr['e1_vencto'], errors='coerce')
    df_pmr['e1_vencrea'] = pd.to_datetime(df_pmr['e1_vencrea'], errors='coerce') # Pode ser nulo se não pagou
    df_pmr['e1_valor'] = pd.to_numeric(df_pmr['e1_valor'], errors='coerce').fillna(0)

    # Base (Dias Negociados) - Pega todas as notas
    df_pmr['dias_base'] = (df_pmr['e1_vencto'] - df_pmr['e1_emissao']).dt.days
    df_pmr['peso_base'] = df_pmr['dias_base'] * df_pmr['e1_valor']

    # Real (Dias Pagos) - ATENÇÃO: Só calcula onde a data de baixa existe!
    df_pmr['dias_real'] = np.where(df_pmr['e1_vencrea'].notna(), (df_pmr['e1_vencrea'] - df_pmr['e1_emissao']).dt.days, np.nan)
    df_pmr['peso_real'] = df_pmr['dias_real'] * df_pmr['e1_valor']

    # Seleciona as colunas finais
    df_final_pmr = df_pmr[['e1_emissao', 'e1_cliente', 'e1_loja', 'razao social', 'regional', 'segmento', 'e1_valor', 'dias_base', 'peso_base', 'dias_real', 'peso_real']]
    
    caminho_pmr = f"s3://{S3_BUCKET}/financeiro/pmr/pmr_titulos.parquet"
    df_final_pmr.to_parquet(caminho_pmr, engine='pyarrow', index=False, storage_options=AWS_STORAGE_OPTIONS)
    print(f"✅ PMR Salvo no Data Lake! ({len(df_final_pmr)} Títulos)")

# ==========================================
# PROCESSAMENTO PMP (PAGAMENTO FORNECEDORES)
# ==========================================
async def processar_pmp():
    print("\n🔄 Iniciando Processamento do PMP (Contas a Pagar)...")
    
    df_se2 = await fetch_report_6_months(590) # Contas a Pagar
    df_sf1 = await fetch_report_6_months(589) # Notas Fiscais Entrada

    if df_se2.empty or df_sf1.empty:
        print("❌ Dados insuficientes para calcular PMP.")
        return

    # Tratamento SF1 (Entradas) - Filtra Materia Prima, Embalagem e Consumo
    df_sf1 = df_sf1[df_sf1['f1_doc'].notna()]
    df_sf1 = df_sf1[df_sf1['d1_tp'].isin(['MP', 'EM', 'SI'])]
    
    # Chave Única SF1
    df_sf1['chave'] = df_sf1['f1_fornece'].astype(str) + "-" + df_sf1['f1_loja'].astype(str) + "-" + df_sf1['f1_doc'].astype(str)
    df_sf1 = df_sf1.drop_duplicates(subset=['chave'])

    # Chave Única SE2 (Contas a Pagar)
    df_se2 = df_se2[df_se2['e2_emissao'].notna()]
    df_se2['chave'] = df_se2['e2_fornece'].astype(str) + "-" + df_se2['e2_loja'].astype(str) + "-" + df_se2['e2_num'].astype(str)

    # O "LeftOuter" (Cruzamento Fiscal x Financeiro)
    df_pmp = df_se2.merge(df_sf1[['chave', 'd1_tp']], on='chave', how='inner')

    # Cálculos Financeiros (Datas e Valores)
    df_pmp['e2_emissao'] = pd.to_datetime(df_pmp['e2_emissao'], errors='coerce')
    df_pmp['e2_vencto'] = pd.to_datetime(df_pmp['e2_vencto'], errors='coerce')
    df_pmp['e2_vencrea'] = pd.to_datetime(df_pmp['e2_vencrea'], errors='coerce')
    df_pmp['e2_valor'] = pd.to_numeric(df_pmp['e2_valor'], errors='coerce').fillna(0)

    # Base (Dias Negociados)
    df_pmp['dias_base'] = (df_pmp['e2_vencto'] - df_pmp['e2_emissao']).dt.days
    df_pmp['peso_base'] = df_pmp['dias_base'] * df_pmp['e2_valor']

    # Real (Dias Pagos) - Só onde a baixa existe
    df_pmp['dias_real'] = np.where(df_pmp['e2_vencrea'].notna(), (df_pmp['e2_vencrea'] - df_pmp['e2_emissao']).dt.days, np.nan)
    df_pmp['peso_real'] = df_pmp['dias_real'] * df_pmp['e2_valor']

    # Seleciona as colunas finais
    df_final_pmp = df_pmp[['e2_emissao', 'e2_fornece', 'a2_nome', 'd1_tp', 'e2_valor', 'dias_base', 'peso_base', 'dias_real', 'peso_real']]
    
    caminho_pmp = f"s3://{S3_BUCKET}/financeiro/pmp/pmp_titulos.parquet"
    df_final_pmp.to_parquet(caminho_pmp, engine='pyarrow', index=False, storage_options=AWS_STORAGE_OPTIONS)
    print(f"✅ PMP Salvo no Data Lake! ({len(df_final_pmp)} Títulos)")

# ==========================================
# EXECUÇÃO PRINCIPAL
# ==========================================
async def main():
    inicio = time.time()
    print("==================================================")
    print("💰 INICIANDO ROTINA FINANCEIRA NEXUS (CCC)")
    print("==================================================")
    
    await processar_pmr()
    await processar_pmp()
    
    fim = time.time()
    minutos = int((fim - inicio) // 60)
    segundos = int((fim - inicio) % 60)
    print(f"==================================================")
    print(f"🏁 DADOS GRAVADOS COM SUCESSO! Tempo: {minutos}m {segundos}s")

if __name__ == "__main__":
    asyncio.run(main())