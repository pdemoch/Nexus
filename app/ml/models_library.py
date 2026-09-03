"""
models_library.py — a arena de candidatos do NexusForecaster
=============================================================

Todo candidato tem a MESMA assinatura: recebe a série (volume mensal, meses
alvo) e devolve SEMPRE um vetor de HORIZONTE floats finitos e não-negativos.
Nenhum candidato pode devolver nulo: quem não tem base para rodar devolve o
próprio fallback interno e compete assim mesmo, como manda a regra de que todo
SKU passa por todos os modelos.

Os modelos oficiais Prophet e XGBoost são opcionais para permitir execução em
ambientes mínimos; quando instalados, ambos participam da arena normalmente.
"""

import numpy as np
import pandas as pd
import warnings
import logging
from app.core.constants import HORIZONTE

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)
_LOGGED_CANDIDATE_ERRORS = set()

try:
    from scipy import stats as _stats
except ImportError:
    _stats = None

FORCA_SAZONAL = 0.35   # o índice sazonal entra a 35% da força estimada
P_SAZONAL = 0.10       # e só quando o efeito-mês tem p abaixo disto
PENALIDADE_VIES = 0.5  # peso do viés no score de validação

try:
    from prophet import Prophet as _Prophet
except ImportError:
    _Prophet = None

try:
    from xgboost import XGBRegressor as _XGBRegressor
except ImportError:
    _XGBRegressor = None


