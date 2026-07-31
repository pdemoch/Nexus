from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Tuple
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, AuditoriaAjuste
from fastapi import HTTPException

# =====================================================================
# CONSTANTES CANÔNICAS DE ETAPA E STATUS
# =====================================================================
# Nomes EXATOS como gravados em controle_ciclos (confirmado no banco).
# O código antigo usava 'Top-Down Arena'/'Demand-Review'/'Fechado' e as travas
# NÃO pegavam, porque o banco tem 'TopDown'/'BottomUP'/'CONGELADO'. Toda trava
# e propagação passa a usar estas constantes — fim da divergência de string.
ETAPA_TOPDOWN  = 'TopDown'
ETAPA_BOTTOMUP = 'BottomUP'
ETAPA_METAS    = 'Metas'
ETAPA_SUPPLY   = 'Supply'
ETAPA_FINAL    = 'Final'
STATUS_CONGELADO = 'CONGELADO'

# Ordem do bastão e o campo que cada etapa possui.
ORDEM_ETAPAS = [ETAPA_TOPDOWN, ETAPA_BOTTOMUP, ETAPA_METAS, ETAPA_SUPPLY, ETAPA_FINAL]
CAMPO_DA_ETAPA = {
    ETAPA_TOPDOWN:  'vol_topdown',
    ETAPA_BOTTOMUP: 'vol_bottomup',
    ETAPA_METAS:    'vol_meta',
    ETAPA_SUPPLY:   'vol_supply',
    ETAPA_FINAL:    'vol_final',
}

# =====================================================================
# MÁQUINA DO TEMPO (MOTOR DE DATAS E CICLOS)
# =====================================================================
def _get_active_date(db: Session) -> datetime.date:
    """Lê o relógio global do sistema a partir da base de dados"""
    try:
        resultado = db.execute(text("SELECT ciclo_ativo_global FROM configuracao_sistema ORDER BY id DESC LIMIT 1")).fetchone()
        ciclo_str = resultado[0] if resultado else datetime.date.today().strftime("%m/%Y")
        mes, ano = map(int, ciclo_str.split('/'))
        return datetime.date(ano, mes, 1)
    except Exception:
        return datetime.date.today().replace(day=1)

def get_current_cycle(db: Session) -> str:
    """Retorna a string do ciclo ativo oficial. Ex: '05/2026'."""
    return _get_active_date(db).strftime("%m/%Y")

def get_previous_cycle(db: Session) -> str:
    """Retorna o ciclo imediatamente anterior ao ciclo ativo."""
    return (_get_active_date(db) - relativedelta(months=1)).strftime("%m/%Y")

def get_projection_window(db: Session) -> Tuple[datetime.date, datetime.date]:
    """Retorna a tupla original (Data Início M2, Data Fim M4). Mantido para compatibilidade legado."""
    hoje_ficticio = _get_active_date(db)
    return (hoje_ficticio + relativedelta(months=2), hoje_ficticio + relativedelta(months=4))

# --- NOVAS FUNÇÕES PARA O DOSSIÊ TOP-DOWN (FVA E HIERARQUIA) ---

def get_working_window_months(db: Session) -> List[datetime.date]:
    """Retorna a lista exata dos meses táticos de foco (M2, M3 e M4)."""
    data_ini, data_fim = get_projection_window(db)
    meses = []
    atual = data_ini
    while atual <= data_fim:
        meses.append(atual)
        atual += relativedelta(months=1)
    return meses

def get_comparison_intersection(db: Session) -> List[datetime.date]:
    """
    Retorna os meses da janela atual (M2 e M3) que também existiam 
    na projeção do ciclo passado. Essencial para a ponte Cycle-over-Cycle.
    """
    return get_working_window_months(db)[:2]

def parse_date_safe(date_input) -> datetime.date:
    """Garante que a entrada vira um datetime.date seguro."""
    if isinstance(date_input, datetime.date): 
        return date_input
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()

def _coerce_mes(mes) -> datetime.date:
    """Aceita date/datetime/'YYYY-MM-DD'/'YYYY-MM' e devolve o 1º dia do mês."""
    if isinstance(mes, datetime.datetime):
        return mes.date().replace(day=1)
    if isinstance(mes, datetime.date):
        return mes.replace(day=1)
    partes = str(mes).split("T")[0].split("-")
    return datetime.date(int(partes[0]), int(partes[1]), 1)

