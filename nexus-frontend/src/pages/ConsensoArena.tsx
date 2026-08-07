import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Download, X, Lock, Unlock, ShieldCheck,
  TrendingUp, TrendingDown, Minus, Bot, AlertTriangle, Loader2,
  LineChart as LineIcon, LayoutGrid, ClipboardList, Search,
} from 'lucide-react';
import VisaoGeralMarketing from './VisaoGeralMarketing';
import DossieInferior from './Dossieinferior';

/* =====================================================================
   METAS COMERCIAL — duas abas:
   1) Visão Geral: mesma visão analítica das outras telas.
   2) Preenchimento: árvore de 5 níveis (gerente→coordenador→vendedor→
      cliente/razão→produto). Cadeados por responsável. Dossiê em gaveta.

   Particularidades vs outras telas:
   • Árvore recursiva de 5 níveis com RLS (cada um vê sua carteira).
   • Chave de edits: razao||sku||mes (inclui razão social do cliente).
   • Cadeado individual: cada vendedor bloqueia sua carteira + Admin
     pode reabrir. Congelar a ETAPA é separado.
   • Dossiê: gaveta lateral (não inline) — a árvore já é muito densa.
   • Referência da IA exibida como "IA X cx" (vol_ia).
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number | null) => n == null ? '—' : `${((n) * 100).toFixed(1)}%`;
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

const INDENT: Record<string, string> = {
  gerente: 'pl-0', coordenador: 'pl-4', vendedor: 'pl-8', cliente: 'pl-12', produto: 'pl-16',
};

/* ── COMPONENTE RAIZ ─────────────────────────────────────────────── */
export default function MetasComercial() {
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
            <ClipboardList className="w-4 h-4" /> Preenchimento
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {aba === 'geral'
          ? <VisaoGeralMarketing prefixoApi="/api/v1/carteira" />
          : <PreenchimentoMetas />}
      </div>
    </div>
  );
}

