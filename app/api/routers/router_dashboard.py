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
from fastapi.responses import StreamingResponse
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import io

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



# ---------------------------------------------------------------------
# GET /resumo — Aba "Visão Geral" (idêntica em todas as 5 telas)
# ---------------------------------------------------------------------
@router.get("/resumo")
def resumo(db: Session = Depends(get_db), _: dict = Depends(get_current_user)):
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        return resumo_marketing(db, ciclo)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------
# GET /exportar-visao-geral — Excel 4 abas (Volume, Valor, PMV, Assertividade)
# ---------------------------------------------------------------------
@router.get("/exportar-visao-geral")
def exportar_visao_geral(
    nivel: str = Query("categoria", regex="^(categoria|sku)$"),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        dados = resumo_marketing(db, ciclo)
        label_col = "categoria" if nivel == "categoria" else "descricao"

        def _cagr(anos, campo):
            f = [a for a in (anos or []) if a.get(campo, 0) and a[campo] > 0]
            if len(f) < 2: return None
            vi, vf, n = f[0][campo], f[-1][campo], len(f) - 1
            return (vf / vi) ** (1 / n) - 1 if vi > 0 else None

        def _pct(v): return f"{v*100:+.1f}%" if v is not None else "\u2014"

        def _build(rows, campo_val, campo_tend):
            anos_all = sorted({a["ano"] for r in rows for a in r.get("anos", [])})
            regs = []
            for r in rows:
                idx = {a["ano"]: a.get(campo_val) for a in r.get("anos", [])}
                rec = {"Nome": r.get(label_col) or r.get("sku", ""),
                       "Categoria": r.get("categoria", ""),
                       "Tend\u00eancia": r.get(campo_tend, ""),
                       "CAGR": _pct(_cagr(r.get("anos", []), campo_val))}
                for i, ano in enumerate(anos_all):
                    if i > 0:
                        ant, cur = idx.get(anos_all[i-1]), idx.get(ano)
                        rec[f"{anos_all[i-1]}\u2192{ano}"] = (
                            _pct((cur - ant) / ant) if ant and ant > 0 and cur is not None else "\u2014")
                    rec[str(ano)] = idx.get(ano, "")
                regs.append(rec)
            cols = ["Nome", "Categoria", "Tend\u00eancia", "CAGR"]
            if nivel == "categoria": cols = [c for c in cols if c != "Categoria"]
            cols_anos = []
            for i, ano in enumerate(anos_all):
                if i > 0: cols_anos.append(f"{anos_all[i-1]}\u2192{ano}")
                cols_anos.append(str(ano))
            return pd.DataFrame(regs, columns=cols + cols_anos)

        src_vol = dados["tendencia_volume_categoria"] if nivel == "categoria" else dados["tendencia_volume_sku"]
        src_pmv = dados["tendencia_pmv_categoria"]    if nivel == "categoria" else dados["tendencia_pmv_sku"]
        src_ass = dados["assertividade_categoria"]    if nivel == "categoria" else dados["assertividade_sku"]

        df_vol = _build(src_vol, "vol_cx", "tendencia_cx")
        df_val = _build(src_vol, "vol_rs", "tendencia_rs")
        df_pmv = _build(src_pmv, "pmv", "tendencia")
        df_ass = pd.DataFrame([{
            "Nome": r.get(label_col) or r.get("sku", ""),
            "Categoria": r.get("categoria", ""),
            "Realizado (cx)": r.get("vendido_cx", 0),
            "Humano (cx)": r.get("humano_cx", 0),
            "Ader\u00eancia Humano": _pct(r.get("aderencia_humano")),
            "IA (cx)": r.get("ia_cx", 0),
            "Ader\u00eancia IA": _pct(r.get("aderencia_ia")),
            "Vencedor": r.get("vencedor", "\u2014"),
        } for r in src_ass])

        nota = pd.DataFrame([
            ["CAGR = Compound Annual Growth Rate (crescimento medio anual composto)"],
            ["Formula: (Valor_final / Valor_inicial)^(1/n_anos) - 1"],
        ], columns=["Nota"])

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            for df, sheet in [(df_vol, "Volume (cx)"), (df_val, "Valor de Venda (R$)"), (df_pmv, "PMV")]:
                df.to_excel(w, index=False, sheet_name=sheet)
                nota.to_excel(w, index=False, sheet_name=sheet, startrow=len(df)+2, header=False)
            df_ass.to_excel(w, index=False, sheet_name="Assertividade")
        buf.seek(0)
        nome = f"visao_geral_final_{ciclo.replace('/','_')}_{nivel}.xlsx"
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.get("/tabela")
def tabela(db: Session = Depends(get_db), u: dict = Depends(get_current_user)):
    """
    Consolidação categoria->segmento->SKU. Cada célula traz as 5 camadas +
    final + PMV, orçamento e o final do ciclo anterior (para o toggle de
    comparação). Totalizadores por mês (volume, faturamento, orçamento, anterior).
    """
    ciclo = get_current_cycle(db)
    ciclo_ant = get_previous_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    upstream_ok = etapa_congelada(db, ciclo, ETAPA_SUPPLY)
    publicado   = etapa_congelada(db, ciclo, ETAPA_FINAL)
    aguardando  = not upstream_ok
    congelada   = publicado or aguardando

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
               SUM(f.vol_final * f.pmv_aplicado) AS receita_final
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
        fin = int(r.final or 0)
        receita = float(r.receita_final or 0)
        # PMV exibido = ponderado por volume (receita_grao / volume). Coerente:
        # pmv_ponderado × volume == receita, sempre. Nunca média simples.
        pmv = (receita / fin) if fin > 0 else 0.0
        sk["meses"][r.mes] = {
            "ia": int(r.ia or 0), "topdown": int(r.topdown or 0),
            "bottomup": int(r.bottomup or 0), "meta": int(r.meta or 0),
            "supply": int(r.supply or 0), "final": fin,
            "pmv": round(pmv, 2), "receita": round(receita, 2),
            "orcamento": round(orc.get(r.sku, {}).get(r.mes, 0.0), 2),
            "final_anterior": ant.get(r.sku, {}).get(r.mes, None),
        }
        tot[r.mes]["vol"] += fin
        tot[r.mes]["fat"] += receita   # soma do faturamento no grão (= Excel = fato)
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
        "congelada": congelada,
        "sou_admin": u.get("funcao") == "Administrador",
        "aguardando_upstream": aguardando,
        "motivo_bloqueio": ("Supply Review ainda nao congelou o plano." if aguardando
                             else "S&OP publicado." if publicado else None),
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
    Dossiê do Dashboard = dossiê CANÔNICO (montar_dossie, coluna_meta=vol_final)
    + comparador de cenários (as 5 camadas por mês da janela) exclusivo desta tela.

    Usa a mesma fonte das outras 4 telas, então o gráfico, o FVA de 3 meses e as
    comparações por mês vêm no formato que o DossieInferior espera.
    """
    try:
        from app.api.routers.perfil_sku import montar_dossie
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        desc  = db.execute(text("SELECT descricao FROM dim_produtos WHERE sku=:s"),
                           {"s": sku}).scalar()

        # Dossiê canônico — mesmo payload das outras telas
        base = montar_dossie(db, sku, ciclo, meses, descricao=desc,
                             coluna_meta="vol_final")

        # Extra exclusivo do Dashboard: as 5 camadas lado a lado por mês
        camadas = db.execute(text("""
            SELECT TO_CHAR(mes_projetado,'YYYY-MM-DD') AS mes,
                   SUM(vol_ia)       AS ia,
                   SUM(vol_topdown)  AS topdown,
                   SUM(vol_bottomup) AS bottomup,
                   SUM(vol_meta)     AS meta,
                   SUM(vol_supply)   AS supply,
                   SUM(vol_final)    AS final
            FROM fato_ibp_granular
            WHERE sku = :sku AND ciclo_sop = :c AND mes_projetado = ANY(:meses)
            GROUP BY mes_projetado ORDER BY mes_projetado
        """), {"sku": sku, "c": ciclo, "meses": meses}).fetchall()

        cenarios = []
        for r in camadas:
            vals = {"ia": int(r.ia or 0), "topdown": int(r.topdown or 0),
                    "bottomup": int(r.bottomup or 0), "meta": int(r.meta or 0),
                    "supply": int(r.supply or 0), "final": int(r.final or 0)}
            plan = [vals["topdown"], vals["bottomup"], vals["meta"], vals["supply"]]
            pos  = [v for v in plan if v > 0]
            disp = (max(pos) - min(pos)) / max(pos) if pos else 0.0
            cenarios.append({"mes": r.mes, **vals, "dispersao": round(disp, 3)})

        base["cenarios"] = cenarios
        return base
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
        if not etapa_congelada(db, ciclo, ETAPA_SUPPLY):
            raise HTTPException(423, "Supply Review ainda nao congelou. Aguarde o bastao.")
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
        if not etapa_congelada(db, ciclo, ETAPA_SUPPLY):
            raise HTTPException(423, "Supply Review ainda nao congelou. Aguarde o bastao.")
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
               SUM(f.vol_final * f.pmv_aplicado) AS receita_final
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses)
        GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
    """), {"c": ciclo, "meses": meses}).fetchall()

    def _num(v): return f"{float(v or 0):.2f}".replace(".", ",")
    linhas = ["Categoria;Segmento;SKU;Descricao;Mes;IA;TopDown;BottomUp;Meta;Supply;Final;PMV;Receita Final (R$)"]
    for r in rows:
        fin = int(r.fin or 0); receita = float(r.receita_final or 0)
        pmv = (receita / fin) if fin > 0 else 0.0
        linhas.append(";".join([r.categoria, r.segmento, r.sku, r.descricao, r.mes,
            str(int(r.ia or 0)), str(int(r.td or 0)), str(int(r.bu or 0)),
            str(int(r.meta or 0)), str(int(r.sup or 0)), str(fin), _num(pmv), _num(receita)]))
    buffer = io.StringIO("\r\n".join(linhas))
    resp = StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="demanda_final_{ciclo.replace("/","_")}.csv"'
    return resp