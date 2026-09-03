import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# Agora o banco bebe da fonte centralizada de configurações
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # Configurações de alta performance exclusivas para PostgreSQL
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, 
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
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