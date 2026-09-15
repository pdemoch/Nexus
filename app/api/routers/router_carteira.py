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
import logging
import unicodedata
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
    propagar_linha_jusante,
    check_imutabilidade_mes,
    etapa_congelada,
    congelar_etapa,
    reabrir_etapa,
    ETAPA_METAS,
    ETAPA_BOTTOMUP,
)

router = APIRouter(prefix="/api/v1/carteira", tags=["Metas Comercial"])
logger = logging.getLogger(__name__)


SIMONE_NOME = "SIMONE ANDRADE DE PAULA"


def _normalizar_nome(valor: Optional[str]) -> str:
    texto = unicodedata.normalize("NFKD", valor or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).strip().upper()


def _eh_simone(usuario: dict) -> bool:
    return _normalizar_nome(usuario.get("gerente_nome")) == SIMONE_NOME


DDL_METAS_MONETARIAS_SIMONE = """
CREATE TABLE IF NOT EXISTS metas_monetarias_simone (
    id               SERIAL PRIMARY KEY,
    ciclo_sop        VARCHAR(7) NOT NULL,
    mes_projetado    DATE NOT NULL,
    regional         VARCHAR(120) NOT NULL,
    cgc              VARCHAR(50),
    valor_meta       NUMERIC(18,2) NOT NULL DEFAULT 0,
    atualizado_por   VARCHAR(120),
    atualizado_em    TIMESTAMP DEFAULT NOW(),
    UNIQUE (ciclo_sop, mes_projetado, regional, cgc)
);
CREATE INDEX IF NOT EXISTS ix_metas_monetarias_simone_ciclo
    ON metas_monetarias_simone (ciclo_sop, mes_projetado, regional);
"""


DDL_CONTROLE_METAS_SIMONE = """
CREATE TABLE IF NOT EXISTS controle_metas_simone (
    ciclo_sop        VARCHAR(7) PRIMARY KEY,
    etapa_atual      VARCHAR(20) NOT NULL DEFAULT 'REGIONAL',
    atualizado_por   VARCHAR(120),
    atualizado_em    TIMESTAMP DEFAULT NOW()
);
"""

DDL_METAS_MONETARIAS_SIMONE_SKU = """
CREATE TABLE IF NOT EXISTS metas_monetarias_simone_sku (
    id               SERIAL PRIMARY KEY,
    ciclo_sop        VARCHAR(7) NOT NULL,
    mes_projetado    DATE NOT NULL,
    regional         VARCHAR(120) NOT NULL,
    cgc              VARCHAR(50) NOT NULL,
    sku              VARCHAR(80) NOT NULL,
    valor_meta       NUMERIC(18,2) NOT NULL DEFAULT 0,
    atualizado_por   VARCHAR(120),
    atualizado_em    TIMESTAMP DEFAULT NOW(),
    UNIQUE (ciclo_sop, mes_projetado, regional, cgc, sku)
);
"""

DDL_METAS_REGIONAIS_SIMONE = """
CREATE TABLE IF NOT EXISTS metas_regionais_simone (
    id               SERIAL PRIMARY KEY,
    ciclo_sop        VARCHAR(7) NOT NULL,
    mes_projetado    DATE NOT NULL,
    regional         VARCHAR(120) NOT NULL,
    valor_meta       NUMERIC(18,2) NOT NULL DEFAULT 0,
    atualizado_por   VARCHAR(120),
    atualizado_em    TIMESTAMP DEFAULT NOW(),
    UNIQUE (ciclo_sop, mes_projetado, regional)
);
"""


def _garantir_tabelas_simone(db: Session) -> None:
    for ddl in (DDL_METAS_MONETARIAS_SIMONE, DDL_CONTROLE_METAS_SIMONE,
                DDL_METAS_MONETARIAS_SIMONE_SKU, DDL_METAS_REGIONAIS_SIMONE):
        for stmt in ddl.strip().split(";"):
            if stmt.strip():
                db.execute(text(stmt))
    db.commit()


def _exigir_simone(u: dict) -> None:
    if not _eh_simone(u):
        raise HTTPException(403, "Fluxo monetário exclusivo da gerente Simone Andrade de Paula.")


def _etapa_simone_atual(db: Session, ciclo: str) -> str:
    return db.execute(text("""
        SELECT etapa_atual FROM controle_metas_simone WHERE ciclo_sop = :c
    """), {"c": ciclo}).scalar() or "REGIONAL"


def _ratear_centavos(valor: float, pesos: list[float]) -> list[float]:
    total = max(0, int(round(valor * 100)))
    pesos_positivos = [max(0.0, p) for p in pesos]
    soma = sum(pesos_positivos)
    if not pesos_positivos:
        return []
    if soma == 0:
        pesos_positivos = [1.0] * len(pesos_positivos)
        soma = float(len(pesos_positivos))
    exatos = [total * peso / soma for peso in pesos_positivos]
    partes = [int(v) for v in exatos]
    sobra = total - sum(partes)
    ordem = sorted(range(len(partes)), key=lambda i: exatos[i] - partes[i], reverse=True)
    for i in ordem[:sobra]:
        partes[i] += 1
    return [parte / 100 for parte in partes]


def _volumes_por_valor(valor: float, folhas: list) -> list[int]:
    """Converte a meta monetária em caixas inteiras minimizando o erro em R$."""
    alvo = max(0.0, float(valor or 0))
    if not folhas:
        return []
    pesos = [max(0.0, float(getattr(f, "peso_historico", 0) or 0)) for f in folhas]
    if not any(pesos):
        pesos = [max(0.0, float(getattr(f, "valor_atual", 0) or 0)) for f in folhas]
    if not any(pesos):
        pesos = [1.0] * len(folhas)
    soma = sum(pesos)
    volumes = [
        max(0, int((alvo * peso / soma) / max(0.0, float(f.pmv or 0))))
        if float(f.pmv or 0) > 0 else 0
        for f, peso in zip(folhas, pesos)
    ]
    def erro() -> float:
        return abs(alvo - sum(v * float(f.pmv or 0) for v, f in zip(volumes, folhas)))
    while True:
        melhor = None
        erro_atual = erro()
        for i, folha in enumerate(folhas):
            pmv = float(folha.pmv or 0)
            if pmv <= 0:
                continue
            candidato = abs(alvo - (
                sum(v * float(f.pmv or 0) for v, f in zip(volumes, folhas))
                + pmv
            ))
            if candidato + 0.0001 < erro_atual:
                melhor = i
                erro_atual = candidato
        if melhor is None:
            break
        volumes[melhor] += 1
    return volumes


