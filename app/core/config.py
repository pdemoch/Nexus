import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "Nexus IBP 3.0"
    VERSION: str = "3.0.0"

    GOBI_TOKEN: str = os.getenv("GOBI_TOKEN")
    if not GOBI_TOKEN:
        print("⚠️ AVISO: GOBI_TOKEN não encontrado no ficheiro .env!")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nexus_local.db")

    SECRET_KEY: str = os.getenv("SECRET_KEY", "uma-chave-secreta-muito-segura-para-desenvolvimento")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

settings = Settings()