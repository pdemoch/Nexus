import asyncio
import aiohttp
import polars as pl
import os
import glob
import traceback
from datetime import date, timedelta, datetime
import calendar
from dateutil.relativedelta import relativedelta
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from typing import Optional, Any
from app.core.config import settings 

class GobiExtractor:
    def __init__(self, data_dir: str = "data"):
        self.headers = {"Authorization": f"Bearer {settings.GOBI_TOKEN}"}
        self.base_url = "https://gobi-api.lineaalimentos.com.br/v1/reports" 
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(10)
        self.timeout = aiohttp.ClientTimeout(total=400)

    async def _fetch_json_with_retry(self, session: aiohttp.ClientSession, url: str, params: dict, retries: int = 4) -> Optional[Any]:
        async with self.semaphore:
            for tentativa in range(retries):
                try:
                    async with session.get(url, headers=self.headers, params=params, timeout=self.timeout) as response:
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

    async def _fetch_dia_paginado(self, session: aiohttp.ClientSession, dia_str: str) -> Optional[str]:
        offset = 0
        limit = 1000
        lote_anterior = []
        registros_dia = []

        while True:
            params = {"start_date": dia_str, "end_date": dia_str, "streaming": "true", "format": "json", "limit": limit, "offset": offset}
            lote = await self._fetch_json_with_retry(session, f"{self.base_url}/150/data", params)
            
            if lote is None or len(lote) == 0: break
            if lote_anterior and lote[0] == lote_anterior[0]: break
                
            registros_dia.extend(lote)
            lote_anterior = lote
            offset += len(lote)
            if len(lote) < limit: break
                
        if not registros_dia: return None
            
        df = pl.from_dicts(registros_dia)
        colunas_numericas = ["qtpedido", "qtfatura", "qtcd", "qtpendencia", "qtcorte", "qtestoque", "vlpedido", "vlcorte", "vlfatura", "vlcd", "vlpendencia"]
        cols_para_cast = [c for c in colunas_numericas if c in df.columns]
        if cols_para_cast:
            df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False) for c in cols_para_cast])
            
        arquivo_parquet = self.data_dir / f"150_{dia_str}.parquet"
        df.write_parquet(arquivo_parquet)
        return str(arquivo_parquet)

    async def extrair_pedidos_150(self, data_inicio: date, data_fim: date) -> pl.LazyFrame:
        for f in glob.glob(f"{self.data_dir}/150_*.parquet"):
            try: os.remove(f)
            except: pass

        dias = []
        data_atual = data_inicio
        while data_atual <= data_fim:
            dias.append(data_atual.strftime("%Y-%m-%d"))
            data_atual += timedelta(days=1)

        connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [self._fetch_dia_paginado(session, dia) for dia in dias]
            await asyncio.gather(*tasks)

        arquivos_gerados = glob.glob(f"{self.data_dir}/150_*.parquet")
        if not arquivos_gerados: return pl.LazyFrame()
        return pl.scan_parquet(arquivos_gerados)

    async def extrair_clientes_188(self) -> pl.LazyFrame:
        for f in glob.glob(f"{self.data_dir}/188_*.parquet"):
            try: os.remove(f)
            except: pass

        offset = 0
        limit = 5000
        lote_anterior = []
        chunk_idx = 0

        connector = aiohttp.TCPConnector(limit=5, keepalive_timeout=30)
        async with aiohttp.ClientSession(connector=connector) as session:
            while True:
                params = {"streaming": "true", "format": "json", "limit": limit, "offset": offset}
                lote = await self._fetch_json_with_retry(session, f"{self.base_url}/188/data", params)
                if not lote: break
                if lote_anterior and lote[0] == lote_anterior[0]: break
                
                df_chunk = pl.from_dicts(lote)
                df_chunk.write_parquet(f"{self.data_dir}/188_chunk_{chunk_idx}.parquet")
                
                lote_anterior = lote
                offset += len(lote)
                chunk_idx += 1
                if len(lote) < limit: break

        arquivos_gerados = glob.glob(f"{self.data_dir}/188_*.parquet")
        if not arquivos_gerados: return pl.LazyFrame()
        return pl.scan_parquet(arquivos_gerados)

    def extrair_segmentos(self, caminho_arquivo: str) -> pl.DataFrame:
        try:
            caminho = Path(caminho_arquivo)
            if caminho.exists():
                df = pl.read_excel(caminho_arquivo)
                df.columns = [c.lower().strip() for c in df.columns]
                colunas_foco = ["produto", "bu", "categoria", "segmento", "2026"]
                cols_existentes = [c for c in colunas_foco if c in df.columns]
                df = df.select(cols_existentes)
                if "produto" in df.columns: df = df.with_columns(pl.col("produto").cast(pl.Utf8))
                return df
        except Exception as e: print(f"Erro ao ler Segmentos.xlsx: {e}")
        return pl.DataFrame()

    def extrair_orcamento(self, caminho_arquivo: str) -> pl.DataFrame:
        try:
            caminho = Path(caminho_arquivo)
            if caminho.exists():
                import pandas as pd
                df_pd = pd.read_excel(caminho_arquivo, dtype={"Produto": str})
                df = pl.from_pandas(df_pd)
                return df
        except Exception as e: 
            print(f"Erro ao ler Orçamento.xlsx: {e}")
        return pl.DataFrame()

    async def extrair_tudo(self, data_inicio: date, data_fim: date):
        caminho_excel = "app/etl/Segmentos.xlsx"
        caminho_orcamento = "app/etl/Orçamento.xlsx"
        
        tarefa_150 = self.extrair_pedidos_150(data_inicio, data_fim)
        tarefa_188 = self.extrair_clientes_188()
        df_seg = self.extrair_segmentos(caminho_excel)
        df_orc = self.extrair_orcamento(caminho_orcamento)
        
        lf_150, lf_188 = await asyncio.gather(tarefa_150, tarefa_188)
        return lf_150, lf_188, df_seg, df_orc


