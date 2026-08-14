"""
router_irrestrita.py  —  Demanda Irrestrita

Tela de consenso entre IA, Marketing (vol_topdown) e Comercial (vol_bottomup).
Grava sempre em vol_bottomup e propaga para jusante (Metas, Supply, Final).

Contrato:
  GET  /api/v1/irrestrita/status
  GET  /api/v1/irrestrita/tabela      → árvore categoria -> segmento -> SKU
  POST /api/v1/irrestrita/salvar      → {sku, mes_projetado, novo_volume}[]
  GET  /api/v1/irrestrita/exportar    → Excel
  GET  /api/v1/irrestrita/resumo      → Visão Geral (mesmo payload das outras telas)
  GET  /api/v1/irrestrita/exportar-visao-geral
  GET  /api/v1/irrestrita/dossie      → dossiê do SKU (coluna_meta = vol_bottomup)
  POST /api/v1/irrestrita/congelar    → Admin congela ETAPA_BOTTOMUP
  POST /api/v1/irrestrita/reabrir     → Admin reabre ETAPA_BOTTOMUP

Posição no bastão: TopDown → **BottomUp (esta tela)** → Metas → Supply → Final
"""

import io
import datetime
from collections import defaultdict
from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from dateutil.relativedelta import relativedelta
import pandas as pd

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_working_window_months,
    etapa_congelada,
    congelar_etapa,
    reabrir_etapa,
    propagar_para_jusante,
    propagar_linha_jusante,
    escrever_volume_rateado,
    registrar_log_auditoria,
    ETAPA_TOPDOWN,
    ETAPA_BOTTOMUP,
)

router = APIRouter(prefix="/api/v1/irrestrita", tags=["Demanda Irrestrita"])


# ---------------------------------------------------------------------------
# Helpers de acesso
# ---------------------------------------------------------------------------
def require_irrestrita(u: dict = Depends(get_current_user)):
    """
    Acesso permitido a: Administrador, Gerente, C-Level, Marketing, Supply Chain.
    Tela estratégica de alinhamento entre demandas — amplo acesso de leitura/escrita.
    """
    permitidos = {"Administrador", "Gerente", "C-Level", "Marketing", "Supply Chain"}
    if u.get("funcao") not in permitidos:
        raise HTTPException(403, "Acesso restrito à equipe de planejamento estratégico.")
    return u


def require_admin(u: dict = Depends(get_current_user)):
    if u.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador pode congelar/reabrir etapas.")
    return u


def _parse_mes(mes_str: str):
    """Aceita YYYY-MM ou YYYY-MM-DD."""
    if len(mes_str) == 7:
        mes_str += "-01"
    return datetime.date.fromisoformat(mes_str)


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------
@router.get("/status")
def status(db: Session = Depends(get_db), _: dict = Depends(require_irrestrita)):
    ciclo = get_current_cycle(db)
    upstream_ok = etapa_congelada(db, ciclo, ETAPA_TOPDOWN)
    propria     = etapa_congelada(db, ciclo, ETAPA_BOTTOMUP)
    return {
        "ciclo": ciclo,
        "congelada": propria or not upstream_ok,
        "congelada_propria": propria,
        "aguardando_upstream": not upstream_ok,
    }


# ---------------------------------------------------------------------------
# GET /resumo  — Visão Geral (mesmo payload das outras telas)
# ---------------------------------------------------------------------------
@router.get("/resumo")
def resumo(db: Session = Depends(get_db), _: dict = Depends(require_irrestrita)):
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        return resumo_marketing(db, ciclo)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /exportar-visao-geral
# ---------------------------------------------------------------------------
@router.get("/exportar-visao-geral")
def exportar_visao_geral(
    nivel: str = Query("categoria", regex="^(categoria|sku)$"),
    db: Session = Depends(get_db),
    _: dict = Depends(require_irrestrita),
):
    try:
        from app.api.routers.router_topdown import exportar_visao_geral as _exp
        return _exp(nivel=nivel, db=db, _=_)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /tabela  — árvore categoria -> segmento -> SKU com 5 visões por célula
