"""
=====================================================================
PLANEJADOR DE DEMANDA — AVALIACAO POR CATEGORIA/SKU + CHAT ESCOPADO
=====================================================================
Destino: app/api/routers/planejador_demanda.py

Agente separado do agente_kpis.py (auditor de portfolio). Este aqui e
um conselheiro de planejamento: escreve avaliacoes curtas por SKU e por
categoria para orientar quem esta preenchendo a previsao do PROXIMO
ciclo, e responde perguntas escopadas a um SKU/categoria especifico via
o chat unico da Sidebar (modo "Especialista em Demanda").

POR QUE UM MODULO SEPARADO
---------------------------------------------------------------------
Publico e trabalho diferentes do agente_kpis: aquele audita o portfolio
inteiro para a diretoria, uma vez por ciclo. Este aconselha sobre UM
item, enquanto o planejador decide o numero do proximo mes. Compartilha
helpers com agente_kpis (via import direto) em vez de duplicar.

FONTE DE DADOS — SEM BASE NOVA
---------------------------------------------------------------------
Le exclusivamente mart_acuracia_sku_mes e dim_produtos — os mesmos
marts que o agente_kpis usa, so que filtrados por categoria ou por SKU
em vez do portfolio inteiro. Nao toca em fato_vendas nem
fato_ibp_granular diretamente: e por isso que nao precisou de nenhuma
base nova.

O dossie interativo (montar_dossie em perfil_sku.py) tem mais detalhe
(por cliente, vs orcamento) mas e uma consulta pontual ja rapida por
natureza — nao teve motivo para servir de fonte aqui; o agente usa a
mesma camada que ja validamos exaustivamente nas Fases 5-7.

JANELA — TRAILING 12 MESES, NAO YTD
---------------------------------------------------------------------
Diferente do agente_kpis (que usa ano vigente / YTD para auditoria),
aqui a janela e trailing de 12 meses terminando no ultimo mes fechado.
Planejamento precisa de contexto de sazonalidade que um YTD parcial
(por exemplo, so 2 meses em fevereiro) nao da.

CUSTO — BATCH POR CATEGORIA, NAO POR SKU
---------------------------------------------------------------------
~112 SKUs ativos = ~112 chamadas se fosse por SKU. Agrupado por
categoria (~13 categorias), UMA chamada avalia a categoria E todos os
SKUs dela juntos, em JSON estruturado. O modelo tambem ganha contexto
comparando os SKUs entre si na mesma chamada. Disparado so em ciclo
NOVO (mesmo padrao do relatorio principal) — nunca em refresh de ciclo
existente.
"""

import re
import json
import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta

from app.core.constants import PISO_HISTORICO
from app.api.routers.agente_kpis import (
    _sdiv, _corrigir_nome_empresa, _chamar_claude,
    _ciclo_atual, _normalizar, _chave, METODOLOGIA, _fill_rate_maps,
)

# _r nao e exportado por todas as versoes do agente_kpis — definido aqui
# para quebrar a dependencia de versao e tornar este modulo auto-contido.
def _r(v, casas: int = 2):
    """Arredonda tolerando None."""
    return None if v is None else round(float(v), casas)

logger = logging.getLogger(__name__)

JANELA_PLANEJADOR_MESES = 12   # trailing, nao YTD — contexto de sazonalidade


# =====================================================================
# DDL DO CACHE
# =====================================================================
DDL_CACHE_PLANEJADOR = """
CREATE TABLE IF NOT EXISTS planejador_avaliacao_cache (
    id          SERIAL PRIMARY KEY,
    tipo        VARCHAR(10) NOT NULL,      -- 'sku' | 'categoria'
    ref_id      VARCHAR(120) NOT NULL,     -- sku ou nome da categoria
    ciclo_sop   VARCHAR(10) NOT NULL,
    avaliacao   TEXT        NOT NULL,
    gerado_por  VARCHAR(120),
    gerado_em   TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT uix_planejador_aval UNIQUE (tipo, ref_id, ciclo_sop)
);

CREATE TABLE IF NOT EXISTS planejador_chat_cache (
    id          SERIAL PRIMARY KEY,
    tipo        VARCHAR(10) NOT NULL,
    ref_id      VARCHAR(120) NOT NULL,
    ciclo_sop   VARCHAR(10) NOT NULL,
    chave       VARCHAR(64) NOT NULL,
    pergunta    TEXT        NOT NULL,
    resposta    TEXT        NOT NULL,
    gerado_em   TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT uix_planejador_chat UNIQUE (tipo, ref_id, ciclo_sop, chave)
);
"""


