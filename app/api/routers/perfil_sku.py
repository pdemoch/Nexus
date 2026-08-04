"""
=====================================================================
PERFIL DO SKU — SERVIÇO CENTRAL DE ANÁLISE (o pilar de todo o sistema)
=====================================================================
O SKU e o pilar. Toda analise — Auditoria, dossies das telas de decisao,
aberturas do dashboard — consome ESTE modulo. Fonte unica, calculo unico.

Passo 1 (este arquivo): NUCLEO DE DIAGNOSTICO DE TRES EIXOS.
Responde, para um SKU num mes fechado, a pergunta central:
  "Onde estamos perdendo dinheiro — no planejamento ou na execucao?"

Tres eixos independentes:
  PLANEJADO (vol_final × pmv)   — o compromisso do S&OP
  VENDIDO   (qt_pedido/vl_pedido) — a demanda real do mercado
  FATURADO  (qtfatura/vlfatura)   — o que o supply conseguiu entregar

Dois gaps que separam os culpados:
  GAP DE PREVISAO = Planejado − Vendido   -> erro do PLANEJADOR (demanda)
  GAP DE EXECUCAO = Vendido − Faturado    -> erro do SUPPLY (entrega)

REGRA TEMPORAL (N-2, anti-look-ahead):
  O mes M e auditado contra o vol_final do ciclo (M − 2 meses). Junho/2026 e
  auditado contra o plano do ciclo 04/2026 — o plano que foi congelado 2 meses
  antes de junho acontecer. Isso responde ao CFO cetico: "este previsto e o que
  voces se comprometeram ANTES do mes, nao o que digitaram depois de ver a venda".
"""

import datetime
from dateutil.relativedelta import relativedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text


# =====================================================================
# REGRA N-2: de qual ciclo vem o "previsto" de um mês
# =====================================================================
# Primeiro ciclo que existiu. Nada antes disto.
CICLO_PISO = datetime.date(2026, 4, 1)
# Defasagem: o ciclo N congela o mês N+2. Logo, o mês M vem do ciclo (M − 2).
DEFASAGEM_MESES = 2


def _coerce_mes(mes) -> datetime.date:
    """Aceita date/datetime/'YYYY-MM-DD'/'YYYY-MM' e devolve o 1º dia do mês."""
    if isinstance(mes, datetime.datetime):
        return mes.date().replace(day=1)
    if isinstance(mes, datetime.date):
        return mes.replace(day=1)
    partes = str(mes).split("T")[0].split("-")
    return datetime.date(int(partes[0]), int(partes[1]), 1)


def ciclo_fonte_do_mes(mes) -> str:
    """
    Dado um mês, devolve o ciclo 'MM/YYYY' cujo vol_final deve ser auditado
    contra a venda daquele mês (regra N-2).

      mes − 2 meses, com piso no primeiro ciclo existente (04/2026).

    Ex.: junho/2026 -> '04/2026' ; julho/2026 -> '05/2026' ; agosto -> '06/2026'.
    Validado contra o banco: fato_ibp_granular tem exatamente esses pares.
    """
    mes_d = _coerce_mes(mes)
    candidato = mes_d - relativedelta(months=DEFASAGEM_MESES)
    if candidato < CICLO_PISO:
        candidato = CICLO_PISO
    return candidato.strftime("%m/%Y")


def _mes_esta_em_andamento(mes) -> bool:
    """True se o mês é o mês-calendário corrente (venda ainda incompleta)."""
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    return _coerce_mes(mes) == hoje.replace(day=1)


