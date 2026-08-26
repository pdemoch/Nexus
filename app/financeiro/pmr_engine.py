"""
=====================================================================
PMR ENGINE — cálculo do Prazo Médio de Recebimento
=====================================================================
Destino: app/financeiro/pmr_engine.py

Três métricas de PMR, todas calculadas como média ponderada por valor:
  PMR = SUM( dias_i * e1_valor_i ) / SUM( e1_valor_i )

Datas de referência:
  pmr_pagamento   -> e5_data_ponderada (data de liquidação na SE5)
  pmr_vencimento  -> e1_vencrea        (vencimento real, SE1)
  pmr_cond_pag    -> e1_vencto         (vencimento da condição de pagamento, SE1)

Regra de negócio crítica:
  As três métricas usam APENAS notas que tenham movimentação bancária
  registrada (INNER JOIN com SE5). Notas ainda não liquidadas são
  excluídas do cálculo e do denominador.

Múltiplos SE5 por Chave_E5 (decisão B — data ponderada por valor):
  Um único título pode ter dois registros de liquidação no mesmo dia
  ou em datas diferentes. Usamos a média ponderada das datas pelos
  valores, representando a "data efetiva de recebimento".

Cadeia de joins:
  SF2 ------- SE1   (via Chave_F2, INNER)
  SE1 ------- SA1   (via Chave_A1, LEFT)
  SE1 ------- SE5   (via Chave_E5, INNER: exclui não liquidadas)

=====================================================================
PERFORMANCE (2 correções)
=====================================================================
1. _agregar_se5 VETORIZADO: o loop Python grupo-a-grupo sobre ~406k
   linhas custava ~140s. groupby().agg() roda em <1s, idêntico.

2. CACHE EM MEMÓRIA de SE1, SE5-agregado e SA1: carregam TODO o
   histórico do S3 (~9s) e só mudam quando o pipeline roda. Cache no
   processo, invalidado por invalidar_cache() (chamada pelo router
   após executar_pipeline). Cold: ~10s. Warm: <0.1s.
"""

import logging
import threading
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.financeiro.s3_store import (
    carregar_mensal,
    carregar_todos_mensal,
    carregar_clientes,
)

logger = logging.getLogger(__name__)

_EPOCH = pd.Timestamp("1970-01-01")


# =====================================================================
# CACHE EM MEMÓRIA — SE1, SE5 agregado, SA1
# =====================================================================

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"se1": None, "se5_agg": None, "sa1": None}


def invalidar_cache() -> None:
    """
    Zera o cache de SE1/SE5/SA1. DEVE ser chamada após o pipeline
    financeiro atualizar os parquets no S3, senão as telas servem
    dados desatualizados até o restart do processo.
    """
    with _cache_lock:
        _cache["se1"] = None
        _cache["se5_agg"] = None
        _cache["sa1"] = None
    logger.info("PMR cache: SE1/SE5_agg/SA1 invalidados.")


def _get_se1() -> pd.DataFrame:
    """SE1 (contas a receber) completo, com cache."""
    with _cache_lock:
        if _cache["se1"] is None:
            logger.info("PMR cache: carregando SE1 do S3 (cold)...")
            _cache["se1"] = carregar_todos_mensal("contas_receber")
        return _cache["se1"]


def _get_se5_agg() -> pd.DataFrame:
    """
    SE5 (movimentação bancária) já AGREGADO por Chave_E5, com cache.
    A agregação é o passo mais caro; cacheá-la evita recalcular.
    """
    with _cache_lock:
        if _cache["se5_agg"] is None:
            logger.info("PMR cache: carregando + agregando SE5 do S3 (cold)...")
            se5_raw = carregar_todos_mensal("movimentacao_bancaria")
            _cache["se5_agg"] = _agregar_se5(se5_raw)
        return _cache["se5_agg"]


def _get_sa1() -> pd.DataFrame:
    """SA1 (cadastro de clientes) completo, com cache."""
    with _cache_lock:
        if _cache["sa1"] is None:
            logger.info("PMR cache: carregando SA1 do S3 (cold)...")
            _cache["sa1"] = carregar_clientes()
        return _cache["sa1"]


# =====================================================================
# CORE — data ponderada dos registros SE5  (VETORIZADO)
# =====================================================================

