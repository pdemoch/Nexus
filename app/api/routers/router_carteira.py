"""
router_carteira_novo.py  —  Metas Comercial (nova API)

Contrato alinhado com MetasComercial.tsx:
  • GET  /tabela              → árvore 5 níveis + meses como dict ISO
  • POST /salvar              → {razao_social, sku, mes_projetado, novo_volume}
  • GET  /exportar            → Excel (.xlsx)
  • GET  /dossie              → dossiê do SKU (coluna_meta = vol_meta)
  • GET  /resumo              → Visão Geral (resumo_marketing)
  • GET  /exportar-visao-geral → Excel da Visão Geral
  • GET  /status              → ciclo + congelada
  • POST /congelar            → congela ETAPA_METAS
  • POST /reabrir             → reabre ETAPA_METAS
  • GET  /cadeados            → lista quem bloqueou a carteira
  • POST /bloquear            → vendedor bloqueia sua própria carteira
  • POST /reabrir-cadeado     → admin reabre carteira de alguém

Toda a lógica de SQL e rateio é preservada do router_carteira.py original.
"""

import datetime
import io
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_working_window_months,
    ratear_maior_resto,
    propagar_para_jusante,
    check_imutabilidade_mes,
    etapa_congelada,
    congelar_etapa,
    reabrir_etapa,
    ETAPA_METAS,
    ETAPA_BOTTOMUP,
)

router = APIRouter(tags=["Metas Comercial"])


def require_metas(usuario: dict = Depends(get_current_user)):
    """
    Acesso à etapa de Metas: Administrador, Gerente ou Coordenador com
    vínculo válido. A validação profunda (escopo + RLS) fica em rls_metas.
    """
    from app.api.routers.rls_metas import exigir_acesso_metas
    exigir_acesso_metas(usuario)   # levanta 403 se não tiver vínculo
    return usuario