# =====================================================================
# NÚCLEO: diagnóstico de três eixos para um SKU num mês
# =====================================================================
def diagnostico_sku_mes(db: Session, sku: str, mes) -> Dict[str, Any]:
    """
    Calcula o diagnóstico de três eixos de UM SKU para UM mês fechado.

    Previsto = vol_final do ciclo N-2 daquele mês (regra anti-look-ahead).
    Vendido  = qt_pedido/vl_pedido de fato_vendas no mês-calendário.
    Faturado = qtfatura/vlfatura de fato_vendas no mês-calendário.

    Retorna todos os números do núcleo + a decomposição preço×volume + o
    diagnóstico textual do quadrante. NAO filtra por cliente ativo nem SKU
    ativo: o escopo do plano ja foi congelado no nascimento do ciclo.
    """
    mes_d = _coerce_mes(mes)
    ciclo = ciclo_fonte_do_mes(mes_d)

    plano = db.execute(text("""
        SELECT
            COALESCE(SUM(vol_final), 0)                    AS plan_cx,
            COALESCE(SUM(vol_final * pmv_aplicado), 0)     AS plan_rs,
            COALESCE(SUM(vol_ia), 0)                       AS ia_cx,
            COALESCE(SUM(vol_ia * pmv_aplicado), 0)        AS ia_rs
        FROM fato_ibp_granular
        WHERE sku = :sku AND ciclo_sop = :ciclo AND mes_projetado = :mes
    """), {"sku": sku, "ciclo": ciclo, "mes": mes_d}).fetchone()

    venda = db.execute(text("""
        SELECT
            COALESCE(SUM(qt_pedido), 0) AS vend_cx, COALESCE(SUM(vl_pedido), 0) AS vend_rs,
            COALESCE(SUM(qtfatura), 0)  AS fat_cx,  COALESCE(SUM(vlfatura), 0)  AS fat_rs
        FROM fato_vendas
        WHERE sku = :sku AND TO_CHAR(data_pedido, 'YYYY-MM') = :ym
    """), {"sku": sku, "ym": mes_d.strftime("%Y-%m")}).fetchone()

    plan_cx = float(plano.plan_cx or 0)
    plan_rs = float(plano.plan_rs or 0)
    ia_cx   = float(plano.ia_cx or 0)
    vend_cx = float(venda.vend_cx or 0)
    vend_rs = float(venda.vend_rs or 0)
    fat_cx  = float(venda.fat_cx or 0)
    fat_rs  = float(venda.fat_rs or 0)

    # Os dois gaps (com sinal).
    gap_previsao_cx = plan_cx - vend_cx   # + planejou a mais / − a menos
    gap_previsao_rs = plan_rs - vend_rs
    gap_execucao_cx = vend_cx - fat_cx    # >= 0: o corte de supply
    gap_execucao_rs = vend_rs - fat_rs

    # PMV nos três eixos.
    pmv_previsto = plan_rs / plan_cx if plan_cx > 0 else 0.0
    pmv_vendido  = vend_rs / vend_cx if vend_cx > 0 else 0.0
    pmv_faturado = fat_rs / fat_cx if fat_cx > 0 else 0.0

    # Decomposição do erro financeiro de PREVISÃO em volume × preço.
    #   efeito_volume = (planejado_cx − vendido_cx) × pmv_previsto
    #   efeito_preco  = (pmv_previsto − pmv_vendido) × vendido_cx
    efeito_volume_rs = gap_previsao_cx * pmv_previsto
    efeito_preco_rs  = (pmv_previsto - pmv_vendido) * vend_cx
    delta_pmv_rs     = pmv_vendido - pmv_previsto
    delta_pmv_pct    = (delta_pmv_rs / pmv_previsto) if pmv_previsto > 0 else 0.0

    # Fill rate (execução do supply, isolada da previsão).
    fill_rate_cx = (fat_cx / vend_cx) if vend_cx > 0 else None
    fill_rate_rs = (fat_rs / vend_rs) if vend_rs > 0 else None

    # Rótulo de negócio para o viés (linguagem do usuario, nao sinal).
    if plan_cx > vend_cx:
        rotulo_vies = "VENDA ABAIXO"   # vendeu menos que o planejado -> superestimou
    elif plan_cx < vend_cx:
        rotulo_vies = "VENDA ACIMA"    # vendeu mais que o planejado -> subestimou
    else:
        rotulo_vies = "NA MEDIDA"

    # Diagnóstico do quadrante: onde se perde dinheiro.
    houve_corte = fat_cx < vend_cx
    if plan_cx > vend_cx and houve_corte:
        diagnostico = "DUPLA FALHA: planejou demais E cortou"
        culpa = "previsao+supply"
    elif plan_cx > vend_cx and not houve_corte:
        diagnostico = "SUPERESTIMOU a demanda (capital em estoque)"
        culpa = "previsao"
    elif plan_cx < vend_cx and houve_corte:
        diagnostico = "SUBESTIMOU E cortou (venda perdida dupla)"
        culpa = "previsao+supply"
    elif plan_cx < vend_cx and not houve_corte:
        diagnostico = "SUBESTIMOU a demanda (venda nao antecipada)"
        culpa = "previsao"
    else:
        diagnostico = "ADERENTE (planejado = vendido)"
        culpa = "nenhum"

    # Quantificacao das perdas por tipo (fatos, sem premissa de custo de carrego).
    valor_planejado_em_excesso = max(0.0, gap_previsao_rs)   # capital imobilizado
    venda_perdida_por_previsao = max(0.0, -gap_previsao_rs)  # nao previu -> nao produziu
    faturamento_perdido_execucao = max(0.0, gap_execucao_rs) # previu mas nao entregou

    return {
        "sku": sku,
        "mes": mes_d.strftime("%Y-%m-%d"),
        "ciclo_fonte": ciclo,
        "mes_em_andamento": _mes_esta_em_andamento(mes_d),

        # Três eixos
        "planejado_cx": round(plan_cx, 1), "planejado_rs": round(plan_rs, 2),
        "vendido_cx": round(vend_cx, 1),   "vendido_rs": round(vend_rs, 2),
        "faturado_cx": round(fat_cx, 1),   "faturado_rs": round(fat_rs, 2),
        "ia_cx": round(ia_cx, 1),

        # Gaps
        "gap_previsao_cx": round(gap_previsao_cx, 1),
        "gap_previsao_rs": round(gap_previsao_rs, 2),
        "gap_execucao_cx": round(gap_execucao_cx, 1),
        "gap_execucao_rs": round(gap_execucao_rs, 2),

        # PMV nos três eixos + variação
        "pmv_previsto": round(pmv_previsto, 2),
        "pmv_vendido": round(pmv_vendido, 2),
        "pmv_faturado": round(pmv_faturado, 2),
        "delta_pmv_rs": round(delta_pmv_rs, 2),
        "delta_pmv_pct": round(delta_pmv_pct, 4),

        # Decomposição preço × volume do erro de previsão
        "efeito_volume_rs": round(efeito_volume_rs, 2),
        "efeito_preco_rs": round(efeito_preco_rs, 2),

        # Fill rate (supply)
        "fill_rate_cx": round(fill_rate_cx, 4) if fill_rate_cx is not None else None,
        "fill_rate_rs": round(fill_rate_rs, 4) if fill_rate_rs is not None else None,

        # Diagnóstico
        "rotulo_vies": rotulo_vies,
        "diagnostico": diagnostico,
        "culpa": culpa,
        "valor_planejado_em_excesso": round(valor_planejado_em_excesso, 2),
        "venda_perdida_por_previsao": round(venda_perdida_por_previsao, 2),
        "faturamento_perdido_execucao": round(faturamento_perdido_execucao, 2),
    }


