"""
=====================================================================
ROUTER CARTEIRA — ETAPA CONSENSO / METAS (vol_meta)
=====================================================================
Onde o número vira compromisso com nome e sobrenome. Coordenadores e
Gerentes definem a meta da sua carteira, por VOLUME (caixas) ou por
VALOR (R$), no nível cliente×SKU, dentro da sua hierarquia.

Apoia-se INTEIRAMENTE na fundação rls_metas para tudo que é sensível:
escopo (quem vê o quê), validação de escrita linha a linha, e cadeados
por responsável com precedência hierárquica.

Regras-chave desta etapa:
  • RLS por hierarquia: Gerente vê sua gerência; Coordenador sua coordenação.
  • Edição em qualquer nível da árvore -> rateio para baixo (Maior Resto).
  • Input em caixas OU reais; reais converte via PMV micro por linha.
  • Cadeado individual por responsável; superior reabre; Admin publica a etapa.
  • A proporção de vol_meta desenhada aqui é a que Supply/Final herdam.
"""

import pandas as pd
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db, engine
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle, get_projection_window,
)
from app.api.routers.rls_metas import (
    exigir_acesso_metas, escopo_usuario, clausula_rls,
    validar_escrita, travar_cadeado, reabrir_cadeado,
    esta_congelado_para_usuario, etapa_metas_bloqueada,
    garantir_tabela_cadeados, cadeado_do_responsavel,
    NIVEL_ADMIN, NIVEL_GERENTE, NIVEL_COORDENADOR,
)

router = APIRouter(prefix="/api/v1/consensus/micro", tags=["Consenso / Metas"])


# =====================================================================
# SCHEMAS
# =====================================================================
class AjusteMeta(BaseModel):
    # Edição em UMA linha granular (id de fato_ibp_granular) ou por nó agregado.
    # O front envia ids das folhas afetadas + o novo volume já rateado, OU
    # envia um nó agregado e deixa o backend ratear. Suportamos os dois:
    ids_folhas: Optional[List[int]] = None      # folhas explícitas (cliente×sku)
    novo_volume: Optional[int] = None           # novo total em CAIXAS
    novo_valor_rs: Optional[float] = None        # novo total em R$ (alternativo)
    mes_projetado: str


class PayloadSalvarMetas(BaseModel):
    ajustes: List[AjusteMeta]


class PayloadCongelar(BaseModel):
    # Coordenador/Gerente congela a si; Gerente pode congelar um coordenador.
    nome_alvo: Optional[str] = None
    nivel_alvo: Optional[str] = None


class PayloadReabrir(BaseModel):
    nome_alvo: str


# =====================================================================
# HELPERS
# =====================================================================
def _maior_resto(total: int, pesos: List[float]) -> List[int]:
    """Distribui 'total' inteiro entre N posições por pesos, soma EXATA."""
    n = len(pesos)
    if n == 0:
        return []
    soma = sum(pesos)
    if soma <= 0:
        base = total // n
        resto = total - base * n
        partes = [base] * n
        for i in range(resto):
            partes[i] += 1
        return partes
    dist = [(p / soma) * total for p in pesos]
    piso = [int(x) for x in dist]
    frac = [dist[i] - piso[i] for i in range(n)]
    sobra = total - sum(piso)
    ordem = sorted(range(n), key=lambda i: frac[i], reverse=True)
    for k in range(sobra):
        piso[ordem[k]] += 1
    return piso


def _mapa_coordenadores_do_gerente(db: Session, gerente_nome: str) -> set:
    """Coordenadores (supervisor_nome) que pertencem a uma gerência."""
    rows = db.execute(text("""
        SELECT DISTINCT TRIM(supervisor_nome) AS coord
        FROM dim_clientes
        WHERE TRIM(gerente_nome) = :g AND supervisor_nome IS NOT NULL AND TRIM(supervisor_nome) != ''
    """), {"g": gerente_nome.strip()}).fetchall()
    return {r.coord for r in rows}


