import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Lock, Unlock, Download,
  Loader2, LineChart as LineIcon, LayoutGrid, ClipboardList,
  Search, X, Zap, TrendingUp, ShoppingCart, CheckCircle2,
} from 'lucide-react';
import DossieInferior from './Dossieinferior';
import VisaoGeralMarketing from './VisaoGeralMarketing';

/* =====================================================================
   DEMANDA IRRESTRITA
   Compara: IA × Marketing (vol_topdown) × Comercial (vol_bottomup)
   Edita:   vol_bottomup  — propaga para Metas → Supply → Final
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
    .format(Math.round(n || 0));
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

/* Divergência relativa entre dois volumes */
function divLabel(a: number, b: number): 'alta' | 'media' | null {
  if (!a || !b) return null;
  const d = Math.abs(a - b) / Math.max(a, b);
  if (d >= 0.4) return 'alta';
  if (d >= 0.2) return 'media';
  return null;
}

/* Atalho: copiar uma das fontes para o bottomup */
type Fonte = 'ia' | 'topdown' | 'bottomup';

/* ── COMPONENTE RAIZ ─────────────────────────────────────────────── */
export default function DemandaIrrestrita() {
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
            <ClipboardList className="w-4 h-4" /> Demanda Irrestrita
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {aba === 'geral'
          ? <VisaoGeralMarketing prefixoApi="/api/v1/irrestrita" />
          : <PreenchimentoIrrestrita />}
      </div>
    </div>
  );
}