def _materializar_skus_simone(db: Session, ciclo: str, responsavel: str) -> None:
    """Persiste a abertura por SKU e propaga caixas para fato_ibp_granular."""
    rows = db.execute(text("""
        SELECT m.mes_projetado, m.regional, m.cgc, m.valor_meta,
               f.sku, f.id AS fato_id, f.pmv_aplicado AS pmv,
               COALESCE(SUM(v.qt_pedido), 0) AS peso_historico,
               COALESCE(f.vol_meta * f.pmv_aplicado, 0) AS valor_atual,
               f.vol_meta AS volume_atual
        FROM metas_monetarias_simone m
        JOIN fato_ibp_granular f
          ON f.ciclo_sop = m.ciclo_sop AND f.mes_projetado = m.mes_projetado
         AND f.cgc = m.cgc
        LEFT JOIN fato_vendas v
          ON v.cgc = f.cgc AND v.sku = f.sku
         AND v.data_pedido >= CURRENT_DATE - INTERVAL '12 months'
        WHERE m.ciclo_sop = :ciclo AND m.cgc IS NOT NULL
        GROUP BY m.mes_projetado, m.regional, m.cgc, m.valor_meta,
                 f.sku, f.id, f.pmv_aplicado, f.vol_meta
    """), {"ciclo": ciclo}).fetchall()
    grupos: dict = {}
    for row in rows:
        grupos.setdefault((row.mes_projetado, row.regional, row.cgc), []).append(row)
    for (mes, regional, cgc), folhas in grupos.items():
        por_sku: dict = {}
        for row in folhas:
            por_sku.setdefault(row.sku, []).append(row)
        sku_folhas = [
            type("SkuFolha", (), {
                "sku": sku,
                "pmv": sum(float(r.pmv or 0) * max(1, len(rows_sku)) for r in rows_sku)
                       / max(1, len(rows_sku)),
                "peso_historico": sum(float(r.peso_historico or 0) for r in rows_sku),
                "valor_atual": sum(float(r.valor_atual or 0) for r in rows_sku),
            })()
            for sku, rows_sku in por_sku.items()
        ]
        volumes_sku = _volumes_por_valor(float(folhas[0].valor_meta or 0), sku_folhas)
        for sku_folha, volume_sku in zip(sku_folhas, volumes_sku):
            linhas_sku = por_sku[sku_folha.sku]
            partes_linhas = _ratear_centavos(
                float(volume_sku),
                [max(0.0, float(r.volume_atual or 0)) for r in linhas_sku],
            )
            for row, parte in zip(linhas_sku, [int(round(v)) for v in partes_linhas]):
                db.execute(text("""
                    UPDATE fato_ibp_granular
                    SET vol_meta = :v
                    WHERE id = :id
                """), {"v": parte, "id": row.fato_id})
            db.execute(text("""
                INSERT INTO metas_monetarias_simone_sku
                    (ciclo_sop, mes_projetado, regional, cgc, sku, valor_meta, atualizado_por)
                VALUES (:c, :m, :r, :cgc, :sku, :v, :p)
                ON CONFLICT (ciclo_sop, mes_projetado, regional, cgc, sku)
                DO UPDATE SET valor_meta=:v, atualizado_por=:p, atualizado_em=NOW()
            """), {"c": ciclo, "m": mes, "r": regional, "cgc": cgc,
                   "sku": sku_folha.sku,
                   "v": round(volume_sku * float(sku_folha.pmv or 0), 2),
                   "p": responsavel})
            propagar_linha_jusante(db, ciclo, sku_folha.sku, mes, ETAPA_METAS)


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
def tabela(responsavel: str = None, nivel_responsavel: str = None,
           db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """
    Árvore SKU-first: SKU -> Coordenador -> Executivo -> Razão Social.
    O frontend decide, com base em 'funcao' + 'fase_atual', até que nível
    a edição é permitida:
      Gerente,     fase SKU:         edita o total do SKU (raiz).
      Gerente,     fase COORDENADOR: edita % de cada Coordenador dentro do SKU.
      Coordenador, fase EXECUTIVO:   edita % de cada Executivo dentro do SKU
                                      (teto = valor do SKU, vindo do Gerente).
      Coordenador, fase RAZAO_SOCIAL: edita % de cada Razão Social dentro do
                                      Executivo, NAQUELE SKU (teto = valor do
                                      executivo só naquele SKU).
    Cada nível carrega 'meta' (R$/caixas atual) e 'bottomup' (valor original
    da Demanda Comercial) para exibir a variação.
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
        from app.api.routers.fase_meta import fase_atual_do_responsavel, garantir_tabela_fase_meta
        escopo = escopo_usuario(u)
        rls    = clausula_rls(escopo, alias_cli="c")
        nome_resp = None if escopo["ve_tudo"] else escopo["nome_responsavel"]

        params = {"ciclo": ciclo, "meses": meses}
        params.update(rls["params"])
        filtro_resp = f"AND {rls['where']}"
        alvo = (responsavel or "").strip()
        nivel_alvo = (nivel_responsavel or "").strip()
        if alvo:
            if not escopo["ve_tudo"]:
                raise HTTPException(403, "Somente o Administrador pode selecionar outra alçada.")
            if nivel_alvo not in ("Gerente", "Coordenador"):
                raise HTTPException(422, "nivel_responsavel deve ser Gerente ou Coordenador.")
            campo_alvo = "gerente_nome" if nivel_alvo == "Gerente" else "supervisor_nome"
            filtro_resp = f"AND TRIM(c.{campo_alvo}) = :responsavel_alvo"
            params["responsavel_alvo"] = alvo

        rows = db.execute(text(f"""
            SELECT
                TRIM(f.sku)                                                    AS sku,
                COALESCE(NULLIF(TRIM(p.descricao),''),'SEM DESCRICAO')        AS descricao,
                COALESCE(NULLIF(TRIM(c.supervisor_nome),''),'SEM COORDENADOR') AS coordenador,
                COALESCE(NULLIF(TRIM(f.vendedor_nome),''),'SEM VENDEDOR')      AS executivo,
                COALESCE(NULLIF(TRIM(c.razaosocial),''),'SEM RAZAO SOCIAL')   AS razao_social,
                TO_CHAR(f.mes_projetado,'YYYY-MM')                             AS mes,
                COALESCE(SUM(f.vol_meta),0)                                   AS meta,
                COALESCE(SUM(f.vol_bottomup),0)                               AS bottomup,
                COALESCE(SUM(f.vol_ia),0)                                     AS ia,
                COALESCE(SUM(f.pmv_aplicado * f.vol_meta),0)                  AS pmv_num,
                COALESCE(SUM(f.vol_meta),0)                                   AS pmv_den
            FROM fato_ibp_granular f
            JOIN dim_clientes c  ON f.cgc  = c.cgc
            JOIN dim_produtos p  ON f.sku  = p.sku
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = ANY(:meses)
              AND f.sku IS NOT NULL AND f.sku != ''
              {filtro_resp}
            GROUP BY f.sku, p.descricao, coordenador, executivo, razao_social, f.mes_projetado
            ORDER BY p.descricao, coordenador, executivo, razao_social, f.mes_projetado
        """), params).fetchall()

        # Peso historico por (razao_social, sku) — ultimos 4 meses de vendas reais.
        pesos_raw = db.execute(text(f"""
            SELECT COALESCE(NULLIF(TRIM(c.razaosocial),''),'SEM RAZAO SOCIAL') AS razao_social,
                   TRIM(f.sku) AS sku,
                   COALESCE(SUM(v.qt_pedido), 0) AS peso
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            LEFT JOIN fato_vendas v
                ON v.cgc = f.cgc AND v.sku = f.sku
               AND v.data_pedido >= CURRENT_DATE - INTERVAL '4 months'
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = ANY(:meses)
              AND f.sku IS NOT NULL AND f.sku != ''
              {filtro_resp}
            GROUP BY c.razaosocial, f.sku
        """), params).fetchall()
        peso_map: dict = {(r.razao_social, r.sku): float(r.peso or 0) for r in pesos_raw}

        def _no_vazio(tipo: str, **extra) -> dict:
            n = {"tipo": tipo, "meses": {}}
            n.update(extra)
            return n

        def _acumula(node: dict, mes: str, meta, bottomup, ia, pmv_num, pmv_den) -> None:
            m = node["meses"].setdefault(mes, {"meta": 0, "bottomup": 0, "ia": 0, "_pmv_num": 0.0, "_pmv_den": 0.0})
            m["meta"] += int(meta or 0)
            m["bottomup"] += int(bottomup or 0)
            m["ia"] += int(ia or 0)
            m["_pmv_num"] += float(pmv_num or 0)
            m["_pmv_den"] += float(pmv_den or 0)

        # Árvore: SKU -> Coordenador -> Executivo -> Razão Social
        tree: dict = {}
        for r in rows:
            sk, co, ex, rz = r.sku, r.coordenador, r.executivo, r.razao_social

            sku_node   = tree.setdefault(sk, _no_vazio("sku", sku=sk, descricao=r.descricao, subRows={}))
            coord_node = sku_node["subRows"].setdefault(co, _no_vazio("coordenador", nome=co, subRows={}))
            exec_node  = coord_node["subRows"].setdefault(ex, _no_vazio("executivo", nome=ex, subRows={}))
            razao_node = exec_node["subRows"].setdefault(rz, _no_vazio("razao_social", nome=rz))

            for node in (sku_node, coord_node, exec_node, razao_node):
                _acumula(node, r.mes, r.meta, r.bottomup, r.ia, r.pmv_num, r.pmv_den)

            razao_node["meses"][r.mes]["peso_historico"] = peso_map.get((rz, sk), 0.0)

        def _fechar_meses(node: dict) -> None:
            for m in node["meses"].values():
                pmv_den = m.pop("_pmv_den", 0)
                pmv_num = m.pop("_pmv_num", 0)
                m["pmv"] = round(pmv_num / pmv_den, 2) if pmv_den else 0.0
                bu = m.get("bottomup", 0)
                m["variacao_pct"] = round((m["meta"] - bu) / bu, 4) if bu else (None if m["meta"] == 0 else 1.0)

        def _serializar_arvore(node_dict: dict) -> list:
            out = []
            for v in node_dict.values():
                _fechar_meses(v)
                n = {k: val for k, val in v.items() if k != "subRows"}
                if "subRows" in v:
                    n["subRows"] = _serializar_arvore(v["subRows"])
                out.append(n)
            return out

        arvore = _serializar_arvore(tree)

        # Cadeado individual (cadeado FINAL — trancamento definitivo do Coordenador)
        minha_congelada = False
        if nome_resp:
            from app.api.routers.rls_metas import esta_congelado_para_usuario
            minha_congelada = esta_congelado_para_usuario(db, escopo, ciclo)

        # Coordenador só edita depois que o Gerente travar a fase COORDENADOR
        # (dispara _registrar_metas_coordenadores automaticamente).
        aguardando_gerente = False
        if nome_resp and escopo.get("funcao") == "Coordenador":
            aguardando_gerente = not _coordenador_recebeu_meta(db, ciclo, nome_resp)

        # Fase corrente do responsável logado (ou do alvo, se Admin operando
        # em nome de alguém) — usada pelo frontend para montar o stepper.
        minha_fase = None
        fase_resp = nome_resp or (alvo if alvo else None)
        fase_nivel = escopo.get("funcao") if nome_resp else nivel_alvo
        if fase_resp and fase_nivel in ("Gerente", "Coordenador"):
            garantir_tabela_fase_meta(db)
            minha_fase = fase_atual_do_responsavel(db, ciclo, fase_resp, fase_nivel)

        responsaveis = db.execute(text("""
            SELECT DISTINCT 'Gerente' AS nivel, TRIM(gerente_nome) AS nome
            FROM dim_clientes c
            WHERE NULLIF(TRIM(c.gerente_nome), '') IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM fato_ibp_granular f
                  WHERE f.ciclo_sop = :ciclo AND f.cgc = c.cgc
              )
            UNION
            SELECT DISTINCT 'Coordenador', TRIM(supervisor_nome)
            FROM dim_clientes c
            WHERE NULLIF(TRIM(c.supervisor_nome), '') IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM fato_ibp_granular f
                  WHERE f.ciclo_sop = :ciclo AND f.cgc = c.cgc
              )
            ORDER BY 1, 2
        """), {"ciclo": ciclo}).fetchall() if escopo["ve_tudo"] else []

        return {
            "ciclo": ciclo,
            "meses": meses_iso,
            "etapa_congelada": congelada,
            "congelada_propria": propria_congelada,
            "aguardando_upstream": aguardando,
            "aguardando_gerente": aguardando_gerente,
            "motivo_bloqueio": ("Demanda Comercial ainda nao congelou o plano." if aguardando
                                 else "Etapa congelada pelo Administrador." if propria_congelada
                                 else "Aguardando o Gerente passar a meta financeira para sua coordenação." if aguardando_gerente
                                 else None),
            "minha_carteira_congelada": minha_congelada,
            "minha_fase": minha_fase,
            "responsaveis": [{"nivel": r.nivel, "nome": r.nome} for r in responsaveis],
            "responsavel_selecionado": {"nivel": nivel_alvo, "nome": alvo} if alvo else None,
            "sou_admin": u.get("funcao") == "Administrador",
            "funcao": u.get("funcao", ""),
            "simone_monetario": _eh_simone(u),
            "arvore": arvore,
        }
    except HTTPException:
        raise
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
    gerente_nome: Optional[str] = None
    coordenador_nome: Optional[str] = None
    vendedor_nome: Optional[str] = None

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteMeta]


class PayloadAcaoMetas(PayloadSalvar):
    nome_alvo: Optional[str] = None
    nivel_alvo: Optional[str] = None


class MetaMonetariaSimone(BaseModel):
    regional: str
    mes_projetado: str
    valor_meta: float


class ClienteMonetarioSimone(BaseModel):
    regional: str
    cgc: str
    mes_projetado: str
    valor_meta: float


def _escopo_simone_sql(escopo: dict) -> tuple[str, dict]:
    filtro, params = _filtro_escopo_sql(escopo, alias_cli="c")
    return filtro, params


def _mes_iso(valor) -> str:
    if hasattr(valor, "strftime"):
        return valor.strftime("%Y-%m")
    texto = str(valor or "").strip()
    return texto[:7] if len(texto) >= 7 else texto


def _linhas_simone(db: Session, ciclo: str, escopo: dict, meses: list) -> list:
    filtro, params = _escopo_simone_sql(escopo)
    meses_iso = [_mes_iso(m) for m in meses]
    params.update({"ciclo": ciclo, "meses_iso": meses_iso})
    return db.execute(text(f"""
        SELECT
            COALESCE(NULLIF(TRIM(c.regional), ''), 'SEM REGIONAL') AS regional,
            COALESCE(NULLIF(TRIM(c.razaosocial), ''), 'SEM RAZAO SOCIAL') AS cliente,
            COALESCE(NULLIF(TRIM(f.vendedor_nome), ''), 'SEM VENDEDOR') AS executivo,
            c.cgc,
            TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes,
            COALESCE(SUM(f.vol_meta * f.pmv_aplicado), 0) AS valor_atual,
            COALESCE(SUM(v.qt_pedido), 0) AS peso_historico
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON c.cgc = f.cgc
        LEFT JOIN fato_vendas v
          ON v.cgc = f.cgc AND v.sku = f.sku
         AND v.data_pedido >= CURRENT_DATE - INTERVAL '4 months'
        WHERE f.ciclo_sop = :ciclo
          AND TO_CHAR(f.mes_projetado, 'YYYY-MM') = ANY(:meses_iso)
          {filtro}
        GROUP BY regional, cliente, executivo, c.cgc, f.mes_projetado
        ORDER BY regional, executivo, cliente, f.mes_projetado
    """), params).fetchall()


@router.get("/tabela-monetaria-simone")
def tabela_monetaria_simone(
    db: Session = Depends(get_db), u: dict = Depends(require_metas)
):
    _exigir_simone(u)
    try:
        from app.api.routers.rls_metas import escopo_usuario
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        _garantir_tabelas_simone(db)
        escopo = escopo_usuario(u)
        linhas = _linhas_simone(db, ciclo, escopo, meses)
        armazenadas = db.execute(text("""
            SELECT mes_projetado, regional, cgc, valor_meta
            FROM metas_monetarias_simone
            WHERE ciclo_sop = :ciclo
        """), {"ciclo": ciclo}).fetchall()
        armazenadas_map = {
            (_mes_iso(r.mes_projetado), r.regional, r.cgc): float(r.valor_meta or 0)
            for r in armazenadas
        }
        por_regional: dict = {}
        for r in linhas:
            mes = str(r.mes)
            nome_regional = r.regional
            regional_node = por_regional.setdefault(nome_regional, {
                "regional": nome_regional, "meses": {}, "clientes": {}
            })
            chave = (mes, nome_regional, r.cgc)
            valor = armazenadas_map.get(chave, float(r.valor_atual or 0))
            item = regional_node["clientes"].setdefault(r.cgc, {
                "cgc": r.cgc, "cliente": r.cliente, "executivo": r.executivo,
                "meses": {}, "peso_historico": 0.0,
            })
            item["meses"][mes] = valor
            item["peso_historico"] += float(r.peso_historico or 0)
            regional_node["meses"][mes] = regional_node["meses"].get(mes, 0.0) + valor
        controle = db.execute(text("""
            SELECT etapa_atual FROM controle_metas_simone WHERE ciclo_sop = :ciclo
        """), {"ciclo": ciclo}).scalar()
        for reg in por_regional.values():
            reg["clientes"] = list(reg["clientes"].values())
        return {
            "simone_monetario": True,
            "ciclo": ciclo,
            "meses": [_mes_iso(m) for m in meses],
            "etapa_atual": controle or "REGIONAL",
            "regionais": list(por_regional.values()),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Falha ao carregar tabela monetária da Simone")
        raise HTTPException(500, repr(e))


@router.post("/salvar-metas-monetarias-simone")
def salvar_metas_monetarias_simone(
    payload: List[MetaMonetariaSimone],
    db: Session = Depends(get_db),
    u: dict = Depends(require_metas),
):
    _exigir_simone(u)
    if not payload:
        raise HTTPException(422, "Informe ao menos uma meta regional.")
    try:
        from app.api.routers.rls_metas import escopo_usuario
        ciclo = get_current_cycle(db)
        _garantir_tabelas_simone(db)
        if _etapa_simone_atual(db, ciclo) != "REGIONAL":
            raise HTTPException(423, "A etapa Regional já foi travada.")
        escopo = escopo_usuario(u)
        linhas = _linhas_simone(db, ciclo, escopo, get_working_window_months(db))
        por_regional_mes: dict = {}
        for r in linhas:
            por_regional_mes.setdefault((r.regional, str(r.mes)), []).append(r)
        for item in payload:
            valor = round(float(item.valor_meta), 2)
            if valor < 0:
                raise HTTPException(422, "A meta monetária não pode ser negativa.")
            folhas = por_regional_mes.get((item.regional.strip(), item.mes_projetado), [])
            if not folhas:
                raise HTTPException(404, f"Regional/mês não encontrado: {item.regional} / {item.mes_projetado}.")
            pesos = [max(0.0, float(r.peso_historico or 0)) for r in folhas]
            soma = sum(pesos) or sum(max(0.0, float(r.valor_atual or 0)) for r in folhas)
            if soma == 0:
                pesos = [1.0] * len(folhas)
                soma = float(len(folhas))
            partes = _ratear_centavos(valor, pesos)
            for r, parte in zip(folhas, partes):
                db.execute(text("""
                    INSERT INTO metas_monetarias_simone
                        (ciclo_sop, mes_projetado, regional, cgc, valor_meta, atualizado_por)
                    VALUES (:c, TO_DATE(:m, 'YYYY-MM'), :r, :cgc, :v, :p)
                    ON CONFLICT (ciclo_sop, mes_projetado, regional, cgc)
                    DO UPDATE SET valor_meta=:v, atualizado_por=:p, atualizado_em=NOW()
                """), {"c": ciclo, "m": item.mes_projetado, "r": item.regional.strip(),
                       "cgc": r.cgc, "v": parte, "p": u.get("gerente_nome")})
            db.execute(text("""
                INSERT INTO metas_regionais_simone
                    (ciclo_sop, mes_projetado, regional, valor_meta, atualizado_por)
                VALUES (:c, TO_DATE(:m, 'YYYY-MM'), :r, :v, :p)
                ON CONFLICT (ciclo_sop, mes_projetado, regional)
                DO UPDATE SET valor_meta=:v, atualizado_por=:p, atualizado_em=NOW()
            """), {"c": ciclo, "m": item.mes_projetado, "r": item.regional.strip(),
                   "v": valor, "p": u.get("gerente_nome")})
        _materializar_skus_simone(db, ciclo, u.get("gerente_nome") or SIMONE_NOME)
        db.commit()
        return {"status": "ok"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))


@router.post("/salvar-clientes-monetarios-simone")
def salvar_clientes_monetarios_simone(
    payload: List[ClienteMonetarioSimone],
    db: Session = Depends(get_db),
    u: dict = Depends(require_metas),
):
    _exigir_simone(u)
    if not payload:
        raise HTTPException(422, "Informe ao menos uma distribuição por cliente.")
    try:
        from app.api.routers.rls_metas import escopo_usuario
        ciclo = get_current_cycle(db)
        _garantir_tabelas_simone(db)
        if _etapa_simone_atual(db, ciclo) != "CLIENTE":
            raise HTTPException(423, "A etapa Cliente ainda não está aberta.")
        escopo = escopo_usuario(u)
        filtro, params = _escopo_simone_sql(escopo)
        for item in payload:
            if item.valor_meta < 0:
                raise HTTPException(422, "A meta monetária não pode ser negativa.")
            params_linha = {
                "ciclo": ciclo, "mes": item.mes_projetado, "regional": item.regional.strip(),
                "cgc": item.cgc.strip(), **params,
            }
            existe = db.execute(text(f"""
                SELECT 1 FROM fato_ibp_granular f
                JOIN dim_clientes c ON c.cgc = f.cgc
                WHERE f.ciclo_sop=:ciclo AND TO_CHAR(f.mes_projetado,'YYYY-MM')=:mes
                  AND c.cgc=:cgc
                  AND COALESCE(NULLIF(TRIM(c.regional),''),'SEM REGIONAL')=:regional
                  {filtro} LIMIT 1
            """), params_linha).fetchone()
            if not existe:
                raise HTTPException(404, f"Cliente não encontrado na regional: {item.cgc}.")
            db.execute(text("""
                INSERT INTO metas_monetarias_simone
                    (ciclo_sop, mes_projetado, regional, cgc, valor_meta, atualizado_por)
                VALUES (:c, TO_DATE(:m, 'YYYY-MM'), :r, :cgc, :v, :p)
                ON CONFLICT (ciclo_sop, mes_projetado, regional, cgc)
                DO UPDATE SET valor_meta=:v, atualizado_por=:p, atualizado_em=NOW()
            """), {"c": ciclo, "m": item.mes_projetado, "r": item.regional.strip(),
                   "cgc": item.cgc.strip(), "v": round(item.valor_meta, 2),
                   "p": u.get("gerente_nome")})
        _materializar_skus_simone(db, ciclo, u.get("gerente_nome") or SIMONE_NOME)
        db.commit()
        return {"status": "ok"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))


class TravarSimone(BaseModel):
    etapa: str


@router.post("/travar-fase-monetaria-simone")
def travar_fase_monetaria_simone(
    payload: TravarSimone,
    db: Session = Depends(get_db),
    u: dict = Depends(require_metas),
):
    _exigir_simone(u)
    if payload.etapa not in ("REGIONAL", "CLIENTE", "CONFIRMACAO"):
        raise HTTPException(422, "Etapa monetária inválida.")
    try:
        ciclo = get_current_cycle(db)
        _garantir_tabelas_simone(db)
        atual = db.execute(text("""
            SELECT etapa_atual FROM controle_metas_simone WHERE ciclo_sop=:c
        """), {"c": ciclo}).scalar() or "REGIONAL"
        ordem = {"REGIONAL": 0, "CLIENTE": 1, "CONFIRMACAO": 2}
        if payload.etapa != atual or ordem[payload.etapa] != ordem[atual]:
            raise HTTPException(409, f"A etapa atual é {atual}.")
        if payload.etapa == "CONFIRMACAO":
            _materializar_skus_simone(db, ciclo, u.get("gerente_nome") or SIMONE_NOME)
        proxima = {"REGIONAL": "CLIENTE", "CLIENTE": "CONFIRMACAO", "CONFIRMACAO": "CONFIRMACAO"}[atual]
        db.execute(text("""
            INSERT INTO controle_metas_simone (ciclo_sop, etapa_atual, atualizado_por)
            VALUES (:c, :e, :p)
            ON CONFLICT (ciclo_sop)
            DO UPDATE SET etapa_atual=:e, atualizado_por=:p, atualizado_em=NOW()
        """), {"c": ciclo, "e": proxima, "p": u.get("gerente_nome")})
        db.commit()
        return {"status": "ok", "etapa_atual": proxima}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, repr(e))


DDL_METAS_FINANCEIRAS = """
CREATE TABLE IF NOT EXISTS metas_financeiras_responsavel (
    id               SERIAL PRIMARY KEY,
    ciclo_sop        VARCHAR(7)   NOT NULL,
    nivel            VARCHAR(20)  NOT NULL,
    nome_responsavel VARCHAR(120) NOT NULL,
    mes_projetado    DATE         NOT NULL,
    valor_meta       NUMERIC(18,2) NOT NULL DEFAULT 0,
    definido_por     VARCHAR(120),
    atualizado_em    TIMESTAMP DEFAULT NOW(),
    UNIQUE (ciclo_sop, nivel, nome_responsavel, mes_projetado)
);
CREATE INDEX IF NOT EXISTS ix_metas_fin_resp_ciclo ON metas_financeiras_responsavel (ciclo_sop, nivel, nome_responsavel);
"""


def garantir_tabela_metas_financeiras(db: Session) -> None:
    for stmt in DDL_METAS_FINANCEIRAS.strip().split(";"):
        s = stmt.strip()
        if s:
            db.execute(text(s))


def _stream_xlsx(buf: io.BytesIO, nome: str) -> StreamingResponse:
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def _escopo_efetivo_para_acao(u: dict, responsavel: Optional[str], nivel: Optional[str]) -> tuple[dict, dict]:
    from app.api.routers.rls_metas import escopo_usuario, NIVEL_GERENTE, NIVEL_COORDENADOR
    escopo = escopo_usuario(u)
    if escopo["ve_tudo"] and responsavel:
        nv = (nivel or "").strip()
        if nv not in (NIVEL_GERENTE, NIVEL_COORDENADOR):
            raise HTTPException(422, "nivel_alvo deve ser Gerente ou Coordenador.")
        return {
            **escopo,
            "ve_tudo": False,
            "funcao": nv,
            "campo_rls": "gerente_nome" if nv == NIVEL_GERENTE else "supervisor_nome",
            "valor_rls": responsavel.strip(),
            "nome_responsavel": responsavel.strip(),
        }, escopo
    return escopo, escopo


def _aplicar_ajustes_meta(db: Session, ciclo: str, ajustes: List[AjusteMeta], escopo: Optional[dict] = None) -> None:
    filtro_escopo = ""
    params_escopo: dict = {}
    if escopo is not None:
        filtro_escopo, params_escopo = _filtro_escopo_sql(escopo, alias_cli="c")
    for aj in ajustes:
        check_imutabilidade_mes(aj.mes_projetado, aj.sku, contexto="Meta")
        filtro_hierarquia = ""
        params_hierarquia = {}
        if aj.gerente_nome:
            filtro_hierarquia += """
              AND COALESCE(NULLIF(TRIM(c.gerente_nome),''), 'SEM GERENTE') = :gerente_nome
            """
            params_hierarquia["gerente_nome"] = aj.gerente_nome.strip()
        if aj.coordenador_nome:
            filtro_hierarquia += """
              AND COALESCE(NULLIF(TRIM(c.supervisor_nome),''), 'SEM COORDENADOR') = :coordenador_nome
            """
            params_hierarquia["coordenador_nome"] = aj.coordenador_nome.strip()
        if aj.vendedor_nome:
            filtro_hierarquia += """
              AND COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR') = :vendedor_nome
            """
            params_hierarquia["vendedor_nome"] = aj.vendedor_nome.strip()

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
              {filtro_hierarquia}
              {filtro_escopo}
            GROUP BY f.id, f.cgc ORDER BY f.id
        """.format(filtro_hierarquia=filtro_hierarquia, filtro_escopo=filtro_escopo)), {
            "ciclo": ciclo,
            "mes": aj.mes_projetado,
            "sku": aj.sku,
            "razao": aj.razao_social,
            **params_hierarquia,
            **params_escopo,
        }).fetchall()

        if not result:
            raise HTTPException(
                404,
                f"Nenhuma linha encontrada para {aj.gerente_nome or 'gerente'} / {aj.coordenador_nome or 'coordenador'} / {aj.vendedor_nome or 'executivo'} / {aj.razao_social} / {aj.sku} / {aj.mes_projetado}."
            )

        pesos = [max(0.0, float(r.peso_historico or 0)) for r in result]
        partes = ratear_maior_resto(aj.novo_volume, pesos)

        for r, parte in zip(result, partes):
            db.execute(text("UPDATE fato_ibp_granular SET vol_meta=:v WHERE id=:id"),
                       {"v": int(parte), "id": r.fato_id})

        propagar_linha_jusante(db, ciclo, aj.sku, aj.mes_projetado, ETAPA_METAS)


