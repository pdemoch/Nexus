import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import {
  TrendingUp, TrendingDown, Minus, Loader2, ArrowUpDown, ArrowUp, ArrowDown,
  Trophy, Bot, Users, HelpCircle, Download,
} from 'lucide-react';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Cell,
} from 'recharts';

const fmtCx  = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs  = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number | null) => n == null ? '—' : `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`;

type SortDir = 'asc' | 'desc';
type Metrica = 'cx' | 'rs';

function useSort<T extends Record<string, any>>(
  data: T[], defaultKey: string, defaultDir: SortDir = 'desc'
) {
  const [key, setKey]  = useState<string>(defaultKey);
  const [dir, setDir]  = useState<SortDir>(defaultDir);
  useEffect(() => { setKey(defaultKey); }, [defaultKey]);
  const sorted = useMemo(() => {
    const arr = [...data];
    arr.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return dir === 'asc' ? av - bv : bv - av;
    });
    return arr;
  }, [data, key, dir]);
  const toggle = (k: string) => {
    if (k === key) setDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setKey(k); setDir('desc'); }
  };
  return { sorted, sortKey: key, sortDir: dir, toggle };
}

function Th({ label, k, sortKey, sortDir, onClick, left = false }: any) {
  const active = sortKey === k;
  return (
    <button onClick={() => onClick(k)}
      className={`flex items-center gap-1 text-[10px] font-black uppercase tracking-widest hover:text-slate-600 ${active ? 'text-indigo-600' : 'text-slate-400'} ${left ? '' : 'ml-auto'}`}>
      {label}
      {active ? (sortDir === 'desc' ? <ArrowDown className="w-3 h-3"/> : <ArrowUp className="w-3 h-3"/>) : <ArrowUpDown className="w-3 h-3 opacity-40"/>}
    </button>
  );
}

function TendIcon({ t }: { t: string }) {
  if (t === 'CRESCIMENTO') return <TrendingUp className="w-3.5 h-3.5 text-emerald-600"/>;
  if (t === 'DECLINIO')    return <TrendingDown className="w-3.5 h-3.5 text-rose-600"/>;
  if (t === 'SEM_BASE' || t === 'SEM_DADO') return <HelpCircle className="w-3.5 h-3.5 text-slate-300"/>;
  return <Minus className="w-3.5 h-3.5 text-slate-400"/>;
}

function LabelTend({ t }: { t: string }) {
  const m: Record<string,string> = { CRESCIMENTO:'Cresce', DECLINIO:'Cai', ESTAVEL:'Estável', SEM_BASE:'Item novo', SEM_DADO:'Sem dado' };
  return <span className="font-bold">{m[t] || t}</span>;
}

