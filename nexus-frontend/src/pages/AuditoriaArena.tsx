import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  Trophy, Medal, Bot, TrendingUp, TrendingDown, ChevronDown, ChevronRight,
  Loader2, HelpCircle, Target, Activity, BarChart3, ArrowUp, ArrowDown, Minus, Crown
} from 'lucide-react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, ScatterChart, Scatter, ZAxis, Cell, ReferenceLine
} from 'recharts';

// ============================================================
// HELPERS
// ============================================================
const fmtNum = (v: any, visao: string) => {
  const n = Number(v) || 0;
  if (visao === 'financeiro') {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n));
  }
  return Math.round(n).toLocaleString('pt-BR');
};
const fmtPct = (v: any) => `${(Number(v) || 0).toFixed(1)}%`;

// Cor por faixa de acurácia/nota (verde bom, âmbar médio, vermelho ruim).
const corNota = (nota: number) => {
  if (nota >= 80) return { txt: 'text-emerald-600', bg: 'bg-emerald-50', ring: 'ring-emerald-200', bar: '#059669' };
  if (nota >= 60) return { txt: 'text-amber-600', bg: 'bg-amber-50', ring: 'ring-amber-200', bar: '#d97706' };
  return { txt: 'text-rose-600', bg: 'bg-rose-50', ring: 'ring-rose-200', bar: '#e11d48' };
};

// Tooltip educativo (o "?" que explica a métrica com o número real).
const Explica = ({ titulo, texto }: { titulo: string; texto: string }) => (
  <span className="group relative inline-flex items-center ml-1 align-middle">
    <HelpCircle className="w-3.5 h-3.5 text-slate-300 hover:text-slate-500 cursor-help" />
    <span className="invisible group-hover:visible absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-3 bg-slate-900 text-white text-[11px] leading-relaxed rounded-xl shadow-xl font-medium">
      <span className="block font-black mb-1 text-indigo-300">{titulo}</span>
      {texto}
    </span>
  </span>
);

const DIM_LABELS: Record<string, string> = {
  coordenador: 'Coordenador', vendedor: 'Vendedor', cliente: 'Cliente',
  regional: 'Regional', categoria: 'Categoria', segmento: 'Segmento', sku: 'SKU',
};