def _garantir_tabelas_planejador(db: Session) -> None:
    for stmt in [s.strip() for s in DDL_CACHE_PLANEJADOR.split(";") if s.strip()]:
        db.execute(text(stmt))
    db.commit()


# =====================================================================
# JANELA
# =====================================================================
def janela_planejador(ciclo: str) -> Dict[str, Any]:
    """Trailing 12 meses terminando no ultimo mes fechado do ciclo."""
    mes, ano = int(ciclo[:2]), int(ciclo[3:])
    ref = datetime.date(ano, mes, 1) - relativedelta(months=1)
    ini = (ref - relativedelta(months=JANELA_PLANEJADOR_MESES - 1)).replace(day=1)
    if ini < PISO_HISTORICO:
        ini = PISO_HISTORICO
    meses = []
    d = ini
    while d <= ref:
        meses.append(d.strftime("%Y-%m"))
        d += relativedelta(months=1)
    return {
        "ciclo": ciclo,
        "mes_referencia": ref.strftime("%Y-%m"),
        "ini": ini.isoformat(),
        "fim": ref.isoformat(),
        "meses": meses,
    }


def janela_chat(ciclo: str, data_ini=None, data_fim=None) -> Dict[str, Any]:
    """Usa o periodo escolhido no chat; sem datas, preserva a janela do ciclo."""
    if data_ini is None and data_fim is None:
        return janela_planejador(ciclo)
    if data_ini is None or data_fim is None or data_ini > data_fim:
        raise ValueError("O período da análise é inválido.")

    ini = data_ini.replace(day=1)
    fim = data_fim.replace(day=1)
    if ini < PISO_HISTORICO:
        ini = PISO_HISTORICO
    meses = []
    d = ini
    while d <= fim:
        meses.append(d.strftime("%Y-%m"))
        d += relativedelta(months=1)
    return {
        "ciclo": ciclo,
        "mes_referencia": fim.strftime("%Y-%m"),
        "ini": ini.isoformat(),
        "fim": fim.isoformat(),
        "meses": meses,
    }


def listar_categorias_ativas(db: Session) -> List[str]:
    rows = db.execute(text("""
        SELECT DISTINCT categoria FROM dim_produtos
        WHERE ativo = TRUE AND categoria IS NOT NULL
        ORDER BY 1
    """)).fetchall()
    return [r[0] for r in rows]


def buscar_itens(db: Session, q: str) -> List[Dict[str, Any]]:
    """
    Autocomplete de SKU/categoria para o seletor do chat (modo Demanda).

    Busca por codigo do SKU, descricao (ILIKE) ou nome de categoria.
    Chamado por GET /api/v1/assistente/buscar?q=... — o painel da Sidebar
    usa isso para o usuario escolher o item antes de perguntar, ja que o
    chat unico nao esta mais preso a tela onde um dossie especifico esteja
    aberto.
    """
    termo = f"%{(q or '').strip()}%"
    resultados: List[Dict[str, Any]] = []

    skus = db.execute(text("""
        SELECT sku, descricao, categoria
        FROM dim_produtos
        WHERE ativo = TRUE AND (sku ILIKE :t OR descricao ILIKE :t)
        ORDER BY descricao
        LIMIT 15
    """), {"t": termo}).fetchall()
    for r in skus:
        resultados.append({
            "tipo": "sku", "id": r.sku,
            "label": f"{r.descricao} ({r.sku})",
            "categoria": r.categoria,
        })

    cats = db.execute(text("""
        SELECT DISTINCT categoria FROM dim_produtos
        WHERE ativo = TRUE AND categoria ILIKE :t
        ORDER BY categoria
        LIMIT 8
    """), {"t": termo}).fetchall()
    for r in cats:
        resultados.append({
            "tipo": "categoria", "id": r.categoria,
            "label": f"Categoria: {r.categoria}",
            "categoria": r.categoria,
        })

    return resultados


