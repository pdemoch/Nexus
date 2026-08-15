from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import datetime
import io
import pandas as pd
import numpy as np
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.core.state import AppState
from app.models.domain_models import ControleCiclo, DimProduto, DimCliente, FatoIbpGranular, AuditoriaAjuste
from app.etl.pipeline import executar_pipeline_nexus
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_truth_query,
    get_projection_window_dates,
    normalizar_ciclo,
)

router = APIRouter(prefix="/api/v1/admin", tags=["Administração e Pipeline"])


# SCHEMA PARA ALTERAÇÃO DE CICLO
class PayloadCiclo(BaseModel):
    novo_ciclo: str  # Formato "MM/YYYY"


@router.get("/pipeline/status")
async def obter_status_pipeline(usuario_logado: dict = Depends(get_current_user)):
    return {"is_running": AppState.pipeline_rodando, "logs": AppState.logs}


@router.post("/pipeline/start")
async def iniciar_pipeline(background_tasks: BackgroundTasks, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Administradores podem rodar o pipeline.")

    if AppState.pipeline_rodando:
        raise HTTPException(status_code=400, detail="O pipeline já está em execução.")

    # Lê o ciclo ativo (já normalizado pela fundação) para suprir o "ciclo_alvo".
    ciclo_atual = get_current_cycle(db)

    AppState.pipeline_rodando = True
    AppState.logs = []

    def _log(msg: str):
        AppState.logs.append(msg)
        print(msg)

    background_tasks.add_task(executar_pipeline_nexus, ciclo_atual, _log)

    return {"status": "success", "message": f"Pipeline de Engenharia de Dados iniciado para o ciclo {ciclo_atual}!"}


@router.post("/pipeline/recarga-total")
async def iniciar_recarga_total(background_tasks: BackgroundTasks, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    """
    RECARGA TOTAL DA FATO_VENDAS — uso único / operação destrutiva.

    Apaga TODA a fato_vendas (TRUNCATE) e reinsere o histórico completo
    desde jan/2023, sem nenhum filtro de canal. Após concluir, o pipeline
    volta automaticamente ao modo normal de 5 meses nas próximas execuções.

    Restrito a Administrador. Requer que o pipeline não esteja em execução.
    """
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Administradores podem rodar a recarga total.")

    if AppState.pipeline_rodando:
        raise HTTPException(status_code=400, detail="O pipeline já está em execução. Aguarde a conclusão.")

    ciclo_atual = get_current_cycle(db)

    AppState.pipeline_rodando = True
    AppState.logs = []

    def _log(msg: str):
        AppState.logs.append(msg)
        print(msg)

    background_tasks.add_task(executar_pipeline_nexus, ciclo_atual, _log, True)

    return {
        "status": "success",
        "message": (
            f"🔴 RECARGA TOTAL iniciada para o ciclo {ciclo_atual}. "
            "Toda a fato_vendas será apagada e recarregada desde jan/2023. "
            "Acompanhe os logs no painel."
        )
    }


# =====================================================================
# ROTAS DA MÁQUINA DO TEMPO
# =====================================================================

@router.get("/ciclo-ativo")
async def obter_ciclo_ativo(db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Retorna o ciclo que está atualmente a comandar o sistema (formato canônico)."""
    try:
        # get_current_cycle já lê configuracao_sistema e normaliza para MM/YYYY.
        return {"ciclo_ativo": get_current_cycle(db)}
    except Exception as e:
        return {"ciclo_ativo": datetime.date.today().strftime("%m/%Y"), "error": str(e)}


@router.post("/ciclo-ativo")
async def atualizar_ciclo_ativo(payload: PayloadCiclo, db: Session = Depends(get_db), usuario: dict = Depends(get_current_user)):
    """Altera o relógio global do sistema S&OP."""
    if usuario.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Apenas administradores podem viajar no tempo.")

    # normalizar_ciclo valida faixa e formato; levanta HTTP 400 se inválido.
    # Isto fecha o vetor do input de texto livre do AdminPanel: o banco só
    # recebe ciclo em formato canônico MM/YYYY, nunca '7/2026' ou lixo.
    ciclo_canonico = normalizar_ciclo(payload.novo_ciclo)

    try:
        db.execute(
            text("INSERT INTO configuracao_sistema (ciclo_ativo_global) VALUES (:ciclo)"),
            {"ciclo": ciclo_canonico}
        )
        db.commit()
        return {"status": "success", "message": f"Sistema Nexus agora focado no ciclo {ciclo_canonico}"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================================
# GESTÃO DE CADEADOS E EXPORTAÇÃO
# =====================================================================

@router.post("/reabrir-ciclo")
async def reabrir_ciclo(origem: str, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")

    ciclo_atual = get_current_cycle(db)

    # Vocabulário canônico de etapas (fonte única do passe de bastão).
    ETAPAS_VALIDAS = {'TOPDOWN', 'BOTTOMUP', 'METAS', 'SUPPLY', 'FINAL'}
    origem_norm = origem.strip().upper()
    if origem_norm not in ETAPAS_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Origem inválida: '{origem}'. Use uma das etapas: TopDown, BottomUP, Metas, Supply, Final."
        )

    try:
        # Reabrir uma etapa = remover o cadeado (registro CONGELADO) dela.
        # Todas as etapas são tratadas igual: apaga o registro de trava da etapa.
        # (No Consenso/Metas, travas por vendedor vivem em estrutura separada e
        #  serão reabertas por endpoint próprio quando essa etapa for construída.)
        cadeados = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo_atual,
            func.upper(func.trim(ControleCiclo.origem)) == origem_norm
        ).all()

        if cadeados:
            for c in cadeados:
                db.delete(c)
            db.commit()
            return {"status": "success", "message": f"Etapa '{origem}' reaberta com sucesso ({len(cadeados)} cadeado(s) removido(s))."}

        return {"status": "success", "message": f"A etapa '{origem}' já se encontra aberta."}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exportar-base")
async def exportar_base_granular(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")

    try:
        engine = db.get_bind()
        ciclo = get_current_cycle(db)
        # CONSERTO: a fundação nova devolve LISTA de 3 em get_projection_window.
        # Para o par (M2, M4) usa-se get_projection_window_dates, que devolve tupla.
        m2, m4 = get_projection_window_dates(db)

        # Lê f.pmv_aplicado direto (base de precificação global cravada no pipeline).
        sql = """
            SELECT 
                f.ciclo_sop AS "Ciclo S&OP",
                c.cgc AS "CGC",
                c.cod_cliente AS "Cliente",
                c.loja AS "Loja",
                c.razaosocial AS "Razão Social",
                c.regional AS "Regional",
                c.gerente_nome AS "Gerente",
                c.supervisor_nome AS "Coordenador",
                f.vendedor_nome AS "Vendedor",
                p.categoria AS "Categoria",
                p.segmento AS "Segmento",
                f.sku AS "SKU",
                p.descricao AS "Produto",
                TO_CHAR(f.mes_projetado, 'YYYY-MM-DD') AS "Mês Projetado",
                ROUND(COALESCE(f.pmv_aplicado, 0)::numeric, 2) AS "PMV Unitário (R$)",
                COALESCE(f.vol_final, 0) AS "Final S&OP (CX)",
                ROUND((COALESCE(f.vol_final, 0) * COALESCE(f.pmv_aplicado, 0))::numeric, 2) AS "Receita S&OP (R$)"
            FROM fato_ibp_granular f
            JOIN dim_clientes c ON f.cgc = c.cgc
            JOIN dim_produtos p ON f.sku = p.sku
            WHERE f.ciclo_sop = :ciclo
              AND f.mes_projetado >= :m2
              AND f.mes_projetado <= :m4
              AND UPPER(TRIM(COALESCE(c.bloqueado, 'ATIVO'))) != 'INATIVO'
            ORDER BY c.gerente_nome, c.supervisor_nome, f.vendedor_nome, c.razaosocial, f.sku
        """

        df = pd.read_sql(text(sql), engine, params={"ciclo": ciclo, "m2": m2, "m4": m4})

        if df.empty:
            raise HTTPException(404, detail="Sem dados no ciclo selecionado.")

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Base_SOP_Granular')

        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=Nexus_Base_Granular_{ciclo.replace('/','_')}.xlsx"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, repr(e))



# =====================================================================
# EXPORTAÇÃO PARA MODELAGEM PREDITIVA (Data Science)
# =====================================================================
@router.get("/exportar-dataset-ia")
async def exportar_dataset_ia(db: Session = Depends(get_db),
                              usuario_logado: dict = Depends(get_current_user)):
    """
    Gera o dataset de treino/diagnóstico para reformular o motor de previsão.

    Estrutura pensada para modelagem de série temporal por SKU:

    Aba 'serie_mensal'  — o painel principal. Uma linha por SKU × mês, com
      todo o histórico disponível. É a base para ETS/ARIMA/LightGBM: já traz
      volume, receita, preço médio, nº de clientes ativos e flags de calendário.

    Aba 'forecast_vs_real' — o gabarito de acurácia. Para cada SKU × mês
      fechado, o que a IA previu (vol_ia), o que o humano prometeu (vol_final)
      e o que de fato vendeu, com o ciclo de origem (M-2). É onde se mede
      WMAPE/BIAS por horizonte e se descobre em que SKUs a IA erra mais.

    Aba 'cadastro_sku'  — dimensão: categoria, segmento, data da 1ª venda,
      meses ativos, curva ABC. Serve para clusterizar SKUs semelhantes.

    Aba 'dicionario'    — o que é cada coluna, para não haver ambiguidade.

    Vocabulário: vl_pedido = valor pedido (R$), qt_pedido = volume pedido (cx).
    """
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")

    try:
        engine = db.get_bind()

        # ── 1. SÉRIE MENSAL POR SKU ──────────────────────────────────
        sql_serie = """
            WITH base AS (
                SELECT v.sku,
                       DATE_TRUNC('month', v.data_pedido)::date AS mes,
                       SUM(v.qt_pedido)                         AS qt_pedido,
                       SUM(v.vl_pedido)                         AS vl_pedido,
                       COUNT(DISTINCT v.cgc)                    AS clientes_ativos,
                       COUNT(DISTINCT DATE(v.data_pedido))      AS dias_com_pedido
                FROM fato_vendas v
                WHERE v.sku IS NOT NULL AND v.sku <> ''
                GROUP BY v.sku, DATE_TRUNC('month', v.data_pedido)
            )
            SELECT b.sku,
                   COALESCE(p.descricao,'')  AS descricao,
                   COALESCE(p.categoria,'')  AS categoria,
                   COALESCE(p.segmento,'')   AS segmento,
                   TO_CHAR(b.mes,'YYYY-MM')  AS mes,
                   EXTRACT(YEAR  FROM b.mes)::int AS ano,
                   EXTRACT(MONTH FROM b.mes)::int AS mes_num,
                   EXTRACT(QUARTER FROM b.mes)::int AS trimestre,
                   b.qt_pedido,
                   ROUND(b.vl_pedido::numeric, 2) AS vl_pedido,
                   ROUND((b.vl_pedido / NULLIF(b.qt_pedido,0))::numeric, 4) AS pmv,
                   b.clientes_ativos,
                   b.dias_com_pedido
            FROM base b
            LEFT JOIN dim_produtos p ON p.sku = b.sku
            ORDER BY b.sku, b.mes
        """
        avisos = []
        try:
            df_serie = pd.read_sql(text(sql_serie), engine)
        except Exception as e:
            df_serie = pd.DataFrame()
            avisos.append(f"serie_mensal falhou: {e!r}")

        # Features derivadas — calculadas aqui para o arquivo já sair pronto
        try:
          if not df_serie.empty:
            df_serie = df_serie.sort_values(["sku", "mes"])
            g = df_serie.groupby("sku")["qt_pedido"]
            df_serie["qt_lag_1"]   = g.shift(1)
            df_serie["qt_lag_12"]  = g.shift(12)
            df_serie["qt_mm_3"]    = g.transform(lambda x: x.shift(1).rolling(3).mean().round(2))
            df_serie["qt_mm_12"]   = g.transform(lambda x: x.shift(1).rolling(12).mean().round(2))
            df_serie["var_vs_mes_ant"] = (
                (df_serie["qt_pedido"] - df_serie["qt_lag_1"]) / df_serie["qt_lag_1"].replace(0, np.nan)
            ).round(4)
            df_serie["var_vs_ano_ant"] = (
                (df_serie["qt_pedido"] - df_serie["qt_lag_12"]) / df_serie["qt_lag_12"].replace(0, np.nan)
            ).round(4)
        except Exception as e:
            avisos.append(f"features da serie_mensal falharam: {e!r}")

        # ── 2. FORECAST vs REAL (o gabarito de acurácia) ─────────────
        sql_acc = """
            SELECT f.sku,
                   COALESCE(p.categoria,'') AS categoria,
                   f.ciclo_sop                                  AS ciclo_origem,
                   TO_CHAR(f.mes_projetado,'YYYY-MM')           AS mes_projetado,
                   SUM(f.vol_ia)                                AS previsto_ia,
                   SUM(f.vol_topdown)                           AS plano_topdown,
                   SUM(f.vol_bottomup)                          AS plano_bottomup,
                   SUM(f.vol_meta)                              AS plano_meta,
                   SUM(f.vol_supply)                            AS plano_supply,
                   SUM(f.vol_final)                             AS plano_final,
                   COALESCE(r.realizado, 0)                     AS realizado
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON p.sku = f.sku
            LEFT JOIN (
                SELECT sku, DATE_TRUNC('month', data_pedido)::date AS mes,
                       SUM(qt_pedido) AS realizado
                FROM fato_vendas GROUP BY sku, DATE_TRUNC('month', data_pedido)
            ) r ON r.sku = f.sku AND r.mes = f.mes_projetado
            GROUP BY f.sku, p.categoria, f.ciclo_sop, f.mes_projetado, r.realizado
            ORDER BY f.sku, f.ciclo_sop, f.mes_projetado
        """
        try:
            df_acc = pd.read_sql(text(sql_acc), engine)
        except Exception as e:
            df_acc = pd.DataFrame()
            avisos.append(f"forecast_vs_real falhou: {e!r}")

        try:
          if not df_acc.empty:
            # horizonte = distância em meses entre o ciclo de origem e o mês previsto
            def _horizonte(row):
                try:
                    mm, yy = str(row["ciclo_origem"]).split("/")
                    c = datetime.date(int(yy), int(mm), 1)
                    p = datetime.datetime.strptime(row["mes_projetado"] + "-01", "%Y-%m-%d").date()
                    return (p.year - c.year) * 12 + (p.month - c.month)
                except Exception:
                    return None
            df_acc["horizonte_meses"] = df_acc.apply(_horizonte, axis=1)
            for col, nome in [("previsto_ia", "erro_ia"), ("plano_final", "erro_humano")]:
                df_acc[nome] = df_acc[col] - df_acc["realizado"]
                df_acc[nome + "_abs_pct"] = (
                    df_acc[nome].abs() / df_acc["realizado"].replace(0, np.nan)
                ).round(4)
        except Exception as e:
            avisos.append(f"metricas de erro falharam: {e!r}")

        # ── 3. CADASTRO / PERFIL DO SKU ──────────────────────────────
        sql_cad = """
            WITH v AS (
                SELECT sku,
                       MIN(data_pedido)                    AS primeira_venda,
                       MAX(data_pedido)                    AS ultima_venda,
                       COUNT(DISTINCT DATE_TRUNC('month', data_pedido)) AS meses_com_venda,
                       SUM(qt_pedido)                      AS qt_total,
                       SUM(vl_pedido)                      AS vl_total
                FROM fato_vendas WHERE sku IS NOT NULL AND sku <> ''
                GROUP BY sku
            )
            SELECT v.sku,
                   COALESCE(p.descricao,'') AS descricao,
                   COALESCE(p.categoria,'') AS categoria,
                   COALESCE(p.segmento,'')  AS segmento,
                   TO_CHAR(v.primeira_venda,'YYYY-MM-DD') AS primeira_venda,
                   TO_CHAR(v.ultima_venda,'YYYY-MM-DD')   AS ultima_venda,
                   v.meses_com_venda,
                   v.qt_total,
                   ROUND(v.vl_total::numeric, 2) AS vl_total,
                   ROUND((v.vl_total / NULLIF(v.qt_total,0))::numeric, 4) AS pmv_medio
            FROM v LEFT JOIN dim_produtos p ON p.sku = v.sku
            ORDER BY v.vl_total DESC
        """
        try:
            df_cad = pd.read_sql(text(sql_cad), engine)
        except Exception as e:
            df_cad = pd.DataFrame()
            avisos.append(f"cadastro_sku falhou: {e!r}")

        try:
          if not df_cad.empty:
            # Curva ABC por valor pedido acumulado
            df_cad = df_cad.sort_values("vl_total", ascending=False)
            total = df_cad["vl_total"].sum()
            if total > 0:
                acum = df_cad["vl_total"].cumsum() / total
                df_cad["share_acumulado"] = acum.round(4)
                df_cad["curva_abc"] = pd.cut(
                    acum, bins=[-0.01, 0.8, 0.95, 1.01], labels=["A", "B", "C"]
                ).astype(str)
            # Intermitência: quantos meses do intervalo de vida ficaram sem venda
            # Meses de vida: usa to_datetime (tolera nulo/formato invalido)
            _pv = pd.to_datetime(df_cad["primeira_venda"], errors="coerce")
            _uv = pd.to_datetime(df_cad["ultima_venda"],   errors="coerce")
            df_cad["meses_de_vida"] = (
                (_uv.dt.year * 12 + _uv.dt.month) - (_pv.dt.year * 12 + _pv.dt.month) + 1
            ).fillna(1).clip(lower=1).astype(int)
            df_cad["taxa_intermitencia"] = (
                1 - df_cad["meses_com_venda"] / df_cad["meses_de_vida"].replace(0, np.nan)
            ).round(4)
        except Exception as e:
            avisos.append(f"perfil do cadastro_sku falhou: {e!r}")

        # ── 4. DICIONÁRIO DE DADOS ───────────────────────────────────
        dicionario = pd.DataFrame([
            ["serie_mensal", "qt_pedido", "Volume pedido no mes, em caixas (soma de qt_pedido)"],
            ["serie_mensal", "vl_pedido", "Valor pedido no mes, em R$ (soma de vl_pedido)"],
            ["serie_mensal", "pmv", "Preco medio de venda ponderado = vl_pedido / qt_pedido"],
            ["serie_mensal", "clientes_ativos", "CNPJs distintos que compraram o SKU no mes"],
            ["serie_mensal", "dias_com_pedido", "Dias distintos com pedido — proxy de regularidade"],
            ["serie_mensal", "qt_lag_1 / qt_lag_12", "Volume do mes anterior / do mesmo mes do ano anterior"],
            ["serie_mensal", "qt_mm_3 / qt_mm_12", "Media movel de 3 e 12 meses (defasada, sem vazamento)"],
            ["serie_mensal", "var_vs_mes_ant", "Variacao percentual contra o mes anterior"],
            ["serie_mensal", "var_vs_ano_ant", "Variacao percentual contra o mesmo mes do ano anterior"],
            ["forecast_vs_real", "ciclo_origem", "Ciclo S&OP em que a previsao foi feita (MM/YYYY)"],
            ["forecast_vs_real", "horizonte_meses", "Distancia em meses entre o ciclo e o mes previsto (M+2, M+3, M+4)"],
            ["forecast_vs_real", "previsto_ia", "vol_ia — o que o modelo previu"],
            ["forecast_vs_real", "plano_final", "vol_final — o numero que o humano fechou"],
            ["forecast_vs_real", "realizado", "Volume realmente vendido naquele mes"],
            ["forecast_vs_real", "erro_ia / erro_humano", "Previsto menos realizado (positivo = superestimou)"],
            ["forecast_vs_real", "erro_*_abs_pct", "Erro absoluto percentual sobre o realizado"],
            ["cadastro_sku", "meses_com_venda", "Quantos meses distintos tiveram venda"],
            ["cadastro_sku", "meses_de_vida", "Meses entre a primeira e a ultima venda"],
            ["cadastro_sku", "taxa_intermitencia", "1 - (meses_com_venda / meses_de_vida). Alto = demanda esparsa (usar Croston/TSB)"],
            ["cadastro_sku", "curva_abc", "A = ate 80% do valor acumulado, B = ate 95%, C = cauda"],
        ], columns=["aba", "coluna", "significado"])

        # ── Monta o Excel ────────────────────────────────────────────
        # Se as tres abas vieram vazias, o problema e de dados — avisa claro.
        if df_serie.empty and df_acc.empty and df_cad.empty:
            raise HTTPException(
                404,
                "Nenhum dado encontrado. Verifique se fato_vendas e fato_ibp_granular "
                "tem registros. Detalhes: " + ("; ".join(avisos) if avisos else "sem erro tecnico")
            )

        diagnostico = pd.DataFrame({
            "item": ["linhas serie_mensal", "linhas forecast_vs_real",
                     "linhas cadastro_sku", "gerado_em", "avisos"],
            "valor": [len(df_serie), len(df_acc), len(df_cad),
                      datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                      "; ".join(avisos) if avisos else "nenhum"],
        })

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            (df_serie if not df_serie.empty else pd.DataFrame({"aviso": ["sem dados"]})
             ).to_excel(writer, index=False, sheet_name='serie_mensal')
            (df_acc if not df_acc.empty else pd.DataFrame({"aviso": ["sem dados"]})
             ).to_excel(writer, index=False, sheet_name='forecast_vs_real')
            (df_cad if not df_cad.empty else pd.DataFrame({"aviso": ["sem dados"]})
             ).to_excel(writer, index=False, sheet_name='cadastro_sku')
            dicionario.to_excel(writer, index=False, sheet_name='dicionario')
            diagnostico.to_excel(writer, index=False, sheet_name='diagnostico')

        buffer.seek(0)
        hoje = datetime.date.today().strftime("%Y%m%d")
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=Nexus_Dataset_IA_{hoje}.xlsx"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, repr(e))


@router.get("/auditoria/logs")
async def listar_logs_auditoria(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado.")

    try:
        logs = db.query(AuditoriaAjuste).order_by(AuditoriaAjuste.data_ajuste.desc()).limit(500).all()

        dados = []
        for l in logs:
            dados.append({
                "id": l.id,
                "data": l.data_ajuste.strftime("%d/%m/%Y %H:%M"),
                "usuario": l.usuario_nome or "Sistema",
                "origem": l.origem_ajuste,
                "cliente": l.razaosocial_afetada,
                "sku": l.sku,
                "mes_ref": l.mes_projetado.strftime("%m/%Y"),
                "de": int(l.valor_antigo or 0),
                "para": int(l.vol_novo)
            })
        return {"status": "success", "dados": dados}
    except Exception as e:
        raise HTTPException(500, f"Erro ao carregar auditoria: {str(e)}")


# =====================================================================
# GESTÃO DE USUÁRIOS E SEGURANÇA (SQL PURO)
# =====================================================================

@router.delete("/delete-user/{user_id}")
async def excluir_usuario_sistema(user_id: int, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado.get('funcao') != 'Administrador':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores podem excluir usuários.")

    if user_id == usuario_logado.get('id'):
        raise HTTPException(
            status_code=400,
            detail="Operação negada: Você não pode excluir a sua própria conta de administrador."
        )

    try:
        db.execute(text("DELETE FROM usuarios WHERE id = :id"), {"id": user_id})
        db.commit()
        return {"status": "success", "message": "Utilizador removido com sucesso do Nexus."}
    except Exception as e:
        db.rollback()
        try:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            db.commit()
            return {"status": "success", "message": "Utilizador removido com sucesso do Nexus."}
        except Exception as e2:
            db.rollback()
            raise HTTPException(status_code=500, detail="Erro interno ao excluir utilizador. Contacte o suporte.")