def _filtro_escopo_sql(escopo: dict, alias_cli: str = "c") -> tuple[str, dict]:
    from app.api.routers.rls_metas import clausula_rls
    rls = clausula_rls(escopo, alias_cli=alias_cli)
    return f"AND {rls['where']}", dict(rls["params"])


def _registrar_metas_coordenadores(db: Session, ciclo: str, escopo: dict, definido_por: str) -> None:
    garantir_tabela_metas_financeiras(db)
    filtro, params = _filtro_escopo_sql(escopo, alias_cli="c")
    params["ciclo"] = ciclo
    rows = db.execute(text(f"""
        SELECT
            COALESCE(NULLIF(TRIM(c.supervisor_nome),''), 'SEM COORDENADOR') AS coordenador,
            f.mes_projetado,
            COALESCE(SUM(f.vol_meta * f.pmv_aplicado), 0) AS valor_meta
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON c.cgc = f.cgc
        WHERE f.ciclo_sop = :ciclo {filtro}
        GROUP BY coordenador, f.mes_projetado
    """), params).fetchall()
    for r in rows:
        db.execute(text("""
            INSERT INTO metas_financeiras_responsavel
                (ciclo_sop, nivel, nome_responsavel, mes_projetado, valor_meta, definido_por)
            VALUES (:c, 'Coordenador', :n, :m, :v, :p)
            ON CONFLICT (ciclo_sop, nivel, nome_responsavel, mes_projetado)
            DO UPDATE SET valor_meta=:v, definido_por=:p, atualizado_em=NOW()
        """), {
            "c": ciclo,
            "n": r.coordenador,
            "m": r.mes_projetado,
            "v": round(float(r.valor_meta or 0), 2),
            "p": definido_por,
        })


