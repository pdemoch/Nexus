"""
=====================================================================
ROUTER — DEMANDA COMERCIAL (Bottom-Up)  [reconstrução]
=====================================================================
Segunda etapa do bastão. A Gerência Comercial revê o número do Top-Down
cliente a cliente, trazendo a visão de baixo pra cima. Acesso: Administrador,
Gerente (via Sidebar).

Proposta específica desta tela (aprendida com a versão anterior):
  • Mostra o TopDown HERDADO ao lado do Bottom-Up editável — o gerente vê de
    onde partiu (a decisão da Diretoria) e ajusta sobre ela.
  • Semáforo sinaliza divergência do TOPDOWN (montante) além da IA.
  • Orçamento (rec_orcada) como âncora financeira.

Grava vol_bottomup. Peso de rateio: vol_topdown (cascata p/ ia). Congelamento
é ação do ADMIN. Requer que o TopDown esteja congelado (bastão recebido).
"""

import datetime
from dateutil.relativedelta import relativedelta
from typing import List
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_working_window_months,
    escrever_volume_rateado, propagar_para_jusante, congelar_etapa,
    reabrir_etapa, etapa_congelada, registrar_log_auditoria, parse_date_safe,
    ETAPA_TOPDOWN, ETAPA_BOTTOMUP,
)

router = APIRouter(prefix="/api/v1/gerenciamento", tags=["Demanda Comercial (Bottom-Up)"])


def require_gerente(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") not in ("Administrador", "Gerente"):
        raise HTTPException(403, "Acesso restrito à Gerência Comercial.")
    return usuario

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador congela etapas.")
    return usuario


class AjusteBottomUp(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteBottomUp]


@router.get("/status")
def status(db: Session = Depends(get_db), _: dict = Depends(require_gerente)):
    ciclo = get_current_cycle(db)
    return {
        "ciclo": ciclo,
        "congelada": etapa_congelada(db, ciclo, ETAPA_BOTTOMUP),
        "topdown_liberado": etapa_congelada(db, ciclo, ETAPA_TOPDOWN),
    }


@router.get("/tabela")
def tabela(db: Session = Depends(get_db), _: dict = Depends(require_gerente)):
    """
    Matriz categoria->segmento->SKU. Cada célula traz: IA, TopDown herdado
    (referência de montante), Bottom-Up (editável), orçamento, PMV e receita.
    Totalizadores de volume e faturamento previstos.
    """
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    congelada = etapa_congelada(db, ciclo, ETAPA_BOTTOMUP)
    topdown_liberado = etapa_congelada(db, ciclo, ETAPA_TOPDOWN)

    plano = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao,'SEM DESCRICAO')  AS descricao,
               COALESCE(p.categoria,'SEM CATEGORIA')  AS categoria,
               COALESCE(p.segmento,'SEM SEGMENTO')    AS segmento,
               TO_CHAR(f.mes_projetado,'YYYY-MM-DD')  AS mes,
               SUM(f.vol_ia)        AS ia,
               SUM(f.vol_topdown)   AS topdown,
               SUM(f.vol_bottomup)  AS bottomup,
               AVG(f.pmv_aplicado)  AS pmv
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    # Orçamento por SKU x mês (âncora financeira)
    orc = defaultdict(dict)
    orc_rows = db.execute(text("""
        SELECT sku, TO_CHAR(mes_projetado,'YYYY-MM-DD') AS mes, SUM(receita_orcamento) AS rec
        FROM fato_orcamento
        WHERE mes_projetado = ANY(:meses)
        GROUP BY sku, mes_projetado
    """), {"meses": meses}).fetchall()
    for r in orc_rows:
        orc[r.sku][r.mes] = float(r.rec or 0)

    tree: dict = {}
    tot_vol = {mi: 0 for mi in meses_iso}
    tot_rs = {mi: 0.0 for mi in meses_iso}
    tot_orc = {mi: 0.0 for mi in meses_iso}

    for r in plano:
        cat = tree.setdefault(r.categoria, {"nome": r.categoria, "segmentos": {}})
        seg = cat["segmentos"].setdefault(r.segmento, {"nome": r.segmento, "skus": {}})
        sk = seg["skus"].setdefault(r.sku, {"sku": r.sku, "descricao": r.descricao, "meses": {}})
        bu = int(r.bottomup or 0); td = int(r.topdown or 0); ia = int(r.ia or 0)
        pmv = float(r.pmv or 0)
        rec_orc = orc.get(r.sku, {}).get(r.mes, 0.0)
        sk["meses"][r.mes] = {
            "ia": ia, "topdown": td, "bottomup": bu, "pmv": round(pmv, 2),
            "receita": round(bu * pmv, 2), "rec_orcada": round(rec_orc, 2),
        }
        tot_vol[r.mes] += bu
        tot_rs[r.mes] += bu * pmv
        tot_orc[r.mes] += rec_orc

    categorias = []
    for cat in sorted(tree.values(), key=lambda c: c["nome"]):
        segs = []
        for seg in sorted(cat["segmentos"].values(), key=lambda s: s["nome"]):
            skus = sorted(seg["skus"].values(), key=lambda s: s["descricao"])
            segs.append({"nome": seg["nome"], "skus": skus})
        categorias.append({"nome": cat["nome"], "segmentos": segs})

    return {
        "ciclo": ciclo, "congelada": congelada, "topdown_liberado": topdown_liberado,
        "meses": meses_iso, "categorias": categorias,
        "totais": {
            "volume": {mi: tot_vol[mi] for mi in meses_iso},
            "faturamento": {mi: round(tot_rs[mi], 2) for mi in meses_iso},
            "orcamento": {mi: round(tot_orc[mi], 2) for mi in meses_iso},
        },
    }


