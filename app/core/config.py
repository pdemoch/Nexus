import os
from dotenv import load_dotenv

# Carrega as variáveis ocultas do ficheiro .env na raiz do projeto
load_dotenv()

class Settings:
    # Nome e Versão do Sistema
    PROJECT_NAME: str = "Nexus IBP 3.0"
    VERSION: str = "3.0.0"

    # Chave de Autenticação da API do ERP (Gobi)
    GOBI_TOKEN: str = os.getenv("GOBI_TOKEN")
    if not GOBI_TOKEN:
        print("⚠️ AVISO: GOBI_TOKEN não encontrado no ficheiro .env!")

    # URL do Banco de Dados Relacional (PostgreSQL ou SQLite local)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nexus_local.db")

    # (Opcional) Chaves do JWT para quando refatorarmos a Autenticação
    SECRET_KEY: str = os.getenv("SECRET_KEY", "uma-chave-secreta-muito-segura-para-desenvolvimento")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # 7 dias

# Instância global para ser importada nos outros ficheiros
settings = Settings()