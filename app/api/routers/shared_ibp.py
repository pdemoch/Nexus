from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Tuple
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo, AuditoriaAjuste
from fastapi import HTTPException

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

# =====================================================================
# A FONTE DA VERDADE ÚNICA (SINGLE SOURCE OF TRUTH)
# =====================================================================
def get_truth_query(db: Session, ciclo: str, data_ini: datetime.date, data_fim: datetime.date):
    """Query blindada base para o Motor de S&OP, bloqueando clientes inativos."""
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
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        ControleCiclo.origem == 'S&OP-Final', 
        ControleCiclo.status == 'Fechado'
    ).first()
    
    if registro:
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global já está publicado.")

def check_origin_lock(db: Session, ciclo: str, origem: str):
    """Valida trancas individuais por departamento."""
    if not origem: return
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        func.upper(func.trim(ControleCiclo.origem)) == origem.strip().upper(),
        ControleCiclo.status == 'Fechado'
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

def get_projection_window(db: Session, ciclo_atual: str) -> list:
    """
    Retorna a janela tática correta de planejamento S&OP: M2, M3 e M4.
    Se o ciclo é 07/2026 (Julho), pula o mês corrente e M1, retornando:
    ['2026-09-01', '2026-10-01', '2026-11-01'] (Set, Out, Nov).
    """
    try:
        mes, ano = map(int, ciclo_atual.split('/'))
        data_base = datetime.date(ano, mes, 1)
        
        meses = []
        # i=0 vira M2 (+2 meses), i=1 vira M3 (+3 meses), i=2 vira M4 (+4 meses)
        for i in range(3): 
            data_proj = data_base + relativedelta(months=(i + 2))
            meses.append(data_proj.strftime('%Y-%m-%d'))
            
        return meses
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao calcular janela de projeção: {str(e)}")