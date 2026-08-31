"""Motor de PMP (Prazo Médio de Pagamento).

O menor grão do cálculo é o movimento E5.  Assim pagamentos parciais não são
deduplicados e o peso é sempre o valor efetivamente pago.
"""
from datetime import date
import logging
from typing import Any
import pandas as pd

from app.financeiro.s3_store import carregar_todos_mensal, carregar_mensal, carregar_fornecedores

logger = logging.getLogger(__name__)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").fillna(0) if col in df else pd.Series(0, index=df.index)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df else pd.Series("", index=df.index)


def _norm_key(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def _as_filter(value) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, str):
        value = value.split(",")
    values = [str(v).strip().upper() for v in value if str(v).strip()]
    return values or None


def _base(data_ini: date, data_fim: date, motivos=None, tipos=None,
          fornecedores=None, clifor=None) -> pd.DataFrame:
    nf = carregar_todos_mensal("notas_entrada", data_fim)
    tit = carregar_todos_mensal("contas_pagar", data_fim)
    mov = carregar_mensal("movimentacao_bancaria", data_ini, data_fim)
    if tit.empty or mov.empty:
        return pd.DataFrame()
    tit = tit.copy()
    tit["clifor"] = _col(tit, "e2_fornece") if "e2_fornece" in tit else _col(tit, "clifor")
    tit["clifor"] = _norm_key(tit["clifor"])
    mov["clifor"] = (_col(mov, "e5_clifor") if "e5_clifor" in mov
                     else _col(mov, "e5_fornece")).astype(str).str.strip()
    mov["clifor"] = _norm_key(mov["clifor"])
    # E5 and SE2 use different physical names for the same title keys.
    # Canonical columns prevent a fallback CLIFOR-only many-to-many join.
    key_map = {
        "e2_num": "e5_numero", "e2_prefixo": "e5_prefixo",
        "e2_parcela": "e5_parcela", "e2_filial": "e5_filial",
        "e2_tipo": "e5_tipo",
    }
    join_keys = []
    for e2_key, e5_key in key_map.items():
        if e2_key in tit.columns and e5_key in mov.columns:
            canonical = "_join_" + e2_key
            tit[canonical] = _norm_key(tit[e2_key])
            mov[canonical] = _norm_key(mov[e5_key])
            join_keys.append(canonical)
    if join_keys:
        titulo_keys = ["clifor"] + join_keys
        duplicados = int(tit.duplicated(titulo_keys, keep=False).sum())
        if duplicados:
            logger.warning(
                "PMP: %d linhas SE2 repetidas por chave de título; mantendo a ocorrência mais recente.",
                duplicados,
            )
            tit = tit.drop_duplicates(titulo_keys, keep="last")
    mov["e5_valor"] = _num(mov, "e5_valor")
    mov["e5_data"] = pd.to_datetime(_col(mov, "e5_data"), errors="coerce")
    mov = mov[(mov["e5_valor"] > 0) & mov["e5_data"].notna()]
    if mov.empty:
        return pd.DataFrame()
    # E5 é uma base mista: contém recebimentos e pagamentos. O PMP considera
    # somente movimentos que identificam um título a pagar na SE2 pelo CLIFOR
    # e suas chaves; movimentos não casados permanecem fora do PMP, mas devem
    # ser contabilizados separadamente nos diagnósticos de cobertura.
    # CLIFOR é a chave deliberada de fornecedor (loja nunca participa do agrupamento).
    if join_keys:
        base = mov.merge(tit, on=["clifor"] + join_keys,
                         how="inner", suffixes=("", "_e2"),
                         validate="many_to_one")
    else:
        # Some historical SE2 extracts lack title identifiers.  Do not
        # multiply movements: only accept an unambiguous CLIFOR snapshot.
        counts = tit.groupby("clifor").size()
        tit = tit[tit["clifor"].map(counts).eq(1)]
        base = mov.merge(tit, on="clifor", how="inner", suffixes=("", "_e2"),
                         validate="many_to_one")
    if base.empty:
        return base
    if not nf.empty:
        nf = nf.copy()
        nf["clifor"] = _norm_key(_col(nf, "f1_fornece"))
        # Align SF1 names with the canonical SE2 title keys.
        nf["_join_e2_num"] = _norm_key(_col(nf, "f1_doc"))
        nf["_join_e2_prefixo"] = _norm_key(_col(nf, "f1_serie"))
        nf["_join_e2_filial"] = _norm_key(_col(nf, "f1_filial"))
        keys = [c for c in ("_join_e2_num", "_join_e2_prefixo", "_join_e2_filial")
                if c in base]
        if keys:
            cols = keys + ["clifor", "f1_emissao"]
            if "f1_valbrut" in nf.columns:
                cols.append("f1_valbrut")
            if "d1_tp" in nf.columns:
                cols.append("d1_tp")
            n = nf[cols].drop_duplicates(keys + ["clifor"])
            base = base.merge(n, on=["clifor"] + keys, how="left")
    # Garante f1_emissao para Excel (referência — não usada no cálculo)
    if "f1_emissao" not in base:
        base["f1_emissao"] = pd.NaT
    base["f1_emissao"] = pd.to_datetime(base["f1_emissao"], errors="coerce")

    # Data base: sempre e2_emissao (100% de cobertura, sem fallback)
    emissao             = pd.to_datetime(_col(base, "e2_emissao"), errors="coerce")
    vencimento_real     = pd.to_datetime(_col(base, "e2_vencrea"), errors="coerce")
    vencimento_condicao = pd.to_datetime(_col(base, "e2_vencto"),  errors="coerce")

    base["dias_pagamento"]  = (base["e5_data"] - emissao).dt.days.clip(lower=0)
    base["dias_vencimento"] = (vencimento_real     - emissao).dt.days.clip(lower=0)
    base["dias_cond_pag"]   = (vencimento_condicao - emissao).dt.days.clip(lower=0)
    base["fonte_emissao"] = "SE2"
    base = base[base["dias_pagamento"].notna()]
    motivo_values = _as_filter(motivos)
    tipo_values = _as_filter(tipos)
    fornecedor_values = _as_filter(fornecedores) or _as_filter(clifor)
    if motivo_values:
        base = base[_norm_key(_col(base, "e5_motbx")).isin(motivo_values)]
    if tipo_values:
        _d1 = _norm_key(_col(base, "d1_tp")).replace("", "SEM TIPO")
        base = base[_d1.isin(tipo_values)]
    if fornecedor_values:
        base = base[_norm_key(_col(base, "clifor")).isin(fornecedor_values)]
    return base


