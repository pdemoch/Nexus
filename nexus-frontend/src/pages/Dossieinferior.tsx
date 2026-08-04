import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  X, TrendingUp, TrendingDown, Minus, Bot, AlertTriangle, Loader2,
  ChevronUp, Maximize2, Minimize2,
} from 'lucide-react';
import {
  ComposedChart, Line, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Legend,
} from 'recharts';

/* =====================================================================
   DOSSIÊ INFERIOR — componente compartilhado pelas 5 telas de consenso.
   Abre embaixo (split-screen), largura total. Herói: o gráfico unificado
   que sobrepõe realizado (vendido/faturado) ao previsto (IA/Final), com a
   linha do "hoje" separando fato de aposta.

   Props:
     prefixoApi   : ex '/api/v1/dashboard' — de onde buscar /dossie
     tipo         : 'sku' | 'categoria'
     id           : o sku ou o nome da categoria
     titulo       : nome legível
     onFechar     : callback
   ===================================================================== */

const fmtCx = (n: number | null) => n == null ? '—' : new Intl.NumberFormat('pt-BR').format(Math.round(n));
const fmtRs = (n: number | null) => n == null ? '—' : new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n));
const fmtPct = (n: number | null) => n == null ? '—' : `${(n * 100).toFixed(1)}%`;
const mesCurto = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

type Unidade = 'cx' | 'rs';

