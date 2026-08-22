from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# =========================================================================
# ROTAS — Nexus IBP 3.0 (núcleo reconstruído, enxuto)
# Só o que foi reconstruído e validado. Os módulos de apoio antigos (SOE,
# MTRIX, AI, Agent SQL, DataLake, CCC) foram removidos e serão refeitos na
# fase seguinte (pipeline Parquet/S3). Menos superfície = boot confiável.
# =========================================================================
from app.api.routers import (
    router_auth,            # login / JWT (essencial)
    router_admin,           # pipeline / ciclo (essencial)
    router_topdown,         # Demanda Marketing (Top-Down)
    router_gerenciamento,   # Demanda Comercial (Bottom-Up)
    router_irrestrita,      # Demanda Irrestrita (IA × Marketing × Comercial)
    router_carteira,        # Metas Comercial (RLS + cadeados)
    router_supply,          # Supply Review
    router_dashboard,       # Demanda Final (S&OP Global)
    router_npd,             # NPD / Inovações
    router_kpis,            # Auditoria / KPIs
    router_assistente,      # Chat único da Sidebar (Indicadores + Demanda)
)
from app.core.config import settings
from app.models.domain_models import Base
from app.core.database import engine

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Nexus IBP 3.0 API",
    description="Motor de S&OP com Rateio Atômico, Governança de Bastão e Auditoria de Três Eixos",
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
# Prefixos internos: /api/v1/{auth,admin,topdown,gerenciamento,metas,
#                     supply,dashboard,npd,kpis}
# =========================================================================
app.include_router(router_auth.router)
app.include_router(router_admin.router)
app.include_router(router_topdown.router)
app.include_router(router_gerenciamento.router)
app.include_router(router_irrestrita.router)
app.include_router(router_carteira.router)
app.include_router(router_supply.router)
app.include_router(router_dashboard.router)
app.include_router(router_npd.router)
app.include_router(router_kpis.router)
app.include_router(router_assistente.router)


@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "online",
        "sistema": "Nexus IBP 3.0",
        "mensagem": "Motor de S&OP rodando — núcleo reconstruído.",
    }