# =====================================================================
# QUERIES — todas em mart_acuracia_sku_mes, sem base nova
# =====================================================================
_SQL_CATEGORIA_AGREGADO = """
SELECT SUM(a.qt_pedido)   AS qt_pedido,
       SUM(a.vl_pedido)   AS vl_pedido,
       SUM(a.qt_entregue) AS qt_entregue,
       SUM(a.qt_corte)    AS qt_corte,
       SUM(a.vl_corte)    AS vl_corte,
       SUM(a.qt_corte_transferencia) AS qt_corte_transf,
       SUM(a.vl_corte_transferencia) AS vl_corte_transf,
       SUM(a.qt_plano)    AS qt_plano,
       SUM(a.erro_abs_cx) AS erro_abs,
       SUM(a.vl_excesso_plano)  AS vl_excesso,
       SUM(a.vl_perda_subplano) AS vl_subplano,
       SUM(a.qt_pedido) FILTER (WHERE a.tem_plano) AS qt_com_plano,
       COUNT(DISTINCT a.sku) AS n_skus
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.categoria = :categoria AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
"""

_SQL_CATEGORIA_MENSAL = """
SELECT TO_CHAR(a.mes,'YYYY-MM') AS mes,
       SUM(a.qt_pedido)   AS qt_pedido,
       SUM(a.qt_plano)    AS qt_plano,
       SUM(a.erro_abs_cx) AS erro_abs,
       SUM(a.qt_entregue) AS qt_entregue
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.categoria = :categoria AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
GROUP BY 1 ORDER BY 1
"""

_SQL_SKUS_DA_CATEGORIA = """
SELECT a.sku,
       COALESCE(MAX(p.descricao), a.sku) AS descricao,
       MAX(a.maturidade)  AS maturidade,
       SUM(a.qt_pedido)   AS qt_pedido,
       SUM(a.vl_pedido)   AS vl_pedido,
       SUM(a.qt_plano)    AS qt_plano,
       SUM(a.qt_entregue) AS qt_entregue,
       SUM(a.qt_corte)    AS qt_corte,
       SUM(a.vl_corte)    AS vl_corte,
       SUM(a.qt_corte_transferencia) AS qt_corte_transf,
       SUM(a.vl_corte_transferencia) AS vl_corte_transf,
       SUM(a.erro_abs_cx) AS erro_abs,
       SUM(a.vl_excesso_plano)  AS vl_excesso,
       SUM(a.vl_perda_subplano) AS vl_subplano,
       AVG(a.pmv)         AS pmv,
       BOOL_AND(a.tem_plano) AS sempre_com_plano
FROM mart_acuracia_sku_mes a
LEFT JOIN dim_produtos p ON p.sku = a.sku
WHERE a.ativo AND a.categoria = :categoria AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
GROUP BY a.sku
ORDER BY SUM(a.erro_abs_cx) DESC
"""

_SQL_SKUS_MENSAL_CATEGORIA = """
SELECT a.sku, TO_CHAR(a.mes,'YYYY-MM') AS mes, a.qt_pedido, a.qt_plano
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.categoria = :categoria AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
ORDER BY a.sku, a.mes
"""

_SQL_SKU_MENSAL = """
SELECT TO_CHAR(a.mes,'YYYY-MM') AS mes, a.qt_pedido, a.qt_plano,
       a.qt_entregue, a.qt_corte, a.pmv, a.tem_plano
FROM mart_acuracia_sku_mes a
WHERE a.ativo AND a.sku = :sku AND a.qt_pedido > 0
  AND a.mes >= CAST(:ini AS date) AND a.mes <= CAST(:fim AS date)
ORDER BY a.mes
"""


