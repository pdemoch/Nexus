"""
=====================================================================
ROUTER — DEMANDA MARKETING (Top-Down)  [reconstrução]
=====================================================================
Primeira etapa do bastão. A Diretoria/Marketing define o volume macro por SKU
na janela M2-M4. Acesso: Administrador, Marketing (via Sidebar).

Consome a espinha nova (shared_ibp): rateio canônico com balanço, propagação
que respeita congelamento, imutabilidade, regra N-2. Congelamento é ação do
ADMIN (congela a etapa e passa o bastão).

Endpoints:
  GET  /tabela      -> matriz categoria->segmento->SKU com colunas por mês,
                       IA / Supply / realizado-ano-passado, + totalizadores.
  POST /salvar      -> grava vol_supply rateado (rascunho). Digitado = somado.
  POST /congelar    -> (admin) congela a etapa e propaga p/ jusante não-congelado.
  POST /reabrir     -> (admin) reabre a etapa.
  GET  /status      -> a etapa está congelada?
"""

import datetime
import io
from dateutil.relativedelta import relativedelta
from typing import List, Optional
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_working_window_months, get_projection_window,
    escrever_volume_rateado, propagar_para_jusante, congelar_etapa,
    reabrir_etapa, etapa_congelada, registrar_log_auditoria, parse_date_safe,
    ETAPA_SUPPLY,
    ETAPA_METAS,
)

router = APIRouter(prefix="/api/v1/supply", tags=["Supply Review"])


# ---------------------------------------------------------------------
# Governança de acesso
# ---------------------------------------------------------------------
def require_supply(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") not in ("Administrador", "Supply Chain"):
        raise HTTPException(403, "Acesso restrito ao Supply Chain.")
    return usuario

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador congela etapas.")
    return usuario


# ---------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------
class AjusteSupply(BaseModel):
    sku: str
    mes_projetado: str      # 'YYYY-MM' ou 'YYYY-MM-DD'
    novo_volume: int

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteSupply]


