from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
import requests
import json

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai", tags=["Nexus AI Analyst"])

# =====================================================================
# SCHEMAS (ATUALIZADOS PARA O MODELO AGENTE)
# =====================================================================
class ItemResumido(BaseModel):
    nome: str
    delta_ia_bu: int
    impacto_ia_brl: float
    crescimento_pico_vs_historico: float
    assertividade_ia_pico: float
    justificativas_supply: str
    rec_final: float
    risco_ruptura_brl: float

class ContextoDashboard(BaseModel):
    visao_ativa: str
    risco_supply_total_brl: float
    oportunidade_ia_total_brl: float
    dados_resumidos: List[ItemResumido]

class PayloadAI(BaseModel):
    pergunta: str
    contexto_dashboard: ContextoDashboard

# =====================================================================
# CONFIGURAÇÃO GOOGLE GEMINI (VERSÃO 2.5 FLASH VALIDADA)
# =====================================================================
GEMINI_API_KEY = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

@router.post("/analise-sop")
def analisar_dados_sop(payload: PayloadAI, usuario: dict = Depends(get_current_user)):
    try:
        ctx = payload.contexto_dashboard
        visao = "Categorias" if ctx.visao_ativa == "categorias" else "Clientes Curva A"
        
        # O "JSON" bruto para a IA mastigar como se fosse um Agente Autônomo
        contexto_json = json.dumps([item.dict() for item in ctx.dados_resumidos], ensure_ascii=False)
        
        prompt_sistema = f"""Você é o Nexus AI, um analista de dados de S&OP de nível sênior. 
Acesso à Base de Dados Atuais (Visão: {visao}):
{contexto_json}

INSTRUÇÕES CRÍTICAS DE COMPORTAMENTO:
1. RESPONDA APENAS À PERGUNTA DO UTILIZADOR. Não crie introduções, relatórios gerais, ou cumprimentos longos.
2. Vá direto ao ponto. Use 1 ou 2 parágrafos no máximo.
3. Se a pergunta for sobre "quem mais cresceu", olhe a chave 'crescimento_pico_vs_historico' e cite apenas o vencedor e o número exato.
4. Se a pergunta for de justificação de Supply, leia a chave 'justificativas_supply' de forma objetiva.
5. Se os dados mostrarem 0.0, não minta, mas sugira educadamente: "De acordo com o contexto em ecrã, os valores consolidados constam como 0%. Sugiro avaliar a granularidade do SKU."

Pergunta do Diretor: "{payload.pergunta}"
Resposta Direta:"""

        headers = {'Content-Type': 'application/json'}
        
        corpo_chamada = {
            "contents": [{"parts": [{"text": prompt_sistema}]}]
        }

        # Timeout de 60s para dar tempo à IA de processar os dados sem cortar a ligação AWS
        response = requests.post(GEMINI_URL, headers=headers, json=corpo_chamada, timeout=60)
        
        if response.status_code != 200:
            print(f"[ERRO NEXUS AI] Status {response.status_code}: {response.text}")
            erro_msg = response.json().get('error', {}).get('message', 'Erro desconhecido')
            return {"status": "success", "resposta": f"⚠️ **Erro na API do Google (Status {response.status_code}):** {erro_msg}"}
            
        dados_ia = response.json()
        resposta_texto = dados_ia['candidates'][0]['content']['parts'][0]['text']

        return {"status": "success", "resposta": resposta_texto}

    except requests.exceptions.Timeout:
        print("[ERRO NEXUS AI] Timeout de rede.")
        return {"status": "success", "resposta": "⚠️ **Timeout de Rede:** O servidor AWS EC2 demorou muito para aceder ao Google. Tente novamente."}
    except Exception as e:
        print(f"[ERRO NEXUS AI] Falha Geral: {e}")
        return {"status": "success", "resposta": f"❌ **Falha interna de processamento:** {str(e)}"}