def gerar_insight(diag: Dict[str, Any]) -> str:
    """
    Frase afirmativa e quantificada — o insight que o CFO cético não consegue
    rebater porque já nomeia o driver dominante do erro com número.
    """
    sku = diag["sku"]
    plan = int(diag["planejado_cx"])
    vend = int(diag["vendido_cx"])
    fat = int(diag["faturado_cx"])

    partes = [f"SKU {sku}: planejou {plan:,} cx, o mercado pediu {vend:,} cx"]
    if fat != vend:
        partes.append(f", entregou {fat:,} cx")
    partes.append(". ")

    ev = diag["efeito_volume_rs"]
    ep = diag["efeito_preco_rs"]
    if abs(ev) >= abs(ep) and abs(ev) > 0:
        driver = f"O erro e dominado por VOLUME (R$ {abs(ev):,.0f})"
        if abs(ep) > 0:
            sinal = "favoravel" if ep < 0 else "desfavoravel"
            driver += f", com preco {sinal} de R$ {abs(ep):,.0f}"
    elif abs(ep) > 0:
        driver = f"O erro e dominado por PRECO (R$ {abs(ep):,.0f})"
    else:
        driver = "Aderente"
    partes.append(driver + ". ")

    dp = diag["delta_pmv_pct"]
    if abs(dp) >= 0.02:
        direcao = "acima" if dp > 0 else "abaixo"
        partes.append(f"PMV realizado {abs(dp)*100:.1f}% {direcao} do previsto. ")

    fr = diag["fill_rate_cx"]
    if fr is not None and fr < 0.98:
        partes.append(f"Supply entregou {fr*100:.0f}% do pedido (corte de R$ {diag['faturamento_perdido_execucao']:,.0f}). ")

    partes.append(f"Diagnostico: {diag['diagnostico']}.")
    return "".join(partes)