// ============================================================
// COMPONENTE PRINCIPAL
// ============================================================
export default function AuditoriaArena() {
  const [aba, setAba] = useState<'ranking' | 'analise'>('ranking');
  const [visao, setVisao] = useState<'caixas' | 'financeiro'>('caixas');
  const [dimensao, setDimensao] = useState('coordenador');
  const [mesesDisp, setMesesDisp] = useState<string[]>([]);
  const [mesesSel, setMesesSel] = useState<string[]>([]);

  const [ranking, setRanking] = useState<any[]>([]);
  const [totais, setTotais] = useState<any>({});
  const [loading, setLoading] = useState(false);
  const [expandido, setExpandido] = useState<string | null>(null);
  const [drill, setDrill] = useState<Record<string, any[]>>({});

  const [evolucao, setEvolucao] = useState<any[]>([]);
  const [diag, setDiag] = useState<any>({ por_categoria: [], por_regional: [], dispersao_sku: [] });

  // Carrega meses auditáveis (dinâmico).
  useEffect(() => {
    axios.get('/api/v1/kpis/meses-auditaveis').then(r => {
      setMesesDisp(r.data.meses || []);
      setMesesSel(r.data.meses || []);
    }).catch(() => {});
  }, []);

  const paramsMeses = useMemo(() => {
    const p = new URLSearchParams();
    p.append('visao', visao);
    mesesSel.forEach(m => p.append('meses', m));
    return p;
  }, [visao, mesesSel]);

  // Carrega ranking.
  const carregarRanking = useCallback(async () => {
    if (mesesSel.length === 0) return;
    setLoading(true);
    try {
      const p = new URLSearchParams(paramsMeses);
      p.append('dimensao', dimensao);
      const r = await axios.get(`/api/v1/kpis/ranking?${p.toString()}`);
      setRanking(r.data.itens || []);
      setTotais(r.data.totais || {});
    } catch { setRanking([]); }
    finally { setLoading(false); }
  }, [dimensao, paramsMeses, mesesSel]);

  useEffect(() => { if (aba === 'ranking') carregarRanking(); }, [aba, carregarRanking]);

  // Carrega análise.
  useEffect(() => {
    if (aba !== 'analise' || mesesSel.length === 0) return;
    axios.get(`/api/v1/kpis/evolucao?visao=${visao}`).then(r => setEvolucao(r.data.serie || [])).catch(() => {});
    axios.get(`/api/v1/kpis/diagnostico?${paramsMeses.toString()}`).then(r => setDiag(r.data)).catch(() => {});
  }, [aba, visao, paramsMeses, mesesSel]);

  // Drilldown de um item do ranking.
  const toggleDrill = async (nome: string) => {
    if (expandido === nome) { setExpandido(null); return; }
    setExpandido(nome);
    if (!drill[nome]) {
      try {
        const p = new URLSearchParams(paramsMeses);
        p.append('dimensao', dimensao);
        p.append('valor', nome);
        p.append('sub_dimensao', 'sku');
        const r = await axios.get(`/api/v1/kpis/drilldown?${p.toString()}`);
        setDrill(prev => ({ ...prev, [nome]: r.data.itens || [] }));
      } catch { setDrill(prev => ({ ...prev, [nome]: [] })); }
    }
  };

  const dimsPessoa = dimensao === 'coordenador' || dimensao === 'vendedor';

  return (
    <div className="min-h-screen bg-slate-50 p-6 lg:p-8">
      <div className="max-w-[1500px] mx-auto">

        {/* CABEÇALHO */}
        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-black text-slate-900 tracking-tight flex items-center gap-2">
              <Trophy className="w-7 h-7 text-indigo-600" /> Desvios · Placar de Acurácia
            </h1>
            <p className="text-sm text-slate-500 font-medium mt-0.5">
              Quem posiciona melhor o produto no cliente erra menos. O plano congelado vs o que vendeu de fato.
            </p>
          </div>

          {/* Toggle caixas/financeiro */}
          <div className="flex items-center bg-white rounded-2xl p-1 shadow-sm border border-slate-100">
            <button onClick={() => setVisao('caixas')} className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${visao === 'caixas' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400'}`}>
              Caixas
            </button>
            <button onClick={() => setVisao('financeiro')} className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${visao === 'financeiro' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400'}`}>
              Financeiro
            </button>
          </div>
        </div>

        {/* ABAS */}
        <div className="flex items-center gap-2 mb-6">
          <button onClick={() => setAba('ranking')} className={`flex items-center gap-2 px-5 py-2.5 rounded-2xl text-sm font-black transition-all ${aba === 'ranking' ? 'bg-slate-900 text-white shadow-lg' : 'bg-white text-slate-500 border border-slate-100'}`}>
            <Trophy className="w-4 h-4" /> Ranking
          </button>
          <button onClick={() => setAba('analise')} className={`flex items-center gap-2 px-5 py-2.5 rounded-2xl text-sm font-black transition-all ${aba === 'analise' ? 'bg-slate-900 text-white shadow-lg' : 'bg-white text-slate-500 border border-slate-100'}`}>
            <Activity className="w-4 h-4" /> Análise da Empresa
          </button>

          {/* Seletor de meses (chips) */}
          <div className="flex items-center gap-1.5 ml-auto flex-wrap">
            {mesesDisp.map(m => {
              const on = mesesSel.includes(m);
              return (
                <button key={m} onClick={() => setMesesSel(on ? mesesSel.filter(x => x !== m) : [...mesesSel, m])}
                  className={`px-3 py-1.5 rounded-xl text-[11px] font-black transition-all ${on ? 'bg-indigo-100 text-indigo-700 ring-1 ring-indigo-200' : 'bg-white text-slate-400 border border-slate-100'}`}>
                  {m}
                </button>
              );
            })}
          </div>
        </div>

        {aba === 'ranking' ? (
          <RankingView
            visao={visao} dimensao={dimensao} setDimensao={setDimensao}
            ranking={ranking} totais={totais} loading={loading}
            expandido={expandido} toggleDrill={toggleDrill} drill={drill}
            dimsPessoa={dimsPessoa}
          />
        ) : (
          <AnaliseView visao={visao} evolucao={evolucao} diag={diag} />
        )}
      </div>
    </div>
  );
}

// ============================================================
// ABA RANKING (o jogo)
// ============================================================
function RankingView({ visao, dimensao, setDimensao, ranking, totais, loading, expandido, toggleDrill, drill, dimsPessoa }: any) {
  return (
    <>
      {/* CARTÕES DE SAÚDE — linguagem humana */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <CardSaude
          titulo="Acurácia do time" valor={fmtPct(totais.acuracia * 100)}
          sub={`de cada 100, erramos ${Math.round((totais.wmape || 0) * 100)}`}
          icone={<Target className="w-5 h-5" />} tom={totais.acuracia >= 0.8 ? 'bom' : totais.acuracia >= 0.6 ? 'medio' : 'ruim'}
          ajuda="Acurácia = 1 − WMAPE. De cada 100 caixas vendidas, quantas o plano acertou. Ponderada por volume: itens grandes pesam mais."
        />
        <CardSaude
          titulo="Tendência" valor={totais.vies_label || '—'}
          sub={`viés de ${fmtPct((totais.bias || 0) * 100)}`}
          icone={totais.bias > 0 ? <TrendingUp className="w-5 h-5" /> : <TrendingDown className="w-5 h-5" />}
          tom={Math.abs(totais.bias || 0) < 0.05 ? 'bom' : 'medio'}
          ajuda="BIAS = (previsto − realizado) / realizado. Mostra se o time sistematicamente prevê demais (infla estoque) ou de menos (rupturas). Perto de zero é saudável."
        />
        <CardSaude
          titulo="Valor vs Nexus Bot" valor={totais.fva >= 0 ? 'Supera a IA' : 'Abaixo da IA'}
          sub={`FVA ${fmtPct((totais.fva || 0) * 100)}`}
          icone={<Bot className="w-5 h-5" />} tom={totais.fva >= 0 ? 'bom' : 'ruim'}
          ajuda="FVA (Forecast Value Added) = erro da IA − erro do humano. Positivo: o ajuste humano melhorou a previsão da máquina. Negativo: seria melhor confiar na IA."
        />
        <CardSaude
          titulo={visao === 'financeiro' ? 'Realizado (R$)' : 'Realizado (cx)'} valor={fmtNum(totais.realizado, visao)}
          sub="volume vendido no período" icone={<BarChart3 className="w-5 h-5" />} tom="neutro"
          ajuda="Volume total efetivamente vendido (qt_pedido / vl_pedido) no período auditado. É a base que pondera todos os erros."
        />
      </div>

      {/* SELETOR DE DIMENSÃO */}
      <div className="flex items-center gap-1.5 mb-4 flex-wrap">
        <span className="text-[11px] font-black text-slate-400 uppercase tracking-widest mr-1">Ranquear por</span>
        {Object.keys(DIM_LABELS).map(d => (
          <button key={d} onClick={() => setDimensao(d)}
            className={`px-3.5 py-1.5 rounded-xl text-xs font-black transition-all ${dimensao === d ? 'bg-indigo-600 text-white shadow' : 'bg-white text-slate-500 border border-slate-100'}`}>
            {DIM_LABELS[d]}
          </button>
        ))}
      </div>

      {/* PLACAR */}
      <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 overflow-hidden">
        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Calculando o placar...</div>
        ) : ranking.length === 0 ? (
          <div className="p-16 text-center text-slate-400 font-medium">Sem dados para o período selecionado.</div>
        ) : (
          <div className="divide-y divide-slate-50">
            {/* Cabeçalho */}
            <div className="grid grid-cols-12 gap-2 px-6 py-3 bg-slate-50/50 text-[10px] font-black text-slate-400 uppercase tracking-widest">
              <div className="col-span-1">#</div>
              <div className="col-span-4">{DIM_LABELS[dimensao]}</div>
              <div className="col-span-2 text-center">Nota <Explica titulo="Nota do jogo (0–100)" texto="Combina acurácia (base), penalidade leve por viés, e bônus por superar o Nexus Bot. É o que decide a posição." /></div>
              <div className="col-span-2 text-center">Acertou</div>
              <div className="col-span-1 text-center">Tende a</div>
              <div className="col-span-2 text-center">vs Bot</div>
            </div>

            {ranking.map((it: any) => (
              <LinhaRanking key={it.nome} it={it} visao={visao} dimensao={dimensao}
                dimsPessoa={dimsPessoa} expandido={expandido} toggleDrill={toggleDrill} drill={drill} />
            ))}
          </div>
        )}
      </div>

      {/* EXPLICAÇÃO DOS CÁLCULOS */}
      <ExplicacaoCalculos />
    </>
  );
}

function LinhaRanking({ it, visao, dimensao, dimsPessoa, expandido, toggleDrill, drill }: any) {
  const c = corNota(it.nota);
  const aberto = expandido === it.nome;
  const podio = it.posicao <= 3 && !it.eh_bot;
  const medalCor = it.posicao === 1 ? 'text-yellow-500' : it.posicao === 2 ? 'text-slate-400' : 'text-amber-700';

  if (it.eh_bot) {
    return (
      <div className="grid grid-cols-12 gap-2 px-6 py-4 items-center bg-gradient-to-r from-indigo-50/70 to-transparent border-y-2 border-indigo-100">
        <div className="col-span-1 font-black text-indigo-400">{it.posicao}º</div>
        <div className="col-span-4 flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center"><Bot className="w-4 h-4 text-white" /></div>
          <span className="font-black text-indigo-700">Nexus Bot</span>
          <span className="text-[9px] font-black text-indigo-400 bg-indigo-100 px-2 py-0.5 rounded-full uppercase tracking-wider">a régua</span>
        </div>
        <div className="col-span-2 text-center"><span className="text-lg font-black text-indigo-600">{it.nota}</span></div>
        <div className="col-span-2 text-center font-bold text-indigo-500">{fmtPct(it.acuracia * 100)}</div>
        <div className="col-span-1 text-center text-[10px] font-bold text-indigo-400">{it.vies_label}</div>
        <div className="col-span-2 text-center text-[10px] font-black text-indigo-400 uppercase">benchmark</div>
      </div>
    );
  }

  return (
    <div>
      <div onClick={() => toggleDrill(it.nome)} className="grid grid-cols-12 gap-2 px-6 py-4 items-center hover:bg-slate-50/50 cursor-pointer transition-colors">
        <div className="col-span-1 flex items-center gap-1">
          {podio ? <Medal className={`w-5 h-5 ${medalCor}`} /> : <span className="font-black text-slate-400">{it.posicao}º</span>}
        </div>
        <div className="col-span-4 flex items-center gap-2 min-w-0">
          {aberto ? <ChevronDown className="w-4 h-4 text-slate-300 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-300 shrink-0" />}
          <span className="font-black text-slate-700 truncate" title={it.nome}>{it.nome}</span>
          {it.acima_do_bot && <span title="Acima do Nexus Bot" className="shrink-0 inline-flex"><Crown className="w-3.5 h-3.5 text-yellow-500" /></span>}
        </div>
        <div className="col-span-2 text-center">
          <span className={`inline-block px-3 py-1 rounded-xl font-black ${c.txt} ${c.bg} ring-1 ${c.ring}`}>{it.nota}</span>
        </div>
        <div className="col-span-2 text-center font-black text-slate-700">{fmtPct(it.acuracia * 100)}</div>
        <div className="col-span-1 text-center">
          {it.vies_label === 'prevê demais' ? <ArrowUp className="w-4 h-4 text-rose-400 mx-auto" />
            : it.vies_label === 'prevê de menos' ? <ArrowDown className="w-4 h-4 text-sky-400 mx-auto" />
            : <Minus className="w-4 h-4 text-emerald-400 mx-auto" />}
        </div>
        <div className="col-span-2 text-center">
          {dimsPessoa ? (
            it.acima_do_bot
              ? <span className="text-[10px] font-black text-emerald-600 bg-emerald-50 px-2 py-1 rounded-lg">supera ✓</span>
              : <span className="text-[10px] font-black text-rose-500 bg-rose-50 px-2 py-1 rounded-lg">abaixo</span>
          ) : <span className="text-[10px] text-slate-300">—</span>}
        </div>
      </div>

      {/* DRILLDOWN: onde essa pessoa mais erra */}
      {aberto && (
        <div className="px-6 pb-4 bg-slate-50/40">
          <div className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-2 pt-2">Onde {it.nome} mais erra (piores SKUs)</div>
          {!drill[it.nome] ? (
            <div className="text-slate-400 text-xs flex items-center gap-2 py-2"><Loader2 className="w-4 h-4 animate-spin" /> carregando...</div>
          ) : drill[it.nome].length === 0 ? (
            <div className="text-slate-400 text-xs py-2">Sem detalhamento.</div>
          ) : (
            <div className="space-y-1">
              {drill[it.nome].slice(0, 8).map((d: any, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs bg-white rounded-lg px-3 py-2 border border-slate-100">
                  <span className="font-bold text-slate-600 truncate">{d.nome}</span>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-slate-400">Real: {fmtNum(d.realizado, visao)}</span>
                    <span className={`font-black ${corNota(d.acuracia * 100).txt}`}>{fmtPct(d.acuracia * 100)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CardSaude({ titulo, valor, sub, icone, tom, ajuda }: any) {
  const tomCor = tom === 'bom' ? 'text-emerald-600' : tom === 'ruim' ? 'text-rose-600' : tom === 'medio' ? 'text-amber-600' : 'text-slate-700';
  const tomBg = tom === 'bom' ? 'bg-emerald-50' : tom === 'ruim' ? 'bg-rose-50' : tom === 'medio' ? 'bg-amber-50' : 'bg-slate-100';
  return (
    <div className="bg-white rounded-[24px] shadow-sm border border-slate-100 p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{titulo}<Explica titulo={titulo} texto={ajuda} /></span>
        <div className={`p-2 rounded-xl ${tomBg} ${tomCor}`}>{icone}</div>
      </div>
      <div className={`text-2xl font-black tracking-tight ${tomCor}`}>{valor}</div>
      <div className="text-[11px] font-bold text-slate-400 mt-1">{sub}</div>
    </div>
  );
}

// ============================================================
// ABA ANÁLISE (a empresa)
// ============================================================
function AnaliseView({ visao, evolucao, diag }: any) {
  return (
    <div className="space-y-6">
      {/* EVOLUÇÃO TEMPORAL */}
      <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 p-6">
        <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-1 flex items-center gap-2">
          <Activity className="w-4 h-4 text-indigo-600" /> Estamos aprendendo a acertar?
        </h3>
        <p className="text-xs text-slate-400 font-medium mb-4">Acurácia da empresa mês a mês, com a linha do Nexus Bot como referência.</p>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={evolucao} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis dataKey="mes" tick={{ fill: '#64748b', fontSize: 12, fontWeight: 700 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} unit="%" domain={[0, 100]} />
              <Tooltip contentStyle={{ borderRadius: 12, fontSize: 12, border: '1px solid #e2e8f0' }} formatter={(v: any) => `${v}%`} />
              <Legend wrapperStyle={{ fontSize: 12, fontWeight: 700, paddingTop: 10 }} iconType="circle" />
              <Line dataKey="acuracia" name="Empresa" stroke="#6366f1" strokeWidth={3} dot={{ r: 4 }} />
              <Line dataKey="acuracia_bot" name="Nexus Bot" stroke="#94a3b8" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* CONCENTRAÇÃO: categoria e regional */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <BarrasDiag titulo="Acurácia por categoria" dados={diag.por_categoria} />
        <BarrasDiag titulo="Acurácia por regional" dados={diag.por_regional} />
      </div>

      {/* MAPA VOLUME x ERRO */}
      <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 p-6">
        <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-1 flex items-center gap-2">
          <Target className="w-4 h-4 text-rose-500" /> Onde o erro dói no bolso
        </h3>
        <p className="text-xs text-slate-400 font-medium mb-4">Cada ponto é um SKU. Canto superior direito = muito volume + muito erro = prioridade máxima.</p>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis type="number" dataKey="volume" name="Volume" tick={{ fill: '#94a3b8', fontSize: 11 }}
                tickFormatter={(v) => new Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(v)}
                label={{ value: 'Volume realizado', position: 'insideBottom', offset: -10, fontSize: 11, fill: '#94a3b8' }} />
              <YAxis type="number" dataKey="erro_pct" name="Erro %" unit="%" tick={{ fill: '#94a3b8', fontSize: 11 }}
                label={{ value: 'Erro (WMAPE %)', angle: -90, position: 'insideLeft', fontSize: 11, fill: '#94a3b8' }} />
              <ZAxis range={[60, 60]} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ borderRadius: 12, fontSize: 12 }}
                formatter={(v: any, n: any) => n === 'Erro %' ? `${v}%` : fmtNum(v, visao)}
                labelFormatter={() => ''} content={({ payload }: any) => {
                  if (!payload || !payload.length) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-slate-900 text-white p-2 rounded-lg text-[11px]">
                      <div className="font-black">{d.sku}</div>
                      <div className="text-slate-300">{d.categoria}</div>
                      <div>Vol: {fmtNum(d.volume, visao)} · Erro: {d.erro_pct}%</div>
                    </div>
                  );
                }} />
              <Scatter data={diag.dispersao_sku} fill="#6366f1">
                {(diag.dispersao_sku || []).map((d: any, i: number) => (
                  <Cell key={i} fill={d.erro_pct > 30 ? '#e11d48' : d.erro_pct > 15 ? '#d97706' : '#059669'} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>

      <ExplicacaoCalculos />
    </div>
  );
}

function BarrasDiag({ titulo, dados }: any) {
  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 p-6">
      <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-4">{titulo}</h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={(dados || []).slice(0, 8)} layout="vertical" margin={{ left: 10, right: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false} />
            <XAxis type="number" domain={[0, 100]} unit="%" tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="nome" width={110} tick={{ fill: '#64748b', fontSize: 11, fontWeight: 700 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ borderRadius: 12, fontSize: 12 }} formatter={(v: any) => `${v}%`} />
            <Bar dataKey="acuracia" name="Acurácia" radius={[0, 6, 6, 0]}>
              {(dados || []).slice(0, 8).map((d: any, i: number) => (
                <Cell key={i} fill={d.acuracia >= 80 ? '#059669' : d.acuracia >= 60 ? '#d97706' : '#e11d48'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ============================================================
// EXPLICAÇÃO DOS CÁLCULOS (transparência do jogo)
// ============================================================
function ExplicacaoCalculos() {
  const [aberto, setAberto] = useState(false);
  const metricas = [
    { n: 'Acurácia', f: '1 − WMAPE', t: 'De cada 100 caixas vendidas, quantas o plano acertou. É a nota-base. Ponderada por volume — errar num item grande pesa mais que num pequeno.' },
    { n: 'WMAPE', f: 'Σ|real − previsto| ÷ Σreal', t: 'Erro percentual ponderado. Soma todos os erros e divide pelo total vendido. Mais justo que o MAPE simples porque itens grandes dominam, como no negócio real.' },
    { n: 'BIAS', f: '(Σprevisto − Σreal) ÷ Σreal', t: 'A tendência. Positivo: prevê demais (infla estoque, trava capital). Negativo: prevê de menos (gera ruptura). Perto de zero é o ideal.' },
    { n: 'FVA', f: 'erro da IA − erro do humano', t: 'Forecast Value Added. Mede se o ajuste humano melhorou a previsão pura da máquina. Positivo: você agregou valor. Negativo: a IA sozinha teria acertado mais.' },
    { n: 'Nota', f: 'Acurácia − 0,15·|BIAS| + 0,20·FVA', t: 'A pontuação do jogo. Premia acertar (acurácia), sem viés (penalidade leve), e melhor que o Nexus Bot (bônus). Difícil de manipular porque exige os três ao mesmo tempo.' },
  ];
  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 mt-6 overflow-hidden">
      <button onClick={() => setAberto(!aberto)} className="w-full flex items-center justify-between px-6 py-4 hover:bg-slate-50/50">
        <span className="text-sm font-black text-slate-700 flex items-center gap-2"><HelpCircle className="w-4 h-4 text-indigo-600" /> Como as notas são calculadas</span>
        {aberto ? <ChevronDown className="w-5 h-5 text-slate-400" /> : <ChevronRight className="w-5 h-5 text-slate-400" />}
      </button>
      {aberto && (
        <div className="px-6 pb-6 grid grid-cols-1 md:grid-cols-2 gap-3">
          {metricas.map(m => (
            <div key={m.n} className="bg-slate-50 rounded-2xl p-4 border border-slate-100">
              <div className="flex items-baseline gap-2 mb-1">
                <span className="font-black text-slate-800">{m.n}</span>
                <code className="text-[11px] font-mono text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">{m.f}</code>
              </div>
              <p className="text-xs text-slate-500 font-medium leading-relaxed">{m.t}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}