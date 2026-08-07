import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Download, X, Rocket, Unlock,
  TrendingUp, TrendingDown, Minus, Bot, AlertTriangle, Loader2, LineChart as LineIcon,
  Layers, Check, LayoutGrid, ClipboardList,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import VisaoGeralMarketing from './VisaoGeralMarketing';
import DossieInferior from './Dossieinferior';

/* =====================================================================
   DEMANDA FINAL — a sala de guerra do C-Level.
   Tabela consolidada (Final editável) + toggles de comparação + gaveta com
   COMPARADOR DE CENÁRIOS (adotar qualquer camada como Final).
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`;
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

type Visao = 'caixas' | 'financeiro';

export default function DemandaFinal() {
  const [aba, setAba] = useState<'geral' | 'preenchimento'>('geral');
  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <div className="shrink-0 bg-white border-b border-slate-200 px-6 pt-3">
        <div className="flex items-center gap-1">
          <button onClick={() => setAba('geral')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors
              ${aba === 'geral' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <LayoutGrid className="w-4 h-4" /> Visão Geral
          </button>
          <button onClick={() => setAba('preenchimento')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors
              ${aba === 'preenchimento' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <ClipboardList className="w-4 h-4" /> Consolidação
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {aba === 'geral'
          ? <VisaoGeralMarketing prefixoApi="/api/v1/dashboard" />
          : <ConsolidacaoFinal />}
      </div>
    </div>
  );
}

function ConsolidacaoFinal() {
  const [dados, setDados] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, number>>({});
  const [dossieSku, setDossieSku] = useState<string | null>(null);
  const [visao, setVisao] = useState<Visao>('caixas');
  const [cmpOrcamento, setCmpOrcamento] = useState(false);
  const [cmpAnterior, setCmpAnterior] = useState(false);
  const [busca, setBusca] = useState('');

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/dashboard/tabela');
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[] = dados?.meses || [];
  const publicado: boolean = dados?.publicado;
  const aguardandoUpstream: boolean = dados?.aguardando_upstream;
  const bloqueado: boolean = publicado || aguardandoUpstream;
  const fin = visao === 'financeiro';

  const valorCelula = (sku: string, mes: string, original: number) => {
    const k = `${sku}|${mes}`;
    return k in edits ? edits[k] : original;
  };
  const setCelula = (sku: string, mes: string, v: number) => {
    setEdits((e) => ({ ...e, [`${sku}|${mes}`]: Math.max(0, Math.round(v || 0)) }));
  };

  const totaisVivos = useMemo(() => {
    const vol: Record<string, number> = {}; const fat: Record<string, number> = {};
    meses.forEach((m) => { vol[m] = 0; fat[m] = 0; });
    dados?.categorias?.forEach((cat: any) =>
      cat.segmentos.forEach((seg: any) =>
        seg.skus.forEach((s: any) =>
          meses.forEach((m) => {
            const cel = s.meses[m]; if (!cel) return;
            const v = valorCelula(s.sku, m, cel.final);
            vol[m] += v; fat[m] += v * (cel.pmv || 0);
          })
        )
      )
    );
    return { vol, fat };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [sku, mes] = k.split('|');
        return { sku, mes_projetado: mes, novo_volume: v };
      });
      await axios.post('/api/v1/dashboard/salvar', { ajustes });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally { setSalvando(false); }
  };

  const publicar = async () => {
    if (!confirm('Publicar o S&OP? Isto congela o número final do ciclo.')) return;
    try { await axios.post('/api/v1/dashboard/publicar', {}); await carregar(); }
    catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao publicar.'); }
  };

  const toggle = (nome: string) =>
    setAbertas((p) => { const n = new Set(p); n.has(nome) ? n.delete(nome) : n.add(nome); return n; });

  const exibe = (cxVal: number, pmv: number) => fin ? fmtRs(cxVal * (pmv || 0)) : fmtCx(cxVal);

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Consolidando o plano…</div>;
  }

  return (
    <div className="h-full overflow-y-auto bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      {/* CABEÇALHO */}
      <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
        <div className="px-6 pt-5 pb-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-black text-slate-900 tracking-tight">Demanda Final · S&OP</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · consolide e publique o número oficial
              {aguardandoUpstream && <span className="ml-2 text-orange-500 font-bold">· aguardando Supply Review congelar</span>}
              {!aguardandoUpstream && publicado && <span className="ml-2 text-emerald-600 font-bold">· publicado</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={salvar} disabled={!temEdicoes || salvando || bloqueado}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !bloqueado ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            {!publicado ? (
              <button onClick={publicar}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-emerald-600 text-white hover:bg-emerald-700">
                <Rocket className="w-4 h-4" /> Publicar S&OP
              </button>
            ) : (
              <button onClick={async () => { if (confirm('Reabrir o ciclo publicado?')) { await axios.post('/api/v1/dashboard/reabrir', {}); carregar(); } }}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
                <Unlock className="w-4 h-4" /> Reabrir
              </button>
            )}
            <a href="/api/v1/dashboard/exportar"
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
              <Download className="w-4 h-4" /> CSV
            </a>
          </div>
        </div>

        {/* TOGGLES */}
        <div className="px-6 pb-2 flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5">
            <button onClick={() => setVisao('caixas')}
              className={`px-3 py-1 text-xs font-black rounded-md ${visao === 'caixas' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>Caixas</button>
            <button onClick={() => setVisao('financeiro')}
              className={`px-3 py-1 text-xs font-black rounded-md ${visao === 'financeiro' ? 'bg-slate-900 text-white' : 'text-slate-500'}`}>R$</button>
          </div>
          <button onClick={() => setCmpOrcamento((v) => !v)}
            className={`px-3 py-1 text-xs font-bold rounded-lg border ${cmpOrcamento ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 text-slate-500'}`}>vs Orçamento</button>
          <button onClick={() => setCmpAnterior((v) => !v)}
            className={`px-3 py-1 text-xs font-bold rounded-lg border ${cmpAnterior ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 text-slate-500'}`}>vs Ciclo Anterior</button>
        </div>

        {/* TOTAIS */}
        <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr))` }}>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total consolidado</div>
          {meses.map((m) => {
            const val = fin ? totaisVivos.fat[m] : totaisVivos.vol[m];
            const orc = dados?.totais?.orcamento?.[m] || 0;
            const antVol = dados?.totais?.anterior?.[m] || 0;
            const vsOrc = fin && orc > 0 ? (totaisVivos.fat[m] - orc) / orc : 0;
            const vsAnt = antVol > 0 ? (totaisVivos.vol[m] - antVol) / antVol : 0;
            return (
              <div key={m} className="bg-slate-50 rounded-lg px-3 py-2">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
                <div className="text-sm font-black text-slate-900">{fin ? fmtRs(val) : `${fmtCx(val)} cx`}</div>
                {cmpOrcamento && fin && orc > 0 && (
                  <div className={`text-[10px] font-bold ${vsOrc >= 0 ? 'text-slate-400' : 'text-rose-500'}`}>{vsOrc >= 0 ? '+' : ''}{fmtPct(vsOrc)} orç.</div>
                )}
                {cmpAnterior && antVol > 0 && (
                  <div className={`text-[10px] font-bold ${vsAnt >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>{vsAnt >= 0 ? '+' : ''}{fmtPct(vsAnt)} ciclo ant.</div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {aguardandoUpstream && (
        <div className="mx-6 mt-4 mb-2 flex items-center gap-3 rounded-xl bg-orange-50 border border-orange-200 px-4 py-3">
          <div className="w-2 h-2 rounded-full bg-orange-400 shrink-0 animate-pulse" />
          <div>
            <div className="text-xs font-black text-orange-700">Consolidação bloqueada</div>
            <div className="text-[11px] font-medium text-orange-600">Supply Review ainda não congelou o plano deste ciclo. Os números ficam visíveis, mas não editáveis até o bastão ser passado.</div>
          </div>
        </div>
      )}

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
                    acc + seg.skus.reduce((a: number, s: any) => {
                      const cel = s.meses[m]; if (!cel) return a;
                      const v = valorCelula(s.sku, m, cel.final);
                      return a + (fin ? v * (cel.pmv || 0) : v);
                    }, 0), 0);
                  return <div key={m} className="text-right text-sm font-bold text-slate-500">{fin ? fmtRs(soma) : fmtCx(soma)}</div>;
                })}
                <div />
              </button>

              {catAberta && cat.segmentos.map((seg: any) => (
                <div key={seg.nome} className="ml-4 mt-1">
                  <div className="px-3 py-1.5 text-[11px] font-black uppercase tracking-wider text-slate-400">{seg.nome}</div>
                  {seg.skus.map((s: any) => (
                    <div key={s.sku} className="grid gap-2 px-3 py-1.5 items-center hover:bg-white rounded-lg group"
                      style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
                      <div className="min-w-0 pl-2">
                        <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                        <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                      </div>
                      {meses.map((m) => {
                        const cel = s.meses[m];
                        if (!cel) return <div key={m} />;
                        const val = valorCelula(s.sku, m, cel.final);
                        const editado = `${s.sku}|${m}` in edits;
                        const vsAnt = cmpAnterior && cel.final_anterior ? val - cel.final_anterior : null;
                        return (
                          <div key={m} className="text-right">
                            <input type="number" value={val} disabled={bloqueado}
                              onChange={(e) => setCelula(s.sku, m, parseInt(e.target.value))}
                              className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                                ${editado ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                                ${bloqueado ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`} />
                            <div className="text-[9px] font-bold text-slate-300 pr-2">
                              {fin ? fmtRs(val * (cel.pmv || 0)) : `PMV ${cel.pmv}`}
                              {vsAnt != null && <span className={vsAnt >= 0 ? 'text-emerald-500' : 'text-rose-400'}> · {vsAnt >= 0 ? '+' : ''}{fmtCx(vsAnt)}</span>}
                            </div>
                          </div>
                        );
                      })}
                      <button onClick={() => setDossieSku(s.sku)}
                        className="justify-self-center p-1.5 rounded-lg text-slate-300 hover:bg-indigo-50 hover:text-indigo-600 transition-colors"
                        title="Cenários e dossiê">
                        <Layers className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {dossieSku && <GavetaDossie sku={dossieSku} publicado={publicado} fechar={() => setDossieSku(null)} recarregar={carregar} />}
    </div>
  );
}

/* GAVETA — dossiê + COMPARADOR DE CENÁRIOS (o signature do Dashboard) */
function GavetaDossie({ sku, publicado, fechar, recarregar }: { sku: string; publicado: boolean; fechar: () => void; recarregar: () => void }) {
  const [cenarios, setCenarios] = useState<any[]>([]);

  useEffect(() => {
    axios.get(`/api/v1/dashboard/dossie?sku=${encodeURIComponent(sku)}`)
      .then((r) => setCenarios(r.data?.cenarios || []))
      .catch(() => setCenarios([]));
  }, [sku]);

  const adotar = async (mes: string, camada: string) => {
    if (publicado) { alert('S&OP publicado. Reabra para editar.'); return; }
    try {
      await axios.post(`/api/v1/dashboard/adotar-cenario?sku=${encodeURIComponent(sku)}&mes=${mes}&camada=${camada}`);
      recarregar();
      const r = await axios.get(`/api/v1/dashboard/dossie?sku=${encodeURIComponent(sku)}`);
      setCenarios(r.data?.cenarios || []);
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao adotar cenário.'); }
  };

  const CAMADAS = [
    { key: 'ia',       label: 'IA',       cor: 'text-slate-500'  },
    { key: 'topdown',  label: 'TopDown',  cor: 'text-indigo-600' },
    { key: 'bottomup', label: 'BottomUp', cor: 'text-violet-600' },
    { key: 'meta',     label: 'Meta',     cor: 'text-purple-600' },
    { key: 'supply',   label: 'Supply',   cor: 'text-fuchsia-600'},
  ];

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-2xl bg-white z-40 shadow-2xl overflow-y-auto">

        {/* COMPARADOR DE CENÁRIOS — exclusivo do Dashboard */}
        {cenarios.length > 0 && (
          <div className="px-5 pt-5">
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
              <Layers className="w-3.5 h-3.5" /> Comparador de cenários — adote uma camada como Final
            </div>
            <div className="space-y-3 mb-4">
              {cenarios.map((c: any) => (
                <div key={c.mes} className="rounded-xl border border-slate-100 bg-slate-50 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-black text-slate-700">{mesLabel(c.mes)}</span>
                    <span className="text-[10px] font-bold text-slate-400">
                      Final atual: <span className="text-slate-800 font-black">{fmtCx(c.final)} cx</span>
                      {c.dispersao > 0.15 && (
                        <span className="ml-2 text-amber-600">· dispersão {fmtPct(c.dispersao)}</span>
                      )}
                    </span>
                  </div>
                  <div className="grid grid-cols-5 gap-1.5">
                    {CAMADAS.map((cam) => {
                      const val = c[cam.key] || 0;
                      const ehFinal = val === c.final && val > 0;
                      return (
                        <button key={cam.key} onClick={() => adotar(c.mes, cam.key)}
                          disabled={publicado}
                          title={publicado ? 'S&OP publicado' : `Adotar ${cam.label} como Final`}
                          className={`rounded-lg px-2 py-1.5 border text-left transition-colors
                            ${ehFinal ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white hover:border-indigo-300 hover:bg-indigo-50'}
                            ${publicado ? 'opacity-50 cursor-not-allowed' : ''}`}>
                          <div className={`text-[9px] font-black uppercase ${cam.cor}`}>{cam.label}</div>
                          <div className="text-xs font-black text-slate-700">{fmtCx(val)}</div>
                          {ehFinal && <div className="text-[8px] font-black text-emerald-600 flex items-center gap-0.5"><Check className="w-2.5 h-2.5" /> adotado</div>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* DOSSIÊ CANÔNICO — mesmo componente das outras 4 telas */}
        <DossieInferior
          prefixoApi="/api/v1/dashboard"
          tipo="sku"
          id={sku}
          titulo={sku}
          onFechar={fechar}
        />
      </div>
    </>
  );
}