# =====================================================================
# FILTROS (opções de navegação conforme escopo)
# =====================================================================
@router.get("/filtros")
async def filtros_micro(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)

    rls = clausula_rls(escopo, alias_cli="c")
    sql = f"""
        SELECT
            COALESCE(NULLIF(TRIM(c.gerente_nome), ''), 'SEM GERENTE')     AS gerente,
            COALESCE(NULLIF(TRIM(c.supervisor_nome), ''), 'SEM COORDENADOR') AS coordenador,
            COALESCE(NULLIF(TRIM(f.vendedor_nome), ''), 'SEM VENDEDOR')   AS vendedor
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        WHERE f.ciclo_sop = :ciclo AND {rls['where']}
        GROUP BY 1,2,3
    """
    params = {"ciclo": ciclo, **rls["params"]}
    df = pd.read_sql(text(sql), engine, params=params)

    return {
        "gerentes": sorted(df["gerente"].unique().tolist()),
        "coordenadores": sorted(df["coordenador"].unique().tolist()),
        "vendedores": sorted(df["vendedor"].unique().tolist()),
        "meu_nivel": escopo["funcao"],
        "meu_nome": escopo["nome_responsavel"],
    }


# =====================================================================
# DADOS (árvore hierárquica filtrada por escopo)
# =====================================================================
@router.get("")
def get_dados_metas(
    nome_responsavel: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    usuario: dict = Depends(get_current_user)
):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    garantir_tabela_cadeados(db)

    meses = get_projection_window(db, ciclo)
    if len(meses) < 3:
        raise HTTPException(status_code=500, detail="Janela de projeção incompleta.")
    m2, m3, m4 = meses[0], meses[1], meses[2]

    # RLS base do usuário.
    rls = clausula_rls(escopo, alias_cli="c")
    params: Dict[str, Any] = {"ciclo": ciclo, "m2": m2, "m3": m3, "m4": m4, **rls["params"]}

    # Filtro adicional opcional (Admin/Gerente escolhem um responsável p/ focar).
    filtro_resp = ""
    if nome_responsavel and nome_responsavel not in ("", "TODOS"):
        filtro_resp = " AND (TRIM(c.gerente_nome) = :resp OR TRIM(c.supervisor_nome) = :resp OR TRIM(f.vendedor_nome) = :resp) "
        params["resp"] = nome_responsavel.strip()

    sql = f"""
        SELECT
            f.id,
            f.cgc,
            COALESCE(NULLIF(TRIM(c.gerente_nome), ''), 'SEM GERENTE')       AS gerente,
            COALESCE(NULLIF(TRIM(c.supervisor_nome), ''), 'SEM COORDENADOR') AS coordenador,
            COALESCE(NULLIF(TRIM(f.vendedor_nome), ''), 'SEM VENDEDOR')     AS vendedor,
            COALESCE(NULLIF(TRIM(c.razaosocial), ''), 'SEM NOME')          AS cliente,
            f.sku,
            COALESCE(NULLIF(TRIM(p.descricao), ''), f.sku)                 AS descricao,
            f.mes_projetado::DATE AS mes,
            COALESCE(f.vol_meta, 0)      AS vol_meta,
            COALESCE(f.vol_bottomup, 0)  AS vol_base,
            COALESCE(f.pmv_aplicado, 0)  AS pmv
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        LEFT JOIN dim_produtos p ON f.sku = p.sku
        WHERE f.ciclo_sop = :ciclo
          AND f.mes_projetado IN (:m2, :m3, :m4)
          AND {rls['where']}
          {filtro_resp}
    """
    df = pd.read_sql(text(sql), engine, params=params)

    # ORÇAMENTO — por SKU×mês, somado UMA vez. Buscado à parte (nunca no JOIN
    # granular, senão multiplica pelo nº de clientes do SKU). Aplicado ao df de
    # forma que só a PRIMEIRA linha de cada SKU×mês carrega o valor; as demais
    # ficam zero. Assim qualquer soma por nó conta cada SKU uma única vez.
    orcamento_map: Dict[tuple, float] = {}
    if not df.empty:
        skus_carteira = df["sku"].unique().tolist()
        orc_rows = db.execute(text("""
            SELECT sku, DATE_TRUNC('month', mes_projetado)::DATE AS mes, SUM(receita_orcamento) AS rec_orc
            FROM fato_orcamento
            WHERE sku = ANY(:skus)
              AND DATE_TRUNC('month', mes_projetado)::DATE = ANY(CAST(:meses AS DATE[]))
            GROUP BY sku, DATE_TRUNC('month', mes_projetado)::DATE
        """), {"skus": skus_carteira, "meses": [m2, m3, m4]}).fetchall()
        for r in orc_rows:
            orcamento_map[(str(r.sku), r.mes.strftime("%Y-%m-%d"))] = float(r.rec_orc or 0)

    # Realizado do MESMO período no ano anterior (âncora histórica por cliente×sku).
    # Para cada mês projetado, busca vendas do mesmo mês 12 meses atrás.
    from dateutil.relativedelta import relativedelta as _rd
    meses_hist = {m: (pd.to_datetime(m) - _rd(months=12)).strftime("%Y-%m-%d") for m in [m2, m3, m4]}
    hist_rows = db.execute(text("""
        SELECT cgc, sku, DATE_TRUNC('month', data_pedido)::DATE AS mes, SUM(qt_pedido) AS vol_real
        FROM fato_vendas
        WHERE DATE_TRUNC('month', data_pedido)::DATE = ANY(CAST(:meses AS DATE[]))
        GROUP BY cgc, sku, DATE_TRUNC('month', data_pedido)::DATE
    """), {"meses": list(meses_hist.values())}).fetchall()
    # Mapa: (cgc, sku, mes_projetado_atual) -> vol_real do ano anterior.
    hist_inv = {v: k for k, v in meses_hist.items()}  # mes_hist -> mes_atual
    realizado_map: Dict[tuple, float] = {}
    for r in hist_rows:
        mes_hist_str = r.mes.strftime("%Y-%m-%d")
        mes_atual = hist_inv.get(mes_hist_str)
        if mes_atual:
            realizado_map[(str(r.cgc), str(r.sku), mes_atual)] = float(r.vol_real or 0)

    if df.empty:
        return {
            "ciclo_ativo": ciclo, "dados": [], "meses": [],
            "meu_congelamento": "ABERTO", "etapa_bloqueada": etapa_metas_bloqueada(db, ciclo),
            "escopo": {"nivel": escopo["funcao"], "nome": escopo["nome_responsavel"]},
        }

    df["mes"] = pd.to_datetime(df["mes"])
    meses_cols = [
        {"mes_banco": m2, "mes_str": pd.to_datetime(m2).strftime("%b/%y").capitalize()},
        {"mes_banco": m3, "mes_str": pd.to_datetime(m3).strftime("%b/%y").capitalize()},
        {"mes_banco": m4, "mes_str": pd.to_datetime(m4).strftime("%b/%y").capitalize()},
    ]

    # Anexa o realizado histórico (ano anterior) a cada linha do df, por chave.
    df["realizado_hist"] = df.apply(
        lambda r: realizado_map.get((str(r["cgc"]), str(r["sku"]), r["mes"].strftime("%Y-%m-%d")), 0.0),
        axis=1
    )

    # =================================================================
    # MONTAGEM OTIMIZADA DA ÁRVORE (sem refiltrar o DataFrame por nó).
    # Antes: bloco_meses(sub) refiltrava df em CADA nó (O(nós × linhas)),
    # inviável para Admin (empresa inteira). Agora pré-agregamos com groupby
    # vetorizado uma vez e montamos a árvore lendo de dicionários indexados.
    # =================================================================
    df["fat_meta"] = df["vol_meta"] * df["pmv"]
    df["fat_com"] = df["vol_base"] * df["pmv"]
    df["fat_hist"] = df["realizado_hist"] * df["pmv"]
    df["mes_str_k"] = df["mes"].dt.strftime("%Y-%m-%d")

    NIVEIS = ["gerente", "coordenador", "vendedor", "cliente", "sku"]

    def agrega_por(chaves: List[str]) -> pd.DataFrame:
        # Uma única agregação vetorizada para um nível da árvore.
        g = df.groupby(chaves + ["mes_str_k"], sort=False).agg(
            vol_meta=("vol_meta", "sum"),
            fat_meta=("fat_meta", "sum"),
            vol_com=("vol_base", "sum"),
            fat_com=("fat_com", "sum"),
            vol_hist=("realizado_hist", "sum"),
            fat_hist=("fat_hist", "sum"),
        ).reset_index()
        return g

    # Pré-computa os agregados de cada nível de uma vez (5 groupbys totais,
    # em vez de dezenas de milhares de filtragens).
    agg_cache: Dict[tuple, Dict[str, dict]] = {}
    for i in range(len(NIVEIS)):
        chaves = NIVEIS[: i + 1]
        gdf = agrega_por(chaves)
        for row in gdf.itertuples(index=False):
            keyvals = tuple(getattr(row, c) for c in chaves)
            mkey = row.mes_str_k
            agg_cache.setdefault((tuple(chaves), keyvals), {})[mkey] = {
                "vol_meta": int(row.vol_meta),
                "fat_meta": round(float(row.fat_meta), 2),
                "vol_comercial": int(row.vol_com),
                "fat_comercial": round(float(row.fat_com), 2),
                "vol_hist": int(row.vol_hist),
                "fat_hist": round(float(row.fat_hist), 2),
            }

    # Orçamento por nó = soma do orçamento dos SKUs DISTINTOS do nó (cada SKU
    # uma vez), por mês. Pré-computa o conjunto de SKUs de cada nó.
    orc_cache: Dict[tuple, Dict[str, float]] = {}
    for i in range(len(NIVEIS)):
        chaves = NIVEIS[: i + 1]
        skus_por_no = df.groupby(chaves, sort=False)["sku"].unique()
        for keyvals, skus in skus_por_no.items():
            kv = keyvals if isinstance(keyvals, tuple) else (keyvals,)
            por_mes = {}
            for mc in meses_cols:
                mk = mc["mes_banco"]
                por_mes[mk] = round(sum(orcamento_map.get((str(s), mk), 0.0) for s in skus), 2)
            orc_cache[(tuple(chaves), kv)] = por_mes

    def bloco_por_chave(chaves: List[str], keyvals: tuple) -> List[dict]:
        pormes = agg_cache.get((tuple(chaves), keyvals), {})
        orcmes = orc_cache.get((tuple(chaves), keyvals), {})
        out = []
        for mc in meses_cols:
            d = pormes.get(mc["mes_banco"], {})
            out.append({
                "mes_banco": mc["mes_banco"], "mes_str": mc["mes_str"],
                "vol_meta": d.get("vol_meta", 0), "fat_meta": d.get("fat_meta", 0.0),
                "vol_comercial": d.get("vol_comercial", 0), "fat_comercial": d.get("fat_comercial", 0.0),
                "rec_orcada": orcmes.get(mc["mes_banco"], 0.0),
                "vol_hist": d.get("vol_hist", 0), "fat_hist": d.get("fat_hist", 0.0),
            })
        return out

    # Folhas (id, pmv, vol_meta) por SKU×mês — uma passada, indexada.
    folhas_idx: Dict[tuple, Dict[str, list]] = {}
    for r in df.itertuples(index=False):
        chave_sku = (r.gerente, r.coordenador, r.vendedor, r.cliente, r.sku)
        folhas_idx.setdefault(chave_sku, {}).setdefault(r.mes_str_k, []).append(
            {"id": int(r.id), "pmv": float(r.pmv), "vol_meta": int(r.vol_meta)}
        )

    def folhas_do_sku(keyvals: tuple) -> Dict[str, list]:
        porm = folhas_idx.get(keyvals, {})
        return {mc["mes_banco"]: porm.get(mc["mes_banco"], []) for mc in meses_cols}

    # Estrutura hierárquica de chaves únicas (sem refiltrar df).
    from collections import OrderedDict
    hier: "OrderedDict" = OrderedDict()
    for r in df[["gerente", "coordenador", "vendedor", "cliente", "sku", "descricao"]].drop_duplicates().itertuples(index=False):
        hier.setdefault(r.gerente, OrderedDict()) \
            .setdefault(r.coordenador, OrderedDict()) \
            .setdefault(r.vendedor, OrderedDict()) \
            .setdefault(r.cliente, OrderedDict())[r.sku] = r.descricao

    arvore = []
    for gerente, coords in hier.items():
        g_node = {"tipo": "gerente", "nome": gerente, "chave": f"G|{gerente}",
                  "meses": bloco_por_chave(["gerente"], (gerente,)), "subRows": []}
        for coord, vends in coords.items():
            c_node = {"tipo": "coordenador", "nome": coord, "chave": f"C|{gerente}|{coord}",
                      "meses": bloco_por_chave(["gerente", "coordenador"], (gerente, coord)), "subRows": []}
            for vend, clis in vends.items():
                v_node = {"tipo": "vendedor", "nome": vend, "chave": f"V|{coord}|{vend}",
                          "meses": bloco_por_chave(["gerente", "coordenador", "vendedor"], (gerente, coord, vend)), "subRows": []}
                for cli, skus in clis.items():
                    cli_node = {"tipo": "cliente", "nome": cli, "chave": f"CLI|{vend}|{cli}",
                                "meses": bloco_por_chave(["gerente", "coordenador", "vendedor", "cliente"], (gerente, coord, vend, cli)), "subRows": []}
                    for sku, desc in skus.items():
                        sku_node = {
                            "tipo": "produto", "nome": desc, "produto": sku,
                            "chave": f"SKU|{cli}|{sku}",
                            "meses": bloco_por_chave(NIVEIS, (gerente, coord, vend, cli, sku)),
                            "folhas": folhas_do_sku((gerente, coord, vend, cli, sku)),
                        }
                        cli_node["subRows"].append(sku_node)
                    v_node["subRows"].append(cli_node)
                c_node["subRows"].append(v_node)
            g_node["subRows"].append(c_node)
        arvore.append(g_node)

    return {
        "ciclo_ativo": ciclo,
        "meses": meses_cols,
        "dados": arvore,
        "meu_congelamento": "CONGELADO" if esta_congelado_para_usuario(db, escopo, ciclo) else "ABERTO",
        "etapa_bloqueada": etapa_metas_bloqueada(db, ciclo),
        "escopo": {"nivel": escopo["funcao"], "nome": escopo["nome_responsavel"]},
    }