def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador congela etapas.")
    return usuario


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------
@router.get("/status")
def status(db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    ciclo = get_current_cycle(db)
    return {"ciclo": ciclo, "congelada": etapa_congelada(db, ciclo, ETAPA_METAS)}


# ---------------------------------------------------------------------------
# GET /resumo  — Visão Geral (mesmo payload que as outras telas)
# ---------------------------------------------------------------------------
@router.get("/resumo")
def resumo(db: Session = Depends(get_db), _: dict = Depends(require_metas)):
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        return resumo_marketing(db, ciclo)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /exportar-visao-geral  — Excel Visão Geral
# ---------------------------------------------------------------------------
@router.get("/exportar-visao-geral")
def exportar_visao_geral(
    nivel: str = Query("categoria", regex="^(categoria|sku)$"),
    db: Session = Depends(get_db),
    _: dict = Depends(require_metas),
):
    try:
        from app.api.routers.perfil_sku import resumo_marketing
        ciclo = get_current_cycle(db)
        dados = resumo_marketing(db, ciclo)
        label_col = "categoria" if nivel == "categoria" else "descricao"

        def _cagr(anos_com_dado, campo):
            filtrados = [a for a in (anos_com_dado or []) if a.get(campo, 0) and a[campo] > 0]
            if len(filtrados) < 2: return None
            vi, vf, n = filtrados[0][campo], filtrados[-1][campo], len(filtrados)-1
            return (vf/vi)**(1/n)-1 if vi > 0 else None

        def _pct(v): return f"{v*100:+.1f}%" if v is not None else "—"

        def _build_df(rows, campo_val, campo_tend):
            todos_anos = sorted({a["ano"] for r in rows for a in r.get("anos", [])})
            registros = []
            for r in rows:
                idx = {a["ano"]: a.get(campo_val) for a in r.get("anos", [])}
                rec = {"Nome": r.get(label_col) or r.get("sku",""), "Categoria": r.get("categoria",""),
                       "Tendência": r.get(campo_tend,""), "CAGR": _pct(_cagr(r.get("anos",[]), campo_val))}
                for i, ano in enumerate(todos_anos):
                    if i > 0:
                        ant, cur = idx.get(todos_anos[i-1]), idx.get(ano)
                        rec[f"{todos_anos[i-1]}→{ano}"] = _pct((cur-ant)/ant) if ant and ant>0 and cur is not None else "—"
                    rec[str(ano)] = idx.get(ano, "")
                registros.append(rec)
            cols = ["Nome","Categoria","Tendência","CAGR"]
            if nivel == "categoria": cols = [c for c in cols if c != "Categoria"]
            cols_anos = []
            for i, ano in enumerate(todos_anos):
                if i > 0: cols_anos.append(f"{todos_anos[i-1]}→{ano}")
                cols_anos.append(str(ano))
            return pd.DataFrame(registros, columns=cols + cols_anos)

        src_vol = dados["tendencia_volume_categoria"] if nivel=="categoria" else dados["tendencia_volume_sku"]
        src_pmv = dados["tendencia_pmv_categoria"]    if nivel=="categoria" else dados["tendencia_pmv_sku"]
        src_ass = dados["assertividade_categoria"]    if nivel=="categoria" else dados["assertividade_sku"]

        df_vol = _build_df(src_vol, "vol_cx", "tendencia_cx")
        df_val = _build_df(src_vol, "vol_rs", "tendencia_rs")
        df_pmv = _build_df(src_pmv, "pmv",    "tendencia")

        ass_rows = [{"Nome": r.get(label_col) or r.get("sku",""), "Categoria": r.get("categoria",""),
                     "Realizado (cx)": r.get("vendido_cx",0), "Humano (cx)": r.get("humano_cx",0),
                     "Aderência Humano": _pct(r.get("aderencia_humano")), "IA (cx)": r.get("ia_cx",0),
                     "Aderência IA": _pct(r.get("aderencia_ia")), "Vencedor": r.get("vencedor","—")}
                    for r in src_ass]
        cols_ass = ["Nome","Realizado (cx)","Humano (cx)","Aderência Humano","IA (cx)","Aderência IA","Vencedor"]
        if nivel == "sku": cols_ass.insert(1, "Categoria")
        df_ass = pd.DataFrame(ass_rows, columns=cols_ass)

        meses_label = ", ".join(dados.get("meses_auditados", []))
        nota = pd.DataFrame([["CAGR = taxa de crescimento médio anual composto (Valor_final/Valor_inicial)^(1/anos)−1"]], columns=["Nota"])
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            for df, sheet in [(df_vol, "Volume (cx)"), (df_val, "Valor de Venda (R$)"), (df_pmv, "PMV")]:
                df.to_excel(w, index=False, sheet_name=sheet)
                nota.to_excel(w, index=False, sheet_name=sheet, startrow=len(df)+2, header=False)
            df_ass.to_excel(w, index=False, sheet_name=f"Assertividade ({meses_label})" if meses_label else "Assertividade")
        buf.seek(0)
        nome = f"visao_geral_metas_{ciclo.replace('/','_')}_{nivel}.xlsx"
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /tabela  — árvore 5 níveis no contrato do MetasComercial.tsx
# ---------------------------------------------------------------------------
@router.get("/tabela")
def tabela(db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """
    Retorna a árvore hierárquica (gerente→coordenador→vendedor→cliente→produto)
    no formato esperado pelo MetasComercial.tsx:
      • cada produto tem campo 'meses' como dict {ISO: {meta, ia, pmv, orcamento}}
      • campo 'sku' e 'descricao' (não 'produto'/'nome')
      • campo 'tipo' = 'produto' nas folhas
    """
    try:
        ciclo   = get_current_cycle(db)
        meses   = get_working_window_months(db)
        meses_iso = [m.strftime("%Y-%m") for m in meses]
        upstream_ok       = etapa_congelada(db, ciclo, ETAPA_BOTTOMUP)
        propria_congelada = etapa_congelada(db, ciclo, ETAPA_METAS)
        aguardando        = not upstream_ok
        congelada         = propria_congelada or aguardando

        # RLS canônico — escopo derivado do BANCO a cada request (rls_metas)
        from app.api.routers.rls_metas import escopo_usuario, clausula_rls
        escopo = escopo_usuario(u)
        rls    = clausula_rls(escopo, alias_cli="c")
        nome_resp = None if escopo["ve_tudo"] else escopo["nome_responsavel"]

        params = {"ciclo": ciclo, "meses": meses}
        params.update(rls["params"])
        filtro_resp = f"AND {rls['where']}"

        rows = db.execute(text(f"""
            SELECT
                COALESCE(NULLIF(TRIM(c.gerente_nome),''),'SEM GERENTE')       AS gerente,
                COALESCE(NULLIF(TRIM(c.supervisor_nome),''),'SEM COORDENADOR') AS coordenador,
                COALESCE(NULLIF(TRIM(f.vendedor_nome),''),'SEM VENDEDOR')      AS vendedor,
                COALESCE(NULLIF(TRIM(c.razaosocial),''),'SEM RAZAO SOCIAL')   AS razao_social,
                TRIM(f.sku)                                                    AS sku,
                COALESCE(NULLIF(TRIM(p.descricao),''),'SEM DESCRICAO')        AS descricao,
                TO_CHAR(f.mes_projetado,'YYYY-MM')                             AS mes,
                COALESCE(SUM(f.vol_meta),0)                                   AS meta,
                COALESCE(SUM(f.vol_bottomup),0)                               AS bottomup,
                COALESCE(SUM(f.vol_ia),0)                                     AS ia,
                COALESCE(SUM(f.pmv_aplicado * f.vol_bottomup)
                         / NULLIF(SUM(f.vol_bottomup),0), 0)                  AS pmv,
                COALESCE(SUM(o.receita_orcamento),0)                          AS orcamento
            FROM fato_ibp_granular f
            JOIN dim_clientes c  ON f.cgc  = c.cgc
            JOIN dim_produtos p  ON f.sku  = p.sku
            LEFT JOIN fato_orcamento o ON o.sku = f.sku AND o.mes_projetado = f.mes_projetado
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = ANY(:meses)
              AND f.sku IS NOT NULL AND f.sku != ''
              {filtro_resp}
            GROUP BY gerente, coordenador, vendedor, razao_social, f.sku, p.descricao, f.mes_projetado
            ORDER BY gerente, coordenador, vendedor, razao_social, p.descricao, f.mes_projetado
        """), params).fetchall()

        # Monta árvore em Python
        tree: dict = {}
        for r in rows:
            g  = r.gerente; co = r.coordenador; v = r.vendedor
            rz = r.razao_social; sk = r.sku

            ger   = tree.setdefault(g, {"nome": g, "tipo": "gerente", "subRows": {}})
            coord = ger["subRows"].setdefault(co, {"nome": co, "tipo": "coordenador", "subRows": {}})
            vend  = coord["subRows"].setdefault(v, {"nome": v, "tipo": "vendedor", "subRows": {}})
            cli   = vend["subRows"].setdefault(rz, {"nome": rz, "tipo": "cliente", "subRows": {}})
            prod  = cli["subRows"].setdefault(sk, {
                "sku": sk, "descricao": r.descricao, "tipo": "produto", "meses": {}
            })
            prod["meses"][r.mes] = {
                "meta":     int(r.meta or 0),
                "ia":       int(r.ia or 0),
                "pmv":      round(float(r.pmv or 0), 2),
                "orcamento": round(float(r.orcamento or 0), 2),
            }

        def _serializar_arvore(node_dict: dict) -> list:
            out = []
            for v in node_dict.values():
                n = {k: val for k, val in v.items() if k != "subRows"}
                if "subRows" in v:
                    if v.get("tipo") == "cliente":
                        n["subRows"] = list(v["subRows"].values())
                    else:
                        n["subRows"] = _serializar_arvore(v["subRows"])
                out.append(n)
            return out

        arvore = _serializar_arvore(tree)

        # Cadeado individual: carteira do usuário bloqueada?
        minha_congelada = False
        if nome_resp:
            from app.api.routers.rls_metas import esta_congelado_para_usuario
            minha_congelada = esta_congelado_para_usuario(db, escopo, ciclo)

        return {
            "ciclo": ciclo,
            "meses": meses_iso,
            "etapa_congelada": congelada,
            "congelada_propria": propria_congelada,
            "aguardando_upstream": aguardando,
            "motivo_bloqueio": ("Demanda Comercial ainda nao congelou o plano." if aguardando
                                 else "Etapa congelada pelo Administrador." if propria_congelada else None),
            "minha_carteira_congelada": minha_congelada,
            "sou_admin": u.get("funcao") == "Administrador",
            "arvore": arvore,
        }
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# POST /salvar  — grava vol_meta rateado por CNPJ (peso histórico 4 meses)
# ---------------------------------------------------------------------------
class AjusteMeta(BaseModel):
    razao_social: str
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteMeta]

@router.post("/salvar")
def salvar(payload: PayloadSalvar, db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    try:
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP):
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_METAS):
            raise HTTPException(423, "Etapa congelada — não é possível salvar.")

        for aj in payload.ajustes:
            check_imutabilidade_mes(aj.mes_projetado, aj.sku, contexto="Meta")

            result = db.execute(text("""
                SELECT f.id AS fato_id, f.cgc,
                       COALESCE(SUM(v.qt_pedido),0) AS peso_historico
                FROM fato_ibp_granular f
                JOIN dim_clientes c ON f.cgc = c.cgc
                LEFT JOIN fato_vendas v
                    ON v.cgc = f.cgc AND v.sku = f.sku
                    AND v.data_pedido >= CURRENT_DATE - INTERVAL '4 months'
                WHERE f.ciclo_sop = :ciclo
                  AND TO_CHAR(f.mes_projetado,'YYYY-MM') = :mes
                  AND f.sku = :sku
                  AND TRIM(c.razaosocial) = TRIM(:razao)
                GROUP BY f.id, f.cgc ORDER BY f.id
            """), {"ciclo": ciclo, "mes": aj.mes_projetado,
                   "sku": aj.sku, "razao": aj.razao_social}).fetchall()

            if not result: continue

            pesos     = [max(0.0, float(r.peso_historico or 0)) for r in result]
            partes    = ratear_maior_resto(aj.novo_volume, pesos)

            for r, parte in zip(result, partes):
                db.execute(text("UPDATE fato_ibp_granular SET vol_meta=:v WHERE id=:id"),
                           {"v": int(parte), "id": r.fato_id})

        db.commit()
        return {"status": "ok"}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /exportar  — Excel cópia de segurança do preenchimento
