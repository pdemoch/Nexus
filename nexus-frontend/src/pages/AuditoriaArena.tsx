import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import axios from 'axios';
import {
  Activity, TrendingUp, TrendingDown, Target, Bot, Sparkles,
  ArrowUpCircle, ArrowDownCircle, AlertTriangle, CheckCircle2,
  ChevronDown, ChevronRight, Loader2, Info, Send, Wallet
} from 'lucide-react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Cell
} from 'recharts';

const API = '/api/v1/kpis';

// ═══════════════════════════════════════════════════════════════════════════
// FORMATADORES
// ═══════════════════════════════════════════════════════════════════════════
const cx = (v: any) => `${Math.round(Number(v) || 0).toLocaleString('pt-BR')} cx`;
const rs = (v: any) => {
  const n = Number(v) || 0;
  if (Math.abs(n) >= 1_000_000) return `R$ ${(n / 1_000_000).toFixed(1).replace('.', ',')}M`;
  if (Math.abs(n) >= 1_000)     return `R$ ${(n / 1_000).toFixed(0)}k`;
  return `R$ ${n.toFixed(0)}`;
};
const rsFull = (v: any) =>
  (Number(v) || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 });
const pct = (v: any, casas = 1) =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(casas).replace('.', ',')}%`;
const pctSinal = (v: any) => {
  if (v == null) return '—';
  const n = Number(v) * 100;
  return `${n > 0 ? '+' : ''}${n.toFixed(1).replace('.', ',')}%`;
};

// ═══════════════════════════════════════════════════════════════════════════
// ESTILO POR CLASSE DE DIAGNÓSTICO
// ═══════════════════════════════════════════════════════════════════════════
const ESTILO: Record<string, any> = {
  'Superestimando': { cor: '#e11d48', bg: 'bg-rose-50',    txt: 'text-rose-700',    borda: 'border-rose-200',    Icone: ArrowUpCircle },
  'Subestimando':   { cor: '#2563eb', bg: 'bg-blue-50',    txt: 'text-blue-700',    borda: 'border-blue-200',    Icone: ArrowDownCircle },
  'Errático':       { cor: '#d97706', bg: 'bg-amber-50',   txt: 'text-amber-700',   borda: 'border-amber-200',   Icone: AlertTriangle },
  'Atenção':        { cor: '#7c3aed', bg: 'bg-violet-50',  txt: 'text-violet-700',  borda: 'border-violet-200',  Icone: Info },
  'Sob controle':   { cor: '#059669', bg: 'bg-emerald-50', txt: 'text-emerald-700', borda: 'border-emerald-200', Icone: CheckCircle2 },
  'Sem base':       { cor: '#64748b', bg: 'bg-slate-50',   txt: 'text-slate-500',   borda: 'border-slate-200',   Icone: Info },
};
const est = (c: string) => ESTILO[c] || ESTILO['Sem base'];

const corWmape = (v: any) => v == null ? '#94a3b8' : v <= 0.20 ? '#059669' : v <= 0.35 ? '#d97706' : '#e11d48';
const clsWmape = (v: any) => v == null ? 'text-slate-400' : v <= 0.20 ? 'text-emerald-600' : v <= 0.35 ? 'text-amber-600' : 'text-rose-600';

// ═══════════════════════════════════════════════════════════════════════════
// CARD DE KPI
// ═══════════════════════════════════════════════════════════════════════════
const Card = ({ titulo, valor, sub, cor = '#4f46e5', Icone, ajuda }: any) => {
  const [ver, setVer] = useState(false);
  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5 relative">
      <div className="flex items-start justify-between mb-3">
        <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{titulo}</span>
        <div className="flex items-center gap-1.5">
          <Icone className="w-4 h-4" style={{ color: cor }} />
          {ajuda && (
            <span className="relative" onMouseEnter={() => setVer(true)} onMouseLeave={() => setVer(false)}>
              <Info className="w-3.5 h-3.5 text-slate-300 cursor-help" />
              {ver && (
                <div className="absolute right-0 top-5 z-50 bg-slate-800 text-white text-[11px] rounded-xl p-3 w-64 shadow-xl leading-relaxed font-normal normal-case tracking-normal">
                  {ajuda}
                </div>
              )}
            </span>
          )}
        </div>
      </div>
      <div className="text-[26px] font-black leading-none tracking-tight" style={{ color: cor }}>{valor}</div>
      {sub && <div className="text-[11px] text-slate-400 font-medium mt-1.5">{sub}</div>}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// PAINEL DO AGENTE
// ═══════════════════════════════════════════════════════════════════════════
const Agente = ({ filtros }: any) => {
  const [msgs, setMsgs] = useState<any[]>([]);
  const [txt, setTxt] = useState('');
  const [carregando, setCarregando] = useState(false);
  const [sugestoes, setSugestoes] = useState<string[]>([]);
  const fim = useRef<HTMLDivElement>(null);

  useEffect(() => {
    axios.get(`${API}/agente/sugestoes`).then(r => setSugestoes(r.data.sugestoes || [])).catch(() => {});
  }, []);
  useEffect(() => { fim.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, carregando]);

  const perguntar = async (pergunta: string) => {
    if (!pergunta.trim() || carregando) return;
    setMsgs(m => [...m, { tipo: 'user', texto: pergunta }]);
    setTxt(''); setCarregando(true);
    try {
      const r = await axios.post(`${API}/agente`, { pergunta, ...filtros });
      setMsgs(m => [...m, { tipo: 'bot', texto: r.data.resposta, ctx: r.data.contexto }]);
    } catch (e: any) {
      setMsgs(m => [...m, { tipo: 'erro', texto: e?.response?.data?.detail || 'Falha ao consultar o agente.' }]);
    } finally { setCarregando(false); }
  };

  return (
    <div className="flex flex-col h-[560px]">
      <div className="flex items-center gap-2 pb-3 mb-3 border-b border-slate-100">
        <Sparkles className="w-4 h-4 text-indigo-500" />
        <span className="text-xs font-bold text-slate-600">Análise sobre os dados filtrados</span>
        <span className="ml-auto text-[10px] text-slate-400">
          Os números vêm dos endpoints; o agente só interpreta
        </span>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {msgs.length === 0 && (
          <div className="space-y-2">
            <p className="text-xs text-slate-400 mb-3">Comece por uma destas:</p>
            {sugestoes.map(s => (
              <button key={s} onClick={() => perguntar(s)}
                className="block w-full text-left text-[13px] text-slate-600 bg-slate-50 hover:bg-indigo-50 hover:text-indigo-700 rounded-xl px-4 py-2.5 transition-colors border border-slate-100">
                {s}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={m.tipo === 'user' ? 'flex justify-end' : ''}>
            <div className={`rounded-2xl px-4 py-3 text-[13px] leading-relaxed max-w-[85%] whitespace-pre-wrap
              ${m.tipo === 'user' ? 'bg-indigo-600 text-white'
                : m.tipo === 'erro' ? 'bg-rose-50 text-rose-700 border border-rose-200'
                : 'bg-slate-50 text-slate-700 border border-slate-100'}`}>
              {m.texto}
              {m.ctx && (
                <div className="mt-2 pt-2 border-t border-slate-200 text-[10px] text-slate-400">
                  {m.ctx.skus_analisados} SKUs · {m.ctx.periodo?.inicio} a {m.ctx.periodo?.fim}
                </div>
              )}
            </div>
          </div>
        ))}
        {carregando && (
          <div className="flex items-center gap-2 text-slate-400 text-xs px-4">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> analisando…
          </div>
        )}
        <div ref={fim} />
      </div>

      <div className="flex gap-2 pt-3 border-t border-slate-100 mt-3">
        <input value={txt} onChange={e => setTxt(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && perguntar(txt)}
          placeholder="Pergunte sobre os indicadores…"
          className="flex-1 border border-slate-200 rounded-xl px-4 py-2.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-indigo-100 focus:border-indigo-300" />
        <button onClick={() => perguntar(txt)} disabled={carregando || !txt.trim()}
          className="bg-indigo-600 text-white rounded-xl px-4 disabled:opacity-30 hover:bg-indigo-700 transition-colors">
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// TELA
// ═══════════════════════════════════════════════════════════════════════════
export default function AuditoriaArena() {
  const [opts, setOpts] = useState<any>({ bus: [], categorias: [], skus: [] });
  const [f, setF] = useState<any>({ inicio: '2023-01', fim: '', bu: '', categoria: '', sku: '' });
  const [resumo, setResumo] = useState<any>({});
  const [serie, setSerie] = useState<any[]>([]);
  const [diag, setDiag] = useState<any>({ itens: [], agregados: {} });
  const [nivel, setNivel] = useState<'sku' | 'categoria'>('sku');
  const [filtroClasse, setFiltroClasse] = useState<string>('');
  const [aba, setAba] = useState<'diagnostico' | 'evolucao' | 'agente'>('diagnostico');
  const [aberto, setAberto] = useState<string | null>(null);
  const [hist, setHist] = useState<any>(null);
  const [load, setLoad] = useState(false);

  useEffect(() => {
    axios.get(`${API}/filtros`).then(r => {
      setOpts(r.data);
      setF((p: any) => ({ ...p, fim: r.data.ultimo_mes_fechado }));
    }).catch(() => {});
  }, []);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => { if (v) p.append(k, String(v)); });
    return p.toString();
  }, [f]);

  const carregar = useCallback(async () => {
    if (!f.fim) return;
    setLoad(true);
    try {
      const [r, e, d] = await Promise.all([
        axios.get(`${API}/resumo?${qs}`),
        axios.get(`${API}/evolucao?${qs}`),
        axios.get(`${API}/diagnostico?${qs}&nivel=${nivel}`),
      ]);
      setResumo(r.data.totais || {});
      setSerie(e.data.serie || []);
      setDiag(d.data || { itens: [], agregados: {} });
    } catch { setResumo({}); setSerie([]); setDiag({ itens: [], agregados: {} }); }
    finally { setLoad(false); }
  }, [qs, nivel, f.fim]);

  useEffect(() => { carregar(); }, [carregar]);

  const abrir = async (chave: string) => {
    if (aberto === chave) { setAberto(null); return; }
    setAberto(chave); setHist(null);
    if (nivel !== 'sku') return;
    try {
      const r = await axios.get(`${API}/sku/${chave}/historico?inicio=${f.inicio}&fim=${f.fim}`);
      setHist(r.data);
    } catch { setHist(null); }
  };

  const ag = diag.agregados || {};
  const itens = filtroClasse ? diag.itens.filter((i: any) => i.classe === filtroClasse) : diag.itens;
  const temIA = serie.some(s => s.vol_ia != null);

  // top 12 por exposição, para o gráfico de barras
  const topBarras = useMemo(() =>
    diag.itens.slice(0, 12).map((i: any) => ({
      nome: nivel === 'sku' ? i.sku : i.categoria,
      desc: i.descricao || i.categoria,
      excesso: i.excesso_rs, falta: -i.falta_rs, classe: i.classe,
    })), [diag.itens, nivel]);

  return (
    <div className="min-h-screen bg-slate-50 p-6 lg:p-8">
      <div className="max-w-[1560px] mx-auto">

        {/* CABEÇALHO */}
        <div className="flex flex-wrap items-end justify-between gap-4 mb-5">
          <div>
            <h1 className="text-[22px] font-black text-slate-900 tracking-tight flex items-center gap-2">
              <Activity className="w-6 h-6 text-indigo-600" /> Acurácia do S&OP
            </h1>
            <p className="text-[13px] text-slate-500 font-medium mt-0.5">
              Onde o plano erra, em que direção, com que frequência e quanto custa
            </p>
          </div>
          {load && <Loader2 className="w-4 h-4 text-slate-300 animate-spin mb-2" />}
        </div>

        {/* FILTROS */}
        <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-5 flex flex-wrap gap-3 items-end">
          {[['inicio', 'De'], ['fim', 'Até']].map(([k, label]) => (
            <label key={k} className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{label}</span>
              <input type="month" value={f[k]} max={opts.ultimo_mes_fechado}
                min={opts.primeiro_mes}
                onChange={e => setF((p: any) => ({ ...p, [k]: e.target.value }))}
                className="border border-slate-200 rounded-xl px-3 py-1.5 text-[13px]" />
            </label>
          ))}
          {[['bu', 'BU', opts.bus], ['categoria', 'Categoria', opts.categorias]].map(([k, label, lista]: any) => (
            <label key={k} className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{label}</span>
              <select value={f[k]} onChange={e => setF((p: any) => ({ ...p, [k]: e.target.value, sku: '' }))}
                className="border border-slate-200 rounded-xl px-3 py-1.5 text-[13px] bg-white min-w-[140px]">
                <option value="">Todas</option>
                {(lista || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          ))}
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">SKU</span>
            <select value={f.sku} onChange={e => setF((p: any) => ({ ...p, sku: e.target.value }))}
              className="border border-slate-200 rounded-xl px-3 py-1.5 text-[13px] bg-white min-w-[240px]">
              <option value="">Todos</option>
              {(opts.skus || [])
                .filter((s: any) => !f.categoria || s.categoria === f.categoria)
                .map((s: any) => <option key={s.sku} value={s.sku}>{s.sku} — {s.descricao}</option>)}
            </select>
          </label>
          <div className="ml-auto flex items-center bg-slate-100 rounded-xl p-1">
            {(['sku', 'categoria'] as const).map(n => (
              <button key={n} onClick={() => { setNivel(n); setAberto(null); }}
                className={`px-3.5 py-1.5 rounded-lg text-[11px] font-black uppercase tracking-wider transition-all
                  ${nivel === n ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-400'}`}>
                {n === 'sku' ? 'SKU' : 'Categoria'}
              </button>
            ))}
          </div>
        </div>

        {/* CARDS */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
          <Card titulo="Erro do plano (WMAPE)" valor={pct(resumo.wmape)}
            sub={`acurácia ${pct(resumo.acuracia)} · ${resumo.meses || 0} meses · ${resumo.skus_avaliados || 0} SKUs`}
            cor={corWmape(resumo.wmape)} Icone={Target}
            ajuda="Erro absoluto ponderado por volume. Mede o tamanho do erro, não a direção." />
          <Card titulo="Viés do plano" valor={pctSinal(resumo.vies)}
            sub={(resumo.vies || 0) > 0.02 ? 'planejando acima do vendido'
               : (resumo.vies || 0) < -0.02 ? 'planejando abaixo do vendido' : 'equilibrado'}
            cor={Math.abs(resumo.vies || 0) < 0.05 ? '#059669' : (resumo.vies || 0) > 0 ? '#e11d48' : '#2563eb'}
            Icone={(resumo.vies || 0) > 0 ? TrendingUp : TrendingDown}
            ajuda="Direção do erro. Positivo = plano acima do vendido, gera estoque. Negativo = plano abaixo, gera ruptura." />
          <Card titulo="Exposição financeira" valor={rs(ag.exposicao_total_rs)}
            sub={`${rs(ag.excesso_total_rs)} em excesso · ${rs(ag.falta_total_rs)} em falta`}
            cor="#7c3aed" Icone={Wallet}
            ajuda="Valor do desvio entre plano e vendido, ao PMV do mês. Excesso é capital parado; falta é venda em risco. Nenhum dos dois é perda realizada." />
          <Card titulo="Tendência" valor={resumo.tendencia == null ? '—' : pctSinal(resumo.tendencia)}
            sub={resumo.tendencia == null ? '—'
               : resumo.tendencia < 0 ? `melhorou (${pct(resumo.wmape_antigo)} → ${pct(resumo.wmape_recente)})`
               : `piorou (${pct(resumo.wmape_antigo)} → ${pct(resumo.wmape_recente)})`}
            cor={(resumo.tendencia || 0) < 0 ? '#059669' : '#e11d48'}
            Icone={(resumo.tendencia || 0) < 0 ? TrendingDown : TrendingUp}
            ajuda="WMAPE da metade recente do período menos o da metade anterior. Negativo é melhora." />
        </div>

        {/* FAIXA DE CLASSES CLICÁVEL */}
        <div className="flex flex-wrap gap-2 mb-5">
          {[
            ['Superestimando', ag.superestimando],
            ['Subestimando',   ag.subestimando],
            ['Errático',       ag.erratico],
            ['Atenção',        ag.atencao],
            ['Sob controle',   ag.sob_controle],
          ].map(([classe, dados]: any) => {
            const e = est(classe); const ativo = filtroClasse === classe;
            return (
              <button key={classe} onClick={() => setFiltroClasse(ativo ? '' : classe)}
                className={`flex items-center gap-2.5 rounded-2xl border px-4 py-2.5 transition-all
                  ${ativo ? `${e.bg} ${e.borda} ring-2 ring-offset-1` : 'bg-white border-slate-100 hover:border-slate-200'}`}
                style={ativo ? { boxShadow: `0 0 0 2px ${e.cor}22` } : {}}>
                <e.Icone className="w-4 h-4" style={{ color: e.cor }} />
                <div className="text-left">
                  <div className="text-[11px] font-bold text-slate-600 leading-none">{classe}</div>
                  <div className="text-[10px] text-slate-400 mt-1">
                    {dados?.qtd || 0} {nivel === 'sku' ? 'SKUs' : 'categorias'} · {rs(dados?.erro_rs)}
                  </div>
                </div>
              </button>
            );
          })}
          {filtroClasse && (
            <button onClick={() => setFiltroClasse('')}
              className="text-[11px] text-slate-400 underline self-center ml-1">limpar filtro</button>
          )}
        </div>

        {/* ABAS */}
        <div className="bg-white rounded-2xl border border-slate-100 shadow-sm">
          <div className="flex border-b border-slate-100 px-5">
            {([['diagnostico', 'Diagnóstico e ação'], ['evolucao', 'Evolução'], ['agente', 'Agente']] as const).map(([id, label]) => (
              <button key={id} onClick={() => setAba(id)}
                className={`py-3.5 px-4 text-[13px] font-bold border-b-2 transition-all flex items-center gap-1.5
                  ${aba === id ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
                {id === 'agente' && <Bot className="w-3.5 h-3.5" />}{label}
              </button>
            ))}
          </div>

          <div className="p-5">

            {/* ─── DIAGNÓSTICO ─────────────────────────────────────────── */}
            {aba === 'diagnostico' && (
              <>
                {topBarras.length > 0 && (
                  <div className="mb-6">
                    <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-3">
                      Exposição por {nivel === 'sku' ? 'SKU' : 'categoria'} — acima do eixo é excesso, abaixo é falta
                    </p>
                    <ResponsiveContainer width="100%" height={190}>
                      <BarChart data={topBarras} margin={{ top: 4, right: 8, bottom: 4, left: 8 }} stackOffset="sign">
                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                        <XAxis dataKey="nome" tick={{ fontSize: 9, fill: '#94a3b8' }} interval={0} angle={-30} textAnchor="end" height={50} />
                        <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => rs(v)} />
                        <Tooltip formatter={(v: any, n: any) => [rsFull(Math.abs(v)), n === 'excesso' ? 'Excesso (estoque)' : 'Falta (ruptura)']}
                          labelFormatter={(l: any, p: any) => p?.[0]?.payload?.desc || l} />
                        <ReferenceLine y={0} stroke="#cbd5e1" />
                        <Bar dataKey="excesso" stackId="a" fill="#e11d48" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="falta"   stackId="a" fill="#2563eb" radius={[0, 0, 3, 3]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}

                <div className="overflow-x-auto">
                  <table className="w-full text-[13px] border-collapse">
                    <thead>
                      <tr className="border-b-2 border-slate-100">
                        {[nivel === 'sku' ? 'SKU' : 'Categoria', nivel === 'sku' ? 'Descrição' : '',
                          'Diagnóstico', 'Ação recomendada', 'Viés', 'Persist.', 'WMAPE',
                          'Vendido', 'Exposição', 'Tend.', ''].map((h, i) => (
                          <th key={i} className={`py-2.5 px-3 text-[10px] font-black uppercase tracking-wider text-slate-400
                            ${['Viés','Persist.','WMAPE','Vendido','Exposição','Tend.'].includes(h) ? 'text-right' : 'text-left'}`}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {itens.map((i: any, idx: number) => {
                        const chave = i.sku || i.categoria;
                        const e = est(i.classe);
                        return (
                          <React.Fragment key={chave}>
                            <tr onClick={() => abrir(chave)}
                              className={`border-b border-slate-50 cursor-pointer hover:bg-slate-50/70 transition-colors ${idx % 2 ? 'bg-slate-50/30' : ''}`}>
                              <td className="py-2.5 px-3 font-mono text-[11px] text-slate-500">{chave}</td>
                              {nivel === 'sku' && (
                                <td className="py-2.5 px-3 max-w-[210px] truncate font-medium" title={i.descricao}>{i.descricao}</td>
                              )}
                              <td className="py-2.5 px-3">
                                <span className={`inline-flex items-center gap-1.5 ${e.bg} ${e.txt} rounded-full px-2.5 py-1 text-[11px] font-bold border ${e.borda}`}>
                                  <e.Icone className="w-3 h-3" />{i.classe}
                                </span>
                              </td>
                              <td className="py-2.5 px-3 text-[12px] text-slate-600 font-medium">{i.acao}</td>
                              <td className="py-2.5 px-3 text-right font-bold tabular-nums"
                                style={{ color: Math.abs(i.vies) < 0.05 ? '#64748b' : i.vies > 0 ? '#e11d48' : '#2563eb' }}>
                                {pctSinal(i.vies)}
                              </td>
                              <td className="py-2.5 px-3 text-right tabular-nums text-slate-500">
                                {Math.round(i.persistencia * 100)}%
                              </td>
                              <td className={`py-2.5 px-3 text-right font-bold tabular-nums ${clsWmape(i.wmape)}`}>{pct(i.wmape)}</td>
                              <td className="py-2.5 px-3 text-right tabular-nums text-slate-500">{cx(i.vol_real)}</td>
                              <td className="py-2.5 px-3 text-right tabular-nums font-bold text-violet-700">{rs(i.erro_abs_rs)}</td>
                              <td className="py-2.5 px-3 text-right tabular-nums text-[11px]"
                                style={{ color: i.tendencia < 0 ? '#059669' : '#e11d48' }}>
                                {i.tendencia < 0 ? '↓' : '↑'} {pct(Math.abs(i.tendencia))}
                              </td>
                              <td className="py-2.5 px-2 text-slate-300">
                                {aberto === chave ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                              </td>
                            </tr>

                            {aberto === chave && (
                              <tr>
                                <td colSpan={11} className="bg-indigo-50/30 p-0">
                                  <div className="p-5">
                                    {nivel === 'sku' && hist?.serie ? (
                                      <>
                                        <div className="flex flex-wrap gap-6 mb-4 text-[12px]">
                                          <div><span className="text-slate-400">Vendido</span><div className="font-bold">{cx(i.vol_real)}</div></div>
                                          <div><span className="text-slate-400">Planejado</span><div className="font-bold">{cx(i.vol_previsto)}</div></div>
                                          <div><span className="text-slate-400">Excesso</span><div className="font-bold text-rose-600">{rsFull(i.excesso_rs)}</div></div>
                                          <div><span className="text-slate-400">Falta</span><div className="font-bold text-blue-600">{rsFull(i.falta_rs)}</div></div>
                                          {i.fva != null && (
                                            <div><span className="text-slate-400">FVA vs IA</span>
                                              <div className={`font-bold ${i.fva >= 0 ? 'text-emerald-600' : 'text-indigo-600'}`}>
                                                {pctSinal(i.fva)} {i.fva >= 0 ? '(humano melhor)' : '(IA melhor)'}
                                              </div>
                                            </div>
                                          )}
                                        </div>
                                        <ResponsiveContainer width="100%" height={190}>
                                          <LineChart data={hist.serie} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                                            <XAxis dataKey="mes" tick={{ fontSize: 9, fill: '#94a3b8' }} />
                                            <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} />
                                            <Tooltip formatter={(v: any) => cx(v)} />
                                            <Line dataKey="vol_real"   name="Vendido"   stroke="#0f172a" strokeWidth={2} dot={false} />
                                            <Line dataKey="vol_humano" name="Plano"     stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 3" dot={false} />
                                            <Line dataKey="vol_ia"     name="IA"        stroke="#10b981" strokeWidth={1.5} dot={{ r: 2 }} connectNulls />
                                          </LineChart>
                                        </ResponsiveContainer>
                                      </>
                                    ) : (
                                      <p className="text-[12px] text-slate-400">
                                        {nivel === 'sku' ? 'Carregando histórico…' : 'Alterne para o nível SKU para ver o detalhe mês a mês.'}
                                      </p>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                      {itens.length === 0 && !load && (
                        <tr><td colSpan={11} className="py-14 text-center text-slate-400 text-[13px]">
                          Nenhum item para os filtros selecionados.
                        </td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {/* ─── EVOLUÇÃO ────────────────────────────────────────────── */}
            {aba === 'evolucao' && (
              <>
                <div className="grid lg:grid-cols-2 gap-6">
                  <div>
                    <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-3">Volume: vendido vs planejado</p>
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={serie} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                        <XAxis dataKey="mes" tick={{ fontSize: 9, fill: '#94a3b8' }} />
                        <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} />
                        <Tooltip formatter={(v: any) => cx(v)} />
                        <Line dataKey="vol_real"   name="Vendido" stroke="#0f172a" strokeWidth={2.5} dot={false} />
                        <Line dataKey="vol_humano" name="Plano"   stroke="#f59e0b" strokeWidth={2} strokeDasharray="5 3" dot={false} />
                        {temIA && <Line dataKey="vol_ia" name="IA" stroke="#10b981" strokeWidth={2} dot={{ r: 2 }} connectNulls />}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  <div>
                    <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-3">Viés mensal — acima de zero é excesso</p>
                    <ResponsiveContainer width="100%" height={280}>
                      <BarChart data={serie} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                        <XAxis dataKey="mes" tick={{ fontSize: 9, fill: '#94a3b8' }} />
                        <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `${(v * 100).toFixed(0)}%`} />
                        <Tooltip formatter={(v: any) => pctSinal(v)} />
                        <ReferenceLine y={0} stroke="#94a3b8" />
                        <ReferenceLine y={0.1}  stroke="#e11d48" strokeDasharray="3 3" />
                        <ReferenceLine y={-0.1} stroke="#2563eb" strokeDasharray="3 3" />
                        <Bar dataKey="bias_humano" name="Viés" radius={[3, 3, 0, 0]}>
                          {serie.map((s, i) => (
                            <Cell key={i} fill={(s.bias_humano || 0) > 0 ? '#e11d48' : '#2563eb'} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <p className="text-[11px] text-slate-400 mt-4">
                  As linhas tracejadas marcam ±10%, o limite a partir do qual o viés passa a ser material para o plano.
                  Meses fechados apenas — o mês corrente não entra em nenhuma métrica.
                </p>
              </>
            )}

            {/* ─── AGENTE ──────────────────────────────────────────────── */}
            {aba === 'agente' && <Agente filtros={f} />}

          </div>
        </div>

        <div className="mt-4 text-[11px] text-slate-400 leading-relaxed">
          <strong>Viés</strong> é a direção média do erro · <strong>Persistência</strong> é em quantos meses o erro foi na mesma direção —
          acima de 70% indica padrão sistemático, e só aí faz sentido corrigir o nível do plano ·
          <strong> Exposição</strong> valoriza o desvio pelo PMV do mês: excesso é capital parado, falta é venda em risco ·
          Plano humano: arquivo histórico até mai/26 e <span className="font-mono">vol_final</span> do ciclo M+2 a partir de 04/26
        </div>

      </div>
    </div>
  );
}