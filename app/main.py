from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# =========================================================================
# ROTAS — Nexus IBP (reconstrução do motor de S&OP)
# As 5 telas de consenso + NPD foram reconstruídas sobre o novo shared_ibp.
# A Auditoria (KPIs) usa perfil_sku + auditoria_agg + router_kpis.
# =========================================================================
from app.api.routers import (
    router_auth,
    router_admin,
    router_dashboard,       # Demanda Final (S&OP Global) — reconstruído
    router_npd,             # NPD / Inovações — reconstruído
    router_topdown,         # Demanda Marketing (Top-Down) — reconstruído
    router_carteira,        # Metas Comercial — reconstruído (RLS + cadeados)
    router_gerenciamento,   # Demanda Comercial (Bottom-Up) — reconstruído
    router_supply,          # Supply Review — reconstruído
    router_kpis,            # Auditoria / KPIs — reconstruído
    # --- módulos de apoio que permanecem ---
    router_soe,
    router_mtrix,
    router_ai,
    router_agent_sql,
    router_datalake,
    router_ccc,
)
from app.core.config import settings
from app.models.domain_models import Base
from app.core.database import engine

# Cria tabelas que ainda não existam (inclui controle_metas_responsavel se
# modelada; senão é criada on-demand por garantir_tabela_cadeados).
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Nexus IBP 3.0 API",
    description="Motor de S&OP, Machine Learning e Rateio Atômico",
    version="3.0.0",
)

# =========================================================================
# CORS
# =========================================================================
origins = [
    "https://lineanexus.com.br",
    "https://www.lineanexus.com.br",
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
# INJEÇÃO DAS ROTAS
# Cada router já carrega seu próprio prefixo interno:
#   /api/v1/topdown, /api/v1/gerenciamento, /api/v1/metas,
#   /api/v1/supply, /api/v1/dashboard, /api/v1/npd, /api/v1/kpis
# =========================================================================
app.include_router(router_auth.router)
app.include_router(router_admin.router)
app.include_router(router_dashboard.router)
app.include_router(router_npd.router)
app.include_router(router_topdown.router)
app.include_router(router_carteira.router)
app.include_router(router_gerenciamento.router)
app.include_router(router_supply.router)
app.include_router(router_soe.router)
app.include_router(router_kpis.router)
app.include_router(router_mtrix.router)
app.include_router(router_ai.router)
app.include_router(router_agent_sql.router)
app.include_router(router_datalake.router)
app.include_router(router_ccc.router)


@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "online",
        "sistema": "Nexus IBP 3.0",
        "mensagem": "Servidor rodando em Arquitetura Modular com PostgreSQL!",
    }