# ---------------------------------------------------------------------------
@router.get("/exportar")
def exportar(db: Session = Depends(get_db), _: dict = Depends(require_metas)):
    try:
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        rows = db.execute(text("""
            SELECT c.gerente_nome, c.supervisor_nome, f.vendedor_nome,
                   c.razaosocial, f.sku, p.descricao,
                   TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
                   SUM(f.vol_meta) AS meta,
                   SUM(f.vol_ia)   AS ia,
                   SUM(f.vol_meta * f.pmv_aplicado) AS receita
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            JOIN dim_produtos p ON f.sku = p.sku
            WHERE f.ciclo_sop=:c AND f.mes_projetado=ANY(:m)
            GROUP BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome,
                     c.razaosocial, f.sku, p.descricao, f.mes_projetado
            ORDER BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome,
                     c.razaosocial, p.descricao, f.mes_projetado
        """), {"c": ciclo, "m": meses}).fetchall()

        registros = [{
            "Gerente": r.gerente_nome, "Coordenador": r.supervisor_nome,
            "Vendedor": r.vendedor_nome, "Razão Social": r.razaosocial,
            "SKU": r.sku, "Descrição": r.descricao, "Mês": r.mes,
            "Vol. Meta (cx)": int(r.meta or 0), "IA (cx)": int(r.ia or 0),
            "Receita Prevista (R$)": round(float(r.receita or 0), 2),
        } for r in rows]

        df = pd.DataFrame(registros)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Metas Comercial")
            nota = pd.DataFrame([[f"Ciclo: {ciclo}"],
                                  [f"Meses: {', '.join(m.strftime('%m/%Y') for m in meses)}"],
                                  ["Gerado pelo Nexus S&OP — cópia de segurança"]], columns=["Nota"])
            nota.to_excel(w, index=False, sheet_name="Metas Comercial",
                          startrow=len(df)+2, header=False)
        buf.seek(0)
        nome = f"metas_{ciclo.replace('/','_')}.xlsx"
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /dossie  — dossiê do SKU (coluna_meta = vol_meta)
# ---------------------------------------------------------------------------
@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), _: dict = Depends(require_metas)):
    try:
        from app.api.routers.perfil_sku import montar_dossie
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        desc  = db.execute(text("SELECT descricao FROM dim_produtos WHERE sku=:s"), {"s": sku}).scalar()
        return montar_dossie(db, sku, ciclo, meses, descricao=desc, coluna_meta="vol_meta")
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# POST /congelar / POST /reabrir  — controle de etapa (Admin)
# ---------------------------------------------------------------------------
@router.post("/congelar")
def congelar(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        congelar_etapa(db, ciclo, ETAPA_METAS)
        res = propagar_para_jusante(db, ciclo, ETAPA_METAS)
        db.commit()
        return {"status": "congelada", "propagacao": res}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))