TOLERANCIA_TRANCAMENTO_COORDENADOR = 0.01  # ±1% por mês, medido contra a meta que o gerente passou


def _coordenador_recebeu_meta(db: Session, ciclo: str, coordenador: str) -> bool:
    """
    Diz se o Gerente já passou a meta financeira para este coordenador neste
    ciclo (existe linha em metas_financeiras_responsavel). Sem essa meta, o
    coordenador não tem contra o que se comparar e não deve poder editar/
    trancar — precisa aguardar o gerente clicar em "Passar para coordenadores".
    """
    garantir_tabela_metas_financeiras(db)
    existe = db.execute(text("""
        SELECT 1 FROM metas_financeiras_responsavel
        WHERE ciclo_sop = :ciclo AND nivel = 'Coordenador'
          AND TRIM(nome_responsavel) = :coordenador
        LIMIT 1
    """), {"ciclo": ciclo, "coordenador": coordenador.strip()}).fetchone()
    return existe is not None


def _validar_tolerancia_coordenador(db: Session, ciclo: str, coordenador: str) -> list[dict]:
    """
    Compara o total editado pelo coordenador (vol_meta) contra a meta
    financeira que o Gerente passou (metas_financeiras_responsavel). SEM
    fallback para vol_bottomup: se o Gerente não passou a meta ainda, não há
    base de comparação válida — quem chama esta função já deve ter garantido
    com _coordenador_recebeu_meta() que a meta existe.
    """
    garantir_tabela_metas_financeiras(db)
    rows = db.execute(text("""
        WITH atual AS (
            SELECT f.mes_projetado,
                   COALESCE(SUM(f.vol_meta * f.pmv_aplicado), 0) AS valor_atual
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON c.cgc = f.cgc
            WHERE f.ciclo_sop = :ciclo
              AND TRIM(c.supervisor_nome) = :coordenador
            GROUP BY f.mes_projetado
        ),
        alvo AS (
            SELECT mes_projetado, valor_meta AS valor_alvo
            FROM metas_financeiras_responsavel
            WHERE ciclo_sop = :ciclo
              AND nivel = 'Coordenador'
              AND TRIM(nome_responsavel) = :coordenador
        )
        SELECT TO_CHAR(a.mes_projetado, 'YYYY-MM') AS mes,
               a.valor_atual,
               COALESCE(al.valor_alvo, 0) AS valor_alvo,
               CASE WHEN COALESCE(al.valor_alvo, 0) > 0
                    THEN (a.valor_atual - al.valor_alvo) / al.valor_alvo
                    ELSE 0 END AS variacao
        FROM atual a
        LEFT JOIN alvo al ON al.mes_projetado = a.mes_projetado
        ORDER BY a.mes_projetado
    """), {"ciclo": ciclo, "coordenador": coordenador.strip()}).fetchall()
    fora = []
    for r in rows:
        variacao = float(r.variacao or 0)
        if abs(variacao) > TOLERANCIA_TRANCAMENTO_COORDENADOR:
            fora.append({
                "mes": r.mes,
                "valor_atual": round(float(r.valor_atual or 0), 2),
                "valor_alvo": round(float(r.valor_alvo or 0), 2),
                "variacao_pct": round(variacao * 100, 2),
            })
    return fora


def _coordenadores_pendentes(db: Session, ciclo: str, escopo: dict) -> list[str]:
    from app.api.routers.rls_metas import garantir_tabela_cadeados
    garantir_tabela_cadeados(db)
    filtro, params = _filtro_escopo_sql(escopo, alias_cli="c")
    params["ciclo"] = ciclo
    rows = db.execute(text(f"""
        SELECT DISTINCT COALESCE(NULLIF(TRIM(c.supervisor_nome),''), 'SEM COORDENADOR') AS coordenador
        FROM dim_clientes c
        WHERE EXISTS (
            SELECT 1 FROM fato_ibp_granular f
            WHERE f.ciclo_sop = :ciclo AND f.cgc = c.cgc
        )
        {filtro}
        ORDER BY 1
    """), params).fetchall()
    todos = [r.coordenador for r in rows if r.coordenador]
    if not todos:
        return []
    travados = db.execute(text("""
        SELECT TRIM(nome_responsavel) AS nome
        FROM controle_metas_responsavel
        WHERE ciclo_sop = :ciclo
          AND nivel = 'Coordenador'
          AND status = 'CONGELADO'
    """), {"ciclo": ciclo}).fetchall()
    travados_set = {r.nome for r in travados if r.nome in todos}
    return [n for n in todos if n not in travados_set]


