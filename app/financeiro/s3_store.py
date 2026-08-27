"""
=====================================================================
S3 STORE — leitura e escrita de parquets financeiros no S3
=====================================================================
Destino: app/financeiro/s3_store.py

Layout S3:
  s3://{BUCKET}/financeiro/notas_saida/{YYYY}/{MM:02d}.parquet
  s3://{BUCKET}/financeiro/contas_receber/{YYYY}/{MM:02d}.parquet
  s3://{BUCKET}/financeiro/movimentacao_bancaria/{YYYY}/{MM:02d}.parquet
  s3://{BUCKET}/financeiro/clientes/latest.parquet

Estratégia por fonte:
  notas_saida, contas_receber, movimentacao_bancaria:
    Parquet mensal, particionado pela coluna de data principal
    (f2_emissao, e1_emissao, e5_data respectivamente).
    Re-extrair um mês sobrescreve o arquivo — idempotente.

  clientes:
    Snapshot único. Sempre sobrescreve latest.parquet.

VARIÁVEIS DE AMBIENTE:
  NEXUS_S3_BUCKET         — bucket existente do projeto
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_DEFAULT_REGION
"""

import io
import os
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

import boto3
import pandas as pd
from dateutil.relativedelta import relativedelta
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_BUCKET = os.getenv("NEXUS_S3_BUCKET", "")
_PREFIX = "financeiro"


# Coluna de data usada para particionamento por mês de cada fonte
_DATA_COL: dict[str, str] = {
    "notas_saida":            "f2_emissao",
    "contas_receber":         "e1_emissao",
    "movimentacao_bancaria":  "e5_data",
    "notas_entrada":          "f1_emissao",
    "contas_pagar":           "e2_emissao",
}


def _s3():
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def _chave(fonte: str, ano: int, mes: int) -> str:
    return f"{_PREFIX}/{fonte}/{ano}/{mes:02d}.parquet"


def _chave_clientes() -> str:
    return f"{_PREFIX}/clientes/latest.parquet"

def salvar_fornecedores(df: pd.DataFrame) -> bool:
    """Snapshot SA2 (latest), separado do cadastro de clientes/PMR."""
    if df is None:
        return False
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    try:
        _s3().put_object(Bucket=_BUCKET, Key=f"{_PREFIX}/fornecedores/latest.parquet",
                         Body=buf.getvalue())
        return True
    except Exception as e:
        logger.error("S3 fornecedores/latest: %s", e)
        return False

def carregar_fornecedores() -> pd.DataFrame:
    df = _baixar_parquet(f"{_PREFIX}/fornecedores/latest.parquet")
    return df if df is not None else pd.DataFrame()


def salvar_snapshot(df: pd.DataFrame, fonte: str) -> bool:
    """Persiste um cadastro sem datas em ``{fonte}/latest.parquet``."""
    if df is None or df.empty:
        return False
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    try:
        _s3().put_object(Bucket=_BUCKET, Key=f"{_PREFIX}/{fonte}/latest.parquet",
                         Body=buf.getvalue())
        return True
    except Exception as e:
        logger.error("S3 snapshot %s/latest: %s", fonte, e)
        return False


def carregar_snapshot(fonte: str) -> pd.DataFrame:
    df = _baixar_parquet(f"{_PREFIX}/{fonte}/latest.parquet")
    return df if df is not None else pd.DataFrame()


def salvar_sb1(df: pd.DataFrame) -> bool:
    return salvar_snapshot(df, "sb1")


def salvar_sg1(df: pd.DataFrame) -> bool:
    return salvar_snapshot(df, "sg1")


def carregar_sb1() -> pd.DataFrame:
    return carregar_snapshot("sb1")


def carregar_sg1() -> pd.DataFrame:
    return carregar_snapshot("sg1")


# ─── Escrita ──────────────────────────────────────────────────────────────────

def salvar_mensal(df: pd.DataFrame, fonte: str, ano: int, mes: int) -> bool:
    """
    Salva o DataFrame como parquet mensal no S3.
    Usa pyarrow pelo engine do pandas — nenhum import extra necessário.
    """
    if df.empty:
        logger.warning("salvar_mensal: DataFrame vazio para %s %d/%02d — nada gravado.", fonte, ano, mes)
        return False

    key = _chave(fonte, ano, mes)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    buf.seek(0)

    try:
        _s3().put_object(Bucket=_BUCKET, Key=key, Body=buf.getvalue())
        logger.info("✅ S3: %s/%s gravado (%d linhas, %.1f KB)",
                    _BUCKET, key, len(df), buf.tell() / 1024)
        return True
    except Exception as e:
        logger.error("❌ S3 put_object %s/%s: %s", _BUCKET, key, e)
        return False


