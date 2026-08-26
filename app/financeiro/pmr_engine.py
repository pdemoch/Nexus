"""
=====================================================================
PMR ENGINE — cálculo do Prazo Médio de Recebimento
=====================================================================
Destino: app/financeiro/pmr_engine.py

Três métricas de PMR, todas calculadas como média ponderada por valor:
  PMR = SUM( dias_i * e5_valor_i ) / SUM( e5_valor_i )

Datas de referência:
  pmr_pagamento   -> e5_data (data de liquidação na SE5)
  pmr_vencimento  -> e1_vencrea        (vencimento real, SE1)
  pmr_cond_pag    -> e1_vencto         (vencimento da condição de pagamento, SE1)

Regra de negócio crítica:
  As três métricas usam APENAS notas que tenham movimentação bancária
  registrada (INNER JOIN com SE5). Notas ainda não liquidadas são
  excluídas do cálculo e do denominador.

Cadeia de joins:
  SF2 ─── SE1   (via Chave_F2, INNER)
  SE1 ─── SA1   (via Chave_A1, LEFT: enriquece regional/nome)
  SE1 ─── SE5   (via Chave_E5, INNER: exclui não liquidadas)

FIX de colisão: SF2 tambem carrega Chave_A1; descartada antes do merge
com SE1 para nao gerar Chave_A1_x/_y e quebrar o join com SA1.
"""

import logging
import os
import threading
import time
from datetime import date
from typing import Any, Optional, Union

import numpy as np
import pandas as pd

from app.financeiro.s3_store import (
    carregar_mensal,
    carregar_todos_mensal,
    carregar_clientes,
)

logger = logging.getLogger(__name__)

_EPOCH = pd.Timestamp("1970-01-01")
_BASE_CACHE_TTL = max(float(os.getenv("PMR_CACHE_TTL_SECONDS", "300")), 0)
_BASE_CACHE: dict[tuple[date, date], tuple[float, pd.DataFrame]] = {}
_BASE_CACHE_LOCK = threading.Lock()

# Aceita filtro como string unica OU lista de strings
FiltroValor = Optional[Union[str, list]]


# =====================================================================
# CORE — data ponderada dos registros SE5
# =====================================================================

# =====================================================================
# CÁLCULO DO PMR
# =====================================================================

def _pmr(df: pd.DataFrame, col_dias: str) -> float:
    """PMR ponderado por e1_valor. Retorna 0.0 se denominador for zero."""
    total = df["e5_valor"].sum()
    if total == 0:
        return 0.0
    return float((df[col_dias] * df["e5_valor"]).sum() / total)


