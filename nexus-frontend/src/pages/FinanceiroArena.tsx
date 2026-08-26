// FinanceiroArena.tsx — Dashboard PMR / CCC
// Destino: src/pages/FinanceiroArena.tsx (ou src/components/)
//
// Layout replicando o PowerBI do usuario:
//   - Cards KPI (PMR Pagamento, PMR Vencimento, PMR Cond.Pag., Delta Atraso)
//   - Grafico misto por Regional (barras = Valor NF, linhas = 3 PMRs)
//   - Tabela de clientes por Razao Social (CNPJ consolidado)
//   - Filtros: data, regional, segmento
//   - Toggle: PMR | PMP | PME | CCC
//   - Exportar Excel

import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';
import {
  Download, RefreshCw, Loader2, TrendingDown, TrendingUp,
  Clock, DollarSign, Filter, AlertTriangle,
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

// Abbrevia nome regional para caber no eixo X
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

// ─── Meses disponíveis para seletor ──────────────────────────────────────────
const MESES = Array.from({ length: 12 }, (_, i) =>
  String(i + 1).padStart(2, '0')
);
const ANOS = ['2023', '2024', '2025', '2026'];
const mesAno2Date = (mes: string, ano: string, fim = false) => {
  if (!fim) return `${ano}-${mes}-01`;
  const d = new Date(Number(ano), Number(mes), 0); // último dia do mês
  return `${ano}-${mes}-${String(d.getDate()).padStart(2, '0')}`;
};

export default function FinanceiroArena() {
  // ── Estado de filtros ──
  const [mesFim, setMesFim]   = useState('07');
  const [anoFim, setAnoFim]   = useState('2026');
  const [mesIni, setMesIni]   = useState('01');
  const [anoIni, setAnoIni]   = useState('2026');
  const [segmento, setSegmento]   = useState('');
  const [regional, setRegional]   = useState('');
  const [toggleAtivo, setToggleAtivo] = useState<'PMR' | 'PMP' | 'PME' | 'CCC'>('PMR');

  // ── Dados ──
  const [global, setGlobal]     = useState<any>(null);
  const [regionais, setRegionais] = useState<any[]>([]);
  const [clientes, setClientes]   = useState<any[]>([]);
  const [filtros, setFiltros]     = useState<any>({ regionais: [], segmentos: [] });
  const [loading, setLoading]     = useState(false);
  const [baixando, setBaixando]   = useState(false);
  const [semDados, setSemDados]   = useState(false);

  const dataIni = mesAno2Date(mesIni, anoIni);
  const dataFim = mesAno2Date(mesFim, anoFim, true);

  const params = {
    data_ini: dataIni,
    data_fim: dataFim,
    ...(segmento ? { segmento } : {}),
    ...(regional ? { regional } : {}),
  };

  const buscarDados = useCallback(async () => {
    setLoading(true);
    setSemDados(false);
    try {
      const [gRes, rRes, cRes] = await Promise.all([
        axios.get('/api/v1/financeiro/pmr/global-filtrado', { params }),
        axios.get('/api/v1/financeiro/pmr/regional-filtrado', { params }),
        axios.get('/api/v1/financeiro/pmr/clientes-filtrado', { params: { ...params, limit: 100 } }),
      ]);
      // CORREÇÃO: rotas -filtrado envolvem o payload em { status, data: {...} }
      setGlobal(gRes.data);
      setRegionais(rRes.data?.regionais || []);
      setClientes(cRes.data?.clientes || []);
      if (!gRes.data?.notas_pagas) setSemDados(true);
    } catch {
      setSemDados(true);
    } finally {
      setLoading(false);
    }
  }, [dataIni, dataFim, segmento, regional]);

  const buscarFiltros = useCallback(async () => {
    try {
      const r = await axios.get('/api/v1/financeiro/pmr/filtros', {
        params: { data_ini: dataIni, data_fim: dataFim },
      });
      // CORREÇÃO: rota de filtros também usa envelope { status, data: {...} }
      setFiltros(r.data || { regionais: [], segmentos: [] });
    } catch {}
  }, [dataIni, dataFim]);

  useEffect(() => { buscarFiltros(); }, [buscarFiltros]);
  useEffect(() => { if (toggleAtivo === 'PMR') buscarDados(); }, [buscarDados, toggleAtivo]);

  const exportar = async () => {
    setBaixando(true);
    try {
      const r = await axios.get('/api/v1/financeiro/pmr/exportar', {
        params, responseType: 'blob',
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

  // ─── Dados do gráfico ────────────────────────────────────────────────────
  const dadosGrafico = regionais.map(r => ({
    regional: abrevReg(r.regional),
    valor_nf: Math.round(r.valor_total),
    pmr_pag:  r.pmr_pagamento,
    pmr_vnc:  r.pmr_vencimento,
    pmr_cond: r.pmr_cond_pag,
  }));

  const maxValor = Math.max(...dadosGrafico.map(d => d.valor_nf), 1);
  const maxPMR   = Math.max(...dadosGrafico.flatMap(d => [d.pmr_pag, d.pmr_vnc, d.pmr_cond]), 60);

  // ─── Tooltip customizado ─────────────────────────────────────────────────
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
              {p.name === 'Valor NF' ? fmtRs(p.value) : `${p.value} dias`}
            </span>
          </div>
        ))}
      </div>
    );
  };

  const isEmBreve = (t: string) => ['PMP', 'PME', 'CCC'].includes(t);

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-6">

      {/* CABEÇALHO */}
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
          {/* Toggle PMR / PMP / PME / CCC */}
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
      <div className="bg-white rounded-2xl border border-slate-100 px-4 py-3 mb-5 flex flex-wrap items-center gap-4">
        <Filter className="w-4 h-4 text-slate-400 shrink-0" />

        {/* Periodo De */}
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

        {/* Periodo Ate */}
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

        {/* Regional */}
        <select value={regional} onChange={e => setRegional(e.target.value)}
          className="text-xs font-bold border border-slate-200 rounded-lg px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300">
          <option value="">Todas as Regionais</option>
          {filtros.regionais.map((r: string) => <option key={r} value={r}>{r}</option>)}
        </select>

        {/* Segmento */}
        <select value={segmento} onChange={e => setSegmento(e.target.value)}
          className="text-xs font-bold border border-slate-200 rounded-lg px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-300">
          <option value="">Todos os Segmentos</option>
          {filtros.segmentos.map((s: string) => <option key={s} value={s}>{s}</option>)}
        </select>

        <button onClick={buscarDados} disabled={loading}
          className="ml-auto bg-violet-600 hover:bg-violet-500 text-white px-5 py-1.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all disabled:opacity-50">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Aplicar'}
        </button>
      </div>

      {toggleAtivo === 'PMR' && (
        <>
          {/* SEM DADOS */}
          {semDados && !loading && (
            <div className="flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl px-5 py-4 mb-5 text-sm font-bold text-amber-800">
              <AlertTriangle className="w-5 h-5 shrink-0" />
              Nenhum dado disponível para este período. Verifique se os parquets foram extraídos no Painel Admin.
            </div>
          )}

          {/* CARDS KPI */}
          {global && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
              {[
                { label: 'PMR Pagamento', val: global.pmr_pagamento, icon: Clock, desc: 'Emissão → pagamento real', cor: '#7c3aed' },
                { label: 'PMR Vencimento', val: global.pmr_vencimento, icon: Clock, desc: 'Emissão → vencimento real', cor: '#2563eb' },
                { label: 'PMR Cond. Pag.', val: global.pmr_cond_pag, icon: Clock, desc: 'Emissão → vencimento contratual', cor: '#0891b2' },
                { label: 'Delta Atraso', val: global.delta_atraso, icon: global.delta_atraso > 0 ? TrendingUp : TrendingDown,
                  desc: 'PMR Pag. − PMR Cond. (+ = cliente paga tarde)', cor: corDelta(global.delta_atraso) },
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

          {/* GRÁFICO + TABELA */}
          <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">

            {/* GRÁFICO por Regional */}
            <div className="xl:col-span-3 bg-white rounded-2xl border border-slate-100 p-5">
              <div className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">
                Valor NF e PMR por Regional
              </div>
              <div className="text-[10px] text-slate-400 mb-4">
                Barras = Valor NF (eixo esq.) · Linhas = PMR em dias (eixo dir.)
              </div>

              {loading ? (
                <div className="flex items-center justify-center h-64 text-slate-300">
                  <Loader2 className="w-6 h-6 animate-spin" />
                </div>
              ) : dadosGrafico.length === 0 ? (
                <div className="flex items-center justify-center h-64 text-slate-300 text-xs font-bold">
                  Sem dados
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <ComposedChart data={dadosGrafico} margin={{ top: 10, right: 20, left: 0, bottom: 30 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="regional" tick={{ fontSize: 10, fontWeight: 700, fill: '#64748b' }}
                      angle={-30} textAnchor="end" interval={0} />
                    <YAxis yAxisId="left" tickFormatter={fmtRs}
                      tick={{ fontSize: 10, fill: '#64748b' }} width={68} />
                    <YAxis yAxisId="right" orientation="right" domain={[0, Math.ceil(maxPMR * 1.2)]}
                      tick={{ fontSize: 10, fill: '#64748b' }} unit=" d" width={42} />
                    <Tooltip content={<TooltipCustom />} />
                    <Legend wrapperStyle={{ fontSize: 10, fontWeight: 700 }} />

                    <Bar yAxisId="left" dataKey="valor_nf" name="Valor NF"
                      fill="#c7d2fe" radius={[4, 4, 0, 0]} maxBarSize={48} />
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

            {/* TABELA por Razão Social */}
            <div className="xl:col-span-2 bg-white rounded-2xl border border-slate-100 flex flex-col overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100 flex-shrink-0">
                <div className="text-xs font-black uppercase tracking-widest text-slate-400">
                  Maiores Recebedores
                </div>
                <div className="text-[10px] text-slate-400">por Razão Social · CNPJ consolidado</div>
              </div>

              <div className="overflow-auto flex-1" style={{ maxHeight: 340 }}>
                {loading ? (
                  <div className="flex items-center justify-center h-40 text-slate-300">
                    <Loader2 className="w-5 h-5 animate-spin" />
                  </div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr style={{ background: '#f8fafc', position: 'sticky', top: 0, zIndex: 1 }}>
                        {['Razão Social', 'Valor NF', 'PMR Pag.', 'PMR Vnc.', 'PMR Cond.'].map(h => (
                          <th key={h} style={{ padding: '7px 10px', textAlign: h === 'Razão Social' ? 'left' : 'right',
                            fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.04em',
                            color: '#94a3b8', borderBottom: '1px solid #f1f5f9', whiteSpace: 'nowrap' }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {clientes.slice(0, 30).map((c: any, i: number) => (
                        <tr key={c.cgc} style={{ background: i % 2 ? '#f9fafb' : '#fff', borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '6px 10px', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap', fontWeight: 700, color: '#334155' }}
                            title={c.nome}>{c.nome}</td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 700, color: '#475569', whiteSpace: 'nowrap' }}>
                            {fmtRs(c.valor_total)}
                          </td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 900, color: corDias(c.pmr_pagamento) }}>
                            {c.pmr_pagamento?.toFixed(0)}
                          </td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', color: '#64748b' }}>
                            {c.pmr_vencimento?.toFixed(0)}
                          </td>
                          <td style={{ padding: '6px 10px', textAlign: 'right', color: '#64748b' }}>
                            {c.pmr_cond_pag?.toFixed(0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {clientes.length > 30 && (
                <div className="px-4 py-2 border-t border-slate-100 text-[10px] text-slate-400 font-bold flex-shrink-0">
                  Exibindo 30 de {clientes.length} clientes · Baixe o Excel para ver todos
                </div>
              )}
            </div>
          </div>

          {/* LEGENDA DE CORES */}
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
              PMR Pagamento colorido · PMR Pagamento = data baixa bancária − data emissão NF
            </span>
          </div>
        </>
      )}

      {/* PLACEHOLDERS FUTUROS */}
      {toggleAtivo !== 'PMR' && (
        <div className="flex flex-col items-center justify-center h-64 bg-white rounded-2xl border border-slate-100 text-slate-400">
          <DollarSign className="w-10 h-10 mb-3 opacity-20" />
          <div className="text-sm font-black text-slate-600">
            {toggleAtivo} — Em desenvolvimento
          </div>
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