# =====================================================================
# BLOCO PLURIANUAL — a trajetória longa do SKU (volume, PMV, fill rate)
# =====================================================================
# Faixas de classificação de tendência (YoY sobre os anos existentes).
FAIXA_CRESCIMENTO = 0.10   # > +10% ao ano = crescimento
FAIXA_DECLINIO = -0.10     # < −10% ao ano = declínio
# Um SKU é "lançamento" se seu primeiro ano de venda é recente.
ANO_LANCAMENTO_CORTE = 2   # primeiro ano dentro dos últimos N anos = lançamento


def _classificar_tendencia(valores_por_ano: list) -> str:
    """
    Recebe [(ano, valor), ...] JÁ ordenado e só com anos existentes.
    Classifica pela variação média ano-a-ano. Nunca assume base zero:
    calcula só sobre os anos presentes (lançamento não vira 'crescimento
    infinito' por ter nascido do nada).
    """
    if len(valores_por_ano) < 2:
        return "SEM SERIE"
    variacoes = []
    for i in range(1, len(valores_por_ano)):
        anterior = valores_por_ano[i - 1][1]
        atual = valores_por_ano[i][1]
        if anterior and anterior > 0:
            variacoes.append((atual - anterior) / anterior)
    if not variacoes:
        return "SEM SERIE"
    media = sum(variacoes) / len(variacoes)
    if media > FAIXA_CRESCIMENTO:
        return "CRESCIMENTO"
    if media < FAIXA_DECLINIO:
        return "DECLINIO"
    return "MATURIDADE"


