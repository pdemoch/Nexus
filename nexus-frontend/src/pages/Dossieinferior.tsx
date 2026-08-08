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
   DOSSIÊ — INLINE EXPANSÍVEL, só em Caixas (qt_pedido).
   Sem toggle R$/Caixas — volume de caixas é o que o planejador decide.

   Linhas do gráfico (nomenclatura alinhada ao S&OP):
     vendido          = indigo forte, sólido — realizado (qt_pedido)
     Meta             = verde tracejado — vol_final do ciclo M-2 de cada mês
                        (para meses passados: o compromisso original;
                         para meses futuros: o plano do ciclo ativo)
     IA               = roxo tracejado fino — vol_ia do ciclo M-2
     Ciclo Anterior   = âmbar pontilhado — vol_final do ciclo (ativo-1)
                        só nos meses M+2 e M+3 que ele cobriu

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

const tendIcon = (t: string) =>
  t === 'CRESCIMENTO' ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
  t === 'DECLINIO' ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
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
    // paramsExtra permite escopo adicional (ex.: razao_social na tela de Metas)
    const extra = paramsExtra
      ? '&' + Object.entries(paramsExtra)
          .filter(([, v]) => v != null && v !== '')
          .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
      : '';
    axios.get(`${prefixoApi}/${rota}?${param}${extra}`)
      .then((r) => setD(r.data))
      .catch(() => setD(null))
      .finally(() => setLoading(false));
  }, [prefixoApi, tipo, id, JSON.stringify(paramsExtra || {})]);

  // Só caixas — o planejador decide volume, o valor é derivado.
  const dadosGrafico = (d?.serie || []).map((x: any) => ({
    mes: mesCurto(x.mes),
    vendido:  x.vendido_cx,
    meta:     x.final_cx,        // Meta = vol_final do ciclo M-2 (compromisso/plano atual)
    ia:       x.ia_cx,           // IA   = vol_ia  do ciclo M-2
    cicloAnt: x.final_ciclo_ant_cx,  // Ciclo anterior — só M+2 e M+3 que ele cobriu
  }));

  const marcoHoje = d?.marco_hoje ? mesCurto(d.marco_hoje) : null;

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
              Realizado vs previsto · caixas
            </div>
            <div className="flex items-center gap-5 text-[10px] font-bold text-slate-500 flex-wrap">
              <span className="flex items-center gap-1.5">
                <span className="w-5 h-0.5 bg-indigo-600 inline-block rounded-full" style={{ height: 3 }} />
                vendido
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-5 inline-block" style={{ height: 2, background: 'repeating-linear-gradient(90deg,#059669 0 6px,transparent 6px 10px)' }} />
                Meta
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-5 inline-block" style={{ height: 2, background: 'repeating-linear-gradient(90deg,#8b5cf6 0 4px,transparent 4px 7px)' }} />
                IA
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-5 inline-block" style={{ height: 2, background: 'repeating-linear-gradient(90deg,#d97706 0 2px,transparent 2px 5px)' }} />
                Ciclo anterior
              </span>
            </div>
          </div>

          <div style={{ height: 300 }} className="mb-6">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={dadosGrafico} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis
                  dataKey="mes"
                  tick={{ fontSize: 10, fill: '#64748b' }}
                  interval={1}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: '#64748b' }}
                  width={50}
                  tickFormatter={(v: any) =>
                    v == null || isNaN(v) ? '' : new Intl.NumberFormat('pt-BR').format(v)
                  }
                />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #cbd5e1' }}
                  formatter={(v: any, name: any) => {
                    if (v == null || isNaN(v)) return ['—', name];
                    return [`${fmtCx(v)} cx`, name];
                  }}
                />
                {marcoHoje && (
                  <ReferenceLine
                    x={marcoHoje}
                    stroke="#f59e0b"
                    strokeDasharray="4 4"
                    label={{ value: 'hoje', fontSize: 9, fill: '#b45309', position: 'top' }}
                  />
                )}
                {/* Realizado — sólido, grosso */}
                <Line
                  type="monotone" dataKey="vendido" name="Vendido"
                  stroke="#4338ca" strokeWidth={3} dot={false} connectNulls={false}
                />
                {/* Meta = vol_final do ciclo M-2 de cada mês */}
                <Line
                  type="monotone" dataKey="meta" name="Meta"
                  stroke="#059669" strokeWidth={2.5} strokeDasharray="6 4"
                  dot={{ r: 3, fill: '#059669' }} connectNulls={false}
                />
                {/* IA = vol_ia do ciclo M-2 */}
                <Line
                  type="monotone" dataKey="ia" name="IA"
                  stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="3 3"
                  dot={false} connectNulls={false}
                />
                {/* Ciclo anterior — só M+2 e M+3 que ele cobriu */}
                <Line
                  type="monotone" dataKey="cicloAnt" name="Ciclo anterior"
                  stroke="#d97706" strokeWidth={1.5} strokeDasharray="2 3"
                  dot={false} connectNulls={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* INSIGHTS */}
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

          {/* COMPARAÇÕES — um cartão por mês da janela ativa */}
          {(d.comparacoes_por_mes || (d.comparacoes ? [d.comparacoes] : [])).length > 0 && (
            <div className="mb-4">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
                Comparações por mês
              </div>
              <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${(d.comparacoes_por_mes || [d.comparacoes]).length}, 1fr)` }}>
                {(d.comparacoes_por_mes || [d.comparacoes]).map((comp: any) => (
                  <div key={comp.mes_foco} className="rounded-xl bg-white border border-slate-100 p-3">
                    <div className="text-[11px] font-black text-indigo-600 mb-2">{comp.mes_label}</div>
                    <div className="space-y-2">
                      {[
                        { label: 'Meta',           cx: comp.final_cx,    rsv: comp.final_rs,    base: true },
                        { label: 'Orçamento',      cx: null,             rsv: comp.orcamento_rs },
                        { label: 'Ciclo anterior', cx: comp.ciclo_ant_cx, rsv: comp.ciclo_ant_rs },
                        { label: 'Ano anterior',   cx: comp.ano_ant_cx,  rsv: comp.ano_ant_rs },
                      ].map((linha) => {
                        const val = linha.cx;
                        const planoVal = comp.final_cx;
                        let delta: number | null = null;
                        if (!linha.base && val != null && val !== 0 && planoVal != null) {
                          const dc = (planoVal - val) / val;
                          if (isFinite(dc)) delta = dc;
                        }
                        return (
                          <div key={linha.label} className="flex items-center justify-between text-xs">
                            <span className={`${linha.base ? 'font-black text-slate-700' : 'font-medium text-slate-500'} text-[11px]`}>
                              {linha.label}
                            </span>
                            <div className="text-right">
                              <span className={`font-bold ${linha.base ? 'text-slate-900' : 'text-slate-600'}`}>
                                {val == null ? '—' : `${fmtCx(val)} cx`}
                              </span>
                              {linha.rsv != null && linha.rsv > 0 && (
                                <div className="text-[10px] font-bold text-indigo-500">{fmtRs(linha.rsv)}</div>
                              )}
                              {delta != null && (
                                <span className={`text-[10px] font-bold ${delta >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                                  {delta >= 0 ? '+' : ''}{fmtPct(delta)}
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TRÊS BLOCOS — FVA + composição + plurianual */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {d.fva ? (
              <div className="rounded-xl bg-white border border-slate-100 p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                    Quem acertou mais
                  </div>
                  {d.fva.baixo_volume && (
                    <span className="text-[9px] font-black uppercase text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">
                      baixo volume
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: 'Humano', val: d.fva.aderencia_humano, vol: d.fva.humano_cx, key: 'HUMANO' },
                    { label: 'IA',     val: d.fva.aderencia_ia,     vol: d.fva.ia_cx,     key: 'IA' },
                  ].map((x) => {
                    const venceu = d.fva.vencedor === x.key;
                    return (
                      <div key={x.key} className={`rounded-lg px-2 py-2 border ${venceu ? 'border-emerald-300 bg-emerald-50' : 'border-slate-100 bg-slate-50'}`}>
                        <div className="text-[9px] font-bold text-slate-400">{x.label}</div>
                        <div className={`text-base font-black ${venceu ? 'text-emerald-700' : 'text-slate-500'}`}>
                          {x.val != null ? fmtPct(x.val) : '—'}
                        </div>
                        <div className="text-[9px] font-bold text-slate-400">previu {fmtCx(x.vol)} cx</div>
                      </div>
                    );
                  })}
                </div>
                {/* Detalhe mês a mês */}
                {(d.fva.detalhe_por_mes || []).length > 0 && (
                  <div className="mt-2 space-y-1">
                    <div className="text-[9px] font-black uppercase tracking-widest text-slate-300 mb-1">
                      Mês a mês ({d.fva.n_meses} meses)
                    </div>
                    {d.fva.detalhe_por_mes.map((m: any) => (
                      <div key={m.mes} className="grid grid-cols-3 gap-1 text-[10px]">
                        <span className="font-bold text-slate-500">{m.mes_label}</span>
                        <span className={`text-right font-black ${d.fva.vencedor === 'HUMANO' && m.aderencia_humano >= m.aderencia_ia ? 'text-indigo-600' : 'text-slate-400'}`}>
                          H: {m.aderencia_humano != null ? fmtPct(m.aderencia_humano) : '—'}
                        </span>
                        <span className={`text-right font-black ${d.fva.vencedor === 'IA' && m.aderencia_ia >= m.aderencia_humano ? 'text-violet-600' : 'text-slate-400'}`}>
                          IA: {m.aderencia_ia != null ? fmtPct(m.aderencia_ia) : '—'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {d.fva.insight_fva && (
                  <div className="mt-2 text-[11px] font-medium text-slate-600">{d.fva.insight_fva}</div>
                )}
              </div>
            ) : (
              d.composicao && (
                <div className="rounded-xl bg-white border border-slate-100 p-3">
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
              )
            )}

            {/* BLOCO C — Trajetória plurianual */}
            {d.plurianual && (
              <div className="rounded-xl bg-white border border-slate-100 p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                    Trajetória · {d.plurianual.periodo_label || 'anual'}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] font-bold">
                    <span className="flex items-center gap-0.5 text-slate-500">
                      Vol {tendIcon(d.plurianual.tendencia_volume)}
                    </span>
                    <span className="flex items-center gap-0.5 text-slate-500">
                      PMV {tendIcon(d.plurianual.tendencia_pmv)}
                    </span>
                  </div>
                </div>
                {d.plurianual.alerta_historico && (
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold text-amber-700 bg-amber-50 rounded px-2 py-1">
                    <AlertTriangle className="w-3 h-3 shrink-0" /> {d.plurianual.alerta_historico}
                  </div>
                )}
                <div className="space-y-0.5">
                  <div className="grid grid-cols-4 gap-1 px-1 text-[9px] font-black uppercase tracking-wider text-slate-300">
                    <div>Ano</div>
                    <div className="text-right">Vol</div>
                    <div className="text-right">PMV</div>
                    <div className="text-right">Cli</div>
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