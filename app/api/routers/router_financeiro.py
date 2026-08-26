# -*- coding: utf-8 -*-
"""
Router Financeiro - PMR + Pipeline de dados
Destino: app/api/routers/router_financeiro.py

CONTRATO DE RESPOSTA (rotas -filtrado / filtros / evolucao / notas / clientes-busca):
  retornam o objeto de dados DIRETO (sem envelope {"status":"success","data":...}),
  pois o FinanceiroArena.tsx consome r.data.<campo> diretamente. Em erro, devolvem
  estrutura vazia valida (nunca undefined) para o .map() do frontend nao quebrar.
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
# Estado do pipeline financeiro
# ------------------------------------------------------------

class FinanceiroState:
    pipeline_rodando:  bool = False
    is_recarga_total:  bool = False
    logs:              list = []


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

def _split(v):
    """Converte 'A,B,C' em ['A','B','C']; None/'' vira None."""
    if not v:
        return None
    itens = [x.strip() for x in v.split(",") if x.strip()]
    return itens or None


# ------------------------------------------------------------
# Status
# ------------------------------------------------------------

@router.get("/status")
async def status(_: dict = Depends(get_current_user)):
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
# PMR Global / Regional / Clientes (rotas autenticadas — inalteradas)
# ------------------------------------------------------------

@router.get("/pmr/global")
async def pmr_global(
    data_ini: date = Query(..., description="Inicio da janela (f2_emissao)"),
    data_fim: date = Query(..., description="Fim da janela (f2_emissao)"),
    _: dict = Depends(get_current_user),
):
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(_engine().calcular_pmr_global, data_ini, data_fim)
    except Exception as e:
        logger.exception("pmr_global: %s", e)
        raise HTTPException(500, f"Erro ao calcular PMR global: {e}")


@router.get("/pmr/regional")
async def pmr_regional(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    _: dict = Depends(get_current_user),
):
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(_engine().calcular_pmr_regional, data_ini, data_fim)
    except Exception as e:
        logger.exception("pmr_regional: %s", e)
        raise HTTPException(500, f"Erro ao calcular PMR regional: {e}")


@router.get("/pmr/clientes")
async def pmr_clientes(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    limit:    int  = Query(50, ge=1, le=500),
    offset:   int  = Query(0,  ge=0),
    _: dict = Depends(get_current_user),
):
    _validar_datas(data_ini, data_fim)
    try:
        return await asyncio.to_thread(
            _engine().calcular_pmr_clientes, data_ini, data_fim, None, None, limit, offset
        )
    except Exception as e:
        logger.exception("pmr_clientes: %s", e)
        raise HTTPException(500, f"Erro ao calcular PMR por cliente: {e}")


# ------------------------------------------------------------
# Pipeline - background tasks
# ------------------------------------------------------------

def _run_pipeline(recarga_total: bool) -> None:
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
        _engine().limpar_cache()
        FinanceiroState.pipeline_rodando  = False
        FinanceiroState.is_recarga_total  = False


@router.post("/pipeline")
async def pipeline_atualizar(
    background_tasks: BackgroundTasks,
    usuario: dict = Depends(get_current_user),
):
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
    data_ini: date = Query(..., description="Data de início (YYYY-MM-DD)"),
    data_fim: date = Query(..., description="Data de fim (YYYY-MM-DD)")
):
    try:
        filtros = await asyncio.to_thread(_engine().listar_filtros, data_ini, data_fim)
        return filtros  # {"regionais": [...], "segmentos": [...]}
    except Exception as e:
        print("\n" + "="*50)
        print("ERRO DETECTADO NA ROTA DE FILTROS")
        traceback.print_exc()
        print("="*50 + "\n")
        return {"regionais": [], "segmentos": []}


@router.get("/pmr/clientes-busca")
async def get_clientes_busca(
    data_ini: date = Query(..., description="Data de início (YYYY-MM-DD)"),
    data_fim: date = Query(..., description="Data de fim (YYYY-MM-DD)"),
    q: str = Query("", description="Texto de busca (nome ou CNPJ)"),
    limit: int = Query(20, ge=1, le=100),
):
    """Autocomplete do filtro de cliente."""
    try:
        clientes = await asyncio.to_thread(
            _engine().listar_clientes_busca, data_ini, data_fim, q, limit
        )
        return {"clientes": clientes}
    except Exception as e:
        logger.exception("clientes-busca: %s", e)
        return {"clientes": []}


# ------------------------------------------------------------
# Rotas filtradas (multi-valor: regionais/segmentos comma-separated)
# ------------------------------------------------------------

@router.get("/pmr/global-filtrado")
async def get_pmr_global_filtrado(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    regional:  str = Query(None, description="Uma regional (compat)"),
    segmento:  str = Query(None, description="Um segmento (compat)"),
    regionais: str = Query(None, description="Multiplas regionais separadas por virgula"),
    segmentos: str = Query(None, description="Multiplos segmentos separados por virgula"),
    cgc:       str = Query(None, description="Filtro opcional por CNPJ do cliente"),
):
    try:
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_global, data_ini, data_fim, seg, reg, cgc
        )
        return dados
    except Exception as e:
        logger.exception("global-filtrado: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim),
                            "dias_periodo": max((data_fim - data_ini).days, 1)},
                "notas_base": 0, "notas_pagas": 0, "valor_total": 0.0, "valor_por_dia": 0.0,
                "pmr_pagamento": None, "pmr_vencimento": None, "pmr_cond_pag": None,
                "delta_atraso": None}


@router.get("/pmr/regional-filtrado")
async def get_pmr_regional_filtrado(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    segmento:  str = Query(None),
    segmentos: str = Query(None, description="Multiplos segmentos separados por virgula"),
):
    try:
        seg = _split(segmentos) or segmento
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_regional, data_ini, data_fim, seg
        )
        return dados  # {"periodo": {...}, "regionais": [...]}
    except Exception as e:
        logger.exception("regional-filtrado: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "regionais": []}


@router.get("/pmr/clientes-filtrado")
async def get_pmr_clientes_filtrado(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    regional:  str = Query(None),
    segmento:  str = Query(None),
    regionais: str = Query(None, description="Multiplas regionais separadas por virgula"),
    segmentos: str = Query(None, description="Multiplos segmentos separados por virgula"),
    limit: int = Query(50),
    offset: int = Query(0),
):
    try:
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_clientes, data_ini, data_fim, seg, reg, limit, offset
        )
        return dados  # {"periodo": {...}, "total": N, "clientes": [...]}
    except Exception as e:
        logger.exception("clientes-filtrado: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
                "total": 0, "clientes": []}


@router.get("/pmr/evolucao")
async def get_pmr_evolucao(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    regional:  str = Query(None),
    segmento:  str = Query(None),
    regionais: str = Query(None, description="Multiplas regionais separadas por virgula"),
    segmentos: str = Query(None, description="Multiplos segmentos separados por virgula"),
    cgc:       str = Query(None, description="Filtro opcional por CNPJ do cliente"),
):
    """Evolucao mes a mes, agrupada pelo mes de pagamento (e5_data_pond)."""
    try:
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_mensal, data_ini, data_fim, seg, reg, cgc
        )
        return dados  # {"periodo": {...}, "meses": [...]}
    except Exception as e:
        logger.exception("evolucao: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": []}


@router.get("/pmr/notas")
async def get_pmr_notas(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    cgc:      str  = Query(..., description="CNPJ do cliente (obrigatorio)"),
):
    """Drill-down: parcelas (SE1) de um cliente no periodo, com SF2 e SE5."""
    try:
        dados = await asyncio.to_thread(
            _engine().obter_notas_cliente, data_ini, data_fim, cgc
        )
        return dados  # {"cgc", "nome", "total", "resumo", "notas": [...]}
    except Exception as e:
        logger.exception("notas: %s", e)
        return {"cgc": cgc, "nome": None, "total": 0, "resumo": {}, "notas": []}


# ------------------------------------------------------------
# Exportacao Excel — 5 abas (inclui Evolucao Mensal)
# ------------------------------------------------------------

@router.get("/pmr/exportar")
async def pmr_exportar(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    segmento: str = Query(None),
    regional: str = Query(None),
    segmentos: str = Query(None),
    regionais: str = Query(None),
    _: dict = Depends(get_current_user),
):
    """
    Gera um Excel com 5 abas para validacao e para servir de base ao agente de IA:
      1. Dados Brutos     - linha a linha (nivel parcela) com todas as variaveis
      2. Por Regional     - PMR agregado por regional
      3. Por Cliente      - PMR agregado por razao social (CNPJ)
      4. Evolucao Mensal  - PMR mes a mes por mes de pagamento (+ flag maturacao)
      5. Metodologia      - explicacao + exemplos numericos provados
    """
    import io
    from fastapi.responses import StreamingResponse

    _validar_datas(data_ini, data_fim)

    def _gerar():
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment

        engine = _engine()
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento

        df_bruto    = engine.obter_dados_brutos(data_ini, data_fim, seg, reg)
        d_regional  = engine.calcular_pmr_regional(data_ini, data_fim, seg)
        d_clientes  = engine.calcular_pmr_clientes(data_ini, data_fim, seg, reg, limit=5000)
        d_global    = engine.calcular_pmr_global(data_ini, data_fim, seg, reg)
        d_mensal    = engine.calcular_pmr_mensal(data_ini, data_fim, seg, reg)

        wb = openpyxl.Workbook()
        hdr_fill = PatternFill("solid", fgColor="1E3A5F")
        hdr_font = Font(bold=True, color="FFFFFF", size=10)

        # ABA 1: Dados Brutos
        ws1 = wb.active
        ws1.title = "Dados Brutos"
        if not df_bruto.empty:
            for j, col in enumerate(df_bruto.columns, 1):
                c = ws1.cell(row=1, column=j, value=col)
                c.fill, c.font = hdr_fill, hdr_font
                c.alignment = Alignment(horizontal="center")
            for i, row in enumerate(df_bruto.itertuples(index=False), 2):
                for j, v in enumerate(row, 1):
                    ws1.cell(row=i, column=j, value=v)

        # ABA 2: Por Regional
        ws2 = wb.create_sheet("Por Regional")
        hdrs2 = ["Regional", "Notas Pagas", "Valor NF (R$)",
                 "PMR Pagamento (dias)", "PMR Vencimento (dias)", "PMR Cond.Pag. (dias)", "Delta Atraso (dias)"]
        for j, h in enumerate(hdrs2, 1):
            c = ws2.cell(row=1, column=j, value=h); c.fill, c.font = hdr_fill, hdr_font
        for i, r in enumerate(d_regional.get("regionais", []), 2):
            ws2.cell(row=i, column=1, value=r["regional"])
            ws2.cell(row=i, column=2, value=r["notas_pagas"])
            ws2.cell(row=i, column=3, value=r["valor_total"])
            ws2.cell(row=i, column=4, value=r["pmr_pagamento"])
            ws2.cell(row=i, column=5, value=r["pmr_vencimento"])
            ws2.cell(row=i, column=6, value=r["pmr_cond_pag"])
            ws2.cell(row=i, column=7, value=r["delta_atraso"])

        # ABA 3: Por Cliente
        ws3 = wb.create_sheet("Por Cliente")
        hdrs3 = ["CNPJ", "Razao Social", "Regional", "Segmento", "Notas Pagas", "Valor NF (R$)",
                 "PMR Pagamento (dias)", "PMR Vencimento (dias)", "PMR Cond.Pag. (dias)", "Delta Atraso (dias)"]
        for j, h in enumerate(hdrs3, 1):
            c = ws3.cell(row=1, column=j, value=h); c.fill, c.font = hdr_fill, hdr_font
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

        # ABA 4: Evolucao Mensal
        ws5 = wb.create_sheet("Evolucao Mensal")
        hdrs5 = ["Mes Pagamento", "Notas Pagas", "Valor NF (R$)", "Valor Recebido (R$)",
                 "PMR Pagamento (dias)", "PMR Vencimento (dias)", "PMR Cond.Pag. (dias)",
                 "Delta Atraso (dias)", "Em Maturacao?"]
        for j, h in enumerate(hdrs5, 1):
            c = ws5.cell(row=1, column=j, value=h); c.fill, c.font = hdr_fill, hdr_font
        for i, m in enumerate(d_mensal.get("meses", []), 2):
            ws5.cell(row=i, column=1, value=m["mes"])
            ws5.cell(row=i, column=2, value=m["notas_pagas"])
            ws5.cell(row=i, column=3, value=m["valor_total"])
            ws5.cell(row=i, column=4, value=m["valor_recebido"])
            ws5.cell(row=i, column=5, value=m["pmr_pagamento"])
            ws5.cell(row=i, column=6, value=m["pmr_vencimento"])
            ws5.cell(row=i, column=7, value=m["pmr_cond_pag"])
            ws5.cell(row=i, column=8, value=m["delta_atraso"])
            ws5.cell(row=i, column=9, value="SIM" if m.get("em_maturacao") else "nao")

        # ABA 5: Metodologia (com exemplos numericos provados)
        ws4 = wb.create_sheet("Metodologia")
        titulo_font = Font(bold=True, size=12, color="1E3A5F")

        vt   = d_global.get("valor_total", 0) or 0
        dias = d_global.get("periodo", {}).get("dias_periodo", 1) or 1
        vpd  = d_global.get("valor_por_dia", 0) or 0
        pmr_pag = d_global.get("pmr_pagamento") or 0

        texto = [
            ("CALCULO DO PMR (Prazo Medio de Recebimento)", True),
            ("", False),
            ("O PMR mede, em dias, o tempo medio entre a emissao da NF e o recebimento.", False),
            ("E' uma media PONDERADA pelo valor do titulo (e1_valor).", False),
            ("", False),
            ("FORMULA GERAL:", True),
            ("PMR = SUM( dias_i * e1_valor_i ) / SUM( e1_valor_i )", False),
            ("", False),
            ("TRES METRICAS (todas contadas a partir de f2_emissao):", True),
            ("1. PMR Pagamento:  dias = e5_data_ponderada - f2_emissao (recebimento real no banco).", False),
            ("2. PMR Vencimento: dias = e1_vencrea - f2_emissao (vencimento real negociado).", False),
            ("3. PMR Cond.Pag.:  dias = e1_vencto - f2_emissao (vencimento contratual).", False),
            ("", False),
            ("EXEMPLO NUMERICO (3 parcelas ficticias):", True),
            ("Parcela A: emissao 01/03, pago 10/04 (40d), R$ 100.000  ->  40 x 100.000 = 4.000.000", False),
            ("Parcela B: emissao 01/03, pago 20/04 (50d), R$  50.000  ->  50 x  50.000 = 2.500.000", False),
            ("Parcela C: emissao 01/03, pago 05/05 (65d), R$  10.000  ->  65 x  10.000 =   650.000", False),
            ("SUM(dias*valor) = 7.150.000 ; SUM(valor) = 160.000", False),
            ("PMR Pagamento = 7.150.000 / 160.000 = 44,7 dias (nao a media simples 51,7d).", False),
            ("Observe: a parcela A (maior valor) puxa o PMR para baixo -> por isso ponderamos por valor.", False),
            ("", False),
            ("IMPACTO DE 1 DIA DE PMR NO CAPITAL DE GIRO:", True),
            ("Formula: valor_por_dia = valor_recebido_no_periodo / dias_do_periodo", False),
            (f"Neste recorte: valor_total = R$ {vt:,.2f}", False),
            (f"               dias_periodo = {dias} dias", False),
            (f"               valor_por_dia = {vt:,.2f} / {dias} = R$ {vpd:,.2f} por dia de PMR", False),
            ("Interpretacao: reduzir o PMR em 1 dia LIBERA (ganho unico) esse valor de caixa", False),
            ("preso no contas-a-receber. Reduzir N dias = N x valor_por_dia.", False),
            (f"Ex.: cair o PMR de {pmr_pag:.0f}d para {max(pmr_pag-5,0):.0f}d (5 dias) liberaria ~R$ {vpd*5:,.2f}.", False),
            ("RESSALVA: assume faturamento aproximadamente uniforme no periodo; e' estimativa de", False),
            ("ordem de grandeza para decisao, nao um numero contabil ao centavo.", False),
            ("", False),
            ("EVOLUCAO MENSAL (aba Evolucao Mensal):", True),
            ("Agrupada pelo MES DE PAGAMENTO (mes de e5_data_ponderada). Para as notas pagas no", False),
            ("mes M, calcula os 3 PMRs (sempre desde a emissao) e o valor recebido no mes.", False),
            ("ATENCAO - survivor bias: os 2 ultimos meses (flag 'Em Maturacao? = SIM') tendem a", False),
            ("mostrar PMR menor porque as notas de pagadores lentos ainda nao foram liquidadas.", False),
            ("Nao interpretar como melhora de comportamento ate o mes maturar.", False),
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
            ("- A tabela Dados Brutos esta ao nivel de PARCELA (SE1), nao de NF.", False),
            ("- A aba Por Cliente agrega todas as filiais/lojas do mesmo CNPJ.", False),
            ("- Delta Atraso = PMR Pagamento - PMR Cond.Pag. (positivo = cliente paga com atraso).", False),
            ("- e5_valor e' liquido de desconto (e5_valor - e5_vldesco), filtrado > 0 na extracao.", False),
        ]
        for i, (txt, bold) in enumerate(texto, 1):
            c = ws4.cell(row=i, column=1, value=txt)
            if bold:
                c.font = titulo_font
        ws4.column_dimensions["A"].width = 95

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