# ---------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------
@router.get("/status")
def status(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    ciclo = get_current_cycle(db)
    return {"ciclo": ciclo, "congelada": etapa_congelada(db, ciclo, ETAPA_SUPPLY)}


# ---------------------------------------------------------------------
# GET /resumo — Aba "Visão Geral": tendências de volume/PMV + assertividade
# ---------------------------------------------------------------------
@router.get("/resumo")
def resumo(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    """
    Pacote da primeira tela que o planejador vê: quem cresce/cai em volume
    (vl_pedido, YTD comparável), quem cresce/cai em PMV, e a assertividade
    Humano/IA/Ano-passado dos últimos meses fechados (janela dinâmica, piso
    06/2026). Por SKU e por categoria, sem corte — o front classifica.
    """
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        return resumo_marketing(db, ciclo)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# GET /exportar-visao-geral — Excel com 4 abas (Volume cx, Valor R$, PMV, Assertividade)
# ---------------------------------------------------------------------
@router.get("/exportar-visao-geral")
def exportar_visao_geral(
    nivel: str = Query("categoria", regex="^(categoria|sku)$"),
    db: Session = Depends(get_db),
    _: dict = Depends(require_supply),
):
    """
    Gera um .xlsx com 4 abas:
      - Volume (cx)       : qtd_pedida por SKU/categoria, grade de anos alinhada, YoY, CAGR
      - Valor de Venda    : vl_pedido por SKU/categoria, mesma estrutura
      - PMV               : preço médio ponderado, mesma estrutura
      - Assertividade     : Humano vs IA vs Realizado dos últimos meses fechados

    CAGR = (Valor_final / Valor_inicial)^(1/nº_anos) − 1
    Taxa de crescimento médio anual composto desde a 1ª venda até o ano atual.
    """
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        dados = resumo_marketing(db, ciclo)

        label_col = "categoria" if nivel == "categoria" else "descricao"

        # --- helpers ---
        def _cagr(anos_com_dado, campo):
            filtrados = [a for a in anos_com_dado if a.get(campo, 0) and a[campo] > 0]
            if len(filtrados) < 2:
                return None
            vi, vf = filtrados[0][campo], filtrados[-1][campo]
            n = len(filtrados) - 1
            if vi <= 0:
                return None
            return (vf / vi) ** (1 / n) - 1

        def _pct(v):
            if v is None:
                return "—"
            return f"{v*100:+.1f}%"

        def _build_vol_df(rows, campo_val, campo_tend, campo_var):
            """Monta DataFrame com grade de anos alinhada, YoY e CAGR."""
            # grade global de anos
            todos_anos = sorted({a["ano"] for r in rows for a in r.get("anos", [])})
            registros = []
            for r in rows:
                idx = {a["ano"]: a.get(campo_val) for a in r.get("anos", [])}
                rec = {
                    "Nome": r.get(label_col) or r.get("sku", ""),
                    "Categoria": r.get("categoria", ""),
                    "Tendência": r.get(campo_tend, ""),
                    "CAGR": _pct(_cagr(r.get("anos", []), campo_val)),
                }
                for i, ano in enumerate(todos_anos):
                    rec[str(ano)] = idx.get(ano, "")
                    if i > 0:
                        ant_ano = todos_anos[i-1]
                        ant_v, rec_v = idx.get(ant_ano), idx.get(ano)
                        if ant_v and ant_v > 0 and rec_v is not None:
                            rec[f"{ant_ano}→{ano}"] = _pct((rec_v - ant_v) / ant_v)
                        else:
                            rec[f"{ant_ano}→{ano}"] = "—"
                # ordena colunas: ano1, ant→ano2, ano2, ant→ano3, ano3...
                registros.append(rec)

            # Constrói colunas na ordem certa (intercalado)
            cols_base = ["Nome", "Categoria", "Tendência", "CAGR"]
            cols_anos = []
            for i, ano in enumerate(todos_anos):
                if i > 0:
                    cols_anos.append(f"{todos_anos[i-1]}→{ano}")
                cols_anos.append(str(ano))
            if nivel == "categoria":
                cols_base = [c for c in cols_base if c != "Categoria"]
            return pd.DataFrame(registros, columns=cols_base + cols_anos)

        # Aba 1 — Volume (cx)
        src_vol = dados["tendencia_volume_categoria"] if nivel == "categoria" else dados["tendencia_volume_sku"]
        df_vol = _build_vol_df(src_vol, "vol_cx", "tendencia_cx", "variacao_pct_cx")

        # Aba 2 — Valor de Venda (R$)
        df_val = _build_vol_df(src_vol, "vol_rs", "tendencia_rs", "variacao_pct_rs")

        # Aba 3 — PMV
        src_pmv = dados["tendencia_pmv_categoria"] if nivel == "categoria" else dados["tendencia_pmv_sku"]
        df_pmv = _build_vol_df(src_pmv, "pmv", "tendencia", "variacao_pct")

        # Aba 4 — Assertividade
        src_ass = dados["assertividade_categoria"] if nivel == "categoria" else dados["assertividade_sku"]
        ass_rows = []
        for r in src_ass:
            ass_rows.append({
                "Nome": r.get(label_col) or r.get("sku", ""),
                "Categoria": r.get("categoria", ""),
                "Realizado (cx)": r.get("vendido_cx", 0),
                "Humano (cx)": r.get("humano_cx", 0),
                "Aderência Humano": _pct(r.get("aderencia_humano")),
                "IA (cx)": r.get("ia_cx", 0),
                "Aderência IA": _pct(r.get("aderencia_ia")),
                "Vencedor": r.get("vencedor", "—"),
            })
        cols_ass = ["Nome", "Realizado (cx)", "Humano (cx)", "Aderência Humano",
                    "IA (cx)", "Aderência IA", "Vencedor"]
        if nivel == "sku":
            cols_ass.insert(1, "Categoria")
        df_ass = pd.DataFrame(ass_rows, columns=cols_ass)

        # Meses auditados (para título da aba)
        meses_label = ", ".join(dados.get("meses_auditados", []))

        # Gera o Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            # Rodapé explicativo no primeiro df
            nota_cagr = pd.DataFrame([
                ["CAGR = Compound Annual Growth Rate (Crescimento Médio Anual Composto)"],
                ["Fórmula: (Valor_final / Valor_inicial)^(1/nº_anos) − 1"],
                ["Representa a taxa constante que, aplicada a cada ano, levaria do 1º ao último valor."],
                ["Mais estável que a variação simples: neutraliza picos e quedas pontuais."],
            ], columns=["Nota"])

            df_vol.to_excel(writer, index=False, sheet_name="Volume (cx)")
            nota_cagr.to_excel(writer, index=False, sheet_name="Volume (cx)",
                               startrow=len(df_vol) + 2, header=False)

            df_val.to_excel(writer, index=False, sheet_name="Valor de Venda (R$)")
            nota_cagr.to_excel(writer, index=False, sheet_name="Valor de Venda (R$)",
                               startrow=len(df_val) + 2, header=False)

            df_pmv.to_excel(writer, index=False, sheet_name="PMV")
            nota_cagr.to_excel(writer, index=False, sheet_name="PMV",
                               startrow=len(df_pmv) + 2, header=False)

            df_ass.to_excel(writer, index=False,
                            sheet_name=f"Assertividade ({meses_label})" if meses_label else "Assertividade")

        buffer.seek(0)
        ciclo_safe = ciclo.replace("/", "_")
        nome_arquivo = f"visao_geral_{ciclo_safe}_{nivel}.xlsx"
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
        )
    except Exception as e:
        raise HTTPException(500, repr(e))



@router.get("/tabela")
def tabela(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    """
    Matriz categoria -> segmento -> SKU, colunas por mês (M2..M4).
    Cada célula-SKU/mês traz: IA, Supply (editável), realizado do ano passado
    (âncora), PMV e receita prevista. Mais totalizadores de topo (volume,
    faturamento previsto). Escopo = plano do ciclo ativo (get via ciclo).
    """
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    upstream_ok   = etapa_congelada(db, ciclo, ETAPA_METAS)
    propria_congelada = etapa_congelada(db, ciclo, ETAPA_SUPPLY)
    aguardando    = not upstream_ok
    congelada     = propria_congelada or aguardando

    # Plano do ciclo, agregado por SKU x mês (soma sobre clientes).
    plano = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao,'SEM DESCRICAO')   AS descricao,
               COALESCE(p.categoria,'SEM CATEGORIA')   AS categoria,
               COALESCE(p.segmento,'SEM SEGMENTO')     AS segmento,
               TO_CHAR(f.mes_projetado,'YYYY-MM-DD')   AS mes,
               SUM(f.vol_ia)                            AS ia,
               SUM(f.vol_supply)                       AS supply,
               AVG(f.pmv_aplicado)                      AS pmv,
               SUM(f.vol_supply * f.pmv_aplicado)      AS receita_td
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    # Realizado do ANO PASSADO (mesmo mês), por SKU — âncora anti-otimismo.
    realizado_ap = defaultdict(dict)
    for m in meses:
        m_ap = (m - relativedelta(years=1))
        rows = db.execute(text("""
            SELECT sku, SUM(qt_pedido) AS cx
            FROM fato_vendas
            WHERE TO_CHAR(data_pedido,'YYYY-MM') = :ym
            GROUP BY sku
        """), {"ym": m_ap.strftime("%Y-%m")}).fetchall()
        for r in rows:
            realizado_ap[r.sku][m.strftime("%Y-%m-%d")] = int(r.cx or 0)

    # Orçamento por SKU x mês (receita em R$ para o período planejado).
    orc_idx: dict = {}
    orc_rows = db.execute(text("""
        SELECT sku, TO_CHAR(mes_projetado,'YYYY-MM-DD') AS mes,
               SUM(receita_orcamento) AS orc
        FROM fato_orcamento
        WHERE mes_projetado = ANY(:meses)
        GROUP BY sku, mes_projetado
    """), {"meses": meses}).fetchall()
    for r in orc_rows:
        orc_idx.setdefault(r.sku, {})[r.mes] = round(float(r.orc or 0), 2)

    # Monta árvore categoria -> segmento -> SKU
    tree: dict = {}
    tot_vol = {mi: 0 for mi in meses_iso}
    tot_rs = {mi: 0.0 for mi in meses_iso}
    tot_ia = {mi: 0 for mi in meses_iso}

    for r in plano:
        cat = tree.setdefault(r.categoria, {"nome": r.categoria, "segmentos": {}})
        seg = cat["segmentos"].setdefault(r.segmento, {"nome": r.segmento, "skus": {}})
        sk = seg["skus"].setdefault(r.sku, {
            "sku": r.sku, "descricao": r.descricao, "meses": {}
        })
        td = int(r.supply or 0); ia = int(r.ia or 0)
        receita = float(r.receita_td or 0)
        pmv = (receita / td) if td > 0 else float(r.pmv or 0)
        sk["meses"][r.mes] = {
            "ia": ia, "supply": td, "pmv": round(pmv, 2),
            "receita": round(receita, 2),
            "orcamento": orc_idx.get(r.sku, {}).get(r.mes, None),
            "realizado_ap": realizado_ap.get(r.sku, {}).get(r.mes, None),
        }
        tot_vol[r.mes] += td
        tot_rs[r.mes] += receita
        tot_ia[r.mes] += ia

    # Serializa (dicts -> listas ordenadas)
    categorias = []
    for cat in sorted(tree.values(), key=lambda c: c["nome"]):
        segs = []
        for seg in sorted(cat["segmentos"].values(), key=lambda s: s["nome"]):
            skus = sorted(seg["skus"].values(), key=lambda s: s["descricao"])
            segs.append({"nome": seg["nome"], "skus": skus})
        categorias.append({"nome": cat["nome"], "segmentos": segs})

    return {
        "ciclo": ciclo,
        "congelada": congelada,
        "congelada_propria": propria_congelada,
        "aguardando_upstream": aguardando,
        "motivo_bloqueio": ("Metas Comercial ainda nao congelou o plano." if aguardando
                             else "Etapa congelada pelo Administrador." if propria_congelada else None),
        "meses": meses_iso,
        "categorias": categorias,
        "totais": {
            "volume": {mi: tot_vol[mi] for mi in meses_iso},
            "faturamento": {mi: round(tot_rs[mi], 2) for mi in meses_iso},
            "ia": {mi: tot_ia[mi] for mi in meses_iso},
        },
    }


# ---------------------------------------------------------------------
# GET /exportar  — Excel do plano Top-Down (cópia de segurança do preenchimento)
# ---------------------------------------------------------------------
@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    """
    Excel (.xlsx) do plano Top-Down no grão SKU × mês — cópia de segurança
    para o planejador ao finalizar o preenchimento. Inclui: categoria,
    segmento, SKU, descrição, mês, IA (cx), vol_supply (cx), PMV e
    receita prevista (R$) = vol_supply × PMV.
    """
    try:
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)

        rows = db.execute(text("""
            SELECT f.sku,
                   COALESCE(p.descricao,'')  AS descricao,
                   COALESCE(p.categoria,'')  AS categoria,
                   COALESCE(p.segmento,'')   AS segmento,
                   TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
                   SUM(f.vol_ia)             AS ia,
                   SUM(f.vol_supply)        AS supply,
                   SUM(f.vol_supply * f.pmv_aplicado) AS receita_td,
                   COALESCE(o.receita_orcamento, 0) AS orcamento
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON p.sku = f.sku
            LEFT JOIN fato_orcamento o
              ON o.sku = f.sku AND o.mes_projetado = f.mes_projetado
            WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
            GROUP BY f.sku, p.descricao, p.categoria, p.segmento,
                     f.mes_projetado, o.receita_orcamento
            ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
        """), {"c": ciclo, "meses": meses}).fetchall()

        registros = []
        for r in rows:
            td = int(r.supply or 0)
            receita = float(r.receita_td or 0)
            pmv = (receita / td) if td > 0 else 0.0
            registros.append({
                "Categoria":           r.categoria,
                "Segmento":            r.segmento,
                "SKU":                 r.sku,
                "Descrição":           r.descricao,
                "Mês":                 r.mes,
                "IA (cx)":             int(r.ia or 0),
                "Vol. Supply (cx)":   td,
                "PMV (R$)":            round(pmv, 2),
                "Receita Prevista (R$)": round(receita, 2),
                "Orçamento (R$)":      round(float(r.orcamento or 0), 2),
            })

        df = pd.DataFrame(registros)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Plano Supply")
            # Rodapé informativo
            notas = pd.DataFrame([
                [f"Ciclo: {ciclo}"],
                [f"Meses: {', '.join(m.strftime('%m/%Y') for m in meses)}"],
                ["Receita Prevista = Vol. Supply × PMV aplicado"],
                ["Gerado pelo Nexus S&OP — cópia de segurança do preenchimento"],
            ], columns=["Nota"])
            notas.to_excel(writer, index=False, sheet_name="Plano Supply",
                           startrow=len(df) + 2, header=False)

        buffer.seek(0)
        nome = f"supply_{ciclo.replace('/','_')}.xlsx"
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        )
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# GET /dossie  — alimenta a GAVETA de insight (sob demanda, no clique do SKU)
# ---------------------------------------------------------------------
@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    """
    Dossiê completo do SKU para o painel inferior (split-screen): gráfico
    unificado realizado+previsto, comparações do mês-foco (orçamento, ciclo
    anterior, ano anterior), insights em texto, plurianual e placar FVA.
    Empacotado pelo perfil_sku.montar_dossie (fonte única).
    """
    try:
        from app.api.routers.perfil_sku import montar_dossie
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        desc = db.execute(text(
            "SELECT descricao FROM dim_produtos WHERE sku=:s"), {"s": sku}).scalar()
        return montar_dossie(db, sku, ciclo, meses, descricao=desc,
                             coluna_meta="vol_supply")
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# POST /salvar  — grava vol_supply rateado (rascunho)
# ---------------------------------------------------------------------
@router.post("/salvar")
def salvar(payload: PayloadSalvar, db: Session = Depends(get_db),
           usuario: dict = Depends(require_supply)):
    """
    Grava cada ajuste em vol_supply, rateado com balanço garantido
    (digitado = somado). Não congela — é rascunho editável.
    """
    try:
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_METAS):
            raise HTTPException(423, "Metas Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_SUPPLY):
            raise HTTPException(423, "Etapa já congelada pelo Administrador.")

        nome_user = usuario.get("nome", usuario.get("email", "?"))
        total = 0
        for aj in payload.ajustes:
            data_alvo = parse_date_safe(
                aj.mes_projetado if len(aj.mes_projetado) > 7 else aj.mes_projetado + "-01"
            )
            antigo = db.execute(text("""
                SELECT COALESCE(SUM(vol_supply),0) FROM fato_ibp_granular
                WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo}).scalar()

            total += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=data_alvo,
                volume_alvo=int(aj.novo_volume), etapa=ETAPA_SUPPLY,
            )
            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Demanda Marketing (Top-Down)",
                usuario=nome_user, sku=aj.sku, cliente="TODOS_OS_CLIENTES",
                mes=data_alvo, v_antigo=int(antigo or 0), v_novo=int(aj.novo_volume))

        db.commit()
        return {"status": "success", "linhas": total}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# POST /congelar  — (admin) fecha a etapa e passa o bastão
# ---------------------------------------------------------------------
@router.post("/congelar")
def congelar(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        congelar_etapa(db, ciclo, ETAPA_SUPPLY)
        res = propagar_para_jusante(db, ciclo, ETAPA_SUPPLY)
        db.commit()
        return {"status": "success",
                "propagou": res["propagou"], "preservou": res["preservou"]}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# POST /reabrir  — (admin)
# ---------------------------------------------------------------------
@router.post("/reabrir")
def reabrir(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_SUPPLY)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))