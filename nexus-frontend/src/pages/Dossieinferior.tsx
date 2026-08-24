import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  X, Loader2, Sparkles,
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

const mesCurto = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

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

  // Avaliação do Planejador de Demanda — gerada em lote por categoria a
  // cada ciclo novo (ver planejador_demanda.py), lida aqui do cache, nunca
  // chamada na hora. O gráfico abaixo continua exatamente como era; isto
  // é um bloco novo, não uma alteração do que já existia.
  const [aval, setAval]               = useState<any>(null);
  const [avalLoading, setAvalLoading] = useState(true);

  useEffect(() => {
    setAvalLoading(true);
    const tipoAval = tipo === 'categoria' ? 'categoria' : 'sku';
    axios.get(`/api/v1/assistente/avaliacao?tipo=${tipoAval}&id=${encodeURIComponent(id)}`)
      .then(r => setAval(r.data))
      .catch(() => setAval(null))
      .finally(() => setAvalLoading(false));
  }, [tipo, id]);

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
      (x.final_cx != null && x.final_cx > 0) ? x.final_cx       :
      (x.humano_hist_cx != null && x.humano_hist_cx > 0) ? x.humano_hist_cx :
      (x.vendido_cx != null ? null : null);

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

      {/* AVALIAÇÃO DO PLANEJADOR DE DEMANDA — gerada por categoria a cada
          ciclo novo (ver planejador_demanda.py). Estado próprio, não
          depende do carregamento do gráfico abaixo. */}
      <div className="px-5 pt-4">
        <div className="rounded-xl border border-violet-100 bg-violet-50/60 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Sparkles className="w-3.5 h-3.5 text-violet-500 shrink-0" />
            <span className="text-[10px] font-black uppercase tracking-widest text-violet-500">
              Avaliação do Planejador de Demanda
            </span>
          </div>
          {avalLoading ? (
            <div className="flex items-center gap-2 text-violet-400 text-xs font-bold py-0.5">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Carregando avaliação…
            </div>
          ) : aval?.avaliacao ? (
            <p className="text-[12.5px] leading-relaxed text-slate-700">{aval.avaliacao}</p>
          ) : (
            <p className="text-xs font-bold text-slate-400">
              Ainda não avaliado neste ciclo. A avaliação é gerada automaticamente
              sempre que um novo ciclo é aberto.
            </p>
          )}
        </div>
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

          {/* Tabela de Acurácia dos últimos meses foi removida daqui — a
              avaliação do Planejador de Demanda (bloco acima, no topo do
              painel) já cobre esta análise em texto, gerada pelo agente
              a cada ciclo novo. */}

          {/* ── 3 BLOCOS ── */}
          <div className="grid grid-cols-1 gap-4">

            {/* BLOCO B — Top SKUs da categoria (único bloco que sobrou;
                Plano vs Ano Anterior e Trajetória plurianual foram
                removidos pelo mesmo motivo da tabela de Acurácia acima) */}
            {d.composicao ? (
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
          </div>

        </div>
      )}
    </div>
  );
}