/* ── ABA PREENCHIMENTO ───────────────────────────────────────────── */
function PreenchimentoIrrestrita() {
  const [dados,    setDados]    = useState<any>(null);
  const [loading,  setLoading]  = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [abertas,  setAbertas]  = useState<Set<string>>(new Set());
  // edits: `${sku}|${mes}` → novo volume do bottomup
  const [edits,    setEdits]    = useState<Record<string, number>>({});
  const [dossieAberto, setDossieAberto] = useState<{ sku: string; descricao: string } | null>(null);
  const [busca,    setBusca]    = useState('');

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/irrestrita/tabela');
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[]   = dados?.meses || [];
  const congelada          = Boolean(dados?.congelada);
  const propria            = Boolean(dados?.congelada_propria);
  const aguardando         = Boolean(dados?.aguardando_upstream);
  const souAdmin           = dados?.sou_admin === true;

  /* Valor corrente do bottomup de uma célula */
  const valBU = (sku: string, mes: string, original: number) => {
    const k = `${sku}|${mes}`;
    return k in edits ? edits[k] : (original || 0);
  };

  const setCelula = (sku: string, mes: string, v: number) =>
    setEdits(p => ({ ...p, [`${sku}|${mes}`]: Math.max(0, Math.round(v || 0)) }));

  /* Adotar uma fonte inteira para um SKU (todos os meses de uma vez) */
  const adotarFonte = (sku: string, fonte: Fonte, skuData: any) => {
    const novo: Record<string, number> = {};
    meses.forEach(m => {
      const cel = skuData.meses[m]; if (!cel) return;
      novo[`${sku}|${m}`] = Math.round(cel[fonte] || 0);
    });
    setEdits(p => ({ ...p, ...novo }));
  };

  /* Totalizadores vivos */
  const totaisVivos = useMemo(() => {
    const vol:     Record<string, number> = {};
    const fat:     Record<string, number> = {};
    const ia:      Record<string, number> = {};
    const topdown: Record<string, number> = {};
    meses.forEach(m => { vol[m] = 0; fat[m] = 0; ia[m] = 0; topdown[m] = 0; });
    dados?.categorias?.forEach((cat: any) =>
      cat.segmentos.forEach((seg: any) =>
        seg.skus.forEach((s: any) =>
          meses.forEach(m => {
            const cel = s.meses[m]; if (!cel) return;
            const v = valBU(s.sku, m, cel.bottomup);
            vol[m]     += v;
            fat[m]     += v * (cel.pmv || 0);
            ia[m]      += cel.ia || 0;
            topdown[m] += cel.topdown || 0;
          })
        )
      )
    );
    return { vol, fat, ia, topdown };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  /* Filtro de busca */
  const categoriasFiltradas = useMemo(() => {
    const q = busca.trim().toLowerCase();
    if (!q) return dados?.categorias || [];
    return (dados?.categorias || []).map((cat: any) => {
      const catMatch = cat.nome.toLowerCase().includes(q);
      const segs = cat.segmentos.map((seg: any) => {
        const skus = seg.skus.filter((s: any) =>
          catMatch ||
          s.sku.toLowerCase().includes(q) ||
          s.descricao.toLowerCase().includes(q)
        );
        return skus.length > 0 ? { ...seg, skus } : null;
      }).filter(Boolean);
      return segs.length > 0 ? { ...cat, segmentos: segs } : null;
    }).filter(Boolean);
  }, [dados, busca]);

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [sku, mes] = k.split('|');
        return { sku, mes_projetado: mes, novo_volume: v };
      });
      await axios.post('/api/v1/irrestrita/salvar', { ajustes });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally { setSalvando(false); }
  };

  const congelarEtapa = async () => {
    if (!confirm('Congelar Demanda Irrestrita? A etapa Metas Comercial será liberada.')) return;
    try {
      if (temEdicoes) await salvar();
      await axios.post('/api/v1/irrestrita/congelar', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao congelar.'); }
  };

  const reabrirEtapa = async () => {
    if (!confirm('Reabrir Demanda Irrestrita?')) return;
    try {
      await axios.post('/api/v1/irrestrita/reabrir', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao reabrir.'); }
  };

  const exportarExcel = async () => {
    if (temEdicoes) await salvar();
    try {
      const r = await axios.get('/api/v1/irrestrita/exportar', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `irrestrita_${dados?.ciclo?.replace('/', '_') || 'ciclo'}.xlsx`;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); window.URL.revokeObjectURL(url);
    } catch { alert('Erro ao gerar o Excel.'); }
  };

  const toggle = (id: string) =>
    setAbertas(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando demandas…
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      <div className="flex-1 min-h-0 overflow-y-auto">

        {/* ── CABEÇALHO STICKY ─────────────────────────────────────── */}
        <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
          <div className="px-6 pt-5 pb-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-lg font-black text-slate-900 tracking-tight">Demanda Irrestrita</h1>
              <p className="text-xs font-medium text-slate-400">
                Ciclo {dados?.ciclo} · compare IA, Marketing e Comercial · edite o Comercial (vol_bottomup)
                {aguardando && <span className="ml-2 text-orange-500 font-bold">· aguardando Marketing congelar</span>}
                {!aguardando && propria && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap justify-end shrink-0">
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input type="text" value={busca} onChange={e => setBusca(e.target.value)}
                  placeholder="Buscar categoria, SKU ou produto…"
                  className="pl-8 pr-7 py-2 text-xs rounded-xl border border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:outline-none focus:border-indigo-400 w-64" />
                {busca && (
                  <button onClick={() => setBusca('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600">
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              <button onClick={salvar} disabled={!temEdicoes || salvando || congelada}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                  ${temEdicoes && !congelada ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
                {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
              </button>
              {souAdmin && (
                propria ? (
                  <button onClick={reabrirEtapa}
                    className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-amber-500 text-white hover:bg-amber-600">
                    <Unlock className="w-4 h-4" /> Reabrir etapa
                  </button>
                ) : (
                  <button onClick={congelarEtapa} disabled={aguardando}
                    className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black
                      ${aguardando ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-emerald-600 text-white hover:bg-emerald-700'}`}>
                    <Lock className="w-4 h-4" /> Congelar etapa
                  </button>
                )
              )}
              <button onClick={exportarExcel}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
                <Download className="w-4 h-4" /> Excel
              </button>
            </div>
          </div>

          {/* Aviso de upstream bloqueado */}
          {aguardando && (
            <div className="mx-6 mb-3 flex items-center gap-3 rounded-xl bg-orange-50 border border-orange-200 px-4 py-3">
              <div className="w-2 h-2 rounded-full bg-orange-400 shrink-0 animate-pulse" />
              <div>
                <div className="text-xs font-black text-orange-700">Preenchimento bloqueado</div>
                <div className="text-[11px] text-orange-600">Demanda Marketing ainda não congelou o plano deste ciclo.</div>
              </div>
            </div>
          )}

          {/* Totalizadores por mês */}
          <div className="px-6 pb-3 grid gap-2"
            style={{ gridTemplateColumns: `200px repeat(${meses.length}, minmax(140px, 1fr))` }}>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total do plano</div>
            {meses.map(m => (
              <div key={m} className="bg-slate-50 rounded-xl px-3 py-2 space-y-1">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
                <div className="flex items-center gap-1.5">
                  <Zap className="w-3 h-3 text-violet-400 shrink-0" />
                  <span className="text-[11px] font-bold text-slate-500">{fmtCx(totaisVivos.ia[m])} cx</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <TrendingUp className="w-3 h-3 text-indigo-400 shrink-0" />
                  <span className="text-[11px] font-bold text-slate-500">{fmtCx(totaisVivos.topdown[m])} cx</span>
                </div>
                <div className="h-px bg-slate-200 my-1" />
                <div className="flex items-center gap-1.5">
                  <ShoppingCart className="w-3 h-3 text-emerald-500 shrink-0" />
                  <span className="text-sm font-black text-slate-900">{fmtCx(totaisVivos.vol[m])} cx</span>
                </div>
                <div className="text-[10px] font-bold text-emerald-600">{fmtRs(totaisVivos.fat[m])}</div>
              </div>
            ))}
          </div>

          {/* Legenda das fontes */}
          <div className="px-6 pb-3 flex items-center gap-5 text-[10px] font-black uppercase tracking-widest">
            <div className="flex items-center gap-1.5 text-violet-500"><Zap className="w-3 h-3" /> IA</div>
            <div className="flex items-center gap-1.5 text-indigo-500"><TrendingUp className="w-3 h-3" /> Marketing</div>
            <div className="flex items-center gap-1.5 text-emerald-600"><ShoppingCart className="w-3 h-3" /> Comercial (editável)</div>
            <div className="ml-auto flex items-center gap-1.5 text-slate-300">
              <CheckCircle2 className="w-3 h-3" /> clique em IA/Mkt para adotar
            </div>
          </div>

          {/* Cabeçalho da tabela */}
          <div className="px-6 pb-1">
            <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
              style={{ gridTemplateColumns: `200px repeat(${meses.length}, minmax(140px, 1fr)) 40px` }}>
              <div>Categoria / Segmento / SKU</div>
              {meses.map(m => <div key={m} className="text-right">{mesLabel(m)}</div>)}
              <div />
            </div>
          </div>
        </div>

        {/* ── CORPO DA TABELA ──────────────────────────────────────── */}
        <div className="px-6 pb-8">
          {categoriasFiltradas.length === 0 && busca.trim() ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Search className="w-8 h-8 mb-2 opacity-30" />
              <div className="text-sm font-bold">Nenhum resultado para "{busca}"</div>
              <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">
                Limpar busca
              </button>
            </div>
          ) : (
            categoriasFiltradas.map((cat: any) => {
              const catAberta = busca.trim() ? true : abertas.has(cat.nome);

              /* totais da categoria */
              const somaCat = (m: string, campo: Fonte | 'bu') =>
                cat.segmentos.reduce((acc: number, seg: any) =>
                  acc + seg.skus.reduce((a: number, s: any) => {
                    const cel = s.meses[m]; if (!cel) return a;
                    return a + (campo === 'bu' ? valBU(s.sku, m, cel.bottomup) : (cel[campo] || 0));
                  }, 0), 0);

              return (
                <div key={cat.nome} className="mb-1">
                  {/* ── LINHA CATEGORIA ── */}
                  <button onClick={() => toggle(cat.nome)}
                    className="w-full grid gap-2 px-3 py-2.5 items-center bg-white rounded-lg border border-slate-100 hover:border-slate-200 transition-colors"
                    style={{ gridTemplateColumns: `200px repeat(${meses.length}, minmax(140px, 1fr)) 40px` }}>
                    <div className="flex items-center gap-2 min-w-0">
                      {catAberta
                        ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
                        : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                      <span className="font-black text-slate-800 text-sm truncate">{cat.nome}</span>
                    </div>
                    {meses.map(m => (
                      <div key={m} className="text-right space-y-0.5">
                        <div className="flex items-center justify-end gap-1">
                          <Zap className="w-2.5 h-2.5 text-violet-300" />
                          <span className="text-[10px] font-bold text-slate-400">{fmtCx(somaCat(m, 'ia'))}</span>
                        </div>
                        <div className="flex items-center justify-end gap-1">
                          <TrendingUp className="w-2.5 h-2.5 text-indigo-300" />
                          <span className="text-[10px] font-bold text-slate-400">{fmtCx(somaCat(m, 'topdown'))}</span>
                        </div>
                        <div className="flex items-center justify-end gap-1">
                          <ShoppingCart className="w-2.5 h-2.5 text-emerald-400" />
                          <span className="text-xs font-black text-slate-700">{fmtCx(somaCat(m, 'bu'))} cx</span>
                        </div>
                      </div>
                    ))}
                    <div />
                  </button>

                  {catAberta && cat.segmentos.map((seg: any) => {
                    const segId = `${cat.nome}>${seg.nome}`;
                    const segAberto = busca.trim() ? true : abertas.has(segId);

                    const somaSeg = (m: string, campo: Fonte | 'bu') =>
                      seg.skus.reduce((a: number, s: any) => {
                        const cel = s.meses[m]; if (!cel) return a;
                        return a + (campo === 'bu' ? valBU(s.sku, m, cel.bottomup) : (cel[campo] || 0));
                      }, 0);

                    return (
                      <div key={seg.nome} className="ml-3 mt-0.5">
                        {/* ── LINHA SEGMENTO ── */}
                        <button onClick={() => toggle(segId)}
                          className="w-full grid gap-2 px-3 py-2 items-center rounded-lg hover:bg-slate-50 transition-colors"
                          style={{ gridTemplateColumns: `200px repeat(${meses.length}, minmax(140px, 1fr)) 40px` }}>
                          <div className="flex items-center gap-1.5 min-w-0 pl-2">
                            {segAberto
                              ? <ChevronDown className="w-3.5 h-3.5 text-slate-300 shrink-0" />
                              : <ChevronRight className="w-3.5 h-3.5 text-slate-300 shrink-0" />}
                            <span className="text-[11px] font-black uppercase tracking-wider text-slate-400 truncate">{seg.nome}</span>
                          </div>
                          {meses.map(m => (
                            <div key={m} className="text-right space-y-0.5">
                              <div className="text-[10px] font-bold text-violet-400">{fmtCx(somaSeg(m, 'ia'))} <span className="text-slate-300">ia</span></div>
                              <div className="text-[10px] font-bold text-indigo-400">{fmtCx(somaSeg(m, 'topdown'))} <span className="text-slate-300">mkt</span></div>
                              <div className="text-xs font-black text-emerald-600">{fmtCx(somaSeg(m, 'bu'))} cx</div>
                            </div>
                          ))}
                          <div />
                        </button>

                        {segAberto && seg.skus.map((s: any) => {
                          const skuAberto = dossieAberto?.sku === s.sku;

                          /* semáforo: maior divergência entre IA e BU em qualquer mês */
                          const maxDiv = meses.reduce<'alta' | 'media' | null>((acc, m) => {
                            const cel = s.meses[m]; if (!cel) return acc;
                            const d = divLabel(cel.ia, valBU(s.sku, m, cel.bottomup));
                            return d === 'alta' ? 'alta' : (d === 'media' && acc !== 'alta' ? 'media' : acc);
                          }, null);
                          const barraColor = maxDiv === 'alta' ? 'bg-rose-400' : maxDiv === 'media' ? 'bg-amber-400' : 'bg-transparent';

                          return (
                            <React.Fragment key={s.sku}>
                              {/* ── LINHA SKU ── */}
                              <div className="grid gap-2 px-3 py-2 items-start hover:bg-white rounded-lg group relative ml-4"
                                style={{ gridTemplateColumns: `200px repeat(${meses.length}, minmax(140px, 1fr)) 40px` }}>
                                <div className={`absolute left-4 top-2 bottom-2 w-0.5 rounded ${barraColor}`} />

                                {/* Nome do SKU + botões "adotar fonte" */}
                                <div className="min-w-0 pl-3">
                                  <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                                  <div className="text-[10px] font-bold text-slate-300 mb-1">{s.sku}</div>
                                  {/* Atalhos para adotar uma fonte inteira */}
                                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button
                                      disabled={congelada}
                                      onClick={() => adotarFonte(s.sku, 'ia', s)}
                                      className="flex items-center gap-0.5 text-[9px] font-black px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 hover:bg-violet-200 disabled:opacity-40"
                                      title="Adotar IA para todos os meses">
                                      <Zap className="w-2.5 h-2.5" /> Adotar IA
                                    </button>
                                    <button
                                      disabled={congelada}
                                      onClick={() => adotarFonte(s.sku, 'topdown', s)}
                                      className="flex items-center gap-0.5 text-[9px] font-black px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 hover:bg-indigo-200 disabled:opacity-40"
                                      title="Adotar Marketing para todos os meses">
                                      <TrendingUp className="w-2.5 h-2.5" /> Adotar Mkt
                                    </button>
                                  </div>
                                </div>

                                {/* Células por mês */}
                                {meses.map(m => {
                                  const cel = s.meses[m];
                                  if (!cel) return <div key={m} />;
                                  const bu      = valBU(s.sku, m, cel.bottomup);
                                  const editado = `${s.sku}|${m}` in edits;
                                  const fatBU   = bu * (cel.pmv || 0);
                                  const divIA   = divLabel(cel.ia, bu);
                                  const divTD   = divLabel(cel.topdown, bu);

                                  return (
                                    <div key={m} className="space-y-1">
                                      {/* IA — somente leitura, clicável para adotar */}
                                      <button
                                        disabled={congelada}
                                        onClick={() => setCelula(s.sku, m, cel.ia || 0)}
                                        className={`w-full flex items-center justify-between gap-1 px-2 py-1 rounded text-[10px] font-bold
                                          ${divIA === 'alta' ? 'bg-rose-50 text-rose-500' : divIA === 'media' ? 'bg-amber-50 text-amber-600' : 'bg-slate-50 text-violet-500'}
                                          hover:ring-1 hover:ring-violet-300 transition-all disabled:cursor-not-allowed`}
                                        title="Clique para adotar IA neste mês">
                                        <span className="flex items-center gap-0.5"><Zap className="w-2.5 h-2.5" /> IA</span>
                                        <span>{fmtCx(cel.ia || 0)} cx</span>
                                      </button>

                                      {/* Marketing — somente leitura, clicável para adotar */}
                                      <button
                                        disabled={congelada}
                                        onClick={() => setCelula(s.sku, m, cel.topdown || 0)}
                                        className={`w-full flex items-center justify-between gap-1 px-2 py-1 rounded text-[10px] font-bold
                                          ${divTD === 'alta' ? 'bg-rose-50 text-rose-500' : divTD === 'media' ? 'bg-amber-50 text-amber-600' : 'bg-slate-50 text-indigo-500'}
                                          hover:ring-1 hover:ring-indigo-300 transition-all disabled:cursor-not-allowed`}
                                        title="Clique para adotar Marketing neste mês">
                                        <span className="flex items-center gap-0.5"><TrendingUp className="w-2.5 h-2.5" /> Mkt</span>
                                        <span>{fmtCx(cel.topdown || 0)} cx</span>
                                      </button>

                                      {/* Comercial — editável */}
                                      <div className={`rounded border transition-colors
                                        ${editado ? 'border-emerald-300 bg-emerald-50' : 'border-transparent bg-white'}`}>
                                        <div className="flex items-center gap-1 px-2 pt-1 text-[9px] font-bold text-emerald-500">
                                          <ShoppingCart className="w-2.5 h-2.5" /> Comercial
                                        </div>
                                        <input
                                          type="number"
                                          value={bu}
                                          disabled={congelada}
                                          onChange={e => setCelula(s.sku, m, parseInt(e.target.value) || 0)}
                                          className={`w-full text-right text-sm font-black px-2 pb-1 bg-transparent outline-none
                                            ${editado ? 'text-emerald-700' : 'text-slate-700'}
                                            ${congelada ? 'cursor-not-allowed opacity-60' : ''}`}
                                        />
                                        {cel.pmv > 0 && (
                                          <div className="text-right text-[9px] font-bold text-slate-300 px-2 pb-1">
                                            {fmtRs(fatBU)}
                                          </div>
                                        )}
                                      </div>

                                      {/* Referências: realizado AP e orçamento */}
                                      {(cel.realizado_ap != null || cel.orcamento != null) && (
                                        <div className="space-y-0.5 pt-0.5">
                                          {cel.realizado_ap != null && (
                                            <div className="flex items-center justify-between px-1 text-[9px] font-bold text-slate-300">
                                              <span>ano ant.</span>
                                              <span>{fmtCx(cel.realizado_ap)} cx</span>
                                            </div>
                                          )}
                                          {cel.orcamento != null && cel.orcamento > 0 && (
                                            <div className="flex items-center justify-between px-1 text-[9px] font-bold text-amber-400">
                                              <span>orçamento</span>
                                              <span>{fmtRs(cel.orcamento)}</span>
                                            </div>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}

                                {/* Botão dossiê */}
                                <button
                                  onClick={async () => {
                                    if (temEdicoes) await salvar();
                                    setDossieAberto(
                                      dossieAberto?.sku === s.sku ? null : { sku: s.sku, descricao: s.descricao }
                                    );
                                  }}
                                  className={`self-start justify-self-center mt-1 p-1.5 rounded-lg transition-colors
                                    ${skuAberto ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
                                  title="Ver dossiê do SKU">
                                  <LineIcon className="w-4 h-4" />
                                </button>
                              </div>

                              {/* Dossiê inline abaixo do SKU */}
                              {skuAberto && (
                                <DossieInferior
                                  prefixoApi="/api/v1/irrestrita"
                                  tipo="sku"
                                  id={s.sku}
                                  titulo={s.descricao}
                                  onFechar={() => setDossieAberto(null)}
                                />
                              )}
                            </React.Fragment>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              );
            })
          )}
        </div>

      </div>
    </div>
  );
}