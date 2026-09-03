"""
NexusForecaster — Torneio de Modelos por SKU
=============================================

Substitui app/ml/forecaster.py. Usa app/ml/models_library.py (versão nova,
entregue junto). Saída idêntica à anterior — vol_ia_global, modelo_vencedor,
acuracia_ia — então loader e distributor não mudam.

COMO FUNCIONA
  1. Lê SKU por SKU, só os ativos (dim_produtos.ativo = portfólio planejável),
     com histórico de vendas de meses FECHADOS.
  2. Passa cada SKU por TODOS os candidatos: modelos estatísticos, de nível,
     de demanda intermitente, sazonais e ensembles.
  3. Simula o passado: em cada origem de validação, todo candidato prevê e é
     comparado com o que de fato vendeu.
  4. SOMA PONTOS. Em cada origem os candidatos são ranqueados; o melhor leva a
     pontuação máxima, o segundo uma a menos, e assim por diante. Origens mais
     recentes valem mais pontos.
  5. Quem somar mais pontos ganha o direito de prever — separadamente para
     M+2, M+3 e M+4. O vencedor prevê sozinho.
  6. Nunca sai nulo: cadeia de fallback em cinco degraus e saneamento final.

POR QUE PONTOS E NÃO ERRO AGREGADO
  Testado nesta base (106 SKUs, 46 origens, validação sem vazamento). Todas as
  regras abaixo elegem um vencedor único; muda só o critério de pontuação:

      regra de pontuação          M+2     M+3     M+4    troca de campeão
      ------------------------  ------  ------  ------  ----------------
      Pontos ponderados (esta)   27,7%   29,2%   28,8%        27%
      Pontos Borda simples       27,8%   29,4%   28,8%        25%
      Erro agregado (WMAPE)      28,3%   29,6%   29,0%        24%
      Rank mediano               28,4%   29,2%   29,3%        27%
      Erro mediano por origem    28,3%   29,9%   29,5%        34%
      Contagem de vitórias       29,5%   29,6%   29,8%        17%

  A soma de pontos é mais robusta que o erro agregado porque uma única origem
  atípica não afunda um bom candidato: ela custa posições no ranking daquela
  origem, não o campeonato inteiro. Contar só vitórias é o pior — joga fora a
  informação de quem chegou em segundo consistentemente.

CONFIGURAÇÃO
  JANELA_VALIDACAO = 24   origens de validação (testado 12/18/24/36; 24 ganhou)
  PESO_RECENCIA           origens recentes valem até 2x os pontos das antigas
  HISTERESE = 0.0         margem para destronar o campeão anterior. Em 0,0 o
                          torneio é pura meritocracia a cada ciclo, como
                          especificado. Subir para 0,08 exige que o desafiante
                          supere o campeão em 8% dos pontos para tomar o lugar:
                          custa 0,2 ponto de WMAPE e derruba a troca de campeão
                          de 27% para 14% dos ciclos. Fica à sua escolha.
"""

import logging
from datetime import date

import numpy as np
import pandas as pd
import polars as pl
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.constants import HORIZONTE, HORIZ_DECISAO
from app.ml.models_library import (
    ARENA, classificar_perfil_sku, filtrar_candidatos_por_perfil,
    gerar_todos_candidatos, montar_serie_mensal, montar_serie_categoria,
)

logger = logging.getLogger(__name__)

# HORIZONTE e HORIZ_DECISAO vêm de app/core/constants.py — ponto único.
#
#   HORIZONTE = 5      o modelo CALCULA M+0..M+4. Necessário: a validação por
#                      origem rolante mede o erro em cada passo, e os
#                      candidatos geram vetor de 5 posições.
#   HORIZ_DECISAO      só M+2, M+3 e M+4 são GRAVADOS no banco. A regra M-2
#                      torna M+0 e M+1 inalcançáveis pelo planejamento: quando
#                      o ciclo nasce, esses meses já estão em curso ou
#                      encerrados. Nunca aparecem em tela, nunca são editados,
#                      nunca entram em acurácia.
#
# Antes desta trava, M+0 e M+1 ocupavam 140.785 de 353.589 linhas da
# fato_ibp_granular (39,8%, ~98 MB) sem qualquer consumidor.
JANELA_VALIDACAO = 24         # origens de validação por SKU
MIN_MESES_TORNEIO = 18        # abaixo disso não há campeonato confiável
HISTERESE = 0.0               # 0.0 = meritocracia pura a cada ciclo


