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


def _bottomup_congelado(db: Session, ciclo: str) -> bool:
    """
    A etapa ANTERIOR ao Consenso é o BottomUP (Gerenciamento). O Consenso só
    libera edição para Gerente/Coordenador se o BottomUP estiver CONGELADO.
    Admin fura essa trava (super-usuário para emergências).
    """
    st = db.execute(text("""
        SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'BottomUP'
    """), {"c": ciclo}).scalar()
    return st == 'CONGELADO'


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

    # ORÇAMENTO — somado UMA vez por SKU×mês (nunca por cliente, senão infla
    # ~N vezes no JOIN granular). Vive só no agregado do portfólio (cards/macro),
    # como no TopDown e no Bottom-Up. Restringe aos SKUs presentes na carteira.
    skus_carteira = df["sku"].unique().tolist() if not df.empty else []
    orcamento_map: Dict[tuple, float] = {}
    if skus_carteira:
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
    # Volume = SUM(qt_pedido); Faturamento = SUM(vl_pedido) REAL da venda —
    # nunca reconstruído por PMV atual (isso inflava o número).
    from dateutil.relativedelta import relativedelta as _rd
    meses_hist = {m: (pd.to_datetime(m) - _rd(months=12)).strftime("%Y-%m-%d") for m in [m2, m3, m4]}
    hist_rows = db.execute(text("""
        SELECT cgc, sku, DATE_TRUNC('month', data_pedido)::DATE AS mes,
               SUM(qt_pedido) AS vol_real, SUM(vl_pedido) AS fat_real
        FROM fato_vendas
        WHERE DATE_TRUNC('month', data_pedido)::DATE = ANY(CAST(:meses AS DATE[]))
          AND qt_pedido > 0
        GROUP BY cgc, sku, DATE_TRUNC('month', data_pedido)::DATE
    """), {"meses": list(meses_hist.values())}).fetchall()
    # Mapa: (cgc, sku, mes_projetado_atual) -> (vol_real, fat_real) do ano anterior.
    hist_inv = {v: k for k, v in meses_hist.items()}  # mes_hist -> mes_atual
    realizado_map: Dict[tuple, tuple] = {}
    for r in hist_rows:
        mes_hist_str = r.mes.strftime("%Y-%m-%d")
        mes_atual = hist_inv.get(mes_hist_str)
        if mes_atual:
            realizado_map[(str(r.cgc), str(r.sku), mes_atual)] = (float(r.vol_real or 0), float(r.fat_real or 0))

    if df.empty:
        bu_ok = _bottomup_congelado(db, ciclo)
        return {
            "ciclo_ativo": ciclo, "dados": [], "meses": [],
            "meu_congelamento": "ABERTO", "etapa_bloqueada": etapa_metas_bloqueada(db, ciclo),
            "bottomup_congelado": bu_ok,
            "etapa_anterior_pendente": (not bu_ok) and (escopo["funcao"] != NIVEL_ADMIN),
            "escopo": {"nivel": escopo["funcao"], "nome": escopo["nome_responsavel"]},
        }

    df["mes"] = pd.to_datetime(df["mes"])
    meses_cols = [
        {"mes_banco": m2, "mes_str": pd.to_datetime(m2).strftime("%b/%y").capitalize()},
        {"mes_banco": m3, "mes_str": pd.to_datetime(m3).strftime("%b/%y").capitalize()},
        {"mes_banco": m4, "mes_str": pd.to_datetime(m4).strftime("%b/%y").capitalize()},
    ]

    # Anexa o realizado histórico REAL (ano anterior) a cada linha: volume e
    # faturamento vindos direto da venda (qt_pedido / vl_pedido), não de PMV.
    df["hist_vol"] = df.apply(
        lambda r: realizado_map.get((str(r["cgc"]), str(r["sku"]), r["mes"].strftime("%Y-%m-%d")), (0.0, 0.0))[0],
        axis=1
    )
    df["hist_fat"] = df.apply(
        lambda r: realizado_map.get((str(r["cgc"]), str(r["sku"]), r["mes"].strftime("%Y-%m-%d")), (0.0, 0.0))[1],
        axis=1
    )

    # Monta árvore gerente > coordenador > vendedor > cliente > sku, com as
    # folhas carregando id + pmv (para conversão financeira no front e rateio).
    def bloco_meses(sub: pd.DataFrame) -> List[dict]:
        out = []
        for mc in meses_cols:
            mrows = sub[sub["mes"] == pd.to_datetime(mc["mes_banco"])]
            vol_meta = int(mrows["vol_meta"].sum())
            fat_meta = float((mrows["vol_meta"] * mrows["pmv"]).sum())
            # Comercial (Bottom-Up) — faturamento via PMV micro do ciclo.
            vol_com = int(mrows["vol_base"].sum())
            fat_com = float((mrows["vol_base"] * mrows["pmv"]).sum())
            # Ano anterior — volume e faturamento REAIS da venda (não PMV).
            vol_hist = int(mrows["hist_vol"].sum())
            fat_hist = float(mrows["hist_fat"].sum())
            # Orçamento — somado UMA vez por SKU deste sub-nó (nunca por cliente).
            skus_no_no = mrows["sku"].unique().tolist()
            rec_orc = sum(orcamento_map.get((str(s), mc["mes_banco"]), 0.0) for s in skus_no_no)
            out.append({
                "mes_banco": mc["mes_banco"], "mes_str": mc["mes_str"],
                "vol_meta": vol_meta, "fat_meta": round(fat_meta, 2),
                "vol_comercial": vol_com, "fat_comercial": round(fat_com, 2),
                "rec_orcada": round(rec_orc, 2),
                "vol_hist": vol_hist, "fat_hist": round(fat_hist, 2),
            })
        return out

    def folhas_por_mes(sub: pd.DataFrame) -> Dict[str, List[dict]]:
        # Para cada mês, as folhas (id, pmv, vol_meta) — usado no rateio do front.
        d: Dict[str, List[dict]] = {}
        for mc in meses_cols:
            mrows = sub[sub["mes"] == pd.to_datetime(mc["mes_banco"])]
            d[mc["mes_banco"]] = [
                {"id": int(r.id), "pmv": float(r.pmv), "vol_meta": int(r.vol_meta)}
                for r in mrows.itertuples()
            ]
        return d

    arvore = []
    for gerente, g_df in df.groupby("gerente"):
        g_node = {"tipo": "gerente", "nome": gerente, "chave": f"G|{gerente}",
                  "meses": bloco_meses(g_df), "subRows": []}
        for coord, c_df in g_df.groupby("coordenador"):
            c_node = {"tipo": "coordenador", "nome": coord, "chave": f"C|{gerente}|{coord}",
                      "meses": bloco_meses(c_df), "subRows": []}
            for vend, v_df in c_df.groupby("vendedor"):
                v_node = {"tipo": "vendedor", "nome": vend, "chave": f"V|{coord}|{vend}",
                          "meses": bloco_meses(v_df), "subRows": []}
                for cli, cli_df in v_df.groupby("cliente"):
                    cli_node = {"tipo": "cliente", "nome": cli, "chave": f"CLI|{vend}|{cli}",
                                "meses": bloco_meses(cli_df), "subRows": []}
                    for sku, s_df in cli_df.groupby("sku"):
                        desc = s_df["descricao"].iloc[0]
                        sku_node = {
                            "tipo": "produto", "nome": desc, "produto": sku,
                            "chave": f"SKU|{cli}|{sku}",
                            "meses": bloco_meses(s_df),
                            "folhas": folhas_por_mes(s_df),
                        }
                        cli_node["subRows"].append(sku_node)
                    v_node["subRows"].append(cli_node)
                c_node["subRows"].append(v_node)
            g_node["subRows"].append(c_node)
        arvore.append(g_node)

    # Bloqueio de precedência: BottomUP precisa estar congelado (Admin fura).
    bu_ok = _bottomup_congelado(db, ciclo)
    etapa_anterior_pendente = (not bu_ok) and (escopo["funcao"] != NIVEL_ADMIN)

    return {
        "ciclo_ativo": ciclo,
        "meses": meses_cols,
        "dados": arvore,
        "meu_congelamento": "CONGELADO" if esta_congelado_para_usuario(db, escopo, ciclo) else "ABERTO",
        "etapa_bloqueada": etapa_metas_bloqueada(db, ciclo),
        "bottomup_congelado": bu_ok,
        "etapa_anterior_pendente": etapa_anterior_pendente,
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

    # Trava de PRECEDÊNCIA: BottomUP (etapa anterior) tem de estar congelado.
    # Admin fura (super-usuário). Gerente/Coordenador aguardam a etapa anterior.
    if escopo["funcao"] != NIVEL_ADMIN and not _bottomup_congelado(db, ciclo):
        raise HTTPException(status_code=403, detail="O Bottom-Up (Gerência Comercial) ainda não foi congelado. Aguarde a etapa anterior fechar.")

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

    # Gerente vê os cadeados dos seus coordenadores; Admin vê todos.
    if escopo["ve_tudo"]:
        rows = db.execute(text("""
            SELECT nome_responsavel, nivel, status, congelado_por, data_congelamento
            FROM controle_metas_responsavel WHERE ciclo_sop = :c
            ORDER BY nivel, nome_responsavel
        """), {"c": ciclo}).fetchall()
    elif escopo["funcao"] == NIVEL_GERENTE:
        coords = _mapa_coordenadores_do_gerente(db, escopo["nome_responsavel"])
        nomes = list(coords) + [escopo["nome_responsavel"]]
        rows = db.execute(text("""
            SELECT nome_responsavel, nivel, status, congelado_por, data_congelamento
            FROM controle_metas_responsavel
            WHERE ciclo_sop = :c AND TRIM(nome_responsavel) = ANY(:nomes)
            ORDER BY nivel, nome_responsavel
        """), {"c": ciclo, "nomes": nomes}).fetchall()
    else:
        # Coordenador vê só o próprio.
        rows = db.execute(text("""
            SELECT nome_responsavel, nivel, status, congelado_por, data_congelamento
            FROM controle_metas_responsavel
            WHERE ciclo_sop = :c AND TRIM(nome_responsavel) = :n
        """), {"c": ciclo, "n": escopo["nome_responsavel"]}).fetchall()

    return {
        "cadeados": [
            {"nome": r.nome_responsavel, "nivel": r.nivel, "status": r.status,
             "por": r.congelado_por,
             "quando": r.data_congelamento.strftime("%d/%m/%Y %H:%M") if r.data_congelamento else None}
            for r in rows
        ],
        "etapa_bloqueada": etapa_metas_bloqueada(db, ciclo),
    }


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