def comparativo_plurianual(db: Session, sku: str, mes_referencia=None,
                           exigir_no_plano: bool = True,
                           ciclo_ativo: Optional[str] = None) -> Dict[str, Any]:
    """
    Trajetória plurianual do SKU sobre fato_vendas, agrupando cliente por
    RAZAO SOCIAL. Mostra só os anos em que o SKU existiu (ausência NAO vira
    zero: zero em comparativo leria-se 'vendia e parou', o oposto de 'ainda
    nao existia'). Marca lançamentos.

    exigir_no_plano: se True, só calcula para SKU que tem linha no ciclo ativo
    (a regra que impede poluir o comparativo com descontinuados). O historico
    puxado e o do proprio SKU, ate onde a base tiver.

    mes_referencia: se dado, também devolve o MES-EQUIVALENTE (ex.: junho de
    cada ano) para a sazonalidade não se perder na média anual.
    """
    # Guarda: o SKU está no plano do ciclo ativo?
    no_plano = True
    if exigir_no_plano and ciclo_ativo:
        r = db.execute(text("""
            SELECT 1 FROM fato_ibp_granular
            WHERE sku = :sku AND ciclo_sop = :ciclo LIMIT 1
        """), {"sku": sku, "ciclo": ciclo_ativo}).fetchone()
        no_plano = r is not None

    # Série anual — MESMO PERÍODO (YTD comparável). Corta todos os anos no mesmo
    # dia-do-ano de hoje, senão o ano corrente (parcial) parece que despencou
    # frente aos anos cheios. Ex.: hoje 03/ago -> compara jan..03-ago de cada ano.
    hoje_real = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    doy_corte = hoje_real.timetuple().tm_yday  # dia do ano (1..366)

    linhas = db.execute(text("""
        SELECT EXTRACT(YEAR FROM v.data_pedido)::int AS ano,
               COUNT(DISTINCT c.razaosocial)          AS razoes_sociais,
               COALESCE(SUM(v.qt_pedido), 0)          AS vendido_cx,
               COALESCE(SUM(v.vl_pedido), 0)          AS vendido_rs,
               COALESCE(SUM(v.qtfatura), 0)           AS faturado_cx,
               COALESCE(SUM(v.vlfatura), 0)           AS faturado_rs
        FROM fato_vendas v
        LEFT JOIN dim_clientes c ON c.cgc = v.cgc
        WHERE v.sku = :sku
          AND EXTRACT(DOY FROM v.data_pedido) <= :doy_corte
        GROUP BY EXTRACT(YEAR FROM v.data_pedido)
        ORDER BY ano
    """), {"sku": sku, "doy_corte": doy_corte}).fetchall()

    anos = []
    for r in linhas:
        vcx = float(r.vendido_cx or 0)
        vrs = float(r.vendido_rs or 0)
        fcx = float(r.faturado_cx or 0)
        frs = float(r.faturado_rs or 0)
        anos.append({
            "ano": int(r.ano),
            "razoes_sociais": int(r.razoes_sociais or 0),
            "vendido_cx": round(vcx, 0),
            "vendido_rs": round(vrs, 2),
            "faturado_cx": round(fcx, 0),
            "pmv": round(vrs / vcx, 2) if vcx > 0 else 0.0,
            "fill_rate_cx": round(fcx / vcx, 4) if vcx > 0 else None,
            "fill_rate_rs": round(frs / vrs, 4) if vrs > 0 else None,
        })

    anos_existentes = [a["ano"] for a in anos]
    primeiro_ano = min(anos_existentes) if anos_existentes else None
    ano_corrente = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).year

    eh_lancamento = (primeiro_ano is not None
                     and primeiro_ano >= ano_corrente - ANO_LANCAMENTO_CORTE + 1)

    alerta = None
    if eh_lancamento:
        alerta = f"Lancamento em {primeiro_ano} — sem base plurianual completa."
    elif primeiro_ano and primeiro_ano > ano_corrente - 4:
        alerta = f"Historico parcial: primeiro registro em {primeiro_ano}."

    tend_vol = _classificar_tendencia([(a["ano"], a["vendido_cx"]) for a in anos])
    tend_pmv = _classificar_tendencia([(a["ano"], a["pmv"]) for a in anos if a["pmv"] > 0])

    # Mês-equivalente (sazonalidade): mesmo mês-calendário em cada ano.
    mes_equiv = []
    if mes_referencia is not None:
        m = _coerce_mes(mes_referencia).month
        linhas_m = db.execute(text("""
            SELECT EXTRACT(YEAR FROM v.data_pedido)::int AS ano,
                   COALESCE(SUM(v.qt_pedido), 0) AS vendido_cx,
                   COALESCE(SUM(v.vl_pedido), 0) AS vendido_rs
            FROM fato_vendas v
            WHERE v.sku = :sku AND EXTRACT(MONTH FROM v.data_pedido) = :m
            GROUP BY EXTRACT(YEAR FROM v.data_pedido)
            ORDER BY ano
        """), {"sku": sku, "m": m}).fetchall()
        for r in linhas_m:
            vcx = float(r.vendido_cx or 0)
            vrs = float(r.vendido_rs or 0)
            mes_equiv.append({
                "ano": int(r.ano), "mes": m,
                "vendido_cx": round(vcx, 0),
                "pmv": round(vrs / vcx, 2) if vcx > 0 else 0.0,
            })

    meses_pt = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez']
    label_periodo = f"jan–{hoje_real.day:02d}/{meses_pt[hoje_real.month-1]}"
    ano_parcial = (hoje_real.month < 12 or hoje_real.day < 31)

    return {
        "sku": sku,
        "no_plano_ciclo_ativo": no_plano,
        "anos": anos,                       # série YTD comparável; front destaca os 2 últimos + atual
        "anos_existentes": anos_existentes,
        "primeiro_ano": primeiro_ano,
        "eh_lancamento": eh_lancamento,
        "alerta_historico": alerta,
        "tendencia_volume": tend_vol,
        "tendencia_pmv": tend_pmv,
        "mes_equivalente": mes_equiv,       # sazonalidade (se mes_referencia dado)
        # Marcação de período: os anos são comparados NO MESMO PERÍODO (YTD).
        "periodo_comparavel": True,
        "periodo_label": label_periodo,     # ex.: "jan–03/ago"
        "ano_corrente_parcial": ano_parcial,
    }


