from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import os
from sqlalchemy import create_engine

# Importações do LangChain
from langchain_community.utilities.sql_database import SQLDatabase
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.agent_toolkits import create_sql_agent, SQLDatabaseToolkit
from langchain.agents.agent_types import AgentType

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai-sql", tags=["Nexus SQL Agent"])

class PerguntaAgente(BaseModel):
    pergunta: str

# 1. Configurar o Modelo Google
os.environ["GOOGLE_API_KEY"] = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

# 2. Configurar a Conexão Segura ao Banco de Dados (Utilizador Read-Only)
DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)
db_langchain = SQLDatabase(engine)

# CORREÇÃO CRÍTICA: Instanciar o Toolkit explicitamente com a nossa conexão
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# 3. Criar o Agente (O "Motor")
agent_executor = create_sql_agent(
    llm=llm,
    toolkit=toolkit,  # Passamos o toolkit aqui em vez de 'None'
    agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,     # Permite ver a query gerada nos logs do Docker
    handle_parsing_errors=True,
    prefix="Você é um Agente Analista S&OP. A sua função é analisar a base de dados PostgresSQL do Nexus e responder perguntas de negócios de forma executiva. Regras: 1. NUNCA gere ou execute comandos de INSERT, UPDATE, DELETE, DROP. 2. Aja e responda sempre em Português. 3. Vá direto ao ponto."
)

@router.post("/perguntar")
def perguntar_ao_banco(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        # CORREÇÃO CRÍTICA: Atualização do padrão de execução para .invoke()
        resposta = agent_executor.invoke({"input": payload.pergunta})
        
        # O invoke devolve um dicionário, extraímos o "output"
        return {"status": "success", "resposta": resposta["output"]}
    except Exception as e:
        print(f"[ERRO AGENTE SQL]: {str(e)}")
        return {"status": "error", "resposta": f"❌ Erro ao consultar a base de dados: {str(e)}"}