function BadgeVenc({ v }: { v: string | null }) {
  if (!v) return <span className="text-slate-300">—</span>;
  const m: Record<string,{label:string;cls:string}> = {
    HUMANO: { label:'Humano', cls:'bg-indigo-50 text-indigo-700 border-indigo-200' },
    IA:     { label:'IA',     cls:'bg-violet-50 text-violet-700 border-violet-200' },
  };
  const cfg = m[v] || { label:v, cls:'bg-slate-100 text-slate-500 border-slate-200' };
  return <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${cfg.cls}`}>{cfg.label}</span>;
}

function ColunasAnos({ anos, campo, fmt }: { anos: any[]; campo: string; fmt:(n:number)=>string }) {
  return (
    <div className="flex items-center gap-3 text-[10px] font-bold text-slate-400">
      {(anos || []).slice(-3).map((a: any) => (
        <span key={a.ano}>{a.ano}: <span className="text-slate-600">{fmt(a[campo])}</span></span>
      ))}
    </div>
  );
}

export default function VisaoGeralMarketing({ prefixoApi }: { prefixoApi: string }) {
  const [data,       setData]       = useState<any>(null);
  const [loading,    setLoading]    = useState(true);
  const [nivel,      setNivel]      = useState<'categoria'|'sku'>('categoria');
  const [metricaVol, setMetricaVol] = useState<Metrica>('cx');

  useEffect(() => {
    setLoading(true);
    axios.get(`${prefixoApi}/resumo`).then(r => setData(r.data)).finally(() => setLoading(false));
  }, [prefixoApi]);

  // Dados reativos ao nível
  const volData = nivel === 'categoria' ? (data?.tendencia_volume_categoria || []) : (data?.tendencia_volume_sku || []);
  const pmvData = nivel === 'categoria' ? (data?.tendencia_pmv_categoria    || []) : (data?.tendencia_pmv_sku    || []);
  const assData = nivel === 'categoria' ? (data?.assertividade_categoria    || []) : (data?.assertividade_sku    || []);

  // Chaves dinâmicas conforme métrica
  const tendKey    = metricaVol === 'cx' ? 'tendencia_cx'    : 'tendencia_rs';
  const varKey     = metricaVol === 'cx' ? 'variacao_pct_cx' : 'variacao_pct_rs';
  const atualKey   = metricaVol === 'cx' ? 'vol_cx_atual'    : 'vol_rs_atual';
  const labelNivel = nivel === 'categoria' ? 'categoria' : 'descricao';

  const volSort = useSort(volData, varKey, 'desc');
  const pmvSort = useSort(pmvData, 'variacao_pct', 'desc');
  const assSort = useSort(assData, 'vendido_cx', 'desc');

  // Cards: itens com base válida, reativos a nivel + metrica
  const volValido  = volData.filter((c: any) => c[tendKey] !== 'SEM_BASE' && c[tendKey] !== 'SEM_DADO' && c[varKey] != null);
  const topCresc   = [...volValido].filter((c: any) => c[tendKey] === 'CRESCIMENTO').sort((a:any,b:any) => b[varKey]-a[varKey]).slice(0,3);
  const topQueda   = [...volValido].filter((c: any) => c[tendKey] === 'DECLINIO').sort((a:any,b:any) => a[varKey]-b[varKey]).slice(0,3);
  const semBase    = volData.filter((c: any) => c[tendKey] === 'SEM_BASE' || c[tendKey] === 'SEM_DADO').length;

  // Quem mais acerta — reativo ao nível
  const contagemVenc = assData.reduce((acc: Record<string,number>, c: any) => {
    if (c.vencedor) acc[c.vencedor] = (acc[c.vencedor] || 0) + 1;
    return acc;
  }, {} as Record<string,number>);
  const maiorVenc = (Object.entries(contagemVenc) as [string, number][])
    .sort((a, b) => b[1] - a[1])[0];

  // Scatter — reativo ao nível e à métrica
  const pmvDataNivel = nivel === 'categoria' ? (data?.tendencia_pmv_categoria||[]) : (data?.tendencia_pmv_sku||[]);
  const scatterData  = volValido.map((cv: any) => {
    const pv = pmvDataNivel.find((p: any) => nivel === 'categoria' ? p.categoria === cv.categoria : p.sku === cv.sku);
    const pmvVar = (pv && pv.tendencia !== 'SEM_BASE' && pv.variacao_pct != null) ? pv.variacao_pct * 100 : 0;
    return { label: cv[labelNivel] || cv.sku, volVar: (cv[varKey] || 0)*100, pmvVar, volAbs: cv[atualKey] };
  });

  // CSV download das 3 tabelas
  const downloadCSV = () => {
    const sep = ';';
    const rows: string[] = [];
    const mLabel = metricaVol === 'cx' ? 'Qtd. pedida (cx)' : 'Valor pedido (R$)';
    rows.push(`Visão Geral — Ciclo ${data?.ciclo_ativo || ''} — ${nivel} — ${mLabel}`);
    rows.push('');

    rows.push(['VOLUME', 'Tendência', 'Variação', `Atual (${metricaVol})`,
      ...(volSort.sorted[0]?.anos || []).map((a: any) => String(a.ano))].join(sep));
    volSort.sorted.forEach((r: any) => {
      const var_ = r[varKey] != null ? `${(r[varKey]*100).toFixed(1)}%` : 'SEM BASE';
      const anos = (r.anos||[]).map((a:any) => metricaVol==='cx' ? a.vol_cx : a.vol_rs);
      rows.push([r[labelNivel]||r.sku, r[tendKey]||'', var_, r[atualKey]||0, ...anos].join(sep));
    });
    rows.push('');

    rows.push(['PMV', 'Tendência', 'Variação', 'PMV Atual',
      ...(pmvSort.sorted[0]?.anos || []).map((a: any) => String(a.ano))].join(sep));
    pmvSort.sorted.forEach((r: any) => {
      const var_ = r.variacao_pct != null ? `${(r.variacao_pct*100).toFixed(1)}%` : 'SEM BASE';
      const anos = (r.anos||[]).map((a:any) => a.pmv);
      rows.push([r[labelNivel]||r.sku, r.tendencia||'', var_, r.pmv_atual||0, ...anos].join(sep));
    });
    rows.push('');

    rows.push(['ASSERTIVIDADE','Vendido (cx)','Humano (cx)','Ader. Humano','IA (cx)','Ader. IA','Vencedor'].join(sep));
    assData.forEach((r: any) => {
      rows.push([
        r[labelNivel]||r.sku, r.vendido_cx, r.humano_cx,
        r.aderencia_humano != null ? `${(r.aderencia_humano*100).toFixed(1)}%` : '—',
        r.ia_cx,
        r.aderencia_ia != null ? `${(r.aderencia_ia*100).toFixed(1)}%` : '—',
        r.vencedor||'—',
      ].join(sep));
    });

    const cicloSafe = (data?.ciclo_ativo || 'ciclo').replace('/','_');
    const blob = new Blob(['\uFEFF' + rows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `visao_geral_${cicloSafe}_${nivel}_${metricaVol}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        <Loader2 className="w-6 h-6 animate-spin mr-2"/> Montando a visão geral…
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-slate-50 px-6 py-5" style={{ fontVariantNumeric:'tabular-nums' }}>

      {/* TÍTULO + BOTÃO CSV */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-black text-slate-900">Visão geral do mercado</h2>
          <p className="text-xs font-medium text-slate-400">
            Ciclo {data?.ciclo_ativo} · assertividade sobre {(data?.meses_auditados||[]).join(', ')||'sem meses auditáveis ainda'}
            {semBase > 0 && <span className="ml-2">· {semBase} {nivel==='categoria'?'categoria(s)':'SKU(s)'} sem histórico suficiente (item novo)</span>}
          </p>
        </div>
        <button onClick={downloadCSV}
          className="flex items-center gap-2 px-3 py-1.5 text-xs font-black rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-100 transition-colors">
          <Download className="w-3.5 h-3.5"/> CSV
        </button>
      </div>

      {/* CARDS */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-emerald-600 mb-2">
            <TrendingUp className="w-3.5 h-3.5"/> Em crescimento
          </div>
          {topCresc.length === 0
            ? <div className="text-xs text-slate-400">Nenhum item com crescimento &gt;5% e base confiável.</div>
            : topCresc.map((c: any) => (
              <div key={c[labelNivel]||c.sku} className="flex items-center justify-between text-xs py-0.5">
                <span className="font-bold text-slate-700 truncate">{c[labelNivel]}</span>
                <span className="font-black text-emerald-600 shrink-0 ml-2">{fmtPct(c[varKey])}</span>
              </div>
            ))}
        </div>
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-rose-600 mb-2">
            <TrendingDown className="w-3.5 h-3.5"/> Em queda
          </div>
          {topQueda.length === 0
            ? <div className="text-xs text-slate-400">Nenhum item com queda &gt;5% e base confiável.</div>
            : topQueda.map((c: any) => (
              <div key={c[labelNivel]||c.sku} className="flex items-center justify-between text-xs py-0.5">
                <span className="font-bold text-slate-700 truncate">{c[labelNivel]}</span>
                <span className="font-black text-rose-600 shrink-0 ml-2">{fmtPct(c[varKey])}</span>
              </div>
            ))}
        </div>
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
            <Trophy className="w-3.5 h-3.5"/> Quem mais acerta
          </div>
          {maiorVenc ? (
            <div className="flex items-center gap-2">
              {maiorVenc[0]==='IA' ? <Bot className="w-5 h-5 text-violet-500"/> : <Users className="w-5 h-5 text-indigo-500"/>}
              <div>
                <div className="text-sm font-black text-slate-800">{maiorVenc[0]==='IA'?'IA':'Humano'}</div>
                <div className="text-[10px] font-bold text-slate-400">
                  {maiorVenc[1]} de {assData.length} {nivel==='categoria'?'categorias':'SKUs'}
                </div>
              </div>
            </div>
          ) : <div className="text-xs text-slate-400">Sem dados de assertividade ainda.</div>}
        </div>
        <div className="bg-white rounded-2xl border border-slate-100 p-4">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Portfólio no ciclo</div>
          <div className="text-2xl font-black text-slate-900">{(data?.tendencia_volume_sku||[]).length}</div>
          <div className="text-[10px] font-bold text-slate-400">
            {nivel==='categoria'
              ? `${(data?.tendencia_volume_sku||[]).length} SKUs em ${(data?.tendencia_volume_categoria||[]).length} categorias`
              : `${(data?.tendencia_volume_sku||[]).length} SKUs · vendo por SKU`}
          </div>
        </div>
      </div>

      {/* TOGGLE NÍVEL */}
      <div className="flex items-center justify-between mb-3">
        <div className="inline-flex rounded-lg border border-slate-200 p-0.5 bg-white">
          <button onClick={() => setNivel('categoria')}
            className={`px-3 py-1 text-xs font-black rounded-md ${nivel==='categoria'?'bg-slate-900 text-white':'text-slate-500'}`}>Categoria</button>
          <button onClick={() => setNivel('sku')}
            className={`px-3 py-1 text-xs font-black rounded-md ${nivel==='sku'?'bg-slate-900 text-white':'text-slate-500'}`}>SKU</button>
        </div>
      </div>

      {/* SCATTER — aparece para categoria e SKU */}
      {scatterData.length > 0 && (
        <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
            Crescimento de volume pedido × crescimento de PMV
          </div>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top:8, right:20, bottom:8, left:0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                <XAxis type="number" dataKey="volVar" name="Volume pedido" unit="%" tick={{ fontSize:10, fill:'#94a3b8' }}/>
                <YAxis type="number" dataKey="pmvVar" name="PMV" unit="%" tick={{ fontSize:10, fill:'#94a3b8' }}/>
                <ZAxis type="number" dataKey="volAbs" range={[40, 400]}/>
                <ReferenceLine x={0} stroke="#cbd5e1"/>
                <ReferenceLine y={0} stroke="#cbd5e1"/>
                <Tooltip content={({ active, payload }: any) => {
                  if (!active||!payload?.length) return null;
                  const p = payload[0].payload;
                  return (
                    <div style={{ fontSize:11, borderRadius:8, border:'1px solid #e2e8f0', background:'white', padding:8 }}>
                      <div className="font-black text-slate-700">{p.label}</div>
                      <div>Volume pedido: {p.volVar>=0?'+':''}{p.volVar.toFixed(1)}%</div>
                      <div>PMV: {p.pmvVar>=0?'+':''}{p.pmvVar.toFixed(1)}%</div>
                    </div>
                  );
                }}/>
                <Scatter data={scatterData}>
                  {scatterData.map((_: any, i: number) => {
                    const e = scatterData[i];
                    const fill = e.volVar>=0 && e.pmvVar>=0 ? '#10b981' : e.volVar<0 && e.pmvVar<0 ? '#ef4444' : '#6366f1';
                    return <Cell key={i} fill={fill} fillOpacity={0.7}/>;
                  })}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className="text-[10px] font-bold text-slate-400 mt-1">
            Tamanho da bolha = {metricaVol==='cx'?'qtd. pedida atual':'valor pedido atual'} · verde = crescendo em volume e preço
          </div>
        </div>
      )}

      {/* TABELA VOLUME */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
            Tendência de volume pedido (YTD, a partir do 1º ano com venda)
          </div>
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5">
            <button onClick={() => setMetricaVol('cx')}
              className={`px-2.5 py-0.5 text-[10px] font-black rounded-md ${metricaVol==='cx'?'bg-slate-900 text-white':'text-slate-500'}`}>Qtd. pedida</button>
            <button onClick={() => setMetricaVol('rs')}
              className={`px-2.5 py-0.5 text-[10px] font-black rounded-md ${metricaVol==='rs'?'bg-slate-900 text-white':'text-slate-500'}`}>Valor pedido</button>
          </div>
        </div>
        <div className="grid gap-2 px-1 py-2 border-b border-slate-100" style={{ gridTemplateColumns:'1.3fr 100px 110px 110px 1fr' }}>
          <Th label={nivel==='categoria'?'Categoria':'SKU'} k={labelNivel} sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle} left/>
          <Th label="Tendência" k={tendKey}  sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle}/>
          <Th label="Variação"  k={varKey}   sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle}/>
          <Th label={metricaVol==='cx'?'Atual (cx)':'Atual (R$)'} k={atualKey} sortKey={volSort.sortKey} sortDir={volSort.sortDir} onClick={volSort.toggle}/>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Últimos anos</div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {volSort.sorted.map((row: any) => (
            <div key={nivel==='categoria'?row.categoria:row.sku}
              className="grid gap-2 px-1 py-2 items-center border-b border-slate-50 hover:bg-slate-50/50 text-xs"
              style={{ gridTemplateColumns:'1.3fr 100px 110px 110px 1fr' }}>
              <div className="min-w-0">
                <div className="font-bold text-slate-700 truncate">{row[labelNivel]}</div>
                {nivel==='sku' && <div className="text-[10px] font-bold text-slate-300">{row.sku} · {row.categoria}</div>}
              </div>
              <div className="flex items-center gap-1"><TendIcon t={row[tendKey]}/><LabelTend t={row[tendKey]}/></div>
              <div className={`text-right font-black ${(row[varKey]||0)>0?'text-emerald-600':(row[varKey]||0)<0?'text-rose-500':'text-slate-400'}`}>
                {fmtPct(row[varKey])}
              </div>
              <div className="text-right font-bold text-slate-600">
                {metricaVol==='cx'?`${fmtCx(row.vol_cx_atual)} cx`:fmtRs(row.vol_rs_atual)}
              </div>
              <ColunasAnos anos={row.anos} campo={metricaVol==='cx'?'vol_cx':'vol_rs'} fmt={metricaVol==='cx'?fmtCx:fmtRs}/>
            </div>
          ))}
        </div>
      </div>

      {/* TABELA PMV */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
          Tendência de PMV (Σ valor pedido ÷ Σ volume pedido — não é média de preços)
        </div>
        <div className="grid gap-2 px-1 py-2 border-b border-slate-100" style={{ gridTemplateColumns:'1.3fr 100px 110px 110px 1fr' }}>
          <Th label={nivel==='categoria'?'Categoria':'SKU'} k={labelNivel} sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle} left/>
          <Th label="Tendência" k="tendencia"   sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle}/>
          <Th label="Variação"  k="variacao_pct" sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle}/>
          <Th label="PMV atual" k="pmv_atual"   sortKey={pmvSort.sortKey} sortDir={pmvSort.sortDir} onClick={pmvSort.toggle}/>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Últimos anos (PMV)</div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {pmvSort.sorted.map((row: any) => (
            <div key={nivel==='categoria'?row.categoria:row.sku}
              className="grid gap-2 px-1 py-2 items-center border-b border-slate-50 hover:bg-slate-50/50 text-xs"
              style={{ gridTemplateColumns:'1.3fr 100px 110px 110px 1fr' }}>
              <div className="min-w-0">
                <div className="font-bold text-slate-700 truncate">{row[labelNivel]}</div>
                {nivel==='sku' && <div className="text-[10px] font-bold text-slate-300">{row.sku} · {row.categoria}</div>}
              </div>
              <div className="flex items-center gap-1"><TendIcon t={row.tendencia}/><LabelTend t={row.tendencia}/></div>
              <div className={`text-right font-black ${(row.variacao_pct||0)>0?'text-emerald-600':(row.variacao_pct||0)<0?'text-rose-500':'text-slate-400'}`}>
                {fmtPct(row.variacao_pct)}
              </div>
              <div className="text-right font-bold text-slate-600">{fmtRs(row.pmv_atual)}</div>
              <ColunasAnos anos={row.anos} campo="pmv" fmt={fmtRs}/>
            </div>
          ))}
        </div>
      </div>

      {/* TABELA ASSERTIVIDADE */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
          Assertividade · Humano vs IA vs Realizado ({(data?.meses_auditados||[]).length} mês(es) fechado(s))
        </div>
        <div className="grid gap-2 px-1 py-2 border-b border-slate-100" style={{ gridTemplateColumns:'1.5fr 110px 150px 150px 110px' }}>
          <Th label={nivel==='categoria'?'Categoria':'SKU'} k={labelNivel} sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle} left/>
          <Th label="Realizado" k="vendido_cx"    sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle}/>
          <Th label="Humano"    k="humano_cx"     sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle}/>
          <Th label="IA"        k="ia_cx"         sortKey={assSort.sortKey} sortDir={assSort.sortDir} onClick={assSort.toggle}/>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 text-right">Vencedor</div>
        </div>
        <div className="max-h-[32rem] overflow-y-auto">
          {assSort.sorted.map((row: any) => (
            <div key={nivel==='categoria'?row.categoria:row.sku}
              className="grid gap-2 px-1 py-2.5 items-center border-b border-slate-50 hover:bg-slate-50/50 text-xs"
              style={{ gridTemplateColumns:'1.5fr 110px 150px 150px 110px' }}>
              <div className="min-w-0">
                <div className="font-bold text-slate-700 truncate">{row[labelNivel]}</div>
                {nivel==='sku' && <div className="text-[10px] font-bold text-slate-300">{row.sku} · {row.categoria}</div>}
              </div>
              <div className="text-right font-black text-slate-800">{fmtCx(row.vendido_cx)} cx</div>
              <div className="text-right">
                <div className={`font-bold ${row.vencedor==='HUMANO'?'text-indigo-600':'text-slate-500'}`}>{fmtCx(row.humano_cx)} cx</div>
                <div className="text-[10px] font-bold text-slate-400">{row.aderencia_humano!=null?fmtPct(row.aderencia_humano):'—'}</div>
              </div>
              <div className="text-right">
                <div className={`font-bold ${row.vencedor==='IA'?'text-violet-600':'text-slate-500'}`}>{fmtCx(row.ia_cx)} cx</div>
                <div className="text-[10px] font-bold text-slate-400">{row.aderencia_ia!=null?fmtPct(row.aderencia_ia):'—'}</div>
              </div>
              <div className="flex justify-end"><BadgeVenc v={row.vencedor}/></div>
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