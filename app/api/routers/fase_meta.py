"""
=====================================================================
FASE DA META COMERCIAL (v2) — SKU primeiro, distribuição percentual
=====================================================================
Construção do zero (não reaproveita a antiga cascata SKU->Executivo->
Razão Social do Coordenador nem a fase única do Gerente).

Fluxo:
  Gerente:     SKU -> COORDENADOR
    - Fase SKU: define R$/caixas por SKU para toda a carteira (comparado
      com o vol_bottomup vindo da Demanda Comercial).
    - Fase COORDENADOR: dentro de cada SKU, distribui percentualmente
      (valor e volume) entre os Coordenadores da sua gerência. Ao travar
      esta fase, o sistema registra a meta financeira de cada Coordenador
      (metas_financeiras_responsavel) automaticamente — dispensa o botão
      manual "passar para coordenadores".

  Coordenador: EXECUTIVO -> RAZAO_SOCIAL
    - Fase EXECUTIVO: dentro de cada SKU (já definido/travado pelo
      Gerente), distribui percentualmente entre os Executivos da sua
      carteira. O teto (100%) é o valor daquele SKU vindo do Gerente.
    - Fase RAZAO_SOCIAL: dentro de um SKU x Executivo, distribui entre
      as Razões Sociais daquele executivo NAQUELE SKU (o teto é o valor
      do executivo só naquele SKU, não o total geral dele). Ao travar
      esta fase, grava o cadeado final (controle_metas_responsavel, já
      existente) — mantém compatibilidade com o congelamento geral de
      etapa (Admin).

Princípio de persistência: SEM tabela nova de valores. Tudo continua
sendo uma agregação em memória de fato_ibp_granular.vol_meta (grão
CNPJ) — só o ESTADO da máquina (em que fase cada responsável está) é
persistido, nesta tabela de controle.
"""

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException

from app.api.routers.rls_metas import NIVEL_GERENTE, NIVEL_COORDENADOR

FASE_SKU = "SKU"
FASE_COORDENADOR = "COORDENADOR"
FASE_EXECUTIVO = "EXECUTIVO"
FASE_RAZAO_SOCIAL = "RAZAO_SOCIAL"

ORDEM_FASES_GERENTE = [FASE_SKU, FASE_COORDENADOR]
ORDEM_FASES_COORDENADOR = [FASE_EXECUTIVO, FASE_RAZAO_SOCIAL]

# Tolerância de fechamento (soma das partes vs. 100% do teto) ao travar
# uma fase de distribuição percentual.
TOLERANCIA_FECHAMENTO_FASE = 0.01

DDL_FASE_META = """
CREATE TABLE IF NOT EXISTS controle_fase_meta (
    id                  SERIAL PRIMARY KEY,
    ciclo_sop           VARCHAR(7)   NOT NULL,
    nome_responsavel    VARCHAR(120) NOT NULL,
    nivel               VARCHAR(20)  NOT NULL,   -- 'Gerente' | 'Coordenador'
    fase_atual          VARCHAR(30)  NOT NULL DEFAULT 'SKU',
    sku_travado         BOOLEAN      NOT NULL DEFAULT FALSE,
    coordenador_travado BOOLEAN      NOT NULL DEFAULT FALSE,
    executivo_travado   BOOLEAN      NOT NULL DEFAULT FALSE,
    snapshot_sku        JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- {"sku|mes": total_volume} tirado ao travar a fase SKU (Gerente) — teto p/ validar % dos Coordenadores
    snapshot_executivo  JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- {"sku|mes|executivo": total_volume} tirado ao travar a fase EXECUTIVO (Coordenador) — teto p/ validar % das Razões Sociais
    atualizado_por      VARCHAR(120),
    atualizado_em       TIMESTAMP    DEFAULT NOW(),
    UNIQUE (ciclo_sop, nome_responsavel)
);
CREATE INDEX IF NOT EXISTS ix_fase_meta_ciclo ON controle_fase_meta (ciclo_sop);
"""


def garantir_tabela_fase_meta(db: Session) -> None:
    for stmt in DDL_FASE_META.strip().split(";"):
        s = stmt.strip()
        if s:
            db.execute(text(s))
    db.commit()


