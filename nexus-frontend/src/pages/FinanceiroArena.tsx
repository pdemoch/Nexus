// FinanceiroArena.tsx — Dashboard PMR / CCC
// Destino: src/pages/FinanceiroArena.tsx
//
//   - Cards KPI (PMR Pagamento, PMR Vencimento, PMR Cond.Pag., Delta Atraso)
//   - Card de impacto: quanto vale 1 dia de PMR no capital de giro
//   - Grafico de EVOLUCAO MENSAL (barras = valor recebido, linhas = 3 PMRs)
//   - Grafico misto por Regional (barras = Valor Recebido E5, linhas = 3 PMRs)
//   - Tabela de clientes por Razao Social (CNPJ consolidado) — clicavel
//   - Painel de DETALHE de notas do cliente selecionado (nivel parcela)
//   - Filtros: data, MULTI-regional, MULTI-segmento, busca de cliente
//   - Exportar Excel

import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';
import {
  Download, RefreshCw, Loader2, TrendingDown, TrendingUp,
  Clock, DollarSign, Filter, AlertTriangle, ChevronDown, X, Search, ArrowUpDown,
} from 'lucide-react';

// ─── Helpers ─────────────────────────────────────────────────────────────────
const fmtRs = (v: number) => {
  if (v >= 1e9) return `R$ ${(v / 1e9).toFixed(2)} Bi`;
  if (v >= 1e6) return `R$ ${(v / 1e6).toFixed(2)} Mi`;
  if (v >= 1e3) return `R$ ${(v / 1e3).toFixed(0)} K`;
  return `R$ ${v.toFixed(0)}`;
};
const fmtRsFull = (v: number) =>
  `R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`;
const corDias = (d: number) =>
  d <= 30 ? '#059669' : d <= 60 ? '#d97706' : '#e11d48';
const corDelta = (d: number) =>
  d < 0 ? '#059669' : d <= 10 ? '#d97706' : '#e11d48';

const abrevReg = (r: string) => {
  const map: Record<string, string> = {
    'KEY ACCOUNT': 'KEY ACC',
    'KEY ACCOUNT FARMA': 'KA FARMA',
    'CASH & CARRY': 'C&C',
    'CANAL INDIRETO': 'INDIRETO',
    'CANAL DIRETO': 'DIRETO',
    'CANAL VERDE': 'VERDE',
    'NOVOS NEGOCIOS': 'NOV NEG',
    'SEM REGIONAL': 'S/REG',
  };
  return map[r] || r;
};

