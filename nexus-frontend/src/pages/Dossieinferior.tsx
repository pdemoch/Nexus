import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  X, TrendingUp, TrendingDown, Minus, AlertTriangle, Loader2,
} from 'lucide-react';
import {
  ComposedChart, Bar, Line, Cell, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine,
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

// ── Tooltip do gráfico principal ──────────────────────────────────────────
const TooltipPrincipal = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  // Excluir a Bar de BIAS do loop de linhas — ela é eixo direito (%) e não
  // deve aparecer como "BIAS X cx" junto com as linhas de volume.
  const items = payload.filter((p: any) =>
    p.value != null && !isNaN(p.value) && p.dataKey !== 'bias'
  );
  if (!items.length) return null;
  // BIAS calculado a partir dos valores de volume (vendido e meta) —
  // só exibe se o ponto de dados já tem bias calculado (mês fechado).
  // Usa o valor da Bar de bias do payload para saber se o mês está fechado.
  const biasEntry = payload.find((p: any) => p.dataKey === 'bias');
  const bias = biasEntry?.value != null && !isNaN(biasEntry.value)
    ? biasEntry.value
    : null;
  return (
    <div style={{ background: '#fff', border: '1px solid #cbd5e1', borderRadius: 8, padding: '10px 14px', fontSize: 11 }}>
      <div style={{ fontWeight: 700, marginBottom: 6, color: '#0f172a' }}>{label}</div>
      {items.map((p: any) => (
        <div key={p.dataKey} style={{ color: p.color, display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
          <span>{p.name}</span>
          <span style={{ fontWeight: 700 }}>{fmtCx(p.value)} cx</span>
        </div>
      ))}
      {bias != null && (
        <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid #f1f5f9', fontSize: 10,
          fontWeight: 800, color: bias > 15 ? '#e11d48' : bias < -15 ? '#2563eb' : '#059669' }}>
          BIAS: {bias >= 0 ? '+' : ''}{bias.toFixed(1)}%
        </div>
      )}
    </div>
  );
};

