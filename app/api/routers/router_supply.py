"""
=====================================================================
ROUTER — SUPPLY REVIEW  [reconstrução]
=====================================================================
Quarta etapa do bastão. A fábrica confronta a demanda comercial (Meta) com a
capacidade produtiva: corta o que não consegue produzir ou injeta se há folga.
Acesso: Administrador, Supply Chain (via Sidebar).

Proposta específica desta tela:
  • Compara Meta (vol_meta, a demanda comercial) vs Supply (vol_supply, a
    capacidade). O gap é o corte/injeção.
  • Justificativa ESTRUTURADA: motivo (categoria) + texto livre, exigida quando
    o supply diverge da meta. Grava em motivo_supply / justificativa_supply.
  • Rateia sobre vol_meta (a decisão comercial já é o peso mais informado).

Grava vol_supply. Congelamento é ação do ADMIN. Requer Metas congelada.
"""

import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Optional
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
    ETAPA_METAS, ETAPA_SUPPLY,
)

router = APIRouter(prefix="/api/v1/supply", tags=["Supply Review"])


def require_supply(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") not in ("Administrador", "Supply Chain"):
        raise HTTPException(403, "Acesso restrito ao time de Supply Chain.")
    return usuario

def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador congela etapas.")
    return usuario


class AjusteSupply(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int
    motivo: Optional[str] = None
    justificativa: Optional[str] = None

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteSupply]


@router.get("/status")
def status(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    ciclo = get_current_cycle(db)
    return {
        "ciclo": ciclo,
        "congelada": etapa_congelada(db, ciclo, ETAPA_SUPPLY),
        "metas_liberada": etapa_congelada(db, ciclo, ETAPA_METAS),
    }


@router.get("/tabela")
def tabela(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    """
    Categoria->segmento->SKU. Cada célula: Meta (demanda comercial), Supply
    (editável), gap, motivo e justificativa correntes. Totalizadores de volume
    demandado vs suportado.
    """
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    congelada = etapa_congelada(db, ciclo, ETAPA_SUPPLY)
    metas_liberada = etapa_congelada(db, ciclo, ETAPA_METAS)

    plano = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao,'SEM DESCRICAO')  AS descricao,
               COALESCE(p.categoria,'SEM CATEGORIA')  AS categoria,
               COALESCE(p.segmento,'SEM SEGMENTO')    AS segmento,
               TO_CHAR(f.mes_projetado,'YYYY-MM-DD')  AS mes,
               SUM(f.vol_ia)        AS ia,
               SUM(f.vol_meta)      AS meta,
               SUM(f.vol_supply)    AS supply,
               AVG(f.pmv_aplicado)  AS pmv,
               SUM(f.vol_supply * f.pmv_aplicado) AS receita_sup,
               SUM(f.vol_meta * f.pmv_aplicado)   AS receita_meta,
               MAX(f.motivo_supply)        AS motivo,
               MAX(f.justificativa_supply) AS justificativa
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    tree: dict = {}
    tot_meta = {mi: 0 for mi in meses_iso}
    tot_sup = {mi: 0 for mi in meses_iso}
    tot_rs = {mi: 0.0 for mi in meses_iso}

    for r in plano:
        cat = tree.setdefault(r.categoria, {"nome": r.categoria, "segmentos": {}})
        seg = cat["segmentos"].setdefault(r.segmento, {"nome": r.segmento, "skus": {}})
        sk = seg["skus"].setdefault(r.sku, {"sku": r.sku, "descricao": r.descricao, "meses": {}})
        meta = int(r.meta or 0); sup = int(r.supply or 0); ia = int(r.ia or 0)
        # Se supply ainda não interveio, exibe a meta como ponto de partida.
        sup_exib = sup if sup > 0 else meta
        # Receita no grão coerente com o volume exibido.
        receita = float(r.receita_sup or 0) if sup > 0 else float(r.receita_meta or 0)
        pmv = (receita / sup_exib) if sup_exib > 0 else float(r.pmv or 0)
        sk["meses"][r.mes] = {
            "ia": ia, "meta": meta, "supply": sup_exib,
            "gap": sup_exib - meta, "pmv": round(pmv, 2),
            "motivo": r.motivo, "justificativa": r.justificativa,
        }
        tot_meta[r.mes] += meta
        tot_sup[r.mes] += sup_exib
        tot_rs[r.mes] += receita

    categorias = []
    for cat in sorted(tree.values(), key=lambda c: c["nome"]):
        segs = []
        for seg in sorted(cat["segmentos"].values(), key=lambda s: s["nome"]):
            skus = sorted(seg["skus"].values(), key=lambda s: s["descricao"])
            segs.append({"nome": seg["nome"], "skus": skus})
        categorias.append({"nome": cat["nome"], "segmentos": segs})

    return {
        "ciclo": ciclo, "congelada": congelada, "metas_liberada": metas_liberada,
        "meses": meses_iso, "categorias": categorias,
        "totais": {
            "meta": {mi: tot_meta[mi] for mi in meses_iso},
            "supply": {mi: tot_sup[mi] for mi in meses_iso},
            "faturamento": {mi: round(tot_rs[mi], 2) for mi in meses_iso},
        },
    }


@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), _: dict = Depends(require_supply)):
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
           usuario: dict = Depends(require_supply)):
    """
    Grava vol_supply rateado (peso vol_meta) + motivo/justificativa por SKU/mês.
    O volume é sempre a soma digitada (trava de balanço). Motivo e justificativa
    são aplicados a todas as linhas do grupo.
    """
    try:
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_SUPPLY):
            raise HTTPException(423, "Etapa já congelada pelo Administrador.")
        if not etapa_congelada(db, ciclo, ETAPA_METAS):
            raise HTTPException(423, "Aguardando o Comercial fechar as Metas.")

        nome_user = usuario.get("nome", usuario.get("email", "?"))
        total = 0
        for aj in payload.ajustes:
            data_alvo = parse_date_safe(
                aj.mes_projetado if len(aj.mes_projetado) > 7 else aj.mes_projetado + "-01")
            antigo = db.execute(text("""
                SELECT COALESCE(SUM(vol_supply),0) FROM fato_ibp_granular
                WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo}).scalar()

            total += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=data_alvo,
                volume_alvo=int(aj.novo_volume), etapa=ETAPA_SUPPLY)

            # motivo/justificativa em todas as linhas do grupo
            if aj.motivo is not None or aj.justificativa is not None:
                db.execute(text("""
                    UPDATE fato_ibp_granular
                    SET motivo_supply = :mo, justificativa_supply = :ju
                    WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
                """), {"mo": aj.motivo, "ju": aj.justificativa,
                       "c": ciclo, "s": aj.sku, "m": data_alvo})

            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Supply Review",
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
        congelar_etapa(db, ciclo, ETAPA_SUPPLY)
        res = propagar_para_jusante(db, ciclo, ETAPA_SUPPLY)
        db.commit()
        return {"status": "success", "propagou": res["propagou"], "preservou": res["preservou"]}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir")
def reabrir(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_SUPPLY)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(require_supply)):
    import io
    from fastapi.responses import StreamingResponse
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    rows = db.execute(text("""
        SELECT f.sku, COALESCE(p.descricao,'') AS descricao,
               COALESCE(p.categoria,'') AS categoria, COALESCE(p.segmento,'') AS segmento,
               TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
               SUM(f.vol_meta) AS meta, SUM(f.vol_supply) AS supply,
               AVG(f.pmv_aplicado) AS pmv,
               MAX(f.motivo_supply) AS motivo, MAX(f.justificativa_supply) AS just
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    def _num(v): return f"{float(v or 0):.2f}".replace(".", ",")
    def _txt(v): return (v or "").replace(";", ",").replace("\n", " ")
    linhas = ["Categoria;Segmento;SKU;Descricao;Mes;Meta (cx);Supply (cx);Gap (cx);PMV;Motivo;Justificativa"]
    for r in rows:
        meta = int(r.meta or 0); sup = int(r.supply or 0) if r.supply else meta
        linhas.append(";".join([r.categoria, r.segmento, r.sku, r.descricao, r.mes,
            str(meta), str(sup), str(sup - meta), _num(r.pmv), _txt(r.motivo), _txt(r.just)]))
    buffer = io.StringIO("\r\n".join(linhas))
    resp = StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="supply_review_{ciclo.replace("/","_")}.csv"'
    return resp