# =====================================================================
# CONTEXTO — categoria (batch) e sku (chat)
# =====================================================================
def montar_contexto_categoria(db: Session, categoria: str,
                              ciclo: str = None, data_ini=None,
                              data_fim=None) -> Optional[Dict[str, Any]]:
    """
    Contexto completo de UMA categoria: agregado, série mensal, e todos
    os SKUs dela com agregado + série mensal compacta cada um.
    """
    ciclo = ciclo or _ciclo_atual(db)
    jan = janela_chat(ciclo, data_ini, data_fim)
    fill_maps = _fill_rate_maps(db, jan["ini"], jan["fim"])
    p = {"categoria": categoria, "ini": jan["ini"], "fim": jan["fim"]}

    agg = db.execute(text(_SQL_CATEGORIA_AGREGADO), p).fetchone()
    if not agg or not agg.qt_pedido:
        return None

    qp = float(agg.qt_pedido or 0)
    categoria_dict = {
        "categoria":   categoria,
        "qt_pedido":   round(qp),
        "vl_pedido":   round(float(agg.vl_pedido or 0)),
        "qt_entregue": round(float(agg.qt_entregue or 0)),
        "qt_corte":    round(float(agg.qt_corte or 0)),
        "vl_corte":    round(float(agg.vl_corte or 0)),
        "qt_corte_transferencia": round(float(agg.qt_corte_transf or 0)),
        "vl_corte_transferencia": round(float(agg.vl_corte_transf or 0)),
        "wmape":       _r(_sdiv(agg.erro_abs, qp, 100)),
        "bias":        _r(_sdiv(float(agg.qt_plano or 0) - qp, qp, 100)),
        "fill_rate": _r(fill_maps["categorias"].get(categoria), 1),
        "cobertura_plano": _r(_sdiv(agg.qt_com_plano, qp, 100), 1),
        "vl_excesso":  round(float(agg.vl_excesso or 0)),
        "vl_subplano": round(float(agg.vl_subplano or 0)),
        "n_skus":      int(agg.n_skus or 0),
    }

    mensal = []
    for r in db.execute(text(_SQL_CATEGORIA_MENSAL), p).fetchall():
        qpm = float(r.qt_pedido or 0)
        mensal.append({
            "mes": r.mes, "qt_pedido": round(qpm),
            "wmape": _r(_sdiv(r.erro_abs, qpm, 100)),
            "bias":  _r(_sdiv(float(r.qt_plano or 0) - qpm, qpm, 100)),
            "fill_rate": _r(fill_maps["mensal"].get(r.mes), 1),
        })

    # série mensal compacta por SKU (qt_pedido, qt_plano) — sazonalidade
    mensal_por_sku: Dict[str, List[Dict[str, Any]]] = {}
    for r in db.execute(text(_SQL_SKUS_MENSAL_CATEGORIA), p).fetchall():
        mensal_por_sku.setdefault(r.sku, []).append({
            "mes": r.mes,
            "qt_pedido": round(float(r.qt_pedido or 0)),
            "qt_plano":  round(float(r.qt_plano or 0)),
        })

    skus = []
    for r in db.execute(text(_SQL_SKUS_DA_CATEGORIA), p).fetchall():
        qps = float(r.qt_pedido or 0)
        serie_sku = mensal_por_sku.get(r.sku, [])
        skus.append({
            "sku": r.sku, "descricao": r.descricao, "maturidade": r.maturidade,
            "qt_pedido": round(qps), "qt_plano": round(float(r.qt_plano or 0)),
            "qt_corte":  round(float(r.qt_corte or 0)),
            "vl_corte":  round(float(r.vl_corte or 0)),
            "qt_corte_transferencia": round(float(r.qt_corte_transf or 0)),
            "vl_corte_transferencia": round(float(r.vl_corte_transf or 0)),
            "wmape": _r(_sdiv(r.erro_abs, qps, 100)),
            "bias":  _r(_sdiv(float(r.qt_plano or 0) - qps, qps, 100)),
            "fill_rate": _r(fill_maps["skus"].get(r.sku), 1),
            "vl_excesso": round(float(r.vl_excesso or 0)),
            "vl_subplano": round(float(r.vl_subplano or 0)),
            "pmv": _r(r.pmv, 4),
            "sem_plano": not bool(r.sempre_com_plano),
            "mensal": serie_sku,
        })

    return {"janela": jan, "categoria": categoria_dict, "mensal": mensal, "skus": skus}


