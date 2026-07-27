import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  Activity, TrendingUp, TrendingDown, Target, Bot, HelpCircle,
  ChevronDown, ChevronRight, Loader2, AlertTriangle, Filter, X
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine
} from 'recharts';

// ============================================================
// HELPERS
// ============================================================
const fmtNum = (v: any, visao: string) => {
  const n = Number(v) || 0;
  if (visao === 'financeiro') return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n));
  return Math.round(n).toLocaleString('pt-BR');
};
const fmtPct = (v: any) => `${(Number(v) || 0).toFixed(1)}%`;

const DIMS = [
  { chave: 'categoria', label: 'Categoria' },
  { chave: 'segmento', label: 'Segmento' },
  { chave: 'sku', label: 'SKU' },
  { chave: 'regional', label: 'Regional' },
  { chave: 'cliente', label: 'Cliente' },
  { chave: 'coordenador', label: 'Coordenador' },
  { chave: 'vendedor', label: 'Vendedor' },
];

// Ponto que marca mês parcial (aro vazado tracejado).
const PontoParcial = (props: any) => {
  const { cx, cy, payload, stroke } = props;
  if (cx == null || cy == null) return null;
  if (payload?.parcial) return <circle cx={cx} cy={cy} r={5} fill="#fff" stroke={stroke} strokeWidth={2} strokeDasharray="2 2" />;
  return <circle cx={cx} cy={cy} r={4} fill={stroke} />;
};