def _resumo(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"pagamentos": 0, "valor_total": 0.0, "pmp": 0.0}
    valor = float(df["e5_valor"].sum())
    media = lambda coluna: round(float((df[coluna].fillna(0) * df["e5_valor"]).sum() / valor), 2) if valor else 0.0
    pagamento = media("dias_pagamento")
    vencimento = media("dias_vencimento")
    condicao = media("dias_cond_pag")
    return {
        "pagamentos": int(len(df)),
        "valor_total": round(valor, 2),
        "pmp": pagamento,
        "pmp_pagamento": pagamento,
        "pmp_vencimento": vencimento,
        "pmp_cond_pag": condicao,
        "delta_atraso": round(pagamento - condicao, 2),
    }


def _resumo_fornecedores(base: pd.DataFrame, limit: int = 100,
                        offset: int = 0) -> dict[str, Any]:
    if base.empty:
        return {"total": 0, "fornecedores": []}
    nomes = carregar_fornecedores()
    nome_col = next((c for c in ("a2_nome", "a2_nom", "nome", "razao_social") if c in nomes), None)
    lookup = nomes.set_index("clifor")[nome_col].to_dict() if nome_col and "clifor" in nomes else {}
    rows = []
    for clifor, grupo in base.groupby("clifor", dropna=False):
        row = {"clifor": str(clifor), "nome": str(lookup.get(str(clifor), "")), **_resumo(grupo)}
        rows.append(row)
    rows.sort(key=lambda r: r["valor_total"], reverse=True)
    return {"total": len(rows), "fornecedores": rows[offset:offset + limit]}


def calcular_pmp_global(data_ini: date, data_fim: date, motivos=None, tipos=None,
                        fornecedores=None, clifor=None, e5_motbx=None, d1_tp=None,
                        fornecedor=None) -> dict[str, Any]:
    motivos = motivos if motivos is not None else e5_motbx
    tipos = tipos if tipos is not None else d1_tp
    fornecedores = fornecedores if fornecedores is not None else fornecedor
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    dias_periodo = max((data_fim - data_ini).days, 1)
    resultado = _resumo(base)
    resultado["periodo"] = {
        "data_ini": str(data_ini),
        "data_fim": str(data_fim),
        "dias_periodo": dias_periodo,
    }
    resultado["valor_por_dia"] = round(resultado["valor_total"] / dias_periodo, 2)
    return resultado


