"""
=====================================================================
ROUTER — NPD / INOVAÇÕES (injeção de lançamento)  [reconstrução]
=====================================================================
Injeta um produto NOVO (sem histórico) no ciclo, usando um SKU "espelho" para
herdar o padrão de distribuição entre clientes. Acesso: Admin, Marketing.

Premissas aplicadas (aprendidas):
  • RATEIO CANÔNICO: distribui o volume nacional entre os clientes do espelho
    com ratear_maior_resto — a soma bate SEMPRE (o antigo usava int(round())
    por cliente, e a soma não fechava).
  • ZERO É ZERO: mês com volume 0 grava 0 em todas as linhas (sem 'continue').
  • MESES DA JANELA DO CICLO: usa get_working_window_months (não datetime.today).
  • NASCE CONSOLIDADO: grava as 6 colunas de volume iguais (o produto novo já
    entra alinhado em todas as etapas).
  • IMUTABILIDADE: não injeta em mês já realizado.
  • curva = 'LANÇAMENTO'.

Contrato do front (mantido):
  GET  /espelhos -> {espelhos:[{produto,descricao}], categorias:[], segmentos:[]}
  POST /injetar  -> {codigo_lancamento, nome_lancamento, sku_espelho, categoria,
                     segmento, pmv, baseline, projecao:[{mes,percentual,volume,receita}]}
"""

import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_working_window_months, ratear_maior_resto,
    check_imutabilidade_mes, parse_date_safe, registrar_log_auditoria,
)

router = APIRouter(prefix="/api/v1/npd", tags=["NPD / Inovações"])


def require_marketing(usuario: dict = Depends(get_current_user)):
    if usuario.get("funcao") not in ("Administrador", "Marketing"):
        raise HTTPException(403, "Acesso restrito à Diretoria de Marketing.")
    return usuario


class ProjecaoMes(BaseModel):
    mes: str
    percentual: float
    volume: int
    receita: float

class PayloadInjetar(BaseModel):
    codigo_lancamento: str
    nome_lancamento: str
    sku_espelho: str
    categoria: str
    segmento: str
    pmv: float
    baseline: int
    projecao: List[ProjecaoMes]


@router.get("/espelhos")
def espelhos(db: Session = Depends(get_db), _: dict = Depends(require_marketing)):
    """Lista SKUs disponíveis como espelho + categorias e segmentos existentes."""
    produtos = db.execute(text("""
        SELECT sku, COALESCE(descricao, sku) AS descricao
        FROM dim_produtos ORDER BY descricao
    """)).fetchall()
    categorias = db.execute(text("""
        SELECT DISTINCT categoria FROM dim_produtos
        WHERE categoria IS NOT NULL AND TRIM(categoria) <> '' ORDER BY categoria
    """)).fetchall()
    segmentos = db.execute(text("""
        SELECT DISTINCT segmento FROM dim_produtos
        WHERE segmento IS NOT NULL AND TRIM(segmento) <> '' ORDER BY segmento
    """)).fetchall()
    return {
        "espelhos": [{"produto": p.sku, "descricao": p.descricao} for p in produtos],
        "categorias": [c.categoria for c in categorias],
        "segmentos": [s.segmento for s in segmentos],
    }


