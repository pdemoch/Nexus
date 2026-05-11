from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
from dateutil.relativedelta import relativedelta
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
    return _get_active_date(db).strftime("%m/%Y")

def get_previous_cycle(db: Session) -> str:
    return (_get_active_date(db) - relativedelta(months=1)).strftime("%m/%Y")

def get_projection_window(db: Session) -> tuple[datetime.date, datetime.date]:
    hoje_ficticio = _get_active_date(db)
    return (hoje_ficticio + relativedelta(months=2), hoje_ficticio + relativedelta(months=4))

def parse_date_safe(date_input) -> datetime.date:
    if isinstance(date_input, datetime.date): return date_input
    return datetime.datetime.strptime(str(date_input).split("T")[0], "%Y-%m-%d").date()

# =====================================================================
# A FONTE DA VERDADE ÚNICA (SINGLE SOURCE OF TRUTH)
# =====================================================================
def get_truth_query(db: Session, ciclo: str, data_ini: datetime.date, data_fim: datetime.date):
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
# VALIDADOR DE TRAVAS E GERADOR DE AUDITORIA
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
    if not origem: return
    registro = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == ciclo, 
        func.upper(func.trim(ControleCiclo.origem)) == origem.strip().upper(),
        ControleCiclo.status == 'Fechado'
    ).first()
    
    if registro:
        raise HTTPException(status_code=403, detail=f"Acesso Negado: A carteira de '{origem}' foi trancada e não pode receber alterações.")

def registrar_log_auditoria(db: Session, ciclo: str, origem: str, usuario: str, sku: str, cliente: str, mes: datetime.date, v_antigo: int, v_novo: int):
    """Grava uma linha na trilha de auditoria se houver mudança de valor"""
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