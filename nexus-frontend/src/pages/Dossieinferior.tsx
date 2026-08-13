import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  X, TrendingUp, TrendingDown, Minus, AlertTriangle, Loader2,
} from 'lucide-react';
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts';

/* =====================================================================
   DOSSIÊ — INLINE EXPANSÍVEL, só em Caixas (qt_pedido).
   Props: prefixoApi, tipo ('sku'|'categoria'), id, titulo, onFechar
   ===================================================================== */

const fmtCx  = (n: number | null) => n == null ? '—' : new Intl.NumberFormat('pt-BR').format(Math.round(n));
const fmtPct = (n: number | null) => n == null ? '—' : `${(n * 100).toFixed(1)}%`;
const sinal  = (n: number | null, casas = 1) =>
  n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(casas).replace('.', ',')}%`;

const mesCurto = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

const tendIcon = (t: string) =>
  t === 'CRESCIMENTO' ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
  t === 'DECLINIO'    ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
  <Minus className="w-4 h-4 text-slate-400" />;

export default function DossieInferior({ prefixoApi, tipo, id, titulo, onFechar, paramsExtra, subtitulo }: any) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const rota = tipo === 'categoria' ? 'dossie-categoria' : 'dossie';
    const param = tipo === 'categoria'
      ? `categoria=${encodeURIComponent(id)}`
      : `sku=${encodeURIComponent(id)}`;
    const extra = paramsExtra
      ? '&' + Object.entries(paramsExtra)
          .filter(([, v]) => v != null && v !== '')
          .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
      : '';
    axios.get(`${prefixoApi}/${rota}?${param}${extra}`)
      .then(r => setD(r.data))
      .catch(() => setD(null))
      .finally(() => setLoading(false));
  }, [prefixoApi, tipo, id, JSON.stringify(paramsExtra || {})]);

  const dadosGrafico = (d?.serie || []).map((x: any) => ({
    mes:        mesCurto(x.mes),
    vendido:    x.vendido_cx,
    meta:       x.final_cx,
    ia:         x.ia_cx,
    humanoHist: x.humano_hist_cx,  // meta histórica do arquivo jan/23–mai/26
  }));

  const marcoHoje = d?.marco_hoje ? mesCurto(d.marco_hoje) : null;

  // ── Comparação agregada vs Ano anterior ──────────────────────────────────
  const comps = d?.comparacoes_por_mes || (d?.comparacoes ? [d.comparacoes] : []);
  const totalMeta   = comps.reduce((s: number, c: any) => s + (c.final_cx   || 0), 0);
  const totalAnoAnt = comps.reduce((s: number, c: any) => s + (c.ano_ant_cx || 0), 0);
  const deltaAnoAnt = totalAnoAnt > 0 ? (totalMeta - totalAnoAnt) / totalAnoAnt * 100 : null;

  return (
    <div
      className="bg-slate-50 border-y border-slate-200 rounded-xl my-1 overflow-hidden"
      style={{ fontVariantNumeric: 'tabular-nums' }}
    >
      {/* CABEÇALHO */}
      <div className="flex items-center justify-between px-5 py-3 bg-white border-b border-slate-100">
        <div className="min-w-0">
          <div className="text-[10px] font-black uppercase tracking-widest text-indigo-500">
            {tipo === 'categoria' ? 'Dossiê da categoria' : 'Dossiê do SKU'}
          </div>
          <div className="text-sm font-black text-slate-900 truncate">{titulo || id}</div>
          {subtitulo && <div className="text-[10px] font-bold text-slate-400 truncate">{subtitulo}</div>}
        </div>
        <button onClick={onFechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-200 shrink-0">
          <X className="w-4 h-4" />
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center text-slate-400 py-12">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Montando o dossiê…
        </div>
      ) : !d ? (
        <div className="flex items-center justify-center text-slate-400 py-12">
          Não foi possível carregar.
        </div>
      ) : (
        <div className="px-5 py-4">

          {/* LEGENDA + GRÁFICO */}
          <div className="mb-2 flex items-center justify-between flex-wrap gap-2">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
              Realizado vs previsto · caixas · últimos 36 meses
            </div>
            <div className="flex items-center gap-4 text-[10px] font-bold text-slate-500 flex-wrap">
              <span className="flex items-center gap-1.5">
                <span className="w-5 h-0.5 bg-indigo-600 inline-block rounded-full" style={{ height: 3 }} />
                vendido
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-5 inline-block" style={{ height: 2, background: 'repeating-linear-gradient(90deg,#059669 0 6px,transparent 6px 10px)' }} />
                Meta (S&OP)
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-5 inline-block" style={{ height: 2, background: 'repeating-linear-gradient(90deg,#0ea5e9 0 5px,transparent 5px 8px)' }} />
                Meta histórica
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-5 inline-block" style={{ height: 2, background: 'repeating-linear-gradient(90deg,#8b5cf6 0 4px,transparent 4px 7px)' }} />
                IA
              </span>
            </div>
          </div>

          <div style={{ height: 300 }} className="mb-6">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={dadosGrafico} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="mes" tick={{ fontSize: 10, fill: '#64748b' }} interval={2} />
                <YAxis tick={{ fontSize: 10, fill: '#64748b' }} width={50}
                  tickFormatter={(v: any) => v == null || isNaN(v) ? '' : new Intl.NumberFormat('pt-BR').format(v)} />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #cbd5e1' }}
                  formatter={(v: any, name: any) => {
                    if (v == null || isNaN(v)) return ['—', name];
                    return [`${fmtCx(v)} cx`, name];
                  }}
                />
                {marcoHoje && (
                  <ReferenceLine x={marcoHoje} stroke="#f59e0b" strokeDasharray="4 4"
                    label={{ value: 'hoje', fontSize: 9, fill: '#b45309', position: 'top' }} />
                )}
                <Line type="monotone" dataKey="vendido" name="Vendido"
                  stroke="#4338ca" strokeWidth={3} dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="meta" name="Meta (S&OP)"
                  stroke="#059669" strokeWidth={2.5} strokeDasharray="6 4"
                  dot={{ r: 3, fill: '#059669' }} connectNulls={false} />
                <Line type="monotone" dataKey="humanoHist" name="Meta histórica"
                  stroke="#0ea5e9" strokeWidth={1.5} strokeDasharray="4 3"
                  dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="ia" name="IA"
                  stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="3 3"
                  dot={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* 3 BLOCOS */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-5">

            {/* BLOCO A — Comparação 1 cartão vs ano anterior */}
            {comps.length > 0 && (
              <div className="rounded-xl bg-white border border-slate-100 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-3">
                  Plano vs ano anterior
                </div>
                <div className="space-y-3">
                  <div>
                    <div className="text-[10px] text-slate-400 font-bold mb-0.5">Meta total da janela</div>
                    <div className="text-xl font-black text-slate-900">{fmtCx(totalMeta)} cx</div>
                  </div>
                  <div className="border-t border-slate-100 pt-3">
                    <div className="text-[10px] text-slate-400 font-bold mb-0.5">Mesmo período ano anterior</div>
                    <div className="text-base font-bold text-slate-600">{fmtCx(totalAnoAnt)} cx</div>
                    {deltaAnoAnt != null && (
                      <div className={`text-sm font-black mt-1 ${deltaAnoAnt >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                        {sinal(deltaAnoAnt)} vs ano anterior
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* BLOCO B — Humano vs IA · somente mês a mês */}
            {d.fva ? (
              <div className="rounded-xl bg-white border border-slate-100 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
                  Humano vs IA · mês a mês
                </div>
                {(d.fva.detalhe_por_mes || []).length > 0 ? (
                  <div className="space-y-1.5">
                    <div className="grid grid-cols-3 gap-1 text-[9px] font-black uppercase tracking-widest text-slate-300 mb-1">
                      <span>Mês</span>
                      <span className="text-right">Humano</span>
                      <span className="text-right">IA</span>
                    </div>
                    {d.fva.detalhe_por_mes.map((m: any) => {
                      const hM = m.aderencia_humano != null && m.aderencia_ia != null && m.aderencia_humano >= m.aderencia_ia;
                      const iM = m.aderencia_humano != null && m.aderencia_ia != null && m.aderencia_ia > m.aderencia_humano;
                      return (
                        <div key={m.mes} className="grid grid-cols-3 gap-1 text-[11px]">
                          <span className="font-bold text-slate-500">{m.mes_label}</span>
                          <span className={`text-right font-black ${hM ? 'text-indigo-600' : 'text-slate-400'}`}>
                            H: {m.aderencia_humano != null ? fmtPct(m.aderencia_humano) : '—'}
                          </span>
                          <span className={`text-right font-black ${iM ? 'text-violet-600' : 'text-slate-400'}`}>
                            IA: {m.aderencia_ia != null ? fmtPct(m.aderencia_ia) : '—'}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-[11px] text-slate-400">
                    Dados de comparação IA/Humano disponíveis a partir de Jun/26.
                  </div>
                )}
              </div>
            ) : d.composicao ? (
              <div className="rounded-xl bg-white border border-slate-100 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
                  Top SKUs da categoria
                </div>
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
            ) : null}

            {/* BLOCO C — Trajetória plurianual */}
            {d.plurianual && (
              <div className="rounded-xl bg-white border border-slate-100 p-4">
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
                <div>
                  <div className="grid grid-cols-4 gap-1 px-1 text-[9px] font-black uppercase tracking-wider text-slate-300 mb-0.5">
                    <div>Ano</div><div className="text-right">Vol</div>
                    <div className="text-right">PMV</div><div className="text-right">Cli</div>
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

          {/* TABELA HISTÓRICA — últimos 12 meses (vendido / meta / BIAS / WMAPE) */}
          {tipo !== 'categoria' && (d.historico_12m || []).length > 0 && (
            <div className="rounded-xl bg-white border border-slate-100 overflow-hidden">
              <div className="px-4 py-3 bg-slate-50 border-b border-slate-100">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                  Acurácia · últimos {d.historico_12m.length} meses fechados
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">
                  BIAS = (meta − vendido) / vendido · WMAPE = |BIAS| ·
                  <span className="text-rose-500 font-bold"> vermelho</span> &gt; 15% superestimado ·
                  <span className="text-blue-500 font-bold"> azul</span> &gt; 15% subestimado
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full" style={{ borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr className="border-b border-slate-100">
                      {['Mês','Vendido (cx)','Meta (cx)','Δ (cx)','BIAS %','WMAPE %'].map(h => (
                        <th key={h} style={{
                          padding: '8px 12px', textAlign: h === 'Mês' ? 'left' : 'right',
                          fontSize: 10, fontWeight: 800, textTransform: 'uppercase',
                          letterSpacing: '.05em', color: '#94a3b8', whiteSpace: 'nowrap',
                        }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(d.historico_12m || []).map((row: any, idx: number) => {
                      const b = row.delta_pct;
                      const corBias = b > 15 ? '#e11d48' : b < -15 ? '#2563eb' : '#059669';
                      const corWmape = row.wmape_pct <= 20 ? '#059669' : row.wmape_pct <= 35 ? '#d97706' : '#e11d48';
                      return (
                        <tr key={row.mes} style={{ background: idx % 2 ? '#f9fafb' : '#fff', borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '7px 12px', fontWeight: 700, color: '#334155' }}>{row.mes_label}</td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', color: '#475569' }}>
                            {new Intl.NumberFormat('pt-BR').format(row.vendido_cx)}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', color: '#475569' }}>
                            {new Intl.NumberFormat('pt-BR').format(row.meta_cx)}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 700,
                            color: row.delta_cx > 0 ? '#e11d48' : row.delta_cx < 0 ? '#2563eb' : '#64748b' }}>
                            {row.delta_cx > 0 ? '▲ ' : row.delta_cx < 0 ? '▼ ' : ''}
                            {new Intl.NumberFormat('pt-BR').format(Math.abs(row.delta_cx))}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corBias }}>
                            {b >= 0 ? '+' : ''}{b?.toFixed(1).replace('.', ',')}%
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corWmape }}>
                            {row.wmape_pct?.toFixed(1).replace('.', ',')}%
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                  {/* Linha de total */}
                  {(() => {
                    const rows = d.historico_12m || [];
                    const totV = rows.reduce((s: number, r: any) => s + r.vendido_cx, 0);
                    const totM = rows.reduce((s: number, r: any) => s + r.meta_cx, 0);
                    const totD = rows.reduce((s: number, r: any) => s + r.delta_cx, 0);
                    const biasT = totV > 0 ? totD / totV * 100 : 0;
                    const wmapeT = totV > 0 ? Math.abs(totD) / totV * 100 : 0;
                    const corBT = biasT > 15 ? '#e11d48' : biasT < -15 ? '#2563eb' : '#059669';
                    const corWT = wmapeT <= 20 ? '#059669' : wmapeT <= 35 ? '#d97706' : '#e11d48';
                    return (
                      <tfoot>
                        <tr style={{ background: '#f0f9ff', borderTop: '2px solid #bae6fd' }}>
                          <td style={{ padding: '8px 12px', fontWeight: 800, fontSize: 11, color: '#0284c7' }}>Total período</td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 800, color: '#0f172a' }}>
                            {new Intl.NumberFormat('pt-BR').format(totV)}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 800, color: '#0f172a' }}>
                            {new Intl.NumberFormat('pt-BR').format(totM)}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 800,
                            color: totD > 0 ? '#e11d48' : totD < 0 ? '#2563eb' : '#64748b' }}>
                            {totD > 0 ? '▲ ' : totD < 0 ? '▼ ' : ''}
                            {new Intl.NumberFormat('pt-BR').format(Math.abs(totD))}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 800, color: corBT }}>
                            {biasT >= 0 ? '+' : ''}{biasT.toFixed(1).replace('.', ',')}%
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 800, color: corWT }}>
                            {wmapeT.toFixed(1).replace('.', ',')}%
                          </td>
                        </tr>
                      </tfoot>
                    );
                  })()}
                </table>
              </div>
            </div>
          )}

        </div>
      )}
    </div>
  );
}