@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), _: dict = Depends(require_gerente)):
    """Dossiê do SKU: histórico 24m, plurianual, FVA. Mesmo padrão do TopDown."""
    try:
        from app.api.routers.perfil_sku import comparativo_plurianual, fva_sku
        ciclo = get_current_cycle(db)
        hoje = datetime.date.today().replace(day=1)
        mes_fechado = (hoje - relativedelta(months=1)).strftime("%Y-%m-%d")

        plurianual = comparativo_plurianual(db, sku, mes_referencia=mes_fechado, ciclo_ativo=ciclo)
        fva = fva_sku(db, sku, mes_fechado)
        hist = db.execute(text("""
            SELECT TO_CHAR(data_pedido,'YYYY-MM') AS mes,
                   SUM(qt_pedido) AS vendido, SUM(qtfatura) AS faturado
            FROM fato_vendas
            WHERE sku = :sku AND data_pedido >= (CURRENT_DATE - INTERVAL '24 months')
            GROUP BY 1 ORDER BY 1
        """), {"sku": sku}).fetchall()
        serie = [{"mes": r.mes, "vendido": int(r.vendido or 0), "faturado": int(r.faturado or 0)} for r in hist]
        desc = db.execute(text("SELECT descricao FROM dim_produtos WHERE sku=:s"), {"s": sku}).scalar()
        return {"sku": sku, "descricao": desc or sku, "grafico": serie, "plurianual": plurianual, "fva": fva}
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.post("/salvar")
def salvar(payload: PayloadSalvar, db: Session = Depends(get_db),
           usuario: dict = Depends(require_gerente)):
    try:
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_BOTTOMUP):
            raise HTTPException(423, "Etapa já congelada pelo Administrador.")
        if not etapa_congelada(db, ciclo, ETAPA_TOPDOWN):
            raise HTTPException(423, "Aguardando a Diretoria liberar o Top-Down.")

        nome_user = usuario.get("nome", usuario.get("email", "?"))
        total = 0
        for aj in payload.ajustes:
            data_alvo = parse_date_safe(
                aj.mes_projetado if len(aj.mes_projetado) > 7 else aj.mes_projetado + "-01")
            antigo = db.execute(text("""
                SELECT COALESCE(SUM(vol_bottomup),0) FROM fato_ibp_granular
                WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo}).scalar()

            total += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=data_alvo,
                volume_alvo=int(aj.novo_volume), etapa=ETAPA_BOTTOMUP)
            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Demanda Comercial (Bottom-Up)",
                usuario=nome_user, sku=aj.sku, cliente="TODOS_OS_CLIENTES",
                mes=data_alvo, v_antigo=int(antigo or 0), v_novo=int(aj.novo_volume))

        db.commit()
        return {"status": "success", "linhas": total}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/congelar")
def congelar(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        congelar_etapa(db, ciclo, ETAPA_BOTTOMUP)
        res = propagar_para_jusante(db, ciclo, ETAPA_BOTTOMUP)
        db.commit()
        return {"status": "success", "propagou": res["propagou"], "preservou": res["preservou"]}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir")
def reabrir(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_BOTTOMUP)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(require_gerente)):
    import io
    from fastapi.responses import StreamingResponse
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    rows = db.execute(text("""
        SELECT f.sku, COALESCE(p.descricao,'') AS descricao,
               COALESCE(p.categoria,'') AS categoria, COALESCE(p.segmento,'') AS segmento,
               TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
               SUM(f.vol_ia) AS ia, SUM(f.vol_topdown) AS td, SUM(f.vol_bottomup) AS bu,
               AVG(f.pmv_aplicado) AS pmv
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    def _num(v): return f"{float(v or 0):.2f}".replace(".", ",")
    linhas = ["Categoria;Segmento;SKU;Descricao;Mes;IA (cx);TopDown (cx);BottomUp (cx);PMV;Receita (R$)"]
    for r in rows:
        bu = int(r.bu or 0); pmv = float(r.pmv or 0)
        linhas.append(";".join([r.categoria, r.segmento, r.sku, r.descricao, r.mes,
            str(int(r.ia or 0)), str(int(r.td or 0)), str(bu), _num(pmv), _num(bu*pmv)]))
    buffer = io.StringIO("\r\n".join(linhas))
    resp = StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="demanda_comercial_{ciclo.replace("/","_")}.csv"'
    return resp