def montar_contexto_sku(db: Session, sku: str,
                        ciclo: str = None, data_ini=None,
                        data_fim=None) -> Optional[Dict[str, Any]]:
    """
    Contexto de UM SKU para o chat: série mensal própria + agregado da
    categoria (peer context leve — não a lista inteira de SKUs vizinhos,
    para não diluir o foco da pergunta).
    """
    ciclo = ciclo or _ciclo_atual(db)
    jan = janela_chat(ciclo, data_ini, data_fim)
    fill_maps = _fill_rate_maps(db, jan["ini"], jan["fim"])

    meta = db.execute(text("""
        SELECT COALESCE(MAX(p.descricao), :sku) AS descricao,
               MAX(a.categoria)   AS categoria,
               MAX(a.maturidade)  AS maturidade
        FROM mart_acuracia_sku_mes a
        LEFT JOIN dim_produtos p ON p.sku = a.sku
        WHERE a.ativo = TRUE AND p.ativo = TRUE AND a.sku = :sku
    """), {"sku": sku}).fetchone()
    if not meta or not meta.categoria:
        return None

    mensal_sku = []
    for r in db.execute(text(_SQL_SKU_MENSAL),
                        {"sku": sku, "ini": jan["ini"], "fim": jan["fim"]}).fetchall():
        qpm = float(r.qt_pedido or 0)
        mensal_sku.append({
            "mes": r.mes,
            "qt_pedido":   round(qpm),
            "qt_plano":    round(float(r.qt_plano or 0)),
            "qt_entregue": round(float(r.qt_entregue or 0)),
            "qt_corte":    round(float(r.qt_corte or 0)),
            "fill_rate":   _r(fill_maps["mensal"].get(r.mes), 1),
            "pmv":         _r(r.pmv, 4),
            "tem_plano":   bool(r.tem_plano),
        })

    ctx_cat = montar_contexto_categoria(db, meta.categoria, ciclo, data_ini, data_fim)

    return {
        "janela": jan, "foco": "sku",
        "sku": sku, "descricao": meta.descricao,
        "categoria": meta.categoria, "maturidade": meta.maturidade,
        "mensal_sku": mensal_sku,
        "categoria_agregado": ctx_cat["categoria"] if ctx_cat else None,
    }


# =====================================================================
# PROMPTS
# =====================================================================
_REGRAS_COMUNS = """
REGRAS INEGOCIAVEIS
- A empresa se chama "Linea Alimentos" — com E, nao "Linha" (palavra comum em portugues).
- Nunca cite nomes de tabelas, campos de banco, sistemas ou ferramentas. Nao mencione
  Nexus, planilha, plataforma, migracao, transicao em nenhuma hipotese.
- NOME DO PRODUTO: use SEMPRE a descricao exata do campo "descricao", sem reescrever,
  abreviar, traduzir ou padronizar.
- vl_corte NAO tem formula: e soma direta do ERP. vl_excesso e vl_subplano SAO calculados
  por (SKU, mes) — confira o sinal contra o BIAS antes de qualificar como excesso ou falta
  (ver METODOLOGIA). Se quiser descrever a conta (gap × PMV), verifique que os tres
  componentes do JSON (qt_plano, qt_pedido, pmv) multiplicados fecham com o campo — se
  nao fecharem ou se os componentes nao estiverem no JSON, cite so o valor final sem
  descrever a conta. NUNCA invente premissas (plano, vendido, PMV) para fazer a conta
  parecer consistente com o total.
- qt_corte_transferencia / vl_corte_transferencia: parte do corte que e transferencia de
  codigo promocional (COPA), nao ruptura real — o cliente recebeu o produto sob outro
  codigo. Quando for parte relevante do corte do item, diga isso em vez de tratar como
  ruptura de suprimento.
- NUNCA atribua a avaliacao a uma area, departamento ou responsavel especifico.
- Todo numero citado deve existir no JSON de entrada. Nao estime nem invente.
- WMAPE = soma dos erros absolutos / soma do vendido, ponderado por volume.
"""

