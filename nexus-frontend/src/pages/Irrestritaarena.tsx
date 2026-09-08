import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Lock, Unlock, Download,
  Loader2, LineChart as LineIcon, LayoutGrid, ClipboardList,
  Search, X, Zap, TrendingUp, ShoppingCart, History,
} from 'lucide-react';
import DossieInferior from './Dossieinferior';
import VisaoGeralMarketing from './VisaoGeralMarketing';

/* =====================================================================
   DEMANDA IRRESTRITA
   Hierarquia: Categoria → SKU (segmento removido da visualização)
   Compara: IA × Marketing (vol_topdown) × Metas (vol_meta) × Ano Anterior
   Edita: vol_irrestrita — propaga para Supply → Final
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

/* Delta % de um valor em relação ao comercial (base) */
function delta(ref: number, base: number): { pct: number; cor: string; sinal: string } | null {
  if (!ref || !base) return null;
  const pct = ((ref - base) / base) * 100;
  const abs = Math.abs(pct);
  const cor = abs >= 20 ? (pct > 0 ? '#e11d48' : '#2563eb')
             : abs >= 8  ? (pct > 0 ? '#d97706' : '#7c3aed')
             : '#6b7280';
  return { pct, cor, sinal: pct >= 0 ? `+${pct.toFixed(0)}%` : `${pct.toFixed(0)}%` };
}

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