// ─── Multi-select dropdown (checkbox) ────────────────────────────────────────
function MultiSelect({ label, options, value, onChange }:
  { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const toggle = (opt: string) => {
    if (value.includes(opt)) onChange(value.filter(v => v !== opt));
    else onChange([...value, opt]);
  };

  const rotulo = value.length === 0
    ? `Todos · ${label}`
    : value.length === 1 ? value[0] : `${value.length} ${label}`;

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-xs font-bold border border-slate-200 rounded-lg px-2.5 py-1.5 text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-violet-300">
        <span className="max-w-[140px] truncate">{rotulo}</span>
        <ChevronDown className="w-3 h-3 text-slate-400" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 bg-white border border-slate-200 rounded-xl shadow-lg py-1 min-w-[190px] max-h-64 overflow-auto">
          {value.length > 0 && (
            <button onClick={() => onChange([])}
              className="w-full text-left px-3 py-1.5 text-[10px] font-black uppercase tracking-wide text-violet-600 hover:bg-violet-50">
              Limpar seleção
            </button>
          )}
          {options.length === 0 && (
            <div className="px-3 py-2 text-[11px] text-slate-400">Sem opções</div>
          )}
          {options.map(opt => (
            <label key={opt} className="flex items-center gap-2 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 cursor-pointer">
              <input type="checkbox" checked={value.includes(opt)}
                onChange={() => toggle(opt)}
                className="accent-violet-600 w-3.5 h-3.5" />
              <span className="truncate">{opt}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Meses / anos ─────────────────────────────────────────────────────────────
const MESES = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'));
const ANOS = ['2023', '2024', '2025', '2026'];
const mesAno2Date = (mes: string, ano: string, fim = false) => {
  if (!fim) return `${ano}-${mes}-01`;
  const d = new Date(Number(ano), Number(mes), 0);
  return `${ano}-${mes}-${String(d.getDate()).padStart(2, '0')}`;
};

export default function FinanceiroArena() {
  // ── Filtros ──
  const [mesFim, setMesFim]   = useState('07');
  const [anoFim, setAnoFim]   = useState('2026');
  const [mesIni, setMesIni]   = useState('01');
  const [anoIni, setAnoIni]   = useState('2026');
  const [segmentosSel, setSegmentosSel] = useState<string[]>([]);
  const [regionaisSel, setRegionaisSel] = useState<string[]>([]);
  const [toggleAtivo, setToggleAtivo]   = useState<'PMR' | 'PMP' | 'PME' | 'CCC'>('PMR');

  // ── Busca de cliente ──
  const [buscaCli, setBuscaCli] = useState('');
  const [sugestoes, setSugestoes] = useState<any[]>([]);
  const [showSug, setShowSug] = useState(false);
  const [clienteSel, setClienteSel] = useState<{ cgc: string; nome: string } | null>(null);

  // ── Dados ──
  const [global, setGlobal]       = useState<any>(null);
  const [regionais, setRegionais] = useState<any[]>([]);
  const [clientes, setClientes]   = useState<any[]>([]);
  const [evolucao, setEvolucao]   = useState<any[]>([]);
  const [filtros, setFiltros]     = useState<any>({ regionais: [], segmentos: [] });
  const [loading, setLoading]     = useState(false);
  const [baixando, setBaixando]   = useState(false);
  const [semDados, setSemDados]   = useState(false);

  // ── Detalhe do cliente ──
  const [notasCli, setNotasCli]   = useState<any>(null);
  const [loadingNotas, setLoadingNotas] = useState(false);
  const [sortClientes, setSortClientes] = useState<{ key: string; dir: 1 | -1 }>({ key: 'valor_total', dir: -1 });
  const [sortNotas, setSortNotas] = useState<{ key: string; dir: 1 | -1 }>({ key: 'emissao', dir: 1 });

  const dataIni = mesAno2Date(mesIni, anoIni);
  const dataFim = mesAno2Date(mesFim, anoFim, true);

  const paramsBase: any = {
    data_ini: dataIni,
    data_fim: dataFim,
    ...(segmentosSel.length ? { segmentos: segmentosSel.join(',') } : {}),
    ...(regionaisSel.length ? { regionais: regionaisSel.join(',') } : {}),
  };

  // ─── Fetch principal ───────────────────────────────────────────────────────
  const buscarDados = useCallback(async () => {
    setLoading(true);
    setSemDados(false);
    try {
      const [gRes, rRes, cRes, eRes] = await Promise.all([
        axios.get('/api/v1/financeiro/pmr/global-filtrado',   { params: paramsBase }),
        axios.get('/api/v1/financeiro/pmr/regional-filtrado', { params: paramsBase }),
        axios.get('/api/v1/financeiro/pmr/clientes-filtrado', { params: { ...paramsBase, limit: 100 } }),
        axios.get('/api/v1/financeiro/pmr/evolucao',          { params: paramsBase }),
      ]);
      setGlobal(gRes.data);
      setRegionais(rRes.data.regionais || []);
      setClientes(cRes.data.clientes || []);
      setEvolucao(eRes.data.meses || []);
      if (!gRes.data.notas_pagas) setSemDados(true);
    } catch {
      setSemDados(true);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataIni, dataFim, segmentosSel, regionaisSel]);

  const buscarFiltros = useCallback(async () => {
    try {
      const r = await axios.get('/api/v1/financeiro/pmr/filtros', {
        params: { data_ini: dataIni, data_fim: dataFim },
      });
      setFiltros(r.data || { regionais: [], segmentos: [] });
    } catch {}
  }, [dataIni, dataFim]);

  useEffect(() => { buscarFiltros(); }, [buscarFiltros]);
  useEffect(() => { if (toggleAtivo === 'PMR') buscarDados(); }, [buscarDados, toggleAtivo]);

  // ─── Autocomplete de cliente (debounced) ─────────────────────────────────────
  useEffect(() => {
    if (!buscaCli || buscaCli.length < 2) { setSugestoes([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await axios.get('/api/v1/financeiro/pmr/clientes-busca', {
          params: { data_ini: dataIni, data_fim: dataFim, q: buscaCli, limit: 15 },
        });
        setSugestoes(r.data.clientes || []);
        setShowSug(true);
      } catch {}
    }, 300);
    return () => clearTimeout(t);
  }, [buscaCli, dataIni, dataFim]);

  // ─── Detalhe de notas do cliente ─────────────────────────────────────────────
  const abrirCliente = useCallback(async (cgc: string, nome: string) => {
    setClienteSel({ cgc, nome });
    setLoadingNotas(true);
    setNotasCli(null);
    try {
      const r = await axios.get('/api/v1/financeiro/pmr/notas', {
        params: { data_ini: dataIni, data_fim: dataFim, cgc },
      });
      setNotasCli(r.data);
    } catch {
      setNotasCli({ notas: [] });
    } finally {
      setLoadingNotas(false);
    }
  }, [dataIni, dataFim]);

  const exportar = async () => {
    setBaixando(true);
    try {
      const r = await axios.get('/api/v1/financeiro/pmr/exportar', {
        params: paramsBase, responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `PMR_${dataIni}_${dataFim}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {}
    finally { setBaixando(false); }
  };

  // ─── Dados dos graficos ──────────────────────────────────────────────────────
  const dadosGrafico = regionais.map(r => ({
    regional: abrevReg(r.regional),
    valor_recebido: Math.round(r.valor_total),
    pmr_pag:  r.pmr_pagamento,
    pmr_vnc:  r.pmr_vencimento,
    pmr_cond: r.pmr_cond_pag,
  })).sort((a, b) => (b.pmr_pag ?? -Infinity) - (a.pmr_pag ?? -Infinity));
  const maxPMR = Math.max(...dadosGrafico.flatMap(d => [d.pmr_pag, d.pmr_vnc, d.pmr_cond]), 60);

  const dadosEvolucao = evolucao.map(m => ({
    mes: m.mes,
    valor: Math.round(m.valor_recebido ?? m.valor_total),
    pmr_pag:  m.pmr_pagamento,
    pmr_vnc:  m.pmr_vencimento,
    pmr_cond: m.pmr_cond_pag,
    maturacao: m.em_maturacao,
  }));
  const maxPMREvol = Math.max(...dadosEvolucao.flatMap(d => [d.pmr_pag, d.pmr_vnc, d.pmr_cond]), 60);
  const ordenar = (key: string, atual: { key: string; dir: 1 | -1 }, setAtual: (v: { key: string; dir: 1 | -1 }) => void) => {
    setAtual({ key, dir: atual.key === key ? (atual.dir * -1) as 1 | -1 : 1 });
  };
  const clientesOrdenados = [...clientes].sort((a, b) => {
    const av = a[sortClientes.key] ?? '';
    const bv = b[sortClientes.key] ?? '';
    return (typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv), 'pt-BR')) * sortClientes.dir;
  });
  const notasOrdenadas = [...(notasCli?.notas || [])].sort((a, b) => {
    const av = a[sortNotas.key] ?? '';
    const bv = b[sortNotas.key] ?? '';
    return (typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv), 'pt-BR')) * sortNotas.dir;
  });
  const SortHeader = ({ label, onSort, align = 'right' }: { label: string; onSort: () => void; align?: 'left' | 'right' }) => (
    <button onClick={onSort} className="inline-flex items-center gap-1 font-inherit" style={{ textAlign: align }}>
      {label}<ArrowUpDown className="w-2.5 h-2.5" />
    </button>
  );

  // ─── Tooltips ─────────────────────────────────────────────────────────────
  const TooltipCustom = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;
    return (
      <div style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 10, padding: '10px 14px' }}>
        <div style={{ color: '#94a3b8', fontSize: 11, fontWeight: 800, marginBottom: 6 }}>{label}</div>
        {payload.map((p: any) => (
          <div key={p.name} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, marginBottom: 2 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: p.color, display: 'inline-block' }} />
            <span style={{ color: '#94a3b8' }}>{p.name}:</span>
            <span style={{ color: '#f1f5f9', fontWeight: 700 }}>
              {/Valor|Recebido/.test(p.name) ? fmtRs(p.value) : `${p.value} dias`}
            </span>
          </div>
        ))}
      </div>
    );
  };

  const isEmBreve = (t: string) => ['PMP', 'PME', 'CCC'].includes(t);

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-6">

      {/* CABECALHO */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="text-lg font-black text-slate-800">
            Financeiro · CCC
            <span className="ml-2 text-[10px] font-black uppercase tracking-widest bg-violet-100 text-violet-600 px-2 py-0.5 rounded-full">
              Beta
            </span>
          </h1>
          <p className="text-[11px] text-slate-400 mt-0.5">
            Ciclo de Conversão de Caixa · Prazo Médio de Recebimento
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {(['PMR', 'PMP', 'PME', 'CCC'] as const).map(t => (
            <button key={t}
              onClick={() => setToggleAtivo(t)}
              disabled={isEmBreve(t)}
              title={isEmBreve(t) ? 'Em breve' : ''}
              className={`px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all relative
                ${toggleAtivo === t
                  ? 'bg-violet-600 text-white shadow'
                  : isEmBreve(t)
                    ? 'bg-slate-100 text-slate-300 cursor-not-allowed'
                    : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'}`}>
              {t}
              {isEmBreve(t) && (
                <span className="absolute -top-1.5 -right-1.5 text-[8px] bg-amber-400 text-amber-900 font-black px-1 rounded-full">
                  em breve
                </span>
              )}
            </button>
          ))}
          <button onClick={exportar} disabled={baixando || !global}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest disabled:opacity-50 transition-all">
            {baixando ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Excel
          </button>
          <button onClick={buscarDados} disabled={loading}
            className="p-2 bg-white border border-slate-200 rounded-xl text-slate-500 hover:bg-slate-50 transition-all">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* FILTROS */}
      <div className="bg-white rounded-2xl border border-slate-100 px-4 py-3 mb-5 flex flex-wrap items-center gap-3">
        <Filter className="w-4 h-4 text-slate-400 shrink-0" />

        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-black text-slate-400 uppercase tracking-wide">De</span>
          <select value={mesIni} onChange={e => setMesIni(e.target.value)}
            className="text-xs font-bold border border-slate-200 rounded-lg px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300">
            {MESES.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <select value={anoIni} onChange={e => setAnoIni(e.target.value)}
            className="text-xs font-bold border border-slate-200 rounded-lg px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300">
            {ANOS.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-black text-slate-400 uppercase tracking-wide">Até</span>
          <select value={mesFim} onChange={e => setMesFim(e.target.value)}
            className="text-xs font-bold border border-slate-200 rounded-lg px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300">
            {MESES.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <select value={anoFim} onChange={e => setAnoFim(e.target.value)}
            className="text-xs font-bold border border-slate-200 rounded-lg px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300">
            {ANOS.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>

        <div className="h-6 w-px bg-slate-200" />

        {/* MULTI Regional / Segmento */}
        <MultiSelect label="Regionais" options={filtros.regionais || []}
          value={regionaisSel} onChange={setRegionaisSel} />
        <MultiSelect label="Segmentos" options={filtros.segmentos || []}
          value={segmentosSel} onChange={setSegmentosSel} />

        {/* Busca de cliente */}
        <div className="relative">
          <div className="flex items-center gap-1.5 border border-slate-200 rounded-lg px-2 py-1.5 focus-within:ring-2 focus-within:ring-violet-300">
            <Search className="w-3.5 h-3.5 text-slate-400" />
            <input value={buscaCli}
              onChange={e => setBuscaCli(e.target.value)}
              onFocus={() => sugestoes.length && setShowSug(true)}
              placeholder="Buscar cliente…"
              className="text-xs font-bold text-slate-700 focus:outline-none w-40 bg-transparent" />
            {buscaCli && (
              <button onClick={() => { setBuscaCli(''); setSugestoes([]); }}>
                <X className="w-3 h-3 text-slate-400" />
              </button>
            )}
          </div>
          {showSug && sugestoes.length > 0 && (
            <div className="absolute z-50 mt-1 bg-white border border-slate-200 rounded-xl shadow-lg py-1 min-w-[240px] max-h-64 overflow-auto">
              {sugestoes.map(s => (
                <button key={s.cgc}
                  onClick={() => { abrirCliente(s.cgc, s.nome); setShowSug(false); setBuscaCli(s.nome); }}
                  className="w-full text-left px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-violet-50 truncate">
                  {s.nome} <span className="text-[10px] text-slate-400">· {s.cgc}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <button onClick={buscarDados} disabled={loading}
          className="ml-auto bg-violet-600 hover:bg-violet-500 text-white px-5 py-1.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all disabled:opacity-50">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Aplicar'}
        </button>
      </div>

      {toggleAtivo === 'PMR' && (
        <>
          {semDados && !loading && (
            <div className="flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl px-5 py-4 mb-5 text-sm font-bold text-amber-800">
              <AlertTriangle className="w-5 h-5 shrink-0" />
              Nenhum dado disponível para este período. Verifique se os parquets foram extraídos no Painel Admin.
            </div>
          )}

          {/* CARDS KPI */}
          {global && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
              {[
                { label: 'PMR Pagamento', val: global.pmr_pagamento, icon: Clock, desc: 'Emissão → pagamento real', cor: '#7c3aed' },
                { label: 'PMR Vencimento', val: global.pmr_vencimento, icon: Clock, desc: 'Emissão → vencimento real', cor: '#2563eb' },
                { label: 'PMR Cond. Pag.', val: global.pmr_cond_pag, icon: Clock, desc: 'Emissão → vencimento contratual', cor: '#0891b2' },
                { label: 'Delta Atraso', val: global.delta_atraso, icon: (global.delta_atraso ?? 0) > 0 ? TrendingUp : TrendingDown,
                  desc: 'PMR Pag. − PMR Cond. (+ = cliente paga tarde)', cor: corDelta(global.delta_atraso ?? 0) },
              ].map(({ label, val, icon: Icon, desc, cor }) => (
                <div key={label} className="bg-white rounded-2xl border border-slate-100 p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Icon className="w-3.5 h-3.5" style={{ color: cor }} />
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{label}</span>
                  </div>
                  <div className="text-3xl font-black mt-1" style={{ color: cor }}>
                    {val != null ? val.toFixed(2).replace('.', ',') : '—'}
                    <span className="text-sm font-bold text-slate-400 ml-1">dias</span>
                  </div>
                  <div className="text-[10px] text-slate-400 mt-1">{desc}</div>
                  {label === 'PMR Pagamento' && global.notas_pagas > 0 && (
                    <div className="text-[10px] text-slate-400 mt-1 font-bold">
                      {global.notas_pagas.toLocaleString('pt-BR')} NFs · {fmtRsFull(global.valor_total)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* CARD IMPACTO — valor de 1 dia de PMR no capital de giro */}
          {global && global.valor_por_dia > 0 && (
            <div className="bg-gradient-to-r from-emerald-50 to-white rounded-2xl border border-emerald-100 p-4 mb-5 flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <DollarSign className="w-5 h-5 text-emerald-600" />
                <span className="text-[10px] font-black uppercase tracking-widest text-emerald-700">
                  Impacto no Capital de Giro
                </span>
              </div>
              <div className="text-2xl font-black text-emerald-700">
                {fmtRsFull(global.valor_por_dia)}
                <span className="text-xs font-bold text-emerald-500 ml-1">por dia de PMR</span>
              </div>
              <div className="text-[11px] text-slate-500 max-w-md leading-snug">
                Cada dia reduzido no PMR libera <b>{fmtRs(global.valor_por_dia)}</b> presos no contas-a-receber
                (ganho único de caixa). Reduzir 5 dias ≈ <b>{fmtRs(global.valor_por_dia * 5)}</b>.
                <span className="block text-[10px] text-slate-400 mt-0.5">
                  = valor recebido ({fmtRs(global.valor_total)}) ÷ {global.periodo?.dias_periodo ?? '—'} dias do período · estimativa de ordem de grandeza.
                </span>
              </div>
            </div>
          )}

          {/* GRAFICO EVOLUCAO MENSAL */}
          <div className="bg-white rounded-2xl border border-slate-100 p-5 mb-4">
            <div className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">
              Evolução Mensal do PMR
            </div>
            <div className="text-[10px] text-slate-400 mb-4">
              Agrupado por mês de pagamento · Barras = valor recebido · Linhas = PMR em dias ·
              <span className="text-amber-500 font-bold"> os 2 últimos meses estão em maturação (viés de sobrevivência)</span>
            </div>
            {loading ? (
              <div className="flex items-center justify-center h-64 text-slate-300"><Loader2 className="w-6 h-6 animate-spin" /></div>
            ) : dadosEvolucao.length === 0 ? (
              <div className="flex items-center justify-center h-64 text-slate-300 text-xs font-bold">Sem dados</div>
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={dadosEvolucao} margin={{ top: 10, right: 20, left: 0, bottom: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis dataKey="mes" tick={{ fontSize: 10, fontWeight: 700, fill: '#64748b' }} />
                  <YAxis yAxisId="left" tickFormatter={fmtRs} tick={{ fontSize: 10, fill: '#64748b' }} width={68} />
                  <YAxis yAxisId="right" orientation="right" domain={[0, Math.ceil(maxPMREvol * 1.2)]}
                    tick={{ fontSize: 10, fill: '#64748b' }} unit=" d" width={42} />
                  <Tooltip content={<TooltipCustom />} />
                  <Legend wrapperStyle={{ fontSize: 10, fontWeight: 700 }} />
                  <Bar yAxisId="left" dataKey="valor" name="Valor Recebido" fill="#a7f3d0" radius={[4, 4, 0, 0]} maxBarSize={48} />
                  <Line yAxisId="right" type="monotone" dataKey="pmr_pag" name="PMR Pagamento"
                    stroke="#7c3aed" strokeWidth={2.5} dot={{ r: 4, fill: '#7c3aed' }} />
                  <Line yAxisId="right" type="monotone" dataKey="pmr_vnc" name="PMR Vencimento"
                    stroke="#2563eb" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="4 2" />
                  <Line yAxisId="right" type="monotone" dataKey="pmr_cond" name="PMR Cond.Pag."
                    stroke="#f59e0b" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="2 3" />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* GRAFICO REGIONAL + TABELA CLIENTES */}
          <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
            <div className="xl:col-span-3 bg-white rounded-2xl border border-slate-100 p-5">
              <div className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">
                Valor Recebido E5 e PMR por Regional
              </div>
              <div className="text-[10px] text-slate-400 mb-4">
                Barras = Valor Recebido E5 (eixo esq.) · Linhas = PMR em dias (eixo dir.)
              </div>
              {loading ? (
                <div className="flex items-center justify-center h-64 text-slate-300"><Loader2 className="w-6 h-6 animate-spin" /></div>
              ) : dadosGrafico.length === 0 ? (
                <div className="flex items-center justify-center h-64 text-slate-300 text-xs font-bold">Sem dados</div>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <ComposedChart data={dadosGrafico} margin={{ top: 10, right: 20, left: 0, bottom: 30 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="regional" tick={{ fontSize: 10, fontWeight: 700, fill: '#64748b' }}
                      angle={-30} textAnchor="end" interval={0} />
                    <YAxis yAxisId="left" tickFormatter={fmtRs} tick={{ fontSize: 10, fill: '#64748b' }} width={68} />
                    <YAxis yAxisId="right" orientation="right" domain={[0, Math.ceil(maxPMR * 1.2)]}
                      tick={{ fontSize: 10, fill: '#64748b' }} unit=" d" width={42} />
                    <Tooltip content={<TooltipCustom />} />
                    <Legend wrapperStyle={{ fontSize: 10, fontWeight: 700 }} />
                    <Bar yAxisId="left" dataKey="valor_recebido" name="Valor Recebido E5" fill="#c7d2fe" radius={[4, 4, 0, 0]} maxBarSize={48} />
                    <Line yAxisId="right" type="monotone" dataKey="pmr_pag" name="PMR Pagamento"
                      stroke="#7c3aed" strokeWidth={2.5} dot={{ r: 4, fill: '#7c3aed' }} />
                    <Line yAxisId="right" type="monotone" dataKey="pmr_vnc" name="PMR Vencimento"
                      stroke="#2563eb" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="4 2" />
                    <Line yAxisId="right" type="monotone" dataKey="pmr_cond" name="PMR Cond.Pag."
                      stroke="#f59e0b" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="2 3" />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* TABELA CLIENTES */}
            <div className="xl:col-span-2 bg-white rounded-2xl border border-slate-100 flex flex-col overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100 flex-shrink-0">
                <div className="text-xs font-black uppercase tracking-widest text-slate-400">Maiores Recebedores</div>
                <div className="text-[10px] text-slate-400">por Razão Social · clique para ver as notas</div>
              </div>
              <div className="overflow-auto flex-1" style={{ maxHeight: 340 }}>
                {loading ? (
                  <div className="flex items-center justify-center h-40 text-slate-300"><Loader2 className="w-5 h-5 animate-spin" /></div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr style={{ background: '#f8fafc', position: 'sticky', top: 0, zIndex: 1 }}>
                        {[['Razão Social','nome'], ['Segmento','segmento'], ['Valor Recebido E5','valor_total'], ['PMR Pag.','pmr_pagamento'], ['PMR Vnc.','pmr_vencimento'], ['PMR Cond.','pmr_cond_pag']].map(([h,key]) => (
                          <th key={h} style={{ padding: '7px 10px', textAlign: h === 'Razão Social' || h === 'Segmento' ? 'left' : 'right',
                            fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.04em',
                            color: '#94a3b8', borderBottom: '1px solid #f1f5f9', whiteSpace: 'nowrap' }}>
                            <SortHeader label={h} onSort={() => ordenar(key, sortClientes, setSortClientes)} align={h === 'Razão Social' || h === 'Segmento' ? 'left' : 'right'} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {clientesOrdenados.slice(0, 50).map((c: any, i: number) => (
                        <tr key={c.cgc}
                          onClick={() => abrirCliente(c.cgc, c.nome)}
                          style={{ background: clienteSel?.cgc === c.cgc ? '#f5f3ff' : i % 2 ? '#f9fafb' : '#fff',
                            borderBottom: '1px solid #f1f5f9', cursor: 'pointer' }}>
                          <td style={{ padding: '6px 10px', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap', fontWeight: 700, color: '#334155' }} title={c.nome}>{c.nome}</td>
                          <td style={{ padding: '6px 10px', fontSize: 10, color: '#94a3b8', whiteSpace: 'nowrap', maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.segmento}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 700, color: '#475569', whiteSpace: 'nowrap' }}>{fmtRs(c.valor_total)}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 900, color: corDias(c.pmr_pagamento) }}>{c.pmr_pagamento?.toFixed(0)}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', color: '#64748b' }}>{c.pmr_vencimento?.toFixed(0)}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', color: '#64748b' }}>{c.pmr_cond_pag?.toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              {clientes.length > 50 && (
                <div className="px-4 py-2 border-t border-slate-100 text-[10px] text-slate-400 font-bold flex-shrink-0">
                  Exibindo 50 de {clientes.length} clientes · Baixe o Excel para ver todos
                </div>
              )}
            </div>
          </div>

          {/* PAINEL DE DETALHE DO CLIENTE */}
          {clienteSel && (
            <div className="bg-white rounded-2xl border border-violet-100 mt-4 overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-black uppercase tracking-widest text-violet-600">
                    Notas de {clienteSel.nome}
                  </div>
                  <div className="text-[10px] text-slate-400">
                    CNPJ {clienteSel.cgc} · nível parcela (SE1) com NF (SF2) e pagamento (SE5)
                    {notasCli?.resumo && (
                      <> · <b>{notasCli.total}</b> parcelas · recebido {fmtRs(notasCli.resumo.valor_recebido)} ·
                      PMR Pag. <b>{notasCli.resumo.pmr_pagamento?.toFixed(1)}d</b></>
                    )}
                  </div>
                </div>
                <button onClick={() => { setClienteSel(null); setNotasCli(null); }}
                  className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100"><X className="w-4 h-4" /></button>
              </div>
              <div className="overflow-auto" style={{ maxHeight: 360 }}>
                {loadingNotas ? (
                  <div className="flex items-center justify-center h-32 text-slate-300"><Loader2 className="w-5 h-5 animate-spin" /></div>
                ) : !notasCli?.notas?.length ? (
                  <div className="flex items-center justify-center h-24 text-slate-300 text-xs font-bold">Sem notas no período</div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr style={{ background: '#f8fafc', position: 'sticky', top: 0, zIndex: 1 }}>
                        {[
                          ['NF', 'nf'], ['Parc.', 'parcela'], ['Emissão', 'emissao'],
                          ['Valor Título', 'valor_titulo'], ['Vencto Cond.', 'vencto_cond'],
                          ['Vencto Real', 'vencto_real'], ['Pagamento', 'data_pagamento'],
                          ['Recebido', 'valor_recebido'], ['PMR Pag.', 'dias_pagamento'], ['Delta', 'delta_atraso'],
                        ].map(([h, key]) => (
                          <th key={h} style={{ padding: '7px 10px', textAlign: ['NF', 'Parc.', 'Emissão', 'Vencto Cond.', 'Vencto Real', 'Pagamento'].includes(h) ? 'left' : 'right',
                            fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.04em',
                            color: '#94a3b8', borderBottom: '1px solid #f1f5f9', whiteSpace: 'nowrap' }}>
                            <SortHeader label={h} onSort={() => ordenar(key, sortNotas, setSortNotas)} align={['NF', 'Parc.', 'Emissão', 'Vencto Cond.', 'Vencto Real', 'Pagamento'].includes(h) ? 'left' : 'right'} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {notasOrdenadas.map((n: any, i: number) => (
                        <tr key={`${n.nf}-${n.parcela}-${i}`} style={{ background: i % 2 ? '#f9fafb' : '#fff', borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '6px 10px', fontWeight: 700, color: '#334155', whiteSpace: 'nowrap' }}>{n.nf}/{n.serie}</td>
                          <td style={{ padding: '6px 10px', color: '#64748b' }}>{n.parcela || '—'}</td>
                          <td style={{ padding: '6px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>{n.emissao}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 700, color: '#475569', whiteSpace: 'nowrap' }}>{fmtRs(n.valor_titulo)}</td>
                          <td style={{ padding: '6px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>{n.vencto_cond}</td>
                          <td style={{ padding: '6px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>{n.vencto_real}</td>
                          <td style={{ padding: '6px 10px', color: '#334155', fontWeight: 600, whiteSpace: 'nowrap' }}>{n.data_pagamento}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', color: '#475569', whiteSpace: 'nowrap' }}>{fmtRs(n.valor_recebido)}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 900, color: corDias(n.dias_pagamento) }}>{n.dias_pagamento}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 900, color: corDelta(n.delta_atraso) }}>
                            {n.delta_atraso > 0 ? '+' : ''}{n.delta_atraso}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* LEGENDA */}
          <div className="flex gap-4 mt-4 flex-wrap">
            {[
              { cor: '#059669', label: '≤ 30 dias — dentro do prazo' },
              { cor: '#d97706', label: '31–60 dias — atenção' },
              { cor: '#e11d48', label: '> 60 dias — crítico' },
            ].map(({ cor, label }) => (
              <div key={label} className="flex items-center gap-2 text-[10px] font-bold text-slate-500">
                <span style={{ width: 10, height: 10, borderRadius: 2, background: cor, display: 'inline-block' }} />
                {label}
              </div>
            ))}
            <span className="text-[10px] text-slate-400 ml-auto">
              Delta na tabela de notas = dias de pagamento − dias cond.pag. (+ = parcela paga em atraso)
            </span>
          </div>
        </>
      )}

      {toggleAtivo !== 'PMR' && (
        <div className="flex flex-col items-center justify-center h-64 bg-white rounded-2xl border border-slate-100 text-slate-400">
          <DollarSign className="w-10 h-10 mb-3 opacity-20" />
          <div className="text-sm font-black text-slate-600">{toggleAtivo} — Em desenvolvimento</div>
          <div className="text-xs text-slate-400 mt-1 max-w-xs text-center">
            {toggleAtivo === 'PMP' && 'Prazo Médio de Pagamento a Fornecedores. Requer SE2 (Contas a Pagar) + NF de Entrada.'}
            {toggleAtivo === 'PME' && 'Prazo Médio de Estoque. Requer snapshot de estoque (SB2) e CMV.'}
            {toggleAtivo === 'CCC' && 'CCC = PME + PMR − PMP. Disponível quando PMP e PME estiverem prontos.'}
          </div>
        </div>
      )}
    </div>
  );
}