# =====================================================================
# BLOCO FVA — humano vs IA vs naive, no grão (o loop de aprendizado)
# =====================================================================
def fva_sku(db: Session, sku: str, mes) -> Dict[str, Any]:
    """
    Compara, para o mês fechado, quem previu melhor no grão SKU:
      humano (vol_final) vs IA (vol_ia) vs naive (venda do mesmo mês ano passado).

    aderencia = 1 − |previsto − vendido| / vendido   (piso 0)
    fva_ia = aderencia_humano − aderencia_ia
             ( > 0: humano melhor ;  < 0: a IA sozinha teria acertado mais )

    É o número que vai para o dossiê das telas de decisão: "neste SKU você
    errou X%, a IA errou Y%, o ano passado errou Z%".
    """
    mes_d = _coerce_mes(mes)
    ciclo = ciclo_fonte_do_mes(mes_d)

    plano = db.execute(text("""
        SELECT COALESCE(SUM(vol_final), 0) AS humano_cx,
               COALESCE(SUM(vol_ia), 0)    AS ia_cx
        FROM fato_ibp_granular
        WHERE sku = :sku AND ciclo_sop = :ciclo AND mes_projetado = :mes
    """), {"sku": sku, "ciclo": ciclo, "mes": mes_d}).fetchone()

    vendido = db.execute(text("""
        SELECT COALESCE(SUM(qt_pedido), 0) AS cx
        FROM fato_vendas
        WHERE sku = :sku AND TO_CHAR(data_pedido, 'YYYY-MM') = :ym
    """), {"sku": sku, "ym": mes_d.strftime("%Y-%m")}).scalar()

    naive = db.execute(text("""
        SELECT COALESCE(SUM(qt_pedido), 0) AS cx
        FROM fato_vendas
        WHERE sku = :sku AND TO_CHAR(data_pedido, 'YYYY-MM') = :ym_ant
    """), {"sku": sku, "ym_ant": (mes_d - relativedelta(years=1)).strftime("%Y-%m")}).scalar()

    humano_cx = float(plano.humano_cx or 0)
    ia_cx = float(plano.ia_cx or 0)
    vend_cx = float(vendido or 0)
    naive_cx = float(naive or 0)

    def _aderencia(previsto, real):
        if real <= 0:
            return None
        return max(0.0, 1 - abs(previsto - real) / real)

    ad_humano = _aderencia(humano_cx, vend_cx)
    ad_ia = _aderencia(ia_cx, vend_cx)
    ad_naive = _aderencia(naive_cx, vend_cx) if naive_cx > 0 else None

    fva_ia = None
    if ad_humano is not None and ad_ia is not None:
        fva_ia = ad_humano - ad_ia

    # Quem ganhou, em linguagem direta.
    vencedor = "SEM VENDA"
    if vend_cx > 0:
        cand = [("HUMANO", ad_humano), ("IA", ad_ia)]
        if ad_naive is not None:
            cand.append(("ANO PASSADO", ad_naive))
        cand = [(n, a) for n, a in cand if a is not None]
        if cand:
            vencedor = max(cand, key=lambda x: x[1])[0]

    # Baixo volume: acurácia percentual importa menos (impacto financeiro pequeno).
    LIMIAR_BAIXO_VOLUME = 200  # cx/mês
    baixo_volume = (vend_cx > 0 and vend_cx < LIMIAR_BAIXO_VOLUME)

    # Frase de leitura — evita que um % solto engane. Diz o que os volumes contam.
    insight_fva = None
    if vend_cx <= 0:
        insight_fva = "Sem venda no mês auditado — sem base para avaliar acurácia."
    else:
        v = int(round(vend_cx))
        if vencedor == "ANO PASSADO":
            # o caso do print: humano e IA superestimaram, o naive acertou
            over_h = ((humano_cx - vend_cx) / vend_cx) if vend_cx > 0 else 0
            direcao = "acima" if over_h > 0 else "abaixo"
            insight_fva = (
                f"Repetir o ano passado ({int(round(naive_cx))} cx) teria batido humano "
                f"({int(round(humano_cx))}) e IA ({int(round(ia_cx))}) — vendeu {v} cx. "
                f"O plano ficou {abs(over_h)*100:.0f}% {direcao} da venda. "
                + ("SKU de baixo volume e estável: candidato a previsão simplificada."
                   if baixo_volume else
                   "Reveja o modelo deste SKU.")
            )
        elif vencedor == "IA":
            insight_fva = (f"A IA ({int(round(ia_cx))} cx) previu melhor que o humano "
                           f"({int(round(humano_cx))}) — vendeu {v} cx. Considere confiar mais na IA aqui.")
        elif vencedor == "HUMANO":
            insight_fva = (f"O humano ({int(round(humano_cx))} cx) previu melhor que a IA "
                           f"({int(round(ia_cx))}) — vendeu {v} cx.")
        if baixo_volume and vencedor != "ANO PASSADO":
            insight_fva = (insight_fva or "") + " (Baixo volume: impacto financeiro pequeno.)"

    return {
        "sku": sku,
        "mes": mes_d.strftime("%Y-%m-%d"),
        "ciclo_fonte": ciclo,
        "vendido_cx": round(vend_cx, 0),
        "humano_cx": round(humano_cx, 0),
        "ia_cx": round(ia_cx, 0),
        "naive_cx": round(naive_cx, 0),
        "aderencia_humano": round(ad_humano, 4) if ad_humano is not None else None,
        "aderencia_ia": round(ad_ia, 4) if ad_ia is not None else None,
        "aderencia_naive": round(ad_naive, 4) if ad_naive is not None else None,
        "fva_ia": round(fva_ia, 4) if fva_ia is not None else None,
        "vencedor": vencedor,
        "ia_teria_batido_humano": (fva_ia is not None and fva_ia < 0),
        "baixo_volume": baixo_volume,
        "insight_fva": insight_fva,
    }