# =====================================================================
# A FONTE DA VERDADE ÚNICA (SINGLE SOURCE OF TRUTH)
# =====================================================================
def get_truth_query(db: Session, ciclo: str, data_ini: datetime.date, data_fim: datetime.date):
    """
    Query base para o Motor de S&OP.

    NÃO filtra por DimCliente.bloqueado NEM por DimProduto.ativo — de propósito.

    O escopo de clientes E de produtos de um ciclo é congelado no NASCIMENTO
    dele (o distributor/loader só gera linha para cliente ATIVO e SKU do mix
    vigente naquele momento). Se a linha existe na fato_ibp_granular, então o
    cliente estava ativo e o SKU estava no mix quando o plano foi feito.

    Reaplicar esses filtros na LEITURA, contra o cadastro de HOJE, causava dois
    danos comprovados:
      • Cliente que ficou INATIVO depois virava linha órfã no rateio (o SKU
        410025614 somava 16.333 para 16.000 digitados).
      • 'ativo' em dim_produtos é RECALCULADO a cada carga do ETL
        (loader.py: UPDATE dim_produtos SET ativo = (sku = ANY(:lst))). É um
        flag volátil de "entrou no último lote", não de negócio: 77% do catálogo
        fica ativo=false, incluindo produtos com plano histórico legítimo.
        Filtrar por ele apagaria retroativamente o plano de ciclos passados e
        mudaria a acurácia histórica sozinho.

    Excluir inativo/fora-de-mix é responsabilidade da GERAÇÃO do ciclo, nunca da
    leitura de um ciclo que já existe.
    """
    return db.query(FatoIbpGranular)\
        .outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
        .outerjoin(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
        .filter(
            FatoIbpGranular.mes_projetado >= data_ini,
            FatoIbpGranular.mes_projetado <= data_fim,
            FatoIbpGranular.ciclo_sop == ciclo
        )

# =====================================================================
# VALIDADOR DE TRAVAS E GERADOR DE AUDITORIA
# =====================================================================
def check_global_lock(db: Session, ciclo: str):
    """Valida se a publicação final do S&OP fechou as edições."""
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo,
        ControleCiclo.origem == ETAPA_FINAL,
        ControleCiclo.status == STATUS_CONGELADO
    ).first()

    if registro:
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global já está publicado.")

def check_origin_lock(db: Session, ciclo: str, origem: str):
    """Valida trancas individuais por departamento."""
    if not origem: return
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        func.upper(func.trim(ControleCiclo.origem)) == origem.strip().upper(),
        ControleCiclo.status == STATUS_CONGELADO
    ).first()
    
    if registro:
        raise HTTPException(status_code=403, detail=f"Acesso Negado: A carteira de '{origem}' foi trancada e não pode receber alterações.")

