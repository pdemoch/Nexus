# -*- coding: utf-8 -*-
"""
Router Financeiro - PMR + Pipeline de dados
Destino: app/api/routers/router_financeiro.py

Registrar em main.py:
  from app.api.routers import router_financeiro
  app.include_router(router_financeiro.router)
"""

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.api.routers.router_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/financeiro", tags=["Financeiro - PMR"])


# ------------------------------------------------------------
# Estado do pipeline financeiro (separado do AppState S&OP)
# ------------------------------------------------------------

class FinanceiroState:
    pipeline_rodando:  bool      = False
    is_recarga_total:  bool      = False
    logs:              list      = []


# ------------------------------------------------------------
# Lazy imports
# ------------------------------------------------------------

def _engine():
    from app.financeiro import pmr_engine
    return pmr_engine

def _store():
    from app.financeiro import s3_store
    return s3_store


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _validar_datas(data_ini: date, data_fim: date) -> None:
    if data_ini > data_fim:
        raise HTTPException(422, "data_ini deve ser anterior a data_fim.")
    if (data_fim - data_ini).days > 365 * 5:
        raise HTTPException(422, "Janela maxima: 5 anos.")

def _so_admin(usuario: dict) -> None:
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Apenas Administradores podem executar o pipeline financeiro.")


# ------------------------------------------------------------
# Status (parquets disponiveis + estado do pipeline)
# ------------------------------------------------------------

@router.get("/status")
async def status(_: dict = Depends(get_current_user)):
    """
    Retorna:
      - is_running: pipeline financeiro em execucao
      - is_recarga_total: recarga completa ou rolling
      - logs: linhas do console geradas ate o momento
      - parquets: meses disponiveis no S3 por fonte
    """
    def _check():
        store = _store()
        fontes = ["notas_saida", "contas_receber", "movimentacao_bancaria"]
        return {
            "is_running":       FinanceiroState.pipeline_rodando,
            "is_recarga_total": FinanceiroState.is_recarga_total,
            "logs":             list(FinanceiroState.logs),
            "parquets": {
                fonte: [f"{a}/{m:02d}" for a, m in store.meses_disponiveis(fonte)]
                for fonte in fontes
            },
        }
    return await asyncio.to_thread(_check)


# ------------------------------------------------------------
# PMR Global
# ------------------------------------------------------------

@router.get("/pmr/global")
async def pmr_global(
    data_ini: date = Query(..., description="Inicio da janela (f2_emissao)"),
    data_fim: date = Query(..., description="Fim da janela (f2_emissao)"),
    _: dict = Depends(get_current_user),
):
    """
    Tres PMRs globais para o periodo selecionado.
    Apenas notas com movimentacao bancaria sao consideradas.
    """
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(_engine().calcular_pmr_global, data_ini, data_fim)
    except Exception as e:
        logger.exception("pmr_global: %s", e)
        raise HTTPException(500, f"Erro ao calcular PMR global: {e}")


# ------------------------------------------------------------
# PMR Regional
# ------------------------------------------------------------

@router.get("/pmr/regional")
async def pmr_regional(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    _: dict = Depends(get_current_user),
):
    """PMR detalhado por regional, ordenado por valor_total desc."""
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(_engine().calcular_pmr_regional, data_ini, data_fim)
    except Exception as e:
        logger.exception("pmr_regional: %s", e)
        raise HTTPException(500, f"Erro ao calcular PMR regional: {e}")


# ------------------------------------------------------------
# PMR Clientes
# ------------------------------------------------------------

@router.get("/pmr/clientes")
async def pmr_clientes(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    limit:    int  = Query(50, ge=1, le=500),
    offset:   int  = Query(0,  ge=0),
    _: dict = Depends(get_current_user),
):
    """PMR por cliente, paginado, ordenado por valor_total desc."""
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_clientes, data_ini, data_fim, limit, offset
        )
    except Exception as e:
        logger.exception("pmr_clientes: %s", e)
        raise HTTPException(500, f"Erro ao calcular PMR por cliente: {e}")


# ------------------------------------------------------------
# Pipeline - background tasks
# ------------------------------------------------------------

def _run_pipeline(recarga_total: bool) -> None:
    """Executa o pipeline e atualiza FinanceiroState em tempo real."""
    from app.financeiro.pipeline_financeiro import executar_pipeline

    FinanceiroState.pipeline_rodando  = True
    FinanceiroState.is_recarga_total  = recarga_total
    FinanceiroState.logs              = []

    def _log(msg: str) -> None:
        FinanceiroState.logs.append(msg)
        logger.info("[financeiro] %s", msg)

    try:
        executar_pipeline(recarga_total=recarga_total, log_callback=_log)
    except Exception as e:
        _log(f"ERRO inesperado no pipeline: {e}")
        logger.exception("pipeline_financeiro falhou: %s", e)
    finally:
        FinanceiroState.pipeline_rodando  = False
        FinanceiroState.is_recarga_total  = False


@router.post("/pipeline")
async def pipeline_atualizar(
    background_tasks: BackgroundTasks,
    usuario: dict = Depends(get_current_user),
):
    """
    Atualizacao Rolling - apaga e re-extrai os ultimos 3 meses + clientes.
    Consulte GET /status para acompanhar o progresso.
    Apenas Administradores.
    """
    _so_admin(usuario)
    if FinanceiroState.pipeline_rodando:
        raise HTTPException(409, "Pipeline financeiro ja esta em execucao.")
    background_tasks.add_task(asyncio.to_thread, _run_pipeline, False)
    return {"status": "iniciado", "modo": "rolling_3_meses"}


@router.post("/pipeline/recarga")
async def pipeline_recarga(
    background_tasks: BackgroundTasks,
    usuario: dict = Depends(get_current_user),
):
    """
    Recarga Completa - extrai historico completo desde jan/2023 + clientes.
    Operacao longa. Apenas Administradores.
    """
    _so_admin(usuario)
    if FinanceiroState.pipeline_rodando:
        raise HTTPException(409, "Pipeline financeiro ja esta em execucao.")
    background_tasks.add_task(asyncio.to_thread, _run_pipeline, True)
    return {"status": "iniciado", "modo": "recarga_total_desde_2023"}