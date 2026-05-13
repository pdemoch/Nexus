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
    temperature=0.0, # Zero alucinação
    max_output_tokens=1024 # Resumo curto e executivo
)

DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)
# LIMITAR PARA NUNCA LER MAIS DE 5 LINHAS (ESTANCA O CONSUMO DE TOKENS)
db_langchain = SQLDatabase(engine, sample_rows_in_table_info=1)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# CÉREBRO: OTIMIZADO PARA CONSUMO BAIXO DE TOKENS E RESPOSTA DIRETA
# =====================================================================
def get_executive_prefix(contexto: ContextoUI):
    return f"""
    Você é o Nexus AI - Consultor S&OP. Seja pragmático, direto e executivo.
    Você tem acesso a um banco PostgreSQL com as tabelas: fato_vendas, fato_ibp_granular, dim_produtos, dim_clientes.
    
    🚨 [MUITO IMPORTANTE: REGRAS PARA NÃO ESTOURAR O LIMITE DE DADOS (TOKENS)] 🚨
    Você ESTÁ PROIBIDO de fazer um "SELECT *" ou consultar todas as linhas da fato_vendas ou fato_ibp_granular.
    Use SEMPRE GROUP BY e SUM() para consolidar a informação.
    Se precisar ver detalhes soltos, limite a consulta a LIMIT 5.

    [COPIE E USE EXATAMENTE ESTAS QUERIES DE BASE (SUBSTITUINDO O ID_DO_SKU)]:
    1) Para ver Histórico:
    SELECT TO_CHAR(data_pedido, 'YYYY-MM') AS mes, SUM(qt_pedido) AS vol_real, SUM(qtcorte) as cortes, ROUND((SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0))::numeric, 2) AS pmv
    FROM fato_vendas WHERE sku = 'ID_DO_SKU' AND data_pedido >= CURRENT_DATE - INTERVAL '6 months' GROUP BY mes ORDER BY mes DESC;

    2) Para ver Cascata e Projeções Futuras:
    SELECT mes_projetado, SUM(vol_ia) as ia, SUM(vol_topdown) as td, SUM(vol_bottomup) as bu, SUM(vol_supply) as supply, SUM(vol_final) as final
    FROM fato_ibp_granular WHERE sku = 'ID_DO_SKU' GROUP BY mes_projetado ORDER BY mes_projetado;

    FORMATO DA SUA RESPOSTA:
    Seja breve, no máximo 3 parágrafos focados em risco e dinheiro.
    Use quebras de linha (<br/>) para organizar.
    OBRIGATÓRIO: A sua resposta final deve iniciar com "Final Answer: " e depois o seu diagnóstico.
    """

@router.post("/perguntar")
def consultoria_360(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True,
            handle_parsing_errors="ALERTA: Escreva a sua recomendação em até 100 palavras e inicie com 'Final Answer: '",
            prefix=get_executive_prefix(payload.contexto),
            # Impede que a IA entre num loop infinito
            max_iterations=4 
        )

        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {"status": "success", "resposta": resultado["output"]}
    except Exception as e:
        print(f"[ERRO AGENTE 360]: {str(e)}")
        # Tratamento do erro de rate limit caso a cota do Google precise de tempo para recuperar
        erro_str = str(e)
        if "429" in erro_str or "Quota exceeded" in erro_str:
            return {"status": "success", "resposta": "⚡ *A IA está a processar muitos cálculos complexos neste momento. Aguarde alguns segundos e tente novamente para receber o seu dossiê executivo.*"}
        
        if "Could not parse LLM output:" in erro_str:
            try:
                resposta_parcial = erro_str.split("Could not parse LLM output:")[1].strip().strip('`')
                return {"status": "success", "resposta": resposta_parcial}
            except:
                pass
            
        return {"status": "error", "resposta": f"Falha na IA. Tente Novamente."}