# =====================================================================
# TESTE MANUAL — roda contra um SKU conhecido
# =====================================================================
def serie_dossie(db: Session, sku: str, ciclo_ativo: str, meses_futuros=None) -> Dict[str, Any]:
    """
    Série unificada do gráfico do dossiê: realizado (vendido/faturado, 24 meses)
    SOBREPOSTO ao previsto (vol_ia e vol_final), onde cada mês de previsão vem do
    ciclo M-2 que o congelou (regra ciclo_fonte_do_mes). Mais orçamento e ano
    anterior por mês. Otimizada: poucas queries agregadas (não uma por mês).

    Estrutura por mês: mes, vendido_cx, faturado_cx, vendido_rs, faturado_rs,
    ia_cx, final_cx, orcamento_rs, ano_ant_cx, ano_ant_rs, eh_futuro.
    """
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    mes_atual = hoje.replace(day=1)

    # 1) Realizado — 24 meses (vendido/faturado).
    realizado = db.execute(text("""
        SELECT TO_CHAR(data_pedido,'YYYY-MM-01') AS mes,
               SUM(qt_pedido) AS vendido_cx, SUM(qtfatura) AS faturado_cx,
               SUM(vl_pedido) AS vendido_rs, SUM(vlfatura) AS faturado_rs
        FROM fato_vendas
        WHERE sku = :sku AND data_pedido >= (CURRENT_DATE - INTERVAL '24 months')
        GROUP BY 1
    """), {"sku": sku}).fetchall()

    mapa: Dict[str, Any] = {}
    def _novo(mes_key):
        return {"mes": mes_key, "vendido_cx": None, "faturado_cx": None,
                "vendido_rs": None, "faturado_rs": None, "ia_cx": None, "final_cx": None,
                "orcamento_rs": None, "ano_ant_cx": None, "ano_ant_rs": None,
                "eh_futuro": False}

    for r in realizado:
        linha = _novo(r.mes)
        linha.update({
            "vendido_cx": int(r.vendido_cx or 0), "faturado_cx": int(r.faturado_cx or 0),
            "vendido_rs": round(float(r.vendido_rs or 0), 2),
            "faturado_rs": round(float(r.faturado_rs or 0), 2),
        })
        mapa[r.mes] = linha

    # 2) Meses futuros do ciclo ativo (só previsão).
    if meses_futuros:
        for md in meses_futuros:
            key = md.strftime("%Y-%m-01")
            if key not in mapa:
                mapa[key] = _novo(key)
                mapa[key]["eh_futuro"] = md >= mes_atual

    # 3) Previsão — TODO o forecast relevante numa query. Traz vol_ia/vol_final
    #    de todos os ciclos, e depois filtra por mês pegando o ciclo M-2 de cada.
    prev_rows = db.execute(text("""
        SELECT ciclo_sop, TO_CHAR(mes_projetado,'YYYY-MM-01') AS mes,
               SUM(vol_ia) AS ia, SUM(vol_final) AS final
        FROM fato_ibp_granular
        WHERE sku = :sku
        GROUP BY ciclo_sop, mes_projetado
    """), {"sku": sku}).fetchall()
    # indexa por (ciclo, mes)
    prev_idx = {(p.ciclo_sop, p.mes): p for p in prev_rows}
    # Primeiro mês com previsão M-2 LEGÍTIMA: o ciclo-piso (04/2026) só congela
    # com 2 meses de antecedência o mês 06/2026. Antes disso, o "ciclo fonte"
    # seria anterior ao piso (inexistente) e cairia forçado no piso — o que
    # compararia o plano quase consigo mesmo. Então só exibimos forecast a
    # partir de 06/2026 (M-2 real), mantendo a linha de previsão honesta.
    primeiro_mes_forecast = CICLO_PISO + relativedelta(months=DEFASAGEM_MESES)
    for key, linha in mapa.items():
        md = datetime.datetime.strptime(key, "%Y-%m-%d").date()
        if md < primeiro_mes_forecast:
            continue  # abril/maio: sem M-2 real, mostra só realizado
        cf = ciclo_fonte_do_mes(md)
        p = prev_idx.get((cf, key))
        if p and (p.ia is not None or p.final is not None):
            linha["ia_cx"] = int(p.ia or 0)
            linha["final_cx"] = int(p.final or 0)
            linha["ciclo_fonte"] = cf

    # 4) Orçamento — todo de uma vez.
    orc_rows = db.execute(text("""
        SELECT TO_CHAR(mes_projetado,'YYYY-MM-01') AS mes, SUM(receita_orcamento) AS rec
        FROM fato_orcamento WHERE sku = :sku GROUP BY 1
    """), {"sku": sku}).fetchall()
    orc_idx = {o.mes: float(o.rec or 0) for o in orc_rows}
    for key, linha in mapa.items():
        if key in orc_idx:
            linha["orcamento_rs"] = round(orc_idx[key], 2)

    # 5) Ano anterior — todo de uma vez (desloca +1 ano na indexação).
    ant_rows = db.execute(text("""
        SELECT TO_CHAR(data_pedido,'YYYY-MM-01') AS mes,
               SUM(qt_pedido) AS cx, SUM(vl_pedido) AS rs
        FROM fato_vendas
        WHERE sku = :sku AND data_pedido >= (CURRENT_DATE - INTERVAL '36 months')
        GROUP BY 1
    """), {"sku": sku}).fetchall()
    ant_idx = {a.mes: a for a in ant_rows}
    for key, linha in mapa.items():
        md = datetime.datetime.strptime(key, "%Y-%m-%d").date()
        key_ant = (md - relativedelta(years=1)).strftime("%Y-%m-01")
        a = ant_idx.get(key_ant)
        if a and a.cx:
            linha["ano_ant_cx"] = int(a.cx or 0)
            linha["ano_ant_rs"] = round(float(a.rs or 0), 2)

    serie = sorted(mapa.values(), key=lambda x: x["mes"])
    return {
        "sku": sku,
        "serie": serie,
        "marco_hoje": mes_atual.strftime("%Y-%m-01"),
        "forecast_inicio": (CICLO_PISO + relativedelta(months=DEFASAGEM_MESES)).strftime("%Y-%m-01"),
    }



    # Uso: python perfil_sku.py   (com a app no path e o banco acessivel)
    from app.core.database import SessionLocal
    db = SessionLocal()
    SKU = "410025614"
    try:
        d = diagnostico_sku_mes(db, SKU, "2026-06-01")
        print(f"\n=== DIAGNOSTICO {SKU} / junho-2026 (ciclo fonte: {d['ciclo_fonte']}) ===")
        for k, v in d.items():
            print(f"  {k:32s} = {v}")
        print("\n=== INSIGHT ===")
        print(" ", gerar_insight(d))

        pl = comparativo_plurianual(db, SKU, mes_referencia="2026-06-01", ciclo_ativo="07/2026")
        print(f"\n=== PLURIANUAL {SKU}  (no plano 07/2026: {pl['no_plano_ciclo_ativo']}) ===")
        print(f"  primeiro_ano={pl['primeiro_ano']}  lancamento={pl['eh_lancamento']}  "
              f"tend_volume={pl['tendencia_volume']}  tend_pmv={pl['tendencia_pmv']}")
        if pl['alerta_historico']:
            print(f"  ALERTA: {pl['alerta_historico']}")
        for a in pl["anos"]:
            print(f"   {a['ano']}: {int(a['vendido_cx']):>8} cx | PMV {a['pmv']:>6} | "
                  f"{a['razoes_sociais']:>3} razoes | fill {a['fill_rate_cx']}")

        fva = fva_sku(db, SKU, "2026-06-01")
        print(f"\n=== FVA {SKU} / junho-2026 ===")
        print(f"  vendido={int(fva['vendido_cx'])}  humano={int(fva['humano_cx'])}  "
              f"ia={int(fva['ia_cx'])}  naive={int(fva['naive_cx'])}")
        print(f"  aderencia: humano={fva['aderencia_humano']}  ia={fva['aderencia_ia']}  "
              f"naive={fva['aderencia_naive']}")
        print(f"  FVA_ia={fva['fva_ia']}  vencedor={fva['vencedor']}  "
              f"IA_teria_batido={fva['ia_teria_batido_humano']}")
    finally:
        db.close()