from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo
from fastapi import HTTPException

# =====================================================================
# MOTOR DE DATAS E CICLOS
# =====================================================================
def get_current_cycle() -> str:
    return datetime.date.today().strftime("%m/%Y")

def get_previous_cycle() -> str:
    return (datetime.date.today().replace(day=1) - relativedelta(months=1)).strftime("%m/%Y")

def get_projection_window() -> tuple[datetime.date, datetime.date]:
    hoje = datetime.date.today().replace(day=1)
    return (hoje + relativedelta(months=2), hoje + relativedelta(months=4))

def parse_date_safe(date_input) -> datetime.date:
    if isinstance(date_input, datetime.date): return date_input
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()

# =====================================================================
# A FONTE DA VERDADE ÚNICA (SINGLE SOURCE OF TRUTH)
# =====================================================================
def get_truth_query(db: Session, ciclo: str, data_ini: datetime.date, data_fim: datetime.date):
    """
    CORREÇÃO DO BUG (AttributeError): 
    Agora o db.query pede APENAS a tabela FatoIbpGranular. 
    Isso garante que as rotas de POST/Salvar recebam o objeto direto, e não uma Tupla.
    Os joins continuam aqui para permitir filtros avançados.
    """
    return db.query(FatoIbpGranular)\
        .outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
        .outerjoin(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
        .filter(
            FatoIbpGranular.mes_projetado >= data_ini,
            FatoIbpGranular.mes_projetado <= data_fim,
            FatoIbpGranular.ciclo_sop == ciclo,
            func.upper(func.coalesce(DimCliente.bloqueado, 'ATIVO')) != 'INATIVO'
        )

# =====================================================================
# VALIDADOR DE TRAVAS (LOCKS)
# =====================================================================
def check_global_lock(db: Session, ciclo: str):
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        ControleCiclo.origem == 'S&OP-Final', 
        ControleCiclo.status == 'Fechado'
    ).first()
    
    if registro:
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global já está publicado.")

def check_origin_lock(db: Session, ciclo: str, origem: str):
    """
    Verifica se a gerência trancou a carteira deste vendedor/regional específico.
    """
    if not origem: return
    
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        func.upper(func.trim(ControleCiclo.origem)) == origem.strip().upper(),
        ControleCiclo.status == 'Fechado'
    ).first()
    
    if registro:
        raise HTTPException(status_code=403, detail=f"Acesso Negado: A carteira de '{origem}' foi trancada pela Gerência e não pode receber alterações.")