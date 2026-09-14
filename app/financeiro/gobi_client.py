"""
=====================================================================
GOBI CLIENT — cliente HTTP para a API Gobi (Protheus ERP)
=====================================================================
Destino: app/financeiro/gobi_client.py

Replica a lógica de extração dia a dia dos queries Power Query,
agora em Python. Três fontes com paginação diária + uma snapshot.

REPORT IDs
  595 — Notas de Saída     (SF2)
  596 — Contas a Receber   (SE1, filtrado por e1_tipo='NF ')
  592 — Movimentação Bancária (SE5)
  611 — Cadastro de Clientes  (SA1, sem filtro de data)

VARIÁVEIS DE AMBIENTE NECESSÁRIAS
  GOBI_API_URL   ex: https://gobi-api.lineaalimentos.com.br
  GOBI_API_KEY   ex: TQWZ7G4UeRzu6zvmyt4b
"""

import os
import logging
import re
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE_URL = os.getenv("GOBI_API_URL_FINANCEIRO", "https://gobi-api.lineaalimentos.com.br")
_API_KEY  = os.getenv("GOBI_TOKEN", "")
_TIMEOUT  = 30   # segundos por requisição, igual ao Power Query
_MAX_RETRY = 3   # tentativas por dia (erros transitórios de rede)


def _normalizar_numero_documento(value: object) -> str:
    """Unifica padding numérico das chaves sem alterar a coluna original."""
    text = "" if value is None else str(value).strip().upper()
    if re.fullmatch(r"0*\d+(?:\.0+)?", text):
        text = text.split(".", 1)[0]
    return text.lstrip("0") or "0" if text else ""


