"""
=====================================================================
ROUTER ASSISTENTE — chat unico (Indicadores + Demanda)
=====================================================================
Destino: app/api/routers/router_assistente.py

Substitui o chat que existia em router_kpis.py. Um unico endpoint,
dois modos de contexto:
  'indicadores' -> agente_kpis (portfolio, ciclo ativo)
  'demanda'     -> planejador_demanda (1 sku ou 1 categoria)

Consumido pelo painel de chat flutuante na Sidebar, nao mais por
nenhuma tela especifica.
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers import planejador_demanda as pd

router = APIRouter(prefix="/api/v1/assistente", tags=["Assistente"])


class PerguntaAssistente(BaseModel):
    pergunta:    str
    modo:        str                    # 'indicadores' | 'demanda'
    escopo_tipo: Optional[str] = None   # 'sku' | 'categoria' (so modo=demanda)
    escopo_id:   Optional[str] = None   # codigo do sku ou nome da categoria
    historico:   Optional[List[dict]] = None


@router.post("/chat")
async def chat(
    payload: PerguntaAssistente,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    if payload.modo not in ("indicadores", "demanda"):
        raise HTTPException(400, "Campo 'modo' precisa ser 'indicadores' ou 'demanda'.")
    if payload.modo == "demanda" and payload.escopo_tipo not in ("sku", "categoria"):
        raise HTTPException(
            400,
            "Modo 'demanda' exige escopo_tipo='sku' ou 'categoria' e escopo_id "
            "preenchido — selecione um item na busca antes de perguntar."
        )
    try:
        return pd.responder_pergunta(
            db, payload.modo, payload.pergunta,
            payload.escopo_tipo, payload.escopo_id, payload.historico,
        )
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro no assistente: {e}")


@router.get("/buscar")
async def buscar(
    q: str = Query(..., min_length=1, max_length=80),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Autocomplete de SKU/categoria para o modo Demanda do chat."""
    return pd.buscar_itens(db, q)


@router.get("/avaliacao")
async def avaliacao(
    tipo: str = Query(..., regex="^(sku|categoria)$"),
    id:   str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """
    Avaliacao gerada em lote no ultimo ciclo novo, para o bloco de texto
    do dossie (Dossieinferior.tsx). Nao gera na hora — so le o cache.
    Se ainda nao foi gerada para este item/ciclo, avaliacao vem None e o
    front mostra o estado "ainda nao avaliado".
    """
    r = pd.avaliacao_existente(db, tipo, id)
    return r or {"avaliacao": None, "gerado_em": None, "ciclo": None}