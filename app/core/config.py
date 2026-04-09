import os
from dotenv import load_dotenv

# Carrega as variáveis do ficheiro .env (que a infraestrutura vai criar no servidor)
load_dotenv()

class Settings:
    PROJECT_NAME: str = "Nexus IBP 3.0"
    VERSION: str = "3.0.0"

    # Gobi API
    GOBI_TOKEN: str = os.getenv("GOBI_TOKEN", "")
    if not GOBI_TOKEN:
        print("⚠️ AVISO: GOBI_TOKEN não encontrado no ficheiro .env!")

    # Segurança JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "uma-chave-secreta-muito-segura-para-desenvolvimento")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 7))

    # =========================================================================
    # CONFIGURAÇÕES DO POSTGRESQL (A serem preenchidas pela Infraestrutura)
    # =========================================================================
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_HOST: str = os.getenv("DB_HOST", "")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "")

    # Monta a URL dinamicamente. Se a infra não preencher, usa o SQLite local como emergência.
    if DB_USER and DB_PASSWORD and DB_HOST and DB_NAME:
        DATABASE_URL: str = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    else:
        DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nexus_local.db")

    # =========================================================================
    # SEGURANÇA DE REDE (CORS)
    # =========================================================================
    # A infraestrutura colocará aqui o IP ou Domínio do Frontend (ex: http://192.168.0.50)
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")

settings = Settings()