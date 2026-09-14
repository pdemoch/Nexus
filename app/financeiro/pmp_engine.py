"""Motor de PMP (Prazo Médio de Pagamento).

O menor grão do cálculo é o movimento E5.  Assim pagamentos parciais não são
deduplicados e o peso é sempre o valor efetivamente pago.
"""
from datetime import date
import logging
import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

from app.financeiro.s3_store import (
    carregar_todos_mensal,
    carregar_mensal,
    carregar_fornecedores,
    carregar_sb1,
)

logger = logging.getLogger(__name__)
SEM_VALOR = "SEM VALOR"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").fillna(0) if col in df else pd.Series(0, index=df.index)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df else pd.Series("", index=df.index)


def _finite_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return round(number, 2) if math.isfinite(number) else default


def _date_text(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.strftime("%Y-%m-%d") if not pd.isna(parsed) else ""


def _norm_key(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def _filter_key(series: pd.Series) -> pd.Series:
    return _norm_key(series).replace("", SEM_VALOR)


def _first_col(df: pd.DataFrame, names) -> str:
    lower_map = {str(col).lower(): col for col in df.columns}
    return next((lower_map[name.lower()] for name in names if name.lower() in lower_map), "")


def _as_filter(value) -> Optional[List[str]]:
    if not value:
        return None
    if isinstance(value, str):
        value = value.split(",")
    values = [str(v).strip().upper() for v in value if str(v).strip()]
    return values or None


@lru_cache(maxsize=16)
def _base_cached(data_ini: date, data_fim: date, motivos: Optional[Tuple[str, ...]],
                 tipos: Optional[Tuple[str, ...]], fornecedores: Optional[Tuple[str, ...]],
                 clifor: Optional[Tuple[str, ...]]) -> pd.DataFrame:
    # A leitura começa na E5: só depois voltamos para o título SE2 e para a
    # nota SF1/F1 que contextualiza o pagamento.
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
        "e2_tipo": "e5_tipo", "e2_loja": "e5_loja",
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
        nf["_join_e2_loja"] = _norm_key(_col(nf, "f1_loja"))
        keys = [c for c in ("_join_e2_num", "_join_e2_prefixo", "_join_e2_filial")
                if c in base]
        if "_join_e2_loja" in base:
            keys.append("_join_e2_loja")
        if keys:
            group_keys = ["clifor"] + keys
            cols = group_keys + ["f1_emissao"]
            if "f1_valbrut" in nf.columns:
                cols.append("f1_valbrut")
            for c in ("f1_doc", "f1_serie", "f1_filial", "f1_loja"):
                if c in nf.columns:
                    cols.append(c)
            if "d1_tp" in nf.columns:
                cols.append("d1_tp")
            n = nf[cols].copy()
            if "d1_tp" in n.columns:
                n["_d1_tipos"] = _filter_key(n["d1_tp"])
            agg = {"f1_emissao": "first"}
            if "f1_valbrut" in n.columns:
                agg["f1_valbrut"] = "sum"
            for c in ("f1_doc", "f1_serie", "f1_filial", "f1_loja"):
                if c in n.columns:
                    agg[c] = "first"
            if "_d1_tipos" in n.columns:
                agg["_d1_tipos"] = lambda s: "|".join(sorted(set(x for x in s if x)))
            n = n.groupby(group_keys, dropna=False, as_index=False).agg(agg)
            base = base.merge(n, on=group_keys, how="left")
    emissao_titulo = pd.to_datetime(_col(base, "e2_emissao"), errors="coerce")
    vencimento_real = pd.to_datetime(_col(base, "e2_vencrea"), errors="coerce")
    # SE2 possui dois vencimentos distintos:
    # E2_VENC = vencimento contratual/original da condição de pagamento;
    # E2_VENCREA = vencimento real negociado. Algumas extrações antigas
    # nomeavam E2_VENC como E2_VENCTO, por isso mantemos esse fallback.
    vencimento_condicao = pd.to_datetime(
        _col(base, "e2_venc") if "e2_venc" in base.columns else _col(base, "e2_vencto"),
        errors="coerce",
    )
    # A emissão exigida para o PMP é a do título SE2. A SF1/F1 é apenas
    # enriquecimento; sem emissão do título não existe origem temporal válida.
    base["dias_pagamento"] = (base["e5_data"] - emissao_titulo).dt.days.clip(lower=0)
    base["dias_vencimento"] = (vencimento_real - emissao_titulo).dt.days
    base["dias_cond_pag"] = (vencimento_condicao - emissao_titulo).dt.days
    base["_vencimento_condicao"] = vencimento_condicao
    base["dias_vencimento"] = base["dias_vencimento"].clip(lower=0)
    base["dias_cond_pag"] = base["dias_cond_pag"].clip(lower=0)
    base = base[emissao_titulo.notna() & base["dias_pagamento"].notna()]
    motivo_values = _as_filter(motivos)
    tipo_values = _as_filter(tipos)
    fornecedor_values = _as_filter(fornecedores) or _as_filter(clifor)
    if motivo_values:
        base = base[_filter_key(_col(base, "e5_motbx")).isin(motivo_values)]
    if tipo_values:
        if "_d1_tipos" in base:
            base = base[base["_d1_tipos"].fillna("").apply(
                lambda raw: bool(set(str(raw).split("|")) & set(tipo_values))
            )]
        else:
            base = base[_filter_key(_col(base, "d1_tp")).isin(tipo_values)]
    if fornecedor_values:
        base = base[_filter_key(_col(base, "clifor")).isin(fornecedor_values)]
    return base


def _base(data_ini: date, data_fim: date, motivos=None, tipos=None,
          fornecedores=None, clifor=None) -> pd.DataFrame:
    def _key(value) -> Optional[Tuple[str, ...]]:
        normalized = _as_filter(value)
        return tuple(normalized) if normalized else None

    return _base_cached(data_ini, data_fim, _key(motivos), _key(tipos),
                        _key(fornecedores), _key(clifor)).copy()


def _filtrar_base_df(base: pd.DataFrame, motivos=None, tipos=None,
                    fornecedores=None, clifor=None) -> pd.DataFrame:
    if base.empty:
        return base
    out = base
    motivo_values = _as_filter(motivos)
    tipo_values = _as_filter(tipos)
    fornecedor_values = _as_filter(fornecedores) or _as_filter(clifor)
    if motivo_values:
        out = out[_filter_key(_col(out, "e5_motbx")).isin(motivo_values)]
    if tipo_values:
        if "_d1_tipos" in out:
            out = out[out["_d1_tipos"].fillna("").apply(
                lambda raw: bool(set(str(raw).split("|")) & set(tipo_values))
            )]
        else:
            out = out[_filter_key(_col(out, "d1_tp")).isin(tipo_values)]
    if fornecedor_values:
        out = out[_filter_key(_col(out, "clifor")).isin(fornecedor_values)]
    return out


def _valores_filtro(base: pd.DataFrame, coluna: str) -> List[str]:
    if base.empty:
        return []
    if coluna == "d1_tp" and "_d1_tipos" in base:
        valores = set()
        for raw in base["_d1_tipos"].fillna(""):
            valores.update(x for x in str(raw).split("|") if x)
        return sorted(valores)
    return sorted(_filter_key(_col(base, coluna)).unique().tolist())


def listar_filtros_pmp(data_ini: date, data_fim: date, motivos=None, tipos=None,
                       fornecedores=None, clifor=None) -> Dict[str, List[str]]:
    base = _base(data_ini, data_fim)
    filtros = {
        "e5_motbx": _valores_filtro(
            _filtrar_base_df(base, tipos=tipos, fornecedores=fornecedores, clifor=clifor),
            "e5_motbx",
        ),
        "d1_tp": _valores_filtro(
            _filtrar_base_df(base, motivos=motivos, fornecedores=fornecedores, clifor=clifor),
            "d1_tp",
        ),
        "fornecedor": _valores_filtro(
            _filtrar_base_df(base, motivos=motivos, tipos=tipos),
            "clifor",
        ),
    }
    filtros["motivos"] = filtros["e5_motbx"]
    filtros["tipos"] = filtros["d1_tp"]
    filtros["fornecedores"] = filtros["fornecedor"]
    filtros["clifor"] = filtros["fornecedor"]
    return filtros


def _resumo(df: pd.DataFrame) -> Dict[str, Any]:
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
                        offset: int = 0) -> Dict[str, Any]:
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


def _resumo_mensal(base: pd.DataFrame, data_ini: date, data_fim: date) -> Dict[str, Any]:
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "meses": []}
    base = base.copy()
    base["_mes"] = pd.to_datetime(base["e5_data"]).dt.to_period("M")
    rows = []
    for mes, grupo in base.groupby("_mes"):
        resumo = _resumo(grupo)
        rows.append({"mes": str(mes), "movimentos_e5": resumo["pagamentos"],
                     "valor_total": resumo["valor_total"],
                     "pmp_pagamento": resumo["pmp_pagamento"],
                     "pmp_vencimento": resumo["pmp_vencimento"],
                     "pmp_cond_pag": resumo["pmp_cond_pag"],
                     "delta_atraso": resumo["delta_atraso"]})
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
            "meses": sorted(rows, key=lambda row: row["mes"])}


