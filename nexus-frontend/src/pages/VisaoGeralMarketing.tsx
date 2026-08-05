import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import {
  TrendingUp, TrendingDown, Minus, Loader2, ArrowUpDown, ArrowUp, ArrowDown,
  Trophy, Bot, Users, History, HelpCircle,
} from 'lucide-react';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Cell,
} from 'recharts';

/* =====================================================================
   VISÃO GERAL — primeira tela do planejador. Categorias/SKUs em
   crescimento ou queda (volume pedido e PMV), e quem mais acerta
   (Humano/IA/Ano passado) nos últimos meses fechados. Tudo classificável.

   Vocabulário: NUNCA "faturamento". É "valor pedido" (vl_pedido, R$) e
   "volume pedido" (qt_pedido, caixas). Sem qtfatura/vlfatura nesta tela.
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number | null) => n == null ? '—' : `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`;

type SortDir = 'asc' | 'desc';
type Metrica = 'cx' | 'rs';

function useSort<T>(data: T[], defaultKey: string, defaultDir: SortDir = 'desc') {
  const [key, setKey] = useState(defaultKey);
  const [dir, setDir] = useState<SortDir>(defaultDir);
  // Ressincroniza quando o defaultKey muda de fora (ex.: toggle Qtd./Valor
  // troca de variacao_pct_cx para variacao_pct_rs) — sem isto a ordenação
  // ficaria "presa" na métrica antiga até o usuário clicar num cabeçalho.
  useEffect(() => { setKey(defaultKey); }, [defaultKey]);
  const sorted = useMemo(() => {
    const arr = [...data];
    arr.sort((a: any, b: any) => {
      const av = a[key]; const bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return dir === 'asc' ? av - bv : bv - av;
    });
    return arr;
  }, [data, key, dir]);
  const toggle = (k: string) => {
    if (k === key) setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setKey(k); setDir('desc'); }
  };
  return { sorted, sortKey: key, sortDir: dir, toggle };
}

function ThOrdenavel({ label, k, sortKey, sortDir, onClick, align = 'right' }: any) {
  const ativo = sortKey === k;
  return (
    <button onClick={() => onClick(k)}
      className={`flex items-center gap-1 text-[10px] font-black uppercase tracking-widest hover:text-slate-600 transition-colors ${ativo ? 'text-indigo-600' : 'text-slate-400'} ${align === 'right' ? 'ml-auto' : ''}`}>
      {label}
      {ativo ? (sortDir === 'desc' ? <ArrowDown className="w-3 h-3" /> : <ArrowUp className="w-3 h-3" />) : <ArrowUpDown className="w-3 h-3 opacity-40" />}
    </button>
  );
}

function TendIcon({ t }: { t: string }) {
  if (t === 'CRESCIMENTO') return <TrendingUp className="w-3.5 h-3.5 text-emerald-600" />;
  if (t === 'DECLINIO') return <TrendingDown className="w-3.5 h-3.5 text-rose-600" />;
  if (t === 'SEM_BASE' || t === 'SEM_DADO') return <HelpCircle className="w-3.5 h-3.5 text-slate-300" />;
  return <Minus className="w-3.5 h-3.5 text-slate-400" />;
}

function LabelTendencia({ t }: { t: string }) {
  const map: Record<string, string> = {
    CRESCIMENTO: 'Cresce', DECLINIO: 'Cai', ESTAVEL: 'Estável',
    SEM_BASE: 'Item novo', SEM_DADO: 'Sem dado',
  };
  return <span className="font-bold">{map[t] || t}</span>;
}

function BadgeVencedor({ v }: { v: string | null }) {
  if (!v) return <span className="text-slate-300">—</span>;
  const map: Record<string, { label: string; cls: string }> = {
    HUMANO: { label: 'Humano', cls: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
    IA: { label: 'IA', cls: 'bg-violet-50 text-violet-700 border-violet-200' },
    'ANO PASSADO': { label: 'Ano passado', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  };
  const cfg = map[v] || { label: v, cls: 'bg-slate-100 text-slate-500 border-slate-200' };
  return <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${cfg.cls}`}>{cfg.label}</span>;
}

/* Colunas de anos (dados absolutos, para o cético conferir a conta) */
function ColunasAnos({ anos, campo, fmt }: { anos: any[]; campo: string; fmt: (n: number) => string }) {
  const ultimos = (anos || []).slice(-3); // últimos 3 anos com dado
  return (
    <div className="flex items-center gap-3 text-[10px] font-bold text-slate-400">
      {ultimos.map((a: any) => (
        <span key={a.ano}>{a.ano}: <span className="text-slate-600">{fmt(a[campo])}</span></span>
      ))}
    </div>
  );
}

