"""
=====================================================================
SHARED_IBP — A ESPINHA DO MOTOR DE S&OP (reconstrução)
=====================================================================
Fundação de ESCRITA e GOVERNANÇA do ciclo de planejamento. Todas as telas de
consenso (Demanda Marketing/TopDown, Demanda Comercial/BottomUP, Metas,
Supply, Demanda Final/Dashboard) e o NPD dependem deste módulo.

A análise (perfil do SKU, acurácia, plurianual) vive em OUTRO módulo
(perfil_sku.py) — aqui é só escrita/governança. Dois pilares, responsabilidades
limpas.

PREMISSAS QUE ESTE MÓDULO GARANTE (aprendidas a duro custo):
  1. DIGITADO = GRAVADO = EXIBIDO. A soma rateada é sempre o número digitado,
     ou a transação aborta (trava de balanço de massa).
  2. ZERO É ZERO. Digitar 0 zera todas as linhas do grupo; nunca "pula".
  3. CLIENTE E SKU SEGUEM O CICLO, não o cadastro de hoje. Rateio/propagação
     nunca filtram por dim_clientes.bloqueado nem dim_produtos.ativo — o escopo
     foi congelado no nascimento do ciclo.
  4. BASTÃO RESPEITA QUEM JÁ PUBLICOU. Congelar uma etapa não sobrescreve
     etapa de jusante já congelada.
  5. PASSADO É IMUTÁVEL. Mês já realizado não aceita escrita (anti look-ahead).
  6. NOMES CANÔNICOS. Etapas: TopDown/BottomUP/Metas/Supply/Final. Status:
     CONGELADO. (Confirmado no banco: controle_ciclos usa exatamente estes.)
  7. REGRA N-2. O mês M é auditado/originado do ciclo (M − 2).
"""

import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Tuple, Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from fastapi import HTTPException

from app.models.domain_models import (
    DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, AuditoriaAjuste
)


# =====================================================================
# 1. CONSTANTES CANÔNICAS DE ETAPA
# =====================================================================
ETAPA_TOPDOWN  = "TopDown"
ETAPA_BOTTOMUP = "BottomUP"
ETAPA_METAS    = "Metas"
ETAPA_SUPPLY   = "Supply"
ETAPA_FINAL    = "Final"
STATUS_CONGELADO = "CONGELADO"

# Ordem do bastão (montante -> jusante).
ORDEM_ETAPAS = [ETAPA_TOPDOWN, ETAPA_BOTTOMUP, ETAPA_METAS, ETAPA_SUPPLY, ETAPA_FINAL]

# Qual coluna cada etapa GRAVA.
CAMPO_DA_ETAPA: Dict[str, str] = {
    ETAPA_TOPDOWN:  "vol_topdown",
    ETAPA_BOTTOMUP: "vol_bottomup",
    ETAPA_METAS:    "vol_meta",
    ETAPA_SUPPLY:   "vol_supply",
    ETAPA_FINAL:    "vol_final",
}

# Cascata de PESO de rateio por etapa (primeiro com soma > 0 vence).
# Supply e Final rateiam sobre a META (decisão comercial já informada),
# NÃO sobre histórico — conforme definido.
PESO_DA_ETAPA: Dict[str, List[str]] = {
    ETAPA_TOPDOWN:  ["vol_ia"],
    ETAPA_BOTTOMUP: ["vol_topdown", "vol_ia"],
    ETAPA_METAS:    ["vol_bottomup", "vol_topdown", "vol_ia"],
    ETAPA_SUPPLY:   ["vol_meta", "vol_bottomup", "vol_ia"],
    ETAPA_FINAL:    ["vol_meta", "vol_supply", "vol_ia"],
}

# Regra N-2.
CICLO_PISO = datetime.date(2026, 4, 1)   # primeiro ciclo existente
DEFASAGEM_MESES = 2


# =====================================================================
# 2. MÁQUINA DO TEMPO
# =====================================================================
def _coerce_mes(mes) -> datetime.date:
    """Aceita date/datetime/'YYYY-MM-DD'/'YYYY-MM' e devolve o 1º dia do mês."""
    if isinstance(mes, datetime.datetime):
        return mes.date().replace(day=1)
    if isinstance(mes, datetime.date):
        return mes.replace(day=1)
    partes = str(mes).split("T")[0].split("-")
    return datetime.date(int(partes[0]), int(partes[1]), 1)