# ---------------------------------------------------------------------------
# POST /distribuir  — ajuste percentual/valor por nível (Coordenador dentro
# do SKU para o Gerente; Executivo dentro do SKU e Razão Social dentro do
# Executivo+SKU para o Coordenador), rateando até o CNPJ.
# ---------------------------------------------------------------------------
NIVEL_COORDENADOR_DIST = "COORDENADOR"
NIVEL_EXECUTIVO_DIST = "EXECUTIVO"
NIVEL_RAZAO_SOCIAL_DIST = "RAZAO_SOCIAL"

CAMPO_SQL_POR_NIVEL = {
    NIVEL_COORDENADOR_DIST: ("c.supervisor_nome", "SEM COORDENADOR"),
    NIVEL_EXECUTIVO_DIST:   ("f.vendedor_nome",    "SEM VENDEDOR"),
    NIVEL_RAZAO_SOCIAL_DIST:("c.razaosocial",      "SEM RAZAO SOCIAL"),
}


class ItemDistribuicao(BaseModel):
    chave: str                                   # nome do coordenador/executivo/razão social
    percentual_valor: Optional[float] = None      # 0.0 a 1.0+
    percentual_volume: Optional[float] = None
    novo_valor: Optional[float] = None            # R$ direto
    novo_volume: Optional[int] = None             # caixas direto


class PayloadDistribuir(BaseModel):
    sku: str
    mes_projetado: str
    nivel: str                                    # COORDENADOR | EXECUTIVO | RAZAO_SOCIAL
    executivo_pai: Optional[str] = None           # obrigatório quando nivel=RAZAO_SOCIAL (teto = executivo NAQUELE sku)
    itens: List[ItemDistribuicao]
    responsavel_nome: Optional[str] = None        # somente Administrador
    responsavel_nivel: Optional[str] = None


def _teto_do_no_pai(db: Session, ciclo: str, sku: str, mes: str, escopo: dict,
                     nivel: str, executivo_pai: Optional[str]) -> int:
    """
    Volume (caixas) atual do nó pai que serve de teto (100%) para a
    distribuição percentual do nível corrente:
      COORDENADOR   -> teto = SKU inteiro, dentro do escopo do Gerente.
      EXECUTIVO     -> teto = SKU inteiro, dentro do escopo do Coordenador.
      RAZAO_SOCIAL  -> teto = Executivo NAQUELE SKU (não o total do executivo).
    """
    filtro_escopo, params_escopo = _filtro_escopo_sql(escopo, alias_cli="c")
    filtro_exec = ""
    params_exec: dict = {}
    if nivel == NIVEL_RAZAO_SOCIAL_DIST:
        if not executivo_pai:
            raise HTTPException(422, "executivo_pai é obrigatório para distribuir Razão Social.")
        filtro_exec = "AND COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR') = :executivo_pai"
        params_exec["executivo_pai"] = executivo_pai.strip()

    total = db.execute(text(f"""
        SELECT COALESCE(SUM(f.vol_meta), 0) AS total
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        WHERE f.ciclo_sop = :ciclo
          AND TO_CHAR(f.mes_projetado,'YYYY-MM') = :mes
          AND f.sku = :sku
          {filtro_exec}
          {filtro_escopo}
    """), {"ciclo": ciclo, "mes": mes, "sku": sku, **params_exec, **params_escopo}).scalar()
    return int(total or 0)


def _pmv_vigente(db: Session, ciclo: str, sku: str, mes: str) -> float:
    pmv = db.execute(text("""
        SELECT COALESCE(SUM(pmv_aplicado * vol_meta) / NULLIF(SUM(vol_meta), 0), 0)
        FROM fato_ibp_granular
        WHERE ciclo_sop = :c AND sku = :s AND TO_CHAR(mes_projetado,'YYYY-MM') = :m
    """), {"c": ciclo, "s": sku, "m": mes}).scalar() or 0
    return float(pmv)


def _aplicar_distribuicao(db: Session, ciclo: str, payload: PayloadDistribuir, escopo: dict) -> dict:
    """
    Aplica os itens da distribuição (percentual ou valor direto) para o
    nível pedido, ratear até o CNPJ com peso histórico (4 meses), sem
    alterar os irmãos não mencionados no payload.
    Retorna o resumo de cada item aplicado (para o frontend atualizar a UI
    sem precisar recarregar a árvore inteira).
    """
    nivel = payload.nivel
    if nivel not in CAMPO_SQL_POR_NIVEL:
        raise HTTPException(422, "nivel deve ser COORDENADOR, EXECUTIVO ou RAZAO_SOCIAL.")

    check_imutabilidade_mes(payload.mes_projetado, payload.sku, contexto="Meta")

    teto = _teto_do_no_pai(db, ciclo, payload.sku, payload.mes_projetado, escopo,
                            nivel, payload.executivo_pai)
    pmv = _pmv_vigente(db, ciclo, payload.sku, payload.mes_projetado)

    campo_sql, rotulo_vazio = CAMPO_SQL_POR_NIVEL[nivel]
    filtro_escopo, params_escopo = _filtro_escopo_sql(escopo, alias_cli="c")
    filtro_exec = ""
    params_exec: dict = {}
    if nivel == NIVEL_RAZAO_SOCIAL_DIST:
        filtro_exec = "AND COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR') = :executivo_pai"
        params_exec["executivo_pai"] = payload.executivo_pai.strip()

    resultado = []
    for item in payload.itens:
        # 1. Determina o novo total (caixas) do item, a partir de % ou valor direto.
        if item.novo_volume is not None:
            novo_total = int(item.novo_volume)
        elif item.novo_valor is not None:
            if pmv <= 0:
                raise HTTPException(422, f"SKU {payload.sku} sem PMV no mês {payload.mes_projetado}; edição em R$ bloqueada.")
            novo_total = int(round(item.novo_valor / pmv))
        elif item.percentual_volume is not None:
            novo_total = int(round(item.percentual_volume * teto))
        elif item.percentual_valor is not None:
            novo_total = int(round(item.percentual_valor * teto))
        else:
            raise HTTPException(422, f"Item '{item.chave}' precisa de percentual_valor, percentual_volume, novo_valor ou novo_volume.")

        # 2. Busca as linhas de CNPJ do item (coordenador/executivo/razão social),
        #    restritas ao escopo do responsável logado, com peso histórico.
        rows = db.execute(text(f"""
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
              AND COALESCE(NULLIF(TRIM({campo_sql}),''), '{rotulo_vazio}') = :chave
              {filtro_exec}
              {filtro_escopo}
            GROUP BY f.id, f.cgc ORDER BY f.id
        """), {
            "ciclo": ciclo, "mes": payload.mes_projetado, "sku": payload.sku,
            "chave": item.chave.strip(), **params_exec, **params_escopo,
        }).fetchall()

        if not rows:
            raise HTTPException(404, f"Nenhuma linha encontrada para {nivel} '{item.chave}' no SKU {payload.sku}.")

        pesos = [max(0.0, float(r.peso_historico or 0)) for r in rows]
        partes = ratear_maior_resto(novo_total, pesos)
        for r, parte in zip(rows, partes):
            db.execute(text("UPDATE fato_ibp_granular SET vol_meta=:v WHERE id=:id"),
                       {"v": int(parte), "id": r.fato_id})

        propagar_linha_jusante(db, ciclo, payload.sku, payload.mes_projetado, ETAPA_METAS)

        resultado.append({
            "chave": item.chave,
            "novo_volume": novo_total,
            "novo_valor": round(novo_total * pmv, 2),
            "percentual_valor": round(novo_total / teto, 4) if teto else None,
            "percentual_volume": round(novo_total / teto, 4) if teto else None,
        })

    return {"teto": teto, "pmv": pmv, "itens": resultado}


@router.post("/distribuir")
def distribuir(payload: PayloadDistribuir, db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """
    Ajusta percentual/valor/volume de um Coordenador (dentro de um SKU, na
    visão do Gerente), de um Executivo (dentro de um SKU, na visão do
    Coordenador) ou de uma Razão Social (dentro de um Executivo+SKU, na
    visão do Coordenador). Edita SÓ o item informado — os irmãos não
    mencionados no payload permanecem intocados.
    """
    try:
        from app.api.routers.rls_metas import escopo_usuario, NIVEL_GERENTE, NIVEL_COORDENADOR
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_METAS) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Etapa congelada — não é possível distribuir.")

        escopo = escopo_usuario(u)
        if escopo["ve_tudo"]:
            nome = (payload.responsavel_nome or "").strip()
            funcao = (payload.responsavel_nivel or "").strip()
            if not nome or funcao not in (NIVEL_GERENTE, NIVEL_COORDENADOR):
                raise HTTPException(422, "Administrador deve informar responsavel_nome e responsavel_nivel.")
            escopo = {
                **escopo, "ve_tudo": False, "nivel_ok": True, "funcao": funcao,
                "campo_rls": "gerente_nome" if funcao == NIVEL_GERENTE else "supervisor_nome",
                "valor_rls": nome, "nome_responsavel": nome,
            }
        funcao = escopo["funcao"]

        if funcao == NIVEL_COORDENADOR and u.get("funcao") != "Administrador":
            if not _coordenador_recebeu_meta(db, ciclo, escopo["nome_responsavel"]):
                raise HTTPException(
                    423,
                    "O Gerente ainda não passou a meta financeira para a sua coordenação neste ciclo."
                )

        if funcao == NIVEL_GERENTE and payload.nivel != NIVEL_COORDENADOR_DIST:
            raise HTTPException(400, "Gerente só distribui no nível COORDENADOR.")
        if funcao == NIVEL_COORDENADOR and payload.nivel not in (NIVEL_EXECUTIVO_DIST, NIVEL_RAZAO_SOCIAL_DIST):
            raise HTTPException(400, "Coordenador só distribui nos níveis EXECUTIVO ou RAZAO_SOCIAL.")

        resultado = _aplicar_distribuicao(db, ciclo, payload, escopo)
        db.commit()
        return {"status": "ok", **resultado}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


class PayloadTravarFase(BaseModel):
    responsavel_nome: Optional[str] = None    # somente Administrador
    responsavel_nivel: Optional[str] = None


class PayloadReabrirFaseMeta(BaseModel):
    nome_alvo: str
    nivel_alvo: Optional[str] = None          # 'Gerente' | 'Coordenador'