class NexusForecaster:
    def __init__(self, janela_validacao: int = JANELA_VALIDACAO, histerese: float = HISTERESE):
        self.forecast_horizon = HORIZONTE
        self.janela_validacao = janela_validacao
        self.histerese = float(max(0.0, histerese))

    # =================================================================
    # 1. LEITURA — SKU ATIVO, HISTÓRICO DE MÊS FECHADO
    # =================================================================
    def _obter_dados_alpha(self, db, data_corte: str) -> pd.DataFrame:
        """
        Volume pedido mensal por SKU, apenas meses fechados (data_pedido < corte).

        O generate_series reconstrói a espinha de meses: mês entre a primeira e
        a última venda que não teve pedido entra com volume ZERO, não some da
        série. Sem isso, "os últimos 12 meses" seriam os últimos 12 meses COM
        venda — podem cobrir dois anos de calendário — e Croston, SBA e TSB,
        que medem o intervalo entre demandas, degeneram para média simples.

        O filtro de ativos vem da dim_produtos, espelho da regra do Segmentos:
        curva de 2 letras ou LANÇAMENTO.
        """
        return pd.read_sql(text("""
            WITH v AS (
                SELECT f.sku,
                       DATE_TRUNC('month', f.data_pedido)::date AS mes_data,
                       SUM(f.qt_pedido) AS volume
                FROM fato_vendas f
                JOIN dim_produtos p ON p.sku = f.sku AND COALESCE(p.ativo, FALSE) = TRUE
                WHERE f.data_pedido < :corte
                GROUP BY f.sku, DATE_TRUNC('month', f.data_pedido)
            ),
            lim AS (
                SELECT sku, MIN(mes_data) AS ini, MAX(mes_data) AS fim
                FROM v WHERE volume > 0 GROUP BY sku
            ),
            grade AS (
                SELECT l.sku, g.mes::date AS mes_data
                FROM lim l
                CROSS JOIN LATERAL generate_series(l.ini, l.fim, interval '1 month') AS g(mes)
            )
            SELECT g.sku, COALESCE(p.categoria, 'Sem categoria') AS categoria,
                   g.mes_data, COALESCE(v.volume, 0)::float AS volume
            FROM grade g
            JOIN dim_produtos p ON p.sku = g.sku
            LEFT JOIN v ON v.sku = g.sku AND v.mes_data = g.mes_data
            ORDER BY g.sku, g.mes_data
        """), db.bind, params={"corte": data_corte})

    # =================================================================
    # 2 e 3. O CAMPEONATO — TODO CANDIDATO PREVÊ O PASSADO
    # =================================================================
    def _disputar(self, y: np.ndarray, datas: list, meses_alvo=None,
                  categoria_y=None, categoria_datas=None) -> dict:
        """
        Origem rolante avançando de 1 mês, até JANELA_VALIDACAO origens.

        Em cada origem, todo candidato prevê M+0..M+4 a partir de dados
        estritamente anteriores, e o erro é registrado POR HORIZONTE.

        Diferenças em relação ao torneio anterior, que usava 3 blocos disjuntos
        de 5 meses e degradava para 1 bloco em série curta:
          - até 24 origens em vez de 3, o que reduz o peso da sorte;
          - erro separado por horizonte, porque a pergunta é quem é melhor em
            M+2, em M+3 e em M+4 — não na média dos cinco meses;
          - falha isolada não desqualifica. A regra antiga
            (len(validos) == num_folds) eliminava um candidato que quebrasse em
            um único fold, mesmo sendo o melhor nos demais — era assim que
            Prophet e AutoARIMA saíam por acidente, não por desempenho.

        Devolve {horizonte: {candidato: pontos}}. Mais pontos, melhor.
        """
        n = len(y)
        erros = {h: [] for h in range(HORIZONTE)}   # (origem, mês-alvo, erros)
        primeira = max(6, n - self.janela_validacao)
        meses_alvo = meses_alvo or [pd.Timestamp(datas[-1]) + pd.offsets.MonthBegin(k + 1)
                                    for k in range(HORIZONTE)]

        for i in range(primeira, n):
            y_tr, d_tr = y[:i], datas[:i]
            alvos = [d_tr[-1] + pd.offsets.MonthBegin(k + 1) for k in range(HORIZONTE)]
            try:
                contexto = None
                if categoria_y is not None and categoria_datas is not None:
                    mask = np.asarray(categoria_datas) <= pd.Timestamp(d_tr[-1])
                    contexto = {'categoria_y': np.asarray(categoria_y)[mask],
                                'categoria_datas': list(np.asarray(categoria_datas)[mask])}
                cand = gerar_todos_candidatos(y_tr, d_tr, alvos, contexto)
                cand = filtrar_candidatos_por_perfil(
                    cand, classificar_perfil_sku(y_tr, d_tr))
            except Exception as e:
                logger.debug("Falha ao gerar candidatos na origem %d: %s", i, e)
                continue
            for h in range(HORIZONTE):
                if i + h >= n:
                    continue
                real = float(y[i + h])
                rodada = {nome: abs(float(pred[h]) - real)
                          for nome, pred in cand.items() if np.isfinite(pred[h])}
                if rodada:
                    erros[h].append((i, alvos[h].month, rodada))

        return {h: self._somar_pontos(erros[h], primeira, n, meses_alvo[h].month)
                for h in range(HORIZONTE)}

    # =================================================================
    # 4. A SOMA DE PONTOS
    # =================================================================
    @staticmethod
    def _somar_pontos(rodadas: list, primeira: int, ultima: int,
                      mes_alvo: int = None) -> dict:
        """
        Em cada origem os candidatos são ranqueados pelo erro daquela origem.
        O melhor leva N pontos (N = número de competidores), o segundo N-1, e
        assim por diante — contagem de Borda. Empate divide os pontos.

        Origens recentes valem mais: o multiplicador vai de 1,0 na origem mais
        antiga da janela até 2,0 na mais recente. Isso faz o campeonato
        responder a mudança de comportamento do SKU sem descartar o passado.
        """
        pontos = {}
        span = max(ultima - primeira, 1)
        for item in rodadas:
            # Aceita também o formato antigo (origem, erros).
            if len(item) == 2:
                i, rodada = item
                mes_origem_alvo = None
            else:
                i, mes_origem_alvo, rodada = item
            nomes = list(rodada.keys())
            n = len(nomes)
            ordem = pd.Series({m: rodada[m] for m in nomes}).rank(method='average')
            peso = 1.0 + (i - primeira) / span          # 1,0 -> 2,0
            if mes_alvo is not None and mes_origem_alvo == mes_alvo:
                peso *= 2.0                              # mesma época do calendário
            for m in nomes:
                pontos[m] = pontos.get(m, 0.0) + (n - float(ordem[m]) + 1.0) * peso
        return pontos

    def _eleger(self, pontos: dict, campeao_anterior=None) -> str:
        """Quem somou mais pontos leva. HISTERESE > 0 exige margem para destronar."""
        if not pontos:
            return None
        campeao = max(pontos, key=pontos.get)
        if self.histerese > 0 and campeao_anterior in pontos:
            if pontos[campeao] < pontos[campeao_anterior] * (1 + self.histerese):
                return campeao_anterior
        return campeao

    # =================================================================
    # 5 e 6. O VENCEDOR PREVÊ — E NUNCA SAI NULO
    # =================================================================
    def prever_sku(self, y, datas, meses_alvo, campeoes_anteriores=None,
                   categoria_y=None, categoria_datas=None) -> tuple:
        """
        Devolve (previsao[HORIZONTE], rotulo, acuracia, campeoes).
        Garantia: nenhum valor nulo, negativo ou infinito.
        """
        y = np.nan_to_num(np.asarray(y, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        n = len(y)
        campeoes_anteriores = campeoes_anteriores or {}

        # --- degraus de fallback: histórico insuficiente para o campeonato ---
        if n == 0:
            return np.zeros(HORIZONTE), "Fallback_Zero", 0.0, {}
        if n <= 2:
            previsao = np.full(HORIZONTE, max(0.0, y.mean()))
            return self._sanear(previsao, y), "Fallback_Media2m", 0.0, {}
        if n <= 5:
            # medido: com 3 a 5 meses o último mês fechado bate todos os demais
            previsao = np.full(HORIZONTE, max(0.0, y[-1]))
            return self._sanear(previsao, y), "Fallback_UltimoMes", 0.0, {}
        if n < 12:
            # medido: de 6 a 11 meses a média simples de todo o histórico ganha
            previsao = np.full(HORIZONTE, max(0.0, y.mean()))
            return self._sanear(previsao, y), "Fallback_MediaSimples", 0.0, {}

        contexto = None
        if categoria_y is not None and categoria_datas is not None:
            contexto = {'categoria_y': categoria_y, 'categoria_datas': categoria_datas}
        cand = gerar_todos_candidatos(y, datas, meses_alvo, contexto)
        perfil = classificar_perfil_sku(y, datas)
        cand = filtrar_candidatos_por_perfil(cand, perfil)

        if n < MIN_MESES_TORNEIO:
            # o SKU passa por todos os modelos, mas não há origens suficientes
            # para um campeonato — o ensemble responde até o histórico crescer
            previsao = self._sanear(cand['Ens_Media'], y)
            return previsao, "SemTorneio_Ensemble", 0.0, {}

        placar = self._disputar(y, datas, meses_alvo, categoria_y, categoria_datas)
        previsao = np.zeros(HORIZONTE)
        rotulo, acuracias, campeoes = [], [], {}

        for h in range(HORIZONTE):
            pontos = {m: p for m, p in placar.get(h, {}).items() if m in cand}
            vencedor = self._eleger(pontos, campeoes_anteriores.get(h))
            if vencedor is None:
                previsao[h] = cand['Ens_Media'][h]
                continue
            previsao[h] = float(cand[vencedor][h])      # o vencedor prevê sozinho
            campeoes[h] = vencedor
            if h in HORIZ_DECISAO:
                total = sum(pontos.values()) or 1.0
                rotulo.append(f"M{h}:{vencedor}")
                acuracias.append(100.0 * pontos[vencedor] / total * len(pontos))

        acuracia = float(np.clip(np.mean(acuracias), 0, 100)) if acuracias else 0.0
        return self._sanear(previsao, y), "Torneio[" + "|".join(rotulo) + "]", acuracia, campeoes

    @staticmethod
    def _sanear(previsao, y) -> np.ndarray:
        """
        Rede final: NENHUM mês sai nulo, negativo ou infinito.
        Socorro: valor calculado -> média dos 3 últimos meses -> 0.
        """
        socorro = float(np.mean(y[-3:])) if len(y) else 0.0
        if not np.isfinite(socorro) or socorro < 0:
            socorro = 0.0
        p = np.asarray(previsao, dtype=float)
        p = np.where(np.isfinite(p), p, socorro)
        return np.round(np.maximum(p, 0.0), 2)

    # =================================================================
    # EXECUÇÃO DO CICLO
    # =================================================================
    def _campeoes_do_ciclo_anterior(self, db, ciclo_alvo: str) -> dict:
        """Lê o campeão gravado no ciclo anterior. Só é usado se HISTERESE > 0."""
        if self.histerese <= 0:
            return {}
        try:
            linhas = db.execute(text("""
                SELECT sku, modelo_vencedor FROM fato_ibp_granular
                WHERE modelo_vencedor LIKE 'Torneio[%%'
                  AND ciclo_sop <> :c
                GROUP BY sku, modelo_vencedor
            """), {"c": ciclo_alvo}).fetchall()
        except Exception as e:
            logger.debug("Não consegui ler campeões anteriores: %s", e)
            return {}
        saida = {}
        for r in linhas:
            mapa = {}
            for parte in str(r.modelo_vencedor).strip("Torneio[]").split("|"):
                if ":" in parte:
                    h, m = parte.split(":", 1)
                    if h.startswith("M") and h[1:].isdigit():
                        mapa[int(h[1:])] = m
            if mapa:
                saida[str(r.sku)] = mapa
        return saida

    def executar_arena(self, ciclo_alvo: str, log_callback=print):
        """Nome mantido por compatibilidade com o pipeline."""
        mes, ano = ciclo_alvo.split('/')
        inicio = date(int(ano), int(mes), 1)
        db = SessionLocal()
        resultados, contagem, por_fallback = [], {}, 0

        try:
            df_alpha = self._obter_dados_alpha(db, inicio.strftime("%Y-%m-%d"))
            if df_alpha.empty:
                log_callback("⚠️ [FORECASTER] Nenhuma venda anterior ao ciclo. Nada a prever.")
                return pl.DataFrame([])

            df_alpha['mes_data'] = pd.to_datetime(df_alpha['mes_data'])
            # Mantém compatibilidade com leitores/mocks legados sem categoria.
            if 'categoria' not in df_alpha.columns:
                df_alpha['categoria'] = 'Sem categoria'
            df_alpha['categoria'] = df_alpha['categoria'].fillna('Sem categoria')
            meses_alvo = [pd.Timestamp(inicio) + relativedelta(months=k) for k in range(HORIZONTE)]
            anteriores = self._campeoes_do_ciclo_anterior(db, ciclo_alvo)

            skus = df_alpha['sku'].unique()
            log_callback(f"   -> Torneio de {len(ARENA) + 3} candidatos para {len(skus)} SKUs "
                         f"({self.janela_validacao} origens de validação, "
                         f"histerese {self.histerese:.2f}).")

            for sku in skus:
                serie = montar_serie_mensal(df_alpha[df_alpha['sku'] == sku])
                categoria = df_alpha.loc[df_alpha['sku'] == sku, 'categoria'].iloc[0]
                serie_categoria = montar_serie_categoria(
                    df_alpha[df_alpha['categoria'] == categoria])
                try:
                    prev, rotulo, acuracia, _ = self.prever_sku(
                        serie['volume'].values, list(serie['mes_data']), meses_alvo,
                        anteriores.get(str(sku)),
                        serie_categoria['volume'].values,
                        list(serie_categoria['mes_data']),
                    )
                except Exception as e:
                    # Um SKU nunca derruba o ciclo e nunca sai sem número.
                    logger.exception("Falha no SKU %s: %s", sku, e)
                    base = float(serie['volume'].tail(3).mean()) if len(serie) else 0.0
                    prev = np.full(HORIZONTE, max(0.0, base if np.isfinite(base) else 0.0))
                    rotulo, acuracia = "Fallback_Erro", 0.0

                chave = rotulo.split('[')[0]
                contagem[chave] = contagem.get(chave, 0) + 1
                if chave.startswith("Fallback"):
                    por_fallback += 1

                # GRAVA SÓ OS HORIZONTES DE DECISÃO (M+2, M+3, M+4).
                # O vetor prev tem HORIZONTE posições — o modelo precisa
                # calcular M+0 e M+1 para a validação por origem rolante —
                # mas eles NÃO vão para o banco: a regra M-2 os torna
                # inalcançáveis pelo planejamento.
                for i in HORIZ_DECISAO:
                    resultados.append({
                        "ciclo_sop": ciclo_alvo,
                        "mes_projetado": inicio + relativedelta(months=i),
                        "sku": sku,
                        "vol_ia_global": float(prev[i]),
                        "modelo_vencedor": rotulo[:255],
                        "acuracia_ia": round(float(acuracia), 2),
                    })

            resumo = ", ".join(f"{k}: {v}" for k, v in sorted(contagem.items()))
            log_callback(f"   -> {len(skus)} SKUs previstos ({resumo}).")
            log_callback(f"   -> {len(resultados)} linhas geradas "
                         f"(horizontes {', '.join('M+'+str(h) for h in HORIZ_DECISAO)} "
                         f"— M+0 e M+1 não são gravados).")
            if por_fallback:
                log_callback(f"   ⚠️ {por_fallback} SKUs saíram por fallback (histórico curto). "
                             f"Têm número, mas erro esperado ~2x maior — revisar no S&OP.")
            return pl.DataFrame(resultados)
        finally:
            db.close()