/* ── ABA PREENCHIMENTO ───────────────────────────────────────────── */
function PreenchimentoMetas() {
  const [dados,          setDados]          = useState<any>(null);
  const [loading,        setLoading]        = useState(true);
  const [salvando,       setSalvando]       = useState(false);
  const [abertas,        setAbertas]        = useState<Set<string>>(new Set());
  const [edits,          setEdits]          = useState<Record<string, number>>({});
  const [dossieSku,      setDossieSku]      = useState<string | null>(null);
  const [painelCadeados, setPainelCadeados] = useState(false);
  const [busca,          setBusca]          = useState('');

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/carteira/tabela');
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[]       = dados?.meses || [];
  const congeladaEtapa        = Boolean(dados?.etapa_congelada);
  const minhaCongelada        = Boolean(dados?.minha_carteira_congelada);
  const aguardandoUpstream    = Boolean(dados?.aguardando_upstream);
  const bloqueado             = congeladaEtapa || minhaCongelada || aguardandoUpstream;

  const keyOf       = (razao: string, sku: string, mes: string) => `${razao}||${sku}||${mes}`;
  const valorCelula = (razao: string, sku: string, mes: string, original: number) => {
    const k = keyOf(razao, sku, mes);
    return k in edits ? edits[k] : (original || 0);
  };
  const setCelula = (razao: string, sku: string, mes: string, v: number) =>
    setEdits(e => ({ ...e, [keyOf(razao, sku, mes)]: Math.max(0, Math.round(v || 0)) }));

  /* Totalizadores — percorre árvore recursivamente */
  const totaisVivos = useMemo(() => {
    const vol: Record<string, number> = {};
    const fat: Record<string, number> = {};
    const orc: Record<string, number> = {};
    meses.forEach(m => { vol[m] = 0; fat[m] = 0; orc[m] = 0; });
    const walk = (node: any, razaoCtx: string | null) => {
      if (node.tipo === 'produto') {
        meses.forEach(m => {
          const cel = node.meses[m]; if (!cel) return;
          const v = valorCelula(razaoCtx || '', node.sku, m, cel.meta);
          vol[m] += v;
          fat[m] += v * (cel.pmv || 0);
          orc[m] += cel.orcamento || 0;
        });
        return;
      }
      const novoCtx = node.tipo === 'cliente' ? node.nome : razaoCtx;
      (node.subRows || []).forEach((f: any) => walk(f, novoCtx));
    };
    (dados?.arvore || []).forEach((g: any) => walk(g, null));
    return { vol, fat, orc };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [razao_social, sku, mes] = k.split('||');
        return { razao_social, sku, mes_projetado: mes, novo_volume: v };
      });
      await axios.post('/api/v1/carteira/salvar', { ajustes });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally { setSalvando(false); }
  };

  const bloquearMinha = async () => {
    if (!confirm('Bloquear sua carteira? Você não poderá mais editar suas metas neste ciclo.')) return;
    try {
      await axios.post('/api/v1/carteira/bloquear', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao bloquear.'); }
  };

  const toggle = (id: string) =>
    setAbertas(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  /* Filtro de busca — percorre árvore e retorna só nós com match */
  const arvoreVisivelOuCompleta = useMemo(() => {
    const q = busca.trim().toLowerCase();
    if (!q) return dados?.arvore || [];

    const filtraNos = (nodes: any[]): any[] =>
      nodes.map(node => {
        if (node.tipo === 'produto') {
          return (
            node.sku.toLowerCase().includes(q) ||
            node.descricao.toLowerCase().includes(q)
          ) ? node : null;
        }
        const matchNome = node.nome.toLowerCase().includes(q);
        const subFiltrados = filtraNos(node.subRows || []);
        if (matchNome || subFiltrados.length > 0) {
          return { ...node, subRows: matchNome ? (node.subRows || []) : subFiltrados };
        }
        return null;
      }).filter(Boolean);

    return filtraNos(dados?.arvore || []);
  }, [dados, busca]);

  const exportarExcel = async () => {
    if (temEdicoes) await salvar();
    try {
      const resp = await axios.get('/api/v1/carteira/exportar', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([resp.data]));
      const a = document.createElement('a');
      a.href = url; a.download = `metas_${dados?.ciclo?.replace('/','_') || 'ciclo'}.xlsx`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch { alert('Erro ao gerar o Excel.'); }
  };

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando sua carteira…
    </div>;
  }

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>

      {/* CABEÇALHO STICKY */}
      <div className="shrink-0 bg-white border-b border-slate-200">
        <div className="px-6 pt-4 pb-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-base font-black text-slate-900">Metas Comercial</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · distribua a meta na sua carteira
              {aguardandoUpstream && <span className="ml-2 text-orange-500 font-bold">· aguardando Demanda Comercial congelar</span>}
              {!aguardandoUpstream && minhaCongelada && <span className="ml-2 text-emerald-600 font-bold">· sua carteira bloqueada</span>}
              {!aguardandoUpstream && congeladaEtapa && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            {/* Busca */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              <input type="text" value={busca} onChange={e => setBusca(e.target.value)}
                placeholder="Buscar cliente, SKU ou produto…"
                className="pl-8 pr-7 py-2 text-xs rounded-xl border border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:outline-none focus:border-indigo-400 w-56" />
              {busca && (
                <button onClick={() => setBusca('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {/* Cadeados */}
            <button onClick={() => setPainelCadeados(true)}
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
              <ShieldCheck className="w-4 h-4" /> Cadeados
            </button>
            {/* Salvar */}
            <button onClick={salvar} disabled={!temEdicoes || salvando || bloqueado}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !bloqueado ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            {/* Bloquear minha carteira */}
            {!minhaCongelada && !dados?.sou_admin && (
              <button onClick={bloquearMinha}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-emerald-600 text-white hover:bg-emerald-700">
                <Lock className="w-4 h-4" /> Bloquear minha carteira
              </button>
            )}
            {/* Excel */}
            <button onClick={exportarExcel}
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
              <Download className="w-4 h-4" /> Excel
            </button>
          </div>
        </div>

        {/* Totalizadores por mês */}
        <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr))` }}>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total da carteira</div>
          {meses.map(m => (
            <div key={m} className="bg-slate-50 rounded-lg px-3 py-2">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
              {totaisVivos.orc[m] > 0 && (
                <div className="text-[9px] font-bold text-amber-500">orç {fmtRs(totaisVivos.orc[m])}</div>
              )}
              <div className="text-[10px] font-bold text-indigo-500">{fmtRs(totaisVivos.fat[m])}</div>
              <div className="text-sm font-black text-slate-900">{fmtCx(totaisVivos.vol[m])} <span className="text-[10px] font-bold text-slate-400">cx</span></div>
            </div>
          ))}
        </div>

        {aguardandoUpstream && (
          <div className="mx-6 mb-3 flex items-center gap-3 rounded-xl bg-orange-50 border border-orange-200 px-4 py-3">
            <div className="w-2 h-2 rounded-full bg-orange-400 shrink-0 animate-pulse" />
            <div>
              <div className="text-xs font-black text-orange-700">Preenchimento bloqueado</div>
              <div className="text-[11px] font-medium text-orange-600">Demanda Comercial ainda não congelou o plano deste ciclo. Sua carteira fica visível, mas não editável até o bastão ser passado.</div>
            </div>
          </div>
        )}

        {/* Cabeçalho da tabela */}
        <div className="px-6 pb-1">
          <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
            style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
            <div>Hierarquia / Cliente / SKU</div>
            {meses.map(m => <div key={m} className="text-right">{mesLabel(m)}</div>)}
            <div />
          </div>
        </div>
      </div>

      {/* ÁRVORE */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-8">
        {arvoreVisivelOuCompleta.length === 0 && busca.trim() ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <Search className="w-8 h-8 mb-2 opacity-30" />
            <div className="text-sm font-bold">Nenhum resultado para "{busca}"</div>
            <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">
              Limpar busca
            </button>
          </div>
        ) : (
          arvoreVisivelOuCompleta.map((g: any) => (
            <NoArvore key={g.nome} node={g} razaoCtx={null} nivel={0}
              meses={meses} abertas={abertas} toggle={toggle}
              valorCelula={valorCelula} setCelula={setCelula}
              bloqueado={bloqueado} edits={edits}
              setDossieSku={setDossieSku}
              idPath={g.nome}
              buscaAtiva={!!busca.trim()} />
          ))
        )}
      </div>

      {/* GAVETA DOSSIÊ */}
      {dossieSku && (
        <GavetaDossie sku={dossieSku} fechar={() => setDossieSku(null)} />
      )}

      {/* PAINEL DE CADEADOS */}
      {painelCadeados && (
        <PainelCadeados fechar={() => setPainelCadeados(false)} recarregar={carregar} />
      )}
    </div>
  );
}

/* ── NÓ RECURSIVO DA ÁRVORE ──────────────────────────────────────── */
function NoArvore({ node, razaoCtx, nivel, meses, abertas, toggle, valorCelula, setCelula,
  bloqueado, edits, setDossieSku, idPath, buscaAtiva }: any) {

  if (node.tipo === 'produto') {
    const razao = razaoCtx || '';
    return (
      <div className="grid gap-2 px-3 py-1.5 items-center hover:bg-white rounded-lg group"
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
        <div className={`min-w-0 ${INDENT.produto}`}>
          <div className="text-xs font-bold text-slate-700 truncate">{node.descricao}</div>
          <div className="text-[10px] font-bold text-slate-300">{node.sku}</div>
        </div>
        {meses.map((m: string) => {
          const cel = node.meses[m];
          if (!cel) return <div key={m} />;
          const val    = valorCelula(razao, node.sku, m, cel.meta);
          const editado = `${razao}||${node.sku}||${m}` in edits;
          const fatPrev = val && cel.pmv ? val * cel.pmv : null;
          const orcPrev = cel.orcamento ?? null;
          return (
            <div key={m} className="text-right">
              {orcPrev != null && orcPrev > 0 && (
                <div className="text-[9px] font-bold text-amber-500 pr-2">orç {fmtRs(orcPrev)}</div>
              )}
              <div className="text-[9px] font-bold text-indigo-400 pr-2 mb-0.5">
                {fatPrev != null ? fmtRs(fatPrev) : '—'}
              </div>
              <input type="number" value={val} disabled={bloqueado}
                onChange={e => setCelula(razao, node.sku, m, parseInt(e.target.value))}
                className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                  ${editado ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                  ${bloqueado ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`} />
              <div className="text-[9px] font-bold text-slate-300 pr-2">IA {fmtCx(cel.ia)} cx</div>
            </div>
          );
        })}
        <button onClick={() => setDossieSku(node.sku)}
          className="justify-self-center p-1.5 rounded-lg text-slate-300 hover:bg-indigo-50 hover:text-indigo-600 transition-colors"
          title="Ver dossiê do SKU">
          <LineIcon className="w-4 h-4" />
        </button>
      </div>
    );
  }

  /* Nó de agrupamento (gerente, coordenador, vendedor, cliente) */
  const aberta    = buscaAtiva || abertas.has(idPath);
  const novoCtx   = node.tipo === 'cliente' ? node.nome : razaoCtx;

  const somaMes = (m: string): number => {
    let s = 0;
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') {
        s += valorCelula(rz || '', n.sku, m, n.meses[m]?.meta || 0); return;
      }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((f: any) => walk(f, c));
    };
    walk(node, razaoCtx);
    return s;
  };

  const fatMes = (m: string): number => {
    let f = 0;
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') {
        const cel = n.meses[m]; if (!cel) return;
        f += valorCelula(rz || '', n.sku, m, cel.meta || 0) * (cel.pmv || 0); return;
      }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((sub: any) => walk(sub, c));
    };
    walk(node, razaoCtx);
    return f;
  };

  return (
    <div className="mb-0.5">
      <button onClick={() => !buscaAtiva && toggle(idPath)}
        className={`w-full grid gap-2 px-3 py-2 items-center rounded-lg transition-colors
          ${nivel === 0 ? 'bg-white border border-slate-100 hover:border-slate-200' : 'hover:bg-white'}`}
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
        <div className={`flex items-center gap-1.5 min-w-0 ${INDENT[node.tipo] || ''}`}>
          {aberta
            ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
          <span className={`truncate ${nivel === 0 ? 'font-black text-slate-800 text-sm' : node.tipo === 'cliente' ? 'font-bold text-slate-600 text-xs' : 'font-bold text-slate-500 text-xs'}`}>
            {node.nome}
          </span>
          <span className="text-[9px] font-black uppercase tracking-wider text-slate-300 ml-1 shrink-0">{node.tipo}</span>
        </div>
        {meses.map((m: string) => (
          <div key={m} className="text-right">
            <div className="text-[9px] font-bold text-indigo-400">{fmtRs(fatMes(m))}</div>
            <div className="text-xs font-bold text-slate-500">{fmtCx(somaMes(m))} cx</div>
          </div>
        ))}
        <div />
      </button>
      {aberta && (node.subRows || []).map((f: any, i: number) => (
        <NoArvore key={(f.nome || f.sku) + i} node={f} razaoCtx={novoCtx} nivel={nivel + 1}
          meses={meses} abertas={abertas} toggle={toggle}
          valorCelula={valorCelula} setCelula={setCelula}
          bloqueado={bloqueado} edits={edits}
          setDossieSku={setDossieSku}
          idPath={`${idPath}>${f.nome || f.sku}`}
          buscaAtiva={buscaAtiva} />
      ))}
    </div>
  );
}

