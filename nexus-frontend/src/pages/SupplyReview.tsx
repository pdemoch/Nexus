import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Download, X, MessageSquare,
  TrendingUp, TrendingDown, Minus, Bot, AlertTriangle, Loader2, LineChart as LineIcon,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';

/* =====================================================================
   SUPPLY REVIEW — a fábrica confronta demanda (Meta) com capacidade.
   Mesmo padrão visual. Proposta: coluna Supply editável vs Meta de
   referência, gap visível, e painel de justificativa (motivo + texto)
   que abre quando o supply diverge da meta.
   ===================================================================== */

const MOTIVOS = [
  { valor: 'capacidade_producao', label: 'Capacidade de Produção' },
  { valor: 'ruptura_insumo', label: 'Ruptura de Insumo' },
  { valor: 'estrategia_estoque', label: 'Estratégia de Estoque' },
  { valor: 'capacidade_ociosa', label: 'Capacidade Ociosa (injeção)' },
  { valor: 'outro', label: 'Outro' },
];

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`;
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

export default function SupplyReview() {
  const [dados, setDados] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  // edits: `${sku}|${mes}` -> { novo_volume, motivo, justificativa }
  const [edits, setEdits] = useState<Record<string, { novo_volume: number; motivo?: string; justificativa?: string }>>({});
  const [justAberta, setJustAberta] = useState<string | null>(null);
  const [dossieSku, setDossieSku] = useState<string | null>(null);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/supply/tabela');
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[] = dados?.meses || [];
  const congelada: boolean = dados?.congelada;
  const metasLiberada: boolean = dados?.metas_liberada;

  const volCelula = (sku: string, mes: string, original: number) => {
    const k = `${sku}|${mes}`;
    return k in edits ? edits[k].novo_volume : original;
  };
  const setVol = (sku: string, mes: string, v: number, metaRef: number) => {
    const k = `${sku}|${mes}`;
    setEdits((e) => ({ ...e, [k]: { ...(e[k] || {}), novo_volume: Math.max(0, Math.round(v || 0)) } }));
  };
  const setJust = (sku: string, mes: string, campo: 'motivo' | 'justificativa', valor: string, volAtual: number) => {
    const k = `${sku}|${mes}`;
    setEdits((e) => {
      const atual = e[k] || {};
      return {
        ...e,
        [k]: {
          ...atual,
          novo_volume: atual.novo_volume ?? volAtual,  // garante o volume; nunca sobrescrito por spread
          [campo]: valor,
        },
      };
    });
  };

  const totaisVivos = useMemo(() => {
    const meta: Record<string, number> = {}; const sup: Record<string, number> = {}; const fat: Record<string, number> = {};
    meses.forEach((m) => { meta[m] = 0; sup[m] = 0; fat[m] = 0; });
    dados?.categorias?.forEach((cat: any) =>
      cat.segmentos.forEach((seg: any) =>
        seg.skus.forEach((s: any) =>
          meses.forEach((m) => {
            const cel = s.meses[m]; if (!cel) return;
            const v = volCelula(s.sku, m, cel.supply);
            meta[m] += cel.meta; sup[m] += v; fat[m] += v * (cel.pmv || 0);
          })
        )
      )
    );
    return { meta, sup, fat };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [sku, mes] = k.split('|');
        return { sku, mes_projetado: mes, novo_volume: v.novo_volume, motivo: v.motivo || null, justificativa: v.justificativa || null };
      });
      await axios.post('/api/v1/supply/salvar', { ajustes });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally { setSalvando(false); }
  };

  const toggle = (nome: string) =>
    setAbertas((p) => { const n = new Set(p); n.has(nome) ? n.delete(nome) : n.add(nome); return n; });

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando o plano…</div>;
  }
  if (!metasLiberada) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center text-center px-6">
        <AlertTriangle className="w-10 h-10 text-amber-400 mb-3" />
        <h2 className="text-lg font-black text-slate-800">Aguardando o Comercial</h2>
        <p className="text-sm text-slate-400 max-w-sm mt-1">A Supply Review abre quando as Metas Comerciais forem congeladas.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      {/* CABEÇALHO + TOTAIS */}
      <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
        <div className="px-6 pt-5 pb-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-black text-slate-900 tracking-tight">Supply Review</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · ajuste o volume à capacidade produtiva
              {congelada && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={salvar} disabled={!temEdicoes || salvando || congelada}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !congelada ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            <a href="/api/v1/supply/exportar"
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
              <Download className="w-4 h-4" /> CSV
            </a>
          </div>
        </div>
        <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr))` }}>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Demanda vs Capacidade</div>
          {meses.map((m) => {
            const gap = totaisVivos.sup[m] - totaisVivos.meta[m];
            return (
              <div key={m} className="bg-slate-50 rounded-lg px-3 py-2">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
                <div className="text-sm font-black text-slate-900">{fmtCx(totaisVivos.sup[m])} <span className="text-[10px] font-bold text-slate-400">cx</span></div>
                <div className="text-[10px] font-bold text-slate-400">meta {fmtCx(totaisVivos.meta[m])}</div>
                <div className={`text-[10px] font-bold ${gap < 0 ? 'text-rose-500' : gap > 0 ? 'text-emerald-600' : 'text-slate-400'}`}>
                  {gap > 0 ? '+' : ''}{fmtCx(gap)} gap
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* CABEÇALHO TABELA */}
      <div className="px-6 pt-4">
        <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
             style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
          <div>Categoria / SKU</div>
          {meses.map((m) => <div key={m} className="text-right">{mesLabel(m)}</div>)}
          <div />
        </div>
      </div>

      {/* CORPO */}
      <div className="px-6 pb-24">
        {dados?.categorias?.map((cat: any) => {
          const catAberta = abertas.has(cat.nome);
          return (
            <div key={cat.nome} className="mb-1">
              <button onClick={() => toggle(cat.nome)}
                className="w-full grid gap-2 px-3 py-2.5 items-center bg-white rounded-lg border border-slate-100 hover:border-slate-200 transition-colors"
                style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
                <div className="flex items-center gap-2 min-w-0">
                  {catAberta ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                  <span className="font-black text-slate-800 text-sm truncate">{cat.nome}</span>
                </div>
                {meses.map((m) => {
                  const soma = cat.segmentos.reduce((acc: number, seg: any) =>
                    acc + seg.skus.reduce((a: number, s: any) => a + volCelula(s.sku, m, s.meses[m]?.supply || 0), 0), 0);
                  return <div key={m} className="text-right text-sm font-bold text-slate-500">{fmtCx(soma)}</div>;
                })}
                <div />
              </button>

              {catAberta && cat.segmentos.map((seg: any) => (
                <div key={seg.nome} className="ml-4 mt-1">
                  <div className="px-3 py-1.5 text-[11px] font-black uppercase tracking-wider text-slate-400">{seg.nome}</div>
                  {seg.skus.map((s: any) => {
                    // semáforo: maior corte relativo à meta
                    const corte = meses.reduce<'alta' | 'media' | null>((acc, m) => {
                      const cel = s.meses[m]; if (!cel || !cel.meta) return acc;
                      const v = volCelula(s.sku, m, cel.supply);
                      const d = (cel.meta - v) / cel.meta; // >0 = corte
                      if (d >= 0.2) return 'alta';
                      if (d >= 0.1 && acc !== 'alta') return 'media';
                      return acc;
                    }, null);
                    const barra = corte === 'alta' ? 'bg-rose-400' : corte === 'media' ? 'bg-amber-400' : 'bg-transparent';
                    return (
                      <div key={s.sku}>
                        <div className="grid gap-2 px-3 py-1.5 items-center hover:bg-white rounded-lg group relative"
                          style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
                          <div className={`absolute left-0 top-1 bottom-1 w-0.5 rounded ${barra}`} />
                          <div className="min-w-0 pl-2">
                            <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                            <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                          </div>
                          {meses.map((m) => {
                            const cel = s.meses[m];
                            if (!cel) return <div key={m} />;
                            const val = volCelula(s.sku, m, cel.supply);
                            const gap = val - cel.meta;
                            const k = `${s.sku}|${m}`;
                            const editado = k in edits;
                            const temJust = edits[k]?.motivo || edits[k]?.justificativa || cel.motivo || cel.justificativa;
                            return (
                              <div key={m} className="text-right">
                                <div className="flex items-center justify-end gap-1">
                                  {gap !== 0 && (
                                    <button onClick={() => setJustAberta(justAberta === k ? null : k)}
                                      className={`p-0.5 rounded ${temJust ? 'text-indigo-500' : 'text-slate-300'} hover:text-indigo-600`}
                                      title="Justificar ajuste">
                                      <MessageSquare className="w-3 h-3" />
                                    </button>
                                  )}
                                  <input type="number" value={val} disabled={congelada}
                                    onChange={(e) => setVol(s.sku, m, parseInt(e.target.value), cel.meta)}
                                    className={`w-20 text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                                      ${editado ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                                      ${congelada ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`} />
                                </div>
                                <div className="text-[9px] font-bold text-slate-300 pr-2">
                                  meta {fmtCx(cel.meta)}{gap !== 0 && <span className={gap < 0 ? 'text-rose-400' : 'text-emerald-500'}> · {gap > 0 ? '+' : ''}{fmtCx(gap)}</span>}
                                </div>
                                {/* painel de justificativa */}
                                {justAberta === k && (
                                  <div className="absolute z-10 mt-1 right-0 w-64 bg-white border border-slate-200 rounded-xl shadow-xl p-3 text-left">
                                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-1">Justificar o ajuste</div>
                                    <select
                                      value={edits[k]?.motivo || cel.motivo || ''}
                                      onChange={(e) => setJust(s.sku, m, 'motivo', e.target.value, val)}
                                      className="w-full text-xs border border-slate-200 rounded-lg px-2 py-1.5 mb-2">
                                      <option value="">Selecione o motivo…</option>
                                      {MOTIVOS.map((mo) => <option key={mo.valor} value={mo.valor}>{mo.label}</option>)}
                                    </select>
                                    <textarea
                                      value={edits[k]?.justificativa ?? cel.justificativa ?? ''}
                                      onChange={(e) => setJust(s.sku, m, 'justificativa', e.target.value, val)}
                                      placeholder="Detalhe (opcional)…" rows={2}
                                      className="w-full text-xs border border-slate-200 rounded-lg px-2 py-1.5 resize-none" />
                                    <button onClick={() => setJustAberta(null)}
                                      className="mt-2 w-full text-xs font-bold bg-slate-900 text-white rounded-lg py-1.5">Pronto</button>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                          <button onClick={() => setDossieSku(s.sku)}
                            className="justify-self-center p-1.5 rounded-lg text-slate-300 hover:bg-indigo-50 hover:text-indigo-600 transition-colors"
                            title="Ver dossiê do SKU">
                            <LineIcon className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {dossieSku && <GavetaDossie sku={dossieSku} fechar={() => setDossieSku(null)} />}
    </div>
  );
}

function GavetaDossie({ sku, fechar }: { sku: string; fechar: () => void }) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    axios.get(`/api/v1/supply/dossie?sku=${encodeURIComponent(sku)}`)
      .then((r) => setD(r.data)).finally(() => setLoading(false));
  }, [sku]);
  const tendIcon = (t: string) =>
    t === 'CRESCIMENTO' ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
    t === 'DECLINIO' ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
    <Minus className="w-4 h-4 text-slate-400" />;
  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-white z-40 shadow-2xl overflow-y-auto" style={{ fontVariantNumeric: 'tabular-nums' }}>
        <div className="sticky top-0 bg-white border-b border-slate-100 px-5 py-4 flex items-start justify-between">
          <div className="min-w-0">
            <div className="text-[10px] font-black uppercase tracking-widest text-indigo-500">Dossiê do SKU</div>
            <div className="text-sm font-black text-slate-900 truncate">{d?.descricao || sku}</div>
            <div className="text-[10px] font-bold text-slate-300">{sku}</div>
          </div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100"><X className="w-4 h-4" /></button>
        </div>
        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400"><Loader2 className="w-5 h-5 animate-spin" /></div>
        ) : (
          <div className="p-5 space-y-6">
            {d?.fva && (
              <div>
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Quem acertou mais · último mês fechado</div>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: 'Humano', val: d.fva.aderencia_humano, key: 'HUMANO' },
                    { label: 'IA', val: d.fva.aderencia_ia, key: 'IA' },
                    { label: 'Ano passado', val: d.fva.aderencia_naive, key: 'ANO PASSADO' },
                  ].map((x) => {
                    const venceu = d.fva.vencedor === x.key;
                    return (
                      <div key={x.key} className={`rounded-xl px-3 py-2 border ${venceu ? 'border-emerald-300 bg-emerald-50' : 'border-slate-100 bg-slate-50'}`}>
                        <div className="text-[10px] font-bold text-slate-400">{x.label}</div>
                        <div className={`text-lg font-black ${venceu ? 'text-emerald-700' : 'text-slate-500'}`}>{x.val != null ? fmtPct(x.val) : '—'}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            <div>
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Histórico · 24 meses</div>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={d?.grafico || []} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="mes" tick={{ fontSize: 9, fill: '#94a3b8' }} interval={3} />
                    <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} />
                    <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #e2e8f0' }} />
                    <Line type="monotone" dataKey="vendido" stroke="#4f46e5" strokeWidth={2} dot={false} name="Vendido" />
                    <Line type="monotone" dataKey="faturado" stroke="#94a3b8" strokeWidth={1.5} dot={false} name="Faturado" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
            {d?.plurianual && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Trajetória plurianual</div>
                  <div className="flex items-center gap-3 text-[10px] font-bold">
                    <span className="flex items-center gap-1 text-slate-500">Vol {tendIcon(d.plurianual.tendencia_volume)}</span>
                    <span className="flex items-center gap-1 text-slate-500">PMV {tendIcon(d.plurianual.tendencia_pmv)}</span>
                  </div>
                </div>
                {d.plurianual.alerta_historico && (
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-amber-600 bg-amber-50 rounded-lg px-3 py-1.5">
                    <AlertTriangle className="w-3.5 h-3.5" /> {d.plurianual.alerta_historico}
                  </div>
                )}
                <div className="space-y-1">
                  {(d.plurianual.anos || []).map((a: any, i: number) => {
                    const ehRecente = i >= (d.plurianual.anos.length - 3);
                    return (
                      <div key={a.ano} className={`grid grid-cols-4 gap-2 px-3 py-1.5 rounded-lg text-xs ${ehRecente ? 'bg-slate-50 font-bold' : 'opacity-50'}`}>
                        <div className="text-slate-500">{a.ano}</div>
                        <div className="text-right text-slate-700">{fmtCx(a.vendido_cx)} cx</div>
                        <div className="text-right text-slate-700">R$ {a.pmv.toFixed(2)}</div>
                        <div className="text-right text-slate-400">{a.razoes_sociais} cli</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}