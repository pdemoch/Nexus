"""
=====================================================================
FUNDAÇÃO DE RLS E TRAVAS — ETAPA CONSENSO / METAS
=====================================================================
Este módulo concentra TODA a lógica sensível de segurança da etapa de
Metas (Consenso), isolada para ser testável sem o router.

Princípios:
  1. O escopo (o que cada usuário vê e pode tocar) é derivado do RETORNO
     de get_current_user — que repovoa funcao/gerente_nome/supervisor_nome
     do BANCO a cada requisição. Nunca do payload do token (falsificável).
  2. RLS não é só filtro de leitura: é trava de ESCRITA. Toda alteração é
     validada linha a linha contra o escopo do usuário no momento do salvar.
  3. Cadeados respeitam PRECEDÊNCIA HIERÁRQUICA: superior sempre pode
     reabrir/editar; par nunca toca o cadeado do outro.

Hierarquia de dados (fato_ibp_granular + dim_clientes):
    Gerente (gerente_nome) > Coordenador (supervisor_nome) > vendedor_nome > cliente > sku
Acesso ao sistema: apenas Administrador, Gerente, Coordenador.
(Vendedor/Executivo NÃO loga — é só dimensão de agrupamento nos dados.)
"""

from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException


# =====================================================================
# NÍVEIS E CONSTANTES
# =====================================================================
NIVEL_ADMIN = "Administrador"
NIVEL_GERENTE = "Gerente"
NIVEL_COORDENADOR = "Coordenador"

# Funções que efetivamente acessam a etapa de Metas.
FUNCOES_COM_ACESSO = {NIVEL_ADMIN, NIVEL_GERENTE, NIVEL_COORDENADOR}

# Precedência: quanto maior o número, mais alto na hierarquia.
_PESO_HIERARQUIA = {
    NIVEL_COORDENADOR: 1,
    NIVEL_GERENTE: 2,
    NIVEL_ADMIN: 3,
}


# =====================================================================
# 1. ESCOPO DO USUÁRIO — "o que este usuário vê e pode tocar"
# =====================================================================
def escopo_usuario(usuario: Dict[str, Any]) -> Dict[str, Any]:
    """
    Traduz o usuário logado (retorno de get_current_user) no seu escopo de
    carteira. Retorna um dict descritivo:
        {
          "funcao": <str>,
          "nivel_ok": <bool>,          # tem acesso à etapa?
          "ve_tudo": <bool>,           # Admin?
          "campo_rls": <str|None>,     # 'gerente_nome' | 'supervisor_nome' | None
          "valor_rls": <str|None>,     # o nome que casa no campo
          "nome_responsavel": <str|None>,  # identidade para o cadeado
        }

    Regras:
      - Admin: vê tudo, sem filtro.
      - Gerente: filtra dim_clientes.gerente_nome = seu gerente_nome.
      - Coordenador: filtra dim_clientes.supervisor_nome = seu supervisor_nome.
    """
    funcao = (usuario.get("funcao") or "").strip()

    if funcao == NIVEL_ADMIN:
        return {
            "funcao": funcao, "nivel_ok": True, "ve_tudo": True,
            "campo_rls": None, "valor_rls": None,
            "nome_responsavel": "ADMIN",
        }

    if funcao == NIVEL_GERENTE:
        nome = (usuario.get("gerente_nome") or "").strip()
        if not nome:
            # Gerente sem gerente_nome amarrado = cadastro inconsistente. Sem escopo.
            return {"funcao": funcao, "nivel_ok": False, "ve_tudo": False,
                    "campo_rls": None, "valor_rls": None, "nome_responsavel": None}
        return {
            "funcao": funcao, "nivel_ok": True, "ve_tudo": False,
            "campo_rls": "gerente_nome", "valor_rls": nome,
            "nome_responsavel": nome,
        }

    if funcao == NIVEL_COORDENADOR:
        nome = (usuario.get("supervisor_nome") or "").strip()
        if not nome:
            return {"funcao": funcao, "nivel_ok": False, "ve_tudo": False,
                    "campo_rls": None, "valor_rls": None, "nome_responsavel": None}
        return {
            "funcao": funcao, "nivel_ok": True, "ve_tudo": False,
            "campo_rls": "supervisor_nome", "valor_rls": nome,
            "nome_responsavel": nome,
        }

    # Qualquer outra função (inclui Vendedor/Executivo) não acessa esta etapa.
    return {"funcao": funcao, "nivel_ok": False, "ve_tudo": False,
            "campo_rls": None, "valor_rls": None, "nome_responsavel": None}