/* ── GAVETA DE DOSSIÊ (lateral, não inline — árvore já é densa) ──── */
function GavetaDossie({ sku, fechar }: { sku: string; fechar: () => void }) {
  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-xl bg-white z-40 shadow-2xl overflow-y-auto">
        <DossieInferior
          prefixoApi="/api/v1/carteira"
          tipo="sku"
          id={sku}
          titulo={sku}
          onFechar={fechar}
        />
      </div>
    </>
  );
}

/* ── PAINEL DE CADEADOS ──────────────────────────────────────────── */
function PainelCadeados({ fechar, recarregar }: { fechar: () => void; recarregar: () => void }) {
  const [d, setD]         = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/carteira/cadeados').then(r => setD(r.data)).finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

  const reabrir = async (nome: string) => {
    if (!confirm(`Reabrir a carteira de ${nome}?`)) return;
    try {
      await axios.post('/api/v1/carteira/reabrir-cadeado', { nome_alvo: nome });
      carregar(); recarregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao reabrir.'); }
  };

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-sm bg-white z-40 shadow-2xl overflow-y-auto">
        <div className="sticky top-0 bg-white border-b border-slate-100 px-5 py-4 flex items-center justify-between">
          <div className="text-sm font-black text-slate-900">Cadeados da equipe</div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100">
            <X className="w-4 h-4" />
          </button>
        </div>
        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : (
          <div className="p-5">
            {(d?.cadeados || []).length === 0 && (
              <div className="text-center text-slate-400 text-sm py-8">Nenhuma carteira bloqueada ainda.</div>
            )}
            <div className="space-y-2">
              {(d?.cadeados || []).map((c: any) => (
                <div key={c.nome} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="text-xs font-bold text-slate-700 truncate flex items-center gap-1.5">
                      <Lock className="w-3 h-3 text-emerald-600" /> {c.nome}
                    </div>
                    <div className="text-[10px] font-bold text-slate-400">{c.nivel} · por {c.por}</div>
                  </div>
                  {(d?.sou_admin || c.nome === d?.meu_nome) && (
                    <button onClick={() => reabrir(c.nome)}
                      className="flex items-center gap-1 text-[11px] font-bold text-slate-500 hover:text-rose-600 px-2 py-1 rounded-lg hover:bg-white">
                      <Unlock className="w-3 h-3" /> Reabrir
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}