def _get_active_date(db: Session) -> datetime.date:
    """Lê o relógio global do ciclo a partir do banco. Fallback: mês real."""
    try:
        r = db.execute(text(
            "SELECT ciclo_ativo_global FROM configuracao_sistema ORDER BY id DESC LIMIT 1"
        )).fetchone()
        s = r[0] if r else None
        if s:
            mes, ano = map(int, s.split("/"))
            return datetime.date(ano, mes, 1)
    except Exception:
        pass
    return datetime.date.today().replace(day=1)


def get_current_cycle(db: Session) -> str:
    """Ciclo ativo oficial, ex. '07/2026'."""
    return _get_active_date(db).strftime("%m/%Y")


def get_previous_cycle(db: Session) -> str:
    return (_get_active_date(db) - relativedelta(months=1)).strftime("%m/%Y")


def normalizar_ciclo(ciclo: str) -> str:
    """Garante formato 'MM/YYYY' com zero à esquerda."""
    ciclo = str(ciclo).strip()
    mes, ano = ciclo.split("/")
    return f"{int(mes):02d}/{int(ano)}"


def ciclo_para_date(ciclo: str) -> datetime.date:
    mes, ano = normalizar_ciclo(ciclo).split("/")
    return datetime.date(int(ano), int(mes), 1)


def get_projection_window(db: Session) -> Tuple[datetime.date, datetime.date]:
    """Janela tática do ciclo: (M2, M4)."""
    base = _get_active_date(db)
    return (base + relativedelta(months=2), base + relativedelta(months=4))


def get_working_window_months(db: Session) -> List[datetime.date]:
    """Lista [M2, M3, M4] do ciclo ativo."""
    ini, fim = get_projection_window(db)
    meses, atual = [], ini
    while atual <= fim:
        meses.append(atual)
        atual += relativedelta(months=1)
    return meses


def ciclo_fonte_do_mes(mes) -> str:
    """
    REGRA N-2: o ciclo cujo plano deve ser lido/auditado para um mês é (M − 2),
    com piso no primeiro ciclo existente. Junho->04, julho->05, agosto->06.
    É o mecanismo anti-look-ahead: lê-se o plano congelado 2 meses antes do mês.
    """
    m = _coerce_mes(mes)
    cand = m - relativedelta(months=DEFASAGEM_MESES)
    if cand < CICLO_PISO:
        cand = CICLO_PISO
    return cand.strftime("%m/%Y")


def _hoje_real() -> datetime.date:
    """Data REAL de calendário (America/Sao_Paulo), não o relógio editável."""
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()


def mes_esta_realizado(mes) -> bool:
    """Mês anterior ao mês-calendário corrente = já fechado."""
    return _coerce_mes(mes) < _hoje_real().replace(day=1)


def check_imutabilidade_mes(mes, sku: str = "", contexto: str = "plano"):
    """
    Bloqueia escrita em mês já realizado. Impede reabrir ciclo antigo e digitar
    conhecendo a venda (look-ahead), e protege até contra carga automática.
    """
    if mes_esta_realizado(mes):
        alvo = _coerce_mes(mes).strftime("%m/%Y")
        raise HTTPException(
            status_code=403,
            detail=(f"O mês {alvo} já foi realizado e o {contexto} dele é imutável"
                    f"{f' (SKU {sku})' if sku else ''}. Ajustes só em meses não fechados.")
        )


# =====================================================================
# 3. QUERY BASE — sem filtro de cadastro atual
# =====================================================================
def get_truth_query(db: Session, ciclo: str, data_ini: datetime.date, data_fim: datetime.date):
    """
    Query base do Motor de S&OP. NÃO filtra por DimCliente.bloqueado nem por
    DimProduto.ativo — o escopo foi congelado no nascimento do ciclo. Se a linha
    existe, o cliente/SKU estava no plano quando ele foi feito. Filtrar pelo
    cadastro de HOJE reescreveria o passado (vazamento de balanço + viés de
    sobrevivência na acurácia).
    """
    ciclo = normalizar_ciclo(ciclo)
    return (
        db.query(FatoIbpGranular)
        .outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)
        .outerjoin(DimProduto, FatoIbpGranular.sku == DimProduto.sku)
        .filter(
            FatoIbpGranular.mes_projetado >= data_ini,
            FatoIbpGranular.mes_projetado <= data_fim,
            FatoIbpGranular.ciclo_sop == ciclo,
        )
    )


