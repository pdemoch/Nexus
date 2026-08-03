"""
=====================================================================
ROUTER — METAS COMERCIAL  [reconstrução]
=====================================================================
Terceira etapa do bastão. Cascata comercial: gerente -> coordenador ->
vendedor -> razão social -> SKU. Cada nível ajusta e BLOQUEIA sua carteira;
o congelamento da ETAPA inteira é do Administrador. Acesso: Admin, Gerente,
Coordenador (nível interno).

Diferenças das outras telas:
  • Hierarquia de 5 níveis, com RLS: cada um só vê/edita sua carteira.
  • Cadeado POR RESPONSÁVEL (rls_metas.py) além do congelamento por Admin.
  • Cliente é RAZÃO SOCIAL na exibição (vários CNPJs por razão); grava por CNPJ.
  • Grava vol_meta. Peso de rateio: vol_bottomup. Requer BottomUP congelado.

Reusa rls_metas.py como fundação de segurança.
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
    ratear_maior_resto, propagar_para_jusante, congelar_etapa, reabrir_etapa,
    etapa_congelada, registrar_log_auditoria, parse_date_safe,
    check_imutabilidade_mes, ETAPA_BOTTOMUP, ETAPA_METAS,
)
from app.api.routers.rls_metas import (
    escopo_usuario, exigir_acesso_metas, clausula_rls, validar_escrita,
    garantir_tabela_cadeados, cadeado_do_responsavel, esta_congelado_para_usuario,
    travar_cadeado, reabrir_cadeado, etapa_metas_bloqueada,
)

router = APIRouter(prefix="/api/v1/metas", tags=["Metas Comercial"])


def require_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") != "Administrador":
        raise HTTPException(403, "Somente o Administrador congela a etapa.")
    return usuario


class AjusteMeta(BaseModel):
    razao_social: str
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadSalvar(BaseModel):
    ajustes: List[AjusteMeta]

class PayloadCadeado(BaseModel):
    nome_alvo: Optional[str] = None
    nivel_alvo: Optional[str] = None


@router.get("/status")
def status(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    garantir_tabela_cadeados(db)
    return {
        "ciclo": ciclo,
        "etapa_congelada": etapa_congelada(db, ciclo, ETAPA_METAS),
        "bottomup_liberado": etapa_congelada(db, ciclo, ETAPA_BOTTOMUP),
        "minha_carteira_congelada": esta_congelado_para_usuario(db, escopo, ciclo),
        "sou_admin": escopo["ve_tudo"],
        "nome_responsavel": escopo["nome_responsavel"],
    }


@router.get("/tabela")
def tabela(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """
    Árvore gerente->coordenador->vendedor->razão social->SKU, filtrada pelo RLS.
    Cliente agregado por RAZÃO SOCIAL. vol_bottomup herdado + vol_meta editável.
    """
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    meses_iso = [m.strftime("%Y-%m-%d") for m in meses]
    garantir_tabela_cadeados(db)
    rls = clausula_rls(escopo, alias_cli="c")

    rows = db.execute(text(f"""
        SELECT
            COALESCE(NULLIF(TRIM(c.gerente_nome),''),'SEM GERENTE')   AS gerente,
            COALESCE(NULLIF(TRIM(c.supervisor_nome),''),'SEM COORD')  AS coordenador,
            COALESCE(NULLIF(TRIM(f.vendedor_nome),''),'SEM VENDEDOR') AS vendedor,
            COALESCE(NULLIF(TRIM(c.razaosocial),''),'SEM RAZAO')      AS razao_social,
            f.sku,
            COALESCE(p.descricao,'SEM DESCRICAO')                     AS descricao,
            TO_CHAR(f.mes_projetado,'YYYY-MM-DD')                     AS mes,
            SUM(f.vol_bottomup)                                       AS bottomup,
            SUM(f.vol_meta)                                           AS meta,
            AVG(f.pmv_aplicado)                                       AS pmv
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = ANY(:meses)
          AND ({rls['where']})
        GROUP BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome,
                 c.razaosocial, f.sku, p.descricao, f.mes_projetado
    """), {"ciclo": ciclo, "meses": meses, **rls["params"]}).fetchall()

    tree: dict = {}
    tot_vol = {mi: 0 for mi in meses_iso}
    tot_rs = {mi: 0.0 for mi in meses_iso}

    for r in rows:
        g = tree.setdefault(r.gerente, {"nome": r.gerente, "tipo": "gerente", "filhos": {}})
        c = g["filhos"].setdefault(r.coordenador, {"nome": r.coordenador, "tipo": "coordenador", "filhos": {}})
        v = c["filhos"].setdefault(r.vendedor, {"nome": r.vendedor, "tipo": "vendedor", "filhos": {}})
        rz = v["filhos"].setdefault(r.razao_social, {"nome": r.razao_social, "tipo": "cliente", "filhos": {}})
        sk = rz["filhos"].setdefault(r.sku, {"sku": r.sku, "descricao": r.descricao, "tipo": "produto", "meses": {}})
        meta = int(r.meta or 0); bu = int(r.bottomup or 0); pmv = float(r.pmv or 0)
        sk["meses"][r.mes] = {"bottomup": bu, "meta": meta, "pmv": round(pmv, 2), "receita": round(meta * pmv, 2)}
        tot_vol[r.mes] += meta
        tot_rs[r.mes] += meta * pmv

    def serial(node):
        if node.get("tipo") == "produto":
            return node
        filhos = [serial(f) for f in node["filhos"].values()]
        filhos.sort(key=lambda x: x.get("descricao") or x.get("nome") or "")
        base = {k: node[k] for k in node if k != "filhos"}
        base["subRows"] = filhos
        return base

    arvore = [serial(g) for g in tree.values()]
    arvore.sort(key=lambda x: x["nome"])

    return {
        "ciclo": ciclo,
        "etapa_congelada": etapa_congelada(db, ciclo, ETAPA_METAS),
        "minha_carteira_congelada": esta_congelado_para_usuario(db, escopo, ciclo),
        "sou_admin": escopo["ve_tudo"],
        "meses": meses_iso,
        "arvore": arvore,
        "totais": {
            "volume": {mi: tot_vol[mi] for mi in meses_iso},
            "faturamento": {mi: round(tot_rs[mi], 2) for mi in meses_iso},
        },
    }


@router.get("/dossie")
def dossie(sku: str, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    exigir_acesso_metas(usuario)
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
           usuario: dict = Depends(get_current_user)):
    """
    Grava vol_meta rateado por CNPJ dentro da razão social. Valida (rls_metas)
    que TODAS as linhas pertencem à carteira do usuário. Peso vol_bottomup.
    """
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    try:
        if etapa_congelada(db, ciclo, ETAPA_METAS):
            raise HTTPException(423, "Etapa de Metas já congelada pelo Administrador.")
        if not etapa_congelada(db, ciclo, ETAPA_BOTTOMUP):
            raise HTTPException(423, "Aguardando a Gerência liberar o Bottom-Up.")
        if esta_congelado_para_usuario(db, escopo, ciclo):
            raise HTTPException(423, "Sua carteira já foi bloqueada.")

        nome_user = usuario.get("nome", usuario.get("email", "?"))
        total = 0

        for aj in payload.ajustes:
            data_alvo = parse_date_safe(
                aj.mes_projetado if len(aj.mes_projetado) > 7 else aj.mes_projetado + "-01")
            check_imutabilidade_mes(data_alvo, aj.sku, contexto="Meta")

            linhas = db.execute(text("""
                SELECT f.id, f.vol_bottomup, f.vol_meta
                FROM fato_ibp_granular f
                JOIN dim_clientes c ON f.cgc = c.cgc
                WHERE f.ciclo_sop = :c AND f.sku = :s AND f.mes_projetado = :m
                  AND TRIM(c.razaosocial) = TRIM(:rz)
                ORDER BY f.id
            """), {"c": ciclo, "s": aj.sku, "m": data_alvo, "rz": aj.razao_social}).fetchall()
            if not linhas:
                continue

            ids = [l.id for l in linhas]
            validar_escrita(db, escopo, ids, ciclo)

            pesos = [max(0.0, float(l.vol_bottomup or 0)) for l in linhas]
            partes = ratear_maior_resto(int(aj.novo_volume), pesos)
            if sum(partes) != int(aj.novo_volume):
                raise HTTPException(500, f"Falha de balanco na meta {aj.razao_social}/{aj.sku}.")

            for l, parte in zip(linhas, partes):
                if int(l.vol_meta or 0) != int(parte):
                    db.execute(text("UPDATE fato_ibp_granular SET vol_meta=:v WHERE id=:id"),
                               {"v": int(parte), "id": l.id})
                    total += 1

            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="Metas Comercial", usuario=nome_user,
                sku=aj.sku, cliente=aj.razao_social, mes=data_alvo,
                v_antigo=0, v_novo=int(aj.novo_volume))

        db.commit()
        return {"status": "success", "linhas": total}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.get("/cadeados")
def cadeados(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    garantir_tabela_cadeados(db)
    rows = db.execute(text("""
        SELECT nome_responsavel, nivel, status, congelado_por
        FROM controle_metas_responsavel WHERE ciclo_sop = :c
        ORDER BY nivel, nome_responsavel
    """), {"c": ciclo}).fetchall()
    return {
        "meu_nome": escopo["nome_responsavel"], "sou_admin": escopo["ve_tudo"],
        "cadeados": [{"nome": r.nome_responsavel, "nivel": r.nivel, "status": r.status,
                      "por": r.congelado_por} for r in rows],
    }


@router.post("/bloquear")
def bloquear(payload: PayloadCadeado, db: Session = Depends(get_db),
             usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    try:
        nome = travar_cadeado(db, escopo, ciclo, payload.nome_alvo, payload.nivel_alvo)
        return {"status": "success", "bloqueado": nome}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir-cadeado")
def reabrir_cad(payload: PayloadCadeado, db: Session = Depends(get_db),
                usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    if not payload.nome_alvo:
        raise HTTPException(400, "Informe o responsavel a reabrir.")
    pertence = False
    if not escopo["ve_tudo"] and escopo["campo_rls"] == "gerente_nome":
        r = db.execute(text("""
            SELECT 1 FROM dim_clientes
            WHERE TRIM(gerente_nome) = :g AND TRIM(supervisor_nome) = :alvo LIMIT 1
        """), {"g": escopo["valor_rls"], "alvo": payload.nome_alvo.strip()}).fetchone()
        pertence = r is not None
    try:
        reabrir_cadeado(db, escopo, ciclo, payload.nome_alvo, pertence)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/congelar")
def congelar(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        congelar_etapa(db, ciclo, ETAPA_METAS)
        res = propagar_para_jusante(db, ciclo, ETAPA_METAS)
        db.commit()
        return {"status": "success", "propagou": res["propagou"], "preservou": res["preservou"]}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.post("/reabrir")
def reabrir_etapa_metas(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    try:
        ciclo = get_current_cycle(db)
        reabrir_etapa(db, ciclo, ETAPA_METAS)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))


@router.get("/exportar")
def exportar(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    import io
    from fastapi.responses import StreamingResponse
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    meses = get_working_window_months(db)
    rls = clausula_rls(escopo, alias_cli="c")
    rows = db.execute(text(f"""
        SELECT c.gerente_nome, c.supervisor_nome, f.vendedor_nome, c.razaosocial,
               f.sku, COALESCE(p.descricao,'') AS descricao,
               TO_CHAR(f.mes_projetado,'MM/YYYY') AS mes,
               SUM(f.vol_meta) AS meta, AVG(f.pmv_aplicado) AS pmv
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c AND f.mes_projetado = ANY(:meses) AND ({rls['where']})
        GROUP BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome, c.razaosocial,
                 f.sku, p.descricao, f.mes_projetado
        ORDER BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome, c.razaosocial, f.sku
    """), {"c": ciclo, "meses": meses, **rls["params"]}).fetchall()

    def _num(v): return f"{float(v or 0):.2f}".replace(".", ",")
    linhas = ["Gerente;Coordenador;Vendedor;Razao Social;SKU;Descricao;Mes;Meta (cx);PMV;Receita (R$)"]
    for r in rows:
        meta = int(r.meta or 0); pmv = float(r.pmv or 0)
        linhas.append(";".join([r.gerente_nome or "", r.supervisor_nome or "", r.vendedor_nome or "",
            r.razaosocial or "", r.sku, r.descricao, r.mes, str(meta), _num(pmv), _num(meta * pmv)]))
    buffer = io.StringIO("\r\n".join(linhas))
    resp = StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="metas_comercial_{ciclo.replace("/","_")}.csv"'
    return resp