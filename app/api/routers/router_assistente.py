"""
=====================================================================
ROUTER ASSISTENTE — Nexus Bot (chat unico: Indicadores + Demanda + Financeiro)
=====================================================================
Destino: app/api/routers/router_assistente.py

Um unico endpoint, tres modos de contexto:
  'indicadores' -> agente_kpis (portfolio, ciclo ativo)
  'demanda'     -> planejador_demanda (1 sku ou 1 categoria)
  'financeiro'  -> agente_financeiro (PMR / carteira / pipeline financeiro)

Consumido pelo painel de chat flutuante global "Nexus Bot" na Sidebar —
unico ponto de entrada de IA do sistema. Os chats embutidos que existiam
nas telas de KPIs e Financeiro foram descontinuados em favor deste.
"""

from datetime import date, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers import planejador_demanda as pd

router = APIRouter(prefix="/api/v1/assistente", tags=["Assistente"])
FUNCOES_NEXUS_BOT = {"Administrador", "C-Level", "Gerente"}


def require_nexus_bot(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") not in FUNCOES_NEXUS_BOT:
        raise HTTPException(403, "Acesso restrito ao Nexus Bot.")
    return usuario


class PerguntaAssistente(BaseModel):
    pergunta:    str
    modo:        Optional[str] = "auto" # 'auto' | 'indicadores' | 'demanda' | 'financeiro'
    escopo_tipo: Optional[str] = None   # 'sku' | 'categoria' (so modo=demanda)
    escopo_id:   Optional[str] = None   # codigo do sku ou nome da categoria
    historico:   Optional[List[dict]] = None
    # modo=financeiro: filtros opcionais equivalentes aos da tela Financeiro
    data_ini:    Optional[date] = None
    data_fim:    Optional[date] = None
    segmentos:   Optional[str] = None
    regionais:   Optional[str] = None
    status:      Optional[str] = None
    motivos:     Optional[str] = None
    cgc:         Optional[str] = None


def _split(valor: Optional[str]):
    return [v.strip() for v in valor.split(",") if v.strip()] if valor else None


_TERMOS_FINANCEIRO = (
    "pmr", "pmp", "pme", "ccc", "recebimento", "recebimentos", "carteira",
    "inadimpl", "vencimento", "condi", "pagamento", "banco", "e5", "e1",
    "nota fiscal", "faturamento", "cliente", "cnpj", "cgc",
)
_TERMOS_INDICADORES = (
    "wmape", "bias", "fill rate", "acur", "fva", "corte",
    "portf", "indicador", "meta humana", "previsao ia", "forecast",
)
_TERMOS_DEMANDA = (
    "planejamento", "planejar", "demanda", "plano", "sku", "categoria",
    "sazonal", "proximo ciclo", "proximo mes", "previsao de venda",
)


def _classificar_area(pergunta: str, escopo_tipo: Optional[str]) -> str:
    """Roteia sem LLM para não desperdiçar uma chamada nem expor dados ao classificador."""
    if escopo_tipo:
        return "demanda"
    texto = pergunta.casefold()
    if any(termo in texto for termo in _TERMOS_INDICADORES):
        return "indicadores"
    if any(termo in texto for termo in _TERMOS_FINANCEIRO):
        return "financeiro"
    if any(termo in texto for termo in _TERMOS_DEMANDA):
        return "demanda"
    return "indicadores"


@router.post("/chat")
async def chat(
    payload: PerguntaAssistente,
    db: Session = Depends(get_db),
    _: dict = Depends(require_nexus_bot),
):
    if payload.modo not in ("auto", "indicadores", "demanda", "financeiro"):
        raise HTTPException(400, "Campo 'modo' precisa ser 'auto', 'indicadores', 'demanda' ou 'financeiro'.")
    if payload.escopo_tipo and payload.escopo_tipo not in ("sku", "categoria"):
        raise HTTPException(400, "escopo_tipo precisa ser 'sku' ou 'categoria'.")
    if payload.escopo_tipo and not payload.escopo_id:
        raise HTTPException(400, "Informe o item selecionado em escopo_id.")
    pergunta = payload.pergunta.strip()
    if not pergunta:
        raise HTTPException(422, "A pergunta não pode ser vazia.")
    if (payload.data_ini is None) != (payload.data_fim is None):
        raise HTTPException(422, "Informe as datas inicial e final da análise.")
    if payload.data_ini and payload.data_fim and payload.data_ini > payload.data_fim:
        raise HTTPException(422, "A data inicial deve ser anterior à data final.")
    area = (
        _classificar_area(pergunta, payload.escopo_tipo)
        if payload.modo == "auto"
        else payload.modo
    )
    try:
        if area == "financeiro":
            import asyncio
            from app.financeiro.agente_financeiro import construir_contexto, responder_pergunta

            data_fim = payload.data_fim or date.today()
            data_ini = payload.data_ini or (data_fim - timedelta(days=90))
            if data_ini > data_fim:
                raise HTTPException(422, "A data inicial deve ser anterior à data final.")
            contexto = await asyncio.to_thread(
                construir_contexto, data_ini, data_fim,
                pergunta=pergunta, segmento=_split(payload.segmentos),
                regional=_split(payload.regionais), cgc=payload.cgc,
                status=_split(payload.status), motivo=_split(payload.motivos),
            )
            resposta = await asyncio.to_thread(
                responder_pergunta, pergunta, contexto, payload.historico
            )
            return {"resposta": resposta, "area": area}

        if area == "demanda" and not payload.escopo_tipo:
            return {
                "area": area,
                "resposta": (
                    "Identifiquei uma pergunta de **Demanda**. Selecione o SKU ou a "
                    "categoria no campo **Escopo da análise** para eu consultar a base correta."
                ),
            }

        resposta = pd.responder_pergunta(
            db, area, pergunta,
            payload.escopo_tipo, payload.escopo_id, payload.historico,
            data_ini=payload.data_ini, data_fim=payload.data_fim,
        )
        return {**resposta, "area": area}
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erro no assistente: {e}")


@router.get("/buscar")
async def buscar(
    q: str = Query(..., min_length=1, max_length=80),
    db: Session = Depends(get_db),
    _: dict = Depends(require_nexus_bot),
):
    """Autocomplete de SKU/categoria para o modo Demanda do chat."""
    return pd.buscar_itens(db, q)


@router.get("/avaliacao")
async def avaliacao(
    tipo: str = Query(..., regex="^(sku|categoria)$"),
    id:   str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _: dict = Depends(require_nexus_bot),
):
    """
    Avaliacao gerada em lote no ultimo ciclo novo, para o bloco de texto
    do dossie (Dossieinferior.tsx). Nao gera na hora — so le o cache.
    Se ainda nao foi gerada para este item/ciclo, avaliacao vem None e o
    front mostra o estado "ainda nao avaliado".
    """
    r = pd.avaliacao_existente(db, tipo, id)
    return r or {"avaliacao": None, "gerado_em": None, "ciclo": None}