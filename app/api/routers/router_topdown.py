import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from dateutil.relativedelta import relativedelta

from app.core.database import get_db
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_projection_window,
    resolver_ciclo_fonte,
    ciclo_para_date,
)

router = APIRouter(prefix="/api/v1/topdown", tags=["TopDown"])


def _maior_resto(total: int, pesos: list) -> list:
    """
    Distribui um total inteiro entre N posições conforme uma lista de pesos,
    garantindo que a SOMA das partes seja EXATAMENTE o total (Método do Maior
    Resto). Usado para que o rateio de caixas feche ao inteiro sem perder
    nem sobrar unidade. Se todos os pesos forem zero, distribui igualitário.
    """
    n = len(pesos)
    if n == 0:
        return []
    soma_pesos = sum(pesos)
    if soma_pesos <= 0:
        base = total // n
        resto = total - base * n
        partes = [base] * n
        for i in range(resto):
            partes[i] += 1
        return partes

    distribuido = [(p / soma_pesos) * total for p in pesos]
    piso = [int(x) for x in distribuido]  # floor (valores não-negativos)
    fracoes = [distribuido[i] - piso[i] for i in range(n)]
    sobra = total - sum(piso)

    ordem = sorted(range(n), key=lambda i: fracoes[i], reverse=True)
    for k in range(sobra):
        piso[ordem[k]] += 1
    return piso


