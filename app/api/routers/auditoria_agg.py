"""
=====================================================================
AGREGADOR DA AUDITORIA (Passo 3) — todos os SKUs de um mês numa query
=====================================================================
Roda a lógica de tres eixos (do perfil_sku) para TODOS os SKUs de um mes de
uma vez, via UMA query em lote (nao um loop Python de 130 idas ao banco).

Por que query unica:
  • Velocidade: a tela abre para o presidente sem espera.
  • Auditabilidade: o CFO pode rodar a MESMA query no psql e ver os numeros da
    tela. Nada e "inventado" na aplicacao.

FULL OUTER JOIN plano<->venda captura as TRES situacoes de uma vez:
  (a) planejado E vendido      -> ranking de aderencia (erramos quanto?)
  (b) planejado, NAO vendido   -> over / estoque parado
  (c) vendido SEM plano        -> "miss": venda que ninguem previu
                                  (confirmado: ha ~15-20 SKUs assim por mes)

Regra N-2 e importada do perfil_sku (fonte unica da regra temporal).
Cliente sempre por RAZAO SOCIAL. Nao filtra por ativo/bloqueado.
"""

import datetime
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.api.routers.perfil_sku import (
    ciclo_fonte_do_mes, _coerce_mes, _mes_esta_em_andamento, gerar_insight,
)


