from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
import datetime
from app.core.database import get_db
from app.core.state import AppState
from app.models.domain_models import ControleCiclo
from app.etl.pipeline import executar_pipeline_nexus
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/admin", tags=["Administração e Pipeline"])

@router.get("/pipeline/status")
async def obter_status_pipeline(usuario_logado: dict = Depends(get_current_user)):
    # Sem restrição de função aqui, pois o App.tsx de TODOS faz polling nesta rota
    return {"is_running": AppState.pipeline_rodando, "logs": AppState.logs}

@router.post("/pipeline/start")
async def iniciar_pipeline(background_tasks: BackgroundTasks, usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Administradores podem rodar o pipeline.")
        
    if AppState.pipeline_rodando:
        raise HTTPException(status_code=400, detail="O pipeline já está em execução.")
    
    AppState.pipeline_rodando = True
    AppState.logs = [] 
    background_tasks.add_task(executar_pipeline_nexus)
    return {"status": "success", "message": "Pipeline iniciado!"}

@router.post("/reabrir-ciclo")
async def reabrir_ciclo(origem: str, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Administradores podem destrancar ciclos.")
        
    ciclo_atual = datetime.date.today().strftime("%m/%Y")
    try:
        cadeado = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo_atual, ControleCiclo.origem == origem).first()
        if cadeado:
            db.delete(cadeado)
            db.commit()
            return {"status": "success", "message": f"Ciclo destrancado para: {origem}"}
        raise HTTPException(status_code=404, detail="Cadeado não encontrado para esta origem.")
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))