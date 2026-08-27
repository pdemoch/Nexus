"""Coleta e análise do agente financeiro.

O modelo recebe somente um contexto calculado no backend. Assim, perguntas e
simulações usam a mesma base E5 -> E1 -> SF2 do painel e não dados enviados
arbitrariamente pelo navegador.
"""

import json
import time
from datetime import date
from typing import Any, Optional

from app.api.routers.agente_kpis import _chamar_claude


_SYSTEM = """Voce e o agente financeiro da Linea Alimentos.
Analise PMR e, quando existirem dados, PMP, PME e CCC. Responda perguntas,
sugira acoes e simule cenarios de clientes, regionais e segmentos.

REGRAS:
- Use somente os numeros do CONTEXTO FINANCEIRO calculado pelo servidor.
- PMR usa cada movimento efetivo da E5, e5_data como periodo e e5_valor como
  peso. E1 fornece vencimentos e SF2 fornece a emissao.
- Em simulacoes, compare sempre faturamento, recebimento, PMR e caixa liberado.
  Nao diga que uma decisao e positiva sem explicitar o trade-off.
- PMP, PME e CCC indisponiveis devem ser declarados como limitacao, nunca
  estimados ou inventados.
- Nao confunda valor faturado com valor recebido.
- Todo valor monetario deve indicar se veio de faturamento SF2 ou recebimento E5.
- Cite a base, o periodo e as limitacoes. Nao cite tabelas, codigo ou S3.
- Responda em portugues do Brasil, com titulos simples e paragrafos curtos."""


def _round(value: Any, places: int = 2) -> float:
    return round(float(value or 0), places)


def construir_contexto(
    data_ini: date,
    data_fim: date,
    segmento: Any = None,
    regional: Any = None,
    cgc: Optional[str] = None,
    status: Any = None,
) -> dict[str, Any]:
    """Calcula o dataset financeiro que fundamenta cada resposta do agente."""
    from app.financeiro import pmr_engine as engine

    started = time.perf_counter()
    base = engine._carregar_base(data_ini, data_fim, segmento, regional, status=status)
    base_escopo = (
        engine._carregar_base(data_ini, data_fim, segmento, regional, cgc, status)
        if cgc else base
    )
    contexto: dict[str, Any] = {
        "metodologia": {
            "periodo_filtro": "e5_data",
            "peso_pmr": "e5_valor",
            "dias_pagamento": "e5_data - f2_emissao",
            "dias_vencimento": "e1_vencrea - f2_emissao",
            "dias_condicao": "e1_vencto - f2_emissao",
            "vinculo": "E5 -> E1 -> SF2",
        },
        "periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
        "filtros": {
            "segmentos": segmento,
            "regionais": regional,
            "status": status,
            "status_opcoes": ["ATIVO", "INATIVO", "SEM STATUS"],
        },
        "indicadores_disponiveis": ["PMR"],
        "indicadores_futuros": ["PMP", "PME", "CCC"],
        "reconciliacao": {"movimentos_validos": len(base)},
    }
    if base.empty:
        contexto.update({"global": {}, "mensal": [], "regionais": [], "clientes": []})
        return contexto

    contexto["global"] = engine.calcular_pmr_global(data_ini, data_fim, segmento, regional, status=status)
    contexto["mensal"] = engine.calcular_pmr_mensal(
        data_ini, data_fim, segmento, regional, status=status
    ).get("meses", [])
    contexto["regionais"] = engine.calcular_pmr_regional(
        data_ini, data_fim, segmento, regional, status
    ).get("regionais", [])
    if cgc:
        contexto["cliente_selecionado"] = {
            "cgc": cgc,
            "dados": engine.calcular_pmr_global(data_ini, data_fim, segmento, regional, cgc),
            "simulacao_exclusao": {
                "recebimento_e5_removido": _round(base_escopo["e5_valor"].sum()),
                "faturamento_sf2_removido": _round(base_escopo.drop_duplicates("Chave_F2")["f2_valbrut"].sum()),
                "pmr_atual": _round((base_escopo["dias_pagamento"] * base_escopo["e5_valor"]).sum() / base_escopo["e5_valor"].sum()),
            },
        }

    # Uma nota pode ter vários movimentos E5. Faturamento é contado uma vez
    # por nota, enquanto recebimento soma cada movimento efetivo.
    chaves_cliente = ["a1_cgc", "a1_nome"]
    base_clientes = base.copy()
    base_clientes[chaves_cliente] = base_clientes[chaves_cliente].fillna("")
    base_clientes["_dias_x_valor"] = (
        base_clientes["dias_pagamento"] * base_clientes["e5_valor"]
    )
    recebimentos = base_clientes.groupby(chaves_cliente, dropna=False).agg(
        movimentos_e5=("e5_valor", "size"),
        recebimento_e5=("e5_valor", "sum"),
        dias_x_valor=("_dias_x_valor", "sum"),
        notas_pagas=("Chave_F2", "nunique"),
        regional=("regional", "first"),
        segmento=("segmento", "first"),
    ).reset_index()

    notas = base_clientes.drop_duplicates("Chave_F2")
    faturamentos = notas.groupby(chaves_cliente, dropna=False).agg(
        faturamento_sf2=("f2_valbrut", "sum"),
    ).reset_index()
    clientes_df = recebimentos.merge(faturamentos, on=chaves_cliente, how="left")
    clientes_df["faturamento_sf2"] = clientes_df["faturamento_sf2"].fillna(0)
    clientes_df["pmr_pagamento"] = (
        clientes_df["dias_x_valor"] / clientes_df["recebimento_e5"].where(
            clientes_df["recebimento_e5"].ne(0)
        )
    ).fillna(0)

    clientes = []
    for row in clientes_df.sort_values("recebimento_e5", ascending=False).head(200).to_dict("records"):
        clientes.append({
            "cgc": str(row["a1_cgc"]), "nome": str(row["a1_nome"]),
            "regional": str(row["regional"]) if row["regional"] else "SEM REGIONAL",
            "segmento": str(row["segmento"]) if row["segmento"] else "SEM SEGMENTO",
            "movimentos_e5": int(row["movimentos_e5"]), "notas_sf2": int(row["notas_pagas"]),
            "faturamento_sf2": _round(row["faturamento_sf2"]),
            "recebimento_e5": _round(row["recebimento_e5"]),
            "pmr_pagamento": _round(row["pmr_pagamento"]),
        })
    clientes.sort(key=lambda row: row["recebimento_e5"], reverse=True)
    contexto["clientes"] = clientes
    contexto["reconciliacao"].update({
        "notas_sf2": int(base["Chave_F2"].nunique()),
        "titulos_e1": int(base["Chave_E5"].nunique()),
        "recebimento_e5": _round(base["e5_valor"].sum()),
    })
    contexto["desempenho"] = {"coleta_calculo_segundos": _round(time.perf_counter() - started, 3)}
    return contexto


def responder_pergunta(
    pergunta: str,
    contexto: dict[str, Any],
    historico: Optional[list[dict[str, str]]] = None,
) -> str:
    dados = json.dumps(contexto, ensure_ascii=False, separators=(",", ":"))
    mensagens = (historico or [])[-6:] + [
        {"role": "user", "content": f"CONTEXTO FINANCEIRO:\n{dados}\n\nPERGUNTA:\n{pergunta}"}
    ]
    return _chamar_claude(_SYSTEM, mensagens, max_tokens=2200)
