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

# Pulmão aumentado para evitar cortes no Output e garantir o parse do LangChain
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    temperature=0.0, 
    max_output_tokens=4096 
)

DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)
db_langchain = SQLDatabase(engine)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# O CÉREBRO 360° (CHAIN OF THOUGHT COM FEW-SHOT PROMPTING)
# =====================================================================
def get_executive_prefix(contexto: ContextoUI):
    return f"""
    VOCÊ É O NEXUS AI - CONSULTOR ESTRATÉGICO DE S&OP.
    
    CONTEXTO ATUAL: Tela '{contexto.tela_ativa}' | Ciclo: '{contexto.ciclo_status}'.

    🚨 REGRAS DE OURO OBRIGATÓRIAS (PUNIÇÃO SE IGNORADAS):
    1. PROIBIDO ALUCINAR: Você nunca pode assumir dados de exemplos do schema. Você DEVE usar o `sql_db_query` para buscar o SKU ou Cliente exato solicitado pelo usuário.
    2. CASCATA IBP (fato_ibp_granular): A tabela projeta o futuro por cliente. Para ver o cenário de um SKU, você OBRIGATORIAMENTE deve agrupar (GROUP BY mes_projetado) e somar os volumes.
    3. HISTÓRICO (fato_vendas): Nunca leia uma linha isolada. Um produto tem vários pedidos no mês. OBRIGATÓRIO usar SUM(qt_pedido) e GROUP BY TO_CHAR(data_pedido, 'YYYY-MM').
    4. PMV MÉDIO PRATICADO: Nunca some PMVs. Calcule sempre pela fórmula matemática real: ROUND((SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0))::numeric, 2).
    5. RESPOSTA FINAL: OBRIGATÓRIO iniciar sua conclusão textual com a string exata "Final Answer: ".

    [EXEMPLO DE QUERY PARA HISTÓRICO (COPIE ESTA ESTRUTURA)]:
    SELECT TO_CHAR(data_pedido, 'YYYY-MM') AS mes, SUM(qt_pedido) AS vol_real, SUM(qtcorte) as cortes, ROUND((SUM(vl_pedido) / NULLIF(SUM(qt_pedido), 0))::numeric, 2) AS pmv
    FROM fato_vendas WHERE sku = 'ID_DO_SKU' AND data_pedido >= CURRENT_DATE - INTERVAL '6 months' GROUP BY mes ORDER BY mes DESC;

    [EXEMPLO DE QUERY PARA CASCATA (COPIE ESTA ESTRUTURA)]:
    SELECT mes_projetado, SUM(vol_ia) as ia, SUM(vol_topdown) as td, SUM(vol_bottomup) as bu, SUM(vol_supply) as supply, SUM(vol_final) as final
    FROM fato_ibp_granular WHERE sku = 'ID_DO_SKU' GROUP BY mes_projetado ORDER BY mes_projetado;

    A METODOLOGIA DOS 4 PILARES (Na Resposta Final):
    1. Variação (Histórico vs Projeção): A IA está otimista demais comparada ao histórico recente?
    2. Gaps na Cascata: Houve corte da fábrica (supply < bu)? Houve pressão da diretoria (td > ia)?
    3. Risco Financeiro (R$): Multiplique a diferença de volume na cascata pelo PMV.
    4. Diagnóstico Direto: Texto curto, pragmático e focado no risco/oportunidade de negócio.
    """

@router.post("/perguntar")
def consultoria_360(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True, 
            handle_parsing_errors="ALERTA: Retorne a análise final começando estritamente com 'Final Answer: '",
            prefix=get_executive_prefix(payload.contexto)
        )

        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {"status": "success", "resposta": resultado["output"]}
    except Exception as e:
        print(f"[ERRO AGENTE 360]: {str(e)}")
        # Fallback de Resgate: Se a IA esquecer o "Final Answer" e der erro de parsing, salvamos o texto.
        erro_str = str(e)
        if "Could not parse LLM output:" in erro_str:
            try:
                resposta_parcial = erro_str.split("Could not parse LLM output:")[1].strip().strip('`')
                return {"status": "success", "resposta": resposta_parcial}
            except:
                pass
            
        return {"status": "error", "resposta": f"Falha ao consolidar o insight executivo. Detalhe técnico: {str(e)}"}