# =====================================================================
# PREPARO DA SÉRIE
# =====================================================================
def montar_serie_mensal(df, coluna_data='mes_data', coluna_volume='volume') -> pd.DataFrame:
    """
    Ordena, corta o período anterior à primeira venda (SKU ainda não existia) e
    garante espinha de meses: mês sem venda dentro do período de vida entra
    como ZERO, nunca como nulo nem como buraco na série.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame({coluna_data: [], coluna_volume: []})

    d = df[[coluna_data, coluna_volume]].copy()
    d[coluna_data] = pd.to_datetime(d[coluna_data])
    d[coluna_volume] = pd.to_numeric(d[coluna_volume], errors='coerce').fillna(0.0)
    d = d.groupby(coluna_data, as_index=False)[coluna_volume].sum().sort_values(coluna_data)

    positivos = d[d[coluna_volume] > 0]
    if positivos.empty:
        return d.reset_index(drop=True)

    d = d[d[coluna_data] >= positivos[coluna_data].min()]
    grade = pd.date_range(d[coluna_data].min(), d[coluna_data].max(), freq='MS')
    d = (d.set_index(coluna_data).reindex(grade).fillna(0.0)
           .rename_axis(coluna_data).reset_index())
    return d


def montar_serie_categoria(df, coluna_data='mes_data', coluna_volume='volume') -> pd.DataFrame:
    """Agrega a espinha mensal de vendas de uma categoria."""
    if df is None or len(df) == 0:
        return pd.DataFrame({coluna_data: [], coluna_volume: []})
    d = df[[coluna_data, coluna_volume]].copy()
    d[coluna_data] = pd.to_datetime(d[coluna_data])
    d[coluna_volume] = pd.to_numeric(d[coluna_volume], errors='coerce').fillna(0.0)
    return montar_serie_mensal(
        d.groupby(coluna_data, as_index=False)[coluna_volume].sum(),
        coluna_data, coluna_volume,
    )


def classificar_perfil_sku(y, datas=None) -> str:
    """Classifica a série do SKU para orientar a arena por comportamento."""
    valores = np.nan_to_num(np.asarray(y, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    n = len(valores)
    if n <= 6:
        return 'Lancamento'
    if np.mean(valores <= 0) >= 0.35:
        return 'Intermitente'
    media = float(np.mean(valores))
    cv = float(np.std(valores) / media) if media > 0 else 0.0
    sazonal = False
    if datas is not None and n >= 24:
        _, p = _indices_sazonais(valores, datas)
        sazonal = p < P_SAZONAL
    if sazonal:
        return 'Sazonal'
    if n >= 6 and media > 0:
        x = np.arange(n, dtype=float)
        incl = float(np.polyfit(x, valores, 1)[0] / media)
        if abs(incl) >= 0.02:
            return 'Tendencia'
    return 'Estavel' if cv < 0.6 else 'Volatil'


_CANDIDATOS_POR_PERFIL = {
    'Lancamento': {'Ens_Media', 'Ens_Mediana', 'MM3', 'Media2m', 'UltimoMes'},
    'Intermitente': {'Croston', 'SBA', 'TSB', 'ADIDA', 'MAPA_SES', 'MMPond12',
                     'Ens_Media', 'Ens_Mediana', 'Ens_Aparada'},
    'Sazonal': {'Sazonal_encolhido', 'SazonalNaive', 'AnualTendenciaRobusta',
                'TendenciaSazonalRobusta', 'SazonalCategoria',
                'SazonalHierarquica', 'ETS_AICc', 'Theta', 'MM12', 'MAPA_Theta',
                'Prophet', 'XGBoost', 'Ens_Media', 'Ens_Mediana', 'Ens_Aparada'},
    'Tendencia': {'Theta', 'ETS_AICc', 'Prophet', 'XGBoost',
                  'AnualTendenciaRobusta',
                  'TendenciaSazonalRobusta', 'Holt_amortecido', 'TheilSen',
                  'RegressaoAmortecida', 'MMAdaptativa', 'MAPA_Theta',
                  'MAPA_SES', 'Ens_Media', 'Ens_Mediana', 'Ens_Aparada'},
    'Estavel': set(),
    'Volatil': {'MM3', 'MM6', 'MM12', 'Mediana12', 'MediaAparada12',
                'Bootstrap', 'Ens_Mediana', 'Ens_Aparada', 'MMPond12'},
}


def filtrar_candidatos_por_perfil(candidatos: dict, perfil: str) -> dict:
    """Mantém candidatos adequados, sem deixar a arena sem fallback."""
    permitidos = _CANDIDATOS_POR_PERFIL.get(perfil, _CANDIDATOS_POR_PERFIL['Estavel'])
    if not permitidos:
        return candidatos
    filtrados = {nome: previsao for nome, previsao in candidatos.items()
                 if nome in permitidos}
    if len(filtrados) >= 3:
        return filtrados
    return candidatos


def score_validacao(erro_abs: float, volume_real: float, vies: float) -> float:
    """
    WMAPE + penalidade de viés — mesmo espírito do calcular_score_torneio
    original, agora acumulado sobre todas as origens de validação em vez de
    ser a média de três blocos.
    """
    if volume_real is None or volume_real <= 0:
        return float('inf')
    wmape = 100.0 * erro_abs / volume_real
    bias = 100.0 * abs(vies) / volume_real
    return wmape + PENALIDADE_VIES * bias


# =====================================================================
# BLOCOS AUXILIARES
# =====================================================================
def _mp(y, k):
    """Média móvel ponderada: peso linear crescente, mês recente pesa k."""
    w = y[-k:]
    if len(w) == 0:
        return 0.0
    p = np.arange(1, len(w) + 1, dtype=float)
    return float((w * p).sum() / p.sum())


def _sse_ses(y, alpha):
    l, s = y[0], 0.0
    for v in y[1:]:
        s += (v - l) ** 2
        l = alpha * v + (1 - alpha) * l
    return s


def _ses_nivel(y, alpha=None):
    """SES com alpha escolhido por SSE de um passo à frente."""
    if len(y) == 0:
        return 0.0
    if alpha is None:
        grade = np.arange(0.05, 0.96, 0.05)
        alpha = float(min(grade, key=lambda a: _sse_ses(y, a)))
    l = y[0]
    for v in y[1:]:
        l = alpha * v + (1 - alpha) * l
    return float(l)


def _croston_nucleo(y, alpha=0.1, sba=False, tsb=False, beta=0.05):
    """
    Croston clássico e as duas correções da literatura:
      SBA — Syntetos & Boylan (2005), corrige o viés de alta multiplicando
            por (1 - alpha/2);
      TSB — Teunter, Syntetos & Babai (2011), suaviza a PROBABILIDADE de
            demanda a cada período, não só nos períodos com venda, o que evita
            o viés de obsolescência quando o item para de vender.
    Todos dependem da espinha de meses com zeros para medir intervalo.
    """
    y = np.asarray(y, dtype=float)
    nz = np.where(y > 0)[0]
    if len(nz) == 0:
        return 0.0

    if tsb:
        z = float(y[nz[0]])
        p = float((y > 0).mean())
        for v in y:
            if v > 0:
                z = alpha * v + (1 - alpha) * z
                p = beta * 1.0 + (1 - beta) * p
            else:
                p = beta * 0.0 + (1 - beta) * p
        return float(max(0.0, p * z))

    z, intervalo, anterior = float(y[nz[0]]), 1.0, int(nz[0])
    for i in nz[1:]:
        z = alpha * y[i] + (1 - alpha) * z
        intervalo = alpha * (i - anterior) + (1 - alpha) * intervalo
        anterior = int(i)
    v = z / intervalo if intervalo > 0 else 0.0
    return float(max(0.0, v * (1 - alpha / 2) if sba else v))


def _indices_sazonais(y, datas):
    """
    Índices multiplicativos por mês (mediana da razão sobre a tendência) e
    p-valor do efeito-mês por Kruskal-Wallis.

    Medido nesta base: 32 SKUs têm efeito-mês significativo e 15 têm força
    sazonal acima de 0,50, somando 23% do volume. Mas aplicar o índice CHEIO
    piora mesmo nesses SKUs (25,9% contra 23,1% em M+2) — os índices são reais
    e ruidosos ao mesmo tempo. Daí o encolhimento em FORCA_SAZONAL.
    """
    if len(y) < 24 or _stats is None:
        return None, 1.0
    tendencia = pd.Series(y).rolling(12, center=True, min_periods=8).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        razao = np.where(tendencia > 0, y / tendencia, np.nan)
    meses = np.array([d.month for d in datas])
    idx, grupos = np.ones(13), []
    for m in range(1, 13):
        v = razao[(meses == m) & np.isfinite(razao)]
        if len(v) >= 2:
            idx[m] = float(np.median(v))
            grupos.append(v)
    if len(grupos) < 3:
        return None, 1.0
    idx[1:] = np.clip(idx[1:], 0.5, 2.0)
    idx[1:] /= idx[1:].mean()
    try:
        p = float(_stats.kruskal(*grupos).pvalue)
    except Exception:
        p = 1.0
    return idx, p


# =====================================================================
# CANDIDATOS DA ARENA
# Cada um recebe (y, datas, meses_alvo, ctx) e devolve lista de HORIZONTE.
# ctx traz o que é caro de recalcular (índices sazonais).
# =====================================================================
def _c_mmpond12(y, d, a, ctx):   return [_mp(y, min(12, len(y)))] * HORIZONTE
def _c_mm12(y, d, a, ctx):       return [float(y[-12:].mean())] * HORIZONTE
def _c_mm24(y, d, a, ctx):       return [float(y[-24:].mean())] * HORIZONTE
def _c_mm6(y, d, a, ctx):        return [float(y[-6:].mean())] * HORIZONTE
def _c_mm3(y, d, a, ctx):        return [float(y[-3:].mean())] * HORIZONTE
def _c_media2m(y, d, a, ctx):    return [float(y[-2:].mean())] * HORIZONTE
def _c_ultimo(y, d, a, ctx):     return [float(y[-1])] * HORIZONTE
def _c_mediana12(y, d, a, ctx):  return [float(np.median(y[-12:]))] * HORIZONTE


def _c_aparada12(y, d, a, ctx):
    """Média de 12 meses descartando o maior e o menor — imune a pico isolado."""
    w = np.sort(y[-12:])
    return [float(w[1:-1].mean()) if len(w) > 2 else float(w.mean())] * HORIZONTE


def _c_adaptativa(y, d, a, ctx):
    """
    Janela que encurta sozinha quando o patamar recente diverge do anterior.
    Não é regra de negócio nem trata substituição de item: é só um estimador de
    nível que reage mais rápido a mudança de patamar, e disputa a arena como
    qualquer outro.
    """
    n = len(y)
    if n < 12:
        return [_mp(y, n)] * HORIZONTE
    longa = _mp(y, 12)
    recente, anterior = y[-3:].mean(), y[-12:-3].mean()
    if anterior <= 0:
        return [longa] * HORIZONTE
    peso = float(min(1.0, abs(recente / anterior - 1.0) / 0.8))
    return [float((1 - peso) * longa + peso * _mp(y, 4))] * HORIZONTE


def _c_ses(y, d, a, ctx):        return [_ses_nivel(y)] * HORIZONTE
def _c_croston(y, d, a, ctx):    return [_croston_nucleo(y)] * HORIZONTE
def _c_sba(y, d, a, ctx):        return [_croston_nucleo(y, sba=True)] * HORIZONTE
def _c_tsb(y, d, a, ctx):        return [_croston_nucleo(y, tsb=True)] * HORIZONTE


def _c_holt(y, d, a, ctx, phi=0.85, alpha=0.3, beta=0.1):
    """
    Holt com tendência amortecida (Gardner & McKenzie). O amortecimento phi
    impede a extrapolação linear explodir em M+4, que era um dos modos de
    falha do HoltWinters com trend='add' puro.
    """
    if len(y) < 4:
        return [float(y.mean())] * HORIZONTE
    l, t = float(y[0]), float(y[1] - y[0])
    for v in y[1:]:
        lp = l
        l = alpha * v + (1 - alpha) * (l + phi * t)
        t = beta * (l - lp) + (1 - beta) * phi * t
    return [float(l + t * sum(phi ** (j + 1) for j in range(k + 1))) for k in range(HORIZONTE)]


def _c_theta(y, d, a, ctx):
    """Theta (Assimakopoulos & Nikolopoulos), vencedor da M3: SES + meia
    inclinação da reta de regressão."""
    if len(y) < 4:
        return [float(y.mean())] * HORIZONTE
    inclinacao = float(np.polyfit(np.arange(len(y)), y, 1)[0])
    nivel = _ses_nivel(y)
    return [float(nivel + 0.5 * inclinacao * (k + 1)) for k in range(HORIZONTE)]


def _c_regressao(y, d, a, ctx):
    """Regressão linear sobre os últimos 12 meses, com inclinação amortecida a
    50% — extrapolação linear pura em horizonte de 5 meses é agressiva demais."""
    w = y[-12:]
    if len(w) < 4:
        return [float(y.mean())] * HORIZONTE
    b, c = np.polyfit(np.arange(len(w)), w, 1)
    base = float(b * (len(w) - 1) + c)
    return [float(base + 0.5 * b * (k + 1)) for k in range(HORIZONTE)]


def _c_sazonal_enc(y, d, a, ctx):
    """Nível (média ponderada 12m) modulado pelo índice sazonal ENCOLHIDO."""
    base = _mp(y, min(12, len(y)))
    idx, p = ctx['saz']
    if idx is None or p >= P_SAZONAL:
        return [base] * HORIZONTE
    return [float(base * (1 + FORCA_SAZONAL * (idx[a[k].month] - 1))) for k in range(HORIZONTE)]


def _c_sazonal_naive(y, d, a, ctx):
    """Mesmo mês do ano anterior; sem 12 meses, cai para a média ponderada."""
    if len(y) < 12:
        return [_mp(y, len(y))] * HORIZONTE
    saida = []
    for k in range(HORIZONTE):
        j = len(y) - 12 + k
        saida.append(float(y[j]) if 0 <= j < len(y) else float(y[-12:].mean()))
    return saida


def _c_anual_tendencia_robusta(y, d, a, ctx):
    """Combina o mesmo mês de anos anteriores com tendência amortecida."""
    if len(y) < 24:
        return [_mp(y, min(12, len(y)))] * HORIZONTE
    saida = []
    recente = float(np.mean(y[-3:]))
    anterior = float(np.mean(y[-15:-12]))
    crescimento = np.clip((recente / anterior - 1.0) if anterior > 0 else 0.0, -0.20, 0.20)
    for k in range(HORIZONTE):
        valores = []
        for atraso in (12, 24, 36):
            pos = len(y) - atraso + k
            if 0 <= pos < len(y):
                valores.append(float(y[pos]))
        base = float(np.median(valores)) if valores else float(np.mean(y[-12:]))
        saida.append(base * (1.0 + 0.5 * crescimento))
    return saida


def _c_tendencia_sazonal_robusta(y, d, a, ctx):
    """Tendência Theil-Sen amortecida e ajustada pelo índice anual do SKU."""
    if len(y) < 24:
        return [_mp(y, min(12, len(y)))] * HORIZONTE
    w = y[-18:]
    slopes = [(w[j] - w[i]) / (j - i)
              for i in range(len(w) - 1) for j in range(i + 1, len(w))]
    slope = float(np.median(slopes)) if slopes else 0.0
    nivel = float(np.median(w))
    saz, p = ctx.get('saz', (None, 1.0))
    saida = []
    for k, alvo in enumerate(a):
        indice = float(saz[alvo.month]) if saz is not None and p < P_SAZONAL else 1.0
        tendencia = nivel + 0.5 * slope * (k + 1)
        saida.append(tendencia * (1.0 + FORCA_SAZONAL * (indice - 1.0)))
    return saida


def _c_sazonal_categoria(y, d, a, ctx):
    """Sazonalidade hierárquica: padrão da categoria encolhido para o SKU."""
    base = _mp(y, min(12, len(y)))
    idx, p = ctx.get('categoria_saz', (None, 1.0))
    if idx is None or p >= P_SAZONAL:
        return [base] * HORIZONTE
    return [float(base * (1 + FORCA_SAZONAL * (idx[a[k].month] - 1)))
            for k in range(HORIZONTE)]


def _c_sazonal_hierarquica(y, d, a, ctx):
    """Combina o sinal mensal do SKU e da categoria, favorecendo a categoria."""
    base = _mp(y, min(12, len(y)))
    cat_idx, cat_p = ctx.get('categoria_saz', (None, 1.0))
    sku_idx, sku_p = ctx.get('saz', (None, 1.0))
    if cat_idx is None and sku_idx is None:
        return [base] * HORIZONTE
    saida = []
    for k in range(HORIZONTE):
        fatores = []
        if cat_idx is not None and cat_p < P_SAZONAL:
            fatores.append((cat_idx[a[k].month], 0.65))
        if sku_idx is not None and sku_p < P_SAZONAL:
            fatores.append((sku_idx[a[k].month], 0.35))
        fator = sum(v * peso for v, peso in fatores) / sum(peso for _, peso in fatores) if fatores else 1.0
        saida.append(float(base * (1 + FORCA_SAZONAL * (fator - 1))))
    return saida



# =====================================================================
# AGREGAÇÃO TEMPORAL MÚLTIPLA E OUTROS CANDIDATOS DA LITERATURA RECENTE
# =====================================================================
def _theta_vetor(y, h=HORIZONTE):
    if len(y) < 4:
        return [float(np.mean(y))] * h
    inclinacao = float(np.polyfit(np.arange(len(y)), y, 1)[0])
    nivel = _ses_nivel(y)
    return [float(nivel + 0.5 * inclinacao * (k + 1)) for k in range(h)]


def _agregar(y, m):
    """Soma a série em blocos contíguos de m meses, alinhando pelo fim."""
    if len(y) // m < 1:
        return np.array([])
    corte = (len(y) // m) * m
    return y[len(y) - corte:].reshape(-1, m).sum(axis=1)


def _c_mapa_theta(y, d, a, ctx):
    """
    MAPA — Kourentzes, Petropoulos & Trapero (IJF 2014).

    Agrega a série em blocos de 1, 2, 3, 4 e 6 meses, prevê em cada frequência
    e desagrega de volta para o mês, combinando os níveis. A lógica: componentes
    que ficam escondidos no ruído mensal (tendência) aparecem no agregado, e o
    inverso também vale. Em vez de escolher um nível de agregação — que pode
    ser o errado — o método repete o processo em vários e combina, o que
    administra o risco de modelagem e aproveita o ganho conhecido de combinação.

    Neste conjunto de dados MAPA vence 2,1% dos SKUs em M+2. Não melhorou o
    resultado agregado do torneio, mas ganha onde ganha por mérito.
    """
    estimativas = []
    for m in (1, 2, 3, 4, 6):
        agg = _agregar(y, m)
        if len(agg) < 5:
            continue
        passos = int(np.ceil((HORIZONTE + 1) / m)) + 1
        f = np.asarray(_theta_vetor(agg, passos), dtype=float)
        estimativas.append(np.repeat(f / m, m)[:HORIZONTE])
    if not estimativas:
        return [_mp(y, min(12, len(y)))] * HORIZONTE
    return list(np.mean(np.vstack(estimativas), axis=0))


def _c_mapa_ses(y, d, a, ctx):
    """Mesma ideia do MAPA, com SES como método base em cada nível."""
    estimativas = []
    for m in (1, 2, 3, 4, 6):
        agg = _agregar(y, m)
        if len(agg) < 5:
            continue
        estimativas.append(np.full(HORIZONTE, _ses_nivel(agg) / m))
    if not estimativas:
        return [_mp(y, min(12, len(y)))] * HORIZONTE
    return list(np.mean(np.vstack(estimativas), axis=0))


def _c_adida(y, d, a, ctx, bloco=3):
    """
    ADIDA — Nikolopoulos, Syntetos, Boylan, Petropoulos & Assimakopoulos (2011).
    Agrega em blocos até a intermitência desaparecer, prevê o bloco e
    desagrega. Alternativa a Croston para item de giro lento.
    """
    agg = _agregar(y, bloco)
    if len(agg) < 3:
        return [_mp(y, min(12, len(y)))] * HORIZONTE
    return [float(_ses_nivel(agg) / bloco)] * HORIZONTE


def _erro_um_passo(y, alpha, beta=None, phi=1.0):
    """SSE de previsão um passo à frente, usado no critério de informação."""
    n = len(y)
    if beta is None:
        return _sse_ses(y, alpha)
    l, t, sse = float(y[0]), float(y[1] - y[0]) if n > 1 else 0.0, 0.0
    for v in y[1:]:
        sse += (v - (l + phi * t)) ** 2
        lp = l
        l = alpha * v + (1 - alpha) * (l + phi * t)
        t = beta * (l - lp) + (1 - beta) * phi * t
    return sse


def _c_ets_aicc(y, d, a, ctx):
    """
    ETS automático por AICc — família de Hyndman & Khandakar, escolhendo entre
    erro constante (ANN), tendência (AAN) e tendência amortecida (AAdN).

    Diferente de todo o resto da arena: seleciona por PARSIMÔNIA, penalizando
    parâmetros, não por erro de backtest. É uma segunda opinião de natureza
    distinta dentro do campeonato.
    """
    n = len(y)
    if n < 8:
        return [float(np.mean(y))] * HORIZONTE

    melhor = (np.inf, None, None)
    for alpha in np.arange(0.1, 0.95, 0.1):
        sse, k = _erro_um_passo(y, alpha), 2
        aicc = n * np.log(sse / n + 1e-12) + 2 * k + (2 * k * (k + 1)) / max(n - k - 1, 1)
        if aicc < melhor[0]:
            melhor = (aicc, 'ANN', (alpha,))
        for beta in np.arange(0.05, 0.5, 0.1):
            for phi in (1.0, 0.8, 0.9, 0.98):
                sse = _erro_um_passo(y, alpha, beta, phi)
                k = 3 if phi == 1.0 else 4
                aicc = n * np.log(sse / n + 1e-12) + 2 * k + (2 * k * (k + 1)) / max(n - k - 1, 1)
                if aicc < melhor[0]:
                    melhor = (aicc, 'AAN' if phi == 1.0 else 'AAdN', (alpha, beta, phi))

    _, tipo, par = melhor
    if tipo == 'ANN' or par is None or len(par) < 3:
        return [_ses_nivel(y, par[0] if par else None)] * HORIZONTE
    alpha, beta, phi = par
    l, t = float(y[0]), float(y[1] - y[0])
    for v in y[1:]:
        lp = l
        l = alpha * v + (1 - alpha) * (l + phi * t)
        t = beta * (l - lp) + (1 - beta) * phi * t
    return [float(l + t * sum(phi ** (j + 1) for j in range(k + 1))) for k in range(HORIZONTE)]


def _c_prophet(y, d, a, ctx):
    """Prophet opcional, treinado somente com o histórico recebido pelo fold."""
    if _Prophet is None or len(y) < 24:
        return [_c_ets_aicc(y, d, a, ctx)[0]] * HORIZONTE
    treino = pd.DataFrame({"ds": pd.to_datetime(d), "y": np.maximum(y, 0.0)})
    modelo = _Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05,
    )
    modelo.fit(treino)
    futuro = pd.DataFrame({"ds": pd.to_datetime(a)})
    return modelo.predict(futuro)["yhat"].to_numpy(dtype=float).tolist()


def _c_xgboost(y, d, a, ctx):
    """XGBoost recursivo com defasagens e calendário, sem olhar o futuro."""
    if _XGBRegressor is None or len(y) < 18:
        return [_c_ets_aicc(y, d, a, ctx)[0]] * HORIZONTE
    valores = np.maximum(np.asarray(y, dtype=float), 0.0)
    datas = pd.to_datetime(d)
    janela = 12

    def atributos(serie, pos, data):
        inicio = max(0, pos - janela)
        hist = serie[inicio:pos]
        if len(hist) < janela:
            hist = np.pad(hist, (janela - len(hist), 0), mode="edge")
        return list(hist[-janela:]) + [
            np.sin(2 * np.pi * data.month / 12),
            np.cos(2 * np.pi * data.month / 12),
            float(np.mean(hist[-3:])),
            float(np.mean(hist[-12:])),
        ]

    X = [atributos(valores, i, datas[i]) for i in range(janela, len(valores))]
    y_treino = valores[janela:]
    modelo = _XGBRegressor(
        n_estimators=120, max_depth=2, learning_rate=0.04,
        subsample=0.9, colsample_bytree=0.9, objective="reg:squarederror",
        random_state=42, n_jobs=1, verbosity=0,
    )
    modelo.fit(np.asarray(X), y_treino)
    serie = list(valores)
    saida = []
    for data in pd.to_datetime(a):
        pos = len(serie)
        pred = float(modelo.predict(np.asarray([atributos(np.asarray(serie), pos, data)]))[0])
        pred = max(0.0, pred)
        saida.append(pred)
        serie.append(pred)
    return saida


def _c_theil_sen(y, d, a, ctx, janela=18):
    """
    Tendência por Theil-Sen: mediana das inclinações entre todos os pares de
    pontos. Um pico isolado desloca a reta mínimos-quadrados; a mediana o
    ignora. Inclinação amortecida a 50% porque extrapolar linear em 5 meses é
    agressivo.
    """
    w = y[-janela:]
    if len(w) < 6:
        return [float(np.mean(y))] * HORIZONTE
    n = len(w)
    idx = np.arange(n)
    inclinacoes = []
    for i in range(n - 1):
        inclinacoes.extend((w[i + 1:] - w[i]) / (idx[i + 1:] - idx[i]))
    b = float(np.median(inclinacoes))
    intercepto = float(np.median(w - b * idx))
    base = b * (n - 1) + intercepto
    return [float(base + 0.5 * b * (k + 1)) for k in range(HORIZONTE)]


def _c_bootstrap(y, d, a, ctx, janela=12, amostras=400):
    """
    Mediana da distribuição bootstrap da média dos últimos 12 meses.
    Estima o centro da distribuição sem supor forma, e a mediana é menos
    sensível a cauda que a média amostral.
    """
    w = y[-janela:]
    if len(w) < 4:
        return [float(np.mean(y))] * HORIZONTE
    rng = np.random.default_rng(42)
    medias = rng.choice(w, size=(amostras, len(w)), replace=True).mean(axis=1)
    return [float(np.median(medias))] * HORIZONTE


ARENA = {
    'MMPond12':            _c_mmpond12,
    'MM12':                _c_mm12,
    'MM24':                _c_mm24,
    'MM6':                 _c_mm6,
    'MM3':                 _c_mm3,
    'Media2m':             _c_media2m,
    'UltimoMes':           _c_ultimo,
    'Mediana12':           _c_mediana12,
    'MediaAparada12':      _c_aparada12,
    'MMAdaptativa':        _c_adaptativa,
    'SES':                 _c_ses,
    'Holt_amortecido':     _c_holt,
    'Theta':               _c_theta,
    'RegressaoAmortecida': _c_regressao,
    'Croston':             _c_croston,
    'SBA':                 _c_sba,
    'TSB':                 _c_tsb,
    'Sazonal_encolhido':   _c_sazonal_enc,
    'SazonalNaive':        _c_sazonal_naive,
    'AnualTendenciaRobusta': _c_anual_tendencia_robusta,
    'TendenciaSazonalRobusta': _c_tendencia_sazonal_robusta,
    'SazonalCategoria':    _c_sazonal_categoria,
    'SazonalHierarquica':  _c_sazonal_hierarquica,
    'MAPA_Theta':          _c_mapa_theta,
    'MAPA_SES':            _c_mapa_ses,
    'ADIDA':               _c_adida,
    'ETS_AICc':            _c_ets_aicc,
    'Prophet':              _c_prophet,
    'XGBoost':              _c_xgboost,
    'TheilSen':            _c_theil_sen,
    'Bootstrap':           _c_bootstrap,
}


def _limpar(v, socorro):
    """Nenhum candidato devolve nulo, negativo ou infinito. Nunca."""
    arr = np.asarray(v, dtype=float).ravel()
    if len(arr) < HORIZONTE:
        arr = np.pad(arr, (0, HORIZONTE - len(arr)),
                     mode='edge' if len(arr) else 'constant',
                     constant_values=socorro)
    arr = arr[:HORIZONTE]
    arr = np.where(np.isfinite(arr), arr, socorro)
    return np.maximum(arr, 0.0)


def gerar_todos_candidatos(y, datas, meses_alvo, contexto=None) -> dict:
    """
    Roda a arena inteira mais os três ensembles. TODO SKU passa por TODOS os
    candidatos; quem falha ou não tem base devolve o socorro (média dos 3
    últimos meses) e segue competindo.
    """
    y = np.nan_to_num(np.asarray(y, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    if len(y) == 0:
        zero = np.zeros(HORIZONTE)
        return {nome: zero.copy() for nome in ARENA}

    socorro = float(np.mean(y[-3:]))
    if not np.isfinite(socorro) or socorro < 0:
        socorro = 0.0

    ctx = {'saz': _indices_sazonais(y, datas)}
    if contexto:
        cy, cd = contexto.get('categoria_y'), contexto.get('categoria_datas')
        ctx['categoria_saz'] = _indices_sazonais(cy, cd) if cy is not None else (None, 1.0)
    cand = {}
    for nome, fn in ARENA.items():
        try:
            cand[nome] = _limpar(fn(y, datas, meses_alvo, ctx), socorro)
        except Exception as exc:
            cand[nome] = np.full(HORIZONTE, socorro)
            if nome not in _LOGGED_CANDIDATE_ERRORS:
                logger.warning(
                    "Candidato %s indisponivel; usando fallback: %s: %s",
                    nome, type(exc).__name__, exc,
                )
                _LOGGED_CANDIDATE_ERRORS.add(nome)

    # Ensembles entram como CANDIDATOS e disputam a eleição do vencedor.
    # A deduplicação evita que candidatos que colapsaram no mesmo número
    # (típico quando o teste sazonal reprova) contem duas vezes na média.
    base = np.vstack(list(cand.values()))
    unicos = [np.unique(np.round(base[:, h], 6)) for h in range(HORIZONTE)]
    cand['Ens_Media']   = _limpar([u.mean() for u in unicos], socorro)
    cand['Ens_Mediana'] = _limpar([np.median(u) for u in unicos], socorro)
    cand['Ens_Aparada'] = _limpar(
        [np.sort(u)[1:-1].mean() if len(u) > 2 else u.mean() for u in unicos], socorro)
    return cand