def fase_inicial(funcao: str) -> str:
    return FASE_SKU if funcao == NIVEL_GERENTE else FASE_EXECUTIVO


def fase_atual_do_responsavel(db: Session, ciclo: str, nome_responsavel: str, funcao: str) -> Dict[str, Any]:
    """
    Estado de fase de um responsável. Sem linha ainda -> estado inicial
    (nada travado), sem gravar nada até a primeira trava efetiva.
    """
    row = db.execute(text("""
        SELECT fase_atual, sku_travado, coordenador_travado, executivo_travado,
               snapshot_sku, snapshot_executivo
        FROM controle_fase_meta
        WHERE ciclo_sop = :c AND TRIM(nome_responsavel) = :n
    """), {"c": ciclo, "n": nome_responsavel.strip()}).fetchone()
    if not row:
        return {
            "fase_atual": fase_inicial(funcao),
            "sku_travado": False,
            "coordenador_travado": False,
            "executivo_travado": False,
            "snapshot_sku": {},
            "snapshot_executivo": {},
        }
    return {
        "fase_atual": row.fase_atual,
        "sku_travado": row.sku_travado,
        "coordenador_travado": row.coordenador_travado,
        "executivo_travado": row.executivo_travado,
        "snapshot_sku": row.snapshot_sku or {},
        "snapshot_executivo": row.snapshot_executivo or {},
    }


def _gravar_estado(db: Session, ciclo: str, nome: str, funcao: str, estado: Dict[str, Any]) -> None:
    import json
    db.execute(text("""
        INSERT INTO controle_fase_meta
            (ciclo_sop, nome_responsavel, nivel, fase_atual, sku_travado,
             coordenador_travado, executivo_travado, snapshot_sku, snapshot_executivo,
             atualizado_por)
        VALUES (:c, :n, :nv, :fa, :st, :ct, :et, CAST(:snk AS JSONB), CAST(:sne AS JSONB), :por)
        ON CONFLICT (ciclo_sop, nome_responsavel)
        DO UPDATE SET fase_atual=:fa, sku_travado=:st, coordenador_travado=:ct,
                      executivo_travado=:et, snapshot_sku=CAST(:snk AS JSONB),
                      snapshot_executivo=CAST(:sne AS JSONB),
                      atualizado_por=:por, atualizado_em=NOW()
    """), {
        "c": ciclo, "n": nome, "nv": funcao,
        "fa": estado["fase_atual"], "st": estado["sku_travado"],
        "ct": estado["coordenador_travado"], "et": estado["executivo_travado"],
        "snk": json.dumps(estado.get("snapshot_sku") or {}),
        "sne": json.dumps(estado.get("snapshot_executivo") or {}),
        "por": nome,
    })


def _somar_teto_sku(db: Session, ciclo: str, escopo: Dict[str, Any]) -> Dict[str, int]:
    """Volume total por (sku|mes) dentro do escopo do responsável — usado como
    snapshot ao travar a fase SKU do Gerente."""
    from app.api.routers.rls_metas import clausula_rls
    rls = clausula_rls(escopo, alias_cli="c")
    rows = db.execute(text(f"""
        SELECT f.sku, TO_CHAR(f.mes_projetado,'YYYY-MM') AS mes,
               COALESCE(SUM(f.vol_meta), 0) AS total
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        WHERE f.ciclo_sop = :ciclo AND {rls['where']}
        GROUP BY f.sku, mes
    """), {"ciclo": ciclo, **rls["params"]}).fetchall()
    return {f"{r.sku}|{r.mes}": int(r.total or 0) for r in rows}


def _somar_teto_executivo(db: Session, ciclo: str, escopo: Dict[str, Any]) -> Dict[str, int]:
    """Volume total por (sku|mes|executivo) dentro do escopo do responsável —
    usado como snapshot ao travar a fase EXECUTIVO do Coordenador."""
    from app.api.routers.rls_metas import clausula_rls
    rls = clausula_rls(escopo, alias_cli="c")
    rows = db.execute(text(f"""
        SELECT f.sku, TO_CHAR(f.mes_projetado,'YYYY-MM') AS mes,
               COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR') AS executivo,
               COALESCE(SUM(f.vol_meta), 0) AS total
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        WHERE f.ciclo_sop = :ciclo AND {rls['where']}
        GROUP BY f.sku, mes, executivo
    """), {"ciclo": ciclo, **rls["params"]}).fetchall()
    return {f"{r.sku}|{r.mes}|{r.executivo}": int(r.total or 0) for r in rows}