def _agregar_se5(se5: pd.DataFrame) -> pd.DataFrame:
    """
    Para cada Chave_E5, calcula a data de recebimento ponderada pelos
    valores dos registros SE5 (decisão B).

    data_pond = EPOCH + Timedelta( SUM(dias_i * valor_i) / SUM(valor_i) )

    Registros com e5_valor <= 0 são descartados.

    VETORIZADO: groupby().agg() em vez de loop Python. ~406k linhas em
    <1s (antes ~140s). Resultado numérico idêntico.
    """
    cols_vazio = ["Chave_E5", "e5_data_pond", "e5_valor_total"]
    if se5.empty:
        return pd.DataFrame(columns=cols_vazio)

    se5 = se5.copy()
    se5["e5_data"] = pd.to_datetime(se5["e5_data"], errors="coerce")
    se5 = se5[se5["e5_data"].notna() & (se5["e5_valor"] > 0)]

    if se5.empty:
        return pd.DataFrame(columns=cols_vazio)

    se5["e5_dias"] = (se5["e5_data"] - _EPOCH).dt.days.astype(float)
    se5["_num_pond"] = se5["e5_dias"] * se5["e5_valor"]

    agg = (
        se5.groupby("Chave_E5", sort=False)
           .agg(_num_sum=("_num_pond", "sum"),
                e5_valor_total=("e5_valor", "sum"))
           .reset_index()
    )

    agg = agg[agg["e5_valor_total"] > 0].copy()
    if agg.empty:
        return pd.DataFrame(columns=cols_vazio)

    dias_pond = agg["_num_sum"] / agg["e5_valor_total"]
    agg["e5_data_pond"] = _EPOCH + pd.to_timedelta(dias_pond, unit="D")

    return agg[["Chave_E5", "e5_data_pond", "e5_valor_total"]]


# =====================================================================
# CÁLCULO DO PMR
# =====================================================================

def _pmr(df: pd.DataFrame, col_dias: str) -> float:
    """PMR ponderado por e1_valor. Retorna 0.0 se denominador for zero."""
    total = df["e1_valor"].sum()
    if total == 0:
        return 0.0
    return float((df[col_dias] * df["e1_valor"]).sum() / total)