@router.get("/dados")
def get_topdown_dados(db: Session = Depends(get_db)):
    ciclo_atual = get_current_cycle(db)
    ciclo_anterior = get_previous_cycle(db)

    meses_proj = get_projection_window(db, ciclo_atual)
    if len(meses_proj) < 3:
        raise HTTPException(status_code=500, detail="Janela de projeção incompleta.")
    m2, m3, m4 = meses_proj[0], meses_proj[1], meses_proj[2]

    # 🕰️ ÂNCORA = CICLO ATIVO (respeita a viagem no tempo; nunca date.today()).
    data_ciclo_ativo = ciclo_para_date(ciclo_atual)
    dt_inicio_grafico = (data_ciclo_ativo - relativedelta(months=24)).strftime("%Y-%m-01")
    dt_fim_grafico = m4

    status_etapa = db.execute(text("""
        SELECT status FROM controle_ciclos 
        WHERE ciclo_sop = :ciclo AND origem = 'TopDown'
    """), {"ciclo": ciclo_atual}).scalar() or "ABERTO"

    # =================================================================
    # GRADE + FATURAMENTO MICRO. O faturamento é SUM(vol × pmv_aplicado)
    # linha a linha (base de precificação global), NÃO vol_agregado × pmv_médio.
    # O vol_ia aqui é o do CICLO ATIVO (base de comparação do ajuste atual).
    # Inclui p.categoria para a árvore Categoria -> SKU do frontend.
    # =================================================================
    grid = db.execute(text("""
        SELECT 
            f.sku,
            p.descricao,
            COALESCE(p.categoria, 'SEM CATEGORIA')    AS categoria,
            f.mes_projetado::DATE AS mes,
            SUM(f.vol_topdown)                        AS vol_topdown,
            SUM(f.vol_topdown * f.pmv_aplicado)       AS fat_topdown,
            SUM(f.vol_ia)                             AS vol_ia,
            SUM(f.vol_ia * f.pmv_aplicado)            AS fat_ia,
            COALESCE(o.rec_orc, 0)                    AS rec_orcada
        FROM fato_ibp_granular f
        JOIN dim_produtos p ON f.sku = p.sku
        LEFT JOIN (
            SELECT sku, DATE_TRUNC('month', mes_projetado)::DATE AS mes, SUM(receita_orcamento) AS rec_orc
            FROM fato_orcamento
            GROUP BY sku, DATE_TRUNC('month', mes_projetado)::DATE
        ) o ON o.sku = f.sku AND o.mes = f.mes_projetado::DATE
        WHERE f.ciclo_sop = :ciclo
          AND f.mes_projetado IN (:m2, :m3, :m4)
        GROUP BY f.sku, p.descricao, p.categoria, f.mes_projetado, o.rec_orc
    """), {"ciclo": ciclo_atual, "m2": m2, "m3": m3, "m4": m4}).fetchall()

    # =================================================================
    # GRÁFICO HISTÓRICO (24m): realizado, IA OFICIAL (regra ciclo-fonte),
    # lag1 (ciclo imediatamente anterior) e curva topdown do ciclo ativo.
    # =================================================================
    meses_eixo = []
    cursor = datetime.strptime(dt_inicio_grafico, "%Y-%m-%d").date()
    limite = datetime.strptime(dt_fim_grafico, "%Y-%m-%d").date()
    while cursor <= limite:
        meses_eixo.append(cursor)
        cursor = cursor + relativedelta(months=1)

    # Para cada mês, o ciclo-fonte OFICIAL da IA (None se < 06/2026).
    mapa_ciclo_ia = {}
    for mes in meses_eixo:
        fonte = resolver_ciclo_fonte(mes, ciclo_atual)
        if fonte:
            mapa_ciclo_ia[mes.strftime("%Y-%m-%d")] = fonte

    # Realizado (fato_vendas) por sku×mês no eixo.
    hist = db.execute(text("""
        SELECT sku, DATE_TRUNC('month', data_pedido)::DATE AS mes, SUM(qt_pedido) AS vol_real
        FROM fato_vendas
        WHERE data_pedido >= CAST(:ini AS DATE) AND data_pedido <= CAST(:fim AS DATE)
        GROUP BY sku, DATE_TRUNC('month', data_pedido)::DATE
    """), {"ini": dt_inicio_grafico, "fim": dt_fim_grafico}).fetchall()
    real_por_sku_mes = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_real or 0) for r in hist}

    # lag1: ciclo imediatamente anterior (referência de movimento).
    lag1 = db.execute(text("""
        SELECT sku, mes_projetado::DATE AS mes, SUM(vol_final) AS vol_lag1
        FROM fato_ibp_granular
        WHERE ciclo_sop = :cant
        GROUP BY sku, mes_projetado
    """), {"cant": ciclo_anterior}).fetchall()
    lag1_por_sku_mes = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_lag1 or 0) for r in lag1}

    # topdown do ciclo ativo (curva de referência no gráfico).
    tdcurva = db.execute(text("""
        SELECT sku, mes_projetado::DATE AS mes, SUM(vol_topdown) AS vol_td
        FROM fato_ibp_granular
        WHERE ciclo_sop = :ciclo
        GROUP BY sku, mes_projetado
    """), {"ciclo": ciclo_atual}).fetchall()
    td_por_sku_mes = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_td or 0) for r in tdcurva}

    # IA OFICIAL histórica: agrupa meses por ciclo-fonte e busca vol_ia.
    ciclos_para_meses = {}
    for mes_str, ciclo_fonte in mapa_ciclo_ia.items():
        ciclos_para_meses.setdefault(ciclo_fonte, []).append(mes_str)

    ia_oficial = {}  # (sku, 'YYYY-MM-DD') -> vol_ia
    for ciclo_fonte, meses_list in ciclos_para_meses.items():
        rows = db.execute(text("""
            SELECT sku, mes_projetado::DATE AS mes, SUM(vol_ia) AS vol_ia
            FROM fato_ibp_granular
            WHERE ciclo_sop = :cf AND mes_projetado = ANY(CAST(:meses AS DATE[]))
            GROUP BY sku, mes_projetado
        """), {"cf": ciclo_fonte, "meses": meses_list}).fetchall()
        for r in rows:
            ia_oficial[(str(r.sku), r.mes.strftime("%Y-%m-%d"))] = float(r.vol_ia or 0)

    # Corte do realizado = mês do ciclo ativo (viagem no tempo respeitada).
    corte_realizado = data_ciclo_ativo

    # =================================================================
    # MONTAGEM POR SKU + TOTAIS + CONTADOR
    # =================================================================
    df = pd.DataFrame(grid, columns=["sku", "descricao", "categoria", "mes", "vol_topdown", "fat_topdown", "vol_ia", "fat_ia", "rec_orcada"])
    df['mes'] = pd.to_datetime(df['mes'])

    meses_alvo = [m2, m3, m4]
    tot_mes = {m: {"fat_topdown": 0.0, "fat_ia": 0.0, "rec_orcada": 0.0, "vol_topdown": 0.0, "vol_ia": 0.0} for m in meses_alvo}

    skus_ajustados = 0
    output = []

    # SKUs únicos com sua categoria e descrição.
    skus_meta = df[['sku', 'descricao', 'categoria']].drop_duplicates('sku')
    skus_unicos = sorted(skus_meta.values.tolist(), key=lambda x: str(x[1]))

    for sku, desc, categoria in skus_unicos:
        grp = df[df['sku'] == sku]
        meses_grid = []
        divergiu = False

        for mes_target in meses_alvo:
            row = grp[grp['mes'] == pd.to_datetime(mes_target)]
            if not row.empty:
                r = row.iloc[0]
                vol_td = int(r['vol_topdown'] or 0)
                fat_td = float(r['fat_topdown'] or 0)
                vol_ia = int(r['vol_ia'] or 0)
                fat_ia = float(r['fat_ia'] or 0)
                rec_orc = float(r['rec_orcada'] or 0)
            else:
                vol_td, fat_td, vol_ia, fat_ia, rec_orc = 0, 0.0, 0, 0.0, 0.0

            if vol_td != vol_ia:
                divergiu = True

            pmv_exib = (fat_td / vol_td) if vol_td > 0 else ((fat_ia / vol_ia) if vol_ia > 0 else 0.0)
            var_orc = ((fat_td - rec_orc) / rec_orc) if rec_orc > 0 else 0.0

            meses_grid.append({
                "mes_banco": str(mes_target),
                "mes_str": pd.to_datetime(mes_target).strftime('%b/%y').capitalize(),
                "vol_topdown": vol_td,
                "fat_topdown": round(fat_td, 2),
                "vol_ia": vol_ia,
                "fat_ia": round(fat_ia, 2),
                "rec_orcada": round(rec_orc, 2),
                "pmv": round(pmv_exib, 2),
                "variacao_orcamento": var_orc,
            })

            tot_mes[mes_target]["fat_topdown"] += fat_td
            tot_mes[mes_target]["fat_ia"] += fat_ia
            tot_mes[mes_target]["rec_orcada"] += rec_orc
            tot_mes[mes_target]["vol_topdown"] += vol_td
            tot_mes[mes_target]["vol_ia"] += vol_ia

        if divergiu:
            skus_ajustados += 1

        # Série do gráfico histórico deste SKU.
        labels, s_real, s_ia, s_lag1, s_td = [], [], [], [], []
        for mes in meses_eixo:
            mkey = mes.strftime("%Y-%m-%d")
            labels.append(mes.strftime('%b/%y').capitalize())
            s_real.append(real_por_sku_mes.get((str(sku), mkey), 0.0) if mes <= corte_realizado else None)
            iav = ia_oficial.get((str(sku), mkey))
            s_ia.append(iav if iav is not None else None)
            s_lag1.append(lag1_por_sku_mes.get((str(sku), mkey)))
            s_td.append(td_por_sku_mes.get((str(sku), mkey)))

        output.append({
            "sku": sku,
            "descricao": desc,
            "categoria": categoria,
            "meses": meses_grid,
            "grafico": {
                "labels": labels,
                "realizado": s_real,
                "ia": s_ia,
                "lag1": s_lag1,
                "topdown": s_td,
            }
        })

    # Totais por mês + consolidado.
    totais_por_mes = []
    cons = {"fat_topdown": 0.0, "fat_ia": 0.0, "rec_orcada": 0.0, "vol_topdown": 0.0, "vol_ia": 0.0}
    for mes_target in meses_alvo:
        t = tot_mes[mes_target]
        var = ((t["fat_topdown"] - t["rec_orcada"]) / t["rec_orcada"]) if t["rec_orcada"] > 0 else 0.0
        totais_por_mes.append({
            "mes_banco": str(mes_target),
            "mes_str": pd.to_datetime(mes_target).strftime('%b/%y').capitalize(),
            "fat_topdown": round(t["fat_topdown"], 2),
            "fat_ia": round(t["fat_ia"], 2),
            "rec_orcada": round(t["rec_orcada"], 2),
            "vol_topdown": int(t["vol_topdown"]),
            "vol_ia": int(t["vol_ia"]),
            "variacao_orcamento": var,
        })
        for k in cons:
            cons[k] += t[k]

    var_cons = ((cons["fat_topdown"] - cons["rec_orcada"]) / cons["rec_orcada"]) if cons["rec_orcada"] > 0 else 0.0
    consolidado = {
        "fat_topdown": round(cons["fat_topdown"], 2),
        "fat_ia": round(cons["fat_ia"], 2),
        "rec_orcada": round(cons["rec_orcada"], 2),
        "vol_topdown": int(cons["vol_topdown"]),
        "vol_ia": int(cons["vol_ia"]),
        "variacao_orcamento": var_cons,
    }

    total_skus = len(skus_unicos)
    contador = {
        "total_skus": total_skus,
        "skus_ajustados": skus_ajustados,
        "skus_intocados": total_skus - skus_ajustados,
    }

    return {
        "ciclo_ativo": ciclo_atual,
        "status_etapa": status_etapa,
        "dados": output,
        "totais": {"por_mes": totais_por_mes, "consolidado": consolidado},
        "contador": contador,
    }