def _calcular_dias(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona as três colunas de dias ao DataFrame já joinado."""
    df = df.copy()
    df["f2_emissao"]   = pd.to_datetime(df["f2_emissao"],   errors="coerce")
    df["e5_data"]     = pd.to_datetime(df["e5_data"],     errors="coerce")
    df["e1_vencrea"]   = pd.to_datetime(df["e1_vencrea"],   errors="coerce")
    df["e1_vencto"]    = pd.to_datetime(df["e1_vencto"],    errors="coerce")

    df["dias_pagamento"]  = (df["e5_data"]     - df["f2_emissao"]).dt.days
    df["dias_vencimento"] = (df["e1_vencrea"]   - df["f2_emissao"]).dt.days
    df["dias_cond_pag"]   = (df["e1_vencto"]    - df["f2_emissao"]).dt.days

    df["dias_pagamento"]  = df["dias_pagamento"].clip(lower=0)

    return df[df["f2_emissao"].notna() & df["e5_data"].notna()]


def _aplicar_filtro(base: pd.DataFrame, coluna: str, valor: FiltroValor) -> pd.DataFrame:
    """
    Filtra 'base' por 'coluna' aceitando str unica OU lista de strings.
    Comparacao case-insensitive. Valor vazio/None nao filtra.
    """
    if not valor:
        return base
    col = base[coluna].fillna("").str.upper().str.strip()
    if isinstance(valor, str):
        alvos = [v.strip().upper() for v in valor.split(",") if v.strip()]
    else:
        alvos = [str(v).strip().upper() for v in valor if str(v).strip()]
    if not alvos:
        return base
    return base[col.isin(alvos)]


# =====================================================================
# CARREGAMENTO DOS DADOS DO S3
# =====================================================================

def _carregar_base_sem_filtros(data_ini: date, data_fim: date) -> pd.DataFrame:
    """
    Monta a base analítica cruzando as quatro fontes do S3.
    Retorna apenas registros de notas liquidadas (INNER com SE5).
    """
    # O período é o recebimento: notas e títulos devem ser históricos.
    sf2 = carregar_todos_mensal("notas_saida")
    if sf2.empty:
        logger.warning("PMR: nenhuma nota encontrada em %s -> %s", data_ini, data_fim)
        return pd.DataFrame()

    logger.info("PMR: %d notas carregadas (%s -> %s)", len(sf2), data_ini, data_fim)

    se1 = carregar_todos_mensal("contas_receber")
    se5 = carregar_mensal("movimentacao_bancaria", data_ini, data_fim)
    sa1 = carregar_clientes()

    if se1.empty:
        logger.warning("PMR: contas_receber vazio no S3")
        return pd.DataFrame()

    # Uma nota pode ter várias parcelas na SE1. Preserve cada Chave_E5 para
    # não perder parcelas no merge com SF2.
    se1_f = se1[["Chave_F2", "Chave_E5", "e1_num", "e1_prefixo", "e1_parcela",
                 "e1_valor", "e1_vencto", "e1_vencrea", "Chave_A1"]].copy()

    # FIX colisao: descarta Chave_A1 do SF2 antes do merge (canonica vem da SE1)
    base = sf2.drop(columns=["Chave_A1"], errors="ignore").merge(se1_f, on="Chave_F2", how="inner")
    logger.info("PMR: apos join SF2->SE1: %d registros", len(base))

    if not sa1.empty:
        sa1_d = sa1[["Chave_A1", "a1_nome", "a1_cgc", "regional", "segmento"]].drop_duplicates("Chave_A1")
        base = base.merge(sa1_d, on="Chave_A1", how="left")
    else:
        for col in ["a1_nome", "a1_cgc", "regional", "segmento"]:
            base[col] = None

    if base.empty:
        return pd.DataFrame()

    se5 = se5.copy()
    se5["e5_data"] = pd.to_datetime(se5["e5_data"], errors="coerce")
    se5["e5_valor"] = pd.to_numeric(se5["e5_valor"], errors="coerce")
    se5 = se5[se5["e5_data"].notna() & (se5["e5_valor"] > 0)]
    if se5.empty:
        logger.warning("PMR: nenhum registro de movimentacao bancaria no S3")
        return pd.DataFrame()

    pago = se5.merge(base, on="Chave_E5", how="inner", validate="many_to_one")
    logger.info("PMR: apos inner join com SE5: %d de %d notas (%d%% liquidadas)",
                len(pago), len(base), int(len(pago) / max(len(base), 1) * 100))

    return _calcular_dias(pago)


def limpar_cache() -> None:
    """Descarta bases PMR para que leituras posteriores reflitam o S3 atualizado."""
    with _BASE_CACHE_LOCK:
        _BASE_CACHE.clear()
    logger.info("PMR: cache da base analítica invalidado")


def _carregar_base(data_ini: date, data_fim: date,
                   segmento: FiltroValor = None,
                   regional: FiltroValor = None,
                   cgc: FiltroValor = None) -> pd.DataFrame:
    """Carrega a base compartilhada e aplica os filtros específicos da consulta."""
    chave = (data_ini, data_fim)
    agora = time.monotonic()

    with _BASE_CACHE_LOCK:
        entrada = _BASE_CACHE.get(chave)
        if entrada and (_BASE_CACHE_TTL == 0 or agora - entrada[0] < _BASE_CACHE_TTL):
            base = entrada[1].copy()
            logger.info("PMR: cache reutilizado para %s -> %s", data_ini, data_fim)
        else:
            base = _carregar_base_sem_filtros(data_ini, data_fim)
            _BASE_CACHE[chave] = (agora, base.copy())
            logger.info("PMR: base armazenada em cache para %s -> %s", data_ini, data_fim)

    base = _aplicar_filtro(base, "segmento", segmento)
    base = _aplicar_filtro(base, "regional", regional)
    base = _aplicar_filtro(base, "a1_cgc", cgc)
    return base


# =====================================================================
# API PÚBLICA — três granularidades
# =====================================================================

def calcular_pmr_global(data_ini: date, data_fim: date,
                        segmento: FiltroValor = None,
                        regional: FiltroValor = None,
                        cgc: FiltroValor = None) -> dict[str, Any]:
    """
    Cards de topo: PMR global nos três critérios.
    Inclui 'valor_por_dia' = valor_total / dias_periodo (impacto de 1 dia de
    PMR no capital de giro — ganho one-off de caixa por dia reduzido).
    """
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional, cgc=cgc)
    if base.empty:
        return _resposta_vazia(data_ini, data_fim)

    valor_total = float(base["e5_valor"].sum())
    dias_periodo = max((data_fim - data_ini).days, 1)
    valor_por_dia = valor_total / dias_periodo

    return {
        "periodo":        {"data_ini": str(data_ini), "data_fim": str(data_fim),
                           "dias_periodo": dias_periodo},
        "notas_base":     int(base["Chave_F2"].nunique()),
        "notas_pagas":    int(len(base)),
        "valor_total":    round(valor_total, 2),
        "valor_por_dia":  round(valor_por_dia, 2),
        "pmr_pagamento":  round(_pmr(base, "dias_pagamento"),  2),
        "pmr_vencimento": round(_pmr(base, "dias_vencimento"), 2),
        "pmr_cond_pag":   round(_pmr(base, "dias_cond_pag"),   2),
        "delta_atraso":   round(_pmr(base, "dias_pagamento") - _pmr(base, "dias_cond_pag"), 2),
    }


def calcular_pmr_regional(data_ini: date, data_fim: date,
                          segmento: FiltroValor = None,
                          regional: FiltroValor = None) -> dict[str, Any]:
    """PMR detalhado por regional."""
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "regionais": []}

    regionais = []
    for reg, g in base.groupby("regional", dropna=False):
        regionais.append({
            "regional":       str(reg) if pd.notna(reg) else "SEM REGIONAL",
            "notas_pagas":    int(len(g)),
            "valor_total":    round(float(g["e5_valor"].sum()), 2),
            "pmr_pagamento":  round(_pmr(g, "dias_pagamento"),  2),
            "pmr_vencimento": round(_pmr(g, "dias_vencimento"), 2),
            "pmr_cond_pag":   round(_pmr(g, "dias_cond_pag"),   2),
            "delta_atraso":   round(_pmr(g, "dias_pagamento") - _pmr(g, "dias_cond_pag"), 2),
        })

    regionais.sort(key=lambda r: r["valor_total"], reverse=True)
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "regionais": regionais}


def calcular_pmr_clientes(data_ini: date, data_fim: date,
                          segmento: FiltroValor = None,
                          regional: FiltroValor = None,
                          limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """
    PMR por RAZAO SOCIAL (a1_cgc + a1_nome), paginado, ordenado por valor_total desc.
    Consolida todas as filiais/lojas do mesmo CNPJ em um único registro.
    """
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
                "total": 0, "clientes": []}

    rows = []
    chave_cols = ["a1_cgc", "a1_nome"]
    for chave, g in base.fillna({"a1_cgc": "SEM_CGC", "a1_nome": "SEM_NOME"}).groupby(chave_cols, dropna=False):
        cgc, nome = chave
        reg  = g["regional"].mode().iloc[0]  if not g["regional"].isna().all() else "—"
        seg  = g["segmento"].mode().iloc[0]  if not g["segmento"].isna().all() else "—"
        rows.append({
            "cgc":            str(cgc),
            "nome":           str(nome),
            "regional":       str(reg),
            "segmento":       str(seg),
            "notas_pagas":    int(len(g)),
            "valor_total":    round(float(g["e5_valor"].sum()), 2),
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


def calcular_pmr_mensal(data_ini: date, data_fim: date,
                        segmento: FiltroValor = None,
                        regional: FiltroValor = None,
                        cgc: FiltroValor = None) -> dict[str, Any]:
    """
    Evolução mês a mês, agrupada pelo mês de recebimento (e5_data).

    Para as notas cujo pagamento caiu no mês M, calcula os três PMRs
    (todos contados a partir da emissão f2_emissao) e o valor recebido.
    Responde: "quanto de caixa entrou no mês M e com que atraso vieram
    as notas pagas nesse mês".

    ATENCAO (survivor bias): os meses mais recentes tendem a exibir PMR
    menor porque as notas de pagadores lentos ainda nao foram liquidadas.
    O campo 'em_maturacao' marca os 2 ultimos meses do recorte.
    """
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional, cgc=cgc)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": []}

    base = base.copy()
    base["mes_pag"] = base["e5_data"].dt.to_period("M")

    meses = []
    for mes, g in base.groupby("mes_pag", sort=True):
        meses.append({
            "mes":            mes.strftime("%Y-%m"),
            "notas_pagas":    int(len(g)),
            "valor_total":    round(float(g["e5_valor"].sum()), 2),
            "valor_recebido": round(float(g["e5_valor"].sum()), 2),
            "pmr_pagamento":  round(_pmr(g, "dias_pagamento"),  2),
            "pmr_vencimento": round(_pmr(g, "dias_vencimento"), 2),
            "pmr_cond_pag":   round(_pmr(g, "dias_cond_pag"),   2),
            "delta_atraso":   round(_pmr(g, "dias_pagamento") - _pmr(g, "dias_cond_pag"), 2),
        })

    meses.sort(key=lambda m: m["mes"])
    # Marca os 2 ultimos meses como em maturacao (survivor bias)
    for m in meses[-2:]:
        m["em_maturacao"] = True
    for m in meses[:-2]:
        m["em_maturacao"] = False

    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": meses}


def listar_filtros(data_ini: date, data_fim: date) -> dict[str, Any]:
    """Retorna os valores únicos de regional e segmento disponíveis no período."""
    base = _carregar_base(data_ini, data_fim)
    if base.empty:
        return {"regionais": [], "segmentos": []}

    regionais = sorted(base["regional"].dropna().unique().tolist())
    segmentos = sorted(base["segmento"].dropna().unique().tolist())
    return {"regionais": regionais, "segmentos": segmentos}


def listar_clientes_busca(data_ini: date, data_fim: date,
                          q: str = "", limit: int = 20) -> list[dict]:
    """
    Autocomplete do filtro de cliente: busca por razao social OU CNPJ.
    Retorna ate 'limit' clientes (a1_cgc + a1_nome) presentes no periodo.
    """
    base = _carregar_base(data_ini, data_fim)
    if base.empty:
        return []

    cli = (base[["a1_cgc", "a1_nome"]]
           .fillna({"a1_cgc": "", "a1_nome": ""})
           .drop_duplicates("a1_cgc"))

    if q:
        qu = q.strip().upper()
        mask = (cli["a1_cgc"].str.upper().str.contains(qu, na=False, regex=False) |
                cli["a1_nome"].str.upper().str.contains(qu, na=False, regex=False))
        cli = cli[mask]

    cli = cli.sort_values("a1_nome").head(limit)
    return [{"cgc": str(r.a1_cgc), "nome": str(r.a1_nome)} for r in cli.itertuples(index=False)]


def obter_notas_cliente(data_ini: date, data_fim: date, cgc: str) -> dict[str, Any]:
    """
    Drill-down: todas as parcelas (nivel SE1) de um cliente no periodo,
    com SF2 (NF/emissao), SE1 (titulo/vencimentos) e SE5 (pagamento).
    Ordenado por data de emissao. Uma linha por parcela.
    """
    base = _carregar_base(data_ini, data_fim, cgc=cgc)
    if base.empty:
        return {"cgc": cgc, "nome": None, "notas": []}

    base = base.sort_values("f2_emissao")
    nome = base["a1_nome"].dropna().iloc[0] if base["a1_nome"].notna().any() else None

    def _s(v):
        return "" if pd.isna(v) else str(v)

    def _d(v):
        return None if pd.isna(v) else pd.to_datetime(v).strftime("%Y-%m-%d")

    notas = []
    for r in base.itertuples(index=False):
        notas.append({
            "nf":             _s(getattr(r, "f2_doc", "")),
            "serie":          _s(getattr(r, "f2_serie", "")),
            "emissao":        _d(getattr(r, "f2_emissao", None)),
            "titulo":         _s(getattr(r, "e1_num", "")),
            "prefixo":        _s(getattr(r, "e1_prefixo", "")),
            "parcela":        _s(getattr(r, "e1_parcela", "")).strip(),
            "valor_titulo":   round(float(getattr(r, "e1_valor", 0) or 0), 2),
            "vencto_cond":    _d(getattr(r, "e1_vencto", None)),
            "vencto_real":    _d(getattr(r, "e1_vencrea", None)),
            "data_pagamento": _d(getattr(r, "e5_data", None)),
            "valor_recebido": round(float(getattr(r, "e5_valor", 0) or 0), 2),
            "dias_pagamento": int(getattr(r, "dias_pagamento", 0)),
            "dias_cond_pag":  int(getattr(r, "dias_cond_pag", 0)),
            "delta_atraso":   int(getattr(r, "dias_pagamento", 0)) - int(getattr(r, "dias_cond_pag", 0)),
        })

    return {
        "cgc":   cgc,
        "nome":  None if nome is None else str(nome),
        "total": len(notas),
        "resumo": {
            "valor_titulo":   round(float(base["e1_valor"].sum()), 2),
            "valor_recebido": round(float(base["e5_valor"].sum()), 2),
            "pmr_pagamento":  round(_pmr(base, "dias_pagamento"),  2),
            "pmr_vencimento": round(_pmr(base, "dias_vencimento"), 2),
            "pmr_cond_pag":   round(_pmr(base, "dias_cond_pag"),   2),
        },
        "notas": notas,
    }


def obter_dados_brutos(data_ini: date, data_fim: date,
                       segmento: FiltroValor = None,
                       regional: FiltroValor = None,
                       cgc: FiltroValor = None) -> pd.DataFrame:
    """DataFrame linha a linha para exportacao Excel."""
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional, cgc=cgc)
    if base.empty:
        return pd.DataFrame()

    cols = [
        "f2_filial", "f2_doc", "f2_serie", "f2_emissao", "f2_valbrut",
        "a1_cgc", "a1_nome", "regional", "segmento",
        "e1_num", "e1_parcela", "e1_valor", "e1_vencto", "e1_vencrea",
        "e5_data", "e5_valor",
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
        "e1_num":        "Num. Titulo",
        "e1_parcela":    "Parcela",
        "e1_valor":      "Valor Titulo E1 (R$)",
        "e1_vencto":     "Vencimento Cond.Pag.",
        "e1_vencrea":    "Vencimento Real",
        "e5_data":       "Data Pagamento (E5)",
        "e5_valor":      "Valor Recebido E5 (R$)",
        "dias_pagamento": "Dias PMR Pagamento",
        "dias_vencimento":"Dias PMR Vencimento",
        "dias_cond_pag":  "Dias PMR Cond.Pag.",
    }
    return df.rename(columns={k: v for k, v in rename.items() if k in df.columns})


def _resposta_vazia(data_ini: date, data_fim: date) -> dict:
    return {
        "periodo":        {"data_ini": str(data_ini), "data_fim": str(data_fim),
                           "dias_periodo": max((data_fim - data_ini).days, 1)},
        "notas_base":     0,
        "notas_pagas":    0,
        "valor_total":    0.0,
        "valor_por_dia":  0.0,
        "pmr_pagamento":  None,
        "pmr_vencimento": None,
        "pmr_cond_pag":   None,
        "delta_atraso":   None,
    }