def validar_tolerancia_antes_de_travar(db: Session, ciclo: str, escopo: Dict[str, Any], funcao: str,
                                        fase_a_travar: str) -> list[dict]:
    """
    Compara o total atual (vol_meta, live) contra o teto oficial que serve
    de 100% para a fase que está sendo travada:
      - Gerente travando COORDENADOR -> teto = snapshot_sku (tirado ao travar SKU).
      - Coordenador travando EXECUTIVO -> teto = snapshot_sku do GERENTE da sua
        gerência (o que ele passou para os coordenadores).
      - Coordenador travando RAZAO_SOCIAL -> teto = snapshot_executivo (tirado
        ao travar EXECUTIVO).
    Retorna a lista de (sku[,executivo]) fora da tolerância de ±1%.
    """
    nome = (escopo["nome_responsavel"] or "").strip()
    estado = fase_atual_do_responsavel(db, ciclo, nome, funcao)

    if funcao == NIVEL_GERENTE and fase_a_travar == FASE_COORDENADOR:
        teto = estado.get("snapshot_sku") or {}
        atual = _somar_teto_sku(db, ciclo, escopo)
    elif funcao == NIVEL_COORDENADOR and fase_a_travar == FASE_EXECUTIVO:
        # teto vem do gerente da gerência deste coordenador
        gerente = db.execute(text("""
            SELECT TRIM(gerente_nome) AS g FROM dim_clientes
            WHERE TRIM(supervisor_nome) = :n AND TRIM(gerente_nome) IS NOT NULL AND TRIM(gerente_nome) != ''
            LIMIT 1
        """), {"n": nome}).fetchone()
        teto = {}
        if gerente and gerente.g:
            estado_gerente = fase_atual_do_responsavel(db, ciclo, gerente.g, NIVEL_GERENTE)
            teto = estado_gerente.get("snapshot_sku") or {}
        atual = _somar_teto_sku(db, ciclo, escopo)
    elif funcao == NIVEL_COORDENADOR and fase_a_travar == FASE_RAZAO_SOCIAL:
        teto = estado.get("snapshot_executivo") or {}
        atual = _somar_teto_executivo(db, ciclo, escopo)
    else:
        return []

    fora = []
    for chave, valor_teto in teto.items():
        valor_atual = atual.get(chave, 0)
        if valor_teto <= 0:
            continue
        variacao = (valor_atual - valor_teto) / valor_teto
        if abs(variacao) > TOLERANCIA_FECHAMENTO_FASE:
            fora.append({
                "chave": chave,
                "volume_atual": valor_atual,
                "volume_alvo": valor_teto,
                "variacao_pct": round(variacao * 100, 2),
            })
    return fora


def avancar_fase_meta(db: Session, escopo: Dict[str, Any], ciclo: str) -> Dict[str, Any]:
    """
    Trava a fase corrente do responsável logado e avança para a próxima.
    O cálculo/gravação do rateio em si acontece ANTES desta chamada, no
    router (POST /distribuir). Esta função só governa o estado e tira o
    snapshot do teto que a próxima fase vai usar como 100%.
    """
    if escopo["ve_tudo"]:
        raise HTTPException(status_code=400, detail="Admin não avança fase própria — use reabertura administrativa.")

    nome = (escopo["nome_responsavel"] or "").strip()
    funcao = escopo["funcao"]
    if not nome:
        raise HTTPException(status_code=403, detail="Escopo inválido para avançar fase.")

    garantir_tabela_fase_meta(db)
    estado = fase_atual_do_responsavel(db, ciclo, nome, funcao)
    fase = estado["fase_atual"]

    if funcao == NIVEL_GERENTE:
        idx = ORDEM_FASES_GERENTE.index(fase) if fase in ORDEM_FASES_GERENTE else 0
        if idx >= len(ORDEM_FASES_GERENTE) - 1:
            raise HTTPException(status_code=400, detail="Carteira já está na última fase (Distribuição por Coordenador).")
        proxima = ORDEM_FASES_GERENTE[idx + 1]
        novo_estado = {
            "fase_atual": proxima,
            "sku_travado": True,
            "coordenador_travado": False,
            "executivo_travado": False,
            "snapshot_sku": _somar_teto_sku(db, ciclo, escopo),
            "snapshot_executivo": estado.get("snapshot_executivo") or {},
        }
    else:  # Coordenador
        idx = ORDEM_FASES_COORDENADOR.index(fase) if fase in ORDEM_FASES_COORDENADOR else 0
        if idx >= len(ORDEM_FASES_COORDENADOR) - 1:
            raise HTTPException(status_code=400, detail="Carteira já está na última fase (Razão Social).")
        proxima = ORDEM_FASES_COORDENADOR[idx + 1]
        novo_estado = {
            "fase_atual": proxima,
            "sku_travado": True,
            "coordenador_travado": False,
            "executivo_travado": True,
            "snapshot_sku": estado.get("snapshot_sku") or {},
            "snapshot_executivo": _somar_teto_executivo(db, ciclo, escopo),
        }

    _gravar_estado(db, ciclo, nome, funcao, novo_estado)
    db.commit()
    return novo_estado


