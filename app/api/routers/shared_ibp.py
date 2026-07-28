from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Tuple, Optional
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, AuditoriaAjuste
from fastapi import HTTPException

# =====================================================================
# CONSTANTES DE GOVERNANÇA TEMPORAL DO S&OP
# =====================================================================
# Primeiro ciclo que existiu. Nada é lido de ciclo anterior a este.
CICLO_PISO = datetime.date(2026, 4, 1)

# Primeiro mês que teve M2 congelado (congelado pelo ciclo 04/2026).
# Meses anteriores a este NÃO possuem projeção congelada — são realizado puro.
MES_PRIMEIRO_CONGELADO = datetime.date(2026, 6, 1)

# Defasagem entre um mês e o ciclo que o congela: o ciclo N congela o mês N+2.
# Logo, a verdade de um mês M vem do ciclo (M - 2 meses).
DEFASAGEM_CONGELAMENTO_MESES = 2


# =====================================================================
# NORMALIZAÇÃO DE FORMATO (FONTE ÚNICA DO PADRÃO 'MM/YYYY')
# =====================================================================
def normalizar_ciclo(ciclo) -> str:
    """
    Devolve o ciclo sempre no formato canônico 'MM/YYYY' com zero à esquerda.
    Aceita '7/2026', '07/2026', date/datetime. É a barreira que impede a trava
    S&OP de furar por divergência de formato ('7/2026' != '07/2026').

    Levanta HTTPException 400 para entrada malformada, para que a escrita do
    ciclo ativo (input de texto livre no AdminPanel) nunca grave lixo.
    """
    if isinstance(ciclo, (datetime.date, datetime.datetime)):
        return ciclo.strftime("%m/%Y")
    try:
        mes, ano = str(ciclo).strip().split('/')
        mes_i, ano_i = int(mes), int(ano)
        if not (1 <= mes_i <= 12) or not (2000 <= ano_i <= 2100):
            raise ValueError
        return f"{mes_i:02d}/{ano_i}"
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Ciclo inválido: '{ciclo}'. Use o formato MM/YYYY (ex: 07/2026)."
        )


def ciclo_para_date(ciclo: str) -> datetime.date:
    """Converte 'MM/YYYY' no primeiro dia do mês como date."""
    mes, ano = map(int, normalizar_ciclo(ciclo).split('/'))
    return datetime.date(ano, mes, 1)


def _coerce_mes(mes) -> datetime.date:
    """Aceita date/datetime/'YYYY-MM-DD'/'YYYY-MM' e devolve o 1º dia do mês."""
    if isinstance(mes, datetime.datetime):
        return mes.date().replace(day=1)
    if isinstance(mes, datetime.date):
        return mes.replace(day=1)
    s = str(mes).split("T")[0]
    partes = s.split("-")
    ano, m = int(partes[0]), int(partes[1])
    return datetime.date(ano, m, 1)


# =====================================================================
# MÁQUINA DO TEMPO (MOTOR DE DATAS E CICLOS)
# =====================================================================
def _get_active_date(db: Session) -> datetime.date:
    """Lê o relógio global do sistema (configuracao_sistema)."""
    try:
        resultado = db.execute(
            text("SELECT ciclo_ativo_global FROM configuracao_sistema ORDER BY id DESC LIMIT 1")
        ).fetchone()
        ciclo_str = resultado[0] if resultado else datetime.date.today().strftime("%m/%Y")
        return ciclo_para_date(ciclo_str)
    except HTTPException:
        # ciclo gravado em formato inválido — cai no fallback seguro.
        return datetime.date.today().replace(day=1)
    except Exception:
        return datetime.date.today().replace(day=1)


def get_current_cycle(db: Session) -> str:
    """Retorna o ciclo ativo oficial no formato canônico. Ex: '07/2026'."""
    return normalizar_ciclo(_get_active_date(db))


def get_previous_cycle(db: Session) -> str:
    """Retorna o ciclo imediatamente anterior ao ativo, formato canônico."""
    return normalizar_ciclo(_get_active_date(db) - relativedelta(months=1))