def exigir_acesso_metas(usuario: Dict[str, Any]) -> Dict[str, Any]:
    """
    Porta de entrada de qualquer endpoint da etapa. Devolve o escopo se o
    usuário tem acesso; caso contrário, 403. Uso:
        escopo = exigir_acesso_metas(usuario)
    """
    escopo = escopo_usuario(usuario)
    if not escopo["nivel_ok"]:
        funcao = escopo.get("funcao") or "(sem função)"
        if funcao in FUNCOES_COM_ACESSO:
            # Tem a função certa, mas falta o vínculo no cadastro. Erro
            # operacional, não de permissão — a mensagem precisa dizer isso,
            # senão o usuário acha que perdeu acesso.
            campo = ("gerente_nome" if funcao == NIVEL_GERENTE else "supervisor_nome")
            raise HTTPException(
                status_code=403,
                detail=(f"Seu usuário está como {funcao}, mas o campo '{campo}' "
                        f"não está preenchido no cadastro. Sem esse vínculo não é "
                        f"possível identificar sua carteira. Peça ao Administrador "
                        f"para completar seu cadastro no Painel Admin.")
            )
        raise HTTPException(
            status_code=403,
            detail=(f"A etapa de Metas é restrita a Gerente, Coordenador ou "
                    f"Administrador. Sua função atual é {funcao}.")
        )
    return escopo


# =====================================================================
# 2. CLÁUSULA SQL DE RLS — para leitura filtrada
# =====================================================================
def clausula_rls(escopo: Dict[str, Any], alias_cli: str = "c") -> Dict[str, Any]:
    """
    Devolve o fragmento SQL e os parâmetros para filtrar a carteira do usuário.
        {"where": "<sql>", "params": {...}}
    Admin -> sem restrição (where '1=1'). Demais -> filtra pelo campo do escopo.

    O filtro é aplicado sobre dim_clientes (alias_cli), pois gerente_nome e
    supervisor_nome vivem lá. vendedor_nome vive em fato_ibp_granular, mas não
    é usado como filtro de acesso (vendedor não loga).
    """
    if escopo["ve_tudo"]:
        return {"where": "1=1", "params": {}}

    campo = escopo["campo_rls"]
    valor = escopo["valor_rls"]
    if not campo or not valor:
        # Sem escopo válido: não retorna nada (falha fechada, nunca aberta).
        return {"where": "1=0", "params": {}}

    return {
        "where": f"TRIM({alias_cli}.{campo}) = :rls_valor",
        "params": {"rls_valor": valor},
    }