def _resumo_tipos(base: pd.DataFrame, data_ini: date, data_fim: date) -> Dict[str, Any]:
    if base.empty:
        return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "tipos": []}
    rows = []
    if "_d1_tipos" in base:
        tipos = _valores_filtro(base, "d1_tp")
        for tipo in tipos:
            grupo = base[base["_d1_tipos"].fillna("").apply(lambda raw: tipo in str(raw).split("|"))]
            rows.append({"tipo": str(tipo), **_resumo(grupo)})
    else:
        for tipo, grupo in base.assign(_tipo=_filter_key(_col(base, "d1_tp"))).groupby("_tipo"):
            rows.append({"tipo": str(tipo), **_resumo(grupo)})
    rows.sort(key=lambda row: row["valor_total"], reverse=True)
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)}, "tipos": rows}


def _lookup_descricoes_sb1() -> Dict[str, str]:
    sb1 = carregar_sb1()
    if sb1.empty:
        return {}
    cod_col = _first_col(sb1, ("b1_cod", "codigo", "cod", "sku", "produto"))
    desc_col = _first_col(sb1, ("b1_desc", "descricao", "desc", "produto_descricao"))
    if not cod_col or not desc_col:
        return {}
    codigos = _norm_key(sb1[cod_col])
    descricoes = sb1[desc_col].fillna("").astype(str).str.strip()
    return {
        str(codigo): str(descricao)
        for codigo, descricao in zip(codigos, descricoes)
        if str(codigo) and str(descricao)
    }


