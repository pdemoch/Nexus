from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
import requests
import json

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai", tags=["Nexus AI Copilot"])

# =====================================================================
# SCHEMAS PARA O CONTEXT INJECTION (O que vem do Frontend)
# =====================================================================
class ContextoDashboard(BaseModel):
    visao_ativa: str
    risco_supply_total_brl: float
    top_itens: List[Dict[str, Any]]

class PayloadAI(BaseModel):
    pergunta: str
    contexto_dashboard: ContextoDashboard

# =====================================================================
# CONFIGURAÇÃO DO GOOGLE GEMINI
# (Dica: No futuro, coloque esta chave num ficheiro .env por segurança)
# =====================================================================
GEMINI_API_KEY = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"
# Usando a versão recomendada para chat analítico rápido
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"

@router.post("/analise-sop")
def analise_sop_copilot(payload: PayloadAI, usuario_logado: dict = Depends(get_current_user)):
    """
    Endpoint que cruza a pergunta do Diretor com o contexto da tela 
    e envia para o Google Gemini gerar a análise S&OP.
    Nota: Usamos 'def' ao invés de 'async def' porque a biblioteca requests 
    é síncrona. O FastAPI lidará com isso perfeitamente em threadpool.
    """
    try:
        contexto = payload.contexto_dashboard
        visao = "Categorias e Portfólio" if contexto.visao_ativa == "categorias" else "Top Clientes (Curva A)"
        
        # 1. CONSTRUÇÃO DO PROMPT EXECUTIVO (A "Alma" do Nexus AI)
        instrucao_sistema = (
            "Você é o Nexus AI, um Copiloto Executivo Sênior especialista em S&OP (Sales and Operations Planning). "
            f"Você está auxiliando {usuario_logado.get('nome')} durante uma reunião de consenso. "
            f"O usuário está atualmente analisando a visão de: {visao}.\n"
            f"O risco total de Supply (demanda comercial cortada pela fábrica) visível nesta tela é de R$ {contexto.risco_supply_total_brl:,.2f}.\n\n"
            "Abaixo estão os Top 5 itens desta tela (com base na receita) e seus respectivos riscos de abastecimento:\n"
        )
        
        for item in contexto.top_itens:
            instrucao_sistema += f"- {item.get('nome', 'N/A')}: Receita Total R$ {item.get('receita_total', 0):,.2f} | Risco Supply: R$ {item.get('risco_supply_brl', 0):,.2f}\n"

        instrucao_sistema += (
            "\nREGRAS DE RESPOSTA:\n"
            "1. Seja direto, assertivo e aja como um conselheiro estratégico.\n"
            "2. Foque sempre em impactos financeiros e gargalos (restrições) de Supply, acrescimos de Comercial e dados da projeção IA.\n"
            "3. Use formatação Markdown (negrito) para destacar valores financeiros e nomes.\n"
            "4. Se a pergunta for abrangente, resuma o que está crítico com base no risco apresentado.\n"
            "5. Não invente ou alucine dados. Se não tiver a resposta nos dados acima, indique que precisa de mais detalhes na tabela granular.\n\n"
            f"PERGUNTA DO USUÁRIO:\n\"{payload.pergunta}\""
        )

        # 2. CHAMADA À API DO GOOGLE GEMINI
        headers = {
            'Content-Type': 'application/json',
            'X-goog-api-key': GEMINI_API_KEY
        }
        
        data = {
            "contents": [{
                "parts": [{"text": instrucao_sistema}]
            }]
        }

        response = requests.post(GEMINI_URL, headers=headers, json=data)
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Erro na LLM do Google: {response.text}")
            
        result = response.json()
        
        try:
            # Extraindo o texto da resposta da estrutura JSON do Gemini
            resposta_ia = result['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            resposta_ia = "Desculpe, ocorreu um erro de processamento de linguagem ao interpretar os dados."

        return {"status": "success", "resposta": resposta_ia}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno no Copiloto AI: {str(e)}")