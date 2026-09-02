"""
=====================================================================
RATEIO EM CASCATA — fases da ConsensoArena (SKU -> Executivo -> Razão Social)
=====================================================================
Ver design-consenso-arena-fases.md para o desenho completo.

Princípio (Opção A do desenho, confirmada com o usuário): NÃO existe
granularidade nova em fato_ibp_granular. SKU-agregado e SKU-Executivo são
sempre projeções calculadas na hora. O único destino de escrita continua
sendo vol_meta por CNPJ — igual ao POST /salvar que já existe hoje.

Isso significa que ratear "um nível acima" (SKU global do Gerente, ou
SKU x Executivo do Coordenador) precisa, na mesma operação, descer a
cascata inteira até o CNPJ, usando peso histórico em cada nível:
  Gerente ajusta SKU     -> peso por Coordenador -> peso por Executivo
                          -> peso por Razão Social -> peso por CNPJ
  Coordenador ajusta SKU -> peso por Executivo (dentro da carteira dele)
                          -> peso por Razão Social -> peso por CNPJ
  Coordenador ajusta      -> peso por Razão Social (dentro do executivo)
    Executivo             -> peso por CNPJ
  (Razão Social -> CNPJ já existe, é o POST /salvar atual — reaproveitado.)

Todas as funções usam ratear_maior_resto (shared_ibp.py) para garantir o
mesmo princípio de balanço de massa já usado no resto do sistema:
soma das partes == total digitado, sempre.
"""

from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException

from app.api.routers.shared_ibp import ratear_maior_resto, propagar_linha_jusante, ETAPA_METAS

JANELA_PESO_HISTORICO = "4 months"


def _peso_historico_por_cnpj(db: Session, ciclo: str, sku: str, mes: str,
                              filtro_extra_sql: str, filtro_extra_params: dict) -> list:
    """
    Busca, para um (ciclo, sku, mês), todas as linhas de fato_ibp_granular
    dentro do filtro extra informado (ex.: restrito a um Coordenador ou
    Executivo), com o peso histórico de vendas (4 meses) por CNPJ.
    Mesma query já usada em POST /salvar, generalizada com filtro variável.
    """
    params = {"ciclo": ciclo, "mes": mes, "sku": sku, **filtro_extra_params}
    rows = db.execute(text(f"""
        SELECT f.id AS fato_id, f.cgc,
               COALESCE(NULLIF(TRIM(c.supervisor_nome),''), 'SEM COORDENADOR') AS coordenador,
               COALESCE(NULLIF(TRIM(f.vendedor_nome),''), 'SEM VENDEDOR')      AS executivo,
               COALESCE(NULLIF(TRIM(c.razaosocial),''), 'SEM RAZAO SOCIAL')    AS razao_social,
               COALESCE(SUM(v.qt_pedido), 0) AS peso_historico
        FROM fato_ibp_granular f
        JOIN dim_clientes c ON f.cgc = c.cgc
        LEFT JOIN fato_vendas v
            ON v.cgc = f.cgc AND v.sku = f.sku
           AND v.data_pedido >= CURRENT_DATE - INTERVAL '{JANELA_PESO_HISTORICO}'
        WHERE f.ciclo_sop = :ciclo
          AND TO_CHAR(f.mes_projetado,'YYYY-MM') = :mes
          AND f.sku = :sku
          {filtro_extra_sql}
        GROUP BY f.id, f.cgc, c.supervisor_nome, f.vendedor_nome, c.razaosocial
        ORDER BY f.id
    """), params).fetchall()
    return rows


def _gravar_rateio_final(db: Session, ciclo: str, sku: str, mes: str,
                          linhas: list, partes: List[int]) -> None:
    """
    Último passo, sempre igual: grava vol_meta por linha (CNPJ) e propaga
    para jusante (Supply, Final) — mesmo comportamento de POST /salvar.
    """
    for r, parte in zip(linhas, partes):
        db.execute(text("UPDATE fato_ibp_granular SET vol_meta=:v WHERE id=:id"),
                   {"v": int(parte), "id": r.fato_id})
    propagar_linha_jusante(db, ciclo, sku, mes, ETAPA_METAS)


def _ratear_para_grupos(linhas: list, total: int, campo_grupo: str) -> Dict[str, int]:
    """
    Agrupa 'linhas' por campo_grupo (ex.: 'coordenador', 'executivo'),
    soma o peso histórico de cada grupo, e distribui 'total' entre os
    grupos via Maior Resto. Retorna {nome_grupo: volume_do_grupo}.
    """
    grupos: Dict[str, float] = {}
    for r in linhas:
        chave = getattr(r, campo_grupo)
        grupos[chave] = grupos.get(chave, 0.0) + max(0.0, float(r.peso_historico or 0))

    nomes = list(grupos.keys())
    pesos = [grupos[n] for n in nomes]
    partes = ratear_maior_resto(total, pesos)
    return dict(zip(nomes, partes))