_SYSTEM_PLANEJADOR_BATCH = f"""Voce e o Planejador de Demanda desta categoria. Sua funcao e
escrever avaliacoes curtas que orientam quem vai preencher a previsao do PROXIMO ciclo —
nao um relatorio para diretoria, e uma nota de apoio a decisao dentro da tela de trabalho.

{METODOLOGIA}
{_REGRAS_COMUNS}

TOM E CONTEUDO DE CADA AVALIACAO
- 2 a 4 frases, direto ao ponto — quem le esta no meio de uma tela de trabalho, nao lendo
  um relatorio.
- NAO repita numeros que o grafico da tela ja mostra (vendido x meta x IA mes a mes). Diga
  o que o grafico sozinho nao diz: tendencia, causa provavel do desvio, sazonalidade se
  aparente na serie mensal, risco (ruptura ou excesso), e uma orientacao objetiva para o
  proximo mes — aumentar, manter ou reduzir o plano, e por que.
- Item com maturidade "Lancamento": nao cobre acuracia como se fosse maduro. Fale sobre
  tendencia de rampa, nao sobre erro percentual alto.
- Se sem_plano=true em algum SKU: diga isso explicitamente — e ausencia de previsao, nao
  erro de previsao.
- Texto CORRIDO, sem markdown: sem #, sem **, sem listas com marcador. O Dossie exibe isto
  como paragrafo simples, nao como chat renderizado — qualquer simbolo de formatacao
  apareceria literal na tela.

FORMATO DE SAIDA — APENAS JSON, SEM TEXTO ANTES OU DEPOIS, SEM BLOCO DE CODIGO MARKDOWN.
Estrutura exata:
{{"categoria": {{"avaliacao": "..."}}, "skus": [{{"sku": "...", "avaliacao": "..."}}, ...]}}

Inclua um item em "skus" para CADA SKU presente no JSON de entrada, na mesma ordem, sem
pular nenhum. Se um SKU nao tiver dado suficiente para uma avaliacao especifica, diga isso
em vez de inventar.
"""

_SYSTEM_PLANEJADOR_CHAT = f"""Voce e o Planejador de Demanda respondendo uma pergunta sobre
um SKU ou categoria especifico, para ajudar quem esta decidindo o proximo plano.

{METODOLOGIA}
{_REGRAS_COMUNS}

- Responda APENAS com base no JSON de contexto fornecido. Se o dado nao estiver la, diga
  que nao esta disponivel nesse recorte — nunca estime.
- Tom pratico, de conselheiro — nao de auditor de portfolio. Responda pensando em "o que eu
  faco com isso no plano do proximo mes".
- Quando a pergunta pedir analise de BIAS ao longo do ano em uma categoria,
  cubra CADA SKU presente no JSON, com nome do produto, BIAS, excesso,
  subplano e corte quando disponiveis. Destaque os itens prioritarios por
  impacto financeiro e nao pare depois da introducao da categoria.

FORMATO DA RESPOSTA:
- Comece com uma frase de resposta direta a pergunta, sem titulo.
- Use no maximo dois niveis de titulo com "## " (dois cerquilhas e espaco) — NUNCA "# "
  com um cerquilha so, o renderizador nao reconhece e o simbolo aparece literal na tela.
- Use **negrito** apenas para numeros-chave e nomes de item.
- Tabelas: use pipe simples com cabecalho e linha separadora. Maximo 6 colunas e 10 linhas.
- Termine com uma linha iniciada por "Leitura: " contendo a conclusao pratica.
- Conclua sempre o raciocinio. Nunca interrompa no meio de uma frase.
- 1 a 3 paragrafos curtos fora das tabelas.
"""


# =====================================================================
# JSON SEGURO
# =====================================================================
def _parse_json_seguro(texto: str) -> Optional[dict]:
    t = (texto or "").strip()
    if t.startswith("```"):
        t = re.sub(r'^```(?:json)?\s*', '', t)
        t = re.sub(r'\s*```$', '', t)
    try:
        return json.loads(t)
    except (json.JSONDecodeError, TypeError):
        return None


# =====================================================================
# PERSISTENCIA
# =====================================================================
def avaliacao_existente(db: Session, tipo: str, ref_id: str,
                        ciclo: str = None) -> Optional[Dict[str, Any]]:
    _garantir_tabelas_planejador(db)
    ciclo = ciclo or _ciclo_atual(db)
    r = db.execute(text("""
        SELECT avaliacao, gerado_por, gerado_em
        FROM planejador_avaliacao_cache
        WHERE tipo = :t AND ref_id = :r AND ciclo_sop = :c
    """), {"t": tipo, "r": ref_id, "c": ciclo}).fetchone()
    if not r:
        return None
    return {
        "avaliacao": r.avaliacao, "gerado_por": r.gerado_por,
        "gerado_em": r.gerado_em.isoformat() if r.gerado_em else None,
        "ciclo": ciclo, "tipo": tipo, "ref_id": ref_id,
    }


