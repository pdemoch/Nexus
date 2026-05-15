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
    max_output_tokens=2048 
)

DATABASE_URL_RO = "postgresql+psycopg2://nexus_ai_readonly:SenhaForteIA2026@nexus-db:5432/nexus_db"
engine = create_engine(DATABASE_URL_RO)

db_langchain = SQLDatabase(engine, sample_rows_in_table_info=1)
toolkit = SQLDatabaseToolkit(db=db_langchain, llm=llm)

# =====================================================================
# CÉREBRO: MAPA DE DADOS E DIRETIVAS CLARAS
# =====================================================================
def get_executive_prefix(contexto: ContextoUI):
    return f"""
    Você é o Nexus AI - Consultor Executivo de S&OP. Responda SEMPRE em Português do Brasil de forma pragmática e focada em negócios.
    
    🚨 [DICIONÁRIO DE DADOS - USE ISTO PARA CRIAR SUAS QUERIES SQL] 🚨
    Para não perder tempo procurando, aqui está a localização exata das métricas que você precisa:
    - fato_vendas: Contém o Histórico Real. Use `SUM(qt_pedido)` para Volume e `SUM(vl_pedido)` para Receita. A data é `data_pedido`.
    - fato_ibp_granular: Contém as Projeções Futuras S&OP. A data é `mes_projetado`. O preço é `pmv_aplicado`. Volumes: `vol_ia` (Inteligência Artificial), `vol_topdown` (Marketing/Diretoria), `vol_final`.
    - dim_produtos: Contém a hierarquia `categoria`, `segmento`, `descricao` e `sku`. Use a coluna `sku` para fazer JOIN com as tabelas de fatos.
    
    🚨 [REGRAS DE EXECUÇÃO] 🚨
    1. Não faça "SELECT *". Selecione apenas as colunas que precisa.
    2. Quando tiver os dados consolidados, gere um dossiê executivo analisando a cascata de volumes, a aderência da IA e o diagnóstico financeiro.
    3. Finalize a sua análise usando EXATAMENTE o texto "Final Answer: " seguido do seu dossiê final. O dossiê deve usar tags <br/> para quebras de linha e **negrito** para destacar valores (ex: **R$ 150.000**).
    4. Responda apenas à pergunta do usuário. Não crie introduções, relatórios gerais, ou cumprimentos longos. Vá direto ao ponto.
    5. Se a pergunta for sobre "quem mais cresceu", olhe a métrica de crescimento percentual entre o pico projetado e o histórico e cite apenas o vencedor e o número exato.
    6. Se a pergunta for de justificação de Supply, leia a chave 'justificativas_supply' de forma objetiva.
    7. Se os dados mostrarem 0.0, não minta, mas sugira educadamente: "De acordo com o contexto em ecrã, os valores consolidados constam como 0%. Sugiro avaliar a granularidade do SKU."
    8. Lembre-se do contexto da tela ativa: {contexto.tela_ativa} e do status do ciclo: {contexto.ciclo_status}. Isso pode influenciar quais dados são mais relevantes para a resposta.
    9. Use no máximo 2048 tokens para a resposta, focando na clareza e objetividade do dossiê executivo.
    10. Responda em Português do Brasil, usando uma linguagem acessível para diretores e gerentes, evitando jargões técnicos complexos.
    11. Se a pergunta for muito ampla, tente identificar o foco principal e responda com base nisso, sempre buscando entregar insights acionáveis.
    12. Se a pergunta envolver comparação entre categorias ou clientes, destaque os vencedores e os números exatos, sem rodeios.
    13. Se a pergunta for sobre projeções futuras, concentre-se nos dados da tabela fato_ibp_granular e destaque as diferenças entre as projeções da IA e do Top-Down.
    14. Se a pergunta for sobre o histórico, concentre-se nos dados da tabela fato_vendas e destaque os números consolidados.
    15. Se a pergunta envolver análise de risco ou oportunidade, destaque os fatores mais críticos identificados nos dados e sugira ações concretas.
    """

@router.post("/perguntar")
def consultoria_360(payload: PerguntaAgente, usuario: dict = Depends(get_current_user)):
    try:
        # Simplificamos o tratamento de erro do LangChain para permitir que o modelo se corrija naturalmente
        agent_executor = create_sql_agent(
            llm=llm,
            toolkit=toolkit,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True,
            handle_parsing_errors=True, # Devolvemos a gestão nativa de erros
            prefix=get_executive_prefix(payload.contexto),
            max_iterations=10 
        )

        resultado = agent_executor.invoke({"input": payload.pergunta})
        
        return {"status": "success", "resposta": resultado["output"]}
    
    except Exception as e:
        print(f"[ERRO AGENTE 360]: {str(e)}")
        erro_str = str(e)
        
        if "429" in erro_str or "Quota exceeded" in erro_str:
            return {"status": "success", "resposta": "⚡ *A IA está processando muitos cálculos. Aguarde alguns segundos e tente novamente.*"}
        
        # Última tentativa de extração caso o Gemini termine a resposta mas sem a tag exata
        if "Could not parse LLM output:" in erro_str:
            try:
                resposta_parcial = erro_str.split("Could not parse LLM output:")[1].strip().strip('`')
                # Removemos "Thought:" residual caso exista no início
                if resposta_parcial.startswith("Thought:"):
                    # Pega a última parte que geralmente é onde a resposta final está sendo construída
                    partes = resposta_parcial.split("Final Answer:")
                    if len(partes) > 1:
                        return {"status": "success", "resposta": partes[-1].strip()}
                    else:
                        # Se não achar o Final Answer, remove apenas o prefixo "Thought: " para não sujar a tela
                        texto_limpo = resposta_parcial[8:].strip()
                        # Se o texto for apenas um raciocínio interno (como "I need to..."), devolve o erro amigável.
                        if "I need to" not in texto_limpo and len(texto_limpo) > 100:
                             return {"status": "success", "resposta": texto_limpo}
            except:
                pass
            
        return {
            "status": "error", 
            "resposta": "⚠️ **Ocorreu uma inconsistência no cruzamento de dados.**<br/><br/>A base de dados retornou informações complexas e o assistente não conseguiu formatá-las a tempo. Tente ser mais específico na solicitação, como 'Qual o faturamento do último semestre para a categoria X?'"
        }