from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import router_consensus, router_auth, router_admin, router_dashboard, router_npd
from app.core.config import settings
from app.models.domain_models import Base
from app.core.database import engine

# =========================================================================
# A MÁGICA DO BANCO DE DADOS
# Esta linha vai ao PostgreSQL na nuvem e cria todas as tabelas 
# automaticamente caso elas ainda não existam.
# =========================================================================
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Nexus IBP 3.0 API",
    description="Motor de S&OP, Machine Learning e Rateio Atômico",
    version="3.0.0"
)

# =========================================================================
# SEGURANÇA DE REDE (CORS)
# Lê do config.py quem tem permissão para aceder à API
# =========================================================================
origins = [
    "https://lineanexus.com.br",
    "http://localhost:3000", # para seus testes locais
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Injeção das Rotas
app.include_router(router_consensus.router)
app.include_router(router_auth.router)
app.include_router(router_admin.router)
app.include_router(router_dashboard.router)
app.include_router(router_npd.router)

@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "online", 
        "sistema": "Nexus IBP 3.0",
        "mensagem": "Servidor rodando perfeitamente em Clean Architecture com PostgreSQL!"
    }