@router.post("/travar-fase")
def travar_fase(payload: PayloadTravarFase, db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """
    Trava a fase corrente do responsável logado (ou de quem o Admin
    indicar) e avança para a próxima. Valida a tolerância de ±1% entre o
    total distribuído e o teto oficial antes de travar. Na última fase de
    cada perfil (COORDENADOR p/ Gerente, RAZAO_SOCIAL p/ Coordenador), não
    avança — apenas marca como travada (fim da cascata daquele perfil).
      - Gerente travando COORDENADOR: dispara o registro automático da
        meta financeira de cada coordenador (metas_financeiras_responsavel).
      - Coordenador travando RAZAO_SOCIAL: dispara o cadeado final
        (controle_metas_responsavel), igual ao fluxo antigo de trancamento.
    """
    try:
        from app.api.routers.rls_metas import escopo_usuario, NIVEL_GERENTE, NIVEL_COORDENADOR
        from app.api.routers.fase_meta import (
            fase_atual_do_responsavel, avancar_fase_meta, travar_fase_final,
            validar_tolerancia_antes_de_travar, FASE_SKU, FASE_COORDENADOR,
            FASE_EXECUTIVO, FASE_RAZAO_SOCIAL,
        )
        ciclo = get_current_cycle(db)
        if etapa_congelada(db, ciclo, ETAPA_METAS) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Etapa congelada — não é possível travar fase.")

        escopo = escopo_usuario(u)
        if escopo["ve_tudo"]:
            nome = (payload.responsavel_nome or "").strip()
            funcao = (payload.responsavel_nivel or "").strip()
            if not nome or funcao not in (NIVEL_GERENTE, NIVEL_COORDENADOR):
                raise HTTPException(422, "Administrador deve informar responsavel_nome e responsavel_nivel.")
            escopo = {
                **escopo, "ve_tudo": False, "nivel_ok": True, "funcao": funcao,
                "campo_rls": "gerente_nome" if funcao == NIVEL_GERENTE else "supervisor_nome",
                "valor_rls": nome, "nome_responsavel": nome,
            }
        funcao = escopo["funcao"]
        nome = escopo["nome_responsavel"]

        estado = fase_atual_do_responsavel(db, ciclo, nome, funcao)
        fase = estado["fase_atual"]
        eh_ultima_fase = (
            (funcao == NIVEL_GERENTE and fase == FASE_COORDENADOR) or
            (funcao == NIVEL_COORDENADOR and fase == FASE_RAZAO_SOCIAL)
        )
        fase_a_validar = fase if eh_ultima_fase else fase

        # SKU (Gerente) e EXECUTIVO (Coordenador) não têm % a validar contra
        # tolerância — são a origem do teto, não uma distribuição percentual.
        if fase not in (FASE_SKU, FASE_EXECUTIVO):
            fora = validar_tolerancia_antes_de_travar(db, ciclo, escopo, funcao, fase_a_validar)
            if fora:
                raise HTTPException(
                    422,
                    {
                        "mensagem": "Distribuição fora da tolerância de ±1% em relação ao teto definido.",
                        "detalhes": fora,
                    },
                )

        if eh_ultima_fase:
            novo_estado = travar_fase_final(db, ciclo, nome, funcao)
            if funcao == NIVEL_GERENTE:
                _registrar_metas_coordenadores(db, ciclo, escopo, definido_por=nome)
            else:
                from app.api.routers.rls_metas import travar_cadeado
                travar_cadeado(db, escopo, ciclo)
        else:
            novo_estado = avancar_fase_meta(db, escopo, ciclo)

        db.commit()
        return {"status": "ok", "fase": novo_estado["fase_atual"],
                "travado_definitivamente": eh_ultima_fase}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir-fase-meta")
def reabrir_fase_meta_endpoint(payload: PayloadReabrirFaseMeta, db: Session = Depends(get_db),
                                u: dict = Depends(require_metas)):
    """Reabre (Admin) a carteira de um responsável de volta à fase inicial."""
    try:
        from app.api.routers.rls_metas import escopo_usuario
        from app.api.routers.fase_meta import reabrir_fase_meta as _reabrir
        ciclo = get_current_cycle(db)
        escopo = escopo_usuario(u)
        _reabrir(db, escopo, ciclo, payload.nome_alvo, payload.nivel_alvo)
        return {"status": "ok"}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


def _gerar_evidencia_metas(db: Session, ciclo: str, escopo: dict, nome_arquivo: str) -> StreamingResponse:
    filtro, params = _filtro_escopo_sql(escopo, alias_cli="c")
    params["ciclo"] = ciclo
    rows = db.execute(text(f"""
        SELECT
            f.ciclo_sop,
            TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes,
            COALESCE(NULLIF(TRIM(c.gerente_nome),''), 'SEM GERENTE') AS gerente,
            COALESCE(NULLIF(TRIM(c.supervisor_nome),''), 'SEM COORDENADOR') AS coordenador,
            COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR') AS executivo,
            TRIM(f.sku) AS sku,
            COALESCE(NULLIF(TRIM(p.descricao),''), 'SEM DESCRICAO') AS descricao,
            COALESCE(NULLIF(TRIM(c.razaosocial),''), 'SEM RAZAO SOCIAL') AS razao_social,
            f.cgc,
            COALESCE(f.vol_bottomup, 0) AS vol_bottomup,
            COALESCE(f.vol_meta, 0) AS vol_meta,
            COALESCE(f.pmv_aplicado, 0) AS pmv,
            COALESCE(f.vol_bottomup * f.pmv_aplicado, 0) AS valor_bottomup,
            COALESCE(f.vol_meta * f.pmv_aplicado, 0) AS valor_meta
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON c.cgc = f.cgc
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :ciclo {filtro}
        ORDER BY gerente, coordenador, executivo, sku, razao_social, f.mes_projetado, f.cgc
    """), params).fetchall()
    df = pd.DataFrame([dict(r._mapping) for r in rows])
    if df.empty:
        df = pd.DataFrame(columns=[
            "ciclo_sop", "mes", "gerente", "coordenador", "executivo", "sku",
            "descricao", "razao_social", "cgc", "vol_bottomup", "vol_meta",
            "pmv", "valor_bottomup", "valor_meta"
        ])
    else:
        df["delta_caixas"] = df["vol_meta"] - df["vol_bottomup"]
        df["delta_valor"] = df["valor_meta"] - df["valor_bottomup"]

    tela_cols = ["gerente", "coordenador", "executivo", "sku", "descricao", "razao_social", "mes"]
    if df.empty:
        df_tela = pd.DataFrame(columns=tela_cols + ["vol_bottomup", "valor_bottomup", "vol_meta", "valor_meta", "delta_caixas", "delta_valor"])
    else:
        df_tela = df.groupby(tela_cols, as_index=False).agg({
            "vol_bottomup": "sum",
            "valor_bottomup": "sum",
            "vol_meta": "sum",
            "valor_meta": "sum",
            "delta_caixas": "sum",
            "delta_valor": "sum",
        })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df_tela.to_excel(w, index=False, sheet_name="Tela")
        df.to_excel(w, index=False, sheet_name="Granular")
        pd.DataFrame([
            {"campo": "ciclo", "valor": ciclo},
            {"campo": "escopo", "valor": escopo.get("nome_responsavel") or "ADMIN"},
            {"campo": "nivel", "valor": escopo.get("funcao") or "Administrador"},
        ]).to_excel(w, index=False, sheet_name="Evidencia")
    return _stream_xlsx(buf, nome_arquivo)

@router.post("/salvar")
def salvar(payload: PayloadAcaoMetas, db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    try:
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_METAS) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Etapa congelada — não é possível salvar.")

        from app.api.routers.rls_metas import NIVEL_COORDENADOR
        escopo, _escopo_exec = _escopo_efetivo_para_acao(u, payload.nome_alvo, payload.nivel_alvo)
        if escopo.get("funcao") == NIVEL_COORDENADOR and u.get("funcao") != "Administrador":
            if not _coordenador_recebeu_meta(db, ciclo, escopo["nome_responsavel"]):
                raise HTTPException(
                    423,
                    "O Gerente ainda não passou a meta financeira para a sua coordenação neste ciclo. "
                    "Aguarde o botão 'Passar para coordenadores'."
                )
        _aplicar_ajustes_meta(db, ciclo, payload.ajustes, escopo)
        db.commit()
        return {"status": "ok"}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/salvar-evidencia")
def salvar_evidencia(payload: PayloadAcaoMetas, db: Session = Depends(get_db),
                     u: dict = Depends(require_metas)):
    try:
        from app.api.routers.rls_metas import NIVEL_COORDENADOR
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_METAS) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Etapa congelada — não é possível salvar.")
        escopo_acao, escopo_executor = _escopo_efetivo_para_acao(u, payload.nome_alvo, payload.nivel_alvo)
        if escopo_acao.get("funcao") == NIVEL_COORDENADOR and u.get("funcao") != "Administrador":
            if not _coordenador_recebeu_meta(db, ciclo, escopo_acao["nome_responsavel"]):
                raise HTTPException(
                    423,
                    "O Gerente ainda não passou a meta financeira para a sua coordenação neste ciclo. "
                    "Aguarde o botão 'Passar para coordenadores'."
                )
        _aplicar_ajustes_meta(db, ciclo, payload.ajustes, escopo_acao)
        db.commit()
        nome = f"metas_salvar_{ciclo.replace('/','_')}_{(escopo_acao.get('nome_responsavel') or 'admin').replace(' ','_')}.xlsx"
        return _gerar_evidencia_metas(db, ciclo, escopo_acao, nome)
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/passar-coordenadores-evidencia")
def passar_coordenadores_evidencia(payload: PayloadAcaoMetas, db: Session = Depends(get_db),
                                   u: dict = Depends(require_metas)):
    try:
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        escopo_acao, escopo_executor = _escopo_efetivo_para_acao(u, payload.nome_alvo, payload.nivel_alvo)
        if escopo_acao.get("funcao") not in ("Gerente", "Administrador"):
            raise HTTPException(403, "Somente Gerente ou Administrador passa metas para coordenadores.")
        _aplicar_ajustes_meta(db, ciclo, payload.ajustes, escopo_acao)
        executor = escopo_executor.get("nome_responsavel") or u.get("nome") or u.get("email") or "ADMIN"
        _registrar_metas_coordenadores(db, ciclo, escopo_acao, executor)
        db.commit()
        nome = f"metas_passar_coordenadores_{ciclo.replace('/','_')}_{(escopo_acao.get('nome_responsavel') or 'admin').replace(' ','_')}.xlsx"
        return _gerar_evidencia_metas(db, ciclo, escopo_acao, nome)
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/trancar-evidencia")
def trancar_evidencia(payload: PayloadAcaoMetas, db: Session = Depends(get_db),
                      u: dict = Depends(require_metas)):
    try:
        from app.api.routers.rls_metas import escopo_usuario, travar_cadeado, NIVEL_COORDENADOR
        ciclo = get_current_cycle(db)
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_METAS) and u.get("funcao") != "Administrador":
            raise HTTPException(423, "Etapa congelada — não é possível trancar.")
        escopo_acao, escopo_executor = _escopo_efetivo_para_acao(u, payload.nome_alvo, payload.nivel_alvo)
        if escopo_acao.get("funcao") != NIVEL_COORDENADOR:
            raise HTTPException(403, "Somente carteiras de Coordenador usam Salvar e trancar.")
        if u.get("funcao") != "Administrador" and not _coordenador_recebeu_meta(db, ciclo, escopo_acao["nome_responsavel"]):
            raise HTTPException(
                423,
                "O Gerente ainda não passou a meta financeira para a sua coordenação neste ciclo. "
                "Aguarde o botão 'Passar para coordenadores'."
            )
        _aplicar_ajustes_meta(db, ciclo, payload.ajustes, escopo_acao)
        fora = _validar_tolerancia_coordenador(db, ciclo, escopo_acao["nome_responsavel"])
        if fora and u.get("funcao") != "Administrador":
            raise HTTPException(422, {"mensagem": "Coordenador fora da tolerância de ±1%.", "itens": fora})
        if escopo_executor["ve_tudo"]:
            travar_cadeado(db, escopo_executor, ciclo, nome_alvo=escopo_acao["nome_responsavel"], nivel_alvo=NIVEL_COORDENADOR)
        else:
            travar_cadeado(db, escopo_acao, ciclo)
        db.commit()
        nome = f"metas_trancar_{ciclo.replace('/','_')}_{escopo_acao['nome_responsavel'].replace(' ','_')}.xlsx"
        return _gerar_evidencia_metas(db, ciclo, escopo_acao, nome)
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.get("/auditoria-impacto")
def auditoria_impacto(responsavel: str = None, nivel_responsavel: str = None,
                      db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """Compara a meta atual com o BottomUP no mesmo escopo da carteira."""
    try:
        from app.api.routers.rls_metas import escopo_usuario, clausula_rls
        ciclo = get_current_cycle(db)
        escopo = escopo_usuario(u)
        rls = clausula_rls(escopo, alias_cli="c")
        params = {"ciclo": ciclo}
        params.update(rls["params"])
        filtro = f"AND {rls['where']}"
        alvo = (responsavel or "").strip()
        nivel = (nivel_responsavel or "").strip()
        if alvo:
            if not escopo["ve_tudo"]:
                raise HTTPException(403, "Somente o Administrador pode selecionar outra alçada.")
            if nivel not in ("Gerente", "Coordenador"):
                raise HTTPException(422, "nivel_responsavel deve ser Gerente ou Coordenador.")
            campo = "gerente_nome" if nivel == "Gerente" else "supervisor_nome"
            filtro = f"AND TRIM(c.{campo}) = :responsavel_alvo"
            params["responsavel_alvo"] = alvo
        rows = db.execute(text(f"""
            SELECT COALESCE(NULLIF(TRIM(c.supervisor_nome), ''), 'SEM COORDENADOR') AS coordenador,
                   TRIM(f.sku) AS sku,
                   COALESCE(NULLIF(TRIM(p.descricao), ''), 'SEM DESCRICAO') AS descricao,
                   TO_CHAR(f.mes_projetado, 'YYYY-MM') AS mes,
                   SUM(f.vol_bottomup) AS bottomup,
                   SUM(f.vol_meta) AS meta,
                   SUM(f.vol_bottomup * f.pmv_aplicado) AS bottomup_rs,
                   SUM(f.vol_meta * f.pmv_aplicado) AS meta_rs
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON c.cgc = f.cgc
            JOIN dim_produtos p ON p.sku = f.sku
            WHERE f.ciclo_sop = :ciclo {filtro}
            GROUP BY c.supervisor_nome, f.sku, p.descricao, f.mes_projetado
            ORDER BY coordenador, f.sku, f.mes_projetado
        """), params).fetchall()
        itens = []
        for r in rows:
            bu = float(r.bottomup or 0)
            meta = float(r.meta or 0)
            bu_rs = float(r.bottomup_rs or 0)
            meta_rs = float(r.meta_rs or 0)
            itens.append({
                "coordenador": r.coordenador, "sku": r.sku, "descricao": r.descricao,
                "mes": r.mes, "bottomup": int(bu), "meta": int(meta),
                "delta_caixas": int(meta - bu),
                "impacto_percentual": round((meta - bu) / bu * 100, 2) if bu else None,
                "bottomup_rs": round(bu_rs, 2), "meta_rs": round(meta_rs, 2),
                "delta_rs": round(meta_rs - bu_rs, 2),
                "impacto_rs_percentual": round((meta_rs - bu_rs) / bu_rs * 100, 2) if bu_rs else None,
            })
        return {"ciclo": ciclo, "itens": itens}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.get("/auditoria-impacto/exportar")
def exportar_auditoria_impacto(responsavel: str = None, nivel_responsavel: str = None,
                               db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """Exporta a auditoria de impacto no mesmo escopo RLS da tela."""
    dados = auditoria_impacto(responsavel, nivel_responsavel, db, u)
    itens = dados.get("itens", [])
    df = pd.DataFrame(itens)
    if df.empty:
        df = pd.DataFrame([{"aviso": "Nenhum impacto encontrado para o escopo selecionado."}])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="auditoria_impacto")
    buf.seek(0)
    nome = f"auditoria_impacto_{dados['ciclo'].replace('/', '_')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={nome}"},
    )