def salvar_clientes(df: pd.DataFrame) -> bool:
    key = _chave_clientes()
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    buf.seek(0)
    try:
        _s3().put_object(Bucket=_BUCKET, Key=key, Body=buf.getvalue())
        logger.info("✅ S3: clientes/latest gravado (%d linhas)", len(df))
        return True
    except Exception as e:
        logger.error("❌ S3 clientes/latest: %s", e)
        return False


# ─── Leitura ──────────────────────────────────────────────────────────────────

def _baixar_parquet(key: str) -> Optional[pd.DataFrame]:
    """Baixa um único parquet do S3 para memória."""
    try:
        obj = _s3().get_object(Bucket=_BUCKET, Key=key)
        buf = io.BytesIO(obj["Body"].read())
        return pd.read_parquet(buf, engine="pyarrow")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        logger.error("S3 get_object %s/%s: %s", _BUCKET, key, e)
        return None
    except Exception as e:
        logger.error("Erro ao ler parquet %s: %s", key, e)
        return None


def carregar_mensal(fonte: str,
                    data_ini: date,
                    data_fim: date) -> pd.DataFrame:
    """
    Carrega e concatena todos os parquets mensais de uma fonte que cobrem
    o intervalo [data_ini, data_fim]. Filtra pelo campo de data da fonte
    após carregar (pré-filtro por nome de arquivo, pós-filtro exato).

    Para notas_saida, usa f2_emissao. Para contas_receber, e1_emissao.
    Para movimentacao_bancaria, e5_data.
    """
    col_data = _DATA_COL.get(fonte)
    frames = []

    # Itera pelos meses que cobrem o intervalo
    d = date(data_ini.year, data_ini.month, 1)
    fim_mes = date(data_fim.year, data_fim.month, 1)
    while d <= fim_mes:
        key = _chave(fonte, d.year, d.month)
        df = _baixar_parquet(key)
        if df is not None and not df.empty:
            frames.append(df)
        d += relativedelta(months=1)

    if not frames:
        return pd.DataFrame()

    resultado = pd.concat(frames, ignore_index=True)

    # Filtro exato por data (o parquet mensal pode ter datas de outros meses
    # em casos de extração parcial ou reprocessamento)
    if col_data and col_data in resultado.columns:
        resultado[col_data] = pd.to_datetime(resultado[col_data], errors="coerce")
        resultado = resultado[
            (resultado[col_data].dt.date >= data_ini) &
            (resultado[col_data].dt.date <= data_fim)
        ]

    return resultado.reset_index(drop=True)


def carregar_todos_mensal(fonte: str, data_fim: Optional[date] = None) -> pd.DataFrame:
    """
    Carrega TODOS os parquets disponíveis para uma fonte, sem filtro de data.
    Usado pelo PMR engine para SF2 e SE1. Quando data_fim e informado, meses
    posteriores ao fim da consulta nao sao baixados.
    """
    try:
        s3 = _s3()
        prefix = f"{_PREFIX}/{fonte}/"
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=_BUCKET, Prefix=prefix)

        keys = [
            obj["Key"]
            for page in pages
            for obj in page.get("Contents", [])
            if obj["Key"].endswith(".parquet")
        ]
        if data_fim:
            limite = (data_fim.year, data_fim.month)
            keys = [
                key for key in keys
                if len(key.split("/")) >= 4
                and key.split("/")[2].isdigit()
                and key.split("/")[3][:2].isdigit()
                and (int(key.split("/")[2]), int(key.split("/")[3][:2])) <= limite
            ]
        # S3 downloads are I/O-bound; fetching independent monthly partitions
        # concurrently avoids making the first PMR request exceed the proxy timeout.
        with ThreadPoolExecutor(max_workers=min(8, max(len(keys), 1))) as pool:
            frames = [
                df for df in pool.map(_baixar_parquet, keys)
                if df is not None and not df.empty
            ]

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    except Exception as e:
        logger.error("carregar_todos_mensal %s: %s", fonte, e)
        return pd.DataFrame()


def carregar_clientes() -> pd.DataFrame:
    df = _baixar_parquet(_chave_clientes())
    return df if df is not None else pd.DataFrame()


# ─── Utilitário de backfill ────────────────────────────────────────────────────

def meses_disponiveis(fonte: str) -> list[tuple[int, int]]:
    """
    Lista todos os (ano, mes) com parquet disponível no S3 para uma fonte.
    Útil para diagnosticar lacunas no histórico antes de iniciar o PMR.
    """
    try:
        s3 = _s3()
        prefix = f"{_PREFIX}/{fonte}/"
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=_BUCKET, Prefix=prefix)

        result = []
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]  # financeiro/notas_saida/2023/01.parquet
                partes = key.replace(prefix, "").split("/")
                if len(partes) == 2 and partes[1].endswith(".parquet"):
                    try:
                        ano = int(partes[0])
                        mes = int(partes[1].replace(".parquet", ""))
                        result.append((ano, mes))
                    except ValueError:
                        pass
        return sorted(result)
    except Exception as e:
        logger.error("meses_disponiveis %s: %s", fonte, e)
        return []