from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# IMPORTAÇÕES ATUALIZADAS (Adeus router_consensus, Olá Rotas Modulares!)
from app.api.routers import (
    router_auth, 
    router_admin, 
    router_dashboard, 
    router_npd, 
    router_topdown, 
    router_bottomup, 
    router_gerenciamento,
    router_soe,
    router_supply,
    router_kpis,
    router_ai,
    router_agent_sql
)
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
    "https://www.lineanexus.com.br", # Adicionado para cobrir acessos via www
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

# =========================================================================
# INJEÇÃO DAS ROTAS (A NOVA ESTRUTURA)
# =========================================================================
app.include_router(router_auth.router)
app.include_router(router_admin.router)
app.include_router(router_dashboard.router)
app.include_router(router_npd.router)
app.include_router(router_topdown.router)
app.include_router(router_bottomup.router)
app.include_router(router_gerenciamento.router)
app.include_router(router_supply.router)
app.include_router(router_soe.router)
app.include_router(router_kpis.router)
app.include_router(router_ai.router)
app.include_router(router_agent_sql.router)

@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "online", 
        "sistema": "Nexus IBP 3.0",
        "mensagem": "Servidor rodando perfeitamente em Arquitetura Modular com PostgreSQL!"
    }