# ---------------------------------------------------------------------------
@router.get("/tabela")
def tabela(db: Session = Depends(get_db), u: dict = Depends(require_irrestrita)):
    """
    Cada célula SKU/mês traz:
      ia        → vol_ia   (previsão do modelo)
      topdown   → vol_topdown (proposta do Marketing)
      bottomup  → vol_bottomup (proposta do Comercial — EDITÁVEL)
      realizado_ap → vendido mesmo mês ano passado (âncora)
      orcamento → orçamento em R$ (referência empresa)
      pmv       → preço médio de venda aplicado
    """
    ciclo    = get_current_cycle(db)
    meses    = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]

    upstream_ok       = etapa_congelada(db, ciclo, ETAPA_TOPDOWN)
    propria_congelada = etapa_congelada(db, ciclo, ETAPA_BOTTOMUP)
    aguardando        = not upstream_ok
    congelada         = propria_congelada or aguardando

    # ── Plano: IA + TopDown + BottomUp por SKU/mês ──────────────────────────
    plano = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao, 'SEM DESCRICAO') AS descricao,
               COALESCE(p.categoria, 'SEM CATEGORIA') AS categoria,
               COALESCE(p.segmento,  'SEM SEGMENTO')  AS segmento,
               TO_CHAR(f.mes_projetado, 'YYYY-MM-DD') AS mes,
               SUM(f.vol_ia)                           AS ia,
               SUM(f.vol_topdown)                      AS topdown,
               SUM(f.vol_bottomup)                     AS bottomup,
               COALESCE(
                   SUM(f.vol_bottomup * f.pmv_aplicado)
                   / NULLIF(SUM(f.vol_bottomup), 0),
                   AVG(f.pmv_aplicado)
               )                                       AS pmv
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    # Realizado ano passado (mesmo mês) — cx E rs reais, sem re-precificação
    realizado_ap: dict = defaultdict(dict)
    for m in meses:
        m_ap = m - relativedelta(years=1)
        rows = db.execute(text("""
            SELECT sku, SUM(qt_pedido) AS cx, SUM(vl_pedido) AS rs
            FROM fato_vendas
            WHERE TO_CHAR(data_pedido, 'YYYY-MM') = :ym
            GROUP BY sku
        """), {"ym": m_ap.strftime("%Y-%m")}).fetchall()
        for r in rows:
            realizado_ap[r.sku][m.strftime("%Y-%m-%d")] = {
                "cx": int(r.cx or 0),
                "rs": round(float(r.rs or 0), 2),
            }

    # ── Orçamento por SKU/mês ──────────────────────────────────────────────
    orc_idx: dict = {}
    for r in db.execute(text("""
        SELECT sku, TO_CHAR(mes_projetado, 'YYYY-MM-DD') AS mes,
               SUM(receita_orcamento) AS orc
        FROM fato_orcamento
        WHERE mes_projetado = ANY(:meses)
        GROUP BY sku, mes_projetado
    """), {"meses": meses}).fetchall():
        orc_idx.setdefault(r.sku, {})[r.mes] = round(float(r.orc or 0), 2)

    # ── Monta árvore ──────────────────────────────────────────────────────
    tree: dict = {}
    tot: dict = {mi: {"ia": 0, "topdown": 0, "bottomup": 0, "fat_bu": 0.0} for mi in meses_iso}

    for r in plano:
        ia  = int(r.ia or 0)
        td  = int(r.topdown or 0)
        bu  = int(r.bottomup or 0)
        pmv = float(r.pmv or 0)

        cat = tree.setdefault(r.categoria, {"nome": r.categoria, "segmentos": {}})
        seg = cat["segmentos"].setdefault(r.segmento, {"nome": r.segmento, "skus": {}})
        sk  = seg["skus"].setdefault(r.sku, {
            "sku": r.sku, "descricao": r.descricao, "meses": {}
        })
        sk["meses"][r.mes] = {
            "ia":          ia,
            "topdown":     td,
            "bottomup":    bu,
            "pmv":         round(pmv, 2),
            "orcamento":   orc_idx.get(r.sku, {}).get(r.mes),
            "realizado_ap": realizado_ap.get(r.sku, {}).get(r.mes),
            # realizado_ap = {"cx": int, "rs": float} — valor real do ano passado
        }
        tot[r.mes]["ia"]       += ia
        tot[r.mes]["topdown"]  += td
        tot[r.mes]["bottomup"] += bu
        tot[r.mes]["fat_bu"]   += bu * pmv

    # Serializa
    categorias = []
    for cat in sorted(tree.values(), key=lambda c: c["nome"]):
        segs = []
        for seg in sorted(cat["segmentos"].values(), key=lambda s: s["nome"]):
            skus = sorted(seg["skus"].values(), key=lambda s: s["descricao"])
            segs.append({"nome": seg["nome"], "skus": skus})
        categorias.append({"nome": cat["nome"], "segmentos": segs})

    return {
        "ciclo":              ciclo,
        "meses":              meses_iso,
        "congelada":          congelada,
        "congelada_propria":  propria_congelada,
        "aguardando_upstream": aguardando,
        "motivo_bloqueio": (
            "Demanda Marketing ainda não congelou o plano." if aguardando
            else "Etapa congelada pelo Administrador." if propria_congelada else None
        ),
        "sou_admin": u.get("funcao") == "Administrador",
        "categorias": categorias,
        "totais": tot,
    }


