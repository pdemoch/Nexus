import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Download, X, Lock, Unlock, ShieldCheck,
  Loader2, LineChart as LineIcon, LayoutGrid, ClipboardList, Search, BarChart2,
} from 'lucide-react';
import VisaoGeralMarketing from './VisaoGeralMarketing';
import DossieInferior from './Dossieinferior';

/* =====================================================================
   METAS COMERCIAL
   Hierarquia: Gerente → Coordenador → Executivo → SKU → Cliente/Razão
   Quem preenche: Coordenador (edita no nível SKU, rateio automático
                  para clientes em tempo real; pode também editar cliente
                  individualmente como override).
   Quem trava: Gerente ou Administrador (trancam os coordenadores).
   Dossiê: gaveta lateral, vinculado ao SKU (com filtro por executivo
           no nível SKU, ou por razão social no nível cliente).
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', {
  style: 'currency', currency: 'BRL', maximumFractionDigits: 0,
}).format(Math.round(n || 0));
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  return `${nomes[parseInt(m)-1]}/${y.slice(2)}`;
};

/* Rateio por maior resto — igual ao backend */
function ratearMaiorResto(total: number, pesos: number[]): number[] {
  const soma = pesos.reduce((a, b) => a + b, 0);
  if (soma === 0 || total === 0) {
    // distribuição uniforme quando sem histórico
    const base = Math.floor(total / pesos.length);
    const resto = total - base * pesos.length;
    return pesos.map((_, i) => base + (i < resto ? 1 : 0));
  }
  const exatos = pesos.map(p => (p / soma) * total);
  const inteiros = exatos.map(Math.floor);
  const sobra = total - inteiros.reduce((a, b) => a + b, 0);
  const restos = exatos.map((e, i) => ({ idx: i, r: e - inteiros[i] }))
    .sort((a, b) => b.r - a.r);
  for (let i = 0; i < sobra; i++) inteiros[restos[i].idx]++;
  return inteiros;
}

const INDENT: Record<string, string> = {
  gerente: 'pl-0', coordenador: 'pl-4', executivo: 'pl-8',
  cliente: 'pl-12', produto: 'pl-16',
};