# ---------------------------------------------------------------------------
# GET /exportar  — Excel cópia de segurança do preenchimento
# ---------------------------------------------------------------------------
@router.get("/exportar")
def exportar(responsavel: str = None, nivel_responsavel: str = None,
             db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    try:
        from app.api.routers.rls_metas import escopo_usuario, clausula_rls
        ciclo  = get_current_cycle(db)
        meses  = get_working_window_months(db)
        escopo = escopo_usuario(u)
        rls    = clausula_rls(escopo, alias_cli="c")
        params = {"c": ciclo, "m": meses}
        params.update(rls["params"])
        if responsavel:
            if not escopo["ve_tudo"]:
                raise HTTPException(403, "Somente o Administrador pode selecionar outra alçada.")
            if nivel_responsavel not in ("Gerente", "Coordenador"):
                raise HTTPException(422, "nivel_responsavel deve ser Gerente ou Coordenador.")
            campo = "gerente_nome" if nivel_responsavel == "Gerente" else "supervisor_nome"
            rls = {"where": f"TRIM(c.{campo}) = :responsavel_alvo", "params": {"responsavel_alvo": responsavel.strip()}}
            params.update(rls["params"])
        filtro_rls = f"AND {rls['where']}" if not escopo["ve_tudo"] else ""
        if responsavel:
            filtro_rls = f"AND {rls['where']}"
        rows = db.execute(text(f"""
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
              {filtro_rls}
            GROUP BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome,
                     c.razaosocial, f.sku, p.descricao, f.mes_projetado
            ORDER BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome,
                     c.razaosocial, p.descricao, f.mes_projetado
        """), params).fetchall()

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
def dossie(sku: str, razao_social: str = None, vendedor_nome: str = None,
           coordenador_nome: str = None,
           db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    """
    Dossie do SKU para Metas Comercial, com escopo conforme o nivel de
    drill-down de onde o botao foi acionado:
    - nenhum filtro: nivel SKU agregado (Gerente na fase SKU, ou Coordenador
      antes de abrir um executivo) — restringe automaticamente ao escopo RLS
      de quem pediu (gerente_nome/supervisor_nome), nunca mostra a empresa
      inteira.
    - coordenador_nome: nivel Coordenador dentro do SKU (Gerente na fase
      COORDENADOR) — restringe aos CGCs daquele coordenador especifico.
    - vendedor_nome: nivel Executivo dentro do SKU (Coordenador na fase
      EXECUTIVO) — agrega todos os CGCs dos clientes daquele executivo.
    - razao_social + vendedor_nome: nivel Razao Social dentro do Executivo
      (Coordenador na fase RAZAO_SOCIAL) — filtra pelos CGCs daquela razao
      social atendidos por aquele executivo especifico.
    - razao_social sozinho: todos os CGCs daquela razao social (uso legado).
    """
    try:
        from app.api.routers.perfil_sku import montar_dossie
        from app.api.routers.rls_metas import escopo_usuario, clausula_rls
        ciclo = get_current_cycle(db)
        meses = get_working_window_months(db)
        desc  = db.execute(text("SELECT descricao FROM dim_produtos WHERE sku=:s"), {"s": sku}).scalar()

        cgcs_override = None

        if razao_social and vendedor_nome:
            # Nivel cliente dentro de um executivo: filtra pelos CGCs que pertencem
            # a essa razao_social E estao sob esse executivo.
            # Evita mostrar filiais do mesmo cliente atendidas por outros executivos.
            cgcs_rows = db.execute(text("""
                SELECT DISTINCT cgc FROM dim_clientes
                WHERE TRIM(COALESCE(razaosocial,'')) = :rz
                  AND TRIM(COALESCE(vendedor_nome,'')) = :vend
                  AND UPPER(TRIM(COALESCE(bloqueado,'ATIVO'))) != 'INATIVO'
            """), {"rz": razao_social.strip(), "vend": vendedor_nome.strip()}).fetchall()
            cgcs_override = [r[0] for r in cgcs_rows] or ["__SEM_CLIENTE__"]

        elif razao_social:
            # Nivel cliente sem filtro de executivo: todos os CGCs da razao social
            pass  # montar_dossie ja faz isso internamente via razao_social

        elif vendedor_nome:
            # Nivel executivo: busca todos os CGCs dos clientes do executivo
            # via dim_clientes (completo) E fato_vendas (histórico real)
            # União das duas fontes garante que não perde clientes sem plano no ciclo
            cgcs_rows = db.execute(text("""
                SELECT DISTINCT cgc FROM dim_clientes
                WHERE TRIM(COALESCE(vendedor_nome,'')) = :vend
                  AND UPPER(TRIM(COALESCE(bloqueado,'ATIVO'))) != 'INATIVO'
                UNION
                SELECT DISTINCT f.cgc
                FROM fato_ibp_granular f
                WHERE f.ciclo_sop = :ciclo
                  AND f.sku = :sku
                  AND COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR') = :vend
            """), {"ciclo": ciclo, "sku": sku, "vend": vendedor_nome}).fetchall()
            cgcs_override = [r[0] for r in cgcs_rows] or ["__SEM_CLIENTE__"]

        elif coordenador_nome:
            # Nivel coordenador dentro do SKU (drill-down do Gerente na fase
            # COORDENADOR): restringe aos CGCs daquele coordenador especifico,
            # nao a carteira inteira do gerente.
            cgcs_rows = db.execute(text("""
                SELECT DISTINCT cgc FROM dim_clientes
                WHERE TRIM(COALESCE(supervisor_nome,'')) = :coord
                  AND UPPER(TRIM(COALESCE(bloqueado,'ATIVO'))) != 'INATIVO'
                UNION
                SELECT DISTINCT f.cgc
                FROM fato_ibp_granular f
                JOIN dim_clientes c2 ON c2.cgc = f.cgc
                WHERE f.ciclo_sop = :ciclo
                  AND f.sku = :sku
                  AND COALESCE(NULLIF(TRIM(c2.supervisor_nome),''), 'SEM COORDENADOR') = :coord
            """), {"ciclo": ciclo, "sku": sku, "coord": coordenador_nome.strip()}).fetchall()
            cgcs_override = [r[0] for r in cgcs_rows] or ["__SEM_CLIENTE__"]

        else:
            # Nivel SKU agregado (fase SKU de Gerente ou Coordenador): SEM esse
            # filtro, o dossie mostraria a empresa inteira para qualquer um com
            # acesso a Metas — bug corrigido aqui. Restringe pelo mesmo campo/
            # valor de RLS (gerente_nome ou supervisor_nome) usado no /tabela.
            escopo = escopo_usuario(u)
            if not escopo["ve_tudo"]:
                rls = clausula_rls(escopo, alias_cli="c")
                cgcs_rows = db.execute(text(f"""
                    SELECT DISTINCT cgc FROM dim_clientes c
                    WHERE {rls['where']}
                      AND UPPER(TRIM(COALESCE(bloqueado,'ATIVO'))) != 'INATIVO'
                """), rls["params"]).fetchall()
                cgcs_override = [r[0] for r in cgcs_rows] or ["__SEM_CLIENTE__"]

        return montar_dossie(db, sku, ciclo, meses, descricao=desc,
                             coluna_meta="vol_meta",
                             razao_social=razao_social if not cgcs_override else None,
                             cgcs_override=cgcs_override)
    except Exception as e:
        raise HTTPException(500, repr(e))


# ---------------------------------------------------------------------------
# GET /consolidado  — visão gerencial somente leitura
# Compara vol_meta (coordenadores) vs vol_bottomup (Demanda Comercial) vs vol_ia
# por categoria → SKU, sem filtro de RLS (visão da empresa toda)
# ---------------------------------------------------------------------------
@router.get("/consolidado")
def consolidado(db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    try:
        if u.get("funcao") not in ("Administrador", "Gerente"):
            raise HTTPException(403, "Consolidado disponível somente para Gerente e Administrador.")
        from app.api.routers.rls_metas import escopo_usuario
        ciclo = get_current_cycle(db)
        escopo = escopo_usuario(u)
        meses = get_working_window_months(db)
        meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
        filtro, params = _filtro_escopo_sql(escopo, alias_cli="c")
        params.update({"ciclo": ciclo, "meses": meses})

        rows = db.execute(text(f"""
            SELECT
                COALESCE(p.categoria, 'SEM CATEGORIA') AS categoria,
                COALESCE(p.segmento,  'SEM SEGMENTO')  AS segmento,
                f.sku,
                COALESCE(p.descricao, 'SEM DESCRICAO') AS descricao,
                TO_CHAR(f.mes_projetado, 'YYYY-MM-DD')  AS mes,
                SUM(f.vol_meta)     AS meta,
                SUM(f.vol_bottomup) AS bu,
                SUM(f.vol_ia)       AS ia,
                COALESCE(
                    SUM(f.vol_meta * f.pmv_aplicado) / NULLIF(SUM(f.vol_meta), 0),
                    SUM(f.vol_ia   * f.pmv_aplicado) / NULLIF(SUM(f.vol_ia), 0),
                    0
                )                   AS pmv
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON c.cgc = f.cgc
            LEFT JOIN dim_produtos p ON p.sku = f.sku
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = ANY(:meses)
              {filtro}
            GROUP BY p.categoria, p.segmento, f.sku, p.descricao, f.mes_projetado
            ORDER BY p.categoria, p.segmento, p.descricao, f.mes_projetado
        """), params).fetchall()

        impactos = db.execute(text(f"""
            SELECT
                COALESCE(NULLIF(TRIM(c.supervisor_nome),''), 'SEM COORDENADOR') AS coordenador,
                TRIM(f.sku) AS sku,
                COALESCE(SUM(f.vol_meta), 0) AS meta,
                COALESCE(SUM(f.vol_bottomup), 0) AS bu,
                COALESCE(SUM(f.vol_meta * f.pmv_aplicado), 0) AS meta_rs,
                COALESCE(SUM(f.vol_bottomup * f.pmv_aplicado), 0) AS bu_rs
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON c.cgc = f.cgc
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = ANY(:meses)
              {filtro}
            GROUP BY coordenador, f.sku
            ORDER BY f.sku, ABS(COALESCE(SUM(f.vol_meta * f.pmv_aplicado), 0) - COALESCE(SUM(f.vol_bottomup * f.pmv_aplicado), 0)) DESC
        """), params).fetchall()
        impactos_por_sku: dict = {}
        for r in impactos:
            impactos_por_sku.setdefault(r.sku, []).append({
                "coordenador": r.coordenador,
                "meta": int(r.meta or 0),
                "bu": int(r.bu or 0),
                "delta_cx": int((r.meta or 0) - (r.bu or 0)),
                "meta_rs": round(float(r.meta_rs or 0), 2),
                "bu_rs": round(float(r.bu_rs or 0), 2),
                "delta_rs": round(float((r.meta_rs or 0) - (r.bu_rs or 0)), 2),
            })

        # Monta árvore categoria → SKUs (segmento oculto na visualização)
        tree: dict = {}
        for r in rows:
            cat = tree.setdefault(r.categoria, {"nome": r.categoria, "skus": {}})
            sk  = cat["skus"].setdefault(r.sku, {
                "sku": r.sku, "descricao": r.descricao, "meses": {}
            })
            meta = int(r.meta or 0)
            bu   = int(r.bu   or 0)
            ia   = int(r.ia   or 0)
            pmv  = round(float(r.pmv or 0), 2)
            delta_cx  = meta - bu
            delta_pct = round(delta_cx / bu * 100, 1) if bu > 0 else None
            sk["meses"][r.mes] = {
                "meta":      meta,
                "bu":        bu,
                "ia":        ia,
                "pmv":       pmv,
                "delta_cx":  delta_cx,
                "delta_pct": delta_pct,
            }
            sk["impactos_coordenadores"] = impactos_por_sku.get(r.sku, [])

        categorias = [
            {
                "nome": cat["nome"],
                "skus": sorted(cat["skus"].values(), key=lambda s: s["descricao"])
            }
            for cat in sorted(tree.values(), key=lambda c: c["nome"])
        ]

        return {
            "ciclo":      ciclo,
            "meses":      meses_iso,
            "sou_admin":  u.get("funcao") == "Administrador",
            "pode_aprovar": u.get("funcao") == "Administrador",
            "coordenadores_pendentes": _coordenadores_pendentes(db, ciclo, escopo),
            "categorias": categorias,
        }
    except Exception as e:
        raise HTTPException(500, repr(e))



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


@router.post("/aprovar-evidencia")
def aprovar_evidencia(payload: PayloadAcaoMetas = PayloadAcaoMetas(ajustes=[]),
                      db: Session = Depends(get_db), u: dict = Depends(require_metas)):
    try:
        ciclo = get_current_cycle(db)
        if u.get("funcao") != "Administrador":
            raise HTTPException(403, "Somente o Administrador aprova e congela metas.")
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP):
            raise HTTPException(423, "Demanda Comercial ainda nao congelou. Aguarde o bastao.")
        if etapa_congelada(db, ciclo, ETAPA_METAS):
            raise HTTPException(423, "Metas Comercial já está aprovada/congelada.")

        escopo_acao, _ = _escopo_efetivo_para_acao(u, payload.nome_alvo, payload.nivel_alvo)
        if payload.ajustes:
            _aplicar_ajustes_meta(db, ciclo, payload.ajustes, escopo_acao)
        pendentes = _coordenadores_pendentes(db, ciclo, escopo_acao)
        if pendentes:
            raise HTTPException(422, {
                "mensagem": "Ainda existem coordenadores sem trancar as metas.",
                "coordenadores_pendentes": pendentes,
            })

        congelar_etapa(db, ciclo, ETAPA_METAS)
        propagar_para_jusante(db, ciclo, ETAPA_METAS)
        db.commit()
        nome = f"metas_aprovadas_{ciclo.replace('/','_')}_{(escopo_acao.get('nome_responsavel') or 'admin').replace(' ','_')}.xlsx"
        return _gerar_evidencia_metas(db, ciclo, escopo_acao, nome)
    except HTTPException:
        db.rollback(); raise
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
        ciclo  = get_current_cycle(db)
        escopo = u.get("funcao", "")
        is_admin = escopo == "Administrador"
        gerente_nome = u.get("gerente_nome") or u.get("nome") or ""

        # Cadeados ativos
        rows = db.execute(text("""
            SELECT nome_responsavel AS nome, nivel, congelado_por AS por,
                   data_congelamento AS quando
            FROM controle_metas_responsavel
            WHERE ciclo_sop=:c AND status='CONGELADO'
            ORDER BY data_congelamento
        """), {"c": ciclo}).fetchall()
        cadeados_ativos = {r.nome for r in rows}

        # Todos os coordenadores sob controle — Admin vê todos, Gerente vê os seus
        if is_admin:
            coord_rows = db.execute(text("""
                SELECT DISTINCT TRIM(supervisor_nome) AS coordenador
                FROM dim_clientes
                WHERE supervisor_nome IS NOT NULL AND TRIM(supervisor_nome) != ''
                ORDER BY 1
            """)).fetchall()
        else:
            coord_rows = db.execute(text("""
                SELECT DISTINCT TRIM(supervisor_nome) AS coordenador
                FROM dim_clientes
                WHERE TRIM(gerente_nome) = :g
                  AND supervisor_nome IS NOT NULL AND TRIM(supervisor_nome) != ''
                ORDER BY 1
            """), {"g": gerente_nome}).fetchall()

        coordenadores = [
            {
                "nome":      r.coordenador,
                "bloqueado": r.coordenador in cadeados_ativos,
                "por":       next((c.por for c in rows if c.nome == r.coordenador), None),
            }
            for r in coord_rows
        ]

        return {
            "sou_admin":    is_admin,
            "meu_nome":     gerente_nome,
            "cadeados":     [{"nome": r.nome, "nivel": r.nivel, "por": r.por} for r in rows],
            "coordenadores": coordenadores,
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
