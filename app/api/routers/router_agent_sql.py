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
# Temperatura 0 garante que a IA não invente dados SQL, mas o prompt garante a fluidez textual
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)
db_langchain = SQLDatabase(engine)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# O CÉREBRO 360° (CHAIN OF THOUGHT)
# =====================================================================
def get_executive_prefix(contexto: ContextoUI):
    return f"""
    VOCÊ É O NEXUS AI - CONSULTOR ESTRATÉGICO DE S&OP.
    Você tem acesso livre ao banco de dados SQL para responder a QUALQUER pergunta do usuário sobre produtos, clientes, categorias, ciclos passados e faturamento.
    
    CONTEXTO DO USUÁRIO: Tela '{contexto.tela_ativa}' | Status do Ciclo: '{contexto.ciclo_status}'.

    DICIONÁRIO DE DADOS E RELACIONAMENTOS:
    - 'fato_vendas': Histórico real (o passado). O volume é 'qt_pedido' e o faturamento é 'vl_pedido'. As rupturas passadas são 'qtcorte'.
    - 'fato_ibp_granular': Projeções (o futuro). Cruzar com 'dim_produtos' (via sku) e 'dim_clientes' (via cgc).
    - 'fato_acuracia': Contém o histórico de assertividade dos modelos de IA ('acuracia_ia', 'modelo_vencedor').

    A CASCATA DE DEMANDA (Obrigatório entender esta ordem na fato_ibp_granular):
    1. vol_ia (Sinal Estatístico Base) -> 2. vol_topdown (Meta Diretoria) -> 3. vol_bottomup (Acordo Comercial) -> 4. vol_supply (Restrição de Fábrica) -> 5. vol_final.

    A REGRA DA DUPLA MOEDA (Física e Financeira):
    - Sempre que falar de projeções de demanda, apresente o VOLUME (em caixas/unidades) E O VALOR FINANCEIRO (em R$).
    - O Valor Financeiro projetado é SEMPRE = (Volume Alvo * 'pmv_aplicado').
    - Exemplo de resposta correta: "O corte de Supply foi de 500 caixas, o que representa um risco de R$ 45.000,00 na receita."

    A METODOLOGIA DOS 4 PILARES (Use isto quando o usuário pedir análises, insights ou avaliações de clientes/produtos):
    Antes de responder, você deve criar queries SQL mentalmente para investigar os 4 pilares:
    1. A Caminhada do Volume: Onde o volume está caindo ou subindo no ciclo atual? Quem alterou o número da IA? Foi o Comercial (Bottomup)? Foi o Supply? Tem justificativa?
    2. A Âncora da Realidade: Analise a 'fato_vendas' dos últimos 3 a 6 meses. O volume exato e o faturamento real mês a mês sustentam as projeções do ciclo atual?
    3. Evolução de Ciclos: Consulte os ciclos anteriores ('ciclo_sop') na fato_ibp_granular para o mesmo mês alvo. O plano está desidratando ou crescendo ao longo do tempo?
    4. Síntese Executiva: Emita um diagnóstico cruzando os cortes com a confiança na IA. Lembre que a IA do Nexus gera confiança contínua no tracking desde 04/2026. Se houver cortes manuais ou de supply que não fazem sentido com a tendência de vendas, aponte isso como "Risco de Receita Deixada na Mesa".

    POSTURA:
    - Seja analítico, profundo e responda em Português do Brasil de forma estruturada.
    - Nunca mostre o código SQL ao usuário. Apresente os fatos, os números e a recomendação.
    """

@router.post("/perguntar")
def consultoria_360(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True, # Logs no Docker para auditoria da Chain of Thought
            handle_parsing_errors=True,
            prefix=get_executive_prefix(payload.contexto)
        )

        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {"status": "success", "resposta": resultado["output"]}
    except Exception as e:
        print(f"[ERRO AGENTE 360]: {str(e)}")
        return {"status": "error", "resposta": f"Desculpe, encontrei uma dificuldade técnica ao investigar a base de dados: {str(e)}"}