@router.post("/reabrir")
def reabrir(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_METAS)
        db.commit()
        return {"status": "reaberta"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /cadeados  — lista carteiras bloqueadas individualmente
# POST /bloquear — vendedor bloqueia a própria carteira
# POST /reabrir-cadeado — admin reabre a carteira de alguém
# ---------------------------------------------------------------------------
@router.get("/cadeados")
def cadeados(db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    try:
        ciclo = get_current_cycle(db)
        rows  = db.execute(text("""
            SELECT nome_responsavel AS nome, nivel, congelado_por AS por,
                   data_congelamento AS quando
            FROM controle_metas_responsavel
            WHERE ciclo_sop=:c AND status='CONGELADO'
            ORDER BY data_congelamento
        """), {"c": ciclo}).fetchall()
        return {
            "sou_admin": u.get("funcao") == "Administrador",
            "meu_nome":  (u.get("gerente_nome") or u.get("supervisor_nome") or u.get("nome")),
            "cadeados":  [{"nome": r.nome, "nivel": r.nivel, "por": r.por} for r in rows],
        }
    except Exception as e:
        raise HTTPException(500, repr(e))

class PayloadBloquear(BaseModel):
    nome_alvo: Optional[str] = None  # só admin usa; vendedor bloqueia a si mesmo

@router.post("/bloquear")
def bloquear(payload: PayloadBloquear = PayloadBloquear(),
             db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """Congela a carteira do próprio responsável (ou de um subordinado, se Gerente)."""
    try:
        from app.api.routers.rls_metas import (
            escopo_usuario, garantir_tabela_cadeados, travar_cadeado)
        ciclo  = get_current_cycle(db)
        escopo = escopo_usuario(u)
        garantir_tabela_cadeados(db)
        nome = travar_cadeado(db, escopo, ciclo, nome_alvo=payload.nome_alvo)
        return {"status": "bloqueado", "responsavel": nome}
    except HTTPException: raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir-cadeado")
def reabrir_cadeado_ep(payload: PayloadBloquear, db: Session = Depends(get_db),
                       u: dict = Depends(require_metas)):
    """Reabre a carteira. Precedência hierárquica validada em rls_metas."""
    try:
        from app.api.routers.rls_metas import escopo_usuario, reabrir_cadeado
        ciclo  = get_current_cycle(db)
        escopo = escopo_usuario(u)
        if not payload.nome_alvo:
            raise HTTPException(400, "nome_alvo é obrigatório.")
        # Gerente pode reabrir coordenador da sua gerência
        pertence = False
        if not escopo["ve_tudo"] and escopo["funcao"] == "Gerente":
            pertence = bool(db.execute(text("""
                SELECT 1 FROM dim_clientes
                WHERE TRIM(gerente_nome) = :g AND TRIM(supervisor_nome) = :a LIMIT 1
            """), {"g": escopo["valor_rls"], "a": payload.nome_alvo.strip()}).scalar())
        reabrir_cadeado(db, escopo, ciclo, payload.nome_alvo, pertence)
        return {"status": "reaberto", "responsavel": payload.nome_alvo}
    except HTTPException: raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))