def _lookup_itens_d1(data_fim: date, clifor: str) -> Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]]:
    nf = carregar_todos_mensal("notas_entrada", data_fim)
    if nf.empty:
        return {}
    nf = nf.copy()
    nf["clifor"] = _norm_key(_col(nf, "f1_fornece"))
    nf = nf[nf["clifor"].eq(str(clifor).strip().upper())]
    if nf.empty:
        return {}

    cod_col = _first_col(nf, ("d1_cod", "d1_codigo", "d1_produto", "d1_item"))
    if not cod_col:
        return {}
    quant_col = _first_col(nf, ("d1_quant", "d1_qtd", "d1_quantidade"))
    vunit_col = _first_col(nf, ("d1_vunit", "d1_vlrunit", "d1_prunit"))
    total_col = _first_col(nf, ("d1_total", "d1_valor", "d1_vtotal"))
    tp_col = _first_col(nf, ("d1_tp", "d1_tipo"))

    descricoes = _lookup_descricoes_sb1()
    lookup = {}
    for row in nf.to_dict("records"):
        key = (
            str(row.get("clifor", "")).strip().upper(),
            str(row.get("f1_doc", "")).strip().upper(),
            str(row.get("f1_serie", "")).strip().upper(),
            str(row.get("f1_filial", "")).strip().upper(),
            str(row.get("f1_loja", "")).strip().upper(),
        )
        codigo = str(row.get(cod_col, "")).strip().upper()
        if not codigo:
            continue
        item = {
            "codigo": codigo,
            "descricao": descricoes.get(codigo, ""),
            "tipo_d1": str(row.get(tp_col, "") or SEM_VALOR).strip() if tp_col else SEM_VALOR,
            "quantidade": _finite_float(row.get(quant_col)) if quant_col else 0.0,
            "valor_unitario": _finite_float(row.get(vunit_col)) if vunit_col else 0.0,
            "valor_total": _finite_float(row.get(total_col)) if total_col else 0.0,
        }
        lookup.setdefault(key, []).append(item)
    return lookup


def calcular_pmp_global(data_ini: date, data_fim: date, motivos=None, tipos=None,
                        fornecedores=None, clifor=None, e5_motbx=None, d1_tp=None,
                        fornecedor=None) -> Dict[str, Any]:
    motivos = motivos if motivos is not None else e5_motbx
    tipos = tipos if tipos is not None else d1_tp
    fornecedores = fornecedores if fornecedores is not None else fornecedor
    resumo = _resumo(_base(data_ini, data_fim, motivos, tipos, fornecedores, clifor))
    dias_periodo = max((data_fim - data_ini).days, 1)
    resumo["valor_por_dia"] = round(resumo["valor_total"] / dias_periodo, 2)
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim),
                         "dias_periodo": dias_periodo}, **resumo}


def calcular_pmp_fornecedores(data_ini: date, data_fim: date,
                              limit: int = 100, offset: int = 0, motivos=None,
                              tipos=None, fornecedores=None, clifor=None,
                              e5_motbx=None, d1_tp=None, fornecedor=None) -> Dict[str, Any]:
    motivos = motivos if motivos is not None else e5_motbx
    tipos = tipos if tipos is not None else d1_tp
    fornecedores = fornecedores if fornecedores is not None else fornecedor
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    resumo = _resumo_fornecedores(base, limit, offset)
    return {"periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim)},
            **resumo}