export default function DossieInferior({ prefixoApi, tipo, id, titulo, onFechar }: any) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [unidade, setUnidade] = useState<Unidade>('cx');
  const [expandido, setExpandido] = useState(false);

  useEffect(() => {
    setLoading(true);
    const rota = tipo === 'categoria' ? 'dossie-categoria' : 'dossie';
    const param = tipo === 'categoria' ? `categoria=${encodeURIComponent(id)}` : `sku=${encodeURIComponent(id)}`;
    axios.get(`${prefixoApi}/${rota}?${param}`)
      .then((r) => setD(r.data))
      .catch(() => setD(null))
      .finally(() => setLoading(false));
  }, [prefixoApi, tipo, id]);

  const rs = unidade === 'rs';

  // Prepara dados do gráfico conforme unidade
  const dadosGrafico = (d?.serie || []).map((x: any) => ({
    mes: mesCurto(x.mes),
    mesIso: x.mes,
    vendido: rs ? x.vendido_rs : x.vendido_cx,
    faturado: rs ? x.faturado_rs : x.faturado_cx,
    ia: rs ? x.ia_rs : x.ia_cx,
    final: rs ? x.final_rs : x.final_cx,
    orcamento: rs ? x.orcamento_rs : null,
    anoAnt: rs ? x.ano_ant_rs : x.ano_ant_cx,
  }));

  const marcoHoje = d?.marco_hoje ? mesCurto(d.marco_hoje) : null;
  const alturaGrafico = expandido ? 420 : 280;

  const tendIcon = (t: string) =>
    t === 'CRESCIMENTO' ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
    t === 'DECLINIO' ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
    <Minus className="w-4 h-4 text-slate-400" />;

  return (
    <div className="border-t-2 border-slate-200 bg-white flex flex-col" style={{ fontVariantNumeric: 'tabular-nums', maxHeight: expandido ? '80vh' : '52vh' }}>
      {/* CABEÇALHO DO DOSSIÊ */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div className="min-w-0">
            <div className="text-[10px] font-black uppercase tracking-widest text-indigo-500">
              {tipo === 'categoria' ? 'Dossiê da Categoria' : 'Dossiê do SKU'}
            </div>
            <div className="text-sm font-black text-slate-900 truncate">{titulo || id}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5">
            <button onClick={() => setUnidade('cx')}
              className={`px-3 py-1 text-xs font-black rounded-md ${unidade === 'cx' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>Caixas</button>
            <button onClick={() => setUnidade('rs')}
              className={`px-3 py-1 text-xs font-black rounded-md ${unidade === 'rs' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>R$</button>
          </div>
          <button onClick={() => setExpandido((v) => !v)}
            className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100" title={expandido ? 'Recolher' : 'Expandir'}>
            {expandido ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
          <button onClick={onFechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-slate-400 py-16">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Montando o dossiê…
        </div>
      ) : !d ? (
        <div className="flex-1 flex items-center justify-center text-slate-400 py-16">
          Não foi possível carregar o dossiê.
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* HERÓI — GRÁFICO UNIFICADO */}
          <div className="mb-2 flex items-center justify-between">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
              Realizado vs Previsto · {rs ? 'faturamento' : 'volume'}
            </div>
            <div className="flex items-center gap-4 text-[10px] font-bold text-slate-400">
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-indigo-600 inline-block" /> vendido</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-slate-300 inline-block" /> faturado</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 border-t-2 border-dashed border-emerald-500 inline-block" /> final previsto</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 border-t-2 border-dashed border-violet-400 inline-block" /> IA</span>
            </div>
          </div>
          <div style={{ height: alturaGrafico }} className="mb-6">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={dadosGrafico} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="mes" tick={{ fontSize: 10, fill: '#94a3b8' }} interval={expandido ? 0 : 1} angle={expandido ? -45 : 0} textAnchor={expandido ? 'end' : 'middle'} height={expandido ? 50 : 30} />
                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} width={rs ? 70 : 45}
                  tickFormatter={(v: any) => {
                    if (v == null || isNaN(v)) return '';
                    return rs ? `${Math.round(v / 1000)}k` : new Intl.NumberFormat('pt-BR').format(v);
                  }} />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #e2e8f0' }}
                  formatter={(v: any, name: any) => {
                    if (v == null || isNaN(v)) return ['—', name] as [string, any];
                    return [rs ? fmtRs(v) : `${fmtCx(v)} cx`, name] as [string, any];
                  }}
                />
                {marcoHoje && (
                  <ReferenceLine x={marcoHoje} stroke="#f59e0b" strokeDasharray="4 4"
                    label={{ value: 'hoje', fontSize: 9, fill: '#f59e0b', position: 'top' }} />
                )}
                {/* Realizado (sólido) */}
                <Line type="monotone" dataKey="vendido" name="Vendido" stroke="#4f46e5" strokeWidth={2.5} dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="faturado" name="Faturado" stroke="#cbd5e1" strokeWidth={1.5} dot={false} connectNulls={false} />
                {/* Previsto (tracejado) */}
                <Line type="monotone" dataKey="final" name="Final previsto" stroke="#10b981" strokeWidth={2.5} strokeDasharray="5 4" dot={{ r: 2 }} connectNulls={false} />
                <Line type="monotone" dataKey="ia" name="IA" stroke="#a78bfa" strokeWidth={1.5} strokeDasharray="4 4" dot={false} connectNulls={false} />
                {/* Orçamento e ano anterior — sutis, referência */}
                {rs && <Line type="monotone" dataKey="orcamento" name="Orçamento" stroke="#f59e0b" strokeWidth={1} strokeDasharray="2 3" dot={false} connectNulls />}
                <Line type="monotone" dataKey="anoAnt" name="Ano anterior" stroke="#e2e8f0" strokeWidth={1} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* INSIGHTS EM TEXTO */}
          {(d.insights || []).length > 0 && (
            <div className="mb-6 space-y-2">
              {d.insights.map((ins: string, i: number) => (
                <div key={i} className="flex items-start gap-2 text-[12px] font-medium text-slate-600 bg-slate-50 rounded-lg px-3 py-2">
                  <Bot className="w-4 h-4 shrink-0 mt-0.5 text-indigo-500" />
                  {ins}
                </div>
              ))}
            </div>
          )}

          {/* TRÊS BLOCOS LADO A LADO */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* BLOCO A — COMPARAÇÕES DO MÊS */}
            {d.comparacoes && (
              <div className="rounded-xl border border-slate-100 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
                  Comparações · {d.comparacoes.mes_label || 'mês foco'}
                </div>
                <div className="space-y-2">
                  {[
                    { label: 'Plano final', cx: d.comparacoes.final_cx, rsv: d.comparacoes.final_rs, base: true },
                    { label: 'Orçamento', cx: null, rsv: d.comparacoes.orcamento_rs },
                    { label: 'Ciclo anterior', cx: d.comparacoes.ciclo_ant_cx, rsv: d.comparacoes.ciclo_ant_rs },
                    { label: 'Ano anterior', cx: d.comparacoes.ano_ant_cx, rsv: d.comparacoes.ano_ant_rs },
                  ].map((linha) => {
                    const val = rs ? linha.rsv : linha.cx;
                    // delta = quanto o plano final está acima/abaixo desta referência
                    const planoVal = rs ? d.comparacoes.final_rs : d.comparacoes.final_cx;
                    let delta: number | null = null;
                    if (!linha.base && val != null && val !== 0 && planoVal != null) {
                      const dcalc = (planoVal - val) / val;
                      if (isFinite(dcalc)) delta = dcalc;
                    }
                    return (
                      <div key={linha.label} className="flex items-center justify-between text-xs">
                        <span className={`${linha.base ? 'font-black text-slate-700' : 'font-medium text-slate-500'}`}>{linha.label}</span>
                        <div className="text-right">
                          <span className={`font-bold ${linha.base ? 'text-slate-900' : 'text-slate-600'}`}>
                            {val == null ? '—' : rs ? fmtRs(val) : `${fmtCx(val)} cx`}
                          </span>
                          {delta != null && (
                            <span className={`ml-2 text-[10px] font-bold ${delta >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                              {delta >= 0 ? '+' : ''}{fmtPct(delta)}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* BLOCO B — FVA (só SKU) */}
            {d.fva ? (
              <div className="rounded-xl border border-slate-100 p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Quem acertou mais</div>
                  {d.fva.baixo_volume && (
                    <span className="text-[9px] font-black uppercase tracking-wider text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">baixo volume</span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: 'Humano', val: d.fva.aderencia_humano, vol: d.fva.humano_cx, key: 'HUMANO' },
                    { label: 'IA', val: d.fva.aderencia_ia, vol: d.fva.ia_cx, key: 'IA' },
                    { label: 'Ano passado', val: d.fva.aderencia_naive, vol: d.fva.naive_cx, key: 'ANO PASSADO' },
                  ].map((x) => {
                    const venceu = d.fva.vencedor === x.key;
                    return (
                      <div key={x.key} className={`rounded-lg px-2 py-2 border ${venceu ? 'border-emerald-300 bg-emerald-50' : 'border-slate-100 bg-slate-50'}`}>
                        <div className="text-[9px] font-bold text-slate-400">{x.label}</div>
                        <div className={`text-base font-black ${venceu ? 'text-emerald-700' : 'text-slate-500'}`}>{x.val != null ? fmtPct(x.val) : '—'}</div>
                        <div className="text-[9px] font-bold text-slate-400">previu {fmtCx(x.vol)}</div>
                      </div>
                    );
                  })}
                </div>
                {d.fva.insight_fva && (
                  <div className="mt-2 text-[11px] font-medium text-slate-600">{d.fva.insight_fva}</div>
                )}
              </div>
            ) : (
              /* BLOCO B alternativo (categoria) — composição/mix */
              d.composicao && (
                <div className="rounded-xl border border-slate-100 p-3">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Top SKUs da categoria</div>
                  <div className="space-y-1.5">
                    {(d.composicao || []).slice(0, 6).map((s: any) => {
                      const pct = s.share || 0;
                      return (
                        <div key={s.sku} className="flex items-center gap-2">
                          <div className="flex-1 min-w-0">
                            <div className="text-[11px] font-bold text-slate-600 truncate">{s.descricao}</div>
                            <div className="h-1.5 bg-slate-100 rounded overflow-hidden mt-0.5">
                              <div className="h-full bg-indigo-500 rounded" style={{ width: `${pct * 100}%` }} />
                            </div>
                          </div>
                          <span className="text-[10px] font-black text-slate-500 shrink-0">{fmtPct(pct)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )
            )}

            {/* BLOCO C — TRAJETÓRIA PLURIANUAL */}
            {d.plurianual && (
              <div className="rounded-xl border border-slate-100 p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                    Trajetória · {d.plurianual.periodo_label || 'anual'}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] font-bold">
                    <span className="flex items-center gap-0.5 text-slate-500">Vol {tendIcon(d.plurianual.tendencia_volume)}</span>
                    <span className="flex items-center gap-0.5 text-slate-500">PMV {tendIcon(d.plurianual.tendencia_pmv)}</span>
                  </div>
                </div>
                {d.plurianual.alerta_historico && (
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold text-amber-600 bg-amber-50 rounded px-2 py-1">
                    <AlertTriangle className="w-3 h-3 shrink-0" /> {d.plurianual.alerta_historico}
                  </div>
                )}
                <div className="space-y-0.5">
                  <div className="grid grid-cols-4 gap-1 px-1 text-[9px] font-black uppercase tracking-wider text-slate-300">
                    <div>Ano</div><div className="text-right">Vol</div><div className="text-right">PMV</div><div className="text-right">Cli</div>
                  </div>
                  {(d.plurianual.anos || []).slice(-4).map((a: any, i: number, arr: any[]) => {
                    const ehUlt = i >= arr.length - 2;
                    return (
                      <div key={a.ano} className={`grid grid-cols-4 gap-1 px-1 py-1 rounded text-[11px] ${ehUlt ? 'bg-slate-50 font-bold' : 'opacity-60'}`}>
                        <div className="text-slate-500">{a.ano}</div>
                        <div className="text-right text-slate-700">{fmtCx(a.vendido_cx)}</div>
                        <div className="text-right text-slate-700">{a.pmv?.toFixed(0)}</div>
                        <div className="text-right text-slate-400">{a.razoes_sociais}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}