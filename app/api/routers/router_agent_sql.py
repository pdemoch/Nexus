from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import os
import json
from sqlalchemy import create_engine

# Importações LangChain e Google
from langchain_community.utilities.sql_database import SQLDatabase
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.agent_toolkits import create_sql_agent, SQLDatabaseToolkit
from langchain.agents.agent_types import AgentType

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai-sql", tags=["Nexus SQL Strategic Consultant"])

# --- SCHEMAS DE ENTRADA ---
class ContextoUI(BaseModel):
    tela_ativa: str     # 'categorias' ou 'clientes'
    ciclo_status: str    # Status extraído do controle_ciclos / lockMessage

class PerguntaAgente(BaseModel):
    pergunta: str
    contexto: ContextoUI

# --- CONFIGURAÇÃO DO CÉREBRO (GEMINI 2.5 FLASH) ---
# A temperatura 0 garante precisão matemática, mas o prompt dará a fluidez estratégica.
os.environ["GOOGLE_API_KEY"] = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

# --- CONEXÃO SEGURA (READ-ONLY) ---
DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)
db_langchain = SQLDatabase(engine)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# DICIONÁRIO SEMÂNTICO E DIRETRIZES ESTRATÉGICAS (CONHECIMENTO DO MOTOR)
# =====================================================================
def get_strategic_prefix(contexto: ContextoUI):
    return f"""
    VOCÊ É O NEXUS AI - CONSULTOR ESTRATÉGICO DE S&OP E IBP.
    Você não é um gerador de SQL; você é um analista que usa SQL para extrair decisões.
    
    CONTEXTO ATUAL DO SISTEMA:
    - O utilizador está na visão de: {contexto.tela_ativa}.
    - Status do Ciclo: {contexto.ciclo_status}.

    1. ENTENDIMENTO DO ECOSSISTEMA (Tabelas e Relacionamentos):
    - 'fato_ibp_granular': Contém o futuro (M2 a M4) e os ciclos passados. É onde ocorrem os ajustes de Top-Down, Bottom-Up e Supply.
    - 'fato_vendas': Contém o passado (Histórico). Use para calcular crescimento (CAGR) e tendências.
    - 'dim_produtos' e 'dim_clientes': Contêm os atributos (Categoria, Regional, Gerente). JOIN sempre por 'sku' ou 'cgc'.
    - 'fato_acuracia': Contém o desempenho da IA. Aqui você descobre qual modelo (Prophet, Random Forest) venceu e qual a acurácia.

    2. DIRETRIZES DE CÁLCULO ESTRATÉGICO:
    - FATURAMENTO (RECEITA): Sempre Volume * 'pmv_aplicado'. O PMV é o Preço Médio de Venda que garante a visão financeira.
    - POTENCIAL DE CRESCIMENTO: Identifique onde 'vol_ia' > 'vol_bottomup' (Comercial) E 'vol_supply' (Fábrica) não tem restrição.
    - RISCO DE RECEITA: Onde 'vol_bottomup' > 'vol_supply'. O valor do risco é (BottomUp - Supply) * PMV.
    - BIAS/ERRO: Diferença entre o que foi planejado no passado e o que foi realizado na 'fato_vendas'.

    3. DIRETRIZES POR TIPO DE PERGUNTA:
    - ESTRATÉGICA (Onde atuar?): Compare a projeção da IA (tendência estatística) com o plano humano. Sugira focar onde a IA vê demanda que o comercial não viu, mas que a fábrica consegue produzir.
    - TÁTICA (Quem são os piores/melhores?): Use a 'dim_clientes' para agrupar por Regional ou Gerente e identificar gaps de faturamento.
    - MATEMÁTICA (Qual o valor?): Execute somas precisas de 'vl_pedido' (passado) ou Projeção de Receita (futuro).

    4. COMPORTAMENTO E LINGUAGEM:
    - NUNCA mencione nomes de tabelas ou colunas. Use "No plano de demanda", "Nas vendas históricas", "A fábrica justificou".
    - Responda sempre em Português do Brasil.
    - Se encontrar um erro de supply, cite a 'justificativa_supply' de forma elegante.
    - Formate valores financeiros como R$ 0.000,00.
    """

@router.post("/perguntar")
def perguntar_ao_consultor(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        # Criamos o agente dinamicamente com o prefixo contextualizado
        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True, # Logs no Docker para auditoria da query
            handle_parsing_errors=True,
            prefix=get_strategic_prefix(payload.contexto)
        )

        # Execução com o padrão moderno .invoke()
        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {
            "status": "success", 
            "resposta": resultado["output"]
        }
        
    except Exception as e:
        print(f"[ERRO AGENTE ESTRATÉGICO]: {str(e)}")
        return {
            "status": "error", 
            "resposta": f"Desculpe, tive uma dificuldade técnica ao analisar os dados. Pode reformular a pergunta? (Erro: {str(e)})"
        }