def _get(report_id: int, start: Optional[str] = None, end: Optional[str] = None) -> Optional[list]:
    """
    Chama GET /v1/reports/{id}/data com start_date e end_date.
    Retorna a lista de registros ou None se falhar após retries.
    """
    url = f"{_BASE_URL}/v1/reports/{report_id}/data"
    params = {"streaming": "true", "format": "json"}
    if start is not None:
        params["start_date"] = start
    if end is not None:
        params["end_date"] = end
    headers = {"Authorization": _API_KEY}

    for tentativa in range(1, _MAX_RETRY + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else None
        except Exception as e:
            if tentativa < _MAX_RETRY:
                time.sleep(2 ** tentativa)
            else:
                logger.warning("Gobi report %d %s→%s falhou após %d tentativas: %s",
                               report_id, start, end, _MAX_RETRY, e)
                return None


def _iterar_dias(data_ini: date, data_fim: date, report_id: int) -> list[dict]:
    """
    Itera dia a dia no intervalo [data_ini, data_fim], acumula todos os
    registros em uma lista plana. Mesmo comportamento do Power Query.
    """
    registros = []
    d = data_ini
    while d <= data_fim:
        ds = d.strftime("%Y-%m-%d")
        lote = _get(report_id, ds, ds)
        if lote:
            registros.extend(lote)
        d += timedelta(days=1)
    return registros


# ─── Notas de Saída (SF2 / report 595) ───────────────────────────────────────
def extrair_notas_saida(data_ini: date, data_fim: date) -> pd.DataFrame:
    """
    Campos retornados pela API:
      f2_filial, f2_doc, f2_serie, f2_cliente, f2_loja, f2_emissao, f2_valbrut

    Chaves derivadas (mesma lógica Power Query):
      Chave_A1  = f2_cliente + "-" + f2_loja
      Chave_F2  = f2_filial + "-" + f2_doc + "-" + f2_serie + "-" + f2_cliente + "-" + f2_loja

    Observação: os campos chegam da API já com o padding do Protheus
    (ex: f2_filial="0107", f2_doc="000045248") — nenhum zero-pad adicional.
    """
    registros = _iterar_dias(data_ini, data_fim, 595)
    if not registros:
        return pd.DataFrame()

    df = pd.DataFrame(registros)
    df = df.astype(str)   # garante concatenação correta mesmo com tipos mistos

    df["Chave_A1"] = df["f2_cliente"] + "-" + df["f2_loja"]
    df["_chave_doc"] = df["f2_doc"].map(_normalizar_numero_documento)
    df["Chave_F2"] = (df["f2_filial"] + "-" + df["_chave_doc"] + "-" + df["f2_serie"]
                      + "-" + df["f2_cliente"] + "-" + df["f2_loja"])

    df["f2_emissao"]  = pd.to_datetime(df["f2_emissao"],  errors="coerce")
    df["f2_valbrut"]  = pd.to_numeric(df["f2_valbrut"],   errors="coerce")

    # Deduplicação: cada nota deve aparecer uma única vez.
    df = df.drop_duplicates("Chave_F2")
    return df.reset_index(drop=True)


# ─── Contas a Receber (SE1 / report 596) ─────────────────────────────────────
def extrair_contas_receber(data_ini: date, data_fim: date) -> pd.DataFrame:
    """
    Campos retornados:
      e1_filial, e1_prefixo, e1_num, e1_parcela, e1_tipo,
      e1_cliente, e1_loja, e1_valor, e1_emissao, e1_vencto, e1_vencrea

    Chaves derivadas:
      Chave_A1  = e1_cliente + "-" + e1_loja
      Chave_F2  = e1_filial + "-" + e1_num + "-" + e1_prefixo + "-" + e1_cliente + "-" + e1_loja
      Chave_E5  = e1_filial + "-" + e1_num + "-" + e1_prefixo + "-" + e1_parcela + "-"
                  + e1_tipo + "-" + e1_cliente + "-" + e1_loja

    Parcela nula: o Protheus armazena parcela como campo de 3 chars padded com
    espaços ("   "). Quando nula na API, convertemos para "   " para manter o
    join com SE5 (que vem com o mesmo padrão). Mesma lógica do Power Query.

    Filtro: apenas e1_tipo.strip() == "NF" (notas fiscais).
    """
    registros = _iterar_dias(data_ini, data_fim, 596)
    if not registros:
        return pd.DataFrame()

    df = pd.DataFrame(registros)
    df = df.astype(str)

    # e1_parcela: preserva string original (já padded pelo Protheus);
    # se vier como "nan" (pandas NaN convertido), usa "   " (3 espaços)
    df["e1_parcela"] = df["e1_parcela"].replace("nan", "   ")

    df["Chave_A1"] = df["e1_cliente"] + "-" + df["e1_loja"]
    df["_chave_doc"] = df["e1_num"].map(_normalizar_numero_documento)
    df["Chave_F2"] = (df["e1_filial"] + "-" + df["_chave_doc"] + "-" + df["e1_prefixo"]
                      + "-" + df["e1_cliente"] + "-" + df["e1_loja"])
    df["Chave_E5"] = (df["e1_filial"] + "-" + df["_chave_doc"] + "-" + df["e1_prefixo"]
                      + "-" + df["e1_parcela"] + "-" + df["e1_tipo"]
                      + "-" + df["e1_cliente"] + "-" + df["e1_loja"])

    df["e1_valor"]  = pd.to_numeric(df["e1_valor"],  errors="coerce")
    df["e1_emissao"]= pd.to_datetime(df["e1_emissao"],errors="coerce")
    df["e1_vencto"] = pd.to_datetime(df["e1_vencto"], errors="coerce")
    df["e1_vencrea"]= pd.to_datetime(df["e1_vencrea"],errors="coerce")

    # Filtro: apenas notas fiscais
    df = df[df["e1_tipo"].str.strip() == "NF"]
    # A mesma NF pode possuir várias parcelas; Chave_F2 não inclui parcela.
    # A unidade do título é Chave_E5, que preserva cada recebimento/parcelamento.
    df = df.drop_duplicates("Chave_E5")
    return df.reset_index(drop=True)


# ─── Movimentação Bancária (SE5 / report 592) ─────────────────────────────────
def extrair_movimentacao_bancaria(data_ini: date, data_fim: date) -> pd.DataFrame:
    """
    Campos retornados:
      e5_filial, e5_prefixo, e5_numero, e5_parcela, e5_tipo,
      e5_cliente, e5_loja, e5_valor, e5_data, e5_motbx, e5_vldesco

    e5_valor líquido = e5_valor - e5_vldesco (descontos concedidos).
    Mesmo tratamento do Power Query.

    Chaves derivadas:
      Chave_A1  = e5_cliente + "-" + e5_loja
      Chave_E5  = e5_filial + "-" + e5_numero + "-" + e5_prefixo + "-" + e5_parcela
                  + "-" + e5_tipo + "-" + e5_cliente + "-" + e5_loja

    NÃO deduplica por Chave_E5 aqui: múltiplos E5 por chave são legítimos
    (pagamentos parciais em datas diferentes). A agregação (data ponderada)
    acontece no pmr_engine.py.
    """
    registros = _iterar_dias(data_ini, data_fim, 592)
    if not registros:
        return pd.DataFrame()

    df = pd.DataFrame(registros)
    df = df.astype(str)

    df["e5_parcela"] = df["e5_parcela"].replace("nan", "   ")

    df["Chave_A1"] = df["e5_cliente"] + "-" + df["e5_loja"]
    # E5 usa e5_fornece para contas a pagar e e5_cliente para receber.
    # Canonicalizar permite ao PMP filtrar CLIFOR sem alterar o PMR.
    if "e5_clifor" not in df:
        df["e5_clifor"] = (df["e5_fornece"] if "e5_fornece" in df
                           else df["e5_cliente"])
    df["_chave_doc"] = df["e5_numero"].map(_normalizar_numero_documento)
    df["Chave_E5"] = (df["e5_filial"] + "-" + df["_chave_doc"] + "-" + df["e5_prefixo"]
                      + "-" + df["e5_parcela"] + "-" + df["e5_tipo"]
                      + "-" + df["e5_cliente"] + "-" + df["e5_loja"])

    df["e5_data"]   = pd.to_datetime(df["e5_data"],   errors="coerce")
    df["e5_valor"]  = pd.to_numeric(df["e5_valor"],   errors="coerce")
    df["e5_vldesco"]= pd.to_numeric(df["e5_vldesco"], errors="coerce").fillna(0)

    # Valor líquido (mesmo cálculo do Power Query)
    df["e5_valor"]  = df["e5_valor"] - df["e5_vldesco"]
    df = df.drop(columns=["e5_vldesco"])

    # Remove registros com data ou valor inválido
    df = df[df["e5_data"].notna() & (df["e5_valor"] > 0)]
    return df.reset_index(drop=True)


# ─── Cadastro de Clientes (SA1 / report 611) ──────────────────────────────────
def extrair_clientes() -> pd.DataFrame:
    """
    Extract completo sem filtro de data (snapshot).
    Chave_A1 = a1_cod + "-" + a1_loja
    """
    registros = _get(611)   # report sem parâmetro de data
    if not registros:
        # Fallback: alguns reports aceitam datas vazias, outros não. Tenta sem query string.
        try:
            url = f"{_BASE_URL}/v1/reports/611/data"
            r = requests.get(url, params={"streaming": "true", "format": "json"},
                             headers={"Authorization": _API_KEY}, timeout=60)
            r.raise_for_status()
            registros = r.json()
        except Exception as e:
            logger.error("Falha ao extrair clientes: %s", e)
            return pd.DataFrame()

    df = pd.DataFrame(registros)
    df = df.astype(str)
    df["Chave_A1"] = df["a1_cod"] + "-" + df["a1_loja"]
    df = df.drop_duplicates("Chave_A1")
    return df.reset_index(drop=True)


# ─── Contas a pagar / fornecedores (PMP) ─────────────────────────────────────
def extrair_notas_entrada(data_ini: date, data_fim: date) -> pd.DataFrame:
    """Extrai SF1 (589) e SD1 itemizado, sem repetir f1_valbrut por item."""
    registros = _iterar_dias(data_ini, data_fim, 589)
    if not registros:
        return pd.DataFrame()
    df = pd.DataFrame(registros).astype(str)
    if "f1_emissao" in df:
        df["f1_emissao"] = pd.to_datetime(df["f1_emissao"], errors="coerce")
    if "f1_valbrut" in df:
        df["f1_valbrut"] = pd.to_numeric(df["f1_valbrut"], errors="coerce").fillna(0)
    chave = [c for c in ("f1_filial", "f1_doc", "f1_serie", "f1_fornece", "f1_loja") if c in df]
    if not chave:
        return df.reset_index(drop=True)
    if not any(c.startswith("d1_") for c in df.columns):
        return df.drop_duplicates(chave).reset_index(drop=True)
    # SF1 valor bruto fica em uma única linha por NF; SD1 permanece itemizado.
    df["_nf_ordem"] = df.groupby(chave, dropna=False).cumcount()
    df.loc[df["_nf_ordem"] > 0, "f1_valbrut"] = 0
    df = df.drop(columns=["_nf_ordem"])
    itens = df
    itens["d1_quant"] = pd.to_numeric(itens.get("d1_quant"), errors="coerce").fillna(0)
    itens["d1_vunit"] = pd.to_numeric(itens.get("d1_vunit"), errors="coerce").fillna(0)
    itens["d1_total"] = pd.to_numeric(itens.get("d1_total"), errors="coerce")
    itens["d1_total"] = itens["d1_total"].fillna(itens["d1_quant"] * itens["d1_vunit"])
    # O cabeçalho é a fonte exclusiva do valor bruto; itens só detalham a NF.
    return itens.reset_index(drop=True)


def extrair_contas_pagar(data_ini: date, data_fim: date) -> pd.DataFrame:
    registros = _iterar_dias(data_ini, data_fim, 590)
    if not registros:
        return pd.DataFrame()
    df = pd.DataFrame(registros).astype(str)
    for col in ("e2_valor", "e2_saldo"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("e2_emissao", "e2_vencto", "e2_vencrea"):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    df["clifor"] = (df["e2_fornece"] if "e2_fornece" in df
                    else pd.Series("", index=df.index)).astype(str).str.strip()
    return df.reset_index(drop=True)


def extrair_fornecedores() -> pd.DataFrame:
    registros = _get(614) or []
    if not registros:
        return pd.DataFrame()
    df = pd.DataFrame(registros).astype(str)
    df["clifor"] = df.get("a2_cod", df.get("a2_fornece", "")).astype(str).str.strip()
    if "a2_loja" in df:
        df["clifor"] = df["clifor"].str.strip()
    return df.drop_duplicates("clifor").reset_index(drop=True)


def _extrair_snapshot(report_id: int) -> pd.DataFrame:
    """Baixa um report sem janela de datas (snapshot integral)."""
    registros = _get(report_id) or []
    if not registros:
        return pd.DataFrame()
    return pd.DataFrame(registros).reset_index(drop=True)


def extrair_sb1() -> pd.DataFrame:
    """Snapshot do cadastro de produtos (SB1, report Gobi 152)."""
    return _extrair_snapshot(152)


def extrair_sg1() -> pd.DataFrame:
    """Snapshot da estrutura/composição de produtos / lista técnica (SG1, report Gobi 591)."""
    return _extrair_snapshot(591)


# Nomes descritivos mantidos como API pública para o pipeline.
extrair_produtos = extrair_sb1
extrair_estrutura_produtos = extrair_sg1