# =====================================================================
# SALVAR (rateio inteligente + validação de escrita linha a linha)
# =====================================================================
@router.post("/salvar")
async def salvar_metas(payload: PayloadSalvarMetas, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)

    # Trava de etapa: se o Admin já publicou Metas, ninguém edita.
    if etapa_metas_bloqueada(db, ciclo):
        raise HTTPException(status_code=403, detail="A etapa de Metas já foi publicada pelo Administrador. Edições encerradas.")

    # Cadeado individual: se a carteira do próprio usuário está congelada, bloqueia
    # (exceto Admin). Superior reabre antes de editar (endpoint /reabrir).
    if esta_congelado_para_usuario(db, escopo, ciclo):
        raise HTTPException(status_code=403, detail="Sua carteira está congelada. Reabra-a (ou peça a um superior) antes de editar.")

    # Coleta TODAS as folhas envolvidas para validar escrita de uma vez.
    todas_ids: List[int] = []
    for aj in payload.ajustes:
        if aj.ids_folhas:
            todas_ids.extend(aj.ids_folhas)

    # GARANTIA DE SEGURANÇA: toda linha alterada tem de estar no escopo.
    validar_escrita(db, escopo, todas_ids, ciclo)

    try:
        for aj in payload.ajustes:
            if not aj.ids_folhas:
                continue

            # Carrega as folhas (id, pmv, vol_meta atual) para ratear.
            rows = db.execute(text("""
                SELECT id, COALESCE(pmv_aplicado,0) AS pmv, COALESCE(vol_meta,0) AS vol_meta
                FROM fato_ibp_granular
                WHERE id = ANY(CAST(:ids AS BIGINT[])) AND ciclo_sop = :ciclo
                ORDER BY id
            """), {"ids": aj.ids_folhas, "ciclo": ciclo}).fetchall()
            if not rows:
                continue

            # Determina o novo total em CAIXAS.
            if aj.novo_volume is not None:
                novo_total_cx = int(round(aj.novo_volume))
                pesos_rateio = [float(r.vol_meta) for r in rows]
            elif aj.novo_valor_rs is not None:
                # Conversão financeira agregada. Para que o R$ digitado seja
                # PRESERVADO ao reconverter, distribui o valor pela proporção de
                # FATURAMENTO atual de cada folha (vol_meta*pmv), não de volume,
                # e converte cada fatia pelo PMV micro da folha. Assim
                # Σ(caixas_folha × pmv_folha) ≈ valor digitado (erro < 1 cx).
                fat_atual = [(float(r.vol_meta) * float(r.pmv)) for r in rows]
                soma_fat = sum(fat_atual)
                vols_eq: List[float] = []
                if soma_fat > 0:
                    for r, fa in zip(rows, fat_atual):
                        valor_folha = aj.novo_valor_rs * (fa / soma_fat)
                        vols_eq.append((valor_folha / float(r.pmv)) if r.pmv > 0 else 0.0)
                else:
                    # Sem faturamento base (tudo zerado): distribui valor por PMV
                    # igualmente entre folhas com preço válido.
                    validos = [r for r in rows if r.pmv > 0]
                    n = len(validos) if validos else 1
                    for r in rows:
                        vols_eq.append(((aj.novo_valor_rs / n) / float(r.pmv)) if r.pmv > 0 else 0.0)
                novo_total_cx = int(round(sum(vols_eq)))
                # O rateio final segue a proporção de volume equivalente recém-
                # calculada (que já embute a mistura de receita correta).
                pesos_rateio = vols_eq
            else:
                continue

            # Rateio para as folhas com Maior Resto (soma EXATA).
            partes = _maior_resto(novo_total_cx, pesos_rateio)

            for r, parte in zip(rows, partes):
                db.execute(
                    text("UPDATE fato_ibp_granular SET vol_meta = :v WHERE id = :id"),
                    {"v": int(parte), "id": r.id}
                )

        db.commit()
        return {"status": "sucesso", "mensagem": "Metas salvas e rateadas com soma exata."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao salvar metas: {e}")


# =====================================================================
# CONGELAR (cadeado individual por responsável)
# =====================================================================
@router.post("/congelar")
async def congelar_metas(payload: PayloadCongelar, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)

    if etapa_metas_bloqueada(db, ciclo):
        raise HTTPException(status_code=403, detail="A etapa de Metas já foi publicada. Não é possível congelar carteiras.")

    # Se um Gerente vai congelar EM NOME de um coordenador, valida que o
    # coordenador pertence à gerência dele (precedência hierárquica real).
    if payload.nome_alvo and escopo["funcao"] == NIVEL_GERENTE:
        coords = _mapa_coordenadores_do_gerente(db, escopo["nome_responsavel"])
        if payload.nome_alvo.strip() not in coords:
            raise HTTPException(status_code=403, detail="Este coordenador não pertence à sua gerência.")

    nome = travar_cadeado(db, escopo, ciclo, nome_alvo=payload.nome_alvo, nivel_alvo=payload.nivel_alvo)
    return {"status": "sucesso", "mensagem": f"Carteira de '{nome}' congelada."}


# =====================================================================
# REABRIR (precedência hierárquica)
# =====================================================================
@router.post("/reabrir")
async def reabrir_metas(payload: PayloadReabrir, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)

    if etapa_metas_bloqueada(db, ciclo):
        raise HTTPException(status_code=403, detail="A etapa de Metas já foi publicada. Reabra a etapa pelo painel do Administrador.")

    alvo = payload.nome_alvo.strip()

    # Determina se 'alvo' está abaixo do usuário (precedência).
    pertence = False
    if escopo["funcao"] == NIVEL_GERENTE:
        pertence = alvo in _mapa_coordenadores_do_gerente(db, escopo["nome_responsavel"])

    reabrir_cadeado(db, escopo, ciclo, alvo, pertence_ao_escopo=pertence)
    return {"status": "sucesso", "mensagem": f"Carteira de '{alvo}' reaberta."}


# =====================================================================
# STATUS DOS CADEADOS (para o painel do Gerente/Admin ver quem fechou)
# =====================================================================
@router.get("/cadeados")
def status_cadeados(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    escopo = exigir_acesso_metas(usuario)
    ciclo = get_current_cycle(db)
    garantir_tabela_cadeados(db)

    # Lista TODOS os responsáveis do ciclo (coordenadores e gerentes que têm
    # carteira), com o status de cadeado de cada um — ABERTO se ainda não
    # congelou. Assim o painel mostra o progresso completo desde o início,
    # não só quem já fechou.
    cadeados_existentes = {
        r.nome.strip(): r for r in db.execute(text("""
            SELECT TRIM(nome_responsavel) AS nome, nivel, status, congelado_por, data_congelamento
            FROM controle_metas_responsavel WHERE ciclo_sop = :c
        """), {"c": ciclo}).fetchall()
    }

    # Universo de responsáveis conforme o escopo. RESTRITO a quem tem carteira
    # no ciclo (existe em fato_ibp_granular do ciclo ativo) — não o dim_clientes
    # inteiro, senão aparecem responsáveis sem carteira no progresso.
    if escopo["ve_tudo"]:
        resp_rows = db.execute(text("""
            SELECT DISTINCT TRIM(c.gerente_nome) AS nome, 'Gerente' AS nivel
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            WHERE f.ciclo_sop = :ciclo AND c.gerente_nome IS NOT NULL AND TRIM(c.gerente_nome) != ''
            UNION
            SELECT DISTINCT TRIM(c.supervisor_nome) AS nome, 'Coordenador' AS nivel
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            WHERE f.ciclo_sop = :ciclo AND c.supervisor_nome IS NOT NULL AND TRIM(c.supervisor_nome) != ''
        """), {"ciclo": ciclo}).fetchall()
    elif escopo["funcao"] == NIVEL_GERENTE:
        # Coordenadores da gerência QUE TÊM carteira no ciclo.
        coord_rows = db.execute(text("""
            SELECT DISTINCT TRIM(c.supervisor_nome) AS nome
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            WHERE f.ciclo_sop = :ciclo AND TRIM(c.gerente_nome) = :g
              AND c.supervisor_nome IS NOT NULL AND TRIM(c.supervisor_nome) != ''
        """), {"ciclo": ciclo, "g": escopo["nome_responsavel"]}).fetchall()
        resp_rows = [type("R", (), {"nome": escopo["nome_responsavel"], "nivel": "Gerente"})()]
        resp_rows += [type("R", (), {"nome": r.nome, "nivel": "Coordenador"})() for r in coord_rows]
    else:
        resp_rows = [type("R", (), {"nome": escopo["nome_responsavel"], "nivel": "Coordenador"})()]

    cadeados = []
    for rr in resp_rows:
        nome = rr.nome.strip()
        existente = cadeados_existentes.get(nome)
        if existente:
            cadeados.append({
                "nome": nome, "nivel": existente.nivel or rr.nivel,
                "status": existente.status, "por": existente.congelado_por,
                "quando": existente.data_congelamento.strftime("%d/%m/%Y %H:%M") if existente.data_congelamento else None,
            })
        else:
            cadeados.append({"nome": nome, "nivel": rr.nivel, "status": "ABERTO", "por": None, "quando": None})

    # Ordena: gerentes primeiro, depois coordenadores, alfabético.
    cadeados.sort(key=lambda c: (0 if c["nivel"] == "Gerente" else 1, c["nome"]))

    return {"cadeados": cadeados, "etapa_bloqueada": etapa_metas_bloqueada(db, ciclo)}


# =====================================================================
# BLOQUEIO FINAL DA ETAPA (só Admin) — passa o bastão ao Supply
# =====================================================================
@router.post("/publicar-etapa")
async def publicar_etapa_metas(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    if (usuario.get("funcao") or "") != NIVEL_ADMIN:
        raise HTTPException(status_code=403, detail="Apenas o Administrador publica a etapa de Metas.")
    ciclo = get_current_cycle(db)

    try:
        # Grava a trava de ETAPA (origem='Metas') — o Supply verifica isto.
        ja = db.execute(text("""
            SELECT id FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Metas'
        """), {"c": ciclo}).scalar()
        if not ja:
            db.execute(text("""
                INSERT INTO controle_ciclos (ciclo_sop, origem, status) VALUES (:c, 'Metas', 'CONGELADO')
            """), {"c": ciclo})
        else:
            db.execute(text("UPDATE controle_ciclos SET status='CONGELADO' WHERE id = :id"), {"id": ja})

        # PROPAGAÇÃO COMO PARTIDA: vol_meta -> vol_supply e vol_final.
        # vol_ia, vol_topdown, vol_bottomup permanecem intactos (camadas acima).
        # A PROPORÇÃO de vol_meta entre clientes é o que o Supply herda como
        # peso de rateio (lida na hora do granular; não é armazenada à parte).
        db.execute(text("""
            UPDATE fato_ibp_granular
            SET vol_supply = vol_meta, vol_final = vol_meta
            WHERE ciclo_sop = :c
        """), {"c": ciclo})

        db.commit()
        return {"status": "sucesso", "mensagem": "Etapa de Metas publicada. Bastão passado ao Supply."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao publicar etapa: {e}")