// ============================================================
// COMPONENTE PRINCIPAL
// ============================================================
export default function AuditoriaArena() {
  const [visao, setVisao] = useState<'caixas' | 'financeiro'>('caixas');
  const [mesesDisp, setMesesDisp] = useState<any[]>([]);
  const [mesesSel, setMesesSel] = useState<string[]>([]);
  const [filtros, setFiltros] = useState<Record<string, string>>({});
  const [serie, setSerie] = useState<any[]>([]);
  const [totais, setTotais] = useState<any>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    axios.get('/api/v1/kpis/meses-auditaveis').then(r => {
      // Normalizacao defensiva: aceita tanto o formato novo (objeto
      // {mes, ano, num, parcial}) quanto o antigo (string "MM/YYYY"),
      // para descompasso de versao backend/front nunca quebrar a tela.
      const brutos = r.data.meses || [];
      const ms = brutos.map((m: any) => typeof m === 'string'
        ? { mes: m, ano: m.split('/')[1] || '', num: m.split('/')[0] || m, parcial: false }
        : m);
      setMesesDisp(ms);
      setMesesSel(ms.map((m: any) => m.mes));
    }).catch(() => {});
  }, []);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    p.append('visao', visao);
    mesesSel.forEach(m => p.append('meses', m));
    Object.entries(filtros).forEach(([k, v]) => { if (v) p.append(k, v); });
    return p;
  }, [visao, mesesSel, filtros]);

  const carregar = useCallback(async () => {
    if (mesesSel.length === 0) { setSerie([]); setTotais({}); return; }
    setLoading(true);
    try {
      const [ev, rs] = await Promise.all([
        axios.get(`/api/v1/kpis/evolucao?${params.toString()}`),
        axios.get(`/api/v1/kpis/resumo?${params.toString()}`),
      ]);
      setSerie(ev.data.serie || []);
      setTotais(rs.data.totais || {});
    } catch { setSerie([]); setTotais({}); }
    finally { setLoading(false); }
  }, [params, mesesSel]);

  useEffect(() => { carregar(); }, [carregar]);

  const temParcial = serie.some(s => s.parcial);
  const filtrosAtivos = Object.entries(filtros).filter(([, v]) => v);

  return (
    <div className="min-h-screen bg-slate-50 p-6 lg:p-8">
      <div className="max-w-[1500px] mx-auto">

        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-black text-slate-900 tracking-tight flex items-center gap-2">
              <Activity className="w-7 h-7 text-indigo-600" /> Desvios · Acurácia do S&OP
            </h1>
            <p className="text-sm text-slate-500 font-medium mt-0.5">
              O plano congelado (2 meses antes) vs o que vendeu de fato. IA contra o humano, mês a mês.
            </p>
          </div>
          <div className="flex items-center bg-white rounded-2xl p-1 shadow-sm border border-slate-100">
            <button onClick={() => setVisao('caixas')} className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${visao === 'caixas' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400'}`}>Caixas</button>
            <button onClick={() => setVisao('financeiro')} className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${visao === 'financeiro' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400'}`}>Financeiro</button>
          </div>
        </div>

        {/* FILTROS */}
        <div className="bg-white rounded-[24px] shadow-sm border border-slate-100 p-4 mb-6">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-black text-slate-400 uppercase tracking-widest flex items-center gap-1.5"><Filter className="w-3.5 h-3.5" /> Filtros</span>
            <SeletorMeses mesesDisp={mesesDisp} mesesSel={mesesSel} setMesesSel={setMesesSel} />
            {DIMS.map(d => (
              <SeletorFiltro key={d.chave} dim={d} valor={filtros[d.chave] || ''}
                onChange={(v: string) => setFiltros(prev => ({ ...prev, [d.chave]: v }))} />
            ))}
            {filtrosAtivos.length > 0 && (
              <button onClick={() => setFiltros({})} className="flex items-center gap-1 text-[11px] font-black text-rose-500 hover:text-rose-600 ml-1">
                <X className="w-3.5 h-3.5" /> limpar
              </button>
            )}
          </div>
          {filtrosAtivos.length > 0 && (
            <div className="flex items-center gap-2 mt-3 flex-wrap">
              {filtrosAtivos.map(([k, v]) => (
                <span key={k} className="text-[11px] font-black text-indigo-700 bg-indigo-50 px-2.5 py-1 rounded-lg flex items-center gap-1.5">
                  {DIMS.find(d => d.chave === k)?.label}: {v}
                  <button onClick={() => setFiltros(prev => ({ ...prev, [k]: '' }))}><X className="w-3 h-3" /></button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* CARTÕES RESUMO */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <CardResumo titulo="Acurácia" valor={fmtPct((totais.acuracia || 0) * 100)}
            sub={`erro de ${fmtPct((totais.wmape || 0) * 100)}`} tom={(totais.acuracia || 0) >= 0.7 ? 'bom' : (totais.acuracia || 0) >= 0.5 ? 'medio' : 'ruim'}
            icone={<Target className="w-5 h-5" />} ajuda="Acurácia = 1 − WMAPE. Mede, sobre os totais do recorte, quão perto o plano ficou do realizado." />
          <CardResumo titulo="Tendência (BIAS)" valor={totais.vies_label || '—'}
            sub={`viés de ${fmtPct((totais.bias || 0) * 100)}`} tom={Math.abs(totais.bias || 0) < 0.05 ? 'bom' : 'medio'}
            icone={(totais.bias || 0) > 0 ? <TrendingUp className="w-5 h-5" /> : <TrendingDown className="w-5 h-5" />}
            ajuda="BIAS = (previsto − realizado) / realizado. Positivo = prevê demais (infla estoque). Negativo = prevê de menos (rupturas)." />
          <CardResumo titulo="Valor vs IA (FVA)" valor={(totais.fva || 0) >= 0 ? 'Humano supera' : 'IA supera'}
            sub={`FVA ${fmtPct((totais.fva || 0) * 100)}`} tom={(totais.fva || 0) >= 0 ? 'bom' : 'ruim'}
            icone={<Bot className="w-5 h-5" />} ajuda="FVA = erro da IA − erro do humano. Positivo: o ajuste humano melhorou a previsão da máquina." />
          <CardResumo titulo={visao === 'financeiro' ? 'Realizado (R$)' : 'Realizado (cx)'} valor={fmtNum(totais.realizado, visao)}
            sub={`${fmtPct((totais.cobertura ?? 1) * 100)} coberto pelo plano`}
            tom={(totais.cobertura ?? 1) >= 0.9 ? 'neutro' : 'medio'} icone={<Activity className="w-5 h-5" />}
            ajuda="Total vendido no recorte. A cobertura mostra quanto desse total estava no radar do planejamento (tinha previsão). O restante é demanda fora do radar — detalhada abaixo." />
        </div>

        {temParcial && (
          <div className="flex items-center gap-2 text-[12px] font-bold text-amber-600 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 mb-6">
            <AlertTriangle className="w-4 h-4" /> Meses com aro tracejado ainda estão em andamento — a acurácia é parcial e tende a melhorar até o fechamento.
          </div>
        )}

        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Calculando indicadores...</div>
        ) : (
          <>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
              <GraficoLinha titulo="WMAPE — erro percentual do plano" subtitulo="Quanto o plano errou vs o que vendeu. Menor é melhor."
                serie={serie} chaves={[{ k: 'wmape', nome: 'Humano (S&OP)', cor: '#6366f1' }, { k: 'wmape_ia', nome: 'Nexus Bot (IA)', cor: '#94a3b8' }]}
                ajuda="WMAPE = |Σprevisto − Σrealizado| ÷ Σrealizado. Erro sobre os totais do recorte. 20% = o plano errou 20 de cada 100 caixas." />
              <GraficoLinha titulo="Acurácia — o quanto acertou" subtitulo="1 − WMAPE. Maior é melhor."
                serie={serie} chaves={[{ k: 'acuracia', nome: 'Humano (S&OP)', cor: '#059669' }, { k: 'acuracia_ia', nome: 'Nexus Bot (IA)', cor: '#94a3b8' }]}
                ajuda="Acurácia = 1 − WMAPE. De cada 100 caixas vendidas, quantas o plano acertou no total." />
              <GraficoLinha titulo="BIAS — tendência de erro" subtitulo="Acima de zero: prevê demais. Abaixo: prevê de menos."
                serie={serie} chaves={[{ k: 'bias', nome: 'Humano (S&OP)', cor: '#d97706' }, { k: 'bias_ia', nome: 'Nexus Bot (IA)', cor: '#94a3b8' }]}
                ajuda="BIAS = (Σprevisto − Σrealizado) ÷ Σrealizado. Viés sistemático: inflar (positivo) ou subestimar (negativo)." zero />
              <GraficoLinha titulo="FVA — valor que o humano agrega" subtitulo="Acima de zero: o humano bate a IA. Abaixo: a IA seria melhor."
                serie={serie} chaves={[{ k: 'fva', nome: 'FVA (humano − IA)', cor: '#7c3aed' }]}
                ajuda="FVA (Forecast Value Added) = erro da IA − erro do humano. Positivo: o ajuste humano melhorou a previsão pura da máquina." zero />
            </div>
            <SemPlano params={params} visao={visao} />
            <ExplicacaoCalculos />
          </>
        )}
      </div>
    </div>
  );
}

function GraficoLinha({ titulo, subtitulo, serie, chaves, ajuda, zero }: any) {
  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 p-6">
      <h3 className="text-sm font-black text-slate-800 tracking-tight">{titulo}<Explica titulo={titulo} texto={ajuda} /></h3>
      <p className="text-xs text-slate-400 font-medium mb-4">{subtitulo}</p>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={serie} margin={{ top: 10, right: 20, left: -10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
            <XAxis dataKey="mes" tick={{ fill: '#64748b', fontSize: 12, fontWeight: 700 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false} unit="%" />
            <Tooltip contentStyle={{ borderRadius: 12, fontSize: 12, border: '1px solid #e2e8f0' }} formatter={(v: any) => `${v}%`}
              labelFormatter={(l: any) => { const p = serie.find((s: any) => s.mes === l); return p?.parcial ? `${l} (parcial)` : l; }} />
            <Legend wrapperStyle={{ fontSize: 12, fontWeight: 700, paddingTop: 8 }} iconType="circle" />
            {zero && <ReferenceLine y={0} stroke="#cbd5e1" strokeWidth={1.5} />}
            {chaves.map((c: any) => (
              <Line key={c.k} dataKey={c.k} name={c.nome} stroke={c.cor} strokeWidth={c.k.includes('ia') ? 2 : 3}
                strokeDasharray={c.k.includes('ia') ? '5 5' : undefined}
                dot={<PontoParcial stroke={c.cor} />} activeDot={{ r: 6 }} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function SeletorMeses({ mesesDisp, mesesSel, setMesesSel }: any) {
  const [aberto, setAberto] = useState(false);
  const porAno = useMemo(() => {
    const g: Record<string, any[]> = {};
    mesesDisp.forEach((m: any) => { (g[m.ano] = g[m.ano] || []).push(m); });
    return g;
  }, [mesesDisp]);
  const toggle = (mes: string) => setMesesSel(mesesSel.includes(mes) ? mesesSel.filter((x: string) => x !== mes) : [...mesesSel, mes]);
  const toggleAno = (ano: string) => {
    const doAno = porAno[ano].map((m: any) => m.mes);
    const todosSel = doAno.every((m: string) => mesesSel.includes(m));
    setMesesSel(todosSel ? mesesSel.filter((m: string) => !doAno.includes(m)) : [...new Set([...mesesSel, ...doAno])]);
  };
  return (
    <div className="relative">
      <button onClick={() => setAberto(!aberto)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-black bg-slate-100 text-slate-600 hover:bg-slate-200">
        {mesesSel.length === 0 ? 'Meses' : `${mesesSel.length} mês(es)`} <ChevronDown className="w-3.5 h-3.5" />
      </button>
      {aberto && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setAberto(false)} />
          <div className="absolute z-50 mt-2 w-56 bg-white rounded-2xl shadow-xl border border-slate-100 p-3 max-h-80 overflow-auto">
            {Object.keys(porAno).sort().map(ano => (
              <div key={ano} className="mb-2">
                <button onClick={() => toggleAno(ano)} className="w-full text-left text-[11px] font-black text-slate-700 uppercase tracking-widest py-1 hover:text-indigo-600">{ano}</button>
                <div className="grid grid-cols-3 gap-1 mt-1">
                  {porAno[ano].sort((a: any, b: any) => a.num.localeCompare(b.num)).map((m: any) => (
                    <button key={m.mes} onClick={() => toggle(m.mes)}
                      className={`text-[11px] font-bold py-1 rounded-lg relative ${mesesSel.includes(m.mes) ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-50 text-slate-400'}`}>
                      {m.num}{m.parcial && <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-400 rounded-full" />}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SeletorFiltro({ dim, valor, onChange }: any) {
  const [aberto, setAberto] = useState(false);
  const [opcoes, setOpcoes] = useState<string[]>([]);
  const [busca, setBusca] = useState('');
  useEffect(() => {
    if (aberto && opcoes.length === 0) {
      axios.get(`/api/v1/kpis/opcoes-filtro?dimensao=${dim.chave}`).then(r => setOpcoes(r.data.opcoes || [])).catch(() => {});
    }
  }, [aberto, dim.chave]);
  const filtradas = useMemo(() => opcoes.filter(o => o.toLowerCase().includes(busca.toLowerCase())).slice(0, 100), [opcoes, busca]);
  return (
    <div className="relative">
      <button onClick={() => setAberto(!aberto)} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-black ${valor ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
        {dim.label} <ChevronDown className="w-3.5 h-3.5" />
      </button>
      {aberto && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setAberto(false)} />
          <div className="absolute z-50 mt-2 w-64 bg-white rounded-2xl shadow-xl border border-slate-100 p-2 max-h-80 overflow-auto">
            <input value={busca} onChange={e => setBusca(e.target.value)} placeholder={`Buscar ${dim.label.toLowerCase()}...`}
              className="w-full text-xs px-3 py-2 rounded-lg bg-slate-50 border border-slate-100 mb-2 outline-none focus:border-indigo-300" />
            <button onClick={() => { onChange(''); setAberto(false); }} className="w-full text-left text-[11px] font-bold text-slate-400 px-3 py-1.5 hover:bg-slate-50 rounded-lg">— todos —</button>
            {filtradas.map(o => (
              <button key={o} onClick={() => { onChange(o); setAberto(false); }}
                className={`w-full text-left text-[11px] font-bold px-3 py-1.5 rounded-lg truncate ${valor === o ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-50'}`}>{o}</button>
            ))}
            {filtradas.length === 0 && <div className="text-[11px] text-slate-400 px-3 py-2">nenhuma opção</div>}
          </div>
        </>
      )}
    </div>
  );
}

function Explica({ titulo, texto }: any) {
  return (
    <span className="group relative inline-flex items-center ml-1 align-middle">
      <HelpCircle className="w-3.5 h-3.5 text-slate-300 hover:text-slate-500 cursor-help" />
      <span className="invisible group-hover:visible absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-3 bg-slate-900 text-white text-[11px] leading-relaxed rounded-xl shadow-xl font-medium">
        <span className="block font-black mb-1 text-indigo-300">{titulo}</span>{texto}
      </span>
    </span>
  );
}

function CardResumo({ titulo, valor, sub, icone, tom, ajuda }: any) {
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

function ExplicacaoCalculos() {
  const [aberto, setAberto] = useState(false);
  const metricas = [
    { n: 'WMAPE', f: '|Σprev − Σreal| ÷ Σreal', t: 'Erro percentual sobre os totais do recorte. Método agregado: soma tudo primeiro, depois a razão. É o erro de dimensionamento — quão perto o plano ficou do total vendido.' },
    { n: 'Acurácia', f: '1 − WMAPE', t: 'O complemento do erro. De cada 100 caixas vendidas, quantas o plano acertou no total. É a nota-base do desempenho.' },
    { n: 'BIAS', f: '(Σprev − Σreal) ÷ Σreal', t: 'A tendência sistemática. Positivo: prevê demais (infla estoque, trava capital). Negativo: prevê de menos (rupturas). Perto de zero é saudável.' },
    { n: 'FVA', f: 'WMAPE da IA − WMAPE do humano', t: 'Forecast Value Added. Mede se o ajuste humano melhorou a previsão pura da máquina (Nexus Bot). Positivo: o humano agregou valor. Negativo: a IA sozinha teria acertado mais.' },
  ];
  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 overflow-hidden">
      <button onClick={() => setAberto(!aberto)} className="w-full flex items-center justify-between px-6 py-4 hover:bg-slate-50/50">
        <span className="text-sm font-black text-slate-700 flex items-center gap-2"><HelpCircle className="w-4 h-4 text-indigo-600" /> Como os indicadores são calculados</span>
        {aberto ? <ChevronDown className="w-5 h-5 text-slate-400" /> : <ChevronRight className="w-5 h-5 text-slate-400" />}
      </button>
      {aberto && (
        <div className="px-6 pb-6 grid grid-cols-1 md:grid-cols-2 gap-3">
          {metricas.map(m => (
            <div key={m.n} className="bg-slate-50 rounded-2xl p-4 border border-slate-100">
              <div className="flex items-baseline gap-2 mb-1 flex-wrap">
                <span className="font-black text-slate-800">{m.n}</span>
                <code className="text-[11px] font-mono text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">{m.f}</code>
              </div>
              <p className="text-xs text-slate-500 font-medium leading-relaxed">{m.t}</p>
            </div>
          ))}
          <div className="bg-indigo-50 rounded-2xl p-4 border border-indigo-100 md:col-span-2">
            <p className="text-xs text-indigo-900 font-medium leading-relaxed">
              <span className="font-black">Método agregado:</span> os indicadores somam o previsto e o realizado do recorte antes de calcular. Mede o dimensionamento (acertou o tamanho do negócio?). O erro fino de posicionamento — em qual cliente e SKU o plano errou — aparece no ranking e nos drilldowns.
              <span className="block mt-1"><span className="font-black">Regra temporal:</span> cada mês é comparado com o plano congelado 2 meses antes (o mês 07 usa o ciclo 05). O mês corrente aparece como parcial até fechar.</span>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// DEMANDA FORA DO RADAR — vendas sem nenhum planejamento.
// A lista que força vendedores e gerentes a puxar essa demanda
// para dentro do S&OP.
// ============================================================
function SemPlano({ params, visao }: any) {
  const [agrupar, setAgrupar] = useState<'sku' | 'cliente'>('sku');
  const [dados, setDados] = useState<any>({ itens: [], total_sem_plano: 0, cobertura: 1 });
  const [carregando, setCarregando] = useState(false);
  const [aberto, setAberto] = useState(true);

  useEffect(() => {
    setCarregando(true);
    const p = new URLSearchParams(params);
    p.append('agrupar', agrupar);
    axios.get(`/api/v1/kpis/sem-plano?${p.toString()}`)
      .then(r => setDados(r.data))
      .catch(() => setDados({ itens: [], total_sem_plano: 0, cobertura: 1 }))
      .finally(() => setCarregando(false));
  }, [params, agrupar]);

  const pctFora = (1 - (dados.cobertura ?? 1)) * 100;

  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 mb-6 overflow-hidden">
      <button onClick={() => setAberto(!aberto)} className="w-full flex items-center justify-between px-6 py-4 hover:bg-slate-50/50">
        <span className="text-sm font-black text-slate-700 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-500" /> Demanda fora do radar
          <span className="text-[11px] font-black text-amber-700 bg-amber-50 px-2.5 py-1 rounded-lg">
            {fmtNum(dados.total_sem_plano, visao)} vendidos sem planejamento ({fmtPct(pctFora)})
          </span>
          <Explica titulo="Demanda fora do radar" texto="Vendas que ocorreram sem NENHUMA previsão no plano congelado. Se está vendendo, deveria estar sendo planejado — esta lista mostra onde puxar a demanda para dentro do S&OP." />
        </span>
        {aberto ? <ChevronDown className="w-5 h-5 text-slate-400" /> : <ChevronRight className="w-5 h-5 text-slate-400" />}
      </button>
      {aberto && (
        <div className="px-6 pb-6">
          <div className="flex items-center gap-1.5 mb-3">
            <button onClick={() => setAgrupar('sku')} className={`px-3 py-1.5 rounded-xl text-[11px] font-black ${agrupar === 'sku' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-500'}`}>Por produto</button>
            <button onClick={() => setAgrupar('cliente')} className={`px-3 py-1.5 rounded-xl text-[11px] font-black ${agrupar === 'cliente' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-500'}`}>Por cliente</button>
          </div>
          {carregando ? (
            <div className="text-slate-400 text-xs flex items-center gap-2 py-4"><Loader2 className="w-4 h-4 animate-spin" /> carregando...</div>
          ) : dados.itens.length === 0 ? (
            <div className="text-emerald-600 text-xs font-bold py-4">Tudo que vendeu estava no plano — cobertura total no recorte.</div>
          ) : (
            <div className="space-y-1 max-h-96 overflow-auto pr-1">
              {dados.itens.map((it: any, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs bg-slate-50 rounded-xl px-4 py-2.5 border border-slate-100">
                  <div className="min-w-0 flex-1">
                    <div className="font-black text-slate-700 truncate">
                      {agrupar === 'sku' ? `${it.nome} · ${it.descricao || ''}` : it.nome}
                    </div>
                    <div className="text-[10px] font-bold text-slate-400">
                      {agrupar === 'sku' ? `${it.categoria} · ${it.clientes} cliente(s)` : `${it.regional} · ${it.skus} SKU(s)`}
                    </div>
                  </div>
                  <span className="font-black text-amber-700 shrink-0 ml-3">{fmtNum(it.realizado, visao)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}