@router.post("/injetar")
def injetar(payload: PayloadInjetar, db: Session = Depends(get_db),
            usuario: dict = Depends(require_marketing)):
    """
    Nasce o produto novo no ciclo, rateando cada mês da projeção entre os
    clientes do espelho (peso pelo histórico vol_ia do espelho), com balanço
    garantido. Grava as 6 colunas de volume iguais (nasce consolidado).
    """
    try:
        ciclo = get_current_cycle(db)
        nome_user = usuario.get("nome", usuario.get("email", "?"))

        # Já existe esse SKU no ciclo? (evita duplicar lançamento)
        existe = db.execute(text("""
            SELECT 1 FROM fato_ibp_granular
            WHERE ciclo_sop = :c AND sku = :s LIMIT 1
        """), {"c": ciclo, "s": payload.codigo_lancamento}).fetchone()
        if existe:
            raise HTTPException(409, f"O lançamento {payload.codigo_lancamento} já existe no ciclo {ciclo}.")

        # Clientes do espelho, com peso pelo histórico (vol_ia somado no ciclo).
        clientes = db.execute(text("""
            SELECT cgc, MAX(vendedor_nome) AS vendedor_nome,
                   SUM(COALESCE(vol_ia,0)) AS peso
            FROM fato_ibp_granular
            WHERE sku = :esp AND ciclo_sop = :c
            GROUP BY cgc
        """), {"esp": payload.sku_espelho, "c": ciclo}).fetchall()

        # Fallback: se o espelho não tem linhas no ciclo, tenta em qualquer ciclo.
        if not clientes:
            clientes = db.execute(text("""
                SELECT cgc, MAX(vendedor_nome) AS vendedor_nome,
                       SUM(COALESCE(vol_ia,0)) AS peso
                FROM fato_ibp_granular WHERE sku = :esp GROUP BY cgc
            """), {"esp": payload.sku_espelho}).fetchall()
        if not clientes:
            raise HTTPException(404, f"O SKU espelho {payload.sku_espelho} não tem clientes para herdar a distribuição.")

        pesos = [max(0.0, float(c.peso or 0)) for c in clientes]
        pmv = float(payload.pmv)

        # 'bu' (unidade de negócio) herdada do SKU espelho — faz sentido de
        # negócio e cobre a coluna caso seja NOT NULL.
        bu_espelho = db.execute(text("""
            SELECT bu FROM dim_produtos WHERE sku = :esp
        """), {"esp": payload.sku_espelho}).scalar()

        # Garante dim_produtos do novo SKU (categoria/segmento/curva LANÇAMENTO).
        db.execute(text("""
            INSERT INTO dim_produtos (sku, descricao, bu, categoria, segmento, curva, ativo)
            VALUES (:s, :d, :bu, :cat, :seg, 'LANÇAMENTO', true)
            ON CONFLICT (sku) DO UPDATE
              SET descricao=:d, bu=:bu, categoria=:cat, segmento=:seg,
                  curva='LANÇAMENTO', ativo=true
        """), {"s": payload.codigo_lancamento, "d": payload.nome_lancamento,
               "bu": bu_espelho, "cat": payload.categoria, "seg": payload.segmento})

        linhas_criadas = 0
        for proj in payload.projecao:
            data_alvo = parse_date_safe(proj.mes if len(proj.mes) > 7 else proj.mes + "-01")
            check_imutabilidade_mes(data_alvo, payload.codigo_lancamento, contexto="lançamento")

            volume_nacional = int(proj.volume)   # zero é zero: não pula
            partes = ratear_maior_resto(volume_nacional, pesos)
            if sum(partes) != volume_nacional:
                raise HTTPException(500, f"Falha de balanço no lançamento em {proj.mes}.")

            for c, parte in zip(clientes, partes):
                v = int(parte)
                # Nasce consolidado: as 6 colunas iguais.
                db.execute(text("""
                    INSERT INTO fato_ibp_granular
                      (ciclo_sop, mes_projetado, sku, cgc, vendedor_nome,
                       vol_ia, vol_topdown, vol_bottomup, vol_meta, vol_supply, vol_final,
                       pmv_aplicado)
                    VALUES (:c, :m, :s, :cgc, :vend,
                            :v, :v, :v, :v, :v, :v, :pmv)
                """), {"c": ciclo, "m": data_alvo, "s": payload.codigo_lancamento,
                       "cgc": c.cgc, "vend": c.vendedor_nome,
                       "v": v, "pmv": pmv})
                linhas_criadas += 1

            registrar_log_auditoria(
                db=db, ciclo=ciclo, origem="NPD / Inovações", usuario=nome_user,
                sku=payload.codigo_lancamento, cliente="TODOS_OS_CLIENTES",
                mes=data_alvo, v_antigo=0, v_novo=volume_nacional)

        db.commit()
        return {"status": "success", "sku": payload.codigo_lancamento,
                "linhas_criadas": linhas_criadas, "clientes": len(clientes)}
    except HTTPException:
        db.rollback(); raise
    except Exception as e:
        db.rollback(); raise HTTPException(500, repr(e))