# =====================================================================
# 🧭 A REGRA CANÔNICA: DE QUAL CICLO VEM A VERDADE DE UM MÊS
# =====================================================================
def resolver_ciclo_fonte(mes, ciclo_ativo: str) -> Optional[str]:
    """
    Fonte única da regra temporal do S&OP. Dado um mês e o ciclo ativo,
    devolve o ciclo (canônico 'MM/YYYY') de onde a verdade daquele mês deve
    ser lida — ou None se o mês não possui projeção congelada.

    Regra:
      • mês < 06/2026            -> None (realizado puro; não se lê projeção).
      • candidato = mês - 2 meses (o ciclo que congela o mês).
      • candidato < 04/2026      -> 04/2026 (piso: primeiro ciclo existente).
      • candidato > ciclo ativo  -> ciclo ativo (projeção viva; o mês ainda
                                    não foi congelado por nenhum ciclo passado).
      • caso contrário           -> candidato.

    Exemplos com ciclo ativo 07/2026:
      05/2026 -> None      (antes do primeiro congelado)
      06/2026 -> 04/2026   (congelado pelo ciclo 04)
      07/2026 -> 05/2026
      08/2026 -> 06/2026
      09/2026 -> 07/2026   (M-2 = ativo; mês em congelamento agora)
      10/2026 -> 07/2026   (M-2 = 08 > ativo -> teto no ativo, projeção viva)
      11/2026 -> 07/2026   (idem)
    """
    mes_d = _coerce_mes(mes)
    ativo_d = ciclo_para_date(ciclo_ativo)

    # Meses anteriores ao primeiro congelamento não têm projeção de referência.
    if mes_d < MES_PRIMEIRO_CONGELADO:
        return None

    candidato = mes_d - relativedelta(months=DEFASAGEM_CONGELAMENTO_MESES)

    if candidato < CICLO_PISO:
        candidato = CICLO_PISO
    if candidato > ativo_d:
        candidato = ativo_d

    return normalizar_ciclo(candidato)


def resolver_ciclos_por_mes(meses, ciclo_ativo: str) -> dict:
    """
    Aplica resolver_ciclo_fonte a uma lista de meses. Retorna
    {mes_date: ciclo_fonte_ou_None}. Útil para telas que montam um eixo
    temporal e precisam saber, mês a mês, de qual ciclo puxar o dado.
    """
    return {_coerce_mes(m): resolver_ciclo_fonte(m, ciclo_ativo) for m in meses}


# =====================================================================
# JANELA DE PROJEÇÃO (M2, M3, M4) — FONTE ÚNICA, SEM DUPLICAÇÃO
# =====================================================================
def get_projection_window(db: Session, ciclo: Optional[str] = None) -> List[str]:
    """
    Retorna os três meses táticos de planejamento — M2, M3, M4 — como strings
    'YYYY-MM-DD'. Se 'ciclo' não for informado, usa o ciclo ativo do sistema.

    Para o ciclo 07/2026 devolve ['2026-09-01','2026-10-01','2026-11-01'].

    (Unifica as DUAS definições anteriores que existiam neste arquivo, onde a
    segunda apagava a primeira em runtime — origem de erro de assinatura.)
    """
    try:
        data_base = ciclo_para_date(ciclo) if ciclo else _get_active_date(db)
        return [
            (data_base + relativedelta(months=(i + 2))).strftime('%Y-%m-%d')
            for i in range(3)
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao calcular janela de projeção: {str(e)}")


def get_projection_window_dates(db: Session, ciclo: Optional[str] = None) -> Tuple[datetime.date, datetime.date]:
    """
    Versão em (data_ini M2, data_fim M4) como objetos date. Mantida para os
    endpoints que trabalham com intervalo em vez de lista de meses.
    """
    janela = get_projection_window(db, ciclo)
    return _coerce_mes(janela[0]), _coerce_mes(janela[-1])


def get_working_window_months(db: Session, ciclo: Optional[str] = None) -> List[datetime.date]:
    """Lista dos meses táticos (M2, M3, M4) como date."""
    return [_coerce_mes(m) for m in get_projection_window(db, ciclo)]


def get_comparison_intersection(db: Session, ciclo: Optional[str] = None) -> List[datetime.date]:
    """
    Meses da janela atual (M2 e M3) usados na ponte ciclo-a-ciclo.
    """
    return get_working_window_months(db, ciclo)[:2]


def parse_date_safe(date_input) -> datetime.date:
    """Garante que a entrada vira um datetime.date seguro."""
    if isinstance(date_input, datetime.date):
        return date_input
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()


# =====================================================================
# A FONTE DA VERDADE ÚNICA (SINGLE SOURCE OF TRUTH)
# =====================================================================
def get_truth_query(db: Session, ciclo: str, data_ini: datetime.date, data_fim: datetime.date):
    """Query base blindada do Motor de S&OP, bloqueando clientes inativos."""
    ciclo = normalizar_ciclo(ciclo)
    return db.query(FatoIbpGranular)\
        .outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
        .outerjoin(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
        .filter(
            FatoIbpGranular.mes_projetado >= data_ini,
            FatoIbpGranular.mes_projetado <= data_fim,
            FatoIbpGranular.ciclo_sop == ciclo,
            func.upper(func.trim(func.coalesce(DimCliente.bloqueado, 'ATIVO'))) != 'INATIVO'
        )


# =====================================================================
# VALIDADOR DE TRAVAS E GERADOR DE AUDITORIA
# =====================================================================
def check_global_lock(db: Session, ciclo: str):
    """Valida se a publicação final do S&OP fechou as edições."""
    ciclo = normalizar_ciclo(ciclo)
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        ControleCiclo.origem == 'Final',
        ControleCiclo.status == 'CONGELADO'
    ).first()

    if registro:
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global já está publicado.")


def check_origin_lock(db: Session, ciclo: str, origem: str):
    """Valida trancas individuais por departamento."""
    if not origem:
        return
    ciclo = normalizar_ciclo(ciclo)
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        func.upper(func.trim(ControleCiclo.origem)) == origem.strip().upper(),
        ControleCiclo.status == 'CONGELADO'
    ).first()

    if registro:
        raise HTTPException(status_code=403, detail=f"Acesso Negado: A carteira de '{origem}' foi trancada e não pode receber alterações.")