def _salvar_avaliacao(db: Session, tipo: str, ref_id: str, ciclo: str,
                      avaliacao: str, usuario: str) -> None:
    db.execute(text("""
        INSERT INTO planejador_avaliacao_cache (tipo, ref_id, ciclo_sop, avaliacao, gerado_por)
        VALUES (:t, :r, :c, :a, :u)
        ON CONFLICT (tipo, ref_id, ciclo_sop) DO UPDATE SET
            avaliacao = EXCLUDED.avaliacao, gerado_por = EXCLUDED.gerado_por, gerado_em = now()
    """), {"t": tipo, "r": ref_id, "c": ciclo, "a": avaliacao, "u": usuario})
    db.commit()


# =====================================================================
# GERACAO — batch por categoria
# =====================================================================
def avaliar_categoria(db: Session, categoria: str, ciclo: str = None,
                      forcar: bool = False, usuario: str = "Sistema") -> Optional[Dict[str, Any]]:
    """
    Uma chamada Haiku avalia a categoria E todos os SKUs dela juntos.
    Grava um registro em planejador_avaliacao_cache por item (1 categoria
    + N SKUs). forcar=True ignora o cache — usado pelo pipeline em ciclo novo.
    """
    _garantir_tabelas_planejador(db)
    ciclo = ciclo or _ciclo_atual(db)

    if not forcar:
        cache = avaliacao_existente(db, "categoria", categoria, ciclo)
        if cache:
            return cache

    ctx = montar_contexto_categoria(db, categoria, ciclo)
    if not ctx or not ctx["skus"]:
        return None

    payload = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    # ~250 tokens por SKU (avaliacao de categoria + N avaliacoes de SKU no
    # mesmo JSON) mais um piso de 4000: categorias com 16+ SKUs estouravam
    # o limite fixo de 4000 e a resposta vinha cortada no meio do JSON.
    tokens_estimados = max(4000, 1200 + len(ctx["skus"]) * 250)
    texto = _chamar_claude(
        _SYSTEM_PLANEJADOR_BATCH,
        [{"role": "user", "content": payload}],
        max_tokens=tokens_estimados,
    )
    parsed = _parse_json_seguro(texto)
    if not parsed or "categoria" not in parsed:
        raise RuntimeError(
            f"Resposta do modelo nao veio em JSON valido para a categoria {categoria}."
        )

    av_cat = _corrigir_nome_empresa((parsed["categoria"].get("avaliacao") or "").strip())
    _salvar_avaliacao(db, "categoria", categoria, ciclo, av_cat, usuario)

    n_salvos = 0
    for item in parsed.get("skus", []):
        sk = item.get("sku")
        av = (item.get("avaliacao") or "").strip()
        if sk and av:
            _salvar_avaliacao(db, "sku", sk, ciclo, _corrigir_nome_empresa(av), usuario)
            n_salvos += 1

    return {"categoria": categoria, "avaliacao": av_cat,
            "skus_avaliados": n_salvos, "ciclo": ciclo}


def avaliar_todas_categorias(db: Session, ciclo: str = None,
                             usuario: str = "Sistema",
                             log_callback=print) -> Dict[str, Any]:
    """Chamado pelo pipeline ao abrir um ciclo novo — uma vez por categoria."""
    ciclo = ciclo or _ciclo_atual(db)
    categorias = listar_categorias_ativas(db)
    ok, falhas = 0, []
    for cat in categorias:
        try:
            r = avaliar_categoria(db, cat, ciclo=ciclo, forcar=True, usuario=usuario)
            if r:
                ok += 1
                log_callback(f"   • {cat:24} {r['skus_avaliados']} SKU(s) avaliados")
            else:
                log_callback(f"   • {cat:24} sem dados no periodo, pulado")
        except Exception as e:
            falhas.append(cat)
            log_callback(f"   ⚠️ {cat}: {e}")
    resumo = f"   Categorias avaliadas: {ok}/{len(categorias)}"
    if falhas:
        resumo += f" | falhas: {', '.join(falhas)}"
    log_callback(resumo)
    return {"ok": ok, "total": len(categorias), "falhas": falhas, "ciclo": ciclo}


