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
from pydantic import BaseModel
from typing import Optional
from app.api.routers.router_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/financeiro", tags=["Financeiro - PMR"])


class PerguntaFinanceira(BaseModel):
    pergunta: str
    data_ini: date
    data_fim: date
    segmentos: Optional[str] = None
    regionais: Optional[str] = None
    status: Optional[str] = None
    motivos: Optional[str] = None
    cgc: Optional[str] = None
    historico: Optional[list[dict]] = None


@router.post("/agente/chat")
async def agente_chat(
    payload: PerguntaFinanceira,
    _: dict = Depends(get_current_user),
):
    pergunta = payload.pergunta.strip()
    if not pergunta:
        raise HTTPException(422, "A pergunta financeira nao pode ser vazia.")
    _validar_datas(payload.data_ini, payload.data_fim)
    try:
        from app.financeiro.agente_financeiro import construir_contexto, responder_pergunta
        segmento = _split(payload.segmentos)
        regional = _split(payload.regionais)
        contexto = await asyncio.to_thread(
            construir_contexto, payload.data_ini, payload.data_fim, segmento, regional, payload.cgc,
            _split(payload.status), _split(payload.motivos)
        )
        return {"resposta": await asyncio.to_thread(
            responder_pergunta, pergunta, contexto, payload.historico
        )}
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.exception("agente financeiro: %s", e)
        raise HTTPException(500, "Erro ao consultar o agente financeiro.")


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

def _pmp():
    from app.financeiro import pmp_engine
    return pmp_engine


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
        fontes = ["notas_saida", "contas_receber", "movimentacao_bancaria",
                  "notas_entrada", "contas_pagar"]
        return {
            "is_running":       FinanceiroState.pipeline_rodando,
            "is_recarga_total": FinanceiroState.is_recarga_total,
            "logs":             list(FinanceiroState.logs),
            "parquets": {
                fonte: [f"{a}/{m:02d}" for a, m in store.meses_disponiveis(fonte)]
                for fonte in fontes
            },
            "latest": {
                "fornecedores": "fornecedores/latest.parquet",
                "produtos": "sb1/latest.parquet",
                "lista_tecnica": "sg1/latest.parquet",
            },
            # Cadastros sem coluna de data não aparecem em ``meses_disponiveis``:
            # o indicador explícito evita que consumidores os tratem como
            # partições mensais.
            "snapshots": {
                "fornecedores": "latest",
                "produtos": "latest (SB1)",
                "lista_tecnica": "latest (SG1)",
            },
            "latest_only": ["fornecedores", "produtos", "lista_tecnica"],
        }
    return await asyncio.to_thread(_check)


@router.get("/pmp/global")
async def pmp_global(data_ini: date = Query(...), data_fim: date = Query(...),
                     e5_motbx: str = Query(None), d1_tp: str = Query(None),
                     fornecedor: str = Query(None), _: dict = Depends(get_current_user)):
    _validar_datas(data_ini, data_fim)
    return await asyncio.to_thread(_pmp().calcular_pmp_global, data_ini, data_fim,
                                   _split(e5_motbx), _split(d1_tp), _split(fornecedor))


@router.get("/pmp/fornecedores")
async def pmp_fornecedores(data_ini: date = Query(...), data_fim: date = Query(...),
                           limit: int = Query(100, ge=1, le=500),
                           offset: int = Query(0, ge=0),
                           e5_motbx: str = Query(None), d1_tp: str = Query(None),
                           fornecedor: str = Query(None),
                           _: dict = Depends(get_current_user)):
    _validar_datas(data_ini, data_fim)
    return await asyncio.to_thread(
        _pmp().calcular_pmp_fornecedores, data_ini, data_fim, limit, offset,
        _split(e5_motbx), _split(d1_tp), _split(fornecedor)
    )


def _pmp_filters(motivos, tipos, fornecedores, clifor):
    return (_split(motivos), _split(tipos), _split(fornecedores), _split(clifor))


@router.get("/pmp/filtros")
async def pmp_filtros(data_ini: date = Query(...), data_fim: date = Query(...),
                      _: dict = Depends(get_current_user)):
    def listar():
        base = _pmp()._base(data_ini, data_fim)
        if base.empty:
            return {"e5_motbx": [], "d1_tp": [], "fornecedor": [], "motivos": [], "tipos": [], "fornecedores": [], "clifor": []}
        valores = lambda col: sorted(_pmp()._norm_key(_pmp()._col(base, col)).replace("", "SEM VALOR").unique().tolist())
        fornecedores = valores("clifor")
        motivos = valores("e5_motbx")
        tipos = valores("d1_tp")
        return {"e5_motbx": motivos, "d1_tp": tipos, "fornecedor": fornecedores,
                "motivos": motivos, "tipos": tipos, "fornecedores": fornecedores, "clifor": fornecedores}
    return await asyncio.to_thread(listar)


@router.get("/pmp/global-filtrado")
async def pmp_global_filtrado(data_ini: date = Query(...), data_fim: date = Query(...),
                              motivos: str = Query(None), tipos: str = Query(None),
                              fornecedores: str = Query(None), clifor: str = Query(None),
                              e5_motbx: str = Query(None), d1_tp: str = Query(None),
                              fornecedor: str = Query(None),
                              _: dict = Depends(get_current_user)):
    _validar_datas(data_ini, data_fim)
    f = _pmp_filters(motivos or e5_motbx, tipos or d1_tp, fornecedores or fornecedor, clifor)
    return await asyncio.to_thread(_pmp().calcular_pmp_global, data_ini, data_fim, *f)


