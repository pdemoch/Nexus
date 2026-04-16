from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo

# =====================================================================
# MOTOR DE DATAS E CICLOS
# =====================================================================
def get_current_cycle() -> str:
    return datetime.date.today().strftime("%m/%Y")

def get_previous_cycle() -> str:
    return (datetime.date.today().replace(day=1) - relativedelta(months=1)).strftime("%m/%Y")

def get_projection_window() -> tuple[str, str]:
    hoje = datetime.date.today().replace(day=1)
    return (hoje + relativedelta(months=2)).strftime('%Y-%m-%d'), (hoje + relativedelta(months=4)).strftime('%Y-%m-%d')

def parse_date_safe(date_input) -> datetime.date:
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()

# =====================================================================
# A FONTE DA VERDADE ÚNICA (SINGLE SOURCE OF TRUTH)
# =====================================================================
def get_truth_query(db: Session, ciclo: str, m2: str, m4: str):
    """
    Todas as telas (Top-Down, Bottom-Up, Gerenciamento, Global) 
    SÃO OBRIGADAS a iniciar as suas buscas por esta função.
    Isto garante que não haja discrepância de faturamento por causa de 
    filtros ocultos (ex: clientes inativos ou sem vendedor).
    """
    return db.query(FatoIbpGranular, DimCliente, DimProduto)\
        .join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
        .join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
        .filter(
            FatoIbpGranular.mes_projetado >= m2,
            FatoIbpGranular.mes_projetado <= m4,
            FatoIbpGranular.ciclo_sop == ciclo,
            func.upper(func.coalesce(DimCliente.bloqueado, 'ATIVO')) != 'INATIVO'
        )

# =====================================================================
# VALIDADOR DE TRAVAS (LOCKS)
# =====================================================================
from fastapi import HTTPException

def check_global_lock(db: Session, ciclo: str):
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        ControleCiclo.origem == 'S&OP-Final', 
        ControleCiclo.status == 'Fechado'
    ).first()
    
    if registro:
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global já está publicado.")