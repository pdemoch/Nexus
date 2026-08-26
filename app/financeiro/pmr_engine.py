"""
=====================================================================
PMR ENGINE — cálculo do Prazo Médio de Recebimento
=====================================================================
Destino: app/financeiro/pmr_engine.py

Três métricas de PMR, todas calculadas como média ponderada por valor:
  PMR = SUM( dias_i * e1_valor_i ) / SUM( e1_valor_i )

Datas de referência:
  pmr_pagamento   → e5_data_ponderada (data de liquidação na SE5)
  pmr_vencimento  → e1_vencrea        (vencimento real, SE1)
  pmr_cond_pag    → e1_vencto         (vencimento da condição de pagamento, SE1)

Regra de negócio crítica:
  As três métricas usam APENAS notas que tenham movimentação bancária
  registrada (INNER JOIN com SE5). Notas ainda não liquidadas são
  excluídas do cálculo e do denominador.

  Motivação: comparar PMR Pagamento vs PMR Vencimento vs PMR Cond.Pag
  sobre a mesma base de notas é a única forma de o delta entre elas
  ser interpretável — se as bases diferissem, a comparação seria inválida.

Múltiplos SE5 por Chave_E5 (decisão B — data ponderada por valor):
  Um único título pode ter dois registros de liquidação no mesmo dia
  (caixa + banco, por ex.), ou em datas diferentes (pagamento parcelado).
  Em ambos os casos, usamos a média ponderada das datas pelos valores,
  o que representa a "data efetiva de recebimento" do valor total.

Cadeia de joins:
  SF2 ─────── SE1   (via Chave_F2, INNER: toda nota tem pelo menos 1 SE1)
  SE1 ─────── SA1   (via Chave_A1, LEFT: enriquece regional/nome)
  SE1 ─────── SE5   (via Chave_E5, INNER: exclui não liquidadas)
"""

import logging
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
# CORE — data ponderada dos registros SE5
# =====================================================================

def _agregar_se5(se5: pd.DataFrame) -> pd.DataFrame:
    """
    Para cada Chave_E5, calcula a data de recebimento ponderada pelos
    valores dos registros SE5 (decisão B).

    data_pond = EPOCH + Timedelta( SUM(dias_i * valor_i) / SUM(valor_i) )

    Registros com e5_valor ≤ 0 são descartados (estornos ou zeros).
    """
    if se5.empty:
        return pd.DataFrame(columns=["Chave_E5", "e5_data_pond", "e5_valor_total"])

    se5 = se5.copy()
    se5["e5_data"] = pd.to_datetime(se5["e5_data"], errors="coerce")
    se5 = se5[se5["e5_data"].notna() & (se5["e5_valor"] > 0)]

    if se5.empty:
        return pd.DataFrame(columns=["Chave_E5", "e5_data_pond", "e5_valor_total"])

    se5["e5_dias"] = (se5["e5_data"] - _EPOCH).dt.days.astype(float)

    rows = []
    for chave, g in se5.groupby("Chave_E5"):
        total_val = g["e5_valor"].sum()
        if total_val <= 0:
            continue
        dias_pond = float((g["e5_dias"] * g["e5_valor"]).sum() / total_val)
        rows.append({
            "Chave_E5":       chave,
            "e5_data_pond":   _EPOCH + pd.Timedelta(days=dias_pond),
            "e5_valor_total": total_val,
        })

    return pd.DataFrame(rows)


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
    """
    Adiciona as três colunas de dias ao DataFrame já joinado.
    Todos os dias são calculados como (data_ref - f2_emissao).days.
    """
    df = df.copy()
    df["f2_emissao"]   = pd.to_datetime(df["f2_emissao"],   errors="coerce")
    df["e5_data_pond"] = pd.to_datetime(df["e5_data_pond"], errors="coerce")
    df["e1_vencrea"]   = pd.to_datetime(df["e1_vencrea"],   errors="coerce")
    df["e1_vencto"]    = pd.to_datetime(df["e1_vencto"],    errors="coerce")

    df["dias_pagamento"]  = (df["e5_data_pond"] - df["f2_emissao"]).dt.days
    df["dias_vencimento"] = (df["e1_vencrea"]   - df["f2_emissao"]).dt.days
    df["dias_cond_pag"]   = (df["e1_vencto"]    - df["f2_emissao"]).dt.days

    # Clipa negativos a zero (pagamentos antecipados são legítimos mas
    # não devem diminuir o PMR — eles representam crédito, não atraso)
    df["dias_pagamento"]  = df["dias_pagamento"].clip(lower=0)

    return df[df["f2_emissao"].notna() & df["e5_data_pond"].notna()]