@router.get("/pmp/fornecedores-filtrado")
async def pmp_fornecedores_filtrado(data_ini: date = Query(...), data_fim: date = Query(...),
                                    motivos: str = Query(None), tipos: str = Query(None),
                                    fornecedores: str = Query(None), clifor: str = Query(None),
                                    e5_motbx: str = Query(None), d1_tp: str = Query(None),
                                    fornecedor: str = Query(None),
                                    limit: int = Query(100, ge=1, le=500),
                                    offset: int = Query(0, ge=0),
                                    _: dict = Depends(get_current_user)):
    _validar_datas(data_ini, data_fim)
    f = _pmp_filters(motivos or e5_motbx, tipos or d1_tp, fornecedores or fornecedor, clifor)
    return await asyncio.to_thread(_pmp().calcular_pmp_fornecedores, data_ini, data_fim, limit, offset, *f)


@router.get("/pmp/resumo")
async def pmp_resumo(data_ini: date = Query(...), data_fim: date = Query(...),
                     motivos: str = Query(None), tipos: str = Query(None),
                     fornecedores: str = Query(None), clifor: str = Query(None),
                     e5_motbx: str = Query(None), d1_tp: str = Query(None),
                     fornecedor: str = Query(None),
                     _: dict = Depends(get_current_user)):
    _validar_datas(data_ini, data_fim)
    f = _pmp_filters(motivos or e5_motbx, tipos or d1_tp, fornecedores or fornecedor, clifor)
    return await asyncio.to_thread(_pmp().calcular_pmp_resumo, data_ini, data_fim, *f)


@router.get("/pmp/evolucao")
async def pmp_evolucao(
    data_ini: date = Query(...), data_fim: date = Query(...),
    motivos: str = Query(None), tipos: str = Query(None),
    fornecedores: str = Query(None), clifor: str = Query(None),
    e5_motbx: str = Query(None), d1_tp: str = Query(None),
    fornecedor: str = Query(None),
    _: dict = Depends(get_current_user),
):
    _validar_datas(data_ini, data_fim)
    f = _pmp_filters(motivos or e5_motbx, tipos or d1_tp, fornecedores or fornecedor, clifor)
    try:
        return await asyncio.to_thread(_pmp().calcular_pmp_mensal, data_ini, data_fim, *f)
    except Exception as e:
        logger.exception("pmp/evolucao: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": []}