def _calcular_dias(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona as três colunas de dias ao DataFrame já joinado."""
    df = df.copy()
    df["f2_emissao"]   = pd.to_datetime(df["f2_emissao"],   errors="coerce")
    df["e5_data_pond"] = pd.to_datetime(df["e5_data_pond"], errors="coerce")
    df["e1_vencrea"]   = pd.to_datetime(df["e1_vencrea"],   errors="coerce")
    df["e1_vencto"]    = pd.to_datetime(df["e1_vencto"],    errors="coerce")

    df["dias_pagamento"]  = (df["e5_data_pond"] - df["f2_emissao"]).dt.days
    df["dias_vencimento"] = (df["e1_vencrea"]   - df["f2_emissao"]).dt.days
    df["dias_cond_pag"]   = (df["e1_vencto"]    - df["f2_emissao"]).dt.days

    df["dias_pagamento"]  = df["dias_pagamento"].clip(lower=0)

    return df[df["f2_emissao"].notna() & df["e5_data_pond"].notna()]


# =====================================================================
# CARREGAMENTO DOS DADOS DO S3
# =====================================================================

def _carregar_base(data_ini: date, data_fim: date,
                   segmento: str = None, regional: str = None) -> pd.DataFrame:
    """
    Monta a base analítica cruzando as quatro fontes.
    SF2 é period-specific (carregado direto). SE1/SE5_agg/SA1 vêm do cache.
    """
    sf2 = carregar_mensal("notas_saida", data_ini, data_fim)
    if sf2.empty:
        logger.warning("PMR: nenhuma nota encontrada em %s -> %s", data_ini, data_fim)
        return pd.DataFrame()

    logger.info("PMR: %d notas carregadas (%s -> %s)", len(sf2), data_ini, data_fim)

    se1 = _get_se1()
    sa1 = _get_sa1()

    if se1.empty:
        logger.warning("PMR: contas_receber vazio no S3")
        return pd.DataFrame()

    se1_f = se1[["Chave_F2", "Chave_E5", "e1_valor", "e1_vencto", "e1_vencrea", "Chave_A1"]].copy()
    se1_f = se1_f.drop_duplicates("Chave_F2")

    # drop Chave_A1 do SF2 antes do merge: SE1 já traz Chave_A1 e a colisão
    # geraria Chave_A1_x / Chave_A1_y, quebrando o merge seguinte com SA1
    base = sf2.drop(columns=["Chave_A1"], errors="ignore").merge(se1_f, on="Chave_F2", how="inner")
    logger.info("PMR: apos join SF2->SE1: %d registros", len(base))

    if not sa1.empty:
        sa1_d = sa1[["Chave_A1", "a1_nome", "a1_cgc", "regional", "segmento"]].drop_duplicates("Chave_A1")
        base = base.merge(sa1_d, on="Chave_A1", how="left")
    else:
        for col in ["a1_nome", "a1_cgc", "regional", "segmento"]:
            base[col] = None

    if segmento:
        base = base[base["segmento"].fillna("").str.upper() == segmento.upper()]
    if regional:
        base = base[base["regional"].fillna("").str.upper() == regional.upper()]

    se5_agg = _get_se5_agg()
    if se5_agg.empty:
        logger.warning("PMR: nenhum registro de movimentacao bancaria no S3")
        return pd.DataFrame()

    pago = base.merge(se5_agg, on="Chave_E5", how="inner")
    logger.info("PMR: apos inner join com SE5: %d de %d notas (%d%% liquidadas)",
                len(pago), len(base), int(len(pago) / max(len(base), 1) * 100))

    return _calcular_dias(pago)


# =====================================================================
# API PÚBLICA — três granularidades
# =====================================================================

def calcular_pmr_global(data_ini: date, data_fim: date,
                        segmento: str = None, regional: str = None) -> dict[str, Any]:
    """Cards de topo: PMR global nos três critérios."""
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional)
    if base.empty:
        return _resposta_vazia(data_ini, data_fim)

    return {
        "periodo":        {"data_ini": str(data_ini), "data_fim": str(data_fim)},
        "notas_base":     int(base["Chave_F2"].nunique()),
        "notas_pagas":    int(len(base)),
        "valor_total":    round(float(base["e1_valor"].sum()), 2),
        "pmr_pagamento":  round(_pmr(base, "dias_pagamento"),  2),
        "pmr_vencimento": round(_pmr(base, "dias_vencimento"), 2),
        "pmr_cond_pag":   round(_pmr(base, "dias_cond_pag"),   2),
        "delta_atraso":   round(_pmr(base, "dias_pagamento") - _pmr(base, "dias_cond_pag"), 2),
    }


def calcular_pmr_regional(data_ini: date, data_fim: date,
                          segmento: str = None) -> dict[str, Any]:
    """PMR detalhado por regional."""
    base = _carregar_base(data_ini, data_fim, segmento=segmento)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "regionais": []}

    regionais = []
    for reg, g in base.groupby("regional", dropna=False):
        regionais.append({
            "regional":       str(reg) if pd.notna(reg) else "SEM REGIONAL",
            "notas_pagas":    int(len(g)),
            "valor_total":    round(float(g["e1_valor"].sum()), 2),
            "pmr_pagamento":  round(_pmr(g, "dias_pagamento"),  2),
            "pmr_vencimento": round(_pmr(g, "dias_vencimento"), 2),
            "pmr_cond_pag":   round(_pmr(g, "dias_cond_pag"),   2),
            "delta_atraso":   round(_pmr(g, "dias_pagamento") - _pmr(g, "dias_cond_pag"), 2),
        })

    regionais.sort(key=lambda r: r["valor_total"], reverse=True)
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "regionais": regionais}


def calcular_pmr_clientes(data_ini: date, data_fim: date,
                          segmento: str = None, regional: str = None,
                          limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """PMR por RAZAO SOCIAL (a1_cgc + a1_nome), paginado, ordenado por valor desc."""
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
                "total": 0, "clientes": []}

    rows = []
    chave_cols = ["a1_cgc", "a1_nome"]
    for chave, g in base.fillna({"a1_cgc": "SEM_CGC", "a1_nome": "SEM_NOME"}).groupby(chave_cols, dropna=False):
        cgc, nome = chave
        reg  = g["regional"].mode().iloc[0]  if not g["regional"].isna().all() else "-"
        seg  = g["segmento"].mode().iloc[0]  if not g["segmento"].isna().all() else "-"
        rows.append({
            "cgc":            str(cgc),
            "nome":           str(nome),
            "regional":       str(reg),
            "segmento":       str(seg),
            "notas_pagas":    int(len(g)),
            "valor_total":    round(float(g["e1_valor"].sum()), 2),
            "pmr_pagamento":  round(_pmr(g, "dias_pagamento"),  2),
            "pmr_vencimento": round(_pmr(g, "dias_vencimento"), 2),
            "pmr_cond_pag":   round(_pmr(g, "dias_cond_pag"),   2),
            "delta_atraso":   round(_pmr(g, "dias_pagamento") - _pmr(g, "dias_cond_pag"), 2),
        })

    rows.sort(key=lambda r: r["valor_total"], reverse=True)
    total = len(rows)
    return {
        "periodo":  {"data_ini": str(data_ini), "data_fim": str(data_fim)},
        "total":    total,
        "clientes": rows[offset: offset + limit],
    }


def listar_filtros(data_ini: date, data_fim: date) -> dict[str, Any]:
    """Retorna os valores únicos de regional e segmento disponíveis no período."""
    sf2 = carregar_mensal("notas_saida", data_ini, data_fim)
    sa1 = _get_sa1()
    if sf2.empty or sa1.empty:
        return {"regionais": [], "segmentos": []}

    se1 = _get_se1()
    if se1.empty:
        return {"regionais": [], "segmentos": []}

    se1_f = se1[["Chave_F2", "Chave_A1"]].drop_duplicates("Chave_F2")
    sa1_d = sa1[["Chave_A1", "regional", "segmento"]].drop_duplicates("Chave_A1")
    base  = (sf2.drop(columns=["Chave_A1"], errors="ignore")
                .merge(se1_f, on="Chave_F2", how="inner")
                .merge(sa1_d, on="Chave_A1", how="left"))

    regionais = sorted(base["regional"].dropna().unique().tolist())
    segmentos = sorted(base["segmento"].dropna().unique().tolist())
    return {"regionais": regionais, "segmentos": segmentos}


def obter_dados_brutos(data_ini: date, data_fim: date,
                       segmento: str = None, regional: str = None) -> pd.DataFrame:
    """Retorna o DataFrame linha a linha para exportacao Excel."""
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional)
    if base.empty:
        return pd.DataFrame()

    cols = [
        "f2_filial", "f2_doc", "f2_serie", "f2_emissao", "f2_valbrut",
        "a1_cgc", "a1_nome", "regional", "segmento",
        "e1_valor", "e1_vencto", "e1_vencrea",
        "e5_data_pond", "e5_valor_total",
        "dias_pagamento", "dias_vencimento", "dias_cond_pag",
    ]
    cols_existentes = [c for c in cols if c in base.columns]
    df = base[cols_existentes].copy()

    rename = {
        "f2_filial":     "Filial",
        "f2_doc":        "Num. NF",
        "f2_serie":      "Serie",
        "f2_emissao":    "Data Emissao",
        "f2_valbrut":    "Valor Bruto NF (R$)",
        "a1_cgc":        "CNPJ",
        "a1_nome":       "Razao Social",
        "regional":      "Regional",
        "segmento":      "Segmento",
        "e1_valor":      "Valor Titulo (R$)",
        "e1_vencto":     "Vencimento Cond.Pag.",
        "e1_vencrea":    "Vencimento Real",
        "e5_data_pond":  "Data Pagamento (ponderada)",
        "e5_valor_total":"Valor Recebido (R$)",
        "dias_pagamento": "Dias PMR Pagamento",
        "dias_vencimento":"Dias PMR Vencimento",
        "dias_cond_pag":  "Dias PMR Cond.Pag.",
    }
    return df.rename(columns={k: v for k, v in rename.items() if k in df.columns})


def _resposta_vazia(data_ini: date, data_fim: date) -> dict:
    return {
        "periodo":        {"data_ini": str(data_ini), "data_fim": str(data_fim)},
        "notas_base":     0,
        "notas_pagas":    0,
        "valor_total":    0.0,
        "pmr_pagamento":  None,
        "pmr_vencimento": None,
        "pmr_cond_pag":   None,
    }