def registrar_log_auditoria(db: Session, ciclo: str, origem: str, usuario: str, sku: str, cliente: str, mes: datetime.date, v_antigo: int, v_novo: int):
    """Grava uma linha na trilha de auditoria se houver mudança de valor."""
    if int(v_antigo) == int(v_novo):
        return

    novo_log = AuditoriaAjuste(
        ciclo_sop=normalizar_ciclo(ciclo),
        origem_ajuste=origem,
        usuario_nome=usuario,
        sku=sku,
        razaosocial_afetada=cliente,
        mes_projetado=mes,
        valor_antigo=v_antigo,
        vol_novo=v_novo
    )
    db.add(novo_log)

# =====================================================================
# RATEIO CANÔNICO (FONTE ÚNICA) — o número manual é a base absoluta.
# =====================================================================
def ratear_maior_resto(total: int, pesos: List[float]) -> List[int]:
    """
    Distribui um TOTAL inteiro entre N posições conforme 'pesos', com duas
    garantias inegociáveis:

      1. A SOMA das partes é EXATAMENTE 'total'. O número digitado manualmente
         (agregado) é a base absoluta e é sempre respeitado — inclusive 0, 1, 2.
         Zerar é zerar; um total de 1 gera exatamente 1 caixa no conjunto.

      2. A sobra fracionária vai para os MAIORES PESOS primeiro (maior
         consumidor), não para o de maior fração decimal nem para o último id.
         Assim "total = 1" cai no maior cliente e zera os demais; "total = 3"
         nos três maiores; e assim por diante.

    Casos de borda:
      • total <= 0        -> todos recebem 0 (zerar é zerar).
      • soma(pesos) <= 0  -> distribui igualitário (ninguém tem histórico;
                             o maior resto vai para as primeiras posições).
      • n == 0            -> [].
    """
    n = len(pesos)
    if n == 0:
        return []
    if total <= 0:
        return [0] * n

    soma = sum(pesos)
    if soma <= 0:
        base = total // n
        resto = total - base * n
        partes = [base] * n
        for i in range(resto):
            partes[i] += 1
        return partes

    # Distribuição proporcional, piso por floor.
    dist = [(p / soma) * total for p in pesos]
    piso = [int(x) for x in dist]  # floor (pesos não-negativos)
    sobra = total - sum(piso)

    # A sobra vai para os MAIORES PESOS primeiro (desempate: maior fração).
    # Ordena índices por (peso, fração) decrescente — o maior consumidor lidera.
    ordem = sorted(range(n), key=lambda i: (pesos[i], dist[i] - piso[i]), reverse=True)
    for k in range(sobra):
        piso[ordem[k]] += 1
    return piso