def calcular_pmp_resumo(data_ini: date, data_fim: date, motivos=None, tipos=None,
                        fornecedores=None, clifor=None) -> Dict[str, Any]:
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    global_resumo = _resumo(base)
    dias_periodo = max((data_fim - data_ini).days, 1)
    global_resumo["valor_por_dia"] = round(global_resumo["valor_total"] / dias_periodo, 2)
    return {
        "periodo": {"data_ini": str(data_ini), "data_fim": str(data_fim),
                    "dias_periodo": dias_periodo},
        "global": global_resumo,
        "fornecedores": _resumo_fornecedores(base),
        "evolucao": _resumo_mensal(base, data_ini, data_fim),
        "tipos": _resumo_tipos(base, data_ini, data_fim),
        "filtros": listar_filtros_pmp(data_ini, data_fim, motivos, tipos, fornecedores, clifor),
    }


def calcular_pmp_mensal(data_ini: date, data_fim: date, motivos=None, tipos=None,
                        fornecedores=None, clifor=None) -> Dict[str, Any]:
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    return _resumo_mensal(base, data_ini, data_fim)


def calcular_pmp_tipos(data_ini: date, data_fim: date, motivos=None, tipos=None,
                       fornecedores=None, clifor=None) -> Dict[str, Any]:
    base = _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
    return _resumo_tipos(base, data_ini, data_fim)


def obter_pagamentos_fornecedor(data_ini: date, data_fim: date, clifor: str,
                                motivos=None, tipos=None) -> Dict[str, Any]:
    base = _base(data_ini, data_fim, motivos, tipos, [clifor])
    if base.empty:
        return {"clifor": clifor, "nome": None, "total": 0, "resumo": {}, "pagamentos": []}
    nome_df = carregar_fornecedores()
    nome_col = next((c for c in ("a2_nome", "a2_nom", "nome", "razao_social") if c in nome_df), None)
    nome = None
    if nome_col and "clifor" in nome_df:
        encontrados = nome_df[_norm_key(nome_df["clifor"]).eq(_norm_key(pd.Series([clifor])).iloc[0])]
        if not encontrados.empty:
            nome = str(encontrados.iloc[0][nome_col])
    itens_por_nf = _lookup_itens_d1(data_fim, clifor)
    pagamentos = []
    for row in base.sort_values("e5_data").to_dict("records"):
        chave_nf = (
            str(row.get("clifor", "")).strip().upper(),
            str(row.get("_join_e2_num", row.get("f1_doc", ""))).strip().upper(),
            str(row.get("_join_e2_prefixo", row.get("f1_serie", ""))).strip().upper(),
            str(row.get("_join_e2_filial", row.get("f1_filial", ""))).strip().upper(),
            str(row.get("_join_e2_loja", row.get("f1_loja", ""))).strip().upper(),
        )
        itens = itens_por_nf.get(chave_nf, [])
        pagamentos.append({
            "clifor": str(row.get("clifor", "")),
            "fornecedor": nome or "",
            "titulo": str(row.get("e2_num", "")),
            "parcela": str(row.get("e2_parcela", "")).strip(),
            "nf": str(row.get("f1_doc", row.get("_join_e2_num", ""))),
            "serie": str(row.get("f1_serie", row.get("_join_e2_prefixo", ""))),
            "tipo_d1": str(row.get("_d1_tipos", "") or SEM_VALOR),
            "itens_d1": itens,
            "emissao": _date_text(row.get("e2_emissao")),
            "vencimento_real": _date_text(row.get("e2_vencrea")),
            "vencimento_condicao": _date_text(row.get("_vencimento_condicao")),
            "data_pagamento": _date_text(row.get("e5_data")),
            "valor_pago": _finite_float(row.get("e5_valor")),
            "valor_bruto_nf": _finite_float(row.get("f1_valbrut")),
            "pmp_pagamento": _finite_float(row.get("dias_pagamento")),
            "pmp_vencimento": _finite_float(row.get("dias_vencimento")),
            "pmp_cond_pag": _finite_float(row.get("dias_cond_pag")),
            "delta_atraso": _finite_float(
                _finite_float(row.get("dias_pagamento"))
                - _finite_float(row.get("dias_cond_pag"))
            ),
        })
    return {"clifor": clifor, "nome": nome, "total": len(pagamentos),
            "resumo": _resumo(base), "pagamentos": pagamentos}


def calcular_pmp(data_ini: date, data_fim: date, **filters) -> Dict[str, Any]:
    """Alias estável para consumidores que não precisam escolher a granularidade."""
    return {"global": calcular_pmp_global(data_ini, data_fim, **filters),
            "fornecedores": calcular_pmp_fornecedores(data_ini, data_fim, **filters)}


def obter_dados_brutos(data_ini: date, data_fim: date, motivos=None, tipos=None,
                       fornecedores=None, clifor=None) -> pd.DataFrame:
    """Detalhe auditável no grão E5, mantendo campos de título, NF e SD1."""
    return _base(data_ini, data_fim, motivos, tipos, fornecedores, clifor)