@router.post("/salvar")
def topdown_salvar(payload: dict, db: Session = Depends(get_db)):
    ciclo_atual = get_current_cycle(db)
    alteracoes = payload.get("alteracoes", [])

    for alt in alteracoes:
        sku = alt.get("sku")
        mes_banco = alt.get("mes_banco")
        novo_vol = int(round(float(alt.get("novo_vol", 0))))

        # Peso vem da IA ORIGINAL do ciclo ativo (vol_ia é imutável).
        linhas = db.execute(text("""
            SELECT id, vol_ia
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo AND sku = :sku AND mes_projetado = :mes
            ORDER BY id
        """), {"ciclo": ciclo_atual, "sku": sku, "mes": mes_banco}).fetchall()

        if not linhas:
            continue

        pesos = [float(l.vol_ia or 0) for l in linhas]
        # Maior Resto: soma das partes == novo_vol EXATO (soma exata importa).
        partes = _maior_resto(novo_vol, pesos)

        for l, parte in zip(linhas, partes):
            db.execute(
                text("UPDATE fato_ibp_granular SET vol_topdown = :val WHERE id = :id_linha"),
                {"val": int(parte), "id_linha": l.id}
            )

    db.commit()
    return {"status": "sucesso", "mensagem": "Rascunho do Top-Down salvo com sucesso."}


