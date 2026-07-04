import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from dateutil.relativedelta import relativedelta

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_projection_window,
    resolver_ciclo_fonte,
    ciclo_para_date,
)

router = APIRouter(prefix="/api/v1/consensus/gerenciamento", tags=["Gerenciamento BottomUp"])


def require_manager_or_admin(usuario: dict = Depends(get_current_user)):
    if usuario.get('funcao') not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gerência Comercial.")
    return usuario


def _maior_resto(total: int, pesos: list) -> list:
    """
    Distribui um total inteiro entre N posições conforme pesos, garantindo que
    a SOMA das partes seja EXATAMENTE o total (Método do Maior Resto). Pesos
    zerados -> distribuição igualitária.
    """
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


def _verifica_trava_topdown(db: Session, ciclo: str):
    """Gerenciamento só abre se a etapa TopDown estiver CONGELADA."""
    td = db.execute(text("""
        SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'TopDown'
    """), {"c": ciclo}).scalar()
    if td != 'CONGELADO':
        raise HTTPException(
            status_code=403,
            detail="Etapa bloqueada: o Top-Down (Marketing) ainda não foi congelado."
        )


@router.get("/dados")
def get_gerenciamento_dados(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    ciclo_atual = get_current_cycle(db)
    ciclo_anterior = get_previous_cycle(db)

    meses_proj = get_projection_window(db, ciclo_atual)
    if len(meses_proj) < 3:
        raise HTTPException(status_code=500, detail="Janela de projeção incompleta.")
    m2, m3, m4 = meses_proj[0], meses_proj[1], meses_proj[2]

    data_ciclo_ativo = ciclo_para_date(ciclo_atual)
    dt_inicio_grafico = (data_ciclo_ativo - relativedelta(months=24)).strftime("%Y-%m-01")
    dt_fim_grafico = m4

    status_etapa = db.execute(text("""
        SELECT status FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'BottomUP'
    """), {"ciclo": ciclo_atual}).scalar() or "ABERTO"

    # Está a etapa anterior liberada? (informativo p/ o front habilitar edição)
    td_status = db.execute(text("""
        SELECT status FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'TopDown'
    """), {"ciclo": ciclo_atual}).scalar()
    topdown_liberado = (td_status == 'CONGELADO')

    # ================= GRADE + FATURAMENTO MICRO =================
    # Referências desta fase: IA e TOPDOWN (ambos do ciclo ativo). Editável: BU.
    grid = db.execute(text("""
        SELECT
            f.sku,
            p.descricao,
            COALESCE(p.categoria, 'SEM CATEGORIA')     AS categoria,
            f.mes_projetado::DATE AS mes,
            SUM(f.vol_bottomup)                         AS vol_bu,
            SUM(f.vol_bottomup * f.pmv_aplicado)        AS fat_bu,
            SUM(f.vol_topdown)                          AS vol_td,
            SUM(f.vol_topdown * f.pmv_aplicado)         AS fat_td,
            SUM(f.vol_ia)                               AS vol_ia,
            SUM(f.vol_ia * f.pmv_aplicado)              AS fat_ia,
            COALESCE(o.rec_orc, 0)                      AS rec_orcada
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

    # ================= DOSSIÊ HISTÓRICO (24m) =================
    meses_eixo = []
    cursor = datetime.strptime(dt_inicio_grafico, "%Y-%m-%d").date()
    limite = datetime.strptime(dt_fim_grafico, "%Y-%m-%d").date()
    while cursor <= limite:
        meses_eixo.append(cursor)
        cursor = cursor + relativedelta(months=1)

    mapa_ciclo_ia = {}
    for mes in meses_eixo:
        fonte = resolver_ciclo_fonte(mes, ciclo_atual)
        if fonte:
            mapa_ciclo_ia[mes.strftime("%Y-%m-%d")] = fonte

    hist = db.execute(text("""
        SELECT sku, DATE_TRUNC('month', data_pedido)::DATE AS mes, SUM(qt_pedido) AS vol_real
        FROM fato_vendas
        WHERE data_pedido >= CAST(:ini AS DATE) AND data_pedido <= CAST(:fim AS DATE)
        GROUP BY sku, DATE_TRUNC('month', data_pedido)::DATE
    """), {"ini": dt_inicio_grafico, "fim": dt_fim_grafico}).fetchall()
    real_por = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_real or 0) for r in hist}

    lag1 = db.execute(text("""
        SELECT sku, mes_projetado::DATE AS mes, SUM(vol_final) AS vol_lag1
        FROM fato_ibp_granular WHERE ciclo_sop = :cant
        GROUP BY sku, mes_projetado
    """), {"cant": ciclo_anterior}).fetchall()
    lag1_por = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_lag1 or 0) for r in lag1}

    # Curvas do ciclo ativo p/ o gráfico: topdown e bottomup.
    tdcurva = db.execute(text("""
        SELECT sku, mes_projetado::DATE AS mes,
               SUM(vol_topdown) AS vol_td, SUM(vol_bottomup) AS vol_bu
        FROM fato_ibp_granular WHERE ciclo_sop = :ciclo
        GROUP BY sku, mes_projetado
    """), {"ciclo": ciclo_atual}).fetchall()
    td_por = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_td or 0) for r in tdcurva}
    bu_por = {(str(r.sku), r.mes.strftime("%Y-%m-%d")): float(r.vol_bu or 0) for r in tdcurva}

    ciclos_para_meses = {}
    for mes_str, cf in mapa_ciclo_ia.items():
        ciclos_para_meses.setdefault(cf, []).append(mes_str)
    ia_oficial = {}
    for cf, meses_list in ciclos_para_meses.items():
        rows = db.execute(text("""
            SELECT sku, mes_projetado::DATE AS mes, SUM(vol_ia) AS vol_ia
            FROM fato_ibp_granular
            WHERE ciclo_sop = :cf AND mes_projetado = ANY(:meses)
            GROUP BY sku, mes_projetado
        """), {"cf": cf, "meses": meses_list}).fetchall()
        for r in rows:
            ia_oficial[(str(r.sku), r.mes.strftime("%Y-%m-%d"))] = float(r.vol_ia or 0)

    corte_realizado = data_ciclo_ativo

    # ================= MONTAGEM =================
    df = pd.DataFrame(grid, columns=["sku", "descricao", "categoria", "mes",
                                     "vol_bu", "fat_bu", "vol_td", "fat_td", "vol_ia", "fat_ia", "rec_orcada"])
    df['mes'] = pd.to_datetime(df['mes'])

    meses_alvo = [m2, m3, m4]
    tot_mes = {m: {"fat_bu": 0.0, "fat_td": 0.0, "fat_ia": 0.0, "rec_orcada": 0.0,
                   "vol_bu": 0.0, "vol_td": 0.0, "vol_ia": 0.0} for m in meses_alvo}
    skus_ajustados = 0
    output = []

    skus_meta = df[['sku', 'descricao', 'categoria']].drop_duplicates('sku')
    skus_unicos = sorted(skus_meta.values.tolist(), key=lambda x: str(x[1]))

    for sku, desc, categoria in skus_unicos:
        grp = df[df['sku'] == sku]
        meses_grid = []
        divergiu = False

        for mt in meses_alvo:
            row = grp[grp['mes'] == pd.to_datetime(mt)]
            if not row.empty:
                r = row.iloc[0]
                vol_bu, fat_bu = int(r['vol_bu'] or 0), float(r['fat_bu'] or 0)
                vol_td, fat_td = int(r['vol_td'] or 0), float(r['fat_td'] or 0)
                vol_ia, fat_ia = int(r['vol_ia'] or 0), float(r['fat_ia'] or 0)
                rec_orc = float(r['rec_orcada'] or 0)
            else:
                vol_bu = fat_bu = vol_td = fat_td = vol_ia = fat_ia = 0
                rec_orc = 0.0

            # "Ajustado" = comercial divergiu do que o Marketing entregou (TopDown).
            if vol_bu != vol_td:
                divergiu = True

            pmv_exib = (fat_bu / vol_bu) if vol_bu > 0 else ((fat_td / vol_td) if vol_td > 0 else 0.0)
            var_orc = ((fat_bu - rec_orc) / rec_orc) if rec_orc > 0 else 0.0
            # Divergência proeminente vs TopDown (informativa, não é a âncora).
            div_td = ((fat_bu - fat_td) / fat_td) if fat_td > 0 else 0.0

            meses_grid.append({
                "mes_banco": str(mt),
                "mes_str": pd.to_datetime(mt).strftime('%b/%y').capitalize(),
                "vol_bu": vol_bu, "fat_bu": round(fat_bu, 2),
                "vol_td": vol_td, "fat_td": round(fat_td, 2),
                "vol_ia": vol_ia, "fat_ia": round(fat_ia, 2),
                "rec_orcada": round(rec_orc, 2),
                "pmv": round(pmv_exib, 2),
                "variacao_orcamento": var_orc,
                "divergencia_topdown": div_td,
            })

            t = tot_mes[mt]
            t["fat_bu"] += fat_bu; t["fat_td"] += fat_td; t["fat_ia"] += fat_ia
            t["rec_orcada"] += rec_orc
            t["vol_bu"] += vol_bu; t["vol_td"] += vol_td; t["vol_ia"] += vol_ia

        if divergiu:
            skus_ajustados += 1

        labels, s_real, s_ia, s_lag1, s_td, s_bu = [], [], [], [], [], []
        for mes in meses_eixo:
            mkey = mes.strftime("%Y-%m-%d")
            labels.append(mes.strftime('%b/%y').capitalize())
            s_real.append(real_por.get((str(sku), mkey), 0.0) if mes <= corte_realizado else None)
            iav = ia_oficial.get((str(sku), mkey))
            s_ia.append(iav if iav is not None else None)
            s_lag1.append(lag1_por.get((str(sku), mkey)))
            s_td.append(td_por.get((str(sku), mkey)))
            s_bu.append(bu_por.get((str(sku), mkey)))

        output.append({
            "sku": sku, "descricao": desc, "categoria": categoria,
            "meses": meses_grid,
            "grafico": {
                "labels": labels, "realizado": s_real, "ia": s_ia,
                "lag1": s_lag1, "topdown": s_td, "bottomup": s_bu,
            }
        })

    totais_por_mes = []
    cons = {"fat_bu": 0.0, "fat_td": 0.0, "fat_ia": 0.0, "rec_orcada": 0.0,
            "vol_bu": 0.0, "vol_td": 0.0, "vol_ia": 0.0}
    for mt in meses_alvo:
        t = tot_mes[mt]
        var = ((t["fat_bu"] - t["rec_orcada"]) / t["rec_orcada"]) if t["rec_orcada"] > 0 else 0.0
        totais_por_mes.append({
            "mes_banco": str(mt),
            "mes_str": pd.to_datetime(mt).strftime('%b/%y').capitalize(),
            "fat_bu": round(t["fat_bu"], 2), "fat_td": round(t["fat_td"], 2),
            "fat_ia": round(t["fat_ia"], 2), "rec_orcada": round(t["rec_orcada"], 2),
            "vol_bu": int(t["vol_bu"]), "vol_td": int(t["vol_td"]), "vol_ia": int(t["vol_ia"]),
            "variacao_orcamento": var,
        })
        for k in cons:
            cons[k] += t[k]

    var_cons = ((cons["fat_bu"] - cons["rec_orcada"]) / cons["rec_orcada"]) if cons["rec_orcada"] > 0 else 0.0
    consolidado = {
        "fat_bu": round(cons["fat_bu"], 2), "fat_td": round(cons["fat_td"], 2),
        "fat_ia": round(cons["fat_ia"], 2), "rec_orcada": round(cons["rec_orcada"], 2),
        "vol_bu": int(cons["vol_bu"]), "vol_td": int(cons["vol_td"]), "vol_ia": int(cons["vol_ia"]),
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
        "topdown_liberado": topdown_liberado,
        "dados": output,
        "totais": {"por_mes": totais_por_mes, "consolidado": consolidado},
        "contador": contador,
    }


@router.post("/salvar")
def gerenciamento_salvar(payload: dict, db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    ciclo_atual = get_current_cycle(db)
    _verifica_trava_topdown(db, ciclo_atual)

    alteracoes = payload.get("alteracoes", [])
    for alt in alteracoes:
        sku = alt.get("sku")
        mes_banco = alt.get("mes_banco")
        novo_vol = int(round(float(alt.get("novo_vol", 0))))

        # Peso vem do vol_topdown (camada anterior = ponto de partida).
        linhas = db.execute(text("""
            SELECT id, vol_topdown
            FROM fato_ibp_granular
            WHERE ciclo_sop = :ciclo AND sku = :sku AND mes_projetado = :mes
            ORDER BY id
        """), {"ciclo": ciclo_atual, "sku": sku, "mes": mes_banco}).fetchall()
        if not linhas:
            continue

        pesos = [float(l.vol_topdown or 0) for l in linhas]
        partes = _maior_resto(novo_vol, pesos)
        for l, parte in zip(linhas, partes):
            db.execute(text("UPDATE fato_ibp_granular SET vol_bottomup = :v WHERE id = :id"),
                       {"v": int(parte), "id": l.id})

    db.commit()
    return {"status": "sucesso", "mensagem": "Rascunho Bottom-Up salvo com sucesso."}


@router.post("/congelar")
def gerenciamento_congelar(db: Session = Depends(get_db), usuario: dict = Depends(require_manager_or_admin)):
    ciclo_atual = get_current_cycle(db)
    _verifica_trava_topdown(db, ciclo_atual)

    # PROPAGAÇÃO COMO PARTIDA: BottomUP entrega para Metas/Supply/Final.
    # vol_ia e vol_topdown NÃO são tocados (camadas já congeladas acima).
    db.execute(text("""
        UPDATE fato_ibp_granular
        SET vol_meta = vol_bottomup,
            vol_supply = vol_bottomup,
            vol_final = vol_bottomup
        WHERE ciclo_sop = :ciclo
    """), {"ciclo": ciclo_atual})

    id_ctrl = db.execute(text("""
        SELECT id FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'BottomUP'
    """), {"ciclo": ciclo_atual}).scalar()
    if id_ctrl:
        db.execute(text("UPDATE controle_ciclos SET status = 'CONGELADO' WHERE id = :id"), {"id": id_ctrl})
    else:
        db.execute(text("INSERT INTO controle_ciclos (ciclo_sop, origem, status) VALUES (:ciclo, 'BottomUP', 'CONGELADO')"),
                   {"ciclo": ciclo_atual})

    db.commit()
    return {"status": "sucesso", "mensagem": "Etapa Bottom-Up (Gerenciamento) CONGELADA."}