class MtrixExtractor:
    def __init__(self, bucket_name: str = "nexus-datalake-linea-prd", data_dir: str = "data/mtrix"):
        self.base_url = "http://172.20.2.172:8008"
        self.username = "Phillipe"
        self.password = "Phillipe123@"
        self.bucket_name = bucket_name
        self.s3_prefix = "mtrix/"
        
        self.s3_client = boto3.client('s3') 
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.tabelas_data = ["sellout", "forca_vendas", "estoque"]
        self.tabelas_estaticas = ["produtos", "distribuidores", "clientes"]
        
        self.timeout = aiohttp.ClientTimeout(total=600, connect=60)

    def _verificar_primeira_carga(self) -> bool:
        """Verifica se há QUALQUER arquivo de sellout no bucket para definir a estratégia."""
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=f"{self.s3_prefix}sellout_sellout_")
            return 'Contents' not in response or len(response['Contents']) == 0
        except Exception:
            return True 

    def _calcular_janela_temporal(self, primeira_carga: bool):
        hoje = datetime.now()
        mes_alvo = hoje - relativedelta(months=2) 
        ultimo_dia = calendar.monthrange(mes_alvo.year, mes_alvo.month)[1]
        
        data_fim = datetime(mes_alvo.year, mes_alvo.month, ultimo_dia)
        
        if primeira_carga:
            data_inicio = mes_alvo - relativedelta(years=3)
            data_inicio = data_inicio.replace(day=1)
            modo = "HISTÓRICA (3 Anos Particionados)"
        else:
            data_inicio = mes_alvo - relativedelta(months=3)
            data_inicio = data_inicio.replace(day=1)
            modo = "ROTINA (Últimos 3 Meses)"
            
        return data_inicio.strftime("%Y-%m-%d"), data_fim.strftime("%Y-%m-%d"), modo

    def _gerar_meses(self, start_date: str, end_date: str):
        """Quebra o período de extração em janelas estritas de 1 mês."""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        meses = []
        current = start.replace(day=1)
        
        while current <= end:
            next_month = current + relativedelta(months=1)
            last_day = next_month - timedelta(days=1)
            if last_day > end:
                last_day = end
            
            meses.append((current.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d"), current.strftime("%Y_%m")))
            current = next_month
            
        return meses

    async def _autenticar(self, session: aiohttp.ClientSession):
        url = f"{self.base_url}/auth/login"
        payload = {"username": self.username, "password": self.password}
        async with session.post(url, json=payload, timeout=self.timeout) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("access_token")

    async def _extrair_tabela(self, session: aiohttp.ClientSession, token: str, tabela: str, data_inicio: str, data_fim: str, log_callback, sufixo_nome: str = ""):
        url = f"{self.base_url}/data/tabelas/{tabela}"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"limit": 500, "page": 1}
        
        if data_inicio and data_fim:
            params["start_date"] = data_inicio
            params["end_date"] = data_fim

        async with session.get(url, headers=headers, params=params, timeout=self.timeout) as resp:
            if resp.status != 200: return None
            data = await resp.json()
            
        registros = data.get("dados", [])
        total_paginas = data.get("pagination", {}).get("total_pages", 1)

        if total_paginas > 1:
            async def fetch_page(page_num):
                p = params.copy()
                p["page"] = page_num
                for _ in range(3): 
                    try:
                        async with session.get(url, headers=headers, params=p, timeout=self.timeout) as r:
                            if r.status == 200: return (await r.json()).get("dados", [])
                    except Exception:
                        await asyncio.sleep(2)
                return []

            tasks = [fetch_page(pag) for pag in range(2, total_paginas + 1)]
            resultados = await asyncio.gather(*tasks)
            for res in resultados: registros.extend(res)

        if not registros: 
            return None

        df = pl.DataFrame(registros).cast(pl.Utf8) 
        
        nome_arquivo = f"sellout_{tabela}{sufixo_nome}.parquet"
        caminho_local = str(self.data_dir / nome_arquivo)
        
        df.write_parquet(caminho_local, compression="snappy")
        
        await asyncio.to_thread(self.s3_client.upload_file, caminho_local, self.bucket_name, f"{self.s3_prefix}{nome_arquivo}")
        return caminho_local

    async def executar_extracao(self, log_callback):
        log_callback("🔎 [MTRIX] Verificando infraestrutura S3 para decidir estratégia de carga...")
        
        primeira_carga = await asyncio.to_thread(self._verificar_primeira_carga)
        data_inicio, data_fim, modo = self._calcular_janela_temporal(primeira_carga)
        
        log_callback(f"🚀 [MTRIX] Ingestão Ativada: Modo {modo} ({data_inicio} até {data_fim})")
        
        meses_para_extrair = self._gerar_meses(data_inicio, data_fim)
        
        connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                token = await self._autenticar(session)
                
                for tabela in self.tabelas_data:
                    for dt_ini, dt_fim, mes_str in meses_para_extrair:
                        log_callback(f"   -> [MTRIX] Extraindo '{tabela}' (Competência: {mes_str})...")
                        resultado = await self._extrair_tabela(session, token, tabela, dt_ini, dt_fim, log_callback, sufixo_nome=f"_{mes_str}")
                        if not resultado:
                            log_callback(f"      ⚠️ Sem dados de '{tabela}' para o mês {mes_str}.")
                
                if primeira_carga:
                    for tabela in self.tabelas_estaticas:
                        log_callback(f"   -> [MTRIX] Extraindo cadastro estático: '{tabela}'...")
                        await self._extrair_tabela(session, token, tabela, None, None, log_callback)

                log_callback(f"✅ [S3] Upload de Data Lake finalizado com sucesso!")
                        
            except Exception as e:
                erro_detalhado = traceback.format_exc()
                log_callback(f"❌ [MTRIX] Erro crítico no pipeline de ingestão: {str(e)}")
                print(f"Detalhes do erro MTRIX: \n{erro_detalhado}")