/* ── COMPONENTE RAIZ ─────────────────────────────────────────────── */
export default function MetasComercial() {
  const [aba, setAba] = useState<'geral' | 'preenchimento' | 'consolidado'>('geral');
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
          <button onClick={() => setAba('consolidado')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors
              ${aba === 'consolidado' ? 'border-violet-600 text-violet-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <BarChart2 className="w-4 h-4" /> Consolidado
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {aba === 'geral' && <VisaoGeralMarketing prefixoApi="/api/v1/carteira" />}
      {aba === 'preenchimento' && <PreenchimentoMetas />}
      {aba === 'consolidado' && <ConsolidadoMetas />}
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
  // edits: chave razao||sku||mes → volume (nível cliente, sempre)
  const [edits,          setEdits]          = useState<Record<string, number>>({});
  const [dossieAlvo,     setDossieAlvo]     = useState<{
    sku: string; descricao: string; razao?: string; vendedor?: string;
  } | null>(null);
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

  const meses: string[]    = dados?.meses || [];
  const congeladaEtapa     = Boolean(dados?.etapa_congelada);
  const minhaCongelada     = Boolean(dados?.minha_carteira_congelada);
  const aguardandoUpstream = Boolean(dados?.aguardando_upstream);
  const souAdmin           = dados?.sou_admin === true;
  // funcao vem como campo extra — precisamos dela para controle de cadeado
  const funcao: string     = dados?.funcao || '';
  const bloqueado          = congeladaEtapa || minhaCongelada || aguardandoUpstream;

  /* Chave de edição: sempre no nível razão social */
  const keyOf       = (razao: string, sku: string, mes: string) => `${razao}||${sku}||${mes}`;
  const valorCliente = (razao: string, sku: string, mes: string, original: number) => {
    const k = keyOf(razao, sku, mes);
    return k in edits ? edits[k] : (original || 0);
  };

  /* Edição no nível SKU do executivo: rateia pelos clientes proporcionalmente */
  const setSkuExecutivo = (
    clientesDoSku: Array<{ razao: string; mes: string; pesoHist: number; original: number }>,
    sku: string, mes: string, novoTotal: number
  ) => {
    const pesos = clientesDoSku.map(c => c.pesoHist);
    const partes = ratearMaiorResto(Math.max(0, Math.round(novoTotal)), pesos);
    setEdits(prev => {
      const next = { ...prev };
      clientesDoSku.forEach((c, i) => {
        next[keyOf(c.razao, sku, mes)] = partes[i];
      });
      return next;
    });
  };

  /* Edição direta no nível cliente (override manual) */
  const setCliente = (razao: string, sku: string, mes: string, v: number) =>
    setEdits(prev => ({ ...prev, [keyOf(razao, sku, mes)]: Math.max(0, Math.round(v || 0)) }));

  /* Soma de um SKU de um executivo num mês (para exibir no input do SKU) */
  const somaSkuExecutivo = (clientes: any[], sku: string, mes: string): number => {
    return clientes.reduce((s, cli) => {
      const prods = cli.subRows || [];
      const prod  = prods.find((p: any) => p.sku === sku);
      if (!prod || !prod.meses[mes]) return s;
      return s + valorCliente(cli.nome, sku, mes, prod.meses[mes].meta);
    }, 0);
  };

  /* Totalizadores globais */
  const totaisVivos = useMemo(() => {
    const vol: Record<string, number> = {};
    const fat: Record<string, number> = {};
    meses.forEach(m => { vol[m] = 0; fat[m] = 0; });
    const walkProd = (prod: any, razao: string) => {
      meses.forEach(m => {
        const cel = prod.meses[m]; if (!cel) return;
        const v = valorCliente(razao, prod.sku, m, cel.meta);
        vol[m] += v;
        fat[m] += v * (cel.pmv || 0);
      });
    };
    const walk = (node: any, razaoCtx: string | null) => {
      if (node.tipo === 'produto') { walkProd(node, razaoCtx || ''); return; }
      const novoCtx = node.tipo === 'cliente' ? node.nome : razaoCtx;
      (node.subRows || []).forEach((f: any) => walk(f, novoCtx));
    };
    (dados?.arvore || []).forEach((g: any) => walk(g, null));
    return { vol, fat };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  /* Salvar: envia por razão social (contrato do backend) — só a carteira do coordenador */
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

  const congelarEtapa = async () => {
    if (!confirm('Congelar Metas Comercial? A etapa Supply será liberada.')) return;
    try {
      if (temEdicoes) await salvar();
      await axios.post('/api/v1/carteira/congelar', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao congelar.'); }
  };

  const reabrirEtapa = async () => {
    if (!confirm('Reabrir Metas Comercial?')) return;
    try {
      await axios.post('/api/v1/carteira/reabrir', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao reabrir.'); }
  };

  const toggle = (id: string) =>
    setAbertas(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  /* Filtro de busca */
  const arvoreVisivelOuCompleta = useMemo(() => {
    const q = busca.trim().toLowerCase();
    if (!q) return dados?.arvore || [];
    const filtra = (nodes: any[]): any[] =>
      nodes.map(node => {
        if (node.tipo === 'produto') {
          return (node.sku.toLowerCase().includes(q) || node.descricao.toLowerCase().includes(q))
            ? node : null;
        }
        const match = node.nome.toLowerCase().includes(q);
        const sub   = filtra(node.subRows || []);
        if (match || sub.length > 0) return { ...node, subRows: match ? (node.subRows || []) : sub };
        return null;
      }).filter(Boolean);
    return filtra(dados?.arvore || []);
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
                placeholder="Buscar executivo, SKU ou cliente…"
                className="pl-8 pr-7 py-2 text-xs rounded-xl border border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:outline-none focus:border-indigo-400 w-56" />
              {busca && (
                <button onClick={() => setBusca('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {/* Cadeados — só Gerente e Admin */}
            {(souAdmin || funcao === 'Gerente') && (
              <button onClick={() => setPainelCadeados(true)}
                className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
                <ShieldCheck className="w-4 h-4" /> Cadeados
              </button>
            )}
            {/* Salvar */}
            <button onClick={salvar} disabled={!temEdicoes || salvando || bloqueado}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !bloqueado ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            {/* Congelar/Reabrir etapa — só Gerente e Admin */}
            {(souAdmin || funcao === 'Gerente') && (
              congeladaEtapa ? (
                <button onClick={reabrirEtapa}
                  className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-amber-500 text-white hover:bg-amber-600">
                  <Unlock className="w-4 h-4" /> Reabrir etapa
                </button>
              ) : (
                <button onClick={congelarEtapa} disabled={aguardandoUpstream}
                  className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black
                    ${aguardandoUpstream ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-emerald-600 text-white hover:bg-emerald-700'}`}>
                  <Lock className="w-4 h-4" /> Congelar etapa
                </button>
              )
            )}
            {/* Excel — somente a carteira do coordenador logado */}
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
              <div className="text-[11px] font-medium text-orange-600">Demanda Comercial ainda não congelou o plano deste ciclo.</div>
            </div>
          </div>
        )}

        {/* Cabeçalho da tabela */}
        <div className="px-6 pb-1">
          <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
            style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
            <div>Hierarquia / Executivo / SKU / Cliente</div>
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
            <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">Limpar busca</button>
          </div>
        ) : (
          arvoreVisivelOuCompleta.map((g: any) => (
            <NoArvore key={g.nome} node={g} nivel={0}
              meses={meses} abertas={abertas} toggle={toggle}
              valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
              somaSkuExecutivo={somaSkuExecutivo}
              bloqueado={bloqueado} edits={edits}
              setDossieAlvo={setDossieAlvo}
              dossieAlvo={dossieAlvo}
              idPath={g.nome}
              buscaAtiva={!!busca.trim()} />
          ))
        )}
      </div>

      {/* GAVETA DOSSIÊ */}


      {/* PAINEL DE CADEADOS */}
      {painelCadeados && (
        <PainelCadeados fechar={() => setPainelCadeados(false)} recarregar={carregar} />
      )}
    </div>
  );
}

/* ── NÓ RECURSIVO DA ÁRVORE ──────────────────────────────────────── */
function NoArvore({ node, nivel, meses, abertas, toggle,
  valorCliente, setSkuExecutivo, setCliente, somaSkuExecutivo,
  bloqueado, edits, setDossieAlvo, dossieAlvo, idPath, buscaAtiva }: any) {

  const buscaAtv = buscaAtiva;
  const aberta   = buscaAtv || abertas.has(idPath);

  /* ── NÓ PRODUTO — renderizado dentro do nível EXECUTIVO (vendedor) ── */
  /* Este nó representa um SKU agregado dos clientes do executivo.       */
  /* O input edita o total do SKU e rateia para os clientes abaixo.      */
  if (node.tipo === 'produto_executivo') {
    const { sku, descricao, clientes } = node;
    return (
      <div className="mb-0.5">
        {/* Linha do SKU — input editável, dispara rateio */}
        <div className="grid gap-2 px-3 py-1.5 items-center hover:bg-indigo-50/40 rounded-lg group"
          style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
          <div className={`min-w-0 ${INDENT.produto}`}>
            <div className="text-xs font-bold text-slate-700 truncate">{descricao}</div>
            <div className="text-[10px] font-bold text-slate-300">{sku}</div>
          </div>
          {meses.map((m: string) => {
            const totalSku  = somaSkuExecutivo(clientes, sku, m);
            const cel0      = clientes[0]?.subRows?.find((p: any) => p.sku === sku)?.meses[m];
            const pmv       = cel0?.pmv || 0;
            const ia        = clientes.reduce((s: number, cli: any) => {
              const p = (cli.subRows || []).find((pr: any) => pr.sku === sku);
              return s + (p?.meses[m]?.ia || 0);
            }, 0);
            const temEdit   = clientes.some((cli: any) =>
              `${cli.nome}||${sku}||${m}` in edits
            );
            const clientesInfo = clientes.map((cli: any) => {
              const prod = (cli.subRows || []).find((p: any) => p.sku === sku);
              return {
                razao: cli.nome,
                mes: m,
                pesoHist: prod?.meses[m]?.peso_historico ?? 0,
                original: prod?.meses[m]?.meta ?? 0,
              };
            });
            return (
              <div key={m} className="text-right">
                <div className="text-[9px] font-bold text-indigo-400 pr-2 mb-0.5">
                  {pmv ? fmtRs(totalSku * pmv) : '—'}
                </div>
                <input
                  type="number"
                  value={totalSku}
                  disabled={bloqueado}
                  onChange={e => setSkuExecutivo(clientesInfo, sku, m, parseInt(e.target.value) || 0)}
                  className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                    ${temEdit ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                    ${bloqueado ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
                />
                <div className="text-[9px] font-bold text-slate-300 pr-2">IA {fmtCx(ia)} cx</div>
              </div>
            );
          })}
          {/* Botão dossiê — nível executivo */}
          <button
            onClick={() => setDossieAlvo(prev =>
              prev?.sku === sku && !prev?.razao ? null : { sku, descricao, vendedor: node.executivoNome }
            )}
            className={`justify-self-center p-1.5 rounded-lg transition-colors
              ${dossieAlvo?.sku === sku && !dossieAlvo?.razao ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
            title="Ver dossiê do SKU">
            <LineIcon className="w-4 h-4" />
          </button>
        </div>

        {/* Linhas dos clientes — somente leitura com rateio calculado + override manual */}
        {aberta && clientes.map((cli: any) => {
          const prod = (cli.subRows || []).find((p: any) => p.sku === sku);
          if (!prod) return null;
          return (
            <div key={cli.nome}
              className="grid gap-2 px-3 py-1 items-center hover:bg-slate-50 rounded-lg"
              style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
              <div className={`min-w-0 ${INDENT.cliente}`}>
                <div className="text-[11px] font-bold text-slate-500 truncate">{cli.nome}</div>
                <div className="text-[9px] font-bold text-slate-300">razão social</div>
              </div>
              {meses.map((m: string) => {
                const cel     = prod.meses[m];
                if (!cel) return <div key={m} />;
                const val     = valorCliente(cli.nome, sku, m, cel.meta);
                const editado = `${cli.nome}||${sku}||${m}` in edits;
                return (
                  <div key={m} className="text-right">
                    <input
                      type="number"
                      value={val}
                      disabled={bloqueado}
                      onChange={e => setCliente(cli.nome, sku, m, parseInt(e.target.value) || 0)}
                      className={`w-full text-right text-xs font-bold rounded-md px-2 py-1 border transition-colors
                        ${editado ? 'border-violet-300 bg-violet-50 text-violet-700' : 'border-transparent bg-transparent text-slate-500'}
                        ${bloqueado ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-100 focus:border-violet-400 focus:bg-white focus:outline-none'}`}
                    />
                  </div>
                );
              })}
              {/* Botão dossiê nível cliente */}
              <button
                onClick={() => setDossieAlvo(prev =>
                  prev?.sku === sku && prev?.razao === cli.nome ? null : { sku, descricao: prod.descricao, razao: cli.nome }
                )}
                className={`justify-self-center p-1.5 rounded-lg transition-colors
                  ${dossieAlvo?.sku === sku && dossieAlvo?.razao === cli.nome ? 'bg-violet-100 text-violet-600' : 'text-slate-200 hover:bg-violet-50 hover:text-violet-600'}`}
                title="Ver dossiê por cliente">
                <LineIcon className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })}

        {/* Dossiê inline — nível cliente (abre abaixo da linha do cliente selecionado) */}
        {dossieAlvo?.sku === sku && dossieAlvo?.razao && (() => {
          const cliAlvo = clientes.find((c: any) => c.nome === dossieAlvo.razao);
          if (!cliAlvo) return null;
          const prod = (cliAlvo.subRows || []).find((p: any) => p.sku === sku);
          return (
            <div className="ml-12 mr-2 mb-2">
              <DossieInferior
                prefixoApi="/api/v1/carteira"
                tipo="sku"
                id={sku}
                titulo={prod?.descricao || descricao}
                subtitulo={`${sku} · ${dossieAlvo.razao}`}
                paramsExtra={{ razao_social: dossieAlvo.razao }}
                onFechar={() => setDossieAlvo(null)}
              />
            </div>
          );
        })()}

        {/* Dossiê inline — nível executivo */}
        {dossieAlvo?.sku === sku && !dossieAlvo?.razao && (
          <div className="ml-4 mr-2 mb-2">
            <DossieInferior
              prefixoApi="/api/v1/carteira"
              tipo="sku"
              id={sku}
              titulo={descricao}
              subtitulo={`${sku} · executivo: ${node.executivoNome}`}
              paramsExtra={{ vendedor_nome: node.executivoNome }}
              onFechar={() => setDossieAlvo(null)}
            />
          </div>
        )}
      </div>
    );
  }

  /* ── NÓ EXECUTIVO (vendedor) — agrupa SKUs e expande clientes abaixo ── */
  if (node.tipo === 'vendedor') {
    // Agrupa os SKUs de todos os clientes deste executivo
    const skuMap: Record<string, { sku: string; descricao: string; clientes: any[] }> = {};
    (node.subRows || []).forEach((cli: any) => {
      (cli.subRows || []).forEach((prod: any) => {
        if (!skuMap[prod.sku]) {
          skuMap[prod.sku] = { sku: prod.sku, descricao: prod.descricao, clientes: [] };
        }
        if (!skuMap[prod.sku].clientes.find((c: any) => c.nome === cli.nome)) {
          skuMap[prod.sku].clientes.push(cli);
        }
      });
    });
    const skusAgrupados = Object.values(skuMap);

    const somaMes = (m: string) =>
      (node.subRows || []).reduce((s: number, cli: any) =>
        s + (cli.subRows || []).reduce((ss: number, p: any) =>
          ss + valorCliente(cli.nome, p.sku, m, p.meses[m]?.meta || 0), 0), 0);
    const fatMes = (m: string) =>
      (node.subRows || []).reduce((s: number, cli: any) =>
        s + (cli.subRows || []).reduce((ss: number, p: any) => {
          const cel = p.meses[m]; if (!cel) return ss;
          return ss + valorCliente(cli.nome, p.sku, m, cel.meta) * (cel.pmv || 0);
        }, 0), 0);

    return (
      <div className="mb-0.5">
        <button onClick={() => !buscaAtv && toggle(idPath)}
          className="w-full grid gap-2 px-3 py-2 items-center rounded-lg hover:bg-white transition-colors"
          style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
          <div className={`flex items-center gap-1.5 min-w-0 ${INDENT.executivo}`}>
            {aberta ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                     : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
            <span className="truncate font-bold text-slate-600 text-xs">{node.nome}</span>
            <span className="text-[9px] font-black uppercase tracking-wider text-slate-300 ml-1 shrink-0">executivo</span>
          </div>
          {meses.map((m: string) => (
            <div key={m} className="text-right">
              <div className="text-[9px] font-bold text-indigo-400">{fmtRs(fatMes(m))}</div>
              <div className="text-xs font-bold text-slate-500">{fmtCx(somaMes(m))} cx</div>
            </div>
          ))}
          <div />
        </button>
        {aberta && skusAgrupados.map(skuNode => (
          <NoArvore
            key={skuNode.sku}
            node={{ ...skuNode, tipo: 'produto_executivo', executivoNome: node.nome }}
            nivel={nivel + 1}
            meses={meses} abertas={abertas} toggle={toggle}
            valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
            somaSkuExecutivo={somaSkuExecutivo}
            bloqueado={bloqueado} edits={edits}
            setDossieAlvo={setDossieAlvo}
            dossieAlvo={dossieAlvo}
            idPath={`${idPath}>${skuNode.sku}`}
            buscaAtiva={buscaAtv}
          />
        ))}
      </div>
    );
  }

  /* ── NÓ DE AGRUPAMENTO (gerente, coordenador) ── */
  const tipoLabel: Record<string, string> = { gerente: 'gerente', coordenador: 'coordenador' };

  const somaMes = (m: string): number => {
    let s = 0;
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') { s += valorCliente(rz || '', n.sku, m, n.meses[m]?.meta || 0); return; }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((f: any) => walk(f, c));
    };
    walk(node, null);
    return s;
  };
  const fatMes = (m: string): number => {
    let f = 0;
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') {
        const cel = n.meses[m]; if (!cel) return;
        f += valorCliente(rz || '', n.sku, m, cel.meta || 0) * (cel.pmv || 0); return;
      }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((sub: any) => walk(sub, c));
    };
    walk(node, null);
    return f;
  };

  return (
    <div className="mb-0.5">
      <button onClick={() => !buscaAtv && toggle(idPath)}
        className={`w-full grid gap-2 px-3 py-2 items-center rounded-lg transition-colors
          ${nivel === 0 ? 'bg-white border border-slate-100 hover:border-slate-200' : 'hover:bg-white'}`}
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
        <div className={`flex items-center gap-1.5 min-w-0 ${INDENT[node.tipo] || ''}`}>
          {aberta ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                   : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
          <span className={`truncate ${nivel === 0 ? 'font-black text-slate-800 text-sm' : 'font-bold text-slate-600 text-xs'}`}>
            {node.nome}
          </span>
          <span className="text-[9px] font-black uppercase tracking-wider text-slate-300 ml-1 shrink-0">
            {tipoLabel[node.tipo] || node.tipo}
          </span>
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
        <NoArvore key={(f.nome || f.sku) + i} node={f} nivel={nivel + 1}
          meses={meses} abertas={abertas} toggle={toggle}
          valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
          somaSkuExecutivo={somaSkuExecutivo}
          bloqueado={bloqueado} edits={edits}
          setDossieAlvo={setDossieAlvo}
          dossieAlvo={dossieAlvo}
          idPath={`${idPath}>${f.nome || f.sku}`}
          buscaAtiva={buscaAtv} />
      ))}
    </div>
  );
}

/* ── GAVETA DE DOSSIÊ ────────────────────────────────────────────── */
function GavetaDossie({ alvo, fechar }: {
  alvo: { sku: string; descricao: string; razao?: string; vendedor?: string };
  fechar: () => void;
}) {
  const paramsExtra: Record<string, string> = {};
  if (alvo.razao)    paramsExtra.razao_social   = alvo.razao;
  if (alvo.vendedor) paramsExtra.vendedor_nome  = alvo.vendedor;

  const subtitulo = alvo.razao
    ? `${alvo.sku} · ${alvo.razao}`
    : alvo.vendedor
    ? `${alvo.sku} · executivo: ${alvo.vendedor}`
    : alvo.sku;

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-xl bg-white z-40 shadow-2xl overflow-y-auto">
        <DossieInferior
          prefixoApi="/api/v1/carteira"
          tipo="sku"
          id={alvo.sku}
          titulo={alvo.descricao || alvo.sku}
          subtitulo={subtitulo}
          paramsExtra={Object.keys(paramsExtra).length ? paramsExtra : undefined}
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
  const [novoAlvo, setNovoAlvo] = useState('');

  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/carteira/cadeados').then(r => setD(r.data)).finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

  const travar = async (nome: string) => {
    if (!confirm(`Bloquear a carteira de ${nome}? O coordenador não poderá mais editar suas metas.`)) return;
    try {
      await axios.post('/api/v1/carteira/bloquear', { nome_alvo: nome });
      carregar(); recarregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao bloquear.'); }
  };

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
          <div className="text-sm font-black text-slate-900">Cadeados dos coordenadores</div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100">
            <X className="w-4 h-4" />
          </button>
        </div>
        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : (
          <div className="p-5 space-y-4">
            {/* Travar novo coordenador */}
            <div className="rounded-xl border border-slate-100 bg-slate-50 p-4">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">
                Travar coordenador
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={novoAlvo}
                  onChange={e => setNovoAlvo(e.target.value)}
                  placeholder="Nome do coordenador…"
                  className="flex-1 text-xs rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-400"
                />
                <button
                  onClick={() => { if (novoAlvo.trim()) travar(novoAlvo.trim()); }}
                  disabled={!novoAlvo.trim()}
                  className="flex items-center gap-1 text-[11px] font-black bg-rose-600 text-white px-3 py-2 rounded-lg hover:bg-rose-700 disabled:opacity-40">
                  <Lock className="w-3 h-3" /> Travar
                </button>
              </div>
            </div>

            {/* Lista de cadeados ativos */}
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
              Carteiras travadas
            </div>
            {(d?.cadeados || []).length === 0 && (
              <div className="text-center text-slate-400 text-sm py-4">Nenhuma carteira bloqueada.</div>
            )}
            <div className="space-y-2">
              {(d?.cadeados || []).map((c: any) => (
                <div key={c.nome} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="text-xs font-bold text-slate-700 truncate flex items-center gap-1.5">
                      <Lock className="w-3 h-3 text-rose-500" /> {c.nome}
                    </div>
                    <div className="text-[10px] font-bold text-slate-400">{c.nivel} · por {c.por}</div>
                  </div>
                  <button onClick={() => reabrir(c.nome)}
                    className="flex items-center gap-1 text-[11px] font-bold text-slate-500 hover:text-emerald-600 px-2 py-1 rounded-lg hover:bg-white">
                    <Unlock className="w-3 h-3" /> Reabrir
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/* ── CONSOLIDADO — visão somente leitura: Meta Coordenadores vs Comercial ── */
function ConsolidadoMetas() {
  const [dados,   setDados]   = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());

  useEffect(() => {
    axios.get('/api/v1/carteira/consolidado')
      .then(r => setDados(r.data))
      .finally(() => setLoading(false));
  }, []);

  const meses: string[] = dados?.meses || [];
  const mesLabel = (iso: string) => {
    const [y, m] = iso.split('-');
    const n = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
    return `${n[parseInt(m)-1]}/${y.slice(2)}`;
  };
  const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
  const fmtRs = (n: number) =>
    new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
      .format(Math.round(n || 0));

  const toggle = (id: string) =>
    setAbertas(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando consolidado…
    </div>
  );

  // Totais globais por mês
  const totais = meses.reduce((acc: any, m) => {
    let meta = 0, bu = 0, ia = 0, fatMeta = 0, fatBu = 0;
    (dados?.categorias || []).forEach((cat: any) =>
      cat.skus.forEach((s: any) => {
        const cel = s.meses[m]; if (!cel) return;
        meta   += cel.meta || 0;
        bu     += cel.bu   || 0;
        ia     += cel.ia   || 0;
        fatMeta += (cel.meta || 0) * (cel.pmv || 0);
        fatBu   += (cel.bu   || 0) * (cel.pmv || 0);
      })
    );
    acc[m] = { meta, bu, ia, fatMeta, fatBu };
    return acc;
  }, {});

  const colGrid = `240px repeat(${meses.length}, minmax(200px, 1fr))`;

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      <div className="flex-1 min-h-0 overflow-y-auto">

        {/* CABEÇALHO. */}
        <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
          <div className="px-6 pt-5 pb-3">
            <h1 className="text-lg font-black text-slate-900 tracking-tight">Consolidado de Metas</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · somente leitura · compara o plano dos coordenadores com a Demanda Comercial
            </p>
          </div>

          {/* Totais por mês */}
          <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: colGrid }}>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total</div>
            {meses.map(m => {
              const t = totais[m] || {};
              const dPct = t.bu > 0 ? ((t.meta - t.bu) / t.bu * 100) : null;
              const dCor = dPct == null ? '#94a3b8' : Math.abs(dPct) >= 15 ? '#e11d48' : Math.abs(dPct) >= 5 ? '#d97706' : '#059669';
              return (
                <div key={m} className="bg-slate-50 rounded-xl px-3 py-2 space-y-1.5">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
                  {/* Coordenadores */}
                  <div>
                    <div className="text-[9px] font-black uppercase text-violet-500 mb-0.5">Coordenadores (meta)</div>
                    <div className="text-sm font-black text-slate-900">{fmtCx(t.meta)} cx</div>
                    <div className="text-[10px] font-bold text-violet-600">{fmtRs(t.fatMeta)}</div>
                  </div>
                  <div className="h-px bg-slate-200" />
                  {/* Comercial */}
                  <div>
                    <div className="text-[9px] font-black uppercase text-indigo-500 mb-0.5">Comercial (bu)</div>
                    <div className="text-sm font-black text-slate-700">{fmtCx(t.bu)} cx</div>
                    <div className="text-[10px] font-bold text-indigo-600">{fmtRs(t.fatBu)}</div>
                  </div>
                  {/* Delta */}
                  {dPct != null && (
                    <div className="flex items-center justify-between rounded-lg px-2 py-1"
                      style={{ background: Math.abs(dPct) >= 5 ? '#fff7ed' : '#f0fdf4' }}>
                      <span className="text-[9px] font-black text-slate-400">Δ meta vs bu</span>
                      <span className="text-[11px] font-black" style={{ color: dCor }}>
                        {dPct >= 0 ? '+' : ''}{dPct.toFixed(1)}%
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Cabeçalho da tabela */}
          <div className="px-6 pb-1">
            <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
              style={{ gridTemplateColumns: colGrid }}>
              <div>Categoria / SKU</div>
              {meses.map(m => (
                <div key={m} className="grid grid-cols-3 gap-1 text-center">
                  <span className="text-violet-400">Meta</span>
                  <span className="text-indigo-400">BU</span>
                  <span className="text-slate-400">Δ%</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* CORPO */}
        <div className="px-6 pb-8">
          {(dados?.categorias || []).map((cat: any) => {
            const catAberta = abertas.has(cat.nome);

            // Totais da categoria
            const somaCat = (m: string, campo: 'meta' | 'bu' | 'ia') =>
              cat.skus.reduce((a: number, s: any) => a + (s.meses[m]?.[campo] || 0), 0);

            return (
              <div key={cat.nome} className="mb-1">
                {/* Linha categoria */}
                <button onClick={() => toggle(cat.nome)}
                  className="w-full grid gap-2 px-3 py-3 items-center bg-white rounded-xl border border-slate-100 hover:border-slate-200 transition-colors"
                  style={{ gridTemplateColumns: colGrid }}>
                  <div className="flex items-center gap-2 min-w-0">
                    {catAberta ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
                               : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                    <div className="min-w-0">
                      <div className="font-black text-slate-800 text-sm truncate">{cat.nome}</div>
                      <div className="text-[10px] font-bold text-slate-400">{cat.skus.length} SKUs</div>
                    </div>
                  </div>
                  {meses.map(m => {
                    const meta = somaCat(m, 'meta');
                    const bu   = somaCat(m, 'bu');
                    const d    = bu > 0 ? ((meta - bu) / bu * 100) : null;
                    const cor  = d == null ? '#94a3b8' : Math.abs(d) >= 15 ? '#e11d48' : Math.abs(d) >= 5 ? '#d97706' : '#059669';
                    return (
                      <div key={m} className="grid grid-cols-3 gap-1 items-center">
                        <div className="text-center text-[11px] font-black text-violet-600">{fmtCx(meta)}</div>
                        <div className="text-center text-[11px] font-black text-indigo-500">{fmtCx(bu)}</div>
                        <div className="text-center text-[11px] font-black" style={{ color: cor }}>
                          {d == null ? '—' : `${d >= 0 ? '+' : ''}${d.toFixed(0)}%`}
                        </div>
                      </div>
                    );
                  })}
                </button>

                {/* Linhas SKU */}
                {catAberta && cat.skus.map((s: any) => (
                  <div key={s.sku}
                    className="grid gap-2 px-3 py-2 items-center ml-3 mt-0.5 rounded-xl hover:bg-white"
                    style={{ gridTemplateColumns: colGrid }}>
                    <div className="min-w-0 pl-2">
                      <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                      <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                    </div>
                    {meses.map(m => {
                      const cel  = s.meses[m];
                      if (!cel) return <div key={m} />;
                      const meta = cel.meta || 0;
                      const bu   = cel.bu   || 0;
                      const d    = cel.delta_pct;
                      const cor  = d == null ? '#94a3b8' : Math.abs(d) >= 15 ? '#e11d48' : Math.abs(d) >= 5 ? '#d97706' : '#059669';
                      const editado = meta !== bu;
                      return (
                        <div key={m} className={`grid grid-cols-3 gap-1 items-center px-1 py-1 rounded-lg ${editado ? 'bg-violet-50' : ''}`}>
                          <div className="text-center">
                            <div className="text-[11px] font-black text-violet-700">{fmtCx(meta)}</div>
                            {cel.pmv > 0 && <div className="text-[9px] text-violet-400">{fmtRs(meta * cel.pmv)}</div>}
                          </div>
                          <div className="text-center">
                            <div className="text-[11px] font-bold text-indigo-500">{fmtCx(bu)}</div>
                            {cel.pmv > 0 && <div className="text-[9px] text-indigo-300">{fmtRs(bu * cel.pmv)}</div>}
                          </div>
                          <div className="text-center">
                            <div className="text-[11px] font-black" style={{ color: cor }}>
                              {d == null ? '—' : `${d >= 0 ? '+' : ''}${d.toFixed(0)}%`}
                            </div>
                            {d != null && <div className="text-[9px] text-slate-300">{fmtCx(Math.abs(cel.delta_cx))} cx</div>}
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
      </div>
    </div>
  );
}