@router.get("/pmp/por-tipo")
async def pmp_por_tipo(
    data_ini: date = Query(...), data_fim: date = Query(...),
    motivos: str = Query(None), tipos: str = Query(None),
    fornecedores: str = Query(None), clifor: str = Query(None),
    e5_motbx: str = Query(None), d1_tp: str = Query(None),
    fornecedor: str = Query(None),
    _: dict = Depends(get_current_user),
):
    _validar_datas(data_ini, data_fim)
    f = _pmp_filters(motivos or e5_motbx, tipos or d1_tp, fornecedores or fornecedor, clifor)
    try:
        return await asyncio.to_thread(_pmp().calcular_pmp_por_tipo, data_ini, data_fim, *f)
    except Exception as e:
        logger.exception("pmp/por-tipo: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "tipos": []}


@router.get("/pmp/exportar")
async def pmp_exportar(data_ini: date = Query(...), data_fim: date = Query(...),
                       motivos: str = Query(None), tipos: str = Query(None),
                       fornecedores: str = Query(None), clifor: str = Query(None),
                       e5_motbx: str = Query(None), d1_tp: str = Query(None),
                       fornecedor: str = Query(None),
                       _: dict = Depends(get_current_user)):
    import io
    from fastapi.responses import StreamingResponse
    _validar_datas(data_ini, data_fim)
    f = _pmp_filters(motivos or e5_motbx, tipos or d1_tp, fornecedores or fornecedor, clifor)

    def gerar():
        import pandas as pd
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        pmp      = _pmp()
        base     = pmp._base(data_ini, data_fim, *f)
        d_global = pmp.calcular_pmp_global(data_ini, data_fim, *f)

        if base.empty:
            buf = io.BytesIO(); openpyxl.Workbook().save(buf); buf.seek(0); return buf

        # Enriquecer base com nome do fornecedor (SA2)
        try:
            from app.financeiro.s3_store import carregar_fornecedores
            sa2 = carregar_fornecedores()
            nc = next((c for c in ("a2_nome","a2_nom","nome","razao_social") if c in sa2), None)
            if nc and "clifor" in sa2.columns:
                nm = sa2.set_index(sa2["clifor"].astype(str).str.strip().str.upper())[nc].to_dict()
                base["nome_forn"] = base["clifor"].map(nm).fillna("—")
            else:
                base["nome_forn"] = "—"
        except Exception:
            base["nome_forn"] = "—"

        # Colunas de contribuicao (numerador da media ponderada)
        base["contrib_pgto"] = base["dias_pagamento"].fillna(0) * base["e5_valor"].fillna(0)
        base["contrib_vnc"]  = base["dias_vencimento"].fillna(0) * base["e5_valor"].fillna(0)
        base["contrib_cond"] = base["dias_cond_pag"].fillna(0)  * base["e5_valor"].fillna(0)
        base["e2_emissao"]   = pd.to_datetime(base.get("e2_emissao"),  errors="coerce")
        base["e2_vencto"]    = pd.to_datetime(base.get("e2_vencto"),   errors="coerce")
        base["e2_vencrea"]   = pd.to_datetime(base.get("e2_vencrea"),  errors="coerce")
        base["e5_data"]      = pd.to_datetime(base.get("e5_data"),     errors="coerce")
        base["f1_emissao"]   = pd.to_datetime(base.get("f1_emissao"),  errors="coerce")
        base["d1_label"]     = base["d1_tp"].astype(str).str.strip().str.upper().replace("", "SEM TIPO").fillna("SEM TIPO")

        def _fill(h): return PatternFill("solid", fgColor=h)
        def _fnt(bold=False, color="000000", sz=10):
            return Font(bold=bold, color=color, size=sz, name="Arial")
        def _bdr():
            return Border(bottom=Side(style="thin", color="D9E2EC"))
        def _aln(h="left"):
            return Alignment(horizontal=h, vertical="center", wrap_text=False)

        FMT_BRL = "R$ #,##0.00"; FMT_DATE = "DD/MM/YYYY"
        FMT_INT = "#,##0"; FMT_DAYS = "#,##0.00"; FMT_CONTRIB = "#,##0"
        ALT_BG   = _fill("F2F7FC"); PROVA_BG = _fill("FFF8E7")
        BODY_FNT = _fnt(sz=10); BORDER   = _bdr()

        C_ID = "0F3460"; C_SE2 = "065F46"; C_SE5 = "3B1F6D"
        C_SF1 = "4A4A4A"; C_PROVA = "7C3400"; C_AGG = "1E3A5F"

        def _dt(v):
            try: t = pd.Timestamp(v); return t.date() if pd.notna(t) else None
            except: return None
        def _sv(v):
            try: return None if pd.isna(v) else v
            except: return v

        def write_hdr(ws, row, cols_info):
            ws.row_dimensions[row].height = 32
            for ci, (lbl, w, cor) in enumerate(cols_info, 1):
                c = ws.cell(row=row, column=ci, value=lbl)
                c.fill = _fill(cor)
                c.font = Font(bold=True, color="FFFFFF", size=9, name="Arial")
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                ws.column_dimensions[get_column_letter(ci)].width = w

        def write_data(ws, ri, vals_fmts, alt=False, proof=False):
            bg = PROVA_BG if proof else (ALT_BG if alt else None)
            for ci, (val, fmt) in enumerate(vals_fmts, 1):
                c = ws.cell(row=ri, column=ci, value=val)
                c.font = BODY_FNT; c.border = BORDER
                if bg: c.fill = bg
                if fmt: c.number_format = fmt
                if isinstance(val, (int, float)) and fmt not in (FMT_DATE, None):
                    c.alignment = _aln("right")

        def agg_pmp(grp):
            vt = grp["e5_valor"].sum()
            if not vt: return dict.fromkeys(["pagamentos","valor","contrib_pgto","pmp_pgto","pmp_vnc","pmp_cond","delta"], 0)
            return {
                "pagamentos":   int(len(grp)),
                "valor":        round(float(vt), 2),
                "contrib_pgto": round(float(grp["contrib_pgto"].sum()), 0),
                "pmp_pgto":     round(float(grp["contrib_pgto"].sum()) / float(vt), 2),
                "pmp_vnc":      round(float(grp["contrib_vnc"].sum())  / float(vt), 2),
                "pmp_cond":     round(float(grp["contrib_cond"].sum()) / float(vt), 2),
                "delta":        round((float(grp["contrib_pgto"].sum()) - float(grp["contrib_cond"].sum())) / float(vt), 2),
            }

        wb = openpyxl.Workbook(); wb.remove(wb.active)

        # === ABA 1: DADOS BRUTOS ===
        ws1 = wb.create_sheet("Dados Brutos")
        # Linha 1: grupos mergeados
        GRP_SPANS = [
            ("IDENTIFICACAO", 3, C_ID),
            ("SE2 - TITULO A PAGAR (data base do calculo)", 7, C_SE2),
            ("SE5 - PAGAMENTO BANCARIO", 5, C_SE5),
            ("SF1 - NF REFERENCIA", 2, C_SF1),
            ("PROVA DO CALCULO", 4, C_PROVA),
        ]
        ws1.row_dimensions[1].height = 18
        ci = 1
        for lbl, span, cor in GRP_SPANS:
            if span > 1:
                ws1.merge_cells(start_row=1, start_column=ci, end_row=1, end_column=ci+span-1)
            cell = ws1.cell(row=1, column=ci, value=lbl)
            cell.fill = _fill(cor)
            cell.font = Font(bold=True, color="FFFFFF", size=9, name="Arial")
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ci += span
        COLS_B = [
            ("clifor",         "CLIFOR",                 None,      10, C_ID),
            ("nome_forn",      "Razao Social (SA2)",      None,      32, C_ID),
            ("e5_filial",      "Filial",                  None,       6, C_ID),
            ("e2_num",         "No. Titulo (SE2)",        None,      15, C_SE2),
            ("e2_prefixo",     "Prefixo",                 None,       8, C_SE2),
            ("e2_parcela",     "Parcela",                 None,       8, C_SE2),
            ("e2_tipo",        "Tipo",                    None,       6, C_SE2),
            ("e2_emissao",     "* Data Base (SE2)",      FMT_DATE,  18, C_SE2),
            ("e2_vencto",      "Vencto. Contratual",     FMT_DATE,  18, C_SE2),
            ("e2_vencrea",     "Vencto. Real",           FMT_DATE,  15, C_SE2),
            ("e5_numero",      "No. Mov. E5",             None,      15, C_SE5),
            ("e5_loja",        "Loja",                    None,       6, C_SE5),
            ("e5_data",        "* Data Pagamento (E5)",  FMT_DATE,  18, C_SE5),
            ("e5_valor",       "* Valor Pago (R$)",      FMT_BRL,   18, C_SE5),
            ("e5_motbx",       "Motivo Baixa",            None,      12, C_SE5),
            ("f1_emissao",     "Emissao NF SF1 (ref.)",  FMT_DATE,  16, C_SF1),
            ("d1_label",       "Tipo Compra D1",          None,      12, C_SF1),
            ("dias_pagamento",  "* Dias PMP Pgto.",       FMT_DAYS,  16, C_PROVA),
            ("contrib_pgto",   "* Contrib=Dias x R$",    FMT_CONTRIB,18,C_PROVA),
            ("dias_vencimento", "Dias PMP Vencto.",       FMT_DAYS,  16, C_PROVA),
            ("dias_cond_pag",  "Dias PMP Cond.",          FMT_DAYS,  15, C_PROVA),
        ]
        ws1.row_dimensions[2].height = 34
        for ci, (_, lbl, _, w, cor) in enumerate(COLS_B, 1):
            cell = ws1.cell(row=2, column=ci, value=lbl)
            cell.fill = _fill(cor)
            cell.font = Font(bold=True, color="FFFFFF", size=9, name="Arial")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws1.column_dimensions[get_column_letter(ci)].width = w
        for ri, row in enumerate(base.itertuples(index=False), 3):
            for ci, (col, _, fmt, _, cor) in enumerate(COLS_B, 1):
                v = getattr(row, col, None)
                if fmt == FMT_DATE: v = _dt(v)
                else: v = _sv(v)
                cell = ws1.cell(row=ri, column=ci, value=v)
                cell.font = BODY_FNT; cell.border = BORDER
                if cor == C_PROVA: cell.fill = PROVA_BG
                elif ri % 2 == 0: cell.fill = ALT_BG
                if fmt: cell.number_format = fmt
                if isinstance(v, (int, float)) and fmt not in (FMT_DATE, None):
                    cell.alignment = _aln("right")
        ws1.auto_filter.ref = "A2:" + get_column_letter(len(COLS_B)) + "2"
        ws1.freeze_panes = "A3"
        nr = len(base) + 4
        vt_tot = base["e5_valor"].sum(); ct_tot = base["contrib_pgto"].sum()
        pmp_pv = ct_tot / vt_tot if vt_tot else 0
        nc_cell = ws1.cell(row=nr, column=1,
            value=f"PROVA GLOBAL: SUM(Contrib.) / SUM(Valor) = {ct_tot:,.0f} / {vt_tot:,.0f} = {pmp_pv:.2f} dias")
        nc_cell.font = Font(bold=True, size=10, color="7C3400", name="Arial"); nc_cell.fill = _fill("FFF3CD")
        ws1.merge_cells(start_row=nr, start_column=1, end_row=nr, end_column=min(len(COLS_B), 12))

        # === ABA 2: POR FORNECEDOR ===
        ws2 = wb.create_sheet("Por Fornecedor")
        HFORN = [("CLIFOR",10,C_AGG),("Razao Social (SA2)",36,C_AGG),("Pagamentos",12,C_AGG),
                 ("Valor Pago (R$)",18,C_AGG),("* SIGMA Contrib.Pgto.",18,C_PROVA),
                 ("* PMP Pgto.=Sigma/Valor",18,C_PROVA),("PMP Vencto.(d)",15,C_AGG),
                 ("PMP Cond.Pag.(d)",15,C_AGG),("Delta Atraso(d)",14,C_AGG)]
        write_hdr(ws2, 1, HFORN)
        rows_f = sorted([{"clifor":k,"nome":g["nome_forn"].iloc[0],**agg_pmp(g)}
                         for k,g in base.groupby("clifor", dropna=False)],
                        key=lambda r: r["valor"], reverse=True)
        for ri, r in enumerate(rows_f, 2):
            write_data(ws2, ri, [
                (str(r["clifor"]),None),(str(r["nome"]),None),(r["pagamentos"],FMT_INT),
                (r["valor"],FMT_BRL),(r["contrib_pgto"],FMT_CONTRIB),(r["pmp_pgto"],FMT_DAYS),
                (r["pmp_vnc"],FMT_DAYS),(r["pmp_cond"],FMT_DAYS),(r["delta"],FMT_DAYS),
            ], alt=(ri%2==0))
        ws2.auto_filter.ref = "A1:" + get_column_letter(len(HFORN)) + "1"
        ws2.freeze_panes = "A2"
        nr2 = len(rows_f) + 3
        nc2 = ws2.cell(row=nr2, column=1,
            value="PROVA: coluna * PMP = coluna * SIGMA Contrib. / Valor Pago. SUM(Valor) = total Dados Brutos.")
        nc2.font = Font(bold=True, size=9, color="7C3400", name="Arial"); nc2.fill = _fill("FFF3CD")
        ws2.merge_cells(start_row=nr2, start_column=1, end_row=nr2, end_column=8)

        # === ABA 3: POR TIPO D1 ===
        ws3 = wb.create_sheet("Por Tipo D1")
        HTIPO = [("Cod. D1",10,C_AGG),("Pagamentos",12,C_AGG),("Valor Pago (R$)",18,C_AGG),
                 ("* SIGMA Contrib.Pgto.",18,C_PROVA),("* PMP Pgto.=Sigma/V.",18,C_PROVA),
                 ("PMP Vencto.(d)",15,C_AGG),("PMP Cond.Pag.(d)",15,C_AGG),("Delta(d)",12,C_AGG)]
        write_hdr(ws3, 1, HTIPO)
        rows_t = sorted([{"tipo":k,**agg_pmp(g)} for k,g in base.groupby("d1_label",dropna=False)],
                        key=lambda r: r["pmp_pgto"])
        for ri, r in enumerate(rows_t, 2):
            is_sem = (r["tipo"] == "SEM TIPO")
            write_data(ws3, ri, [
                (r["tipo"],None),(r["pagamentos"],FMT_INT),(r["valor"],FMT_BRL),
                (r["contrib_pgto"],FMT_CONTRIB),(r["pmp_pgto"],FMT_DAYS),
                (r["pmp_vnc"],FMT_DAYS),(r["pmp_cond"],FMT_DAYS),(r["delta"],FMT_DAYS),
            ], alt=(ri%2==0), proof=is_sem)
            ri_final = ri
        ws3.auto_filter.ref = "A1:" + get_column_letter(len(HTIPO)) + "1"
        ws3.freeze_panes = "A2"
        sem_base = base[base["d1_label"] == "SEM TIPO"]
        if not sem_base.empty:
            sep_r = ri_final + 2
            sc = ws3.cell(row=sep_r, column=1,
                value="SEM TIPO — Top Fornecedores (SF1/SD1 nao encontrou tipo de compra para esses pagamentos)")
            sc.font = Font(bold=True, size=10, color="7C3400", name="Arial"); sc.fill = _fill("FFF3CD")
            ws3.merge_cells(start_row=sep_r, start_column=1, end_row=sep_r, end_column=8)
            sub_h = [("CLIFOR",10,C_SF1),("Razao Social",36,C_SF1),("Pagamentos",12,C_SF1),
                     ("Valor Pago (R$)",18,C_SF1),("PMP Pgto.(d)",15,C_SF1)]
            write_hdr(ws3, sep_r+1, sub_h)
            rows_sem = sorted([{"clifor":k,"nome":g["nome_forn"].iloc[0],**agg_pmp(g)}
                                for k,g in sem_base.groupby("clifor",dropna=False)],
                               key=lambda r: r["valor"], reverse=True)
            for sri, r in enumerate(rows_sem[:25], sep_r+2):
                write_data(ws3, sri, [
                    (str(r["clifor"]),None),(str(r["nome"]),None),
                    (r["pagamentos"],FMT_INT),(r["valor"],FMT_BRL),(r["pmp_pgto"],FMT_DAYS),
                ], alt=(sri%2==0))
            nc3 = ws3.cell(row=sri+2, column=1,
                value="Causas: (1) impostos/governo sem NF de fornecedor  "
                      "(2) chave join SE2->SF1 diverge  (3) NF sem itens SD1")
            nc3.font = Font(size=9, color="475569", name="Arial")
            ws3.merge_cells(start_row=sri+2, start_column=1, end_row=sri+2, end_column=8)

        # === ABA 4: EVOLUCAO MENSAL ===
        ws4 = wb.create_sheet("Evolucao Mensal")
        HEVOL = [("Mes Pagamento E5",14,C_AGG),("Pagamentos",12,C_AGG),("Valor Pago (R$)",18,C_AGG),
                 ("* SIGMA Contrib.Pgto.",18,C_PROVA),("* PMP Pgto.=Sigma/V.",18,C_PROVA),
                 ("PMP Vencto.(d)",15,C_AGG),("PMP Cond.Pag.(d)",15,C_AGG),("Delta(d)",12,C_AGG)]
        write_hdr(ws4, 1, HEVOL)
        base["_mes"] = base["e5_data"].dt.to_period("M").astype(str)
        ri = 2
        for mes, g in sorted(base.groupby("_mes", dropna=False), key=lambda x: x[0]):
            r = agg_pmp(g)
            write_data(ws4, ri, [
                (str(mes),None),(r["pagamentos"],FMT_INT),(r["valor"],FMT_BRL),
                (r["contrib_pgto"],FMT_CONTRIB),(r["pmp_pgto"],FMT_DAYS),
                (r["pmp_vnc"],FMT_DAYS),(r["pmp_cond"],FMT_DAYS),(r["delta"],FMT_DAYS),
            ], alt=(ri%2==0))
            ri += 1
        ws4.freeze_panes = "A2"
        nc4 = ws4.cell(row=ri+1, column=1,
            value="PROVA: PMP do mes = SIGMA(Contrib.Pgto.) / Valor Pago daquele mes. SUM(Valor todos meses) = total Dados Brutos.")
        nc4.font = Font(bold=True, size=9, color="7C3400", name="Arial"); nc4.fill = _fill("FFF3CD")
        ws4.merge_cells(start_row=ri+1, start_column=1, end_row=ri+1, end_column=8)

        # === ABA 5: METODOLOGIA ===
        ws5 = wb.create_sheet("Metodologia")
        ws5.column_dimensions["A"].width = 110
        TF = Font(bold=True, size=12, color="1E3A5F", name="Arial")
        CF = Font(size=10, name="Arial"); HF = _fill("EBF3FB"); PF = _fill("FFF8E7")
        dp = (d_global.get("periodo") or {}).get("dias_periodo", 1) or 1
        pmpg = d_global.get("pmp_pagamento") or 0; pmpc = d_global.get("pmp_cond_pag") or 0
        metodo = [
            ("COMO O PMP E CALCULADO - GUIA COMPLETO", True, False),
            ("", False, False),
            ("CADEIA DE DADOS:", True, False),
            ("  SE5 + SE2 + SF1/SD1  ==>  Dados Brutos  (uma linha por pagamento SE5)", False, False),
            ("  Dados Brutos agrupado por CLIFOR         ==>  Por Fornecedor", False, False),
            ("  Dados Brutos agrupado por d1_tp           ==>  Por Tipo D1", False, False),
            ("  Dados Brutos agrupado pelo mes e5_data    ==>  Evolucao Mensal", False, False),
            ("  TODAS as abas usam a MESMA formula: PMP = SUM(contrib) / SUM(valor)", False, False),
            ("", False, False),
            ("FORMULA (media ponderada por valor pago):", True, False),
            ("  Para cada linha nos Dados Brutos:", False, True),
            ("    dias_pgto = e5_data - e2_emissao     [pagamento - data base SE2]", False, True),
            ("    contrib   = dias_pgto x e5_valor     [contribuicao ao numerador]", False, True),
            ("  PMP do grupo = SUM(contrib) / SUM(e5_valor)", False, True),
            ("", False, False),
            ("EXEMPLO NUMERICO (3 pagamentos ficticios - mesmo fornecedor):", True, False),
            ("  Linha 1:  Data Base SE2=01/03  Pgto E5=10/04  ->  40 dias  Valor=R$ 100.000  Contrib=40x100.000=4.000.000", False, True),
            ("  Linha 2:  Data Base SE2=01/03  Pgto E5=20/04  ->  50 dias  Valor=R$  50.000  Contrib=50x50.000=2.500.000", False, True),
            ("  Linha 3:  Data Base SE2=01/03  Pgto E5=05/05  ->  65 dias  Valor=R$  10.000  Contrib=65x10.000=650.000", False, True),
            ("", False, False),
            ("  SUM(Contrib) = 4.000.000 + 2.500.000 + 650.000 = 7.150.000", False, True),
            ("  SUM(Valor)  = 100.000 + 50.000 + 10.000 = 160.000", False, True),
            ("  PMP = 7.150.000 / 160.000 = 44,69 dias", False, True),
            ("", False, False),
            ("  Por que media ponderada e nao simples?", False, False),
            ("  Media simples = (40+50+65)/3 = 51,7 dias  -- ignora que pagar R$100k em 40d pesa muito mais", False, False),
            ("  A ponderacao por valor reflete o impacto real no capital de giro.", False, False),
            ("", False, False),
            ("  Verifique na aba Dados Brutos: localize qualquer linha, calcule", False, False),
            ("  Data Pgto(E5) - Data Base(SE2) = deve bater com coluna Dias PMP Pgto.", False, False),
            ("  Dias x Valor Pago = deve bater com coluna Contrib.", False, False),
            ("", False, False),
            ("EXEMPLO DE VERIFICACAO POR FORNECEDOR:", True, False),
            ("  Na aba Por Fornecedor, linha de um CLIFOR qualquer:", False, True),
            ("  SIGMA Contrib.Pgto. / Valor Pago = PMP Pgto. mostrado  (deve ser identico)", False, True),
            ("  Valide tambem: SUM(Valor Pago de todos os fornecedores) = SUM(Dados Brutos)", False, True),
            ("", False, False),
            ("IMPACTO DE 1 DIA A MAIS DE PMP NO CAIXA:", True, False),
            (f"  Valor pago no periodo: R$ {vt_tot:,.0f}", False, False),
            (f"  Dias do periodo: {dp}", False, False),
            (f"  Valor por dia = R$ {vt_tot/dp:,.0f}", False, False),
            (f"  Cada dia a MAIS no PMP retêm R$ {vt_tot/dp:,.0f} adicionais em caixa.", False, False),
            (f"  Aumentar PMP em 5 dias => R$ {vt_tot/dp*5:,.0f} de folga adicional.", False, False),
            ("  (Estimativa uniforme - assume fluxo de pagamento constante no periodo)", False, False),
            ("", False, False),
            ("DATA BASE: sempre e2_emissao (SE2 Contas a Pagar) - 100% de cobertura", True, False),
            ("  SF1 e carregada so para obter d1_tp (tipo de compra para Por Tipo D1)", False, False),
            ("  e2_emissao = data que o financeiro lancou o titulo no Protheus", False, False),
            ("", False, False),
            ("3 METRICAS DE DIAS (todas a partir de e2_emissao):", True, False),
            ("  PMP Pagamento  = e5_data    - e2_emissao  [quando a empresa pagou de fato]", False, False),
            ("  PMP Vencimento = e2_vencrea - e2_emissao  [prazo ate vencimento real negociado]", False, False),
            ("  PMP Cond.Pag.  = e2_vencto  - e2_emissao  [prazo contratual da condicao de pagto.]", False, False),
            ("  Delta Atraso   = PMP Pag. - PMP Cond.  [+ = empresa paga apos vencto = bom para caixa]", False, False),
            ("", False, False),
            ("SEM TIPO: pagamentos sem d1_tp porque:", True, False),
            ("  (1) Impostos/governo - nao geram NF de fornecedor (sem SF1/SD1)", False, False),
            ("  (2) Chave do join SE2->SF1 falhou (e2_num/f1_doc ou e2_prefixo/f1_serie divergem)", False, False),
            ("  (3) NF existe na SF1 mas sem itens SD1 cadastrados", False, False),
            ("  Veja os fornecedores por tras do SEM TIPO na aba Por Tipo D1.", False, False),
            ("", False, False),
            ("CHAVES DE JOIN:", True, False),
            ("  SE5 <-> SE2: CLIFOR + numero + prefixo + parcela + filial + tipo", False, False),
            ("  SE2 <-> SF1: CLIFOR + e2_num/f1_doc + e2_prefixo/f1_serie + e2_filial/f1_filial", False, False),
            ("  SA2 <-> base: CLIFOR (para razao social)", False, False),
            ("", False, False),
            (f"Periodo analisado: {data_ini.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}", False, False),
            (f"PMP Pagamento global: {pmpg:.2f} dias  |  Delta Atraso global: {pmpg-pmpc:+.2f} dias", False, False),
        ]
        for ri, (txt, bold, proof) in enumerate(metodo, 1):
            c = ws5.cell(row=ri, column=1, value=txt)
            c.font = TF if bold else CF
            if bold: c.fill = HF
            if proof: c.fill = PF

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    conteudo = await asyncio.to_thread(gerar)
    return StreamingResponse(
        conteudo,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="PMP_{data_ini}_{data_fim}.xlsx"'},
    )


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
    status: str = Query(None, description="Status do cliente"),
    motivos: str = Query(None, description="Multiplos motivos da E5"),
    cgc:       str = Query(None, description="Filtro opcional por CNPJ do cliente"),
):
    try:
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento
        stat = _split(status) or status
        mot = _split(motivos) or motivos
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_global, data_ini, data_fim, seg, reg, cgc, stat, mot
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
    regional:   str = Query(None, description="Uma regional (compat)"),
    regionais:  str = Query(None, description="Multiplas regionais separadas por virgula"),
    status:     str = Query(None, description="Status do cliente"),
    motivos:    str = Query(None, description="Multiplos motivos da E5"),
):
    try:
        seg = _split(segmentos) or segmento
        reg = _split(regionais) or regional
        stat = _split(status) or status
        mot = _split(motivos) or motivos
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_regional, data_ini, data_fim, seg, reg, stat, mot
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
    status: str = Query(None, description="Status do cliente"),
    motivos: str = Query(None, description="Multiplos motivos da E5"),
    limit: int = Query(50),
    offset: int = Query(0),
):
    try:
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento
        stat = _split(status) or status
        mot = _split(motivos) or motivos
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_clientes, data_ini, data_fim, seg, reg, limit, offset, stat, mot
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
    status:    str = Query(None, description="Status do cliente"),
    motivos:   str = Query(None, description="Multiplos motivos da E5"),
):
    """Evolucao mes a mes, agrupada pelo mes de recebimento (e5_data)."""
    try:
        reg = _split(regionais) or regional
        seg = _split(segmentos) or segmento
        stat = _split(status) or status
        mot = _split(motivos) or motivos
        dados = await asyncio.to_thread(
            _engine().calcular_pmr_mensal, data_ini, data_fim, seg, reg, cgc, stat, mot
        )
        return dados  # {"periodo": {...}, "meses": [...]}
    except Exception as e:
        logger.exception("evolucao: %s", e)
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": []}


