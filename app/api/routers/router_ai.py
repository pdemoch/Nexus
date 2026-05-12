from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
import requests
import json

from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/ai", tags=["Nexus AI Analyst"])

# =====================================================================
# SCHEMAS
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
# CONFIGURAÇÃO GOOGLE GEMINI (VERSÃO 2.5 FLASH VALIDADA)
# =====================================================================
GEMINI_API_KEY = "AIzaSyAmNP1mhQAcN-afbSibO8m-0VibJ22nNSw"

# URL atualizada com o modelo exato retornado pelo seu terminal
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

@router.post("/analise-sop")
def analisar_dados_sop(payload: PayloadAI, usuario: dict = Depends(get_current_user)):
    try:
        ctx = payload.contexto_dashboard
        visao = "Categorias" if ctx.visao_ativa == "categorias" else "Clientes Curva A"
        
        prompt_sistema = (
            "Você é o Nexus AI, um Diretor de S&OP Estratégico. Analise os dados abaixo para uma reunião de diretoria.\n\n"
            f"CONTEXTO DO PAINEL:\n"
            f"- Visão Atual: {visao}\n"
            f"- Risco Total de Receita (Cortes de Supply): R$ {ctx.risco_supply_total_brl:,.2f}\n"
            f"- Oportunidade de Inovação (Sinal IA > Comercial): R$ {ctx.oportunidade_ia_total_brl:,.2f}\n\n"
            "DADOS DETALHADOS DOS TOP ITENS:\n"
        )
        
        for item in ctx.dados_resumidos:
            prompt_sistema += (
                f"- {item.nome}:\n"
                f"  * Projeção IA vs Comercial: {item.delta_ia_bu:+} CX (Impacto: R$ {item.impacto_ia_brl:,.2f})\n"
                f"  * Variação de Preço (PMV): {item.var_pmv:+.1f}%\n"
                f"  * Crescimento vs Histórico: {item.crescimento:+.1f}%\n"
                f"  * Assertividade IA (M-1): {item.assertividade_ia}% de acurácia no mês passado\n"
                f"  * Justificativa de Supply: {item.justificativa if item.justificativa else 'Sem registro'}\n"
                f"  * Faturamento Final: R$ {item.rec_final:,.2f} | Risco Supply: R$ {item.unmet_brl:,.2f}\n\n"
            )

        prompt_sistema += (
            "DIRETRIZES DE ANÁLISE:\n"
            "1. Identifique Inovações (Onde a IA previu mais que o vendedor).\n"
            "2. Analise Risco de Rentabilidade (Quedas de PMV).\n"
            "3. Cruze as Justificativas de fábrica com o risco em Reais.\n"
            "4. Seja Executivo, conciso e use negrito para números chaves.\n\n"
            f"PERGUNTA: \"{payload.pergunta}\""
        )

        headers = {'Content-Type': 'application/json'}
        
        corpo_chamada = {
            "contents": [{"parts": [{"text": prompt_sistema}]}]
        }

        # Timeout de 15s para proteger o seu EC2
        response = requests.post(GEMINI_URL, headers=headers, json=corpo_chamada, timeout=15)
        
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