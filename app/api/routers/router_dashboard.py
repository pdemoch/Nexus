"""
=====================================================================
ROUTER — DEMANDA FINAL / DASHBOARD S&OP  [reconstrução]
=====================================================================
A sala de guerra do C-Level. Consolida o plano de todas as etapas e publica o
número final (vol_final). Acesso amplo (Admin, Gerente, Supply, Marketing,
C-Level via Sidebar), mas só o ADMIN congela/publica.

Capacidades exclusivas desta tela:
  • Comparador de cenários por SKU: as 5 camadas (IA/TopDown/BottomUP/Meta/
    Supply) lado a lado; o CEO adota qualquer uma como Final ou digita a sua.
  • Toggles: caixas<->R$, vs Orçamento, vs Ciclo Anterior.
  • Abertura micro: categoria -> segmento -> SKU -> razão social.
  • Dossiê enriquecido (via /dossie): plurianual + PMV + FVA + dispersão.

Grava vol_final. Rateia sobre vol_meta (decisão comercial). Publicar = congelar
a etapa Final (só Admin).
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
    get_current_cycle, get_previous_cycle, get_working_window_months,
    escrever_volume_rateado, congelar_etapa, reabrir_etapa, etapa_congelada,
    registrar_log_auditoria, parse_date_safe, ETAPA_FINAL,
    CAMPO_DA_ETAPA, ETAPA_TOPDOWN, ETAPA_BOTTOMUP, ETAPA_METAS, ETAPA_SUPPLY,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["Demanda Final (Dashboard)"])


def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador publica o S&OP.")
    return usuario


class AjusteFinal(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteFinal]


@router.get("/status")
def status(db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    ciclo = get_current_cycle(db)
    return {"ciclo": ciclo, "publicado": etapa_congelada(db, ciclo, ETAPA_FINAL)}


@router.get("/tabela")
def tabela(db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    """
    Consolidação categoria->segmento->SKU. Cada célula traz as 5 camadas +
    final + PMV, orçamento e o final do ciclo anterior (para o toggle de
    comparação). Totalizadores por mês (volume, faturamento, orçamento, anterior).
    """
    ciclo = get_current_cycle(db)
    ciclo_ant = get_previous_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    publicado = etapa_congelada(db, ciclo, ETAPA_FINAL)

    plano = db.execute(text("""
        SELECT f.sku,
               COALESCE(p.descricao,'SEM DESCRICAO')  AS descricao,
               COALESCE(p.categoria,'SEM CATEGORIA')  AS categoria,
               COALESCE(p.segmento,'SEM SEGMENTO')    AS segmento,
               TO_CHAR(f.mes_projetado,'YYYY-MM-DD')  AS mes,
               SUM(f.vol_ia)        AS ia,
               SUM(f.vol_topdown)   AS topdown,
               SUM(f.vol_bottomup)  AS bottomup,
               SUM(f.vol_meta)      AS meta,
               SUM(f.vol_supply)    AS supply,
               SUM(f.vol_final)     AS final,
               AVG(f.pmv_aplicado)  AS pmv
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    # Ciclo anterior (mesmo mês projetado, ciclo -1) — para o toggle "vs anterior"
    ant = defaultdict(dict)
    ant_rows = db.execute(text("""
        SELECT sku, TO_CHAR(mes_projetado,'YYYY-MM-DD') AS mes, SUM(vol_final) AS final
        FROM fato_ibp_granular
        WHERE ciclo_sop = :ca AND mes_projetado = ANY(:meses)
        GROUP BY sku, mes_projetado
    """), {"ca": ciclo_ant, "meses": meses}).fetchall()
    for r in ant_rows:
        ant[r.sku][r.mes] = int(r.final or 0)

    # Orçamento
    orc = defaultdict(dict)
    orc_rows = db.execute(text("""
        SELECT sku, TO_CHAR(mes_projetado,'YYYY-MM-DD') AS mes, SUM(receita_orcamento) AS rec
        FROM fato_orcamento WHERE mes_projetado = ANY(:meses)
        GROUP BY sku, mes_projetado
    """), {"meses": meses}).fetchall()
    for r in orc_rows:
        orc[r.sku][r.mes] = float(r.rec or 0)

    tree: dict = {}
    tot = {mi: {"vol": 0, "fat": 0.0, "orc": 0.0, "ant": 0} for mi in meses_iso}

    for r in plano:
        cat = tree.setdefault(r.categoria, {"nome": r.categoria, "segmentos": {}})
        seg = cat["segmentos"].setdefault(r.segmento, {"nome": r.segmento, "skus": {}})
        sk = seg["skus"].setdefault(r.sku, {"sku": r.sku, "descricao": r.descricao, "meses": {}})
        fin = int(r.final or 0); pmv = float(r.pmv or 0)
        sk["meses"][r.mes] = {
            "ia": int(r.ia or 0), "topdown": int(r.topdown or 0),
            "bottomup": int(r.bottomup or 0), "meta": int(r.meta or 0),
            "supply": int(r.supply or 0), "final": fin,
            "pmv": round(pmv, 2), "receita": round(fin * pmv, 2),
            "orcamento": round(orc.get(r.sku, {}).get(r.mes, 0.0), 2),
            "final_anterior": ant.get(r.sku, {}).get(r.mes, None),
        }
        tot[r.mes]["vol"] += fin
        tot[r.mes]["fat"] += fin * pmv
        tot[r.mes]["orc"] += orc.get(r.sku, {}).get(r.mes, 0.0)
        tot[r.mes]["ant"] += ant.get(r.sku, {}).get(r.mes, 0) or 0

    categorias = []
    for cat in sorted(tree.values(), key=lambda c: c["nome"]):
        segs = []
        for seg in sorted(cat["segmentos"].values(), key=lambda s: s["nome"]):
            skus = sorted(seg["skus"].values(), key=lambda s: s["descricao"])
            segs.append({"nome": seg["nome"], "skus": skus})
        categorias.append({"nome": cat["nome"], "segmentos": segs})

    return {
        "ciclo": ciclo, "ciclo_anterior": ciclo_ant, "publicado": publicado,
        "meses": meses_iso, "categorias": categorias,
        "totais": {
            "volume": {mi: tot[mi]["vol"] for mi in meses_iso},
            "faturamento": {mi: round(tot[mi]["fat"], 2) for mi in meses_iso},
            "orcamento": {mi: round(tot[mi]["orc"], 2) for mi in meses_iso},
            "anterior": {mi: tot[mi]["ant"] for mi in meses_iso},
        },
    }