@router.get("/pmr/notas")
async def get_pmr_notas(
    data_ini: date = Query(...),
    data_fim: date = Query(...),
    cgc:      str  = Query(None, description="CNPJ do cliente (compatibilidade)"),
    razao_social: str = Query(None, description="Razao social consolidada"),
    segmentos: str = Query(None, description="Segmentos aplicados ao detalhe"),
    regionais: str = Query(None, description="Regionais aplicadas ao detalhe"),
    status: str = Query(None, description="Status aplicado ao detalhe"),
    motivos: str = Query(None, description="Motivos E5 aplicados ao detalhe"),
):
    """Drill-down: parcelas de todos os CNPJs de uma razao social."""
    try:
        if not razao_social and not cgc:
            raise HTTPException(422, "Informe razao_social ou cgc.")
        if razao_social:
            dados = await asyncio.to_thread(
                _engine().obter_notas_razao_social, data_ini, data_fim,
                razao_social, _split(segmentos), _split(regionais), _split(status), _split(motivos)
            )
        else:
            dados = await asyncio.to_thread(_engine().obter_notas_cliente, data_ini, data_fim, cgc)
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
    status: str = Query(None, description="Status do cliente"),
    motivos: str = Query(None, description="Multiplos motivos da E5"),
    _: dict = Depends(get_current_user),
):
    """
    Gera um Excel com 5 abas para validacao e para servir de base ao agente de IA:
      1. Dados Brutos     - linha a linha (nivel parcela) com todas as variaveis
      2. Por Regional     - PMR agregado por regional
      3. Resumo por Cliente - PMR agregado por razao social
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
        stat = _split(status) or status
        mot = _split(motivos) or motivos

        df_bruto    = engine.obter_dados_brutos(data_ini, data_fim, seg, reg, status=stat, motivo=mot)
        d_regional  = engine.calcular_pmr_regional(data_ini, data_fim, seg, reg, stat, mot)
        d_clientes  = engine.calcular_pmr_clientes(data_ini, data_fim, seg, reg, 100000, 0, stat, mot)
        d_global    = engine.calcular_pmr_global(data_ini, data_fim, seg, reg, status=stat, motivo=mot)
        d_mensal    = engine.calcular_pmr_mensal(data_ini, data_fim, seg, reg, status=stat, motivo=mot)

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
        hdrs2 = ["Regional", "Movimentos E5", "Valor Recebido E5 (R$)",
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

        # ABA 3: Resumo por Cliente
        ws3 = wb.create_sheet("Resumo por Cliente")
        hdrs3 = ["Razao Social", "CNPJs", "Codigos Cliente", "Regional", "Segmento", "Movimentos E5", "Valor Recebido E5 (R$)",
                 "PMR Pagamento (dias)", "PMR Vencimento (dias)", "PMR Cond.Pag. (dias)", "Delta Atraso (dias)"]
        for j, h in enumerate(hdrs3, 1):
            c = ws3.cell(row=1, column=j, value=h); c.fill, c.font = hdr_fill, hdr_font
        for i, r in enumerate(d_clientes.get("clientes", []), 2):
            ws3.cell(row=i, column=1, value=r["nome"])
            ws3.cell(row=i, column=2, value=", ".join(r.get("cnpjs", [])))
            ws3.cell(row=i, column=3, value=", ".join(r.get("codigos_cliente", [])))
            ws3.cell(row=i, column=4, value=r["regional"])
            ws3.cell(row=i, column=5, value=r["segmento"])
            ws3.cell(row=i, column=6, value=r["notas_pagas"])
            ws3.cell(row=i, column=7, value=r["valor_total"])
            ws3.cell(row=i, column=8, value=r["pmr_pagamento"])
            ws3.cell(row=i, column=9, value=r["pmr_vencimento"])
            ws3.cell(row=i, column=10, value=r["pmr_cond_pag"])
            ws3.cell(row=i, column=11, value=r["delta_atraso"])

        # ABA 4: Evolucao Mensal
        ws5 = wb.create_sheet("Evolucao Mensal")
        hdrs5 = ["Mes Recebimento E5", "Movimentos E5", "Valor Recebido E5 (R$)", "Valor Recebido (R$)",
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
            ("E' uma media PONDERADA pelo valor efetivamente recebido na E5 (e5_valor).", False),
            ("", False),
            ("FORMULA GERAL:", True),
            ("PMR = SUM( dias_i * e5_valor_i ) / SUM( e5_valor_i )", False),
            ("", False),
            ("TRES METRICAS (todas contadas a partir de f2_emissao):", True),
            ("1. PMR Pagamento:  dias = e5_data - f2_emissao (recebimento real no banco).", False),
            ("2. PMR Vencimento: dias = e1_vencrea - f2_emissao (vencimento real negociado).", False),
            ("3. PMR Cond.Pag.:  dias = e1_vencto - f2_emissao (vencimento contratual).", False),
            ("A consolidacao por cliente e feita pela Razao Social, apos o filtro de status por CNPJ.", False),
            ("Cada linha do resumo pode reunir varios CNPJs e codigos de cliente; o detalhe preserva cada loja.", False),
            ("O filtro ATIVO/INATIVO/SEM STATUS e aplicado nos pagamentos E5 antes de recalcular os PMRs.", False),
            ("", False),
            ("COMO OS FILTROS FUNCIONAM:", True),
            ("Os dados sao cruzados no menor grao: cada movimento de E5 ligado ao titulo E1 e a NF SF2.", False),
            ("E1_tipo e mantido exclusivamente como NF (regra da extracao); nao existe selecao de outros tipos.", False),
            ("Status e definido por CNPJ na dim_clientes; CNPJ sem correspondencia recebe SEM STATUS.", False),
            ("Cada filtro aceita varias opcoes com OU dentro do proprio filtro.", False),
            ("Filtros diferentes funcionam com E: status E motivo E regional E segmento.", False),
            ("Apos filtrar os movimentos E5, todos os PMRs e totais sao recalculados.", False),
            ("A consolidacao por Razao Social acontece somente depois dos filtros.", False),
            ("Ex.: Status ATIVO + Motivos NOR,DEB = somente pagamentos E5 ativos cujo motivo seja NOR OU DEB.", False),
            ("Ex.: selecionar apenas Motivo FAT remove os demais pagamentos E5 e pode alterar valor e PMR.", False),
            ("Ex.: uma razao social com 5 CNPJs pode aparecer com menos CNPJs ao selecionar apenas ATIVO.", False),
            ("", False),
            ("EXEMPLO DE RECALCULO POR FILTRO:", True),
            ("Base: A=R$100.000 em 40d, motivo NOR; B=R$50.000 em 50d, motivo DEB; C=R$10.000 em 65d, motivo FAT.", False),
            ("Sem filtro: (40x100.000 + 50x50.000 + 65x10.000) / 160.000 = 44,7 dias.", False),
            ("Motivos NOR e DEB: (40x100.000 + 50x50.000) / 150.000 = 43,3 dias.", False),
            ("Somente motivo FAT: (65x10.000) / 10.000 = 65,0 dias.", False),
            ("O mesmo calculo vale para PMR Pagamento, Vencimento, Cond.Pagamento e Delta.", False),
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
            ("Agrupada pelo MES DE RECEBIMENTO (mes de e5_data). Para os movimentos E5 no", False),
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
            ("- A aba Resumo por Cliente agrega todos os CNPJs e lojas da mesma Razao Social.", False),
            ("- O filtro de status e aplicado por CNPJ no nivel dos pagamentos E5 antes da consolidacao.", False),
            ("- Delta Atraso = PMR Pagamento - PMR Cond.Pag. (positivo = cliente paga com atraso).", False),
            ("- e5_valor e' liquido de desconto (e5_valor - e5_vldesco), filtrado > 0 na extracao.", False),
            ("- O filtro Motivo E5 usa o campo e5_motbx e aceita multiplas selecoes.", False),
            ("- A E1 e extraida apenas com e1_tipo = NF; outros tipos nao fazem parte da base.", False),
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