function PreenchimentoIrrestrita() {
  const [dados,        setDados]        = useState<any>(null);
  const [loading,      setLoading]      = useState(true);
  const [salvando,     setSalvando]     = useState(false);
  const [abertas,      setAbertas]      = useState<Set<string>>(new Set());
  const [edits,        setEdits]        = useState<Record<string, number>>({});
  const [dossieAberto, setDossieAberto] = useState<{ sku: string; descricao: string } | null>(null);
  const [busca,        setBusca]        = useState('');

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/irrestrita/tabela');
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[] = dados?.meses || [];
  const congelada        = Boolean(dados?.congelada);
  const propria          = Boolean(dados?.congelada_propria);
  const aguardando       = Boolean(dados?.aguardando_upstream);
  const souAdmin         = dados?.sou_admin === true;

  const valIrrestrita = (sku: string, mes: string, original: number) => {
    const k = `${sku}|${mes}`;
    return k in edits ? edits[k] : (original || 0);
  };
  const setCelula = (sku: string, mes: string, v: number) =>
    setEdits(p => ({ ...p, [`${sku}|${mes}`]: Math.max(0, Math.round(v || 0)) }));

  /* Adotar uma fonte inteira (todos os meses do SKU de uma vez) */
  const adotarFonte = (sku: string, fonte: 'ia' | 'topdown' | 'meta' | 'realizado_ap', skuData: any) => {
    const novo: Record<string, number> = {};
    meses.forEach(m => {
      const cel = skuData.meses[m];
      if (!cel) return;
      const val = fonte === 'realizado_ap' ? cel.realizado_ap?.cx : cel[fonte];
      if (val != null) novo[`${sku}|${m}`] = Math.round(val);
    });
    setEdits(p => ({ ...p, ...novo }));
  };

  /* Flatten de SKUs por categoria — ignora segmento na apresentação */
  const categoriasFlatadas = useMemo(() => {
    const q = busca.trim().toLowerCase();
    return (dados?.categorias || []).map((cat: any) => {
      /* achata todos os SKUs de todos os segmentos */
      const todosSkus = cat.segmentos.flatMap((seg: any) => seg.skus);
      const skusFilt  = q
        ? todosSkus.filter((s: any) =>
            s.sku.toLowerCase().includes(q) ||
            s.descricao.toLowerCase().includes(q) ||
            cat.nome.toLowerCase().includes(q))
        : todosSkus;
      return skusFilt.length > 0 ? { nome: cat.nome, skus: skusFilt } : null;
    }).filter(Boolean);
  }, [dados, busca]);

  /* Totalizadores vivos */
  const totaisVivos = useMemo(() => {
    const vol: Record<string, number> = {};
    const fat: Record<string, number> = {};
    const ia:  Record<string, number> = {};
    const td:  Record<string, number> = {};
    const meta: Record<string, number> = {};
    const ap:  Record<string, number> = {};
    meses.forEach(m => { vol[m] = 0; fat[m] = 0; ia[m] = 0; td[m] = 0; meta[m] = 0; ap[m] = 0; });
    categoriasFlatadas.forEach((cat: any) =>
      cat.skus.forEach((s: any) =>
        meses.forEach(m => {
          const cel = s.meses[m]; if (!cel) return;
          const ir  = valIrrestrita(s.sku, m, cel.irrestrita ?? cel.bottomup);
          vol[m] += ir;
          fat[m] += ir * (cel.pmv || 0);
          ia[m]  += cel.ia || 0;
          td[m]  += cel.topdown || 0;
          meta[m] += cel.meta || 0;
          ap[m]  += cel.realizado_ap?.cx || 0;
        })
      )
    );
    return { vol, fat, ia, td, meta, ap };
  }, [dados, edits, meses, busca]);

  const temEdicoes = Object.keys(edits).length > 0;

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
    if (!confirm('Congelar Demanda Irrestrita? A etapa Supply será liberada.')) return;
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

  /* Largura da coluna de cada mês — varia se tem realizado_ap */
  const colMes = 'minmax(180px, 1fr)';

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      <div className="flex-1 min-h-0 overflow-y-auto">

        {/* ── CABEÇALHO STICKY ─────────────────────────────── */}
        <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
          <div className="px-6 pt-5 pb-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-lg font-black text-slate-900 tracking-tight">Demanda Irrestrita</h1>
              <p className="text-xs font-medium text-slate-400">
                Ciclo {dados?.ciclo} · selecione IA, Marketing, Metas ou Ano Anterior — ou insira manualmente
                {aguardando && <span className="ml-2 text-orange-500 font-bold">· aguardando Metas congelar</span>}
                {!aguardando && propria && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap justify-end shrink-0">
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input type="text" value={busca} onChange={e => setBusca(e.target.value)}
                  placeholder="Buscar categoria, SKU ou produto…"
                  className="pl-8 pr-7 py-2 text-xs rounded-xl border border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:outline-none focus:border-indigo-400 w-60" />
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

          {aguardando && (
            <div className="mx-6 mb-3 flex items-center gap-3 rounded-xl bg-orange-50 border border-orange-200 px-4 py-3">
              <div className="w-2 h-2 rounded-full bg-orange-400 shrink-0 animate-pulse" />
              <div>
                <div className="text-xs font-black text-orange-700">Preenchimento bloqueado</div>
                <div className="text-[11px] text-orange-600">Metas Comercial ainda não congelou o plano deste ciclo.</div>
              </div>
            </div>
          )}

          {/* Totalizadores por mês */}
          <div className="px-6 pb-3 grid gap-2"
            style={{ gridTemplateColumns: `220px repeat(${meses.length}, ${colMes})` }}>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">
              Total da carteira
            </div>
            {meses.map(m => {
              const ir  = totaisVivos.vol[m];
              const fat = totaisVivos.fat[m];
              const ia  = totaisVivos.ia[m];
              const td  = totaisVivos.td[m];
              const meta = totaisVivos.meta[m];
              // Totais do ano anterior: vêm do backend (soma direta da fato_vendas)
              // Nunca re-precificado pelo PMV atual — é o valor real que aconteceu.
              const ap    = dados?.totais?.[m]?.cx_ap ?? 0;
              const fatAP = dados?.totais?.[m]?.rs_ap ?? 0;
              // Receita do orçamento: soma de cel.orcamento
              const fatOrc = categoriasFlatadas.reduce((acc: number, cat: any) =>
                acc + cat.skus.reduce((a: number, s: any) => {
                  const cel = s.meses[m]; if (!cel || !cel.orcamento) return a;
                  return a + cel.orcamento;
                }, 0), 0);

              const dIA = delta(ia, ir);
              const dTD = delta(td, ir);
              const dMeta = delta(meta, ir);
              const dAP = ap > 0 ? delta(ap, ir) : null;
              return (
                <div key={m} className="bg-slate-50 rounded-xl px-3 py-2 space-y-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>

                  {/* Linha de referências em caixas */}
                  <div className="grid grid-cols-4 gap-1">
                    {/* IA */}
                    <div className="flex flex-col items-center bg-violet-50 rounded-lg px-1 py-1.5">
                      <span className="text-[8px] font-black uppercase tracking-wider text-violet-400 flex items-center gap-0.5 mb-0.5"><Zap className="w-2 h-2"/>IA</span>
                      <span className="text-[11px] font-black text-slate-700">{fmtCx(ia)}</span>
                      {dIA && <span className="text-[9px] font-black" style={{ color: dIA.cor }}>{dIA.sinal}</span>}
                    </div>
                    {/* Mkt */}
                    <div className="flex flex-col items-center bg-indigo-50 rounded-lg px-1 py-1.5">
                      <span className="text-[8px] font-black uppercase tracking-wider text-indigo-400 flex items-center gap-0.5 mb-0.5"><TrendingUp className="w-2 h-2"/>Mkt</span>
                      <span className="text-[11px] font-black text-slate-700">{fmtCx(td)}</span>
                      {dTD && <span className="text-[9px] font-black" style={{ color: dTD.cor }}>{dTD.sinal}</span>}
                    </div>
                    {/* Metas */}
                    <div className="flex flex-col items-center bg-emerald-50 rounded-lg px-1 py-1.5">
                      <span className="text-[8px] font-black uppercase tracking-wider text-emerald-500 flex items-center gap-0.5 mb-0.5"><ShoppingCart className="w-2 h-2"/>Meta</span>
                      <span className="text-[11px] font-black text-slate-700">{fmtCx(meta)}</span>
                      {dMeta && <span className="text-[9px] font-black" style={{ color: dMeta.cor }}>{dMeta.sinal}</span>}
                    </div>
                    {/* Ano anterior */}
                    <div className="flex flex-col items-center bg-slate-100 rounded-lg px-1 py-1.5">
                      <span className="text-[8px] font-black uppercase tracking-wider text-slate-400 flex items-center gap-0.5 mb-0.5"><History className="w-2 h-2"/>Ant.</span>
                      <span className="text-[11px] font-black text-slate-600">{ap > 0 ? fmtCx(ap) : '—'}</span>
                      {dAP && ap > 0 && <span className="text-[9px] font-black" style={{ color: dAP.cor }}>{dAP.sinal}</span>}
                    </div>
                  </div>

                  {/* Divisor */}
                  <div className="h-px bg-slate-200" />

                  {/* Irrestrita — volume decidido */}
                  <div>
                    <div className="text-[8px] font-black uppercase tracking-wider text-emerald-500 flex items-center gap-0.5 mb-0.5">
                      <ShoppingCart className="w-2 h-2" /> Irrestrita
                    </div>
                    <div className="text-sm font-black text-slate-900">{fmtCx(ir)} <span className="text-[10px] font-bold text-slate-400">cx</span></div>
                    <div className="text-[10px] font-bold text-emerald-600">{fmtRs(fat)}</div>
                  </div>

                  {/* Referências de receita */}
                  {(fatAP > 0 || fatOrc > 0) && (
                    <div className="space-y-0.5 pt-0.5 border-t border-slate-100">
                      {fatAP > 0 && (
                        <div className="flex items-center justify-between text-[9px] font-bold text-slate-400">
                          <span className="flex items-center gap-0.5"><History className="w-2.5 h-2.5" /> real. ano ant.</span>
                          <span>{fmtRs(fatAP)}</span>
                        </div>
                      )}
                      {fatOrc > 0 && (
                        <div className="flex items-center justify-between text-[9px] font-bold text-amber-500">
                          <span>orçamento</span>
                          <span>{fmtRs(fatOrc)}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Cabeçalho da tabela */}
          <div className="px-6 pb-1">
            <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
              style={{ gridTemplateColumns: `220px repeat(${meses.length}, ${colMes}) 44px` }}>
              <div>Categoria / SKU</div>
              {meses.map(m => (
                <div key={m} className="grid grid-cols-4 gap-1 text-center">
                  <span className="text-violet-400 flex items-center justify-center gap-0.5"><Zap className="w-2.5 h-2.5" />IA</span>
                  <span className="text-indigo-400 flex items-center justify-center gap-0.5"><TrendingUp className="w-2.5 h-2.5" />Mkt</span>
                  <span className="text-emerald-500 flex items-center justify-center gap-0.5"><ShoppingCart className="w-2.5 h-2.5" />Meta</span>
                  <span className="text-slate-400 flex items-center justify-center gap-0.5"><History className="w-2.5 h-2.5" />Ano ant.</span>
                </div>
              ))}
              <div />
            </div>
          </div>
        </div>

        {/* ── CORPO ────────────────────────────────────────── */}
        <div className="px-6 pb-8">
          {categoriasFlatadas.length === 0 && busca.trim() ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Search className="w-8 h-8 mb-2 opacity-30" />
              <div className="text-sm font-bold">Nenhum resultado para "{busca}"</div>
              <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">
                Limpar busca
              </button>
            </div>
          ) : categoriasFlatadas.map((cat: any) => {
            const catAberta = busca.trim() ? true : abertas.has(cat.nome);

            /* Soma totais da categoria por campo */
            const somacat = (m: string, campo: string) =>
              cat.skus.reduce((acc: number, s: any) => {
                const cel = s.meses[m]; if (!cel) return acc;
                if (campo === 'irrestrita') return acc + valIrrestrita(s.sku, m, cel.irrestrita ?? cel.bottomup);
                if (campo === 'realizado_ap') return acc + (cel.realizado_ap?.cx || 0);
                return acc + (cel[campo] || 0);
              }, 0);

            return (
              <div key={cat.nome} className="mb-1">

                {/* ── LINHA CATEGORIA ── */}
                <button onClick={() => toggle(cat.nome)}
                  className="w-full grid gap-2 px-3 py-3 items-center bg-white rounded-xl border border-slate-100 hover:border-slate-200 transition-colors"
                  style={{ gridTemplateColumns: `220px repeat(${meses.length}, ${colMes}) 44px` }}>
                  <div className="flex items-center gap-2 min-w-0">
                    {catAberta
                      ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
                      : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                    <div className="min-w-0">
                      <div className="font-black text-slate-800 text-sm truncate">{cat.nome}</div>
                      <div className="text-[10px] font-bold text-slate-400">{cat.skus.length} SKUs</div>
                    </div>
                  </div>
                  {meses.map(m => {
                    const ir  = somacat(m, 'irrestrita');
                    const ia  = somacat(m, 'ia');
                    const td  = somacat(m, 'topdown');
                    const meta = somacat(m, 'meta');
                    const ap  = somacat(m, 'realizado_ap');
                    const dIA = delta(ia, ir);
                    const dTD = delta(td, ir);
                    const dMeta = delta(meta, ir);
                    const dAP = delta(ap, ir);
                    return (
                      <div key={m} className="grid grid-cols-4 gap-1 items-end">
                        {/* IA */}
                        <div className="text-center">
                          <div className="text-[10px] font-bold text-slate-500">{fmtCx(ia)}</div>
                          {dIA && <div className="text-[9px] font-black" style={{ color: dIA.cor }}>{dIA.sinal}</div>}
                        </div>
                        {/* Mkt */}
                        <div className="text-center">
                          <div className="text-[10px] font-bold text-slate-500">{fmtCx(td)}</div>
                          {dTD && <div className="text-[9px] font-black" style={{ color: dTD.cor }}>{dTD.sinal}</div>}
                        </div>
                        {/* Metas */}
                        <div className="text-center">
                          <div className="text-[10px] font-bold text-slate-500">{fmtCx(meta)}</div>
                          {dMeta && <div className="text-[9px] font-black" style={{ color: dMeta.cor }}>{dMeta.sinal}</div>}
                        </div>
                        {/* Ano ant */}
                        <div className="text-center">
                          <div className="text-[10px] font-bold text-slate-500">{ap > 0 ? fmtCx(ap) : '—'}</div>
                          {dAP && ap > 0 && <div className="text-[9px] font-black" style={{ color: dAP.cor }}>{dAP.sinal}</div>}
                        </div>
                      </div>
                    );
                  })}
                  {/* Irrestrita separada — fora do grid de refs */}
                  <div />
                </button>
                {/* Irrestrita da categoria abaixo do botão — totais */}
                {!catAberta && (
                  <div className="grid gap-2 px-3 pt-0.5 pb-2"
                    style={{ gridTemplateColumns: `220px repeat(${meses.length}, ${colMes}) 44px` }}>
                    <div />
                    {meses.map(m => (
                      <div key={m} className="text-center">
                        <div className="text-sm font-black text-slate-900">{fmtCx(somacat(m, 'irrestrita'))} <span className="text-[10px] text-slate-400">cx</span></div>
                        <div className="text-[10px] font-bold text-emerald-600">
                          {fmtRs(cat.skus.reduce((acc: number, s: any) => {
                            const cel = s.meses[m]; if (!cel) return acc;
                            return acc + valIrrestrita(s.sku, m, cel.irrestrita ?? cel.bottomup) * (cel.pmv || 0);
                          }, 0))}
                        </div>
                      </div>
                    ))}
                    <div />
                  </div>
                )}

                {/* ── LINHAS SKU ── */}
                {catAberta && cat.skus.map((s: any) => {
                  const skuAberto  = dossieAberto?.sku === s.sku;

                  /* semáforo: divergência máxima IA vs Irrestrita em qualquer mês */
                  const maxDiv = meses.reduce<'alta' | 'media' | null>((acc, m) => {
                    const cel = s.meses[m]; if (!cel) return acc;
                    const ir  = valIrrestrita(s.sku, m, cel.irrestrita ?? cel.bottomup);
                    const pct = cel.ia && ir ? Math.abs(cel.ia - ir) / Math.max(cel.ia, ir) : 0;
                    const d   = pct >= 0.4 ? 'alta' : pct >= 0.2 ? 'media' : null;
                    return d === 'alta' ? 'alta' : (d === 'media' && acc !== 'alta' ? 'media' : acc);
                  }, null);
                  const faixaColor = maxDiv === 'alta' ? '#fca5a5' : maxDiv === 'media' ? '#fcd34d' : 'transparent';

                  return (
                    <React.Fragment key={s.sku}>
                      <div
                        className="grid gap-2 px-3 py-2 items-center hover:bg-white rounded-xl group relative ml-3 mt-0.5"
                        style={{ gridTemplateColumns: `220px repeat(${meses.length}, ${colMes}) 44px` }}>
                        {/* Faixa de divergência */}
                        <div className="absolute left-3 top-2 bottom-2 w-0.5 rounded"
                          style={{ background: faixaColor }} />

                        {/* Nome + atalhos de adoção em massa */}
                        <div className="min-w-0 pl-3">
                          <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                          <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                          <div className="flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button disabled={congelada}
                              onClick={() => adotarFonte(s.sku, 'ia', s)}
                              className="text-[9px] font-black px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 hover:bg-violet-200 disabled:opacity-40 flex items-center gap-0.5">
                              <Zap className="w-2.5 h-2.5" /> IA
                            </button>
                            <button disabled={congelada}
                              onClick={() => adotarFonte(s.sku, 'topdown', s)}
                              className="text-[9px] font-black px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 hover:bg-indigo-200 disabled:opacity-40 flex items-center gap-0.5">
                              <TrendingUp className="w-2.5 h-2.5" /> Mkt
                            </button>
                            <button disabled={congelada}
                              onClick={() => adotarFonte(s.sku, 'meta', s)}
                              className="text-[9px] font-black px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-600 hover:bg-emerald-200 disabled:opacity-40 flex items-center gap-0.5">
                              <ShoppingCart className="w-2.5 h-2.5" /> Meta
                            </button>
                            <button disabled={congelada}
                              onClick={() => adotarFonte(s.sku, 'realizado_ap', s)}
                              className="text-[9px] font-black px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:opacity-40 flex items-center gap-0.5">
                              <History className="w-2.5 h-2.5" /> Ano ant.
                            </button>
                          </div>
                        </div>

                        {/* Células por mês */}
                        {meses.map(m => {
                          const cel    = s.meses[m];
                          if (!cel) return <div key={m} />;
                          const ir     = valIrrestrita(s.sku, m, cel.irrestrita ?? cel.bottomup);
                          const editado = `${s.sku}|${m}` in edits;
                          const dIA    = delta(cel.ia || 0, ir);
                          const dTD    = delta(cel.topdown || 0, ir);
                          const apCx   = cel.realizado_ap?.cx ?? null;
                          const apRs   = cel.realizado_ap?.rs ?? null;
                          const dMeta  = delta(cel.meta || 0, ir);
                          const dAP    = apCx ? delta(apCx, ir) : null;

                          return (
                            <div key={m} className="space-y-1">
                              {/* ── LINHA DE REFERÊNCIAS ── 4 colunas clicáveis */}
                              <div className="grid grid-cols-4 gap-1">
                                {/* IA */}
                                <button disabled={congelada}
                                  onClick={() => setCelula(s.sku, m, cel.ia || 0)}
                                  className="flex flex-col items-center py-1 px-1 rounded-lg bg-violet-50 hover:bg-violet-100 hover:ring-1 hover:ring-violet-300 transition-all disabled:cursor-not-allowed group/btn"
                                  title="Adotar IA neste mês">
                                  <span className="text-[10px] font-black text-slate-600">{fmtCx(cel.ia || 0)}</span>
                                  {dIA
                                    ? <span className="text-[9px] font-black" style={{ color: dIA.cor }}>{dIA.sinal}</span>
                                    : <span className="text-[9px] text-slate-300">—</span>}
                                </button>
                                {/* Mkt */}
                                <button disabled={congelada}
                                  onClick={() => setCelula(s.sku, m, cel.topdown || 0)}
                                  className="flex flex-col items-center py-1 px-1 rounded-lg bg-indigo-50 hover:bg-indigo-100 hover:ring-1 hover:ring-indigo-300 transition-all disabled:cursor-not-allowed"
                                  title="Adotar Marketing neste mês">
                                  <span className="text-[10px] font-black text-slate-600">{fmtCx(cel.topdown || 0)}</span>
                                  {dTD
                                    ? <span className="text-[9px] font-black" style={{ color: dTD.cor }}>{dTD.sinal}</span>
                                    : <span className="text-[9px] text-slate-300">—</span>}
                                </button>
                                {/* Metas */}
                                <button disabled={congelada}
                                  onClick={() => setCelula(s.sku, m, cel.meta || 0)}
                                  className="flex flex-col items-center py-1 px-1 rounded-lg bg-emerald-50 hover:bg-emerald-100 hover:ring-1 hover:ring-emerald-300 transition-all disabled:cursor-not-allowed"
                                  title="Adotar Metas neste mês">
                                  <span className="text-[10px] font-black text-slate-600">{fmtCx(cel.meta || 0)}</span>
                                  {dMeta
                                    ? <span className="text-[9px] font-black" style={{ color: dMeta.cor }}>{dMeta.sinal}</span>
                                    : <span className="text-[9px] text-slate-300">—</span>}
                                </button>
                                {/* Ano anterior */}
                                <button disabled={congelada || !apCx}
                                  onClick={() => apCx && setCelula(s.sku, m, apCx)}
                                  className="flex flex-col items-center py-1 px-1 rounded-lg bg-slate-50 hover:bg-slate-100 hover:ring-1 hover:ring-slate-300 transition-all disabled:cursor-not-allowed"
                                  title="Adotar Ano Anterior neste mês">
                                  <span className="text-[10px] font-black text-slate-500">
                                    {apCx ? fmtCx(apCx) : '—'}
                                  </span>
                                  {dAP && apCx
                                    ? <span className="text-[9px] font-black" style={{ color: dAP.cor }}>{dAP.sinal}</span>
                                    : <span className="text-[9px] text-slate-300">—</span>}
                                </button>
                              </div>

                              {/* ── IRRESTRITA — input principal ── */}
                              <div className={`rounded-xl border-2 transition-all
                                ${editado ? 'border-emerald-400 bg-emerald-50 shadow-sm shadow-emerald-100' : 'border-slate-100 bg-white'}`}>
                                <input
                                  type="number"
                                  value={ir}
                                  disabled={congelada}
                                  onChange={e => setCelula(s.sku, m, parseInt(e.target.value) || 0)}
                                  className={`w-full text-center text-base font-black pt-2 pb-1 bg-transparent outline-none rounded-xl
                                    ${editado ? 'text-emerald-700' : 'text-slate-800'}
                                    ${congelada ? 'cursor-not-allowed opacity-60' : 'focus:ring-2 focus:ring-emerald-300'}`}
                                />
                                {cel.pmv > 0 && (
                                  <div className="text-center text-[10px] font-bold text-slate-400 pb-1.5">
                                    {fmtRs(ir * (cel.pmv || 0))}
                                  </div>
                                )}
                                {/* Orçamento como referência discreta */}
                                {cel.orcamento != null && cel.orcamento > 0 && (
                                  <div className="text-center text-[9px] font-bold text-amber-500 pb-1">
                                    orç {fmtRs(cel.orcamento)}
                                  </div>
                                )}
                              </div>
                            </div>
                          );
                        })}

                        {/* Botão dossiê */}
                        <button
                          onClick={async () => {
                            if (temEdicoes) await salvar();
                            setDossieAberto(dossieAberto?.sku === s.sku ? null : { sku: s.sku, descricao: s.descricao });
                          }}
                          className={`self-start justify-self-center mt-1 p-2 rounded-xl transition-colors
                            ${skuAberto ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
                          title="Ver dossiê do SKU">
                          <LineIcon className="w-4 h-4" />
                        </button>
                      </div>

                      {/* Dossiê inline */}
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

      </div>
    </div>
  );
}