# =====================================================================
# CHAT — dispatcher unico chamado por router_assistente.py
# =====================================================================
def responder_pergunta(db: Session, modo: str, pergunta: str,
                       escopo_tipo: str = None, escopo_id: str = None,
                       historico: List[dict] = None, data_ini=None,
                       data_fim=None) -> Dict[str, Any]:
    """
    Ponto de entrada unico do chat da Sidebar. O router NAO sabe qual
    modulo resolve cada modo — so passa o payload adiante e recebe
    {"resposta": ..., "do_cache": ...} nos dois casos.

      modo='indicadores' -> delega para agente_kpis (portfolio, ciclo ativo)
      modo='demanda'     -> delega para responder_pergunta_planejador
                             (exige escopo_tipo + escopo_id ja escolhidos
                             pelo usuario na busca do painel)
    """
    if modo == "indicadores":
        from app.api.routers import agente_kpis
        r = agente_kpis.responder_pergunta(
            db, pergunta, historico=historico,
            data_ini=data_ini, data_fim=data_fim,
        )
        return {"resposta": r["resposta"], "do_cache": r.get("do_cache", False)}

    if modo == "demanda":
        if not escopo_tipo or not escopo_id:
            raise RuntimeError(
                "Selecione um SKU ou categoria antes de perguntar no modo Demanda."
            )
        return responder_pergunta_planejador(
            db, escopo_tipo, escopo_id, pergunta, historico,
            data_ini=data_ini, data_fim=data_fim,
        )

    raise RuntimeError(f"Modo '{modo}' invalido — use 'indicadores' ou 'demanda'.")


# =====================================================================
# CHAT — modo "Especialista em Demanda"
# =====================================================================
def responder_pergunta_planejador(db: Session, tipo: str, ref_id: str, pergunta: str,
                                  historico: List[dict] = None,
                                  ciclo: str = None, data_ini=None,
                                  data_fim=None) -> Dict[str, Any]:
    """tipo: 'sku' | 'categoria'. ref_id: o código do SKU ou o nome da categoria."""
    _garantir_tabelas_planejador(db)
    ciclo = ciclo or _ciclo_atual(db)

    # Versiona o cache para invalidar respostas curtas geradas pelo limite
    # anterior de tokens.
    ch = _chave(
        "chat-v5-fill-population-screen-parity", tipo, ref_id, ciclo, data_ini, data_fim,
        _normalizar(pergunta),
    )
    r = db.execute(text("""
        SELECT resposta FROM planejador_chat_cache
        WHERE tipo = :t AND ref_id = :r AND ciclo_sop = :c AND chave = :k
    """), {"t": tipo, "r": ref_id, "c": ciclo, "k": ch}).fetchone()
    if r:
        return {"resposta": r.resposta, "do_cache": True}

    ctx = (
        montar_contexto_sku(db, ref_id, ciclo, data_ini, data_fim)
        if tipo == "sku"
        else montar_contexto_categoria(db, ref_id, ciclo, data_ini, data_fim)
    )
    if not ctx:
        return {"resposta": f"Nao encontrei dados para {ref_id} no periodo analisado.",
                "do_cache": False}

    msgs = []
    for h in (historico or [])[-6:]:
        papel = "user" if h.get("role") == "user" else "assistant"
        msgs.append({"role": papel, "content": str(h.get("content", ""))[:2000]})
    msgs.append({
        "role": "user",
        "content": (f"CONTEXTO ({tipo}={ref_id}):\n"
                    f"{json.dumps(ctx, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    f"PERGUNTA: {pergunta}"),
    })

    resposta = _chamar_claude(_SYSTEM_PLANEJADOR_CHAT, msgs)
    resposta = _corrigir_nome_empresa(resposta)

    db.execute(text("""
        INSERT INTO planejador_chat_cache (tipo, ref_id, ciclo_sop, chave, pergunta, resposta)
        VALUES (:t, :r, :c, :k, :p, :resp)
        ON CONFLICT (tipo, ref_id, ciclo_sop, chave) DO UPDATE SET
            resposta = EXCLUDED.resposta, gerado_em = now()
    """), {"t": tipo, "r": ref_id, "c": ciclo, "k": ch, "p": pergunta, "resp": resposta})
    db.commit()

    return {"resposta": resposta, "do_cache": False}