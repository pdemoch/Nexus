# -*- coding: utf-8 -*-
"""
Router Financeiro - PMR + Pipeline de dados
Destino: app/api/routers/router_financeiro.py

Registrar em main.py:
  from app.api.routers import router_financeiro
  app.include_router(router_financeiro.router)
"""
import traceback
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


# ------------------------------------------------------------
# Filtros disponiveis (regionais e segmentos do periodo)
# ------------------------------------------------------------


@router.get("/pmr/filtros")
async def get_pmr_filtros(
    data_ini: str = Query("2026-01-01"),
    data_fim: str = Query("2026-07-31")
):
    try:
        # MANTENHA O SEU CÓDIGO ORIGINAL AQUI DENTRO
        filtros = obter_filtros_disponiveis(data_ini, data_fim)
        return {"status": "success", "data": filtros}
        
    except Exception as e:
        # ISSO VAI FORÇAR O ERRO A APARECER NO DOCKER LOGS
        print("===" * 20)
        print("ERRO FATAL NO PMR FILTROS:")
        traceback.print_exc() 
        print("===" * 20)
        return {"status": "error", "detail": str(e)}

@router.get("/pmr/global-filtrado")
async def pmr_global_filtrado(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    segmento: str = Query(None),
    regional: str = Query(None),
    _: dict = Depends(get_current_user),
):
    """PMR global com filtros opcionais de segmento e regional."""
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_global, data_ini, data_fim, segmento, regional
        )
    except Exception as e:
        raise HTTPException(500, f"Erro ao calcular PMR: {e}")


@router.get("/pmr/regional-filtrado")
async def pmr_regional_filtrado(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    segmento: str = Query(None),
    _: dict = Depends(get_current_user),
):
    """PMR por regional com filtro opcional de segmento."""
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_regional, data_ini, data_fim, segmento
        )
    except Exception as e:
        raise HTTPException(500, f"Erro ao calcular PMR regional: {e}")


@router.get("/pmr/clientes-filtrado")
async def pmr_clientes_filtrado(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    segmento: str = Query(None),
    regional: str = Query(None),
    limit:    int  = Query(100, ge=1, le=1000),
    offset:   int  = Query(0,   ge=0),
    _: dict = Depends(get_current_user),
):
    """PMR por razao social (CNPJ), com filtros opcionais."""
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_clientes,
            data_ini, data_fim, segmento, regional, limit, offset
        )
    except Exception as e:
        raise HTTPException(500, f"Erro ao calcular PMR clientes: {e}")


# ------------------------------------------------------------
# Exportacao Excel com todas as variaveis do calculo
# ------------------------------------------------------------

