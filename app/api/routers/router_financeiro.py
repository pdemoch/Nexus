"""
=====================================================================
ROUTER FINANCEIRO — PMR (Prazo Médio de Recebimento)
=====================================================================
Destino: app/api/routers/router_financeiro.py

Registrar em main.py:
  from app.api.routers import router_financeiro
  app.include_router(router_financeiro.router)

ENDPOINTS PÚBLICOS (qualquer usuário autenticado)
  GET /api/v1/financeiro/pmr/global      → cards de topo
  GET /api/v1/financeiro/pmr/regional    → tabela por regional
  GET /api/v1/financeiro/pmr/clientes    → tabela por cliente (paginada)
  GET /api/v1/financeiro/status          → parquets disponíveis por fonte

ENDPOINTS ADMIN (apenas Administrador)
  POST /api/v1/financeiro/extrair        → dispara extração → S3 em background

PARÂMETROS COMUNS
  data_ini  date (YYYY-MM-DD) — início da janela, baseado em f2_emissao
  data_fim  date (YYYY-MM-DD) — fim da janela

O cálculo é sempre refeito sobre os parquets do S3 — sem cache de banco.
Para queries longas (vários anos) o tempo de resposta pode ser 10-30s
dependendo do volume de parquets no S3 e da conexão com a AWS.
"""

import asyncio
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.routers.router_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/financeiro", tags=["Financeiro — PMR"])


# ── Lazy imports para não falhar o startup se boto3/pyarrow estiver ausente ──

def _engine():
    from app.financeiro import pmr_engine
    return pmr_engine


def _store():
    from app.financeiro import s3_store
    return s3_store


def _extractor():
    from app.financeiro import gobi_client
    return gobi_client


# ── Helper: valida intervalo ──────────────────────────────────────────────────

def _validar_datas(data_ini: date, data_fim: date) -> None:
    if data_ini > data_fim:
        raise HTTPException(422, "data_ini deve ser anterior a data_fim.")
    delta = (data_fim - data_ini).days
    if delta > 365 * 5:
        raise HTTPException(422, "Janela máxima: 5 anos (1.825 dias).")


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def status(_: dict = Depends(get_current_user)):
    """
    Retorna quais meses (ano/mês) estão disponíveis no S3 para cada fonte.
    Útil para diagnosticar lacunas antes de interpretar o PMR.
    """
    store = _store()
    fontes = ["notas_saida", "contas_receber", "movimentacao_bancaria"]

    def _status():
        return {
            fonte: [f"{a}/{m:02d}" for a, m in store.meses_disponiveis(fonte)]
            for fonte in fontes
        }

    return await asyncio.to_thread(_status)


# ── PMR Global ────────────────────────────────────────────────────────────────

@router.get("/pmr/global")
async def pmr_global(
    data_ini: date = Query(..., description="Início da janela (f2_emissao)"),
    data_fim: date = Query(..., description="Fim da janela (f2_emissao)"),
    _: dict = Depends(get_current_user),
):
    """
    Retorna os três PMRs globais para o período selecionado.

    ```json
    {
      "periodo": {"data_ini": "2024-01-01", "data_fim": "2024-12-31"},
      "notas_base":    12500,
      "notas_pagas":   10800,
      "valor_total":   15234567.89,
      "pmr_pagamento":  45.1,
      "pmr_vencimento": 45.8,
      "pmr_cond_pag":   39.4
    }
    ```
    """
    _validar_datas(data_ini, data_fim)
    try:
        resultado = await asyncio.to_thread(
            _engine().calcular_pmr_global, data_ini, data_fim
        )
        return resultado
    except Exception as e:
        logger.exception("pmr_global %s → %s: %s", data_ini, data_fim, e)
        raise HTTPException(500, f"Erro ao calcular PMR global: {e}")


# ── PMR Regional ──────────────────────────────────────────────────────────────

@router.get("/pmr/regional")
async def pmr_regional(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    _: dict = Depends(get_current_user),
):
    """
    Retorna os três PMRs detalhados por regional, ordenados por valor_total desc.

    ```json
    {
      "periodo": {...},
      "regionais": [
        {
          "regional": "KEY ACCOUNT",
          "notas_pagas": 13,
          "valor_total": 2145000.00,
          "pmr_pagamento":  82.7,
          "pmr_vencimento": 83.0,
          "pmr_cond_pag":   59.2
        }, ...
      ]
    }
    ```
    """
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_regional, data_ini, data_fim
        )
    except Exception as e:
        logger.exception("pmr_regional %s → %s: %s", data_ini, data_fim, e)
        raise HTTPException(500, f"Erro ao calcular PMR regional: {e}")


# ── PMR Clientes ──────────────────────────────────────────────────────────────

