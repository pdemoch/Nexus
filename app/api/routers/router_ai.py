from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
import requests
import json

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai", tags=["Nexus AI Analyst"])

# =====================================================================
# SCHEMAS PARA O CONTEXTO E PERGUNTA
# =====================================================================
class ItemResumido(BaseModel):
    nome: str
    delta_ia_bu: int
    impacto_ia_brl: float
    var_pmv: float
    crescimento: float
    assertividade_ia: float
    justificativa: str
    rec_final: float
    unmet_brl: float

class ContextoDashboard(BaseModel):
    visao_ativa: str
    risco_supply_total_brl: float
    oportunidade_ia_total_brl: float
    dados_resumidos: List[ItemResumido]

class PayloadAI(BaseModel):
    pergunta: str
    contexto_dashboard: ContextoDashboard

# =====================================================================
# CONFIGURAÇÃO GOOGLE GEMINI
# =====================================================================
GEMINI_API_KEY = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"

@router.post("/analise-sop")
def analisar_dados_sop(payload: PayloadAI, usuario: dict = Depends(get_current_user)):
    """
    Consome os dados enriquecidos do dashboard e utiliza o Gemini para 
    gerar insights estratégicos sobre o ciclo S&OP.
    """
    try:
        ctx = payload.contexto_dashboard
        visao = "Categorias" if ctx.visao_ativa == "categorias" else "Clientes Curva A"
        
        # 1. CONSTRUÇÃO DO PROMPT ESTRATÉGICO
        # Aqui injetamos a inteligência de negócio que o Gemini deve seguir
        prompt_sistema = (
            "Você é o Nexus AI, um Diretor de S&OP Estratégico. Analise os dados abaixo para uma reunião de diretoria.\n\n"
            f"CONTEXTO DO PAINEL:\n"
            f"- Visão Atual: {visao}\n"
            f"- Risco Total de Receita (Cortes de Supply): R$ {ctx.risco_supply_total_brl:,.2f}\n"
            f"- Oportunidade de Inovação (Sinal IA acima do Comercial): R$ {ctx.oportunidade_ia_total_brl:,.2f}\n\n"
            "DADOS DETALHADOS DOS TOP ITENS:\n"
        )
        
        for item in ctx.dados_resumidos:
            prompt_sistema += (
                f"- {item.nome}:\n"
                f"  * Projeção IA vs Comercial: {item.delta_ia_bu:+} CX (Impacto: R$ {item.impacto_ia_brl:,.2f})\n"
                f"  * Variação de Preço (PMV): {item.var_pmv:+.1f}%\n"
                f"  * Crescimento vs Histórico: {item.crescimento:+.1f}%\n"
                f"  * Assertividade IA (M-1): {item.assertividade_ia}% de acurácia no mês passado\n"
                f"  * Justificativa de Supply: {item.justificativa if item.justificativa else 'Nenhuma observação técnica'}\n"
                f"  * Faturamento Final: R$ {item.rec_final:,.2f} | Risco Supply: R$ {item.unmet_brl:,.2f}\n\n"
            )

        prompt_sistema += (
            "SUAS DIRETRIZES DE ANÁLISE:\n"
            "1. Identifique 'Pontos de Inovação': Destaque onde a IA vê demanda que o comercial não viu e valide isso com a Assertividade da IA.\n"
            "2. Analise a 'Erosão de Margem': Alerte se o faturamento sobe mas o PMV cai (indício de descontos excessivos).\n"
            "3. Interprete o 'Risco de Ruptura': Use as justificativas de supply para explicar por que o faturamento de grandes itens está em risco.\n"
            "4. Seja Executivo: Use termos como 'Aderência ao Plano', 'Gargalo Logístico' e 'Market Share'.\n"
            "5. Responda em Português (PT-BR) de forma concisa e em negrito para números importantes.\n\n"
            f"PERGUNTA DO USUÁRIO: \"{payload.pergunta}\""
        )

        # 2. CHAMADA AO GEMINI
        headers = {'Content-Type': 'application/json'}
        corpo_chamada = {
            "contents": [{
                "parts": [{"text": prompt_sistema}]
            }]
        }

        response = requests.post(GEMINI_URL, headers=headers, json=corpo_chamada)
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Falha na comunicação com o motor de IA.")
            
        dados_ia = response.json()
        
        try:
            resposta_texto = dados_ia['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            resposta_texto = "O Nexus AI não conseguiu processar esta análise no momento. Por favor, tente novamente."

        return {"status": "success", "resposta": resposta_texto}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))