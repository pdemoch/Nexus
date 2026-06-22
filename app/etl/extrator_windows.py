import asyncio
import aiohttp
import polars as pl
import os
from datetime import datetime, timedelta
import calendar
from dateutil.relativedelta import relativedelta
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
import traceback

class NexusBypassWindows:
    def __init__(self):
        # ==========================================
        # 1. Configurações MTRIX (Rede Local)
        # ==========================================
        self.base_url = "http://172.20.2.172:8008"
        self.username = "Phillipe"
        self.password = "Phillipe123@"
        
        # ==========================================
        # 2. Configurações AWS (Acesso Nuvem)
        # ==========================================
        self.bucket_name = "nexus-datalake-linea-prd"
        self.s3_prefix = "mtrix/"
        
        self.aws_access_key = "AKIA4VPN43D6KCHKRYM5"
        self.aws_secret_key = "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"
        self.aws_region = "us-east-1"
        
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.aws_access_key,
            aws_secret_access_key=self.aws_secret_key,
            region_name=self.aws_region
        )
        
        # ==========================================
        # 3. Estrutura de Extração
        # ==========================================
        self.data_dir = Path("extracao_temporaria")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.tabelas_data = ["sellout", "forca_vendas", "estoque"]
        self.tabelas_estaticas = ["produtos", "distribuidores", "clientes"]
        
        self.timeout = aiohttp.ClientTimeout(total=600, connect=60)

    def log(self, msg):
        hora = datetime.now().strftime('%H:%M:%S')
        print(f"[{hora}] {msg}")

    def _verificar_primeira_carga(self) -> bool:
        """Verifica no S3 se já existe histórico para decidir o tamanho da janela."""
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=f"{self.s3_prefix}sellout_sellout_")
            if 'Contents' not in response or len(response['Contents']) == 0:
                return True
            return False
        except ClientError as e:
            self.log(f"⚠️ Erro ao checar S3: {e}")
            return True

    def _calcular_janela_temporal(self, primeira_carga: bool):
        """
        Regra: M-1.
        Se estamos em Junho, M-1 = Maio.
        Carga de Rotina = 3 últimos meses (Março, Abril, Maio).
        """
        hoje = datetime.now()
        # Recua 1 mês para garantir que só pegamos o mês fechado
        mes_fim = hoje - relativedelta(months=1) 
        ultimo_dia = calendar.monthrange(mes_fim.year, mes_fim.month)[1]
        
        data_fim = datetime(mes_fim.year, mes_fim.month, ultimo_dia)
        
        if primeira_carga:
            # Puxa 3 anos para trás a partir do M-1
            data_inicio = mes_fim - relativedelta(years=3)
            data_inicio = data_inicio.replace(day=1)
            modo = "HISTÓRICA (3 Anos)"
        else:
            # Puxa os 3 últimos meses fechados (M-1, M-2, M-3)
            data_inicio = mes_fim - relativedelta(months=3)
            data_inicio = data_inicio.replace(day=1)
            modo = "ROTINA DE ATUALIZAÇÃO (Últimos 3 Meses)"
            
        return data_inicio.strftime("%Y-%m-%d"), data_fim.strftime("%Y-%m-%d"), modo

    def _gerar_meses(self, start_date: str, end_date: str):
        """Fatia a janela de datas em blocos mensais estritos."""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        meses = []
        current = start.replace(day=1)
        
        while current <= end:
            next_month = current + relativedelta(months=1)
            last_day = next_month - timedelta(days=1)
            if last_day > end: last_day = end
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

    async def _extrair_tabela(self, session: aiohttp.ClientSession, token: str, tabela: str, data_inicio: str = None, data_fim: str = None, sufixo_nome: str = ""):
        url = f"{self.base_url}/data/tabelas/{tabela}"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"limit": 500, "page": 1}
        
        if data_inicio and data_fim:
            params["start_date"] = data_inicio
            params["end_date"] = data_fim

        async with session.get(url, headers=headers, params=params, timeout=self.timeout) as resp:
            if resp.status != 200: return False
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
            return False

        # Conversão estruturada e compressão
        df = pl.DataFrame(registros).cast(pl.Utf8) 
        
        # O nome dinâmico permite à AWS substituir (overwrite) o arquivo automaticamente
        nome_arquivo = f"sellout_{tabela}{sufixo_nome}.parquet"
        caminho_local = str(self.data_dir / nome_arquivo)
        
        df.write_parquet(caminho_local, compression="snappy")
        
        # Upload imediato para a Nuvem
        self.log(f"      ☁️ Fazendo Upsert de '{nome_arquivo}' no S3...")
        await asyncio.to_thread(self.s3_client.upload_file, caminho_local, self.bucket_name, f"{self.s3_prefix}{nome_arquivo}")
        
        # Limpa o arquivo local do Windows
        os.remove(caminho_local) 
        return True

    async def executar(self):
        self.log("=========================================================")
        self.log("🚀 NEXUS BYPASS: INICIANDO EXTRAÇÃO INTELIGENTE MTRIX -> S3")
        self.log("=========================================================")
        
        primeira_carga = self._verificar_primeira_carga()
        data_inicio, data_fim, modo = self._calcular_janela_temporal(primeira_carga)
        
        self.log(f"🔎 Estratégia de Carga: {modo}")
        self.log(f"📅 Período Alvo: {data_inicio} até {data_fim}")
        
        meses_para_extrair = self._gerar_meses(data_inicio, data_fim)
        
        connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                self.log("\n🔑 Autenticando na rede local da MTRIX...")
                token = await self._autenticar(session)
                self.log("✅ Autenticado com sucesso!")
                
                # -------------------------------------------------------------
                # 1. Tabelas Dinâmicas (Com data e particionamento Mês a Mês)
                # -------------------------------------------------------------
                for tabela in self.tabelas_data:
                    self.log(f"\n📦 Processando Tabela Fato: '{tabela.upper()}'")
                    for dt_ini, dt_fim, mes_str in meses_para_extrair:
                        self.log(f"   -> Sincronizando Competência: {mes_str}...")
                        resultado = await self._extrair_tabela(session, token, tabela, dt_ini, dt_fim, sufixo_nome=f"_{mes_str}")
                        if not resultado:
                            self.log(f"      ⚠️ Sem dados para este mês.")
                
                # -------------------------------------------------------------
                # 2. Tabelas Estáticas (Sem data, sobrescritas sempre)
                # Atualizamos sempre para garantir novos SKUs e CNPJs
                # -------------------------------------------------------------
                self.log("\n📋 Atualizando Tabelas de Dimensão (Cadastros)...")
                for tabela in self.tabelas_estaticas:
                    self.log(f"   -> Sincronizando Cadastro: '{tabela.upper()}'...")
                    await self._extrair_tabela(session, token, tabela, None, None)

                self.log("\n🎉=======================================================")
                self.log("✅ SINCRONIZAÇÃO CONCLUÍDA! O DATA LAKE ESTÁ ATUALIZADO.")
                self.log("=========================================================")
                        
            except Exception as e:
                erro_detalhado = traceback.format_exc()
                self.log(f"\n❌ ERRO CRÍTICO NO BYPASS: {str(e)}")
                self.log(f"Detalhes:\n{erro_detalhado}")

if __name__ == "__main__":
    extrator = NexusBypassWindows()
    asyncio.run(extrator.executar())