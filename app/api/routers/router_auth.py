import bcrypt
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.domain_models import Usuario, DimCliente

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticação e Usuários"])

# ==========================================
# CONFIGURAÇÕES DE SEGURANÇA JWT
# ==========================================
SECRET_KEY = "NEXUS_SUPER_SECRET_KEY_MUDE_ISSO_EM_PRODUCAO" 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # O token expira em 24 horas

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
        "gerente_nome": getattr(usuario, 'gerente_nome', None)
    }

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
            
        # GERAÇÃO DO TOKEN JWT
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
            
        if payload.funcao == 'Executivo' and payload.nome_vendedor:
            check_vendedor = db.query(Usuario).filter(Usuario.nome_vendedor == payload.nome_vendedor).first()
            if check_vendedor:
                raise HTTPException(status_code=400, detail=f"O vendedor {payload.nome_vendedor} já está vinculado a outro usuário.")
        
        if payload.funcao == 'Gerente' and payload.gerente_nome:
            check_gerente = db.query(Usuario).filter(getattr(Usuario, 'gerente_nome', None) == payload.gerente_nome).first()
            if check_gerente:
                raise HTTPException(status_code=400, detail=f"A gerência {payload.gerente_nome} já está vinculada a outro usuário.")
            
        novo_usuario = Usuario(
            nome=payload.nome.strip().upper(),
            email=email_limpo,
            senha_hash=gerar_hash(payload.senha_inicial),
            funcao=payload.funcao,
            nome_vendedor=payload.nome_vendedor if payload.funcao == 'Executivo' else None,
            gerente_nome=payload.gerente_nome if payload.funcao == 'Gerente' else None, 
            primeiro_acesso=True,
            aprovado=False
        )
        
        db.add(novo_usuario)
        db.commit()
        return {"status": "success", "message": "Usuário criado com sucesso!"}
        
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/alterar-senha")
async def alterar_senha(payload: NovaSenhaPayload, db: Session = Depends(get_db)):
    email_limpo = payload.email.lower().strip()
    try:
        usuario = db.query(Usuario).filter(Usuario.email == email_limpo).first()
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")

        usuario.senha_hash = gerar_hash(payload.nova_senha)
        usuario.primeiro_acesso = False
        db.commit()
        
        return {"status": "success", "message": "Senha alterada com sucesso!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/lista-vendedores")
async def obter_vendedores(db: Session = Depends(get_db)):
    try:
        erp_vendedores = db.query(DimCliente.vendedor_nome).filter(DimCliente.vendedor_nome.isnot(None)).distinct().all()
        todos_vendedores = set(r[0].strip() for r in erp_vendedores if r[0].strip())

        usuarios_vendedores = db.query(Usuario.nome_vendedor).filter(Usuario.nome_vendedor.isnot(None)).distinct().all()
        vendedores_em_uso = set(u[0].strip() for u in usuarios_vendedores if u[0].strip())

        vendedores_livres = sorted(list(todos_vendedores - vendedores_em_uso))
        return {"status": "success", "dados": vendedores_livres}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao carregar lista de vendedores.")

@router.get("/lista-gerentes")
async def obter_gerentes(db: Session = Depends(get_db)):
    try:
        col_gerente_erp = getattr(DimCliente, 'gerente_nome', None)
        col_gerente_user = getattr(Usuario, 'gerente_nome', None)

        if col_gerente_erp is None:
            return {"status": "success", "dados": []}

        erp_gerentes = db.query(col_gerente_erp).filter(col_gerente_erp.isnot(None)).distinct().all()
        todos_gerentes = set(r[0].strip() for r in erp_gerentes if r[0] and str(r[0]).strip())

        if col_gerente_user is None:
            gerentes_em_uso = set()
        else:
            usuarios_gerentes = db.query(col_gerente_user).filter(col_gerente_user.isnot(None)).distinct().all()
            gerentes_em_uso = set(u[0].strip() for u in usuarios_gerentes if u[0] and str(u[0]).strip())

        gerentes_livres = sorted(list(todos_gerentes - gerentes_em_uso))
        return {"status": "success", "dados": gerentes_livres}
        
    except Exception as e:
        return {"status": "success", "dados": []}

@router.get("/pendentes")
async def listar_pendentes(db: Session = Depends(get_db)):
    try:
        pendentes = db.query(Usuario).filter(Usuario.aprovado == False).all()
        dados = [{
            "id": p.id, 
            "nome": p.nome, 
            "email": p.email, 
            "funcao": p.funcao, 
            "nome_vendedor": p.nome_vendedor, 
            "gerente_nome": getattr(p, 'gerente_nome', None),
            "criado_em": p.criado_em
        } for p in pendentes]
        return {"status": "success", "dados": dados}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao buscar pendentes.")

@router.post("/aprovar/{user_id}")
async def aprovar_usuario(user_id: int, db: Session = Depends(get_db)):
    try:
        usuario = db.query(Usuario).filter(Usuario.id == user_id).first()
        if usuario:
            usuario.aprovado = True
            db.commit()
        return {"status": "success", "message": "Usuário aprovado!"}
    except Exception as e:
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
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao rejeitar usuário.")