import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nexus_local.db")

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, 
        pool_pre_ping=True,  
        pool_size=20,        
        max_overflow=30     
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """
    Esta função será usada em TODAS as rotas da sua API.
    Ela abre uma conexão quando o usuário faz o request, e garante que a conexão 
    é fechada (devolvida ao pool) assim que o request termina, mesmo que dê erro.
    Isso impede o vazamento de memória (Memory Leak).
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()