# =====================================================================
# 3. VALIDAÇÃO DE ESCRITA LINHA A LINHA — a trava de escrita
# =====================================================================
def validar_escrita(db: Session, escopo: Dict[str, Any], ids_linhas: List[int], ciclo: str) -> None:
    """
    GARANTIA DE SEGURANÇA CENTRAL. Antes de qualquer UPDATE, confirma que
    TODAS as linhas (ids em fato_ibp_granular) que o usuário quer alterar
    pertencem ao escopo dele. Impede que um payload malicioso/errado toque
    carteira de outro responsável.

    Admin passa direto. Demais: conta quantas das linhas pedidas caem FORA
    do escopo; se houver ao menos uma, aborta a operação inteira com 403.
    """
    if escopo["ve_tudo"]:
        return
    if not ids_linhas:
        return

    campo = escopo["campo_rls"]
    valor = escopo["valor_rls"]
    if not campo or not valor:
        raise HTTPException(status_code=403, detail="Escopo de edição inválido.")

    # Conta linhas pedidas que NÃO pertencem ao escopo (ou não existem no ciclo).
    fora = db.execute(text(f"""
        SELECT COUNT(*) FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        WHERE f.id = ANY(CAST(:ids AS BIGINT[]))
          AND f.ciclo_sop = :ciclo
          AND TRIM(c.{campo}) IS DISTINCT FROM :valor
    """), {"ids": ids_linhas, "ciclo": ciclo, "valor": valor}).scalar() or 0

    if fora > 0:
        raise HTTPException(
            status_code=403,
            detail=f"Operação negada: {fora} linha(s) fora da sua carteira. Nenhuma alteração foi aplicada."
        )

    # Confirma também que todas as linhas pedidas realmente existem no ciclo
    # e no escopo (evita IDs inexistentes passando silenciosamente).
    dentro = db.execute(text(f"""
        SELECT COUNT(*) FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        WHERE f.id = ANY(CAST(:ids AS BIGINT[]))
          AND f.ciclo_sop = :ciclo
          AND TRIM(c.{campo}) = :valor
    """), {"ids": ids_linhas, "ciclo": ciclo, "valor": valor}).scalar() or 0

    if dentro != len(set(ids_linhas)):
        raise HTTPException(
            status_code=400,
            detail="Algumas linhas informadas não existem neste ciclo. Recarregue a tela."
        )


# =====================================================================
# 4. DDL DA TABELA DE CADEADOS POR RESPONSÁVEL
# =====================================================================
# Trava de ETAPA continua em controle_ciclos (origem='Metas'), criada só
# pelo Admin no bloqueio final. As travas POR PESSOA vivem aqui, separadas,
# para nunca misturar 'pessoa' com 'etapa' na mesma coluna.
DDL_CONTROLE_METAS = """
CREATE TABLE IF NOT EXISTS controle_metas_responsavel (
    id                SERIAL PRIMARY KEY,
    ciclo_sop         VARCHAR(7)   NOT NULL,
    nome_responsavel  VARCHAR(120) NOT NULL,
    nivel             VARCHAR(20)  NOT NULL,   -- 'Gerente' | 'Coordenador'
    status            VARCHAR(20)  NOT NULL DEFAULT 'CONGELADO',
    congelado_por     VARCHAR(120),            -- quem efetivou (auditoria)
    data_congelamento TIMESTAMP    DEFAULT NOW(),
    UNIQUE (ciclo_sop, nome_responsavel)
);
CREATE INDEX IF NOT EXISTS ix_ctrl_metas_ciclo ON controle_metas_responsavel (ciclo_sop);
"""


def garantir_tabela_cadeados(db: Session) -> None:
    """Cria a tabela de cadeados por responsável se ainda não existir."""
    for stmt in DDL_CONTROLE_METAS.strip().split(";"):
        s = stmt.strip()
        if s:
            db.execute(text(s))
    db.commit()


# =====================================================================
# 5. CADEADOS — consulta, trava e reabertura com precedência hierárquica
# =====================================================================
def cadeado_do_responsavel(db: Session, ciclo: str, nome_responsavel: str) -> Optional[str]:
    """Retorna o status do cadeado de um responsável, ou None se aberto."""
    return db.execute(text("""
        SELECT status FROM controle_metas_responsavel
        WHERE ciclo_sop = :c AND TRIM(nome_responsavel) = :n
    """), {"c": ciclo, "n": nome_responsavel.strip()}).scalar()


def esta_congelado_para_usuario(db: Session, escopo: Dict[str, Any], ciclo: str) -> bool:
    """
    Diz se a carteira DO PRÓPRIO usuário está congelada (cadeado dele existe).
    Admin nunca é bloqueado por cadeado individual.
    """
    if escopo["ve_tudo"]:
        return False
    nome = escopo["nome_responsavel"]
    if not nome:
        return True  # sem identidade = falha fechada
    return cadeado_do_responsavel(db, ciclo, nome) == "CONGELADO"