@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    """
    Dossiê enriquecido do Dashboard: dossiê padrão (plurianual+FVA+24m) +
    comparador de cenários (as 5 camadas por mês) + dispersão entre camadas.
    """
    try:
        from app.api.routers.perfil_sku import comparativo_plurianual, fva_sku
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
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

        # Comparador de cenários: as 5 camadas por mês da janela
        camadas = db.execute(text("""
            SELECT TO_CHAR(mes_projetado,'YYYY-MM-DD') AS mes,
                   SUM(vol_ia) AS ia, SUM(vol_topdown) AS topdown,
                   SUM(vol_bottomup) AS bottomup, SUM(vol_meta) AS meta,
                   SUM(vol_supply) AS supply, SUM(vol_final) AS final
            FROM fato_ibp_granular
            WHERE sku = :sku AND ciclo_sop = :c AND mes_projetado = ANY(:meses)
            GROUP BY mes_projetado ORDER BY mes_projetado
        """), {"sku": sku, "c": ciclo, "meses": meses}).fetchall()

        cenarios = []
        for r in camadas:
            vals = {"ia": int(r.ia or 0), "topdown": int(r.topdown or 0),
                    "bottomup": int(r.bottomup or 0), "meta": int(r.meta or 0),
                    "supply": int(r.supply or 0), "final": int(r.final or 0)}
            # dispersão: amplitude relativa entre as camadas de plano
            plan_layers = [vals["topdown"], vals["bottomup"], vals["meta"], vals["supply"]]
            positivos = [v for v in plan_layers if v > 0]
            disp = 0.0
            if positivos:
                disp = (max(positivos) - min(positivos)) / max(positivos)
            cenarios.append({"mes": r.mes, **vals, "dispersao": round(disp, 3)})

        desc = db.execute(text("SELECT descricao FROM dim_produtos WHERE sku=:s"), {"s": sku}).scalar()
        return {
            "sku": sku, "descricao": desc or sku, "grafico": serie,
            "plurianual": plurianual, "fva": fva, "cenarios": cenarios,
        }
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.post("/adotar-cenario")
def adotar_cenario(sku: str, mes: str, camada: str,
                   db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    """
    Copia o volume de uma camada (topdown/bottomup/meta/supply/ia) para o Final
    de um SKU/mês. É o 'adotar cenário' da gaveta. Rateia por vol_meta.
    """
    mapa = {"ia": "vol_ia", "topdown": "vol_topdown", "bottomup": "vol_bottomup",
            "meta": "vol_meta", "supply": "vol_supply"}
    if camada not in mapa:
        raise HTTPException(400, f"Camada inválida: {camada}")
    try:
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_FINAL):
            raise HTTPException(423, "S&OP já publicado. Reabra para editar.")
        data_alvo = parse_date_safe(mes if len(mes) > 7 else mes + "-01")
        campo = mapa[camada]
        total = db.execute(text(f"""
            SELECT COALESCE(SUM({campo}),0) FROM fato_ibp_granular
            WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
        """), {"c": ciclo, "s": sku, "m": data_alvo}).scalar()
        escrever_volume_rateado(db=db, ciclo=ciclo, sku=sku, mes=data_alvo,
                                volume_alvo=int(total or 0), etapa=ETAPA_FINAL)
        db.commit()
        return {"status": "success", "sku": sku, "mes": mes, "adotou": camada, "volume": int(total or 0)}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/salvar")