# =====================================================================
# QUERY EM LOTE: três eixos para todos os SKUs de um mês
# =====================================================================
def _linhas_diagnostico_mes(db: Session, mes, categoria: Optional[str] = None,
                            segmento: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Uma linha por SKU com planejado/vendido/faturado + gaps + PMV, para o mês.

    Previsto = vol_final do ciclo N-2 daquele mês (anti-look-ahead).
    Venda    = fato_vendas do mês-calendário.
    FULL OUTER JOIN: inclui SKU sem plano (miss) e SKU sem venda (over).
    """
    mes_d = _coerce_mes(mes)
    ciclo = ciclo_fonte_do_mes(mes_d)
    ym = mes_d.strftime("%Y-%m")

    filtro_cat = ""
    params = {"ciclo": ciclo, "mes": mes_d, "ym": ym}
    if categoria:
        filtro_cat += " AND COALESCE(p.categoria,'SEM CATEGORIA') = :cat "
        params["cat"] = categoria
    if segmento:
        filtro_cat += " AND COALESCE(p.segmento,'SEM SEGMENTO') = :seg "
        params["seg"] = segmento

    sql = f"""
        WITH plano AS (
            SELECT f.sku,
                   SUM(f.vol_final)                   AS plan_cx,
                   SUM(f.vol_final * f.pmv_aplicado)  AS plan_rs,
                   SUM(f.vol_ia)                      AS ia_cx
            FROM fato_ibp_granular f
            WHERE f.ciclo_sop = :ciclo AND f.mes_projetado = :mes
            GROUP BY f.sku
        ),
        venda AS (
            SELECT v.sku,
                   SUM(v.qt_pedido) AS vend_cx, SUM(v.vl_pedido) AS vend_rs,
                   SUM(v.qtfatura)  AS fat_cx,  SUM(v.vlfatura)  AS fat_rs
            FROM fato_vendas v
            WHERE TO_CHAR(v.data_pedido,'YYYY-MM') = :ym
            GROUP BY v.sku
        ),
        universo AS (
            SELECT COALESCE(pl.sku, vd.sku) AS sku,
                   COALESCE(pl.plan_cx,0) AS plan_cx, COALESCE(pl.plan_rs,0) AS plan_rs,
                   COALESCE(pl.ia_cx,0)   AS ia_cx,
                   COALESCE(vd.vend_cx,0) AS vend_cx, COALESCE(vd.vend_rs,0) AS vend_rs,
                   COALESCE(vd.fat_cx,0)  AS fat_cx,  COALESCE(vd.fat_rs,0)  AS fat_rs,
                   (pl.sku IS NOT NULL)   AS tem_plano,
                   (vd.sku IS NOT NULL)   AS tem_venda
            FROM plano pl
            FULL OUTER JOIN venda vd ON vd.sku = pl.sku
        )
        SELECT u.*,
               COALESCE(p.descricao, u.sku)          AS descricao,
               COALESCE(p.categoria,'SEM CATEGORIA') AS categoria,
               COALESCE(p.segmento,'SEM SEGMENTO')   AS segmento
        FROM universo u
        LEFT JOIN dim_produtos p ON p.sku = u.sku
        WHERE TRUE {filtro_cat}
        ORDER BY u.vend_rs DESC
    """
    rows = db.execute(text(sql), params).fetchall()

    out = []
    for r in rows:
        plan_cx = float(r.plan_cx or 0); plan_rs = float(r.plan_rs or 0)
        ia_cx = float(r.ia_cx or 0)
        vend_cx = float(r.vend_cx or 0); vend_rs = float(r.vend_rs or 0)
        fat_cx = float(r.fat_cx or 0);   fat_rs = float(r.fat_rs or 0)

        gap_prev_cx = plan_cx - vend_cx
        gap_prev_rs = plan_rs - vend_rs
        gap_exec_cx = vend_cx - fat_cx
        gap_exec_rs = vend_rs - fat_rs

        pmv_prev = plan_rs / plan_cx if plan_cx > 0 else 0.0
        pmv_vend = vend_rs / vend_cx if vend_cx > 0 else 0.0

        efeito_vol = gap_prev_cx * pmv_prev
        efeito_pre = (pmv_prev - pmv_vend) * vend_cx

        # Classificação da seção
        tem_plano = bool(r.tem_plano); tem_venda = bool(r.tem_venda)
        if tem_plano and tem_venda:
            secao = "auditavel"
        elif tem_plano and not tem_venda:
            secao = "over"      # planejou e ninguem comprou
        else:
            secao = "miss"      # vendeu sem plano

        # Aderência e viés (só fazem sentido com venda)
        aderencia = None
        rotulo = "SEM VENDA"
        if vend_cx > 0:
            aderencia = max(0.0, 1 - abs(plan_cx - vend_cx) / vend_cx)
            if not tem_plano:
                rotulo = "SEM PLANO"
            elif plan_cx > vend_cx:
                rotulo = "VENDA ABAIXO"
            elif plan_cx < vend_cx:
                rotulo = "VENDA ACIMA"
            else:
                rotulo = "NA MEDIDA"

        fill_cx = (fat_cx / vend_cx) if vend_cx > 0 else None

        out.append({
            "sku": r.sku, "descricao": r.descricao,
            "categoria": r.categoria, "segmento": r.segmento,
            "secao": secao,
            "planejado_cx": round(plan_cx, 0), "planejado_rs": round(plan_rs, 2),
            "vendido_cx": round(vend_cx, 0),   "vendido_rs": round(vend_rs, 2),
            "faturado_cx": round(fat_cx, 0),   "faturado_rs": round(fat_rs, 2),
            "ia_cx": round(ia_cx, 0),
            "gap_previsao_cx": round(gap_prev_cx, 0), "gap_previsao_rs": round(gap_prev_rs, 2),
            "gap_execucao_cx": round(gap_exec_cx, 0), "gap_execucao_rs": round(gap_exec_rs, 2),
            "pmv_previsto": round(pmv_prev, 2), "pmv_vendido": round(pmv_vend, 2),
            "efeito_volume_rs": round(efeito_vol, 2), "efeito_preco_rs": round(efeito_pre, 2),
            "fill_rate_cx": round(fill_cx, 4) if fill_cx is not None else None,
            "aderencia": round(aderencia, 4) if aderencia is not None else None,
            "rotulo_vies": rotulo,
        })
    return out


# =====================================================================
# AGREGADO DA AUDITORIA: totais, rankings e as três seções
# =====================================================================
def auditoria_mes(db: Session, mes, categoria: Optional[str] = None,
                  segmento: Optional[str] = None, top_n: int = 10) -> Dict[str, Any]:
    """
    Consolidado de um mês: totais com sinal, rankings por impacto (R$ e cx) e
    as três seções (auditavel / over / miss).

    Responde a pergunta central: onde perdemos dinheiro — e de quem e a culpa.
    """
    linhas = _linhas_diagnostico_mes(db, mes, categoria, segmento)
    mes_d = _coerce_mes(mes)

    auditaveis = [l for l in linhas if l["secao"] == "auditavel"]
    overs = [l for l in linhas if l["secao"] == "over"]
    misses = [l for l in linhas if l["secao"] == "miss"]

    # Totais consolidados (sobre tudo que tem plano OU venda)
    tot_plan_cx = sum(l["planejado_cx"] for l in linhas)
    tot_plan_rs = sum(l["planejado_rs"] for l in linhas)
    tot_vend_cx = sum(l["vendido_cx"] for l in linhas)
    tot_vend_rs = sum(l["vendido_rs"] for l in linhas)
    tot_fat_cx = sum(l["faturado_cx"] for l in linhas)
    tot_fat_rs = sum(l["faturado_rs"] for l in linhas)

    # Aderência agregada (contra o pedido) — e a "fina" (grão, par-a-par)
    # Agregada: |soma_plan − soma_vend| / soma_vend  (erros se cancelam)
    aderencia_agregada = None
    if tot_vend_cx > 0:
        aderencia_agregada = max(0.0, 1 - abs(tot_plan_cx - tot_vend_cx) / tot_vend_cx)
    # Fina: soma dos |erros| de cada SKU auditável / soma vendida — a verdade do grão
    aderencia_fina = None
    soma_abs_erro = sum(abs(l["planejado_cx"] - l["vendido_cx"]) for l in auditaveis)
    soma_vend_aud = sum(l["vendido_cx"] for l in auditaveis)
    if soma_vend_aud > 0:
        aderencia_fina = max(0.0, 1 - soma_abs_erro / soma_vend_aud)

    # Totais COM SINAL (a pergunta: vendemos a mais ou a menos, em R$)
    planejado_a_mais_rs = sum(max(0.0, l["gap_previsao_rs"]) for l in linhas)   # estoque
    planejado_a_menos_rs = sum(max(0.0, -l["gap_previsao_rs"]) for l in linhas) # venda nao antecipada
    corte_supply_rs = sum(max(0.0, l["gap_execucao_rs"]) for l in linhas)       # nao entregou
    venda_sem_plano_rs = sum(l["vendido_rs"] for l in misses)                   # miss

    # Rankings por IMPACTO ABSOLUTO (nao percentual)
    piores_rs = sorted(auditaveis, key=lambda l: -abs(l["gap_previsao_rs"]))[:top_n]
    piores_cx = sorted(auditaveis, key=lambda l: -abs(l["gap_previsao_cx"]))[:top_n]
    # Melhores: menor erro relativo COM volume material (evita premiar SKU de 3 cx)
    limiar_vol = (soma_vend_aud / len(auditaveis)) * 0.3 if auditaveis else 0
    candidatos_bons = [l for l in auditaveis if l["vendido_cx"] >= limiar_vol and l["aderencia"] is not None]
    melhores = sorted(candidatos_bons, key=lambda l: -l["aderencia"])[:top_n]
    # Miss ordenado por faturamento não previsto
    piores_miss = sorted(misses, key=lambda l: -l["vendido_rs"])[:top_n]

    return {
        "mes": mes_d.strftime("%Y-%m-%d"),
        "ciclo_fonte": ciclo_fonte_do_mes(mes_d),
        "mes_em_andamento": _mes_esta_em_andamento(mes_d),
        "filtro": {"categoria": categoria, "segmento": segmento},

        "totais": {
            "planejado_cx": round(tot_plan_cx, 0), "planejado_rs": round(tot_plan_rs, 2),
            "vendido_cx": round(tot_vend_cx, 0),   "vendido_rs": round(tot_vend_rs, 2),
            "faturado_cx": round(tot_fat_cx, 0),   "faturado_rs": round(tot_fat_rs, 2),
            "aderencia_agregada": round(aderencia_agregada, 4) if aderencia_agregada is not None else None,
            "aderencia_fina": round(aderencia_fina, 4) if aderencia_fina is not None else None,
            "planejado_a_mais_rs": round(planejado_a_mais_rs, 2),
            "planejado_a_menos_rs": round(planejado_a_menos_rs, 2),
            "corte_supply_rs": round(corte_supply_rs, 2),
            "venda_sem_plano_rs": round(venda_sem_plano_rs, 2),
            "fill_rate_agregado": round(tot_fat_cx / tot_vend_cx, 4) if tot_vend_cx > 0 else None,
        },
        "contagem": {
            "auditaveis": len(auditaveis), "over": len(overs), "miss": len(misses),
        },
        "ranking_piores_rs": piores_rs,
        "ranking_piores_cx": piores_cx,
        "ranking_melhores": melhores,
        "ranking_miss": piores_miss,
        "insight": _insight_agregado(tot_plan_cx, tot_vend_cx, planejado_a_mais_rs,
                                     planejado_a_menos_rs, corte_supply_rs,
                                     venda_sem_plano_rs, aderencia_agregada, aderencia_fina),
    }


def _insight_agregado(plan_cx, vend_cx, a_mais, a_menos, corte, miss, ad_agg, ad_fina) -> str:
    """Frase consolidada que nomeia onde o dinheiro está sendo perdido."""
    partes = []
    if plan_cx > vend_cx:
        partes.append(f"No agregado, planejou-se {int(plan_cx):,} cx e vendeu-se {int(vend_cx):,} — plano ACIMA da demanda.")
    elif plan_cx < vend_cx:
        partes.append(f"No agregado, planejou-se {int(plan_cx):,} cx e vendeu-se {int(vend_cx):,} — plano ABAIXO da demanda.")
    else:
        partes.append(f"Plano e venda agregados batem em {int(vend_cx):,} cx.")

    # A ilusão da agregação, se houver
    if ad_agg is not None and ad_fina is not None and (ad_agg - ad_fina) > 0.10:
        partes.append(f" Atencao: aderencia agregada {ad_agg*100:.0f}% mas no grao SKU e {ad_fina*100:.0f}% — os erros se cancelam entre SKUs.")

    # Onde perde dinheiro, ordenado
    perdas = []
    if a_mais > 0: perdas.append(("capital em estoque (planejou demais)", a_mais))
    if a_menos > 0: perdas.append(("venda nao antecipada (planejou de menos)", a_menos))
    if corte > 0: perdas.append(("corte de supply (nao entregou)", corte))
    if miss > 0: perdas.append(("venda sem plano (nao previu)", miss))
    perdas.sort(key=lambda x: -x[1])
    if perdas:
        partes.append(" Maiores exposicoes: " + "; ".join(f"{nome} R$ {val:,.0f}" for nome, val in perdas[:3]) + ".")
    return "".join(partes)


# =====================================================================
# MESES AUDITÁVEIS: fechados + o corrente parcial
# =====================================================================
def meses_auditaveis(db: Session) -> List[Dict[str, Any]]:
    """
    Meses que têm plano N-2 E venda. Ordena do mais recente ao mais antigo.
    Marca o mês corrente como parcial.
    """
    rows = db.execute(text("""
        SELECT DISTINCT TO_CHAR(data_pedido,'YYYY-MM-01') AS mes
        FROM fato_vendas
        WHERE data_pedido >= '2026-06-01'
        ORDER BY mes DESC
    """)).fetchall()
    out = []
    for r in rows:
        mes_d = _coerce_mes(r.mes)
        ciclo = ciclo_fonte_do_mes(mes_d)
        # Só lista se existe plano correspondente
        tem_plano = db.execute(text("""
            SELECT 1 FROM fato_ibp_granular
            WHERE ciclo_sop = :c AND mes_projetado = :m LIMIT 1
        """), {"c": ciclo, "m": mes_d}).fetchone()
        if tem_plano:
            out.append({
                "mes": mes_d.strftime("%Y-%m-%d"),
                "label": mes_d.strftime("%m/%Y"),
                "ciclo_fonte": ciclo,
                "em_andamento": _mes_esta_em_andamento(mes_d),
            })
    return out


# =====================================================================
# TESTE MANUAL
# =====================================================================
if __name__ == "__main__":
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        print("=== MESES AUDITAVEIS ===")
        for m in meses_auditaveis(db):
            print("  ", m)

        print("\n=== AUDITORIA junho/2026 ===")
        a = auditoria_mes(db, "2026-06-01", top_n=5)
        print("  ciclo fonte:", a["ciclo_fonte"])
        print("  contagem:", a["contagem"])
        print("  totais:")
        for k, v in a["totais"].items():
            print(f"     {k:28s} = {v}")
        print("\n  INSIGHT:", a["insight"])
        print("\n  TOP 5 PIORES (R$):")
        for l in a["ranking_piores_rs"]:
            print(f"     {l['sku']} {l['descricao'][:25]:25s} plan={int(l['planejado_cx']):>7} vend={int(l['vendido_cx']):>7} gap_rs={l['gap_previsao_rs']:>12,.0f} [{l['rotulo_vies']}]")
        print("\n  TOP 5 MISS (vendeu sem plano):")
        for l in a["ranking_miss"]:
            print(f"     {l['sku']} {l['descricao'][:25]:25s} vend={int(l['vendido_cx']):>7} vend_rs={l['vendido_rs']:>12,.0f}")
    finally:
        db.close()