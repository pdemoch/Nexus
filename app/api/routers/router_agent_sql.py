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
    temperature=0.0, 
    max_output_tokens=2048 # Aumentado para dar espaço ao "raciocínio" da IA sem cortar a meio
)

DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)

# LIMITAR PARA NUNCA LER MAIS DE 5 LINHAS
db_langchain = SQLDatabase(engine, sample_rows_in_table_info=1)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# CÉREBRO: PROMPT COM "TRELA CURTA" PARA EVITAR ERROS DE PARSING
# =====================================================================
def get_executive_prefix(contexto: ContextoUI):
    return f"""
    Você é o Nexus AI - Consultor Executivo de S&OP. Responda SEMPRE em Português do Brasil.
    Você tem acesso às tabelas: fato_vendas, fato_ibp_granular, dim_produtos, dim_clientes.
    
    🚨 [REGRAS DE FORMATAÇÃO REACT - LEIA COM ATENÇÃO] 🚨
    Para usar as ferramentas, você DEVE usar o formato exato abaixo. 
    MUITO IMPORTANTE: O seu 'Thought:' deve ter NO MÁXIMO 1 linha. Não planeje tudo de uma vez, aja rápido!

    Thought: [apenas 1 frase curta sobre o que vai fazer agora]
    Action: [nome da ferramenta]
    Action Input: [query SQL limpa, SEM blocos de código ```]
    Observation: [resultado retornado]
    
    Quando tiver a resposta final pronta, feche EXATAMENTE assim:
    Thought: Eu já tenho todos os dados necessários.
    Final Answer: [O seu dossiê executivo estruturado, usando <br/> para quebras de linha e texto focado em Risco e Faturamento]
    
    🚨 [REGRAS DE SQL] 🚨
    1. Nunca faça SELECT * sem LIMIT.
    2. Use SUM() e GROUP BY para consolidar caixas e receitas (R$).
    """

@router.post("/perguntar")
def consultoria_360(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        # Mensagem que será injetada automaticamente se o Gemini errar a formatação
        msg_autocorrecao = (
            "ALERTA: Você esqueceu-se de usar 'Action:' ou escreveu um 'Thought:' muito longo. "
            "Corrija a formatação imediatamente. Pare de pensar e execute a Action, ou dê a Final Answer."
        )

        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True,
            handle_parsing_errors=msg_autocorrecao, # Ensina a IA a consertar-se sozinha
            prefix=get_executive_prefix(payload.contexto),
            max_iterations=8 # Limite de tentativas para não ficar em loop
        )

        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {"status": "success", "resposta": resultado["output"]}
    
    except Exception as e:
        print(f"[ERRO AGENTE 360]: {str(e)}")
        erro_str = str(e)
        
        if "429" in erro_str or "Quota exceeded" in erro_str:
            return {"status": "success", "resposta": "⚡ *A IA está a processar muitos cálculos. Aguarde alguns segundos e tente novamente.*"}
        
        # Última linha de defesa: Limpa o erro sujo se o LangChain desistir
        if "Could not parse LLM output:" in erro_str:
            try:
                # Tenta extrair qualquer pedaço de inteligência que a IA já tinha começado a escrever
                resposta_parcial = erro_str.split("Could not parse LLM output:")[1].strip().strip('`').replace("Thought:", "").strip()
                # Se for muito curto, foi só um pensamento inútil. Se for longo, pode ser a resposta que não ganhou a tag Final Answer.
                if len(resposta_parcial) > 100:
                    return {"status": "success", "resposta": resposta_parcial}
            except:
                pass
            
        return {
            "status": "error", 
            "resposta": "⚠️ **Aviso de Timeout Analítico**<br/><br/>O Nexus AI analisou as tabelas, mas a formatação dos dados falhou. Por favor, seja mais específico na sua pergunta (ex: 'Qual o faturamento do SKU X nos últimos 3 meses?')."
        }