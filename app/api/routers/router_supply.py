# -*- coding: utf-8 -*-
import pandas as pd
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db, engine
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import get_current_cycle, get_projection_window

router = APIRouter(prefix="/api/v1/consensus/supply", tags=["Supply"])

MOTIVOS_VALIDOS = {
    "capacidade_producao", "ruptura_mp", "restricao_linha",
    "estrategia_estoque", "sazonalidade", "outros",
}


def require_supply_or_admin(usuario: dict = Depends(get_current_user)):
    if (usuario.get("funcao") or "") not in ("Administrador", "Supply Chain"):
        raise HTTPException(status_code=403, detail="Acesso restrito ao time de Supply Chain.")
    return usuario


class AjusteSupply(BaseModel):
    produto: str
    mes_projetado: str
    novo_volume: int
    motivo: Optional[str] = None
    justificativa: Optional[str] = None


class PayloadSalvar(BaseModel):
    ajustes: List[AjusteSupply]


def _garantir_colunas(db: Session):
    # Garante que as colunas de justificativa estruturada existem.
    db.execute(text("""
        ALTER TABLE fato_ibp_granular
        ADD COLUMN IF NOT EXISTS justificativa_supply TEXT,
        ADD COLUMN IF NOT EXISTS motivo_supply VARCHAR(40)
    """))
    db.commit()


def _metas_congelado(db: Session, ciclo: str) -> bool:
    st = db.execute(text("""
        SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Metas'
    """), {"c": ciclo}).scalar()
    return st == 'CONGELADO'


def _supply_congelado(db: Session, ciclo: str) -> bool:
    st = db.execute(text("""
        SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Supply'
    """), {"c": ciclo}).scalar()
    return st == 'CONGELADO'


def _maior_resto(total: int, pesos: List[float]) -> List[int]:
    n = len(pesos)
    if n == 0:
        return []
    soma = sum(pesos)
    if soma <= 0:
        base = total // n
        resto = total - base * n
        p = [base] * n
        for i in range(resto):
            p[i] += 1
        return p
    dist = [(p / soma) * total for p in pesos]
    piso = [int(x) for x in dist]
    frac = [dist[i] - piso[i] for i in range(n)]
    sobra = total - sum(piso)
    ordem = sorted(range(n), key=lambda i: frac[i], reverse=True)
    for k in range(sobra):
        piso[ordem[k]] += 1
    return piso