@router.get("/pmr/exportar")
async def pmr_exportar(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    segmento: str = Query(None),
    regional: str = Query(None),
    _: dict = Depends(get_current_user),
):
    """
    Gera um Excel com 4 abas para validacao pelo time financeiro:
      1. Dados Brutos     - linha a linha com todas as variaveis do calculo
      2. Por Regional     - PMR agregado por regional
      3. Por Cliente      - PMR agregado por razao social (CNPJ)
      4. Metodologia      - explicacao do calculo e das tres metricas
    """
    import io
    from fastapi.responses import StreamingResponse

    _validar_datas(data_ini, data_fim)

    def _gerar():
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment

        engine = _engine()
        df_bruto    = engine.obter_dados_brutos(data_ini, data_fim, segmento, regional)
        d_regional  = engine.calcular_pmr_regional(data_ini, data_fim, segmento)
        d_clientes  = engine.calcular_pmr_clientes(data_ini, data_fim, segmento, regional, limit=5000)

        wb = openpyxl.Workbook()

        # ── ABA 1: Dados Brutos ──
        ws1 = wb.active
        ws1.title = "Dados Brutos"
        hdr_fill = PatternFill("solid", fgColor="1E3A5F")
        hdr_font = Font(bold=True, color="FFFFFF", size=10)
        if not df_bruto.empty:
            for j, col in enumerate(df_bruto.columns, 1):
                c = ws1.cell(row=1, column=j, value=col)
                c.fill, c.font = hdr_fill, hdr_font
                c.alignment = Alignment(horizontal="center")
            for i, row in enumerate(df_bruto.itertuples(index=False), 2):
                for j, v in enumerate(row, 1):
                    ws1.cell(row=i, column=j, value=v)

        # ── ABA 2: Por Regional ──
        ws2 = wb.create_sheet("Por Regional")
        hdrs2 = ["Regional", "Notas Pagas", "Valor NF (R$)",
                 "PMR Pagamento (dias)", "PMR Vencimento (dias)", "PMR Cond.Pag. (dias)", "Delta Atraso (dias)"]
        for j, h in enumerate(hdrs2, 1):
            c = ws2.cell(row=1, column=j, value=h)
            c.fill, c.font = hdr_fill, hdr_font
        for i, r in enumerate(d_regional.get("regionais", []), 2):
            ws2.cell(row=i, column=1, value=r["regional"])
            ws2.cell(row=i, column=2, value=r["notas_pagas"])
            ws2.cell(row=i, column=3, value=r["valor_total"])
            ws2.cell(row=i, column=4, value=r["pmr_pagamento"])
            ws2.cell(row=i, column=5, value=r["pmr_vencimento"])
            ws2.cell(row=i, column=6, value=r["pmr_cond_pag"])
            ws2.cell(row=i, column=7, value=r["delta_atraso"])

        # ── ABA 3: Por Cliente ──
        ws3 = wb.create_sheet("Por Cliente")
        hdrs3 = ["CNPJ", "Razao Social", "Regional", "Segmento",
                 "Notas Pagas", "Valor NF (R$)",
                 "PMR Pagamento (dias)", "PMR Vencimento (dias)", "PMR Cond.Pag. (dias)", "Delta Atraso (dias)"]
        for j, h in enumerate(hdrs3, 1):
            c = ws3.cell(row=1, column=j, value=h)
            c.fill, c.font = hdr_fill, hdr_font
        for i, r in enumerate(d_clientes.get("clientes", []), 2):
            ws3.cell(row=i, column=1, value=r["cgc"])
            ws3.cell(row=i, column=2, value=r["nome"])
            ws3.cell(row=i, column=3, value=r["regional"])
            ws3.cell(row=i, column=4, value=r["segmento"])
            ws3.cell(row=i, column=5, value=r["notas_pagas"])
            ws3.cell(row=i, column=6, value=r["valor_total"])
            ws3.cell(row=i, column=7, value=r["pmr_pagamento"])
            ws3.cell(row=i, column=8, value=r["pmr_vencimento"])
            ws3.cell(row=i, column=9, value=r["pmr_cond_pag"])
            ws3.cell(row=i, column=10, value=r["delta_atraso"])

        # ── ABA 4: Metodologia ──
        ws4 = wb.create_sheet("Metodologia")
        titulo_font = Font(bold=True, size=12, color="1E3A5F")
        texto = [
            ("CALCULO DO PMR (Prazo Medio de Recebimento)", True),
            ("", False),
            ("O PMR mede, em dias, o tempo medio entre a emissao da nota fiscal e o recebimento.", False),
            ("E' uma media ponderada pelo valor do titulo (e1_valor).", False),
            ("", False),
            ("FORMULA GERAL:", True),
            ("PMR = SUM( dias_i * e1_valor_i ) / SUM( e1_valor_i )", False),
            ("", False),
            ("TRES METRICAS:", True),
            ("1. PMR Pagamento:  dias = e5_data_ponderada - f2_emissao", False),
            ("   Data real do recebimento no banco (media ponderada quando ha multiplos pagamentos).", False),
            ("2. PMR Vencimento: dias = e1_vencrea - f2_emissao", False),
            ("   Vencimento real negociado com o cliente.", False),
            ("3. PMR Cond.Pag.:  dias = e1_vencto - f2_emissao", False),
            ("   Vencimento da condicao de pagamento contratual.", False),
            ("", False),
            ("FONTES DE DADOS:", True),
            (f"Periodo analisado: {data_ini} a {data_fim}", False),
            ("SF2 (Notas de Saida)    - Gobi API report 595", False),
            ("SE1 (Contas a Receber)  - Gobi API report 596", False),
            ("SE5 (Mov. Bancaria)     - Gobi API report 592", False),
            ("SA1 (Cadastro Clientes) - Gobi API report 611", False),
            ("", False),
            ("NOTAS IMPORTANTES:", True),
            ("- Apenas notas com movimentacao bancaria registrada entram no calculo.", False),
            ("- Notas ainda nao pagas sao excluidas de TODAS as tres metricas.", False),
            ("- A razao social agrega todas as filiais/lojas do mesmo CNPJ.", False),
            ("- Delta Atraso = PMR Pagamento - PMR Cond.Pag. (positivo = cliente paga com atraso).", False),
        ]
        for i, (txt, bold) in enumerate(texto, 1):
            c = ws4.cell(row=i, column=1, value=txt)
            if bold:
                c.font = titulo_font
        ws4.column_dimensions["A"].width = 90

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    try:
        conteudo = await asyncio.to_thread(_gerar)
        nome = f"PMR_{data_ini}_{data_fim}.xlsx"
        return StreamingResponse(
            io.BytesIO(conteudo),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        )
    except Exception as e:
        logger.exception("pmr_exportar: %s", e)
        raise HTTPException(500, f"Erro ao gerar Excel: {e}")