@router.post("/congelar")
def topdown_congelar(db: Session = Depends(get_db)):
    ciclo_atual = get_current_cycle(db)

    # PROPAGAÇÃO COMO PARTIDA (não trava): entrega o volume às camadas abaixo
    # como ponto de partida. vol_ia NUNCA é tocado. Camadas independentes:
    # cada etapa seguinte pode divergir livremente a partir daqui.
    db.execute(text("""
        UPDATE fato_ibp_granular
        SET vol_supply = vol_topdown,
            vol_final = vol_topdown,
            vol_meta = vol_topdown,
            vol_bottomup = vol_topdown
        WHERE ciclo_sop = :ciclo
    """), {"ciclo": ciclo_atual})

    id_controle = db.execute(text("""
        SELECT id FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'TopDown'
    """), {"ciclo": ciclo_atual}).scalar()

    if id_controle:
        db.execute(text("UPDATE controle_ciclos SET status = 'CONGELADO' WHERE id = :id_ctrl"), {"id_ctrl": id_controle})
    else:
        db.execute(text("INSERT INTO controle_ciclos (ciclo_sop, origem, status) VALUES (:ciclo, 'TopDown', 'CONGELADO')"), {"ciclo": ciclo_atual})

    db.commit()
    return {"status": "sucesso", "mensagem": "Etapa Top-Down CONGELADA."}