@router.get("/status")
def status_supply(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    ciclo = get_current_cycle(db)
    return {
        "ciclo_ativo": ciclo,
        "metas_congelado": _metas_congelado(db, ciclo),
        "supply_congelado": _supply_congelado(db, ciclo),
    }


@router.get("")
def listar_supply(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    ciclo = get_current_cycle(db)
    _garantir_colunas(db)

    meses = get_projection_window(db, ciclo)
    if len(meses) < 3:
        raise HTTPException(status_code=500, detail="Janela de projeção incompleta.")
    m2, m3, m4 = meses[0], meses[1], meses[2]

    meses_cols = [
        {"mes_banco": m, "mes_str": pd.to_datetime(m).strftime("%b/%y").capitalize()}
        for m in (m2, m3, m4)
    ]

    # Reconsolida por SKU: o Supply pensa em produção (por produto), somando
    # todos os clientes. vol_meta = o que o comercial pediu; vol_supply = o que
    # a fábrica entrega; pmv micro para faturamento correto.
    sql = """
        SELECT
            f.sku,
            COALESCE(NULLIF(TRIM(p.descricao), ''), f.sku) AS descricao,
            COALESCE(NULLIF(TRIM(p.categoria), ''), 'SEM CATEGORIA') AS categoria,
            f.mes_projetado::DATE AS mes,
            SUM(f.vol_meta) AS vol_meta,
            SUM(f.vol_supply) AS vol_supply,
            SUM(f.vol_meta * f.pmv_aplicado) AS fat_meta,
            SUM(f.vol_supply * f.pmv_aplicado) AS fat_supply,
            MAX(f.motivo_supply) AS motivo,
            MAX(f.justificativa_supply) AS justificativa
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON f.sku = p.sku
        WHERE f.ciclo_sop = :ciclo AND f.mes_projetado IN (:m2, :m3, :m4)
        GROUP BY f.sku, p.descricao, p.categoria, f.mes_projetado
    """
    df = pd.read_sql(text(sql), engine, params={"ciclo": ciclo, "m2": m2, "m3": m3, "m4": m4})

    if df.empty:
        return {"ciclo_ativo": ciclo, "dados": [], "meses": [],
                "metas_congelado": _metas_congelado(db, ciclo),
                "supply_congelado": _supply_congelado(db, ciclo)}

    df["mes"] = pd.to_datetime(df["mes"])

    def bloco(sub: pd.DataFrame) -> List[dict]:
        out = []
        for mc in meses_cols:
            r = sub[sub["mes"] == pd.to_datetime(mc["mes_banco"])]
            vol_meta = int(r["vol_meta"].sum())
            # Se o Supply ainda não interveio (vol_supply==0 e existe meta),
            # a fábrica enxerga a Meta como alvo inicial.
            vol_sup_raw = int(r["vol_supply"].sum())
            vol_supply = vol_sup_raw if vol_sup_raw > 0 else vol_meta
            fat_meta = float(r["fat_meta"].sum())
            fat_supply = float(r["fat_supply"].sum()) if vol_sup_raw > 0 else fat_meta
            motivo = None
            justif = None
            if not r.empty:
                motivo = r["motivo"].iloc[0]
                justif = r["justificativa"].iloc[0]
            out.append({
                "mes_banco": mc["mes_banco"], "mes_str": mc["mes_str"],
                "vol_meta": vol_meta, "vol_supply": vol_supply,
                "fat_meta": round(fat_meta, 2), "fat_supply": round(fat_supply, 2),
                "gap": vol_supply - vol_meta,
                "motivo": motivo, "justificativa": justif,
            })
        return out

    arvore = []
    for categoria, cat_df in df.groupby("categoria"):
        cat_node = {"tipo": "categoria", "nome": categoria, "chave": f"CAT|{categoria}",
                    "meses": bloco(cat_df), "subRows": []}
        for sku, s_df in cat_df.groupby("sku"):
            desc = s_df["descricao"].iloc[0]
            sku_node = {"tipo": "produto", "nome": desc, "produto": sku,
                        "chave": f"SKU|{sku}", "meses": bloco(s_df)}
            cat_node["subRows"].append(sku_node)
        arvore.append(cat_node)

    return {
        "ciclo_ativo": ciclo,
        "meses": meses_cols,
        "dados": sorted(arvore, key=lambda x: x["nome"]),
        "metas_congelado": _metas_congelado(db, ciclo),
        "supply_congelado": _supply_congelado(db, ciclo),
    }


def _aplicar_ajustes(db: Session, ciclo: str, ajustes: List[AjusteSupply]):
    # Rateia cada ajuste de SKU para os clientes pela proporção de vol_meta
    # (respeita as prioridades comerciais do Consenso). Grava justificativa
    # estruturada por SKU×mês. Não congela — só persiste os números.
    for aj in ajustes:
        if aj.motivo and aj.motivo not in MOTIVOS_VALIDOS:
            raise HTTPException(status_code=400, detail=f"Motivo inválido: {aj.motivo}")

        linhas = db.execute(text("""
            SELECT id, COALESCE(vol_meta, 0) AS vol_meta
            FROM fato_ibp_granular
            WHERE ciclo_sop = :c AND sku = :sku AND mes_projetado = :mes
            ORDER BY id
        """), {"c": ciclo, "sku": aj.produto, "mes": aj.mes_projetado}).fetchall()
        if not linhas:
            continue

        pesos = [float(l.vol_meta) for l in linhas]
        partes = _maior_resto(int(aj.novo_volume), pesos)

        for l, parte in zip(linhas, partes):
            db.execute(text("""
                UPDATE fato_ibp_granular
                SET vol_supply = :v, motivo_supply = :mot, justificativa_supply = :jus
                WHERE id = :id
            """), {"v": int(parte), "mot": aj.motivo, "jus": aj.justificativa, "id": l.id})


@router.post("/salvar")
def salvar_supply(payload: PayloadSalvar, db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    ciclo = get_current_cycle(db)

    if _supply_congelado(db, ciclo):
        raise HTTPException(status_code=403, detail="A etapa de Supply já foi congelada.")
    # Precedência: Metas (Consenso) tem de estar congelado. Admin fura.
    if usuario.get("funcao") != "Administrador" and not _metas_congelado(db, ciclo):
        raise HTTPException(status_code=403, detail="O Consenso (Metas) ainda não foi publicado. Aguarde a etapa anterior.")

    try:
        _aplicar_ajustes(db, ciclo, payload.ajustes)
        db.commit()
        return {"status": "sucesso", "mensagem": "Ajustes de Supply salvos e rateados."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {e}")


@router.post("/congelar")
def congelar_supply(payload: PayloadSalvar, db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    ciclo = get_current_cycle(db)

    if _supply_congelado(db, ciclo):
        raise HTTPException(status_code=423, detail="A etapa de Supply já está congelada.")
    if usuario.get("funcao") != "Administrador" and not _metas_congelado(db, ciclo):
        raise HTTPException(status_code=403, detail="O Consenso (Metas) ainda não foi publicado.")

    try:
        # Aplica ajustes pendentes (se houver) antes de congelar.
        if payload.ajustes:
            _aplicar_ajustes(db, ciclo, payload.ajustes)

        # Onde o Supply não interveio (vol_supply == 0), assume a Meta como
        # entrega (a fábrica aceitou o número comercial).
        db.execute(text("""
            UPDATE fato_ibp_granular
            SET vol_supply = vol_meta
            WHERE ciclo_sop = :c AND (vol_supply IS NULL OR vol_supply = 0)
        """), {"c": ciclo})

        # Propaga vol_supply -> vol_final (partida da última etapa).
        db.execute(text("""
            UPDATE fato_ibp_granular SET vol_final = vol_supply WHERE ciclo_sop = :c
        """), {"c": ciclo})

        # Grava a trava de etapa Supply/CONGELADO.
        ja = db.execute(text("""
            SELECT id FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Supply'
        """), {"c": ciclo}).scalar()
        if ja:
            db.execute(text("UPDATE controle_ciclos SET status='CONGELADO' WHERE id = :id"), {"id": ja})
        else:
            db.execute(text("""
                INSERT INTO controle_ciclos (ciclo_sop, origem, status) VALUES (:c, 'Supply', 'CONGELADO')
            """), {"c": ciclo})

        db.commit()
        return {"status": "sucesso", "mensagem": "Etapa de Supply congelada. Bastão passado ao Final."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao congelar: {e}")


@router.post("/reabrir")
def reabrir_supply(db: Session = Depends(get_db), usuario: dict = Depends(require_supply_or_admin)):
    ciclo = get_current_cycle(db)
    try:
        db.execute(text("""
            DELETE FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Supply'
        """), {"c": ciclo})
        db.commit()
        return {"status": "sucesso", "mensagem": "Etapa de Supply reaberta."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao reabrir: {e}")