def travar_fase_final(db: Session, ciclo: str, nome: str, funcao: str) -> Dict[str, Any]:
    """
    Marca a ÚLTIMA fase do responsável como travada, sem avançar (não há
    próxima fase). Usado quando ele conclui COORDENADOR (Gerente) ou
    RAZAO_SOCIAL (Coordenador).
    """
    garantir_tabela_fase_meta(db)
    estado = fase_atual_do_responsavel(db, ciclo, nome, funcao)
    if funcao == NIVEL_GERENTE:
        if estado["fase_atual"] != FASE_COORDENADOR:
            raise HTTPException(status_code=400, detail="Gerente precisa estar na fase de Distribuição por Coordenador.")
        novo_estado = {**estado, "coordenador_travado": True}
    else:
        if estado["fase_atual"] != FASE_RAZAO_SOCIAL:
            raise HTTPException(status_code=400, detail="Coordenador precisa estar na fase Razão Social.")
        novo_estado = {**estado, "executivo_travado": True}
    _gravar_estado(db, ciclo, nome, funcao, novo_estado)
    db.commit()
    return novo_estado


def reabrir_fase_meta(db: Session, escopo: Dict[str, Any], ciclo: str, nome_alvo: str,
                       nivel_alvo: Optional[str] = None) -> None:
    """
    Reabre a carteira de um responsável de volta para a fase inicial.
    Somente Administrador. Ao reabrir um Gerente, os Coordenadores da sua
    gerência também retornam à fase inicial (recomeçam a cascata).
    """
    if not escopo["ve_tudo"]:
        raise HTTPException(status_code=403, detail="Somente o Administrador pode reabrir fases.")
    alvo = nome_alvo.strip()
    nivel = nivel_alvo or NIVEL_COORDENADOR
    if nivel not in (NIVEL_GERENTE, NIVEL_COORDENADOR):
        raise HTTPException(status_code=422, detail="nivel_alvo deve ser Gerente ou Coordenador.")

    garantir_tabela_fase_meta(db)
    estado_inicial = {
        "fase_atual": fase_inicial(nivel),
        "sku_travado": False, "coordenador_travado": False, "executivo_travado": False,
        "snapshot_sku": {}, "snapshot_executivo": {},
    }
    _gravar_estado(db, ciclo, alvo, nivel, estado_inicial)

    if nivel == NIVEL_GERENTE:
        db.execute(text("""
            UPDATE controle_fase_meta cfm
            SET fase_atual='EXECUTIVO', sku_travado=FALSE, coordenador_travado=FALSE,
                executivo_travado=FALSE, snapshot_sku='{}'::jsonb, snapshot_executivo='{}'::jsonb,
                atualizado_por=:por, atualizado_em=NOW()
            WHERE ciclo_sop = :c
              AND nivel = 'Coordenador'
              AND EXISTS (
                  SELECT 1 FROM dim_clientes dc
                  WHERE TRIM(dc.gerente_nome) = :g
                    AND TRIM(dc.supervisor_nome) = TRIM(cfm.nome_responsavel)
              )
        """), {"c": ciclo, "g": alvo, "por": escopo["nome_responsavel"] or "ADMIN"})
    db.commit()