def travar_cadeado(db: Session, escopo: Dict[str, Any], ciclo: str,
                   nome_alvo: Optional[str] = None, nivel_alvo: Optional[str] = None) -> str:
    """
    Cria/atualiza o cadeado de um responsável.
      - Coordenador/Gerente travando A SI MESMO: nome_alvo=None usa o próprio.
      - Gerente travando EM NOME de um coordenador abaixo: informa nome_alvo
        e nivel_alvo='Coordenador' (precisa validar que o coordenador é da
        gerência dele — feito no router, que tem o mapa gerência->coordenação).
    Retorna o nome efetivamente travado.
    """
    if escopo["ve_tudo"]:
        # Admin não "tranca a si"; ele usa o bloqueio de etapa. Mas pode travar
        # em nome de alguém (uso administrativo).
        if not nome_alvo:
            raise HTTPException(status_code=400, detail="Admin deve indicar o responsável a travar.")
        nome = nome_alvo.strip()
        nivel = nivel_alvo or NIVEL_COORDENADOR
    elif nome_alvo and nome_alvo.strip() != (escopo["nome_responsavel"] or "").strip():
        # Travar em nome de outro só é permitido para nível superior (Gerente).
        if _PESO_HIERARQUIA.get(escopo["funcao"], 0) <= _PESO_HIERARQUIA.get(NIVEL_COORDENADOR, 0):
            raise HTTPException(status_code=403, detail="Você não pode congelar a carteira de outro responsável.")
        nome = nome_alvo.strip()
        nivel = nivel_alvo or NIVEL_COORDENADOR
    else:
        nome = (escopo["nome_responsavel"] or "").strip()
        nivel = escopo["funcao"]

    if not nome:
        raise HTTPException(status_code=400, detail="Responsável inválido para congelamento.")

    executor = (escopo["nome_responsavel"] or escopo["funcao"])
    db.execute(text("""
        INSERT INTO controle_metas_responsavel (ciclo_sop, nome_responsavel, nivel, status, congelado_por)
        VALUES (:c, :n, :nv, 'CONGELADO', :exe)
        ON CONFLICT (ciclo_sop, nome_responsavel)
        DO UPDATE SET status='CONGELADO', nivel=:nv, congelado_por=:exe, data_congelamento=NOW()
    """), {"c": ciclo, "n": nome, "nv": nivel, "exe": executor})
    db.commit()
    return nome


def reabrir_cadeado(db: Session, escopo: Dict[str, Any], ciclo: str, nome_alvo: str,
                    pertence_ao_escopo: bool) -> None:
    """
    Reabre (remove) o cadeado de um responsável. PRECEDÊNCIA HIERÁRQUICA:
      - O próprio dono pode reabrir o seu.
      - Superior (Gerente sobre Coordenador da sua gerência; Admin sobre todos)
        pode reabrir. A verificação de que 'nome_alvo' está de fato abaixo do
        usuário é feita pelo router via 'pertence_ao_escopo' (o router tem o
        mapa hierárquico gerência->coordenação).
      - Par NÃO pode reabrir cadeado de par.
    """
    if not escopo["ve_tudo"]:
        raise HTTPException(status_code=403, detail="Somente o Administrador pode reabrir carteiras.")
    alvo = nome_alvo.strip()
    eh_o_proprio = (alvo == (escopo["nome_responsavel"] or "").strip())

    if not escopo["ve_tudo"] and not eh_o_proprio and not pertence_ao_escopo:
        raise HTTPException(
            status_code=403,
            detail="Você só pode reabrir a sua própria carteira ou a de um responsável abaixo de você."
        )

    db.execute(text("""
        DELETE FROM controle_metas_responsavel
        WHERE ciclo_sop = :c AND TRIM(nome_responsavel) = :n
    """), {"c": ciclo, "n": alvo})
    db.commit()


def etapa_metas_bloqueada(db: Session, ciclo: str) -> bool:
    """
    Diz se a ETAPA Metas foi publicada pelo Admin (bastão já passou p/ Supply).
    Trava de etapa vive em controle_ciclos (origem='Metas', status='CONGELADO').
    """
    st = db.execute(text("""
        SELECT status FROM controle_ciclos
        WHERE ciclo_sop = :c AND origem = 'Metas'
    """), {"c": ciclo}).scalar()
    return st == "CONGELADO"

