from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importa os roteadores (routers) das nossas APIs
from app.api.routers import router_consensus
from app.api.routers import router_auth
from app.api.routers import router_admin
from app.api.routers import router_dashboard
from app.api.routers import router_npd


# Inicializa o servidor FastAPI
app = FastAPI(
    title="Nexus IBP 3.0 API",
    description="Motor de S&OP, Machine Learning e Rateio Atômico",
    version="3.0.0"
)

# ==========================================
# CONFIGURAÇÃO DE CORS (Crucial para o React)
# ==========================================
# Isso permite que o seu frontend (ex: localhost:5173 do Vite) 
# faça requisições para o backend (localhost:8000) sem ser bloqueado pelo navegador.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# REGISTRO DAS ROTAS
# ==========================================
# Aqui nós "espetamos" o router do Consenso (Bottom-Up) que criamos no Passo anterior
app.include_router(router_consensus.router)
app.include_router(router_auth.router)
app.include_router(router_admin.router)
app.include_router(router_dashboard.router)
app.include_router(router_npd.router)


# Rota de Saúde (Health Check)
@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "online", 
        "sistema": "Nexus IBP 3.0",
        "mensagem": "Servidor rodando perfeitamente em Clean Architecture!"
    }