# ---------------------------------------------------------------------------
# POST /salvar  — grava vol_bottomup e propaga
# ---------------------------------------------------------------------------
class AjusteIrrestrita(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int


class PayloadSalvar(BaseModel):
    ajustes: List[AjusteIrrestrita]


@router.post("/salvar")
def salvar(payload: PayloadSalvar,
           db: Session = Depends(get_db),
           u: dict = Depends(require_irrestrita)):
    try:
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_BOTTOMUP):
            raise HTTPException(423, "Etapa já congelada. Reabra para editar.")
        if not etapa_congelada(db, ciclo, ETAPA_TOPDOWN):
            raise HTTPException(423, "Demanda Marketing ainda não congelou o plano.")

        nome_user = u.get("nome", u.get("email", "?"))
        total = 0
        for aj in payload.ajustes:
            data_alvo = _parse_mes(aj.mes_projetado)
            antigo = db.execute(text("""
                SELECT COALESCE(SUM(vol_bottomup), 0) FROM fato_ibp_granular
                WHERE ciclo_sop = :c AND sku = :s AND mes_projetado = :m
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo}).scalar()

            total += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=data_alvo,
                volume_alvo=int(aj.novo_volume), etapa=ETAPA_BOTTOMUP,
            )
            propagar_linha_jusante(db, ciclo, aj.sku, data_alvo, ETAPA_BOTTOMUP)
            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Demanda Irrestrita",
                usuario=nome_user, sku=aj.sku, cliente="TODOS_OS_CLIENTES",
                mes=data_alvo, v_antigo=int(antigo or 0), v_novo=int(aj.novo_volume)
            )

        db.commit()
        return {"status": "ok", "linhas": total}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /exportar  — Excel de segurança
# ---------------------------------------------------------------------------
@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(require_irrestrita)):
    try:
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        rows  = db.execute(text("""
            SELECT p.categoria, p.segmento, f.sku, p.descricao,
                   TO_CHAR(f.mes_projetado, 'MM/YYYY') AS mes,
                   SUM(f.vol_ia)                        AS ia,
                   SUM(f.vol_topdown)                   AS topdown,
                   SUM(f.vol_bottomup)                  AS bottomup,
                   AVG(f.pmv_aplicado)                  AS pmv,
                   SUM(f.vol_bottomup * f.pmv_aplicado) AS receita
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON p.sku = f.sku
            WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:m)
            GROUP BY p.categoria, p.segmento, f.sku, p.descricao, f.mes_projetado
            ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
        """), {"c": ciclo, "m": meses}).fetchall()

        registros = [{
            "Categoria":        r.categoria,
            "Segmento":         r.segmento,
            "SKU":              r.sku,
            "Descrição":        r.descricao,
            "Mês":              r.mes,
            "IA (cx)":          int(r.ia or 0),
            "Marketing (cx)":   int(r.topdown or 0),
            "Comercial (cx)":   int(r.bottomup or 0),
            "PMV (R$)":         round(float(r.pmv or 0), 2),
            "Receita Prev. (R$)": round(float(r.receita or 0), 2),
        } for r in rows]

        df  = pd.DataFrame(registros)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Demanda Irrestrita")
            nota = pd.DataFrame([[f"Ciclo: {ciclo}"],
                                  [f"Meses: {', '.join(m.strftime('%m/%Y') for m in meses)}"],
                                  ["Gerado pelo Nexus S&OP — Demanda Irrestrita"]], columns=["Nota"])
            nota.to_excel(w, index=False, sheet_name="Demanda Irrestrita",
                          startrow=len(df) + 2, header=False)
        buf.seek(0)
        nome = f"irrestrita_{ciclo.replace('/', '_')}.xlsx"
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /dossie  — dossiê do SKU (coluna_meta = vol_bottomup)
# ---------------------------------------------------------------------------
@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db),
           _: dict = Depends(require_irrestrita)):
    try:
        from app.api.routers.perfil_sku import montar_dossie
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        desc  = db.execute(
            text("SELECT descricao FROM dim_produtos WHERE sku = :s"), {"s": sku}
        ).scalar()
        return montar_dossie(db, sku, ciclo, meses, descricao=desc,
                             coluna_meta="vol_bottomup")
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# POST /congelar  — (Admin) fecha ETAPA_BOTTOMUP e passa o bastão
# ---------------------------------------------------------------------------
@router.post("/congelar")
def congelar(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        congelar_etapa(db, ciclo, ETAPA_BOTTOMUP)
        res = propagar_para_jusante(db, ciclo, ETAPA_BOTTOMUP)
        db.commit()
        return {"status": "congelada", "propagacao": res}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# POST /reabrir  — (Admin) reabre ETAPA_BOTTOMUP
# ---------------------------------------------------------------------------
@router.post("/reabrir")
def reabrir(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_BOTTOMUP)
        db.commit()
        return {"status": "reaberta"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))