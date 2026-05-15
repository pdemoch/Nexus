from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import os
from sqlalchemy import create_engine

from langchain_community.utilities.sql_database import SQLDatabase
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.agent_toolkits import create_sql_agent, SQLDatabaseToolkit
from langchain.agents.agent_types import AgentType

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai-sql", tags=["Nexus Executive Agent"])

# --- SCHEMAS DE ENTRADA ---
class ContextoUI(BaseModel):
    tela_ativa: str = "Painel Global" 
    ciclo_status: str = "Aberto"

class PerguntaAgente(BaseModel):
    pergunta: str
    contexto: Optional[ContextoUI] = ContextoUI()

os.environ["GOOGLE_API_KEY"] = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"

# --- OTIMIZAÇÃO DE TOKENS E TEMPO DE RESPOSTA ---
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    temperature=0.0, # Zero alucinação para queries SQL
    max_output_tokens=1024 
)

DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)

# LIMITAR PARA NUNCA LER MAIS DE 5 LINHAS (ESTANCA O CONSUMO DE TOKENS)
db_langchain = SQLDatabase(engine, sample_rows_in_table_info=1)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# CÉREBRO: PROMPT ENGENHEIRADO PARA PREVENIR ERROS DE PARSING (REACT)
# =====================================================================
def get_executive_prefix(contexto: ContextoUI):
    return f"""
    Você é o Nexus AI - Consultor Executivo de S&OP. Seja pragmático, direto e analítico.
    Você responde SEMPRE em Português do Brasil (PT-BR).
    Você tem acesso a um banco PostgreSQL com as tabelas: fato_vendas, fato_ibp_granular, dim_produtos, dim_clientes.
    
    🚨 [REGRAS ESTRITAS DE CONSULTA] 🚨
    1. NUNCA faça um "SELECT *" sem LIMIT.
    2. Use GROUP BY e SUM() para consolidar volumes (caixas) e receitas (R$).
    3. Para cruzar o nome de uma Categoria com as vendas, faça um JOIN entre fato_vendas e dim_produtos usando a chave 'sku'.

    🚨 [FORMATO OBRIGATÓRIO DE RESPOSTA (REACT)] 🚨
    Você deve obrigatoriamente pensar passo a passo usando EXATAMENTE este formato:
    
    Thought: Eu preciso ver quais tabelas usar...
    Action: [nome da ferramenta]
    Action Input: [entrada limpa, SEM usar blocos de código com ```]
    Observation: [resultado retornado pelo banco]
    ... (repita Thought/Action/Action Input/Observation se necessário)
    Thought: Agora eu tenho os dados e posso gerar o dossiê.
    Final Answer: [Seu dossiê executivo final em Português, focado em Risco e Faturamento. Use <br/> para quebras de linha e **negrito** para destacar números.]
    """

@router.post("/perguntar")
def consultoria_360(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        # A MENSAGEM MÁGICA DE AUTOCORREÇÃO
        msg_autocorrecao = (
            "ALERTA DE FORMATO: A sua resposta não seguiu o padrão esperado. "
            "Lembre-se de que você DEVE usar 'Action: [ferramenta]' e 'Action Input: [query sem blocos de código ```]' "
            "OU terminar o raciocínio escrevendo EXATAMENTE 'Final Answer: [O seu dossiê executivo em Português]'."
        )

        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True,
            handle_parsing_errors=msg_autocorrecao, # Ensina a IA a consertar-se sozinha
            prefix=get_executive_prefix(payload.contexto),
            max_iterations=10 # <--- AUMENTADO PARA 10 (Dá tempo para a IA errar, corrigir-se e finalizar)
        )

        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {"status": "success", "resposta": resultado["output"]}
    
    except Exception as e:
        print(f"[ERRO AGENTE 360]: {str(e)}")
        erro_str = str(e)
        
        # Tratamento do erro de rate limit da API do Google
        if "429" in erro_str or "Quota exceeded" in erro_str:
            return {"status": "success", "resposta": "⚡ *A IA está a processar muitos cálculos complexos neste momento. Aguarde alguns segundos e tente novamente.*"}
        
        # Se a IA esgotar as 10 iterações e falhar de forma catastrófica, não mostramos o Thought em Inglês ao usuário.
        return {
            "status": "success", 
            "resposta": "⚠️ **Aviso de Timeout Analítico**<br/><br/>O Nexus AI analisou os dados base da categoria, mas a complexidade cruzada excedeu o tempo de resposta seguro em tempo real. Por favor, tente perguntar novamente ou pesquise um SKU específico."
        }