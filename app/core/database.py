import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do ficheiro .env
load_dotenv()

# A URL do Banco de Dados. 
# Se não encontrar a variável (ex: ambiente de testes na sua máquina), 
# ele cria um banco SQLite localmente (um ficheirinho) para você não parar de programar.
# Quando for para Produção (Neon.tech/Render), basta colocar a URL do Postgres no .env
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nexus_local.db")

# ==========================================
# 1. O MOTOR (Engine)
# ==========================================
# O Engine é a fábrica de conexões. 
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    # Configuração leve para desenvolvimento local com SQLite
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # Configuração de Alta Performance para Produção (PostgreSQL)
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, 
        pool_pre_ping=True,  # Testa se a conexão está viva antes de fazer a query (Evita crashes)
        pool_size=20,        # Mantém 20 conexões abertas e prontas para uso imediato
        max_overflow=30      # Se houver um pico de acessos, abre até mais 30 conexões de emergência
    )

# ==========================================
# 2. A FÁBRICA DE SESSÕES (SessionMaker)
# ==========================================
# Uma sessão é a "conversa" isolada que um usuário tem com o banco de dados.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ==========================================
# 3. INJEÇÃO DE DEPENDÊNCIA (Para o FastAPI)
# ==========================================
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