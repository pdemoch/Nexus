import bcrypt
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.models.domain_models import Usuario, DimCliente

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticação e Usuários"])

# ==========================================
# CONFIGURAÇÕES DE SEGURANÇA JWT
# ==========================================
from app.core.config import settings
SECRET_KEY = settings.SECRET_KEY 
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas ou token expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    if usuario is None:
        raise credentials_exception
        
    return {
        "id": usuario.id,
        "email": usuario.email,
        "funcao": usuario.funcao,
        "nome_vendedor": usuario.nome_vendedor,
        "gerente_nome": getattr(usuario, 'gerente_nome', None),
        "supervisor_nome": getattr(usuario, 'supervisor_nome', None) # <-- ADICIONADO À SESSÃO
    }

# ==========================================
# MOTOR DE HEARTBEAT
# ==========================================
@router.post("/heartbeat")
async def heartbeat(usuario_logado: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        usuario = db.query(Usuario).filter(Usuario.id == usuario_logado['id']).first()
        if usuario:
            usuario.ultima_atividade = datetime.utcnow()
            db.commit()
        return {"status": "alive"}
    except Exception:
        return {"status": "error"}

# ==========================================
# UTILITÁRIOS E PAYLOADS
# ==========================================
def verificar_senha(senha_plana: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha_plana.encode('utf-8')[:72], senha_hash.encode('utf-8'))

def gerar_hash(senha_plana: str) -> str:
    return bcrypt.hashpw(senha_plana.encode('utf-8')[:72], bcrypt.gensalt()).decode('utf-8')

class LoginPayload(BaseModel):
    email: str
    senha: str

class CadastroPayload(BaseModel):
    nome: str
    email: str
    senha_inicial: str
    funcao: str
    nome_vendedor: Optional[str] = None
    gerente_nome: Optional[str] = None 
    supervisor_nome: Optional[str] = None # <-- NOVO CAMPO

class NovaSenhaPayload(BaseModel):
    email: str
    nova_senha: str

# ==========================================
# ROTAS DO SISTEMA
# ==========================================
@router.post("/login")
async def login(payload: LoginPayload, db: Session = Depends(get_db)):
    email_limpo = payload.email.lower().strip()
    
    try:
        usuario = db.query(Usuario).filter(Usuario.email == email_limpo).first()
        
        if not usuario:
            raise HTTPException(status_code=401, detail="Email ou senha incorretos.")
            
        if not verificar_senha(payload.senha, usuario.senha_hash):
            raise HTTPException(status_code=401, detail="Email ou senha incorretos.")
            
        if not usuario.aprovado:
            raise HTTPException(status_code=403, detail="⏳ Cadastro em análise. Aguarde a aprovação de um Administrador.")
            
        # GERAÇÃO DO TOKEN JWT (Adicionamos a função no token)
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": usuario.email, "funcao": usuario.funcao},
            expires_delta=access_token_expires
        )
            
        return {
            "status": "success", 
            "access_token": access_token,
            "token_type": "bearer",
            "usuario": {
                "id": usuario.id,
                "nome": usuario.nome,
                "email": usuario.email,
                "funcao": usuario.funcao,
                "nome_vendedor": usuario.nome_vendedor,
                "gerente_nome": getattr(usuario, 'gerente_nome', None),
                "supervisor_nome": getattr(usuario, 'supervisor_nome', None), # <-- ADICIONADO
                "primeiro_acesso": usuario.primeiro_acesso
            }
        }
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

@router.post("/cadastrar")
async def cadastrar_usuario(payload: CadastroPayload, db: Session = Depends(get_db)):
    email_limpo = payload.email.lower().strip()
    
    try:
        check_email = db.query(Usuario).filter(Usuario.email == email_limpo).first()
        if check_email:
            raise HTTPException(status_code=400, detail="Este email já está cadastrado.")
            
        # AMARRAÇÃO DE COORDENADOR (SUPERVISOR)
        if payload.funcao == 'Coordenador' and payload.supervisor_nome:
            check_coord = db.query(Usuario).filter(Usuario.supervisor_nome == payload.supervisor_nome).first()
            if check_coord:
                raise HTTPException(status_code=400, detail=f"A coordenação {payload.supervisor_nome} já está vinculada a outro usuário.")
        
        # AMARRAÇÃO DE GERENTE
        if payload.funcao == 'Gerente' and payload.gerente_nome:
            check_gerente = db.query(Usuario).filter(Usuario.gerente_nome == payload.gerente_nome).first()
            if check_gerente:
                raise HTTPException(status_code=400, detail=f"A gerência {payload.gerente_nome} já está vinculada a outro usuário.")
            
        total_usuarios = db.query(Usuario).count()
        eh_primeiro_usuario = (total_usuarios == 0)
            
        novo_usuario = Usuario(
            nome=payload.nome.strip().upper(),
            email=email_limpo,
            senha_hash=gerar_hash(payload.senha_inicial),
            funcao=payload.funcao,
            nome_vendedor=None, # Não amarramos mais a um vendedor único
            gerente_nome=payload.gerente_nome if payload.funcao == 'Gerente' else None, 
            supervisor_nome=payload.supervisor_nome if payload.funcao == 'Coordenador' else None, # <-- NOVO
            primeiro_acesso=True,
            aprovado=eh_primeiro_usuario,
            ultima_atividade=datetime.utcnow()
        )
        
        db.add(novo_usuario)
        db.commit()
        return {"status": "success", "message": "Usuário criado com sucesso!"}
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/lista-coordenadores")
async def obter_coordenadores(db: Session = Depends(get_db)):
    """Busca a lista de supervisores disponíveis no ERP (que não estão em uso)"""
    try:
        erp_sup = db.query(DimCliente.supervisor_nome).filter(DimCliente.supervisor_nome.isnot(None)).distinct().all()
        todos_sup = set(r[0].strip() for r in erp_sup if r[0] and r[0].strip())

        usuarios_sup = db.query(Usuario.supervisor_nome).filter(Usuario.supervisor_nome.isnot(None)).distinct().all()
        sup_em_uso = set(u[0].strip() for u in usuarios_sup if u[0] and u[0].strip())

        sup_livres = sorted(list(todos_sup - sup_em_uso))
        return {"status": "success", "dados": sup_livres}
    except Exception:
        raise HTTPException(status_code=500, detail="Erro ao carregar supervisores.")

