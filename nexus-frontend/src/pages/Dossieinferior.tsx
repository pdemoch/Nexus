import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  X, TrendingUp, TrendingDown, Minus, Bot, AlertTriangle, Loader2,
} from 'lucide-react';
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts';

/* =====================================================================
   DOSSIÊ — INLINE EXPANSÍVEL: abre logo abaixo da linha do SKU na tabela
   (como um accordion), empurrando as linhas seguintes para baixo. Vários
   podem ficar abertos ao mesmo tempo. Uma rolagem única na página.

   Cores com contraste real:
     vendido               = indigo forte, sólido, grosso
     faturado              = slate médio, sólido, fino
     final (ciclo ativo)   = verde forte, tracejado grosso
     IA (ciclo ativo)      = roxo médio, tracejado fino
     final (ciclo anterior)= âmbar, pontilhado fino (referência "antes")

   Props: prefixoApi, tipo ('sku'|'categoria'), id, titulo, onFechar
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

  const dadosGrafico = (d?.serie || []).map((x: any) => ({
    mes: mesCurto(x.mes),
    vendido: rs ? x.vendido_rs : x.vendido_cx,
    faturado: rs ? x.faturado_rs : x.faturado_cx,
    final: rs ? x.final_rs : x.final_cx,
    ia: rs ? x.ia_rs : x.ia_cx,
    finalAnt: rs ? x.final_ciclo_ant_rs : x.final_ciclo_ant_cx,
  }));

  const marcoHoje = d?.marco_hoje ? mesCurto(d.marco_hoje) : null;

  const tendIcon = (t: string) =>
    t === 'CRESCIMENTO' ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
    t === 'DECLINIO' ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
    <Minus className="w-4 h-4 text-slate-400" />;

  return (
    <div className="bg-slate-50 border-y border-slate-200 rounded-xl my-1 overflow-hidden" style={{ fontVariantNumeric: 'tabular-nums' }}>
      <div className="flex items-center justify-between px-5 py-3 bg-white border-b border-slate-100">
        <div className="min-w-0">
          <div className="text-[10px] font-black uppercase tracking-widest text-indigo-500">
            {tipo === 'categoria' ? 'Dossiê da categoria' : 'Dossiê do SKU'}
          </div>
          <div className="text-sm font-black text-slate-900 truncate">{titulo || id}</div>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5">
            <button onClick={() => setUnidade('cx')}
              className={`px-3 py-1 text-xs font-black rounded-md ${unidade === 'cx' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>Caixas</button>
            <button onClick={() => setUnidade('rs')}
              className={`px-3 py-1 text-xs font-black rounded-md ${unidade === 'rs' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>R$</button>
          </div>
          <button onClick={onFechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-200">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center text-slate-400 py-12">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Montando o dossiê…
        </div>
      ) : !d ? (
        <div className="flex items-center justify-center text-slate-400 py-12">Não foi possível carregar.</div>
      ) : (
        <div className="px-5 py-4">
          <div className="mb-2 flex items-center justify-between flex-wrap gap-2">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
              Realizado vs previsto · {rs ? 'faturamento' : 'volume'}
            </div>
            <div className="flex items-center gap-4 text-[10px] font-bold text-slate-500 flex-wrap">
              <span className="flex items-center gap-1.5"><span className="w-4 h-1 bg-indigo-600 rounded-full inline-block" /> vendido</span>
              <span className="flex items-center gap-1.5"><span className="w-4 h-1 bg-slate-400 rounded-full inline-block" /> faturado</span>
              <span className="flex items-center gap-1.5"><span className="w-4 h-0.5 bg-emerald-600 inline-block" /> final (ciclo atual)</span>
              <span className="flex items-center gap-1.5"><span className="w-4 h-0.5 bg-violet-500 inline-block" /> IA (ciclo atual)</span>
              <span className="flex items-center gap-1.5"><span className="w-4 h-0.5 bg-amber-500 inline-block" /> final (ciclo anterior)</span>
            </div>
          </div>
          <div style={{ height: 300 }} className="mb-6">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={dadosGrafico} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="mes" tick={{ fontSize: 10, fill: '#64748b' }} interval={1} />
                <YAxis tick={{ fontSize: 10, fill: '#64748b' }} width={rs ? 70 : 45}
                  tickFormatter={(v: any) => {
                    if (v == null || isNaN(v)) return '';
                    return rs ? `${Math.round(v / 1000)}k` : new Intl.NumberFormat('pt-BR').format(v);
                  }} />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #cbd5e1' }}
                  formatter={(v: any, name: any) => {
                    if (v == null || isNaN(v)) return ['—', name];
                    return [rs ? fmtRs(v) : `${fmtCx(v)} cx`, name];
                  }}
                />
                {marcoHoje && (
                  <ReferenceLine x={marcoHoje} stroke="#f59e0b" strokeDasharray="4 4"
                    label={{ value: 'hoje', fontSize: 9, fill: '#b45309', position: 'top' }} />
                )}
                <Line type="monotone" dataKey="vendido" name="Vendido" stroke="#4338ca" strokeWidth={3} dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="faturado" name="Faturado" stroke="#94a3b8" strokeWidth={2} dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="final" name="Final (ciclo atual)" stroke="#059669" strokeWidth={2.5} strokeDasharray="6 4" dot={{ r: 3, fill: '#059669' }} connectNulls={false} />
                <Line type="monotone" dataKey="ia" name="IA (ciclo atual)" stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="3 3" dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="finalAnt" name="Final (ciclo anterior)" stroke="#d97706" strokeWidth={1.5} strokeDasharray="2 3" dot={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {(d.insights || []).length > 0 && (
            <div className="mb-6 space-y-2">
              {d.insights.map((ins: string, i: number) => (
                <div key={i} className="flex items-start gap-2 text-[12px] font-medium text-slate-700 bg-white rounded-lg px-3 py-2 border border-slate-100">
                  <Bot className="w-4 h-4 shrink-0 mt-0.5 text-indigo-500" />
                  {ins}
                </div>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {d.comparacoes && (
              <div className="rounded-xl bg-white border border-slate-100 p-3">
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

            {d.fva ? (
              <div className="rounded-xl bg-white border border-slate-100 p-3">
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
              d.composicao && (
                <div className="rounded-xl bg-white border border-slate-100 p-3">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Top SKUs da categoria</div>
                  <div className="space-y-1.5">
                    {(d.composicao || []).slice(0, 6).map((s: any) => (
                      <div key={s.sku} className="flex items-center gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="text-[11px] font-bold text-slate-600 truncate">{s.descricao}</div>
                          <div className="h-1.5 bg-slate-100 rounded overflow-hidden mt-0.5">
                            <div className="h-full bg-indigo-500 rounded" style={{ width: `${(s.share || 0) * 100}%` }} />
                          </div>
                        </div>
                        <span className="text-[10px] font-black text-slate-500 shrink-0">{fmtPct(s.share)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}

            {d.plurianual && (
              <div className="rounded-xl bg-white border border-slate-100 p-3">
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
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold text-amber-700 bg-amber-50 rounded px-2 py-1">
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