// ── Tooltip do gráfico de barras BIAS ────────────────────────────────────
const TooltipBias = ({ active, payload, label }: any) => {
  if (!active || !payload?.length || payload[0].value == null) return null;
  const v = payload[0].value;
  const cor = v > 15 ? '#e11d48' : v < -15 ? '#2563eb' : '#059669';
  return (
    <div style={{ background: '#fff', border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 12px', fontSize: 11 }}>
      <div style={{ fontWeight: 700, marginBottom: 4, color: '#0f172a' }}>{label}</div>
      <div style={{ fontWeight: 800, color: cor }}>
        BIAS: {v >= 0 ? '+' : ''}{v.toFixed(1)}%
      </div>
      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
        {v > 0 ? 'Meta acima do vendido' : v < 0 ? 'Meta abaixo do vendido' : 'No alvo'}
      </div>
    </div>
  );
};

export default function DossieInferior({
  prefixoApi, tipo, id, titulo, onFechar, paramsExtra, subtitulo
}: any) {
  const [d, setD]             = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const rota  = tipo === 'categoria' ? 'dossie-categoria' : 'dossie';
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

  // ── Meta unificada: final_cx (Nexus) → humano_hist_cx (histórico) → 0 ──
  const dadosGrafico = (d?.serie || []).map((x: any) => {
    const meta =
      x.final_cx        != null ? x.final_cx       :
      x.humano_hist_cx  != null ? x.humano_hist_cx :
      (x.vendido_cx != null ? 0 : null);

    // BIAS em % para o gráfico de barras (só meses fechados com vendido real)
    // eh_futuro cobre tanto meses futuros quanto o mês corrente (em andamento)
    const bias = (!x.eh_futuro && meta != null && x.vendido_cx && x.vendido_cx > 0)
      ? (meta - x.vendido_cx) / x.vendido_cx * 100
      : null;

    return {
      mes:      mesCurto(x.mes),
      vendido:  x.vendido_cx,
      meta,
      ia:       x.ia_cx,
      bias,
      ehFuturo: x.eh_futuro,
    };
  });

  const marcoHoje = d?.marco_hoje ? mesCurto(d.marco_hoje) : null;
  const comps     = d?.comparacoes_por_mes || (d?.comparacoes ? [d.comparacoes] : []);

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

          {/* LEGENDA */}
          <div className="mb-2 flex items-center justify-between flex-wrap gap-2">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
              Realizado vs previsto · caixas · últimos 36 meses
            </div>
            <div className="flex items-center gap-4 text-[10px] font-bold text-slate-500 flex-wrap">
              <span className="flex items-center gap-1.5">
                <span className="inline-block rounded-full" style={{ width: 20, height: 3, background: '#4338ca' }} />
                vendido
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block" style={{ width: 20, height: 2, background: 'repeating-linear-gradient(90deg,#059669 0 6px,transparent 6px 10px)' }} />
                meta
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block" style={{ width: 20, height: 2, background: 'repeating-linear-gradient(90deg,#8b5cf6 0 4px,transparent 4px 7px)' }} />
                IA
              </span>
            </div>
          </div>

          {/* GRÁFICO ÚNICO — linhas (eixo esq.) + barras BIAS % (eixo dir.) */}
          {(() => {
            const biasVals = dadosGrafico.map((d: any) => d.bias).filter((v: any) => v != null) as number[];
            const bMin = biasVals.length ? Math.min(...biasVals, -20) : -20;
            const bMax = biasVals.length ? Math.max(...biasVals,  20) : 20;
            const bDom: [number, number] = [Math.floor(bMin * 1.25), Math.ceil(bMax * 1.25)];
            return (
              <div style={{ height: 300 }} className="mb-3">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={dadosGrafico} margin={{ top: 8, right: 52, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="mes" tick={{ fontSize: 10, fill: '#64748b' }} interval={2} />

                    {/* Eixo esquerdo — caixas */}
                    <YAxis yAxisId="vol" orientation="left" width={52}
                      tick={{ fontSize: 10, fill: '#64748b' }}
                      tickFormatter={(v: any) => v == null || isNaN(v) ? '' : new Intl.NumberFormat('pt-BR').format(v)} />

                    {/* Eixo direito — BIAS % */}
                    <YAxis yAxisId="bias" orientation="right" width={38}
                      domain={bDom}
                      tick={{ fontSize: 9, fill: '#94a3b8' }}
                      tickFormatter={(v: any) => `${Number(v).toFixed(0)}%`} />

                    <Tooltip content={<TooltipPrincipal />} />

                    {/* Barras BIAS primeiro (atrás das linhas) */}
                    <Bar yAxisId="bias" dataKey="bias" name="BIAS"
                      maxBarSize={12} opacity={0.55} radius={[2, 2, 0, 0]}>
                      {dadosGrafico.map((entry: any, index: number) => (
                        <Cell key={index} fill={
                          entry.bias == null ? 'transparent' :
                          entry.bias > 20    ? '#e11d48' :
                          entry.bias > 0     ? '#fca5a5' :
                          entry.bias < -20   ? '#2563eb' :
                          entry.bias < 0     ? '#93c5fd' : '#94a3b8'
                        } />
                      ))}
                    </Bar>

                    {/* Linhas de referência BIAS */}
                    <ReferenceLine yAxisId="bias" y={0}   stroke="#94a3b8" strokeWidth={1} />
                    <ReferenceLine yAxisId="bias" y={20}  stroke="#fca5a5" strokeDasharray="4 2" />
                    <ReferenceLine yAxisId="bias" y={-20} stroke="#93c5fd" strokeDasharray="4 2" />

                    {/* Marco hoje */}
                    {marcoHoje && (
                      <ReferenceLine x={marcoHoje} stroke="#f59e0b" strokeDasharray="4 4"
                        label={{ value: 'hoje', fontSize: 9, fill: '#b45309', position: 'insideTopRight' }} />
                    )}

                    {/* Linhas por cima das barras */}
                    <Line yAxisId="vol" type="monotone" dataKey="vendido" name="Vendido"
                      stroke="#4338ca" strokeWidth={3} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
                    <Line yAxisId="vol" type="monotone" dataKey="meta" name="Meta"
                      stroke="#059669" strokeWidth={2} strokeDasharray="6 4"
                      dot={false} activeDot={{ r: 4 }} connectNulls={false} />
                    <Line yAxisId="vol" type="monotone" dataKey="ia" name="IA"
                      stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="3 3"
                      dot={false} activeDot={{ r: 3 }} connectNulls={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            );
          })()}

          <div className="text-[9px] text-slate-400 mb-4 text-center">
            Barras = BIAS % (meta − vendido) / vendido ·
            <span className="text-rose-400 font-bold"> vermelho</span> = meta acima do vendido ·
            <span className="text-blue-400 font-bold"> azul</span> = meta abaixo · tracejado em ±20%
          </div>

          {/* ── ACURÁCIA DOS ÚLTIMOS 6 MESES ── */}
          {tipo !== 'categoria' && (d.historico_12m || []).length > 0 && (
            <div className="rounded-xl bg-white border border-slate-100 overflow-hidden mb-5">
              <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-100">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                  Acurácia — últimos {d.historico_12m.length} meses fechados
                </div>
                <div className="text-[10px] text-slate-400">
                  BIAS = (meta − vendido) / vendido ·
                  <span className="text-rose-500 font-bold"> vermelho</span> = meta acima do vendido ·
                  <span className="text-blue-500 font-bold"> azul</span> = meta abaixo do vendido
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full" style={{ borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr className="border-b border-slate-100">
                      {/* células vazias: Mês + Vendido + Meta H + Meta IA */}
                      <th colSpan={4} style={{ padding: '4px 12px', background: '#fff', borderBottom: '1px solid #f1f5f9' }} />
                      <th colSpan={2} style={{ padding: '4px 12px', textAlign: 'center', fontSize: 9,
                        fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.05em',
                        color: '#6366f1', borderBottom: '1px solid #e0e7ff', background: '#f5f3ff',
                        whiteSpace: 'nowrap' }}>Humano</th>
                      <th colSpan={3} style={{ padding: '4px 12px', textAlign: 'center', fontSize: 9,
                        fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.05em',
                        color: '#7c3aed', borderBottom: '1px solid #ede9fe', background: '#f5f3ff',
                        whiteSpace: 'nowrap' }}>IA</th>
                      <th style={{ padding: '4px 12px', textAlign: 'center', fontSize: 9,
                        fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.05em',
                        color: '#0891b2', borderBottom: '1px solid #cffafe', background: '#ecfeff',
                        whiteSpace: 'nowrap' }}>FVA</th>
                    </tr>
                    <tr className="border-b border-slate-100">
                      {[
                        { h: 'Mês',           align: 'left'  },
                        { h: 'Vendido (cx)',   align: 'right' },
                        { h: 'Meta H (cx)',    align: 'right' },
                        { h: 'Meta IA (cx)',   align: 'right' },
                        { h: 'BIAS %',         align: 'right' },
                        { h: 'WMAPE %',        align: 'right' },
                        { h: 'BIAS IA %',      align: 'right' },
                        { h: 'WMAPE IA %',     align: 'right' },
                        { h: 'FVA pp',         align: 'right' },
                      ].map(({ h, align }) => (
                        <th key={h} style={{
                          padding: '5px 12px', textAlign: align as any,
                          fontSize: 10, fontWeight: 800, textTransform: 'uppercase',
                          letterSpacing: '.05em', color: '#94a3b8', whiteSpace: 'nowrap',
                        }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(d.historico_12m || []).map((row: any, idx: number) => {
                      const b    = row.delta_pct;
                      const corB = b > 15 ? '#e11d48' : b < -15 ? '#2563eb' : '#059669';
                      const corW = row.wmape_pct <= 20 ? '#059669' : row.wmape_pct <= 35 ? '#d97706' : '#e11d48';
                      const bIA  = row.bias_ia_pct;
                      const corBIA = bIA == null ? '#94a3b8' : bIA > 15 ? '#e11d48' : bIA < -15 ? '#2563eb' : '#059669';
                      const wIA  = row.wmape_ia_pct;
                      const corWIA = wIA == null ? '#94a3b8' : wIA <= 20 ? '#059669' : wIA <= 35 ? '#d97706' : '#e11d48';
                      const fva  = row.fva_pct;
                      const corFVA = fva == null ? '#94a3b8' : fva > 0 ? '#6366f1' : fva < 0 ? '#7c3aed' : '#64748b';
                      return (
                        <tr key={row.mes} style={{ background: idx % 2 ? '#f9fafb' : '#fff', borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '7px 12px', fontWeight: 700, color: '#334155' }}>{row.mes_label}</td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', color: '#475569' }}>
                            {new Intl.NumberFormat('pt-BR').format(row.vendido_cx)}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', color: '#475569' }}>
                            {new Intl.NumberFormat('pt-BR').format(row.meta_cx)}
                          </td>
                          {/* Meta IA (cx) — só disponível a partir de jun/26 */}
                          <td style={{ padding: '7px 12px', textAlign: 'right', color: '#7c3aed', fontWeight: 600 }}>
                            {row.ia_cx != null
                              ? new Intl.NumberFormat('pt-BR').format(row.ia_cx)
                              : '—'}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corB }}>
                            {b >= 0 ? '+' : ''}{b?.toFixed(1).replace('.', ',')}%
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corW }}>
                            {row.wmape_pct?.toFixed(1).replace('.', ',')}%
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corBIA }}>
                            {bIA == null ? '—' : `${bIA >= 0 ? '+' : ''}${bIA.toFixed(1).replace('.', ',')}%`}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corWIA }}>
                            {wIA == null ? '—' : `${wIA.toFixed(1).replace('.', ',')}%`}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corFVA, fontSize: 10 }}>
                            {fva == null ? '—' : `${fva >= 0 ? '+' : ''}${fva.toFixed(1).replace('.', ',')} pp`}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                  {/* Totalizador */}
                  {(() => {
                    const rows  = d.historico_12m || [];
                    const totV  = rows.reduce((s: number, r: any) => s + r.vendido_cx, 0);
                    const totM  = rows.reduce((s: number, r: any) => s + r.meta_cx, 0);
                    const totD  = rows.reduce((s: number, r: any) => s + r.delta_cx, 0);
                    const biasT = totV > 0 ? totD / totV * 100 : 0;
                    const wmT   = totV > 0 ? Math.abs(totD) / totV * 100 : 0;
                    const corBT = biasT > 15 ? '#e11d48' : biasT < -15 ? '#2563eb' : '#059669';
                    const corWT = wmT <= 20 ? '#059669' : wmT <= 35 ? '#d97706' : '#e11d48';
                    // Acumulado IA — só sobre meses com dado de IA
                    const rowsIA = rows.filter((r: any) => r.ia_cx != null);
                    const totIA  = rowsIA.reduce((s: number, r: any) => s + (r.ia_cx || 0), 0);
                    const totVIA = rowsIA.reduce((s: number, r: any) => s + r.vendido_cx, 0);
                    const biasIA = totVIA > 0 ? (totIA - totVIA) / totVIA * 100 : null;
                    const wmIA   = biasIA != null ? Math.abs(biasIA) : null;
                    const corBIA = biasIA == null ? '#94a3b8' : biasIA > 15 ? '#e11d48' : biasIA < -15 ? '#2563eb' : '#059669';
                    const corWIA = wmIA == null ? '#94a3b8' : wmIA <= 20 ? '#059669' : wmIA <= 35 ? '#d97706' : '#e11d48';
                    // FVA acumulado (média simples dos meses com dado)
                    const fvaRows = rows.filter((r: any) => r.fva_pct != null);
                    const fvaAcc  = fvaRows.length > 0
                      ? fvaRows.reduce((s: number, r: any) => s + r.fva_pct, 0) / fvaRows.length
                      : null;
                    const corFVA = fvaAcc == null ? '#94a3b8' : fvaAcc > 0 ? '#6366f1' : fvaAcc < 0 ? '#7c3aed' : '#64748b';
                    return (
                      <tfoot>
                        <tr style={{ background: '#f0f9ff', borderTop: '2px solid #bae6fd' }}>
                          <td style={{ padding: '7px 12px', fontWeight: 800, fontSize: 11, color: '#0284c7' }}>Acumulado</td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800 }}>
                            {new Intl.NumberFormat('pt-BR').format(totV)}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800 }}>
                            {new Intl.NumberFormat('pt-BR').format(totM)}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: '#7c3aed' }}>
                            {rowsIA.length > 0 ? new Intl.NumberFormat('pt-BR').format(Math.round(totIA)) : '—'}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corBT }}>
                            {biasT >= 0 ? '+' : ''}{biasT.toFixed(1).replace('.', ',')}%
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corWT }}>
                            {wmT.toFixed(1).replace('.', ',')}%
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corBIA }}>
                            {biasIA == null ? '—' : `${biasIA >= 0 ? '+' : ''}${biasIA.toFixed(1).replace('.', ',')}%`}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corWIA }}>
                            {wmIA == null ? '—' : `${wmIA.toFixed(1).replace('.', ',')}%`}
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 800, color: corFVA, fontSize: 10 }}>
                            {fvaAcc == null ? '—' : `${fvaAcc >= 0 ? '+' : ''}${fvaAcc.toFixed(1).replace('.', ',')} pp`}
                          </td>
                        </tr>
                      </tfoot>
                    );
                  })()}
                </table>
              </div>
            </div>
          )}

          {/* ── 3 BLOCOS ── */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

            {/* BLOCO A — Plano vs Ano anterior · mês a mês */}
            {comps.length > 0 && (
              <div className="rounded-xl bg-white border border-slate-100 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-3">
                  Plano vs ano anterior
                </div>
                <div>
                  {comps.map((comp: any, idx: number) => {
                    const delta = comp.ano_ant_cx > 0
                      ? (comp.final_cx - comp.ano_ant_cx) / comp.ano_ant_cx * 100
                      : null;
                    return (
                      <div key={comp.mes_foco || idx}
                        className={`py-2.5 ${idx < comps.length - 1 ? 'border-b border-slate-100' : ''}`}>
                        <div className="text-[10px] font-black text-indigo-600 mb-1">{comp.mes_label}</div>
                        <div className="flex items-baseline justify-between gap-2">
                          <div>
                            <div className="text-[9px] text-slate-400 font-bold">Meta</div>
                            <div className="text-base font-black text-slate-900">{fmtCx(comp.final_cx)} cx</div>
                          </div>
                          {comp.ano_ant_cx > 0 && (
                            <div className="text-right">
                              <div className="text-[9px] text-slate-400 font-bold">Ano ant.</div>
                              <div className="text-sm font-bold text-slate-500">{fmtCx(comp.ano_ant_cx)} cx</div>
                              {delta != null && (
                                <div className={`text-[11px] font-black ${delta >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                                  {sinal(delta)}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* BLOCO B — Humano vs IA · mês a mês */}
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

        </div>
      )}
    </div>
  );
}