@router.get("/lista-gerentes")
async def obter_gerentes(db: Session = Depends(get_db)):
    try:
        erp_gerentes = db.query(DimCliente.gerente_nome).filter(DimCliente.gerente_nome.isnot(None)).distinct().all()
        todos_gerentes = set(r[0].strip() for r in erp_gerentes if r[0] and str(r[0]).strip())

        usuarios_gerentes = db.query(Usuario.gerente_nome).filter(Usuario.gerente_nome.isnot(None)).distinct().all()
        gerentes_em_uso = set(u[0].strip() for u in usuarios_gerentes if u[0] and str(u[0]).strip())

        gerentes_livres = sorted(list(todos_gerentes - gerentes_em_uso))
        return {"status": "success", "dados": gerentes_livres}
    except Exception:
        return {"status": "success", "dados": []}

@router.get("/pendentes")
async def listar_pendentes(db: Session = Depends(get_db)):
    try:
        pendentes = db.query(Usuario).filter(Usuario.aprovado == False).all()
        dados = [{
            "id": p.id, "nome": p.nome, "email": p.email, "funcao": p.funcao, 
            "gerente_nome": p.gerente_nome, 
            "supervisor_nome": p.supervisor_nome, # <-- INCLUÍDO
            "criado_em": p.criado_em
        } for p in pendentes]
        return {"status": "success", "dados": dados}
    except Exception:
        raise HTTPException(status_code=500, detail="Erro ao buscar pendentes.")

@router.post("/aprovar/{user_id}")
async def aprovar_usuario(user_id: int, db: Session = Depends(get_db)):
    try:
        usuario = db.query(Usuario).filter(Usuario.id == user_id).first()
        if usuario:
            usuario.aprovado = True
            db.commit()
        return {"status": "success", "message": "Usuário aprovado!"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao aprovar usuário.")

@router.delete("/rejeitar/{user_id}")
async def rejeitar_usuario(user_id: int, db: Session = Depends(get_db)):
    try:
        usuario = db.query(Usuario).filter(Usuario.id == user_id).first()
        if usuario:
            db.delete(usuario)
            db.commit()
        return {"status": "success", "message": "Usuário rejeitado e deletado."}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao rejeitar usuário.")
    
@router.get("/ativos")
async def listar_ativos(db: Session = Depends(get_db)):
    try:
        ativos = db.query(Usuario).filter(Usuario.aprovado == True).all()
        dados = [{
            "id": p.id, "nome": p.nome, "email": p.email, "funcao": p.funcao, 
            "gerente_nome": p.gerente_nome,
            "supervisor_nome": p.supervisor_nome # <-- INCLUÍDO
        } for p in ativos]
        return {"status": "success", "dados": dados}
    except Exception:
        raise HTTPException(status_code=500, detail="Erro ao buscar usuários ativos.")

@router.post("/reset-password/{user_id}")
async def resetar_senha(user_id: int, db: Session = Depends(get_db)):
    try:
        usuario = db.query(Usuario).filter(Usuario.id == user_id).first()
        if usuario:
            usuario.senha_hash = gerar_hash("Linea@123")
            usuario.primeiro_acesso = True 
            db.commit()
        return {"status": "success", "message": "Senha resetada!"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao resetar senha.")
    
@router.post("/reset-password-self")
async def atualizar_senha_primeiro_acesso(payload: NovaSenhaPayload, db: Session = Depends(get_db)):
    """Rota para o próprio utilizador definir a sua senha definitiva no primeiro acesso."""
    try:
        email_limpo = payload.email.lower().strip()
        usuario = db.query(Usuario).filter(Usuario.email == email_limpo).first()
        
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
            
        # Atualiza a hash com a nova senha e tira a flag de primeiro acesso
        usuario.senha_hash = gerar_hash(payload.nova_senha)
        usuario.primeiro_acesso = False 
        db.commit()
        
        return {"status": "success", "message": "Senha atualizada com sucesso!"}
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail="Erro ao atualizar senha.")