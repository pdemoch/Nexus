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


def comparativo_plurianual(db: Session, sku: str, mes_referencia=None, razao_social=None,
                           exigir_no_plano: bool = True,
                           ciclo_ativo: Optional[str] = None,
                           cgcs_override: list = None) -> Dict[str, Any]:
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
    # Escopo por cliente (tela de Metas): a trajetoria plurianual passa a ser
    # a daquele SKU naquela razao social, nao o total da empresa.
    # cgcs_override (nivel executivo) tem prioridade sobre razao_social
    if cgcs_override:
        _fpl = "AND v.cgc = ANY(:_cgcs_plur)"
        _ppl = {"_cgcs_plur": cgcs_override}
    elif razao_social:
        _fpl = ("AND v.cgc = ANY(SELECT cgc FROM dim_clientes "
                "WHERE TRIM(razaosocial) = TRIM(:rz))")
        _ppl = {"rz": razao_social}
    else:
        _fpl = ""
        _ppl = {}

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
          """ + _fpl + """
        GROUP BY EXTRACT(YEAR FROM v.data_pedido)
        ORDER BY ano
    """), {"sku": sku, "doy_corte": doy_corte, **_ppl}).fetchall()

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
            """ + _fpl + """
            GROUP BY EXTRACT(YEAR FROM v.data_pedido)
            ORDER BY ano
        """), {"sku": sku, "m": m, **_ppl}).fetchall()
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
def fva_sku_janela(db: Session, sku: str, n_meses: int = 3,
                   razao_social: str = None,
                   cgcs_override: list = None) -> Dict[str, Any]:
    """
    Versão acumulada do FVA: avalia os últimos N meses FECHADOS com dado real
    (M-2 legítimo, piso = junho/2026). Agrega volume por ciclo-fonte de cada
    mês — humano = vol_final do M-2, ia = vol_ia do M-2, vendido = fato_vendas.

    Usa a aderência acumulada (1 − |total_previsto − total_vendido| / total_vendido)
    sobre o somatório do período — assim um erro pontual grande não apaga meses bons.
    Também devolve o detalhamento mês a mês para o dossiê mostrar a evolução.
    """
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    mes_atual = hoje.replace(day=1)
    piso_forecast = CICLO_PISO + relativedelta(months=DEFASAGEM_MESES)  # 06/2026

    # Escopo por CGC: cgcs_override tem prioridade (nivel executivo)
    cgcs = None
    if cgcs_override:
        cgcs = cgcs_override
    elif razao_social:
        cgcs = [r[0] for r in db.execute(text(
            "SELECT cgc FROM dim_clientes WHERE TRIM(razaosocial) = TRIM(:rz)"
        ), {"rz": razao_social}).fetchall()] or ["__SEM_CLIENTE__"]
    f_cli = "AND cgc = ANY(:cgcs)" if cgcs else ""
    p_cli = {"cgcs": cgcs} if cgcs else {}

    meses_avaliar = []
    for i in range(1, n_meses + 1):
        m = mes_atual - relativedelta(months=i)
        if m >= piso_forecast:
            meses_avaliar.append(m)
    meses_avaliar = sorted(meses_avaliar)

    if not meses_avaliar:
        return None

    total_vendido = 0.0
    total_humano  = 0.0
    total_ia      = 0.0
    detalhe       = []

    for mes_d in meses_avaliar:
        ciclo = ciclo_fonte_do_mes(mes_d)

        plano = db.execute(text("""
            SELECT COALESCE(SUM(vol_final), 0) AS humano_cx,
                   COALESCE(SUM(vol_ia), 0)    AS ia_cx
            FROM fato_ibp_granular
            WHERE sku = :sku AND ciclo_sop = :ciclo AND mes_projetado = :mes
            """ + f_cli + """
        """), {"sku": sku, "ciclo": ciclo, "mes": mes_d, **p_cli}).fetchone()

        vendido = db.execute(text("""
            SELECT COALESCE(SUM(qt_pedido), 0)
            FROM fato_vendas
            WHERE sku = :sku AND TO_CHAR(data_pedido, 'YYYY-MM') = :ym
            """ + f_cli + """
        """), {"sku": sku, "ym": mes_d.strftime("%Y-%m"), **p_cli}).scalar()

        h_cx = float(plano.humano_cx or 0)
        i_cx = float(plano.ia_cx or 0)
        v_cx = float(vendido or 0)

        total_humano  += h_cx
        total_ia      += i_cx
        total_vendido += v_cx

        def _ad(p, r):
            return max(0.0, 1 - abs(p - r) / r) if r > 0 else None

        detalhe.append({
            "mes": mes_d.strftime("%Y-%m"),
            "mes_label": mes_d.strftime("%m/%Y"),
            "ciclo_fonte": ciclo,
            "vendido_cx":  round(v_cx, 0),
            "humano_cx":   round(h_cx, 0),
            "ia_cx":       round(i_cx, 0),
            "aderencia_humano": round(_ad(h_cx, v_cx), 4) if _ad(h_cx, v_cx) is not None else None,
            "aderencia_ia":     round(_ad(i_cx, v_cx), 4) if _ad(i_cx, v_cx) is not None else None,
        })

    # Aderência acumulada sobre o total do período
    def _ad_total(previsto, real):
        return max(0.0, 1 - abs(previsto - real) / real) if real > 0 else None

    ad_humano = _ad_total(total_humano, total_vendido)
    ad_ia     = _ad_total(total_ia, total_vendido)

    cand = [(n, a) for n, a in [("HUMANO", ad_humano), ("IA", ad_ia)] if a is not None]
    vencedor = max(cand, key=lambda x: x[1])[0] if cand else "SEM VENDA"

    fva_ia = (ad_humano - ad_ia) if (ad_humano is not None and ad_ia is not None) else None
    baixo_volume = (total_vendido > 0 and total_vendido < 200 * len(meses_avaliar))

    # Insight resumido
    insight_fva = None
    if total_vendido <= 0:
        insight_fva = "Sem vendas nos meses auditados — sem base para avaliar acurácia."
    else:
        periodo = f"{len(meses_avaliar)} meses ({meses_avaliar[0].strftime('%m/%Y')}–{meses_avaliar[-1].strftime('%m/%Y')})"
        v = int(round(total_vendido))
        if vencedor == "IA":
            insight_fva = (f"Nos últimos {periodo}, a IA ({int(round(total_ia))} cx) "
                           f"previu melhor que o humano ({int(round(total_humano))}) "
                           f"— vendeu {v} cx no total.")
        elif vencedor == "HUMANO":
            insight_fva = (f"Nos últimos {periodo}, o humano ({int(round(total_humano))} cx) "
                           f"previu melhor que a IA ({int(round(total_ia))}) "
                           f"— vendeu {v} cx no total.")
        if baixo_volume:
            insight_fva = (insight_fva or "") + " (Volume baixo no período.)"

    return {
        "sku": sku,
        "meses_avaliados": [m.strftime("%Y-%m") for m in meses_avaliar],
        "n_meses": len(meses_avaliar),
        "vendido_cx":  round(total_vendido, 0),
        "humano_cx":   round(total_humano, 0),
        "ia_cx":       round(total_ia, 0),
        "aderencia_humano": round(ad_humano, 4) if ad_humano is not None else None,
        "aderencia_ia":     round(ad_ia, 4)     if ad_ia is not None else None,
        "fva_ia":           round(fva_ia, 4)    if fva_ia is not None else None,
        "vencedor":         vencedor,
        "baixo_volume":     baixo_volume,
        "insight_fva":      insight_fva,
        "detalhe_por_mes":  detalhe,  # para o dossiê mostrar evolução mês a mês
    }



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
    ad_naive = _aderencia(naive_cx, vend_cx) if naive_cx > 0 else None  # mantido no retorno, não decide vencedor

    fva_ia = None
    if ad_humano is not None and ad_ia is not None:
        fva_ia = ad_humano - ad_ia

    # Vencedor: SOMENTE Humano vs IA (a análise de acurácia trabalha entre os
    # dois planejadores reais do sistema — o "ano passado" não é candidato).
    vencedor = "SEM VENDA"
    if vend_cx > 0:
        cand = [(n, a) for n, a in [("HUMANO", ad_humano), ("IA", ad_ia)] if a is not None]
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
        if vencedor == "IA":
            insight_fva = (f"A IA ({int(round(ia_cx))} cx) previu melhor que o humano "
                           f"({int(round(humano_cx))}) — vendeu {v} cx. Considere confiar mais na IA aqui.")
        elif vencedor == "HUMANO":
            insight_fva = (f"O humano ({int(round(humano_cx))} cx) previu melhor que a IA "
                           f"({int(round(ia_cx))}) — vendeu {v} cx.")
        if baixo_volume:
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
def serie_dossie(db: Session, sku: str, ciclo_ativo: str, meses_futuros=None,
                 coluna_meta: str = "vol_final",
                 razao_social: str = None,
                 cgcs_override: list = None) -> Dict[str, Any]:
    """
    Série unificada do gráfico do dossiê.

    coluna_meta: qual coluna de planejamento usar como "Meta" nos meses da
    janela ativa (M+2, M+3, M+4). Cada tela de preenchimento passa a sua:
      - Demanda Marketing  → 'vol_topdown'
      - Gerenciamento      → 'vol_bottomup'
      - Supply             → 'vol_supply'
      - Consenso / default → 'vol_final'

    Para meses passados (fora da janela ativa), a Meta sempre vem do
    vol_final do ciclo M-2 histórico — o compromisso original.
    """
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    mes_atual = hoje.replace(day=1)

    # ESCOPO POR CLIENTE — quando razao_social vem preenchida (tela de Metas),
    # todo o dossie passa a enxergar SOMENTE aquele SKU naquela razao social.
    # Como uma razao social agrega varios CNPJs (ex.: Atacadao S.A. com 10 lojas),
    # o filtro busca todos os CNPJs dela e restringe as queries a esse conjunto.
    # Escopo por CGC: cgcs_override tem prioridade (nível executivo — lista de CGCs
    # dos clientes do executivo). razao_social é o caminho normal (nível cliente).
    cgcs = None
    if cgcs_override:
        cgcs = cgcs_override
    elif razao_social:
        cgcs = [r[0] for r in db.execute(text("""
            SELECT cgc FROM dim_clientes WHERE TRIM(razaosocial) = TRIM(:rz)
        """), {"rz": razao_social}).fetchall()]
        if not cgcs:
            cgcs = ["__SEM_CLIENTE__"]   # falha fechada: nao vaza total da empresa

    f_vendas = "AND cgc = ANY(:cgcs)" if cgcs else ""
    f_ibp    = "AND cgc = ANY(:cgcs)" if cgcs else ""
    p_cgc    = {"cgcs": cgcs} if cgcs else {}

    # 1) Realizado — 24 meses (vendido/faturado).
    realizado = db.execute(text("""
        SELECT TO_CHAR(data_pedido,'YYYY-MM-01') AS mes,
               SUM(qt_pedido) AS vendido_cx, SUM(qtfatura) AS faturado_cx,
               SUM(vl_pedido) AS vendido_rs, SUM(vlfatura) AS faturado_rs
        FROM fato_vendas
        WHERE sku = :sku AND data_pedido >= (CURRENT_DATE - INTERVAL '36 months')
        """ + f_vendas + """
        GROUP BY 1
    """), {"sku": sku, **p_cgc}).fetchall()

    mapa: Dict[str, Any] = {}
    def _novo(mes_key):
        return {"mes": mes_key, "vendido_cx": None, "faturado_cx": None,
                "vendido_rs": None, "faturado_rs": None, "ia_cx": None, "final_cx": None,
                "ia_rs": None, "final_rs": None,
                "final_ciclo_ant_cx": None, "final_ciclo_ant_rs": None,
                "orcamento_rs": None, "ano_ant_cx": None, "ano_ant_rs": None,
                "eh_futuro": False, "humano_hist_cx": None}

    for r in realizado:
        linha = _novo(r.mes)
        linha.update({
            "vendido_cx": int(r.vendido_cx or 0), "faturado_cx": int(r.faturado_cx or 0),
            "vendido_rs": round(float(r.vendido_rs or 0), 2),
            "faturado_rs": round(float(r.faturado_rs or 0), 2),
        })
        # Mes corrente tem venda parcial mas ainda esta em andamento.
        # Marca eh_futuro=True para o front nao calcular BIAS nem incluir
        # na tabela de acuracia (mes aberto nao tem dado real fechado).
        try:
            _md = datetime.datetime.strptime(r.mes, "%Y-%m-%d").date()
            if _md >= mes_atual:
                linha["eh_futuro"] = True
        except (ValueError, TypeError):
            pass
        mapa[r.mes] = linha

    # 1b) Meta humana histórica (fato_previsao_humana: jan/23 → mai/26)
    #     Aparece no gráfico como linha tracejada fina, distinguindo o plano
    #     feito pelo humano no arquivo Excel do vol_final do ciclo Nexus.
    #     NÃO exibida quando coluna_meta="vol_meta" (Metas Comercial):
    #     nesse contexto, a meta relevante é o vol_meta do Nexus (a partir
    #     de jun/2026) e o histórico do Excel não é comparável.
    if coluna_meta != "vol_meta":
        hist_hum = db.execute(text("""
            SELECT TO_CHAR(mes_projetado,'YYYY-MM-01') AS mes,
                   SUM(vol_humano) AS hcx
            FROM fato_previsao_humana
            WHERE sku = :sku AND fonte = 'HISTORICO'
            GROUP BY 1
        """), {"sku": sku}).fetchall()
        for rh in hist_hum:
            if rh.mes in mapa:          # só popula meses com realizado — não cria fantasmas
                mapa[rh.mes]["humano_hist_cx"] = int(rh.hcx or 0)

        # Meses com realizado dentro do intervalo do arquivo histórico (jan/23-mai/26)
        # mas sem meta cadastrada (meta era 0 no Excel, excluída no load) → aparece como 0
        # em vez de gap, para não quebrar a linha do gráfico.
        _hist_ini = datetime.date(2023, 1, 1)
        _hist_fim = datetime.date(2026, 5, 1)
        for _mes_key, _linha in mapa.items():
            try:
                _d = datetime.datetime.strptime(_mes_key, "%Y-%m-%d").date()
                if _hist_ini <= _d <= _hist_fim and _linha.get("humano_hist_cx") is None:
                    _linha["humano_hist_cx"] = 0
            except (ValueError, KeyError):
                pass

    # 2) Meses futuros do ciclo ativo (só previsão).
    if meses_futuros:
        for md in meses_futuros:
            key = md.strftime("%Y-%m-01")
            if key not in mapa:
                mapa[key] = _novo(key)
                mapa[key]["eh_futuro"] = md >= mes_atual

    # 2b) Meses adicionais com dados no ciclo ativo que não estão na janela
    # (ex: set/26 no ciclo 08/2026 — M+5, aparece no fato_ibp_granular mas
    # não é editável. Deve aparecer no gráfico como previsão futura.)
    meses_com_previsao = db.execute(text("""
        SELECT DISTINCT TO_CHAR(mes_projetado,'YYYY-MM-01') AS mes
        FROM fato_ibp_granular
        WHERE sku = :sku AND ciclo_sop = :ciclo
          AND mes_projetado > CURRENT_DATE
        ORDER BY 1
    """), {"sku": sku, "ciclo": ciclo_ativo}).fetchall()
    for r in meses_com_previsao:
        if r.mes not in mapa:
            mapa[r.mes] = _novo(r.mes)
            mapa[r.mes]["eh_futuro"] = True

    # 3) Previsão — TODO o forecast relevante numa query (todos os ciclos).
    #    Traz todas as colunas de planejamento para que coluna_meta possa ser
    #    qualquer uma delas sem precisar de query extra.
    prev_rows = db.execute(text("""
        SELECT ciclo_sop, TO_CHAR(mes_projetado,'YYYY-MM-01') AS mes,
               SUM(vol_ia)       AS ia,
               SUM(vol_final)    AS final,
               SUM(vol_topdown)  AS topdown,
               SUM(vol_bottomup) AS bottomup,
               SUM(vol_supply)   AS supply,
               SUM(vol_ia      * pmv_aplicado) AS ia_rs,
               SUM(vol_final   * pmv_aplicado) AS final_rs,
               SUM(vol_topdown * pmv_aplicado) AS topdown_rs
        FROM fato_ibp_granular
        WHERE sku = :sku
        """ + f_ibp + """
        GROUP BY ciclo_sop, mes_projetado
    """), {"sku": sku, **p_cgc}).fetchall()
    prev_idx = {(p.ciclo_sop, p.mes): p for p in prev_rows}

    def _meta_cx(p: any, coluna: str):
        """Retorna o volume da coluna_meta, com fallback para vol_final. None = sem meta."""
        val = getattr(p, coluna.replace("vol_", ""), None)
        if val is None:
            val = p.final  # fallback seguro
        if val is None:
            return None
        return int(val or 0)

    def _meta_rs(p: any, coluna: str) -> float:
        """Receita da coluna_meta — só topdown tem precomputed; outros estimam via pmv."""
        if coluna == "vol_topdown":
            return round(float(p.topdown_rs or 0), 2)
        return round(float(p.final_rs or 0), 2)

    # Ciclo anterior (para a linha de referência histórica).
    mm, yy = ciclo_ativo.split("/")
    ciclo_ant_data = datetime.date(int(yy), int(mm), 1) - relativedelta(months=1)
    ciclo_anterior = ciclo_ant_data.strftime("%m/%Y")

    # Janela de trabalho REAL do ciclo ativo (M2, M3, M4 — os meses que a tela
    # de preenchimento efetivamente edita). O ciclo pode ter linhas em
    # fato_ibp_granular para outros meses (ex.: o próprio mês de nascimento
    # do ciclo, antes de M+2), que NÃO fazem parte do que está sendo
    # trabalhado agora. Sem este filtro, a linha "final (ciclo atual)" do
    # gráfico pegava qualquer mês que o ciclo tivesse linha — inclusive meses
    # que não são a janela editável — e o número parecia vir "do nada".
    chaves_janela_ativa = {m.strftime("%Y-%m-01") for m in (meses_futuros or [])}

    primeiro_mes_forecast = CICLO_PISO + relativedelta(months=DEFASAGEM_MESES)  # 06/2026
    for key, linha in mapa.items():
        md = datetime.datetime.strptime(key, "%Y-%m-%d").date()
        if md < primeiro_mes_forecast:
            # Antes do primeiro ciclo: sem forecast M-2 real.
            # Se coluna_meta="vol_meta", também zera final_cx para que o front
            # não calcule BIAS com dados históricos irrelevantes para Metas Comercial.
            if coluna_meta == "vol_meta":
                linha["final_cx"] = None
                linha["final_rs"] = None
                linha["ia_cx"]    = None
            continue  # pula populacao de forecast para meses anteriores ao piso

        # LINHA A — Meta nos meses da JANELA ATIVA: usa coluna_meta (ex:
        # vol_topdown quando chamado pela Demanda Marketing). Para meses
        # passados fora da janela, usa sempre vol_final do M-2 histórico
        # (o que foi prometido antes de acontecer — imutável).
        if key in chaves_janela_ativa:
            p_ativo = prev_idx.get((ciclo_ativo, key))
            if p_ativo and (p_ativo.ia is not None or p_ativo.final is not None):
                linha["ia_cx"]    = int(p_ativo.ia or 0)
                linha["final_cx"] = _meta_cx(p_ativo, coluna_meta)  # pode ser None
                linha["ia_rs"]    = round(float(p_ativo.ia_rs or 0), 2)
                linha["final_rs"] = _meta_rs(p_ativo, coluna_meta)
        else:
            # Meses fora da janela ativa: passados, o mês corrente, ou o mês
            # seguinte. A regra M-2 é UNIFORME nos três casos — não existe
            # "mês corrente usa o ciclo ativo direto".
            #
            # BUG CORRIGIDO (achado por inspeção visual do gráfico: ago/26 e
            # set/26 apareciam sem meta nem IA): antes deste fix, meses com
            # md >= mes_atual (incluindo o próprio mês corrente) buscavam em
            # prev_idx.get((ciclo_ativo, key)) — o ciclo ATIVO. Isso fazia
            # sentido ANTES da Fase 2, quando o ciclo ativo gravava M+0..M+4
            # e portanto tinha linha própria para o mês corrente e o seguinte.
            #
            # Depois da Fase 2 (trava HORIZ_DECISAO = M+2..M+4, ver
            # app/core/constants.py), o ciclo ativo NUNCA MAIS grava M+0 nem
            # M+1 — essas linhas simplesmente não existem mais sob o ciclo
            # ativo. O lookup em (ciclo_ativo, key) para o mês corrente e o
            # seguinte sempre retornava None, e a meta/IA sumia do gráfico
            # exatamente nos dois meses mais recentes. Ninguém pegou isso na
            # validação dos marts porque o dossiê não foi migrado (é consulta
            # pontual, não precisava) — mas herdou o efeito colateral da
            # limpeza mesmo assim.
            #
            # A correção: usar ciclo_fonte_do_mes(md) sempre, igual já era
            # feito para meses passados. Para ago/2026, isso resolve para
            # 06/2026 (mes - 2); para set/2026, resolve para 07/2026 — que é
            # exatamente onde o forecaster gravou a previsão M+2 daqueles
            # meses, e é exatamente o compromisso que deve ser comparado
            # contra o realizado quando o mês fechar.
            cf = ciclo_fonte_do_mes(md)
            p_hist = prev_idx.get((cf, key))
            if p_hist and p_hist.ia is not None:
                linha["ia_cx"] = int(p_hist.ia or 0)
                linha["ia_rs"] = round(float(p_hist.ia_rs or 0), 2)
                linha["ciclo_fonte"] = cf
            if p_hist and p_hist.final is not None:
                linha["final_cx"] = int(p_hist.final) if p_hist.final is not None else None
                linha["final_rs"] = round(float(p_hist.final_rs or 0), 2)

        # LINHA B — ciclo ANTERIOR como referência.
        # Regra: só mostra nos meses da JANELA ATIVA do ciclo corrente
        # (M+2 e M+3). Para o ciclo 07/2026 isso é set e out. Quando
        # avançar para 08/2026, vira out e nov. Não aparece em meses
        # passados nem nos já decididos por ciclos anteriores — apenas
        # onde o planejador está decidindo agora, para ele comparar
        # "o que o ciclo anterior previa para esses mesmos meses".
        if key in chaves_janela_ativa:
            p_ant = prev_idx.get((ciclo_anterior, key))
            if p_ant and p_ant.final is not None:
                linha["final_ciclo_ant_cx"] = int(p_ant.final or 0)
                linha["final_ciclo_ant_rs"] = round(float(p_ant.final_rs or 0), 2)



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
        """ + f_vendas + """
        GROUP BY 1
    """), {"sku": sku, **p_cgc}).fetchall()
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



def get_previous_cycle_str(ciclo: str) -> str:
    """'MM/YYYY' do ciclo imediatamente anterior."""
    mm, yy = ciclo.split("/")
    d = datetime.date(int(yy), int(mm), 1) - relativedelta(months=1)
    return d.strftime("%m/%Y")


def _fmt_cx(n):
    try:
        return f"{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "\u2014"

def _fmt_rs(n):
    try:
        return "R$ " + f"{float(n):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "\u2014"

def _pct(x):
    return f"{x*100:+.0f}%"


def montar_dossie(db: Session, sku: str, ciclo_ativo: str, meses_janela,
                  descricao: str = None,
                  coluna_meta: str = "vol_final",
                  razao_social: str = None,
                  cgcs_override: list = None) -> Dict[str, Any]:
    """
    Empacota o dossie COMPLETO de um SKU: serie do grafico + plurianual + FVA +
    comparacoes do mes-foco + insights em texto. Consumido pelos 5 routers.

    coluna_meta: qual coluna usar como "Meta" nos meses da janela ativa.
      - Demanda Marketing  → 'vol_topdown'
      - Gerenciamento      → 'vol_bottomup'
      - Supply             → 'vol_supply'
      - Consenso / default → 'vol_final'
    """
    serie = serie_dossie(db, sku, ciclo_ativo, meses_futuros=meses_janela,
                         coluna_meta=coluna_meta, razao_social=razao_social,
                         cgcs_override=cgcs_override)
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    mes_fechado = (hoje.replace(day=1) - relativedelta(months=1))

    plur = comparativo_plurianual(db, sku, mes_referencia=mes_fechado.strftime("%Y-%m-%d"),
                                  razao_social=razao_social, ciclo_ativo=ciclo_ativo,
                                  cgcs_override=cgcs_override)
    fva = fva_sku_janela(db, sku, n_meses=6, razao_social=razao_social,
                         cgcs_override=cgcs_override)

    ciclo_ant = get_previous_cycle_str(ciclo_ativo)

    col_sql = coluna_meta

    # Escopo por cliente nas comparações
    _cgcs = None
    if cgcs_override:
        # Nível executivo: CGCs passados diretamente pelo router
        _cgcs = cgcs_override
    elif razao_social:
        _cgcs = [r[0] for r in db.execute(text(
            "SELECT cgc FROM dim_clientes WHERE TRIM(razaosocial) = TRIM(:rz)"
        ), {"rz": razao_social}).fetchall()] or ["__SEM_CLIENTE__"]
    _fc = "AND cgc = ANY(:cgcs)" if _cgcs else ""
    _pc = {"cgcs": _cgcs} if _cgcs else {}

    comparacoes_por_mes = []
    insights = []

    for mes_foco in (meses_janela or []):
        plano = db.execute(text(f"""
            SELECT COALESCE(SUM({col_sql}),0) AS cx,
                   COALESCE(SUM({col_sql} * pmv_aplicado),0) AS rs
            FROM fato_ibp_granular
            WHERE sku=:s AND ciclo_sop=:c AND mes_projetado=:m
            """ + _fc + """
        """), {"s": sku, "c": ciclo_ativo, "m": mes_foco, **_pc}).fetchone()

        # Orcamento e corporativo (nao existe por cliente). No dossie de Metas,
        # que e por razao social, ele nao se aplica — fica zerado.
        orc = 0 if razao_social else db.execute(text("""
            SELECT COALESCE(SUM(receita_orcamento),0) FROM fato_orcamento
            WHERE sku=:s AND mes_projetado=:m
        """), {"s": sku, "m": mes_foco}).scalar()

        cant = db.execute(text("""
            SELECT COALESCE(SUM(vol_final),0) AS cx,
                   COALESCE(SUM(vol_final * pmv_aplicado),0) AS rs
            FROM fato_ibp_granular
            WHERE sku=:s AND ciclo_sop=:ca AND mes_projetado=:m
            """ + _fc + """
        """), {"s": sku, "ca": ciclo_ant, "m": mes_foco, **_pc}).fetchone()

        mes_ano_ant = (mes_foco - relativedelta(years=1)).strftime("%Y-%m")
        aant = db.execute(text("""
            SELECT COALESCE(SUM(qt_pedido),0) AS cx, COALESCE(SUM(vl_pedido),0) AS rs
            FROM fato_vendas
            WHERE sku=:s AND TO_CHAR(data_pedido,'YYYY-MM')=:ym
            """ + _fc + """
        """), {"s": sku, "ym": mes_ano_ant, **_pc}).fetchone()

        plano_cx = int(plano.cx or 0)
        plano_rs = float(plano.rs or 0)
        ml = mes_foco.strftime("%m/%Y")

        comparacoes_por_mes.append({
            "mes_foco": mes_foco.strftime("%Y-%m-%d"),
            "mes_label": ml,
            "final_cx": plano_cx,
            "final_rs": round(plano_rs, 2),
            "orcamento_rs": round(float(orc or 0), 2),
            "ciclo_ant_cx": int(cant.cx or 0),
            "ciclo_ant_rs": round(float(cant.rs or 0), 2),
            "ano_ant_cx": int(aant.cx or 0),
            "ano_ant_rs": round(float(aant.rs or 0), 2),
            "ciclo_anterior": ciclo_ant,
        })

        # Insights por mês
        if orc and float(orc) > 0:
            dd = (plano_rs - float(orc)) / float(orc)
            if abs(dd) >= 0.03:
                direc = "acima" if dd > 0 else "abaixo"
                insights.append(f"{ml}: plano ({_fmt_rs(plano_rs)}) esta {_pct(dd)} {direc} do orcamento ({_fmt_rs(orc)}).")
        if cant and cant.cx and int(cant.cx) > 0:
            dv = (plano_cx - int(cant.cx)) / int(cant.cx)
            if abs(dv) >= 0.05:
                direc = "elevou" if dv > 0 else "reduziu"
                insights.append(f"{ml}: do ciclo {ciclo_ant} para {ciclo_ativo}, volume {direc} {_pct(dv)} ({_fmt_cx(cant.cx)} → {_fmt_cx(plano_cx)} cx).")
        if aant and aant.cx and int(aant.cx) > 0:
            da = (plano_cx - int(aant.cx)) / int(aant.cx)
            direc = "acima" if da > 0 else "abaixo"
            insights.append(f"{ml}: plano esta {_pct(da)} {direc} do vendido no mesmo mes do ano passado ({_fmt_cx(aant.cx)} cx).")

    # Insight de tendência plurianual (uma vez, não por mês)
    if plur and plur.get("tendencia_volume") and plur.get("tendencia_pmv"):
        tv, tp = plur["tendencia_volume"], plur["tendencia_pmv"]
        if tv == "DECLINIO" and tp == "CRESCIMENTO":
            insights.append("Volume em queda e preco subindo ao longo dos anos: produto em maturidade - margem sustenta, giro encolhe.")
        elif tv == "CRESCIMENTO" and tp == "CRESCIMENTO":
            insights.append("Volume e preco crescendo: SKU em expansao saudavel.")
        elif tv == "CRESCIMENTO" and tp == "DECLINIO":
            insights.append("Volume cresce mas preco cai: ganho de share via preco - vigie a margem.")

    # Retrocompatibilidade: comparacoes = primeiro mês (o front usa este campo
    # para o bloco de comparações; comparacoes_por_mes expõe todos os meses)
    comparacoes = comparacoes_por_mes[0] if comparacoes_por_mes else None

    # ── Histórico de acurácia: últimos 12 meses fechados (vendido vs meta) ──
    hoje_local = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    mes_atual_local = hoje_local.replace(day=1)

    # Piso de validade da coluna_meta:
    # - vol_meta (Metas Comercial): só existe a partir de jun/2026 (primeiro ciclo Nexus)
    # - vol_final / vol_topdown / etc: usa humano_hist_cx como fallback para períodos anteriores
    PISO_VOL_META = datetime.date(2026, 6, 1)
    usa_piso_meta = (coluna_meta == "vol_meta")

    # Mapeamento coluna_meta → campo na série do dossiê
    # final_cx é o campo genérico que recebe qualquer coluna_meta via _meta_cx()
    # Para vol_meta especificamente, não há campo próprio na série — usa final_cx
    # (que foi populado com vol_meta quando coluna_meta="vol_meta")

    # Índice do fva.detalhe_por_mes para cruzamento rápido por mês (chave "YYYY-MM")
    fva_idx: Dict[str, Any] = {}
    if fva and fva.get("detalhe_por_mes"):
        for det in fva["detalhe_por_mes"]:
            fva_idx[det["mes"]] = det  # chave já vem como "YYYY-MM"

    historico_12m = []
    for entrada in sorted(serie["serie"], key=lambda x: x["mes"], reverse=True):
        try:
            mes_d = datetime.datetime.strptime(entrada["mes"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if mes_d >= mes_atual_local:
            continue   # mês corrente ainda em andamento

        # Piso de jun/2026 para vol_meta: antes disso não há meta de coordenadores
        if usa_piso_meta and mes_d < PISO_VOL_META:
            continue

        vendido = entrada.get("vendido_cx") or 0
        meta    = entrada.get("final_cx")           # Nexus M-2 congelado (ou vol_meta quando coluna_meta="vol_meta")
        if meta is None and not usa_piso_meta:
            meta = entrada.get("humano_hist_cx")    # arquivo histórico jan/23-mai/26 — só para outras colunas
        # Sem venda real: nada a calcular
        if not vendido or vendido <= 0:
            continue
        # Meta None ou zero: houve venda mas sem plano registrado.
        # Inclui na tabela marcado como sem_meta=True para o front
        # exibir a venda sem calcular BIAS (evita -100% sem sentido).
        if meta is None or meta <= 0:
            historico_12m.append({
                "mes":          entrada["mes"],
                "mes_label":    f"{entrada['mes'][5:7]}/{entrada['mes'][2:4]}",
                "vendido_cx":   int(vendido),
                "meta_cx":      None,
                "delta_cx":     None,
                "delta_pct":    None,
                "wmape_pct":    None,
                "ia_cx":        None,
                "bias_ia_pct":  None,
                "wmape_ia_pct": None,
                "fva_pct":      None,
                "sem_meta":     True,
            })
            if len(historico_12m) >= 6:
                break
            continue
        delta_cx  = meta - vendido
        delta_pct = delta_cx / vendido * 100

        # Dados de IA — cruzados com fva.detalhe_por_mes quando disponível
        chave_fva = mes_d.strftime("%Y-%m")
        det = fva_idx.get(chave_fva)
        ia_cx         = float(det["ia_cx"])    if det and det.get("ia_cx")    is not None else None
        ad_humano_det = float(det["aderencia_humano"]) if det and det.get("aderencia_humano") is not None else None
        ad_ia_det     = float(det["aderencia_ia"])     if det and det.get("aderencia_ia")     is not None else None

        # WMAPE e BIAS da IA (mesma lógica do humano, mas com ia_cx vs vendido)
        if ia_cx is not None and vendido > 0:
            bias_ia  = (ia_cx - vendido) / vendido * 100
            wmape_ia = abs(bias_ia)
        else:
            bias_ia  = None
            wmape_ia = None

        # FVA = aderência_humano − aderência_IA  (>0: humano melhor; <0: IA melhor)
        fva_pct = None
        if ad_humano_det is not None and ad_ia_det is not None:
            fva_pct = round((ad_humano_det - ad_ia_det) * 100, 1)

        historico_12m.append({
            "mes":          entrada["mes"],
            "mes_label":    f"{entrada['mes'][5:7]}/{entrada['mes'][2:4]}",
            "vendido_cx":   int(vendido),
            "meta_cx":      int(meta),
            "delta_cx":     int(delta_cx),
            "delta_pct":    round(delta_pct, 1),        # BIAS Humano %
            "wmape_pct":    round(abs(delta_pct), 1),   # WMAPE Humano % (= |BIAS| no nível SKU)
            "ia_cx":        int(ia_cx) if ia_cx is not None else None,
            "bias_ia_pct":  round(bias_ia, 1)  if bias_ia  is not None else None,  # BIAS IA %
            "wmape_ia_pct": round(wmape_ia, 1) if wmape_ia is not None else None,  # WMAPE IA %
            "fva_pct":      fva_pct,  # FVA = ad_humano − ad_ia em pp (>0: humano venceu)
        })
        if len(historico_12m) >= 6:
            break

    return {
        "sku": sku,
        "descricao": descricao or sku,
        "tipo": "sku",
        "serie": serie["serie"],
        "marco_hoje": serie["marco_hoje"],
        "forecast_inicio": serie["forecast_inicio"],
        "comparacoes": comparacoes,             # primeiro mês (retrocompat)
        "comparacoes_por_mes": comparacoes_por_mes,  # todos os meses da janela
        "insights": insights,
        "plurianual": plur,
        "fva": fva,
        "historico_12m": historico_12m,
    }


# =====================================================================
# RESUMO EXECUTIVO DA MARKETING — Aba "Visão Geral"
# =====================================================================
def _doy_corte_hoje() -> int:
    """Dia-do-ano de hoje (UTC-3), para cortar todos os anos no mesmo ponto (YTD)."""
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    return hoje.timetuple().tm_yday


def _meses_auditaveis_janela() -> list:
    """
    Últimos até-3 meses FECHADOS, cortados no piso do sistema (o primeiro mês
    com M-2 real = CICLO_PISO + DEFASAGEM_MESES = 06/2026). Dinâmico: hoje
    (agosto/2026) devolve [jun, jul] (mai cai fora do piso); a partir de
    setembro devolve 3 meses cheios.
    """
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    mes_atual = hoje.replace(day=1)
    piso_forecast = CICLO_PISO + relativedelta(months=DEFASAGEM_MESES)  # 06/2026
    meses = []
    for i in range(1, 4):  # últimos 3 meses fechados (M-1, M-2, M-3)
        m = mes_atual - relativedelta(months=i)
        if m >= piso_forecast:
            meses.append(m)
    return sorted(meses)


def resumo_marketing(db: Session, ciclo_ativo: str) -> Dict[str, Any]:
    """
    Pacote da Aba "Visão Geral" da Demanda Marketing. Para TODOS os SKUs do
    ciclo ativo (o portfólio em planejamento agora):
      - tendencia_volume: vl_pedido por categoria/SKU, últimos anos (YTD comparável)
      - assertividade: aderência Humano/IA/Ano-passado nos últimos meses fechados
        (janela dinâmica, piso 06/2026), por SKU e agregado por categoria
      - tendencia_pmv: PMV ponderado por categoria/SKU, últimos anos (YTD comparável)

    Sem corte/threshold — devolve tudo; o front classifica/ordena.
    """
    doy = _doy_corte_hoje()
    hoje = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()
    ano_atual = hoje.year

    # Portfólio do ciclo ativo: sku -> categoria/descricao
    portfolio = db.execute(text("""
        SELECT DISTINCT f.sku, COALESCE(p.categoria,'SEM CATEGORIA') AS categoria,
               COALESCE(p.descricao, f.sku) AS descricao
        FROM fato_ibp_granular f
        LEFT JOIN dim_produtos p ON p.sku = f.sku
        WHERE f.ciclo_sop = :c
    """), {"c": ciclo_ativo}).fetchall()
    sku_cat = {r.sku: r.categoria for r in portfolio}
    sku_desc = {r.sku: r.descricao for r in portfolio}
    skus_ciclo = list(sku_cat.keys())
    if not skus_ciclo:
        return {"tendencia_volume_sku": [], "tendencia_volume_categoria": [],
                "tendencia_pmv_sku": [], "tendencia_pmv_categoria": [],
                "assertividade_sku": [], "assertividade_categoria": [],
                "meses_auditados": []}

    # -----------------------------------------------------------------
    # 1) TENDÊNCIA DE VOLUME (vl_pedido/qt_pedido) — YTD comparável, por SKU
    # -----------------------------------------------------------------
    vol_rows = db.execute(text("""
        SELECT sku, EXTRACT(YEAR FROM data_pedido)::int AS ano,
               SUM(qt_pedido) AS vol_cx, SUM(vl_pedido) AS vol_rs
        FROM fato_vendas
        WHERE sku = ANY(:skus) AND EXTRACT(DOY FROM data_pedido) <= :doy
        GROUP BY sku, EXTRACT(YEAR FROM data_pedido)
    """), {"skus": skus_ciclo, "doy": doy}).fetchall()

    # organiza por sku -> {ano: (vol_cx, vol_rs)}
    vol_por_sku: Dict[str, Dict[int, tuple]] = {}
    for r in vol_rows:
        vol_por_sku.setdefault(r.sku, {})[int(r.ano)] = (int(r.vol_cx or 0), float(r.vol_rs or 0))

    # NASCIMENTO REAL — a primeira venda de QUALQUER 1 caixa, em qualquer mês,
    # olhando TODO o histórico (não só a janela YTD usada para a tendência).
    # Decouplado do corte por dia-do-ano: se o item nasceu em novembro, o
    # nascimento é 'aquele ano', mesmo que novembro fique fora da janela YTD
    # (que vai só até o dia-do-ano de hoje). Sem isto, um item lançado depois
    # do corte YTD do próprio ano de lançamento "nasceria" só no ano seguinte.
    nascimento_rows = db.execute(text("""
        SELECT sku, MIN(data_pedido) AS primeira_venda
        FROM fato_vendas
        WHERE sku = ANY(:skus) AND qt_pedido > 0
        GROUP BY sku
    """), {"skus": skus_ciclo}).fetchall()
    sku_ano_nascimento: Dict[str, int] = {r.sku: r.primeira_venda.year for r in nascimento_rows}

    # Piso mínimo no ano-base para calcular variação percentual — evita que
    # itens de base minúscula gerem percentuais explosivos. Unidades distintas
    # para volume (caixas) e valor (R$): volume e preço nem sempre andam juntos
    # (subir preço pode reduzir volume — elasticidade), então cada tendência
    # tem seu próprio piso na unidade certa.
    PISO_VOLUME_BASE_CX = 50
    PISO_VALOR_BASE_RS = 5000.0

    def _tendencia_e_variacao(series_por_ano: Dict[int, float], ano_nascimento: Optional[int],
                              piso_base: float = PISO_VOLUME_BASE_CX):
        """
        Últimos 2 anos com dado, CORTANDO antes do nascimento real do item
        (1ª venda de qualquer volume, em qualquer mês — não precisa do piso
        para 'existir'). O piso só decide se o ANO-BASE da comparação é
        robusto o bastante para um percentual confiável; abaixo disso,
        SEM_BASE em vez de um número explosivo. 'piso_base' está na mesma
        unidade da série recebida (caixas OU reais — nunca misturar).
        """
        if ano_nascimento is None:
            return "SEM_DADO", None
        anos_ok = sorted([a for a in series_por_ano.keys() if a >= ano_nascimento])
        if len(anos_ok) < 2:
            return "SEM_BASE", None  # item nasceu recente — sem 2 anos pra comparar ainda
        a_ant, a_rec = anos_ok[-2], anos_ok[-1]
        v_ant, v_rec = series_por_ano.get(a_ant, 0), series_por_ano[a_rec]
        if v_ant < piso_base:
            return "SEM_BASE", None  # ano-base pequeno demais — percentual não é confiável
        var = (v_rec - v_ant) / v_ant
        if var >= 0.05:
            return "CRESCIMENTO", var
        if var <= -0.05:
            return "DECLINIO", var
        return "ESTAVEL", var

    tendencia_volume_sku = []
    for sku, anos in vol_por_sku.items():
        serie_cx = {a: v[0] for a, v in anos.items()}
        serie_rs = {a: v[1] for a, v in anos.items()}
        nasc = sku_ano_nascimento.get(sku)
        # Duas tendências INDEPENDENTES — volume pode cair enquanto valor sobe
        # (elasticidade: preço subiu, vendeu menos caixas, faturou mais por cx).
        tend_cx, var_cx = _tendencia_e_variacao(serie_cx, nasc, PISO_VOLUME_BASE_CX)
        tend_rs, var_rs = _tendencia_e_variacao(serie_rs, nasc, PISO_VALOR_BASE_RS)
        anos_ordenados = sorted(anos.keys())
        tendencia_volume_sku.append({
            "sku": sku, "descricao": sku_desc.get(sku, sku), "categoria": sku_cat.get(sku, "?"),
            "tendencia_cx": tend_cx, "variacao_pct_cx": round(var_cx, 4) if var_cx is not None else None,
            "tendencia_rs": tend_rs, "variacao_pct_rs": round(var_rs, 4) if var_rs is not None else None,
            "vol_cx_atual": anos[anos_ordenados[-1]][0] if anos_ordenados else 0,
            "vol_rs_atual": round(anos[anos_ordenados[-1]][1], 2) if anos_ordenados else 0.0,
            "anos": [{"ano": a, "vol_cx": anos[a][0], "vol_rs": round(anos[a][1], 2)} for a in anos_ordenados],
        })

    # agrega por categoria (soma dos SKUs, ano a ano). Nascimento da categoria
    # = nascimento do SKU mais antigo dela (a categoria "existe" desde que o
    # primeiro item nela vendeu 1 caixa).
    cat_vol: Dict[str, Dict[int, list]] = {}
    cat_ano_nascimento: Dict[str, int] = {}
    for sku, anos in vol_por_sku.items():
        cat = sku_cat.get(sku, "?")
        for ano, (cx, rs) in anos.items():
            slot = cat_vol.setdefault(cat, {}).setdefault(ano, [0, 0.0])
            slot[0] += cx
            slot[1] += rs
        nasc_sku = sku_ano_nascimento.get(sku)
        if nasc_sku is not None:
            cat_ano_nascimento[cat] = min(nasc_sku, cat_ano_nascimento.get(cat, nasc_sku))
    tendencia_volume_categoria = []
    for cat, anos in cat_vol.items():
        serie_cx = {a: v[0] for a, v in anos.items()}
        serie_rs = {a: v[1] for a, v in anos.items()}
        nasc = cat_ano_nascimento.get(cat)
        tend_cx, var_cx = _tendencia_e_variacao(serie_cx, nasc, PISO_VOLUME_BASE_CX)
        tend_rs, var_rs = _tendencia_e_variacao(serie_rs, nasc, PISO_VALOR_BASE_RS)
        anos_ordenados = sorted(anos.keys())
        tendencia_volume_categoria.append({
            "categoria": cat,
            "tendencia_cx": tend_cx, "variacao_pct_cx": round(var_cx, 4) if var_cx is not None else None,
            "tendencia_rs": tend_rs, "variacao_pct_rs": round(var_rs, 4) if var_rs is not None else None,
            "vol_cx_atual": anos[anos_ordenados[-1]][0] if anos_ordenados else 0,
            "vol_rs_atual": round(anos[anos_ordenados[-1]][1], 2) if anos_ordenados else 0.0,
            "anos": [{"ano": a, "vol_cx": anos[a][0], "vol_rs": round(anos[a][1], 2)} for a in anos_ordenados],
        })

    # -----------------------------------------------------------------
    # 2) TENDÊNCIA DE PMV — SEMPRE por agrupamento (SUM(vl)/SUM(qt) do ano),
    #    nunca média de PMVs. Mesmo corte de nascimento real e piso de volume.
    # -----------------------------------------------------------------
    pmv_por_sku: Dict[str, Dict[int, float]] = {}
    for sku, anos in vol_por_sku.items():
        for ano, (cx, rs) in anos.items():
            if cx > 0:
                pmv_por_sku.setdefault(sku, {})[ano] = rs / cx  # agrupado, não média

    tendencia_pmv_sku = []
    for sku, anos in pmv_por_sku.items():
        vol_serie = {a: vol_por_sku[sku][a][0] for a in anos if a in vol_por_sku.get(sku, {})}
        nasceu = sku_ano_nascimento.get(sku)
        anos_ok = sorted([a for a in anos.keys() if nasceu and a >= nasceu])
        if nasceu is None or len(anos_ok) < 2 or vol_serie.get(anos_ok[-2], 0) < PISO_VOLUME_BASE_CX:
            tend, var = "SEM_BASE", None
        else:
            v_ant, v_rec = anos[anos_ok[-2]], anos[anos_ok[-1]]
            var = (v_rec - v_ant) / v_ant if v_ant > 0 else None
            tend = "CRESCIMENTO" if (var or 0) >= 0.05 else "DECLINIO" if (var or 0) <= -0.05 else "ESTAVEL"
        anos_ordenados = sorted(anos.keys())
        tendencia_pmv_sku.append({
            "sku": sku, "descricao": sku_desc.get(sku, sku), "categoria": sku_cat.get(sku, "?"),
            "tendencia": tend, "variacao_pct": round(var, 4) if var is not None else None,
            "pmv_atual": round(anos[anos_ordenados[-1]], 2) if anos_ordenados else 0.0,
            "anos": [{"ano": a, "pmv": round(anos[a], 2)} for a in anos_ordenados],
        })

    # PMV de categoria = SEMPRE agrupado: soma vl_pedido da categoria / soma
    # qt_pedido da categoria, ano a ano. Nunca média dos PMVs dos SKUs (isso
    # daria peso igual a um SKU de 10cx e um de 10.000cx).
    cat_pmv_base: Dict[str, Dict[int, list]] = {}
    for sku, anos in vol_por_sku.items():
        cat = sku_cat.get(sku, "?")
        for ano, (cx, rs) in anos.items():
            slot = cat_pmv_base.setdefault(cat, {}).setdefault(ano, [0, 0.0])
            slot[0] += cx
            slot[1] += rs
    tendencia_pmv_categoria = []
    for cat, anos in cat_pmv_base.items():
        serie_pmv = {a: (v[1] / v[0]) for a, v in anos.items() if v[0] > 0}
        serie_vol = {a: v[0] for a, v in anos.items()}
        nasceu = cat_ano_nascimento.get(cat)
        anos_ok = sorted([a for a in serie_pmv.keys() if nasceu and a >= nasceu])
        if nasceu is None or len(anos_ok) < 2 or serie_vol.get(anos_ok[-2], 0) < PISO_VOLUME_BASE_CX:
            tend, var = "SEM_BASE", None
        else:
            v_ant, v_rec = serie_pmv[anos_ok[-2]], serie_pmv[anos_ok[-1]]
            var = (v_rec - v_ant) / v_ant if v_ant > 0 else None
            tend = "CRESCIMENTO" if (var or 0) >= 0.05 else "DECLINIO" if (var or 0) <= -0.05 else "ESTAVEL"
        anos_ordenados = sorted(serie_pmv.keys())
        tendencia_pmv_categoria.append({
            "categoria": cat, "tendencia": tend, "variacao_pct": round(var, 4) if var is not None else None,
            "pmv_atual": round(serie_pmv[anos_ordenados[-1]], 2) if anos_ordenados else 0.0,
            "anos": [{"ano": a, "pmv": round(serie_pmv[a], 2)} for a in anos_ordenados],
        })

    # -----------------------------------------------------------------
    # 3) ASSERTIVIDADE — janela dinâmica de meses fechados (piso 06/2026)
    # -----------------------------------------------------------------
    meses_janela = _meses_auditaveis_janela()
    assert_sku_acc: Dict[str, Dict[str, float]] = {}  # sku -> {vendido, humano, ia, naive}
    for mes in meses_janela:
        ciclo_fonte = ciclo_fonte_do_mes(mes)
        vendido_rows = db.execute(text("""
            SELECT sku, SUM(qt_pedido) AS cx FROM fato_vendas
            WHERE sku = ANY(:skus) AND TO_CHAR(data_pedido,'YYYY-MM') = :ym
            GROUP BY sku
        """), {"skus": skus_ciclo, "ym": mes.strftime("%Y-%m")}).fetchall()
        vendido_idx = {r.sku: float(r.cx or 0) for r in vendido_rows}

        plano_rows = db.execute(text("""
            SELECT sku, SUM(vol_final) AS humano, SUM(vol_ia) AS ia
            FROM fato_ibp_granular
            WHERE sku = ANY(:skus) AND ciclo_sop = :cf AND mes_projetado = :m
            GROUP BY sku
        """), {"skus": skus_ciclo, "cf": ciclo_fonte, "m": mes}).fetchall()
        plano_idx = {r.sku: (float(r.humano or 0), float(r.ia or 0)) for r in plano_rows}

        for sku in skus_ciclo:
            v = vendido_idx.get(sku, 0.0)
            h, ia = plano_idx.get(sku, (0.0, 0.0))
            acc = assert_sku_acc.setdefault(sku, {"vendido": 0.0, "humano": 0.0, "ia": 0.0, "meses": 0})
            acc["vendido"] += v; acc["humano"] += h; acc["ia"] += ia
            acc["meses"] += 1

    def _aderencia(prev, real):
        if real <= 0:
            return None
        return max(0.0, 1 - abs(prev - real) / real)

    assertividade_sku = []
    for sku, acc in assert_sku_acc.items():
        v = acc["vendido"]
        ad_h = _aderencia(acc["humano"], v)
        ad_ia = _aderencia(acc["ia"], v)
        cands = [c for c in [("HUMANO", ad_h), ("IA", ad_ia)] if c[1] is not None]
        vencedor = max(cands, key=lambda x: x[1])[0] if cands else None
        assertividade_sku.append({
            "sku": sku, "descricao": sku_desc.get(sku, sku), "categoria": sku_cat.get(sku, "?"),
            "vendido_cx": round(v, 0), "humano_cx": round(acc["humano"], 0),
            "ia_cx": round(acc["ia"], 0),
            "aderencia_humano": round(ad_h, 4) if ad_h is not None else None,
            "aderencia_ia": round(ad_ia, 4) if ad_ia is not None else None,
            "vencedor": vencedor, "meses_considerados": acc["meses"],
        })

    # agrega assertividade por categoria (soma volumes, recalcula aderência)
    cat_acc: Dict[str, Dict[str, float]] = {}
    for sku, acc in assert_sku_acc.items():
        cat = sku_cat.get(sku, "?")
        slot = cat_acc.setdefault(cat, {"vendido": 0.0, "humano": 0.0, "ia": 0.0})
        slot["vendido"] += acc["vendido"]; slot["humano"] += acc["humano"]
        slot["ia"] += acc["ia"]

    assertividade_categoria = []
    for cat, acc in cat_acc.items():
        v = acc["vendido"]
        ad_h = _aderencia(acc["humano"], v)
        ad_ia = _aderencia(acc["ia"], v)
        cands = [c for c in [("HUMANO", ad_h), ("IA", ad_ia)] if c[1] is not None]
        vencedor = max(cands, key=lambda x: x[1])[0] if cands else None
        assertividade_categoria.append({
            "categoria": cat, "vendido_cx": round(v, 0), "humano_cx": round(acc["humano"], 0),
            "ia_cx": round(acc["ia"], 0),
            "aderencia_humano": round(ad_h, 4) if ad_h is not None else None,
            "aderencia_ia": round(ad_ia, 4) if ad_ia is not None else None,
            "vencedor": vencedor,
        })

    return {
        "ciclo_ativo": ciclo_ativo,
        "meses_auditados": [m.strftime("%Y-%m") for m in meses_janela],
        "tendencia_volume_sku": tendencia_volume_sku,
        "tendencia_volume_categoria": tendencia_volume_categoria,
        "tendencia_pmv_sku": tendencia_pmv_sku,
        "tendencia_pmv_categoria": tendencia_pmv_categoria,
        "assertividade_sku": assertividade_sku,
        "assertividade_categoria": assertividade_categoria,
    }