export default function VisaoGeralMarketing({ prefixoApi }: { prefixoApi: string }) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [nivel, setNivel] = useState<'categoria' | 'sku'>('categoria');
  const [metricaVol, setMetricaVol] = useState<Metrica>('cx'); // toggle qt_pedido / vl_pedido

  useEffect(() => {
    setLoading(true);
    axios.get(`${prefixoApi}/resumo`).then((r) => setD(r.data)).finally(() => setLoading(false));
  }, [prefixoApi]);

  const volData = nivel === 'categoria' ? (d?.tendencia_volume_categoria || []) : (d?.tendencia_volume_sku || []);
  const pmvData = nivel === 'categoria' ? (d?.tendencia_pmv_categoria || []) : (d?.tendencia_pmv_sku || []);
  const assData = nivel === 'categoria' ? (d?.assertividade_categoria || []) : (d?.assertividade_sku || []);

  // ordena pela métrica ativa (cx ou rs)
  const volKeyOrdenacao = metricaVol === 'cx' ? 'vol_cx_atual' : 'vol_rs_atual';
  const volSortKey = metricaVol === 'cx' ? 'variacao_pct_cx' : 'variacao_pct_rs';
  const volSort = useSort(volData, volSortKey, 'desc');
  const pmvSort = useSort(pmvData, 'variacao_pct', 'desc');
  const assSort = useSort(assData, 'vendido_cx', 'desc');

  // Cards de destaque — só considera itens com base válida (evita item novo no ranking)
  const catsVol = (d?.tendencia_volume_categoria || []).filter((c: any) => c.tendencia_cx !== 'SEM_BASE' && c.tendencia_cx !== 'SEM_DADO');
  const topCresc = [...catsVol].filter((c) => c.tendencia_cx === 'CRESCIMENTO').sort((a, b) => b.variacao_pct_cx - a.variacao_pct_cx).slice(0, 3);
  const topQueda = [...catsVol].filter((c) => c.tendencia_cx === 'DECLINIO').sort((a, b) => a.variacao_pct_cx - b.variacao_pct_cx).slice(0, 3);
  const catsAss = d?.assertividade_categoria || [];
  const contagemVencedor = catsAss.reduce((acc: any, c: any) => {
    if (c.vencedor) acc[c.vencedor] = (acc[c.vencedor] || 0) + 1;
    return acc;
  }, {});
  const maiorVencedor = Object.entries(contagemVencedor).sort((a: any, b: any) => b[1] - a[1])[0];

  const itensSemBase = (d?.tendencia_volume_categoria || []).filter((c: any) => c.tendencia_cx === 'SEM_BASE' || c.tendencia_cx === 'SEM_DADO').length;

  const scatterData = (d?.tendencia_volume_categoria || [])
    .filter((cv: any) => cv.tendencia !== 'SEM_BASE' && cv.tendencia !== 'SEM_DADO')
    .map((cv: any) => {
      const pv = (d?.tendencia_pmv_categoria || []).find((p: any) => p.categoria === cv.categoria);
      return { categoria: cv.categoria, volVar: (cv.variacao_pct || 0) * 100, pmvVar: (pv?.variacao_pct || 0) * 100, volAbs: cv.vol_rs_atual };
    });

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Montando a visão geral…</div>;
  }

  return (
    <div className="h-full overflow-y-auto bg-slate-50 px-6 py-5" style={{ fontVariantNumeric: 'tabular-nums' }}>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-black text-slate-900">Visão geral do mercado</h2>
          <p className="text-xs font-medium text-slate-400">
            Ciclo {d?.ciclo_ativo} · assertividade sobre {(d?.meses_auditados || []).join(', ') || 'sem meses auditáveis ainda'}
            {itensSemBase > 0 && <span className="ml-2 text-slate-400">· {itensSemBase} categoria(s) sem histórico suficiente (item novo)</span>}
          </p>
        </div>
      </div>

      {/* CARDS DE DESTAQUE */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-emerald-600 mb-2">
            <TrendingUp className="w-3.5 h-3.5" /> Em crescimento
          </div>
          {topCresc.length === 0 ? <div className="text-xs text-slate-400">Nenhuma categoria com crescimento &gt;5% e base confiável.</div> :
            topCresc.map((c: any) => (
              <div key={c.categoria} className="flex items-center justify-between text-xs py-0.5">
                <span className="font-bold text-slate-700 truncate">{c.categoria}</span>
                <span className="font-black text-emerald-600 shrink-0 ml-2">{fmtPct(c.variacao_pct_cx)}</span>
              </div>
            ))}
        </div>
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-rose-600 mb-2">
            <TrendingDown className="w-3.5 h-3.5" /> Em queda
          </div>
          {topQueda.length === 0 ? <div className="text-xs text-slate-400">Nenhuma categoria com queda &gt;5% e base confiável.</div> :
            topQueda.map((c: any) => (
              <div key={c.categoria} className="flex items-center justify-between text-xs py-0.5">
                <span className="font-bold text-slate-700 truncate">{c.categoria}</span>
                <span className="font-black text-rose-600 shrink-0 ml-2">{fmtPct(c.variacao_pct_cx)}</span>
              </div>
            ))}
        </div>
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
            <Trophy className="w-3.5 h-3.5" /> Quem mais acerta
          </div>
          {maiorVencedor ? (
            <div className="flex items-center gap-2">
              {maiorVencedor[0] === 'IA' ? <Bot className="w-5 h-5 text-violet-500" /> :
               maiorVencedor[0] === 'HUMANO' ? <Users className="w-5 h-5 text-indigo-500" /> :
               <History className="w-5 h-5 text-slate-400" />}
              <div>
                <div className="text-sm font-black text-slate-800">{maiorVencedor[0] === 'HUMANO' ? 'Humano' : maiorVencedor[0] === 'IA' ? 'IA' : 'Ano passado'}</div>
                <div className="text-[10px] font-bold text-slate-400">{maiorVencedor[1] as number} de {catsAss.length} categorias</div>
              </div>
            </div>
          ) : <div className="text-xs text-slate-400">Sem dados de assertividade ainda.</div>}
        </div>
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Portfólio no ciclo</div>
          <div className="text-2xl font-black text-slate-900">{(d?.tendencia_volume_sku || []).length}</div>
          <div className="text-[10px] font-bold text-slate-400">SKUs em {(d?.tendencia_volume_categoria || []).length} categorias</div>
        </div>
      </div>

      {/* TOGGLE DE NÍVEL */}
      <div className="flex items-center justify-between mb-3">
        <div className="inline-flex rounded-lg border border-slate-200 p-0.5 bg-white">
          <button onClick={() => setNivel('categoria')}
            className={`px-3 py-1 text-xs font-black rounded-md ${nivel === 'categoria' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>Categoria</button>
          <button onClick={() => setNivel('sku')}
            className={`px-3 py-1 text-xs font-black rounded-md ${nivel === 'sku' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>SKU</button>
        </div>
      </div>

      {/* GRÁFICO: crescimento de volume pedido x crescimento de PMV */}
      {nivel === 'categoria' && scatterData.length > 0 && (
        <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
            Crescimento de volume pedido × crescimento de PMV
          </div>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 20, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis type="number" dataKey="volVar" name="Volume pedido" unit="%" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis type="number" dataKey="pmvVar" name="PMV" unit="%" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <ZAxis type="number" dataKey="volAbs" range={[60, 400]} />
                <ReferenceLine x={0} stroke="#cbd5e1" />
                <ReferenceLine y={0} stroke="#cbd5e1" />
                <Tooltip
                  content={({ active, payload }: any) => {
                    if (!active || !payload?.length) return null;
                    const p = payload[0].payload;
                    return (
                      <div style={{ fontSize: 11, borderRadius: 8, border: '1px solid #e2e8f0', background: 'white', padding: 8 }}>
                        <div className="font-black text-slate-700">{p.categoria}</div>
                        <div>Volume pedido: {p.volVar >= 0 ? '+' : ''}{p.volVar.toFixed(1)}%</div>
                        <div>PMV: {p.pmvVar >= 0 ? '+' : ''}{p.pmvVar.toFixed(1)}%</div>
                      </div>
                    );
                  }}
                />
                <Scatter data={scatterData}>
                  {scatterData.map((entry: any, i: number) => (
                    <Cell key={i} fill={entry.volVar >= 0 && entry.pmvVar >= 0 ? '#10b981' : entry.volVar < 0 && entry.pmvVar < 0 ? '#ef4444' : '#6366f1'} fillOpacity={0.7} />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className="text-[10px] font-bold text-slate-400 mt-1">Tamanho da bolha = valor pedido atual · quadrante superior-direito = crescendo em volume e preço</div>
        </div>
      )}

      {/* TABELA — TENDÊNCIA DE VOLUME PEDIDO */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
            Tendência de volume pedido (mesmo período · YTD, a partir do 1º ano com venda)
          </div>
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5">
            <button onClick={() => setMetricaVol('cx')}
              className={`px-2.5 py-0.5 text-[10px] font-black rounded-md ${metricaVol === 'cx' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>Qtd. pedida</button>
            <button onClick={() => setMetricaVol('rs')}
              className={`px-2.5 py-0.5 text-[10px] font-black rounded-md ${metricaVol === 'rs' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>Valor pedido</button>
          </div>
        </div>
        <div className="grid gap-2 px-1 py-2 border-b border-slate-100" style={{ gridTemplateColumns: '1.3fr 100px 110px 110px 1fr' }}>
          <ThOrdenavel label={nivel === 'categoria' ? 'Categoria' : 'SKU'} k={nivel === 'categoria' ? 'categoria' : 'descricao'} sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle} align="left" />
          <ThOrdenavel label="Tendência" k={metricaVol === 'cx' ? 'tendencia_cx' : 'tendencia_rs'} sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle} />
          <ThOrdenavel label="Variação" k={metricaVol === 'cx' ? 'variacao_pct_cx' : 'variacao_pct_rs'} sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle} />
          <ThOrdenavel label={metricaVol === 'cx' ? 'Atual (cx)' : 'Atual (R$)'} k={volKeyOrdenacao} sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle} />
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Últimos anos (dado absoluto)</div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {volSort.sorted.map((row: any) => (
            <div key={nivel === 'categoria' ? row.categoria : row.sku} className="grid gap-2 px-1 py-2 items-center border-b border-slate-50 hover:bg-slate-50/50 text-xs"
              style={{ gridTemplateColumns: '1.3fr 100px 110px 110px 1fr' }}>
              <div className="min-w-0">
                <div className="font-bold text-slate-700 truncate">{nivel === 'categoria' ? row.categoria : row.descricao}</div>
                {nivel === 'sku' && <div className="text-[10px] font-bold text-slate-300">{row.sku} · {row.categoria}</div>}
              </div>
              <div className="flex items-center gap-1"><TendIcon t={metricaVol === 'cx' ? row.tendencia_cx : row.tendencia_rs} /><LabelTendencia t={metricaVol === 'cx' ? row.tendencia_cx : row.tendencia_rs} /></div>
              <div className={`text-right font-black ${(metricaVol === 'cx' ? row.variacao_pct_cx : row.variacao_pct_rs) > 0 ? 'text-emerald-600' : (metricaVol === 'cx' ? row.variacao_pct_cx : row.variacao_pct_rs) < 0 ? 'text-rose-500' : 'text-slate-400'}`}>{fmtPct(metricaVol === 'cx' ? row.variacao_pct_cx : row.variacao_pct_rs)}</div>
              <div className="text-right font-bold text-slate-600">{metricaVol === 'cx' ? `${fmtCx(row.vol_cx_atual)} cx` : fmtRs(row.vol_rs_atual)}</div>
              <ColunasAnos anos={row.anos} campo={metricaVol === 'cx' ? 'vol_cx' : 'vol_rs'} fmt={metricaVol === 'cx' ? fmtCx : fmtRs} />
            </div>
          ))}
        </div>
      </div>

      {/* TABELA — TENDÊNCIA DE PMV */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
          Tendência de PMV (agrupado: Σ valor pedido ÷ Σ volume pedido do período — não é média de preços)
        </div>
        <div className="grid gap-2 px-1 py-2 border-b border-slate-100" style={{ gridTemplateColumns: '1.3fr 100px 110px 110px 1fr' }}>
          <ThOrdenavel label={nivel === 'categoria' ? 'Categoria' : 'SKU'} k={nivel === 'categoria' ? 'categoria' : 'descricao'} sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle} align="left" />
          <ThOrdenavel label="Tendência" k="tendencia" sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle} />
          <ThOrdenavel label="Variação" k="variacao_pct" sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle} />
          <ThOrdenavel label="PMV atual" k="pmv_atual" sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle} />
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Últimos anos (PMV agrupado)</div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {pmvSort.sorted.map((row: any) => (
            <div key={nivel === 'categoria' ? row.categoria : row.sku} className="grid gap-2 px-1 py-2 items-center border-b border-slate-50 hover:bg-slate-50/50 text-xs"
              style={{ gridTemplateColumns: '1.3fr 100px 110px 110px 1fr' }}>
              <div className="min-w-0">
                <div className="font-bold text-slate-700 truncate">{nivel === 'categoria' ? row.categoria : row.descricao}</div>
                {nivel === 'sku' && <div className="text-[10px] font-bold text-slate-300">{row.sku} · {row.categoria}</div>}
              </div>
              <div className="flex items-center gap-1"><TendIcon t={row.tendencia} /><LabelTendencia t={row.tendencia} /></div>
              <div className={`text-right font-black ${row.variacao_pct > 0 ? 'text-emerald-600' : row.variacao_pct < 0 ? 'text-rose-500' : 'text-slate-400'}`}>{fmtPct(row.variacao_pct)}</div>
              <div className="text-right font-bold text-slate-600">{fmtRs(row.pmv_atual)}</div>
              <ColunasAnos anos={row.anos} campo="pmv" fmt={fmtRs} />
            </div>
          ))}
        </div>
      </div>

      {/* TABELA — ASSERTIVIDADE: Humano, IA, Ano passado vs Realizado */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
          Assertividade · Humano, IA e Ano passado vs Realizado ({(d?.meses_auditados || []).length} mês(es) fechado(s))
        </div>
        <div className="grid gap-2 px-1 py-2 border-b border-slate-100" style={{ gridTemplateColumns: '1.2fr 110px 110px 110px 110px 110px 100px' }}>
          <ThOrdenavel label={nivel === 'categoria' ? 'Categoria' : 'SKU'} k={nivel === 'categoria' ? 'categoria' : 'descricao'} sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle} align="left" />
          <ThOrdenavel label="Realizado" k="vendido_cx" sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle} />
          <ThOrdenavel label="Humano" k="humano_cx" sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle} />
          <ThOrdenavel label="IA" k="ia_cx" sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle} />
          <ThOrdenavel label="Ano passado" k="naive_cx" sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle} />
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 text-right">Aderência</div>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 text-right">Vencedor</div>
        </div>
        <div className="max-h-[32rem] overflow-y-auto">
          {assSort.sorted.map((row: any) => (
            <div key={nivel === 'categoria' ? row.categoria : row.sku} className="grid gap-2 px-1 py-2.5 items-center border-b border-slate-50 hover:bg-slate-50/50 text-xs"
              style={{ gridTemplateColumns: '1.2fr 110px 110px 110px 110px 110px 100px' }}>
              <div className="min-w-0">
                <div className="font-bold text-slate-700 truncate">{nivel === 'categoria' ? row.categoria : row.descricao}</div>
                {nivel === 'sku' && <div className="text-[10px] font-bold text-slate-300">{row.sku} · {row.categoria}</div>}
              </div>
              <div className="text-right font-black text-slate-800">{fmtCx(row.vendido_cx)} cx</div>
              <div className="text-right">
                <div className={`font-bold ${row.vencedor === 'HUMANO' ? 'text-indigo-600' : 'text-slate-500'}`}>{fmtCx(row.humano_cx)} cx</div>
                <div className="text-[10px] font-bold text-slate-400">{row.aderencia_humano != null ? fmtPct(row.aderencia_humano) : '—'}</div>
              </div>
              <div className="text-right">
                <div className={`font-bold ${row.vencedor === 'IA' ? 'text-violet-600' : 'text-slate-500'}`}>{fmtCx(row.ia_cx)} cx</div>
                <div className="text-[10px] font-bold text-slate-400">{row.aderencia_ia != null ? fmtPct(row.aderencia_ia) : '—'}</div>
              </div>
              <div className="text-right">
                <div className={`font-bold ${row.vencedor === 'ANO PASSADO' ? 'text-slate-700' : 'text-slate-400'}`}>{fmtCx(row.naive_cx)} cx</div>
                <div className="text-[10px] font-bold text-slate-400">{row.aderencia_naive != null ? fmtPct(row.aderencia_naive) : '—'}</div>
              </div>
              <div />
              <div className="flex justify-end"><BadgeVencedor v={row.vencedor} /></div>
            </div>
          ))}
          {assSort.sorted.length === 0 && (
            <div className="text-center text-slate-400 text-xs py-8">Ainda não há meses fechados auditáveis neste ciclo.</div>
          )}
        </div>
      </div>
    </div>
  );
}