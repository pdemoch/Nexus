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
                       IA / TopDown / realizado-ano-passado, + totalizadores.
  POST /salvar      -> grava vol_topdown rateado (rascunho). Digitado = somado.
  POST /congelar    -> (admin) congela a etapa e propaga p/ jusante não-congelado.
  POST /reabrir     -> (admin) reabre a etapa.
  GET  /status      -> a etapa está congelada?
"""

import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Optional
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_working_window_months, get_projection_window,
    escrever_volume_rateado, propagar_para_jusante, congelar_etapa,
    reabrir_etapa, etapa_congelada, registrar_log_auditoria, parse_date_safe,
    ETAPA_TOPDOWN,
)

router = APIRouter(prefix="/api/v1/topdown", tags=["Demanda Marketing (Top-Down)"])


# ---------------------------------------------------------------------
# Governança de acesso
# ---------------------------------------------------------------------
def require_marketing(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") not in ("Administrador", "Marketing"):
        raise HTTPException(403, "Acesso restrito à Diretoria de Marketing.")
    return usuario

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador congela etapas.")
    return usuario


# ---------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------
class AjusteTopDown(BaseModel):
    sku: str
    mes_projetado: str      # 'YYYY-MM' ou 'YYYY-MM-DD'
    novo_volume: int

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteTopDown]


# ---------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------
@router.get("/status")
def status(db: Session = Depends(get_db), _: dict = Depends(require_marketing)):
    ciclo = get_current_cycle(db)
    return {"ciclo": ciclo, "congelada": etapa_congelada(db, ciclo, ETAPA_TOPDOWN)}


# ---------------------------------------------------------------------
# GET /resumo — Aba "Visão Geral": tendências de volume/PMV + assertividade
# ---------------------------------------------------------------------
@router.get("/resumo")
def resumo(db: Session = Depends(get_db), _: dict = Depends(require_marketing)):
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
# GET /tabela  — a mesa de trabalho
# ---------------------------------------------------------------------
@router.get("/tabela")
def tabela(db: Session = Depends(get_db), _: dict = Depends(require_marketing)):
    """
    Matriz categoria -> segmento -> SKU, colunas por mês (M2..M4).
    Cada célula-SKU/mês traz: IA, TopDown (editável), realizado do ano passado
    (âncora), PMV e receita prevista. Mais totalizadores de topo (volume,
    faturamento previsto). Escopo = plano do ciclo ativo (get via ciclo).
    """
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    congelada = etapa_congelada(db, ciclo, ETAPA_TOPDOWN)

    # Plano do ciclo, agregado por SKU x mês (soma sobre clientes).
    plano = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao,'SEM DESCRICAO')   AS descricao,
               COALESCE(p.categoria,'SEM CATEGORIA')   AS categoria,
               COALESCE(p.segmento,'SEM SEGMENTO')     AS segmento,
               TO_CHAR(f.mes_projetado,'YYYY-MM-DD')   AS mes,
               SUM(f.vol_ia)                            AS ia,
               SUM(f.vol_topdown)                       AS topdown,
               AVG(f.pmv_aplicado)                      AS pmv,
               SUM(f.vol_topdown * f.pmv_aplicado)      AS receita_td
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
        td = int(r.topdown or 0); ia = int(r.ia or 0)
        receita = float(r.receita_td or 0)
        pmv = (receita / td) if td > 0 else float(r.pmv or 0)
        sk["meses"][r.mes] = {
            "ia": ia, "topdown": td, "pmv": round(pmv, 2),
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
        "meses": meses_iso,
        "categorias": categorias,
        "totais": {
            "volume": {mi: tot_vol[mi] for mi in meses_iso},
            "faturamento": {mi: round(tot_rs[mi], 2) for mi in meses_iso},
            "ia": {mi: tot_ia[mi] for mi in meses_iso},
        },
    }


# ---------------------------------------------------------------------
# GET /exportar  — CSV do plano Top-Down do ciclo
# ---------------------------------------------------------------------
@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(require_marketing)):
    """
    CSV do plano Top-Down no grão SKU × mês, com IA, TopDown, PMV e receita
    prevista. Separador ';' e decimal ',' (padrão pt-BR / Excel Brasil).
    """
    import io
    from fastapi.responses import StreamingResponse

    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)

    rows = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao,'')  AS descricao,
               COALESCE(p.categoria,'')  AS categoria,
               COALESCE(p.segmento,'')   AS segmento,
               TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
               SUM(f.vol_ia)             AS ia,
               SUM(f.vol_topdown)        AS topdown,
               SUM(f.vol_topdown * f.pmv_aplicado) AS receita_td
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    def _num(v):
        return f"{float(v or 0):.2f}".replace(".", ",")

    linhas = ["Categoria;Segmento;SKU;Descricao;Mes;IA (cx);TopDown (cx);PMV;Receita Prevista (R$)"]
    for r in rows:
        td = int(r.topdown or 0); receita = float(r.receita_td or 0)
        pmv = (receita / td) if td > 0 else 0.0
        linhas.append(";".join([
            r.categoria, r.segmento, r.sku, r.descricao, r.mes,
            str(int(r.ia or 0)), str(td), _num(pmv), _num(receita),
        ]))

    conteudo = "\r\n".join(linhas)
    buffer = io.StringIO(conteudo)
    resp = StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="demanda_marketing_{ciclo.replace("/","_")}.csv"'
    return resp


# ---------------------------------------------------------------------
# GET /dossie  — alimenta a GAVETA de insight (sob demanda, no clique do SKU)
# ---------------------------------------------------------------------
@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), _: dict = Depends(require_marketing)):
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
        return montar_dossie(db, sku, ciclo, meses, descricao=desc)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# POST /salvar  — grava vol_topdown rateado (rascunho)
# ---------------------------------------------------------------------
@router.post("/salvar")
def salvar(payload: PayloadSalvar, db: Session = Depends(get_db),
           usuario: dict = Depends(require_marketing)):
    """
    Grava cada ajuste em vol_topdown, rateado com balanço garantido
    (digitado = somado). Não congela — é rascunho editável.
    """
    try:
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_TOPDOWN):
            raise HTTPException(423, "Etapa já congelada pelo Administrador.")

        nome_user = usuario.get("nome", usuario.get("email", "?"))
        total = 0
        for aj in payload.ajustes:
            data_alvo = parse_date_safe(
                aj.mes_projetado if len(aj.mes_projetado) > 7 else aj.mes_projetado + "-01"
            )
            antigo = db.execute(text("""
                SELECT COALESCE(SUM(vol_topdown),0) FROM fato_ibp_granular
                WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo}).scalar()

            total += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=data_alvo,
                volume_alvo=int(aj.novo_volume), etapa=ETAPA_TOPDOWN,
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
        congelar_etapa(db, ciclo, ETAPA_TOPDOWN)
        res = propagar_para_jusante(db, ciclo, ETAPA_TOPDOWN)
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
        reabrir_etapa(db, ciclo, ETAPA_TOPDOWN)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))