# =====================================================================
# CARREGAMENTO DOS DADOS DO S3
# =====================================================================

def _carregar_base(data_ini: date, data_fim: date,
                   segmento: str = None, regional: str = None) -> pd.DataFrame:
    """
    Monta a base analítica cruzando as quatro fontes do S3.
    Retorna apenas registros de notas liquidadas (INNER com SE5).
    Filtros opcionais: segmento e regional (aplicados após join com SA1).
    """
    sf2 = carregar_mensal("notas_saida", data_ini, data_fim)
    if sf2.empty:
        logger.warning("PMR: nenhuma nota encontrada em %s -> %s", data_ini, data_fim)
        return pd.DataFrame()

    logger.info("PMR: %d notas carregadas (%s -> %s)", len(sf2), data_ini, data_fim)

    se1 = carregar_todos_mensal("contas_receber")
    se5 = carregar_todos_mensal("movimentacao_bancaria")
    sa1 = carregar_clientes()

    if se1.empty:
        logger.warning("PMR: contas_receber vazio no S3")
        return pd.DataFrame()

    se1_f = se1[["Chave_F2", "Chave_E5", "e1_valor", "e1_vencto", "e1_vencrea", "Chave_A1"]].copy()
    se1_f = se1_f.drop_duplicates("Chave_F2")

    base = sf2.merge(se1_f, on="Chave_F2", how="inner")
    logger.info("PMR: apos join SF2->SE1: %d registros", len(base))

    if not sa1.empty:
        sa1_d = sa1[["Chave_A1", "a1_nome", "a1_cgc", "regional", "segmento"]].drop_duplicates("Chave_A1")
        base = base.merge(sa1_d, on="Chave_A1", how="left")
    else:
        for col in ["a1_nome", "a1_cgc", "regional", "segmento"]:
            base[col] = None

    # Filtros opcionais — aplicados antes do join com SE5 para reduzir volume
    if segmento:
        base = base[base["segmento"].fillna("").str.upper() == segmento.upper()]
    if regional:
        base = base[base["regional"].fillna("").str.upper() == regional.upper()]

    se5_agg = _agregar_se5(se5)
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
    """
    PMR por RAZAO SOCIAL (a1_cgc + a1_nome), paginado, ordenado por valor_total desc.
    Consolida todas as filiais/lojas do mesmo CNPJ em um único registro.
    """
    base = _carregar_base(data_ini, data_fim, segmento=segmento, regional=regional)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
                "total": 0, "clientes": []}

    rows = []
    # Agrupa por CNPJ (a1_cgc) + nome — consolida filiais do mesmo grupo
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
    sa1 = carregar_clientes()
    if sf2.empty or sa1.empty:
        return {"regionais": [], "segmentos": []}

    se1 = carregar_todos_mensal("contas_receber")
    if se1.empty:
        return {"regionais": [], "segmentos": []}

    # Correção: sf2 já possui Chave_A1. Trazemos apenas Chave_F2 de se1 para evitar colisão (_x / _y)
    se1_f = se1[["Chave_F2"]].drop_duplicates("Chave_F2")
    sa1_d = sa1[["Chave_A1", "regional", "segmento"]].drop_duplicates("Chave_A1")
    
    base = sf2.merge(se1_f, on="Chave_F2", how="inner").merge(sa1_d, on="Chave_A1", how="left")

    regionais = sorted(base["regional"].dropna().unique().tolist())
    segmentos = sorted(base["segmento"].dropna().unique().tolist())
    return {"regionais": regionais, "segmentos": segmentos}

def obter_dados_brutos(data_ini: date, data_fim: date,
                       segmento: str = None, regional: str = None) -> pd.DataFrame:
    """
    Retorna o DataFrame linha a linha para exportacao Excel.
    Inclui todas as variaveis que compoem o calculo do PMR.
    """
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
    # Apenas colunas que existem no DataFrame
    cols_existentes = [c for c in cols if c in base.columns]
    df = base[cols_existentes].copy()

    # Nomes amigáveis para o Excel
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