@router.get("/pmr/clientes")
async def pmr_clientes(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    limit:    int  = Query(50, ge=1, le=500, description="Registros por página"),
    offset:   int  = Query(0,  ge=0,         description="Offset de paginação"),
    _: dict = Depends(get_current_user),
):
    """
    Retorna PMR por cliente, paginado, ordenado por valor_total desc.

    ```json
    {
      "periodo": {...},
      "total": 8629,
      "clientes": [
        {
          "chave_a1": "050014-01",
          "nome": "CLIENTE XYZ",
          "cgc": "12.345.678/0001-90",
          "regional": "CASH & CARRY",
          "segmento": "ATACADO",
          "notas_pagas": 48,
          "valor_total": 980000.00,
          "pmr_pagamento":  72.1,
          "pmr_vencimento": 76.4,
          "pmr_cond_pag":   60.0
        }, ...
      ]
    }
    ```
    """
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_clientes, data_ini, data_fim, limit, offset
        )
    except Exception as e:
        logger.exception("pmr_clientes %s → %s: %s", data_ini, data_fim, e)
        raise HTTPException(500, f"Erro ao calcular PMR por cliente: {e}")


# ── Extração (Admin) ──────────────────────────────────────────────────────────

class ExtracaoPayload(BaseModel):
    ano:  int
    mes:  int
    fontes: Optional[list[str]] = None  # None = todas as quatro

FONTES_VALIDAS = {"notas_saida", "contas_receber", "movimentacao_bancaria", "clientes"}


def _executar_extracao(ano: int, mes: int, fontes: list[str]) -> dict:
    """
    Extrai um mês específico de cada fonte e salva no S3.
    Disparado em background_task para não bloquear a resposta HTTP.
    """
    from calendar import monthrange
    from datetime import date as dt

    gc = _extractor()
    store = _store()
    resultado = {}

    d_ini = dt(ano, mes, 1)
    d_fim = dt(ano, mes, monthrange(ano, mes)[1])

    if "notas_saida" in fontes:
        try:
            df = gc.extrair_notas_saida(d_ini, d_fim)
            ok = store.salvar_mensal(df, "notas_saida", ano, mes)
            resultado["notas_saida"] = f"{'ok' if ok else 'erro'} ({len(df)} linhas)"
        except Exception as e:
            resultado["notas_saida"] = f"erro: {e}"

    if "contas_receber" in fontes:
        try:
            df = gc.extrair_contas_receber(d_ini, d_fim)
            ok = store.salvar_mensal(df, "contas_receber", ano, mes)
            resultado["contas_receber"] = f"{'ok' if ok else 'erro'} ({len(df)} linhas)"
        except Exception as e:
            resultado["contas_receber"] = f"erro: {e}"

    if "movimentacao_bancaria" in fontes:
        try:
            df = gc.extrair_movimentacao_bancaria(d_ini, d_fim)
            ok = store.salvar_mensal(df, "movimentacao_bancaria", ano, mes)
            resultado["movimentacao_bancaria"] = f"{'ok' if ok else 'erro'} ({len(df)} linhas)"
        except Exception as e:
            resultado["movimentacao_bancaria"] = f"erro: {e}"

    if "clientes" in fontes:
        try:
            df = gc.extrair_clientes()
            ok = store.salvar_clientes(df)
            resultado["clientes"] = f"{'ok' if ok else 'erro'} ({len(df)} linhas)"
        except Exception as e:
            resultado["clientes"] = f"erro: {e}"

    logger.info("Extração %d/%02d concluída: %s", ano, mes, resultado)
    return resultado


@router.post("/extrair")
async def extrair(
    payload:          ExtracaoPayload,
    background_tasks: BackgroundTasks,
    usuario_logado:   dict = Depends(get_current_user),
):
    """
    Dispara extração de um mês específico em background.
    Restrito a Administrador.

    body:
    ```json
    { "ano": 2024, "mes": 6, "fontes": ["notas_saida", "movimentacao_bancaria"] }
    ```
    Se `fontes` for omitido, extrai as quatro fontes.
    """
    if usuario_logado.get("funcao") != "Administrador":
        raise HTTPException(403, "Apenas Administradores podem disparar extrações.")

    fontes = payload.fontes or list(FONTES_VALIDAS)
    invalidas = set(fontes) - FONTES_VALIDAS
    if invalidas:
        raise HTTPException(422, f"Fontes inválidas: {invalidas}. Válidas: {FONTES_VALIDAS}")

    if not (1 <= payload.mes <= 12):
        raise HTTPException(422, "Mês deve estar entre 1 e 12.")
    if payload.ano < 2023:
        raise HTTPException(422, "Ano mínimo: 2023.")

    background_tasks.add_task(
        asyncio.to_thread,
        _executar_extracao,
        payload.ano,
        payload.mes,
        fontes,
    )

    return {
        "status":  "iniciada",
        "periodo": f"{payload.ano}/{payload.mes:02d}",
        "fontes":  fontes,
        "mensagem": "Extração rodando em background. Consulte /status para verificar quando concluir.",
    }