def salvar(payload: PayloadSalvar, db: Session = Depends(get_db),
           usuario: dict = Depends(get_current_user)):
    """Edição direta do Final pelo C-Level. Trava de balanço, peso vol_meta."""
    try:
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_FINAL):
            raise HTTPException(423, "S&OP já publicado. Reabra para editar.")
        nome_user = usuario.get("nome", usuario.get("email", "?"))
        total = 0
        for aj in payload.ajustes:
            data_alvo = parse_date_safe(
                aj.mes_projetado if len(aj.mes_projetado) > 7 else aj.mes_projetado + "-01")
            antigo = db.execute(text("""
                SELECT COALESCE(SUM(vol_final),0) FROM fato_ibp_granular
                WHERE ciclo_sop=:c AND sku=:s AND mes_projetado=:m
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo}).scalar()
            total += escrever_volume_rateado(
                db=db, ciclo=ciclo, sku=aj.sku, mes=data_alvo,
                volume_alvo=int(aj.novo_volume), etapa=ETAPA_FINAL)
            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Demanda Final (S&OP Global)",
                usuario=nome_user, sku=aj.sku, cliente="TODOS_OS_CLIENTES",
                mes=data_alvo, v_antigo=int(antigo or 0), v_novo=int(aj.novo_volume))
        db.commit()
        return {"status": "success", "linhas": total}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/publicar")
def publicar(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    """Publica o S&OP: congela a etapa Final. Só Admin."""
    try:
        ciclo = get_current_cycle(db)
        congelar_etapa(db, ciclo, ETAPA_FINAL)
        db.commit()
        return {"status": "success", "mensagem": "S&OP publicado."}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir")
def reabrir(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_FINAL)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    import io
    from fastapi.responses import StreamingResponse
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    rows = db.execute(text("""
        SELECT f.sku, COALESCE(p.descricao,'') AS descricao,
               COALESCE(p.categoria,'') AS categoria, COALESCE(p.segmento,'') AS segmento,
               TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
               SUM(f.vol_ia) AS ia, SUM(f.vol_topdown) AS td, SUM(f.vol_bottomup) AS bu,
               SUM(f.vol_meta) AS meta, SUM(f.vol_supply) AS sup, SUM(f.vol_final) AS fin,
               AVG(f.pmv_aplicado) AS pmv
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    def _num(v): return f"{float(v or 0):.2f}".replace(".", ",")
    linhas = ["Categoria;Segmento;SKU;Descricao;Mes;IA;TopDown;BottomUp;Meta;Supply;Final;PMV;Receita Final (R$)"]
    for r in rows:
        fin = int(r.fin or 0); pmv = float(r.pmv or 0)
        linhas.append(";".join([r.categoria, r.segmento, r.sku, r.descricao, r.mes,
            str(int(r.ia or 0)), str(int(r.td or 0)), str(int(r.bu or 0)),
            str(int(r.meta or 0)), str(int(r.sup or 0)), str(fin), _num(pmv), _num(fin*pmv)]))
    buffer = io.StringIO("\r\n".join(linhas))
    resp = StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="demanda_final_{ciclo.replace("/","_")}.csv"'
    return resp