def registrar_log_auditoria(db: Session, ciclo: str, origem: str, usuario: str, sku: str, cliente: str, mes: datetime.date, v_antigo: int, v_novo: int):
    """Grava uma linha na trilha de auditoria se houver mudança de valor."""
    if int(v_antigo) == int(v_novo):
        return
        
    novo_log = AuditoriaAjuste(
        ciclo_sop=ciclo,
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
# RATEIO CANÔNICO — a soma das partes é SEMPRE o total digitado; zero é zero
# =====================================================================
def ratear_maior_resto(total: int, pesos: List[float]) -> List[int]:
    """
    Distribui um TOTAL inteiro entre N posições conforme 'pesos', com garantias:

      1. sum(partes) == total, SEMPRE. O número digitado manualmente é a base
         absoluta e é respeitado — inclusive 0: se total=0, TODAS as posições
         recebem 0 (zerar é zerar, gravado explicitamente, nunca "pulado").
      2. A sobra fracionária vai para os MAIORES PESOS primeiro.

    Bordas: total<=0 -> [0]*n ; soma(pesos)<=0 -> igualitário ; n==0 -> [].
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

# =====================================================================
# ESCRITOR CANÔNICO — o número digitado É a soma gravada (trava de balanço)
# =====================================================================
def escrever_volume_rateado(db: Session, ciclo: str, sku: str, mes,
                            volume_alvo: int, campo_destino: str,
                            campos_peso: List[str],
                            respeitar_imutabilidade: bool = True) -> int:
    """
    Fonte ÚNICA de escrita de volume rateado, usada por TopDown, Bottom-Up,
    Metas, Supply e S&OP Global. Garante que TODAS as telas obedeçam à mesma
    regra:

      ESCOPO  = todas as linhas do ciclo/sku/mês, SEM filtrar por cadastro atual.
      BALANÇO = a soma gravada é EXATAMENTE volume_alvo, ou a transação aborta.
      ZERO    = volume_alvo=0 grava 0 em todas as linhas (não pula).
      PESOS   = primeiro campo com soma positiva vence; negativos viram zero.

    Retorna o número de linhas alteradas.
    """
    if campo_destino not in CAMPO_DA_ETAPA.values():
        raise HTTPException(status_code=400, detail=f"Campo de destino inválido: {campo_destino}")

    mes_date = _coerce_mes(mes)
    if respeitar_imutabilidade:
        check_imutabilidade_mes(mes_date, sku)

    cols = sorted({"id", "vol_ia", campo_destino, *campos_peso})
    linhas = db.execute(text(f"""
        SELECT {", ".join(cols)} FROM fato_ibp_granular
        WHERE ciclo_sop = :c AND sku = :s AND mes_projetado = :m
        ORDER BY id
    """), {"c": ciclo, "s": sku, "m": mes_date}).fetchall()
    if not linhas:
        return 0

    pesos = []
    for campo in campos_peso:
        candidato = [max(0.0, float(getattr(l, campo) or 0)) for l in linhas]
        if sum(candidato) > 0:
            pesos = candidato
            break
    if not pesos:
        pesos = [1.0] * len(linhas)

    volume_alvo = int(volume_alvo)
    partes = ratear_maior_resto(volume_alvo, pesos)

    if sum(partes) != volume_alvo:
        raise HTTPException(
            status_code=500,
            detail=(f"Falha de balanço de massa no SKU {sku} em "
                    f"{mes_date.strftime('%m/%Y')}: digitado {volume_alvo}, "
                    f"rateado {sum(partes)} em {len(linhas)} linhas.")
        )

    alteradas = 0
    for l, parte in zip(linhas, partes):
        if int(getattr(l, campo_destino) or 0) != int(parte):
            db.execute(
                text(f"UPDATE fato_ibp_granular SET {campo_destino} = :v WHERE id = :id"),
                {"v": int(parte), "id": l.id}
            )
            alteradas += 1
    return alteradas

# =====================================================================
# IMUTABILIDADE DO PASSADO
# =====================================================================
def _hoje_real() -> datetime.date:
    """
    Data REAL de calendário (America/Sao_Paulo), NÃO o relógio interno editável.
    A trava não pode depender do relógio global, porque é recuando esse relógio
    que se reabre um ciclo fechado.
    """
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()

def mes_esta_realizado(mes) -> bool:
    """Mês anterior ao mês corrente = já fechado / virou realizado."""
    return _coerce_mes(mes) < _hoje_real().replace(day=1)

def check_imutabilidade_mes(mes, sku: str = "", contexto: str = "plano"):
    """
    Bloqueia escrita em mês já realizado. Sem esta trava, reabrir um ciclo
    antigo e digitar um número próximo do que já se sabe que vendeu melhora a
    acurácia histórica sem ter previsto nada (look-ahead bias). Protege também
    contra a carga automática do pipeline diário reescrever mês fechado.
    """
    if mes_esta_realizado(mes):
        alvo = _coerce_mes(mes).strftime("%m/%Y")
        raise HTTPException(
            status_code=403,
            detail=(f"O mês {alvo} já foi realizado e é imutável"
                    f"{f' (SKU {sku})' if sku else ''}. Ajustes só são aceitos "
                    f"em meses ainda não fechados.")
        )

# =====================================================================
# PROPAGAÇÃO ENTRE ETAPAS RESPEITANDO QUEM JÁ PUBLICOU
# =====================================================================
def etapa_congelada(db: Session, ciclo: str, etapa: str) -> bool:
    """True se a etapa do ciclo já foi publicada (status CONGELADO)."""
    status = db.execute(text("""
        SELECT status FROM controle_ciclos
        WHERE ciclo_sop = :c AND UPPER(TRIM(origem)) = UPPER(TRIM(:o))
    """), {"c": ciclo, "o": etapa}).scalar()
    return str(status).upper() == STATUS_CONGELADO if status else False

def propagar_para_jusante(db: Session, ciclo: str, origem: str, campo_origem: str) -> dict:
    """
    Entrega o volume de uma etapa às etapas seguintes como PONTO DE PARTIDA,
    com duas proteções que não existiam:

    (1) NÃO SOBRESCREVE ETAPA JÁ CONGELADA. O código antigo rodava um UPDATE
        cego no ciclo inteiro (SET vol_bottomup=vol_topdown, vol_meta=...,
        vol_final=...). Bastava recongelar uma etapa de montante depois da
        publicação do Final para apagar os ajustes da Diretoria — foi o Padrão B
        que corrompeu 05/2026 e 06/2026.
    (2) NÃO REESCREVE MÊS JÁ REALIZADO.

    Devolve o que propagou e o que preservou, para log/auditoria.
    """
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