def calcular_pmp_fornecedores(data_ini: date, data_fim: date,
                              limit: int = 100, offset: int = 0, motivos=None,
                              tipos=None, fornecedores=None, clifor=None,
                              e5_motbx=None, d1_tp=None, fornecedor=None) -> dict[str, Any]:
    motivos = motivos if motivos is not None else e5_motbx
    tipos = tipos if tipos is not None else d1_tp
    fornecedores = fornecedores if fornecedores is not None else fornecedor
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    resumo = _resumo_fornecedores(base, limit, offset)
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
            **resumo}


def calcular_pmp_resumo(data_ini: date, data_fim: date, motivos=None, tipos=None,
                        fornecedores=None, clifor=None) -> dict[str, Any]:
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    dias_periodo = max((data_fim - data_ini).days, 1)
    def _valores(col, empty_label="SEM VALOR"):
        return sorted(_norm_key(_col(base, col)).replace("", empty_label).unique().tolist())
    motivos_disponiveis = _valores("e5_motbx", "SEM VALOR")
    tipos_disponiveis   = _valores("d1_tp",    "SEM TIPO")
    fornecedores_disponiveis = _valores("clifor")
    global_data = _resumo(base)
    global_data["periodo"] = {
        "data_ini": str(data_ini),
        "data_fim": str(data_fim),
        "dias_periodo": dias_periodo,
    }
    global_data["valor_por_dia"] = round(global_data["valor_total"] / dias_periodo, 2)
    return {
        "periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
        "global": global_data,
        "fornecedores": _resumo_fornecedores(base),
        "filtros": {
            "e5_motbx": motivos_disponiveis,
            "d1_tp": tipos_disponiveis,
            "fornecedor": fornecedores_disponiveis,
        },
    }


def calcular_pmp_mensal(data_ini: date, data_fim: date, motivos=None, tipos=None,
                        fornecedores=None, clifor=None) -> dict[str, Any]:
    """
    Evolução mês a mês agrupada pelo mês de pagamento (e5_data).
    Mesmo padrão do PMR mensal: cada mês acumula os pagamentos E5 casados
    com títulos SE2, calculando os três PMPs e o valor pago no período.
    """
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": []}

    base = base.copy()
    base["mes_pag"] = base["e5_data"].dt.to_period("M")

    meses = []
    for mes, g in base.groupby("mes_pag", sort=True):
        r = _resumo(g)
        meses.append({
            "mes":           mes.strftime("%Y-%m"),
            "pagamentos":    r["pagamentos"],
            "valor_total":   r["valor_total"],
            "pmp_pagamento": r["pmp_pagamento"],
            "pmp_vencimento":r["pmp_vencimento"],
            "pmp_cond_pag":  r["pmp_cond_pag"],
            "delta_atraso":  r["delta_atraso"],
        })

    meses.sort(key=lambda m: m["mes"])
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": meses}



_TIPO_DESC: dict[str, str] = {
    "GG":"Gerais/Outros","EM":"Embalagens","MP":"Materia-Prima","MC":"Mat. Consumo",
    "SV":"Servicos","SI":"Serv. Infraestrutura","EP":"Equipamentos","AI":"Ativo Imob.",
    "LI":"Locacao/Imoveis","IN":"Insumos","BN":"Beneficios","AL":"Aluguel",
    "OI":"Outros Insumos","OL":"Outros Locacao","PA":"Prest. Contas",
    "CC":"Custo Comercializ.","ME":"Mat. Escritorio",
}


def calcular_pmp_por_tipo(data_ini: date, data_fim: date, motivos=None, tipos=None,
                          fornecedores=None, clifor=None) -> dict[str, Any]:
    """PMP por tipo compra D1, ordenado por PMP Pagamento crescente."""
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "tipos": []}
    rows = []
    for tp, g in base.groupby("d1_tp", dropna=False):
        tp_str = str(tp).strip().upper() if pd.notna(tp) and str(tp).strip() else "SEM TIPO"
        r = _resumo(g)
        rows.append({"tipo": tp_str, "descricao": _TIPO_DESC.get(tp_str, tp_str), **r})
    rows.sort(key=lambda r: r["pmp_pagamento"])
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "tipos": rows}

def calcular_pmp(data_ini: date, data_fim: date, **filters) -> dict[str, Any]:
    """Alias estável para consumidores que não precisam escolher a granularidade."""
    return {"global": calcular_pmp_global(data_ini, data_fim, **filters),
            "fornecedores": calcular_pmp_fornecedores(data_ini, data_fim, **filters)}


def obter_dados_brutos(data_ini: date, data_fim: date, motivos=None, tipos=None,
                       fornecedores=None, clifor=None) -> pd.DataFrame:
    """Detalhe auditável no grão E5, mantendo campos de título, NF e SD1."""
    return _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)