def ratear_gerente_para_coordenadores(db: Session, ciclo: str, sku: str, mes: str,
                                       novo_total: int, gerente_nome: str) -> Dict[str, int]:
    """
    Gerente ajusta o SKU agregado de TODA a sua carteira. Distribui entre
    os Coordenadores (peso histórico), e cada Coordenador recebe seu próprio
    total — que ele mesmo vai rerratear dentro da fase SKU dele (a cascata
    completa até o CNPJ só acontece quando o Coordenador avança sua fase;
    aqui só gravamos o total do Coordenador, proporcional, direto em
    vol_meta de todos os CNPJs dele, como ponto de partida — reversível
    porque o Coordenador pode reajustar livremente na fase SKU dele antes
    de travar).
    """
    linhas = _peso_historico_por_cnpj(
        db, ciclo, sku, mes,
        filtro_extra_sql="AND TRIM(c.gerente_nome) = :gerente",
        filtro_extra_params={"gerente": gerente_nome.strip()},
    )
    if not linhas:
        return {}

    por_coordenador = _ratear_para_grupos(linhas, novo_total, "coordenador")

    # Dentro de cada coordenador, distribui proporcionalmente pelos CNPJs dele.
    for coord_nome, total_coord in por_coordenador.items():
        linhas_coord = [r for r in linhas if r.coordenador == coord_nome]
        pesos = [max(0.0, float(r.peso_historico or 0)) for r in linhas_coord]
        partes = ratear_maior_resto(total_coord, pesos)
        _gravar_rateio_final(db, ciclo, sku, mes, linhas_coord, partes)

    return por_coordenador


def ratear_coordenador_sku_para_executivos(db: Session, ciclo: str, sku: str, mes: str,
                                            novo_total: int, coordenador_nome: str) -> Dict[str, int]:
    """
    Coordenador ajusta o SKU agregado da PRÓPRIA carteira (fase SKU). Distribui
    entre os Executivos (peso histórico), e dentro de cada Executivo distribui
    pelas Razões Sociais e CNPJs dele — grava direto em vol_meta (Opção A).
    """
    linhas = _peso_historico_por_cnpj(
        db, ciclo, sku, mes,
        filtro_extra_sql="AND TRIM(c.supervisor_nome) = :coordenador",
        filtro_extra_params={"coordenador": coordenador_nome.strip()},
    )
    if not linhas:
        return {}

    por_executivo = _ratear_para_grupos(linhas, novo_total, "executivo")

    for exec_nome, total_exec in por_executivo.items():
        linhas_exec = [r for r in linhas if r.executivo == exec_nome]
        pesos = [max(0.0, float(r.peso_historico or 0)) for r in linhas_exec]
        partes = ratear_maior_resto(total_exec, pesos)
        _gravar_rateio_final(db, ciclo, sku, mes, linhas_exec, partes)

    return por_executivo


def ratear_executivo_para_razao_social(db: Session, ciclo: str, sku: str, mes: str,
                                        novo_total: int, coordenador_nome: str,
                                        executivo_nome: str) -> Dict[str, int]:
    """
    Coordenador ajusta o SKU dentro da fase EXECUTIVO (um executivo específico).
    Distribui pelas Razões Sociais daquele executivo e, dentro de cada uma,
    pelos CNPJs — grava direto em vol_meta.
    """
    linhas = _peso_historico_por_cnpj(
        db, ciclo, sku, mes,
        filtro_extra_sql="AND TRIM(c.supervisor_nome) = :coordenador AND TRIM(f.vendedor_nome) = :executivo",
        filtro_extra_params={"coordenador": coordenador_nome.strip(), "executivo": executivo_nome.strip()},
    )
    if not linhas:
        return {}

    por_razao = _ratear_para_grupos(linhas, novo_total, "razao_social")

    for razao_nome, total_razao in por_razao.items():
        linhas_razao = [r for r in linhas if r.razao_social == razao_nome]
        pesos = [max(0.0, float(r.peso_historico or 0)) for r in linhas_razao]
        partes = ratear_maior_resto(total_razao, pesos)
        _gravar_rateio_final(db, ciclo, sku, mes, linhas_razao, partes)

    return por_razao