# =====================================================================
# 4. RATEIO CANÔNICO — soma = digitado; zero é zero
# =====================================================================
def ratear_maior_resto(total: int, pesos: List[float]) -> List[int]:
    """
    Distribui TOTAL inteiro entre N posições por 'pesos', garantindo
    sum(partes) == total SEMPRE (inclusive total=0 -> [0]*n). Sobra fracionária
    vai para os maiores pesos primeiro. soma(pesos)<=0 -> igualitário.
    """
    n = len(pesos)
    if n == 0:
        return []
    if total <= 0:
        return [0] * n
    soma = sum(pesos)
    if soma <= 0:
        base = total // n
        partes = [base] * n
        for i in range(total - base * n):
            partes[i] += 1
        return partes
    dist = [(p / soma) * total for p in pesos]
    piso = [int(x) for x in dist]
    sobra = total - sum(piso)
    ordem = sorted(range(n), key=lambda i: (pesos[i], dist[i] - piso[i]), reverse=True)
    for k in range(sobra):
        piso[ordem[k]] += 1
    return piso


def escrever_volume_rateado(db: Session, ciclo: str, sku: str, mes,
                            volume_alvo: int, etapa: str,
                            respeitar_imutabilidade: bool = True) -> int:
    """
    ESCRITOR CANÔNICO — a fonte única de escrita de volume rateado, usada por
    todas as etapas. Garante:
      ESCOPO  = todas as linhas do ciclo/sku/mês (sem filtrar cadastro atual).
      BALANÇO = soma gravada == volume_alvo, ou a transação aborta.
      ZERO    = volume_alvo 0 grava 0 em todas as linhas.
      PESO    = cascata definida em PESO_DA_ETAPA[etapa].
    Retorna o nº de linhas alteradas.
    """
    if etapa not in CAMPO_DA_ETAPA:
        raise HTTPException(status_code=400, detail=f"Etapa inválida: {etapa}")
    campo = CAMPO_DA_ETAPA[etapa]
    campos_peso = PESO_DA_ETAPA[etapa]

    ciclo = normalizar_ciclo(ciclo)
    mes_d = _coerce_mes(mes)
    if respeitar_imutabilidade:
        check_imutabilidade_mes(mes_d, sku, contexto=etapa)

    cols = sorted({"id", "vol_ia", campo, *campos_peso})
    linhas = db.execute(text(f"""
        SELECT {", ".join(cols)} FROM fato_ibp_granular
        WHERE ciclo_sop = :c AND sku = :s AND mes_projetado = :m
        ORDER BY id
    """), {"c": ciclo, "s": sku, "m": mes_d}).fetchall()
    if not linhas:
        return 0

    pesos: List[float] = []
    for cp in campos_peso:
        cand = [max(0.0, float(getattr(l, cp) or 0)) for l in linhas]
        if sum(cand) > 0:
            pesos = cand
            break
    if not pesos:
        pesos = [1.0] * len(linhas)

    volume_alvo = int(volume_alvo)
    partes = ratear_maior_resto(volume_alvo, pesos)

    if sum(partes) != volume_alvo:
        raise HTTPException(
            status_code=500,
            detail=(f"Falha de balanço de massa no SKU {sku} em "
                    f"{mes_d.strftime('%m/%Y')}: digitado {volume_alvo}, "
                    f"rateado {sum(partes)} em {len(linhas)} linhas.")
        )

    alteradas = 0
    for l, parte in zip(linhas, partes):
        if int(getattr(l, campo) or 0) != int(parte):
            db.execute(
                text(f"UPDATE fato_ibp_granular SET {campo} = :v WHERE id = :id"),
                {"v": int(parte), "id": l.id}
            )
            alteradas += 1
    return alteradas


# =====================================================================
# 5. GOVERNANÇA DO BASTÃO — congelamento (admin) e propagação
# =====================================================================
def etapa_congelada(db: Session, ciclo: str, etapa: str) -> bool:
    """True se a etapa do ciclo está CONGELADA."""
    ciclo = normalizar_ciclo(ciclo)
    status = db.execute(text("""
        SELECT status FROM controle_ciclos
        WHERE ciclo_sop = :c AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
    """), {"c": ciclo, "o": etapa}).scalar()
    return str(status).strip().upper() == STATUS_CONGELADO if status else False


def congelar_etapa(db: Session, ciclo: str, etapa: str):
    """
    Marca a etapa como CONGELADA (ação do ADMINISTRADOR). Usa a constraint
    única (ciclo_sop, origem): faz upsert do status.
    """
    ciclo = normalizar_ciclo(ciclo)
    reg = db.execute(text("""
        SELECT id FROM controle_ciclos
        WHERE ciclo_sop = :c AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
    """), {"c": ciclo, "o": etapa}).fetchone()
    if reg:
        db.execute(text("""
            UPDATE controle_ciclos SET status = :st, data_fechamento = NOW()
            WHERE id = :id
        """), {"st": STATUS_CONGELADO, "id": reg.id})
    else:
        db.execute(text("""
            INSERT INTO controle_ciclos (ciclo_sop, origem, status, data_fechamento)
            VALUES (:c, :o, :st, NOW())
        """), {"c": ciclo, "o": etapa, "st": STATUS_CONGELADO})


def reabrir_etapa(db: Session, ciclo: str, etapa: str):
    """Reabre uma etapa congelada (ação do ADMINISTRADOR)."""
    ciclo = normalizar_ciclo(ciclo)
    db.execute(text("""
        UPDATE controle_ciclos SET status = 'ABERTO'
        WHERE ciclo_sop = :c AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
    """), {"c": ciclo, "o": etapa})


def propagar_para_jusante(db: Session, ciclo: str, origem: str) -> dict:
    """
    Entrega o volume da etapa 'origem' às etapas de JUSANTE como ponto de
    partida, com duas proteções:
      1. NÃO SOBRESCREVE ETAPA JÁ CONGELADA (fim do Padrão B).
      2. NÃO REESCREVE MÊS JÁ REALIZADO (só mês >= corrente).
    Devolve o que propagou e o que preservou.
    """
    ciclo = normalizar_ciclo(ciclo)
    campo_origem = CAMPO_DA_ETAPA[origem]
    idx = ORDEM_ETAPAS.index(origem)

    destinos, preservadas = [], []
    for etapa in ORDEM_ETAPAS[idx + 1:]:
        if etapa_congelada(db, ciclo, etapa):
            preservadas.append(etapa)
        else:
            destinos.append(CAMPO_DA_ETAPA[etapa])

    if not destinos:
        return {"linhas": 0, "propagou": [], "preservou": preservadas}

    sets = ", ".join([f"{c} = {campo_origem}" for c in destinos])
    res = db.execute(text(f"""
        UPDATE fato_ibp_granular
        SET {sets}
        WHERE ciclo_sop = :c
          AND mes_projetado >= date_trunc('month', NOW())::date
    """), {"c": ciclo})
    return {"linhas": res.rowcount or 0, "propagou": destinos, "preservou": preservadas}


# =====================================================================
# 6. AUDITORIA (trilha)
# =====================================================================
def registrar_log_auditoria(db: Session, ciclo: str, origem: str, usuario: str,
                            sku: str, cliente: str, mes, v_antigo: int, v_novo: int):
    """Grava uma linha na trilha se houve mudança de valor."""
    if int(v_antigo) == int(v_novo):
        return
    db.add(AuditoriaAjuste(
        ciclo_sop=normalizar_ciclo(ciclo),
        origem_ajuste=origem,
        usuario_nome=usuario,
        sku=sku,
        razaosocial_afetada=cliente,
        mes_projetado=_coerce_mes(mes),
        valor_antigo=int(v_antigo),
        vol_novo=int(v_novo),
    ))


def parse_date_safe(date_input) -> datetime.date:
    """Converte entrada em datetime.date de forma segura."""
    if isinstance(date_input, datetime.date):
        return date_input
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()


# =====================================================================
# TESTE ISOLADO — não toca produção; valida rateio, N-2 e balanço
# =====================================================================
if __name__ == "__main__":
    # Testes que NÃO escrevem no banco: rateio e regra N-2 (funções puras).
    print("=== ratear_maior_resto ===")
    for total, pesos in [(16000, [1569, 917, 620]), (0, [5, 3, 2]), (100, [0, 0, 0]), (7, [1, 1, 1])]:
        r = ratear_maior_resto(total, pesos)
        ok = (sum(r) == total)
        print(f"  total={total:>6} pesos={pesos} -> {r}  soma={sum(r)}  BALANCO={'OK' if ok else 'FALHOU'}")

    print("\n=== regra N-2 (ciclo_fonte_do_mes) ===")
    for mes in ["2026-06-01", "2026-07-01", "2026-08-01", "2026-05-01", "2026-04-01"]:
        print(f"  {mes} -> ciclo {ciclo_fonte_do_mes(mes)}")

    print("\n=== pesos por etapa ===")
    for et in ORDEM_ETAPAS:
        print(f"  {et:10s} grava {CAMPO_DA_ETAPA[et]:14s} peso={PESO_DA_ETAPA[et]}")