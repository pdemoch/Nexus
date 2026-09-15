"""Materialização auditável das bases financeiras no PostgreSQL.

O S3 continua sendo a fonte bruta. Estas tabelas são uma camada de consulta
derivada, reconstruível e idempotente a partir dos Parquets.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Callable

import pandas as pd
from sqlalchemy import text

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS financeiro_materializado (
    fonte VARCHAR(12) NOT NULL,
    data_ini DATE NOT NULL,
    data_fim DATE NOT NULL,
    chave_linha VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    materializado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (fonte, data_ini, data_fim, chave_linha)
);
CREATE INDEX IF NOT EXISTS ix_fin_mat_periodo
    ON financeiro_materializado (fonte, data_ini, data_fim);

CREATE TABLE IF NOT EXISTS financeiro_chaves_auditoria (
    id BIGSERIAL PRIMARY KEY,
    fonte VARCHAR(12) NOT NULL,
    data_ini DATE NOT NULL,
    data_fim DATE NOT NULL,
    chave VARCHAR(500) NOT NULL,
    ocorrencias INTEGER NOT NULL,
    acao VARCHAR(32) NOT NULL,
    auditado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_fin_chaves_auditoria
    ON financeiro_chaves_auditoria (fonte, data_ini, data_fim);
"""


def ensure_tables() -> None:
    with SessionLocal.begin() as db:
        for statement in _DDL.split(";"):
            if statement.strip():
                db.execute(text(statement))


def _serializable(value):
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _materialize_dataframe(
    fonte: str,
    data_ini: date,
    data_fim: date,
    base: pd.DataFrame,
    key_columns: list[str],
) -> dict:
    ensure_tables()
    if base.empty:
        return {"fonte": fonte, "linhas": 0, "duplicidades": 0}

    frame = base.copy()
    available = [column for column in key_columns if column in frame.columns]
    if not available:
        raise RuntimeError(f"Materialização {fonte} sem colunas de chave.")

    frame["_chave_base"] = frame[available].fillna("").astype(str).agg("|".join, axis=1)
    duplicados = frame[frame.duplicated("_chave_base", keep=False)]
    audit_rows = (
        duplicados.groupby("_chave_base", as_index=False)
        .size()
        .rename(columns={"size": "ocorrencias"})
    )
    frame["_ordem_chave"] = frame.groupby("_chave_base").cumcount()
    frame["_chave_linha"] = frame.apply(
        lambda row: hashlib.sha256(
            f"{row['_chave_base']}|{row['_ordem_chave']}".encode("utf-8")
        ).hexdigest(),
        axis=1,
    )

    rows = []
    for _, row in frame.iterrows():
        payload = {
            column: _serializable(value)
            for column, value in row.items()
            if not column.startswith("_")
        }
        rows.append({
            "fonte": fonte,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "chave_linha": row["_chave_linha"],
            "payload": json.dumps(payload, ensure_ascii=True),
        })

    with SessionLocal.begin() as db:
        db.execute(
            text("""
                DELETE FROM financeiro_materializado
                WHERE fonte = :fonte AND data_ini = :data_ini AND data_fim = :data_fim
            """),
            {"fonte": fonte, "data_ini": data_ini, "data_fim": data_fim},
        )
        db.execute(
            text("""
                INSERT INTO financeiro_materializado
                    (fonte, data_ini, data_fim, chave_linha, payload)
                VALUES (:fonte, :data_ini, :data_fim, :chave_linha, CAST(:payload AS jsonb))
            """),
            rows,
        )
        if not audit_rows.empty:
            db.execute(
                text("""
                    DELETE FROM financeiro_chaves_auditoria
                    WHERE fonte = :fonte AND data_ini = :data_ini AND data_fim = :data_fim
                """),
                {"fonte": fonte, "data_ini": data_ini, "data_fim": data_fim},
            )
            db.execute(
                text("""
                    INSERT INTO financeiro_chaves_auditoria
                        (fonte, data_ini, data_fim, chave, ocorrencias, acao)
                    VALUES (:fonte, :data_ini, :data_fim, :chave, :ocorrencias, :acao)
                """),
                [
                    {
                        "fonte": fonte,
                        "data_ini": data_ini,
                        "data_fim": data_fim,
                        "chave": str(row["_chave_base"])[:500],
                        "ocorrencias": int(row["ocorrencias"]),
                        "acao": "PRESERVADA",
                    }
                    for _, row in audit_rows.iterrows()
                ],
            )

    return {
        "fonte": fonte,
        "linhas": len(rows),
        "duplicidades": int(len(audit_rows)),
    }


def carregar_materializado(
    fonte: str, data_ini: date, data_fim: date
) -> pd.DataFrame:
    """Retorna a base derivada ou DataFrame vazio se ainda não materializada."""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                text("""
                    WITH periodo AS (
                        SELECT data_ini, data_fim
                        FROM financeiro_materializado
                        WHERE fonte = :fonte
                          AND data_ini <= :data_ini
                          AND data_fim >= :data_fim
                        ORDER BY data_fim DESC, data_ini DESC
                        LIMIT 1
                    )
                    SELECT m.payload
                    FROM financeiro_materializado m
                    JOIN periodo p
                      ON p.data_ini = m.data_ini AND p.data_fim = m.data_fim
                    WHERE m.fonte = :fonte
                """),
                {"fonte": fonte, "data_ini": data_ini, "data_fim": data_fim},
            ).scalars().all()
        return pd.DataFrame([dict(row) for row in rows]) if rows else pd.DataFrame()
    except Exception:
        logger.info("Materialização %s ainda indisponível; usando S3.", fonte)
        return pd.DataFrame()


def materializar_financeiro(
    data_ini: date,
    data_fim: date,
    log_callback: Callable[[str], None] = logger.info,
) -> None:
    """Reconstrói PMR/PMP derivados e registra duplicidades sem descartá-las."""
    from app.financeiro import pmr_engine, pmp_engine

    pmp_engine._base_cached_s3.cache_clear()
    pmr = pmr_engine._carregar_base_sem_filtros(data_ini, data_fim, prefer_materialized=False)
    pmp = pmp_engine._base(data_ini, data_fim, prefer_materialized=False)
    pmr_result = _materialize_dataframe(
        "pmr", data_ini, data_fim, pmr,
        ["Chave_E5", "e5_data", "e5_valor", "Chave_F2"],
    )
    pmp_result = _materialize_dataframe(
        "pmp", data_ini, data_fim, pmp,
        ["clifor", "_join_e2_num", "_join_e2_prefixo", "e5_data", "e5_valor"],
    )
    log_callback(
        "Materialização financeira concluída: "
        f"PMR {pmr_result['linhas']} linhas/{pmr_result['duplicidades']} chaves repetidas; "
        f"PMP {pmp_result['linhas']} linhas/{pmp_result['duplicidades']} chaves repetidas."
    )
