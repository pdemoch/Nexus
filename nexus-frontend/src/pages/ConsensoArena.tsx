import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, X, Lock, Unlock,
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
const parseInteiroFormatado = (valor: string) => {
  const digitos = valor.replace(/\D/g, '');
  return digitos ? parseInt(digitos, 10) : 0;
};
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
  const [busca,          setBusca]          = useState('');
  const [avancando,      setAvancando]      = useState(false);
  const [adminAlvo,      setAdminAlvo]      = useState<{ nome: string; nivel: string } | null>(null);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const params = adminAlvo
        ? { responsavel: adminAlvo.nome, nivel_responsavel: adminAlvo.nivel }
        : {};
      const r = await axios.get('/api/v1/carteira/tabela', { params });
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, [adminAlvo]);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[]    = dados?.meses || [];
  const congeladaEtapa     = Boolean(dados?.etapa_congelada);
  const minhaCongelada     = Boolean(dados?.minha_carteira_congelada);
  const aguardandoUpstream = Boolean(dados?.aguardando_upstream);
  const souAdmin           = dados?.sou_admin === true;
  const adminOperando      = souAdmin && adminAlvo !== null;
  // funcao vem como campo extra — precisamos dela para controle de cadeado
  const funcao: string     = dados?.funcao || '';
  // O Administrador pode operar como supervisor mesmo antes do congelamento
  // upstream; os demais perfis continuam respeitando o bastão da etapa.
  const bloqueado          = congeladaEtapa || minhaCongelada ||
    (aguardandoUpstream && !souAdmin);

  const responsaveis = (dados?.responsaveis || []) as Array<{ nome: string; nivel: string }>;

  /* Chave de edição: sempre no nível razão social */
  const keyOf       = (razao: string, sku: string, mes: string) => `${razao}||${sku}||${mes}`;
  const valorCliente = (razao: string, sku: string, mes: string, original: number) => {
    const k = keyOf(razao, sku, mes);
    return k in edits ? edits[k] : (original || 0);
  };

  const baixarBlob = (blob: Blob, nome: string) => {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  const payloadAjustes = () => ({
    ajustes: Object.entries(edits).map(([k, v]) => {
      const [razao_social, sku, mes] = k.split('||');
      return { razao_social, sku, mes_projetado: mes, novo_volume: v };
    }),
    ...(adminAlvo ? { nome_alvo: adminAlvo.nome, nivel_alvo: adminAlvo.nivel } : {}),
  });

  const evidenciaNome = (prefixo: string) =>
    `${prefixo}_${dados?.ciclo?.replace('/', '_') || 'ciclo'}.xlsx`;

  const executarComEvidencia = async (endpoint: string, prefixoArquivo: string) => {
    const r = await axios.post(endpoint, payloadAjustes(), { responseType: 'blob' });
    baixarBlob(new Blob([r.data]), evidenciaNome(prefixoArquivo));
    setEdits({});
    await carregar();
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

  const setClienteValor = (razao: string, sku: string, mes: string, valor: number, pmv: number) => {
    if (pmv <= 0) return;
    setCliente(razao, sku, mes, Math.round(Math.max(0, valor || 0) / pmv));
  };

  const setNodeValor = (node: any, mes: string, valorAlvo: number) => {
    const folhas: Array<{ razao: string; sku: string; pmv: number; atualVol: number; pesoHist: number }> = [];
    const walk = (n: any, razaoCtx: string | null) => {
      if (n.tipo === 'produto') {
        const cel = n.meses?.[mes];
        if (!cel) return;
        folhas.push({
          razao: razaoCtx || '',
          sku: n.sku,
          pmv: cel.pmv || 0,
          atualVol: valorCliente(razaoCtx || '', n.sku, mes, cel.meta || 0),
          pesoHist: cel.peso_historico || 0,
        });
        return;
      }
      const novoCtx = n.tipo === 'cliente' ? n.nome : razaoCtx;
      (n.subRows || []).forEach((f: any) => walk(f, novoCtx));
    };
    walk(node, null);
    const editaveis = folhas.filter(f => f.pmv > 0);
    if (!editaveis.length) return;
    const pesosAtuais = editaveis.map(f => f.atualVol * f.pmv);
    const temValorAtual = pesosAtuais.some(v => v > 0);
    const pesos = temValorAtual ? pesosAtuais : editaveis.map(f => f.pesoHist);
    const valoresRateados = ratearMaiorResto(Math.round(Math.max(0, valorAlvo || 0)), pesos);
    setEdits(prev => {
      const next = { ...prev };
      editaveis.forEach((f, i) => {
        next[keyOf(f.razao, f.sku, mes)] = Math.max(0, Math.round(valoresRateados[i] / f.pmv));
      });
      return next;
    });
  };

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

  /* Salvar: envia ajustes por razão social, contrato granular esperado pelo backend. */
  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      await executarComEvidencia('/api/v1/carteira/salvar-evidencia', 'metas_salvar');
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally { setSalvando(false); }
  };

  const passarCoordenadores = async () => {
    if (!confirm('Passar metas financeiras para os coordenadores? Será baixado um XLSX de evidência.')) return;
    setAvancando(true);
    try {
      await executarComEvidencia('/api/v1/carteira/passar-coordenadores-evidencia', 'metas_para_coordenadores');
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao passar para coordenadores.');
    } finally { setAvancando(false); }
  };

  const salvarETrancar = async () => {
    if (!confirm('Salvar e trancar sua distribuição? A variação precisa ficar dentro de ±5% da meta recebida.')) return;
    setSalvando(true);
    try {
      await executarComEvidencia('/api/v1/carteira/trancar-evidencia', 'metas_trancadas');
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail?.itens?.length) {
        alert(`${detail.mensagem}\n${detail.itens.map((i: any) => `${i.mes}: ${i.variacao_pct}%`).join('\n')}`);
      } else {
        alert(detail || 'Falha ao trancar.');
      }
    } finally { setSalvando(false); }
  };

  const congelarEtapa = async () => {
    if (!confirm('Aprovar Metas Comercial? A etapa Irrestrita será liberada.')) return;
    try {
      const r = await axios.post('/api/v1/carteira/aprovar-evidencia', payloadAjustes(), { responseType: 'blob' });
      baixarBlob(new Blob([r.data]), evidenciaNome('metas_aprovadas'));
      setEdits({});
      await carregar();
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail?.coordenadores_pendentes?.length) {
        alert(`${detail.mensagem}\n${detail.coordenadores_pendentes.join('\n')}`);
      } else {
        alert(detail || 'Falha ao aprovar metas.');
      }
    }
  };

  const reabrirEtapa = async () => {
    if (!confirm('Reabrir Metas Comercial?')) return;
    try {
      await axios.post('/api/v1/carteira/reabrir', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao reabrir.'); }
  };

  const reabrirFaseAdmin = async () => {
    if (!adminAlvo || !confirm(`Reabrir a fase de ${adminAlvo.nome}?`)) return;
    try {
      await axios.post('/api/v1/carteira/reabrir-fase', {
        nome_alvo: adminAlvo.nome, nivel_alvo: adminAlvo.nivel,
      });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao reabrir a fase.');
    }
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
            <div className="mt-2 text-[10px] font-bold text-indigo-500">
              Edição financeira reativa: Coordenador → Executivo → SKU → Razão Social
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            {souAdmin && (
              <select
                value={adminAlvo ? `${adminAlvo.nivel}||${adminAlvo.nome}` : ''}
                onChange={e => {
                  const [nivel, ...nome] = e.target.value.split('||');
                  setAdminAlvo(e.target.value ? { nivel, nome: nome.join('||') } : null);
                }}
                className="max-w-xs px-3 py-2 rounded-xl border border-violet-200 bg-violet-50 text-xs font-bold text-violet-700 focus:outline-none"
              >
                <option value="">Visão administrativa: toda a carteira</option>
                {responsaveis.map(r => (
                  <option key={`${r.nivel}||${r.nome}`} value={`${r.nivel}||${r.nome}`}>
                    {r.nivel}: {r.nome}
                  </option>
                ))}
              </select>
            )}
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
            <button onClick={salvar} disabled={!temEdicoes || salvando || bloqueado}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !bloqueado ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar + XLSX{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            {(funcao === 'Gerente' || (souAdmin && (!adminAlvo || adminAlvo.nivel === 'Gerente'))) && (
              <button onClick={passarCoordenadores} disabled={avancando || bloqueado}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-60">
                {avancando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                Passar para coordenadores
              </button>
            )}
            {(funcao === 'Coordenador' || adminAlvo?.nivel === 'Coordenador') && (
              <button onClick={salvarETrancar} disabled={salvando || (bloqueado && !(souAdmin && adminOperando))}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60">
                {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                Salvar e trancar
              </button>
            )}
            {souAdmin && adminOperando && (
              <button onClick={reabrirFaseAdmin}
                className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-amber-500 text-white hover:bg-amber-600">
                <Unlock className="w-4 h-4" /> Reabrir fase
              </button>
            )}
            {/* Congelar/Reabrir etapa — só Gerente e Admin */}
            {(souAdmin || funcao === 'Gerente') && (
              congeladaEtapa ? (
                <button onClick={reabrirEtapa}
                  className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-amber-500 text-white hover:bg-amber-600">
                  <Unlock className="w-4 h-4" /> Reabrir etapa
                </button>
              ) :
                <button onClick={congelarEtapa} disabled={aguardandoUpstream}
                  className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black
                    ${aguardandoUpstream ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-emerald-600 text-white hover:bg-emerald-700'}`}>
                  <Lock className="w-4 h-4" /> Aprovar Metas
                </button>
            )}
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

      <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-8">
        {arvoreVisivelOuCompleta.length === 0 && busca.trim()
          ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Search className="w-8 h-8 mb-2 opacity-30" />
              <div className="text-sm font-bold">Nenhum resultado para "{busca}"</div>
              <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">Limpar busca</button>
            </div>
          )
          : arvoreVisivelOuCompleta.map((g: any) => (
            <NoArvore key={g.nome} node={g} nivel={0}
              meses={meses} abertas={abertas} toggle={toggle}
              valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
              somaSkuExecutivo={somaSkuExecutivo}
              bloqueado={bloqueado && !(souAdmin && adminOperando)} edits={edits}
              setClienteValor={setClienteValor}
              setNodeValor={setNodeValor}
              setDossieAlvo={setDossieAlvo}
              dossieAlvo={dossieAlvo}
              idPath={g.nome}
              buscaAtiva={!!busca.trim()} />
          ))}
      </div>
      {/* GAVETA DOSSIÊ */}


      {/* PAINEL DE CADEADOS */}
    </div>
  );
}

/* ── NÓ RECURSIVO DA ÁRVORE ──────────────────────────────────────── */
function NoArvore({ node, nivel, meses, abertas, toggle,
  valorCliente, setSkuExecutivo, setCliente, somaSkuExecutivo,
  bloqueado, edits, setDossieAlvo, dossieAlvo, idPath, buscaAtiva,
  setClienteValor, setNodeValor }: any) {

  const buscaAtv = buscaAtiva;
  const aberta   = buscaAtv || abertas.has(idPath);

  /* ── NÓ PRODUTO — renderizado dentro do nível EXECUTIVO (vendedor) ── */
  /* Este nó representa um SKU agregado dos clientes do executivo.       */
  /* O input edita o total do SKU e rateia para os clientes abaixo.      */
  if (node.tipo === 'produto_executivo') {
    const { sku, descricao, clientes } = node;
    const clientesAbertos = buscaAtv || abertas.has(idPath);
    return (
      <div className="mb-0.5">
        {/* Linha do SKU — input editável, dispara rateio */}
        <div className="grid gap-2 px-3 py-1.5 items-center hover:bg-indigo-50/40 rounded-lg group"
          style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
          <div className={`min-w-0 ${INDENT.produto}`}>
            {/* Botão expandir clientes */}
            <button
              onClick={() => toggle(idPath)}
              className="flex items-center gap-1 w-full text-left"
            >
              {clientesAbertos
                ? <ChevronDown className="w-3 h-3 text-slate-300 shrink-0" />
                : <ChevronRight className="w-3 h-3 text-slate-300 shrink-0" />}
              <div className="min-w-0">
                <div className="text-xs font-bold text-slate-700 truncate">{descricao}</div>
                <div className="text-[10px] font-bold text-slate-300">{sku} · {clientes.length} cliente{clientes.length !== 1 ? 's' : ''}</div>
              </div>
            </button>
          </div>
          {meses.map((m: string) => {
            const totalSku  = somaSkuExecutivo(clientes, sku, m);
            const cel0      = clientes[0]?.subRows?.find((p: any) => p.sku === sku)?.meses[m];
            const pmvSoma = clientes.reduce((s: number, cli: any) => {
              const p = (cli.subRows || []).find((pr: any) => pr.sku === sku);
              const cel = p?.meses?.[m];
              const volume = cel ? valorCliente(cli.nome, sku, m, cel.meta) : 0;
              return s + volume * (cel?.pmv || 0);
            }, 0);
            const pmvComVolume = clientes.reduce((s: number, cli: any) => {
              const p = (cli.subRows || []).find((pr: any) => pr.sku === sku);
              return s + (p?.meses?.[m]?.pmv || 0);
            }, 0);
            const pmv       = totalSku > 0
              ? pmvSoma / totalSku
              : (clientes.length ? pmvComVolume / clientes.length : 0);
            const valorAtual = totalSku * pmv;
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
                <input
                  type="text"
                  value={fmtRs(valorAtual)}
                  disabled={bloqueado || pmv <= 0}
                  onChange={e => {
                    const valor = parseInteiroFormatado(e.target.value);
                    const volume = pmv > 0 ? Math.round(valor / pmv) : totalSku;
                    setSkuExecutivo(clientesInfo, sku, m, volume);
                  }}
                  title={pmv <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                  inputMode="numeric"
                  className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                    ${temEdit ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-indigo-600'}
                    ${bloqueado || pmv <= 0 ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
                />
                <input
                  type="text"
                  value={fmtCx(totalSku)}
                  disabled={bloqueado}
                  onChange={e => setSkuExecutivo(clientesInfo, sku, m, parseInteiroFormatado(e.target.value))}
                  inputMode="numeric"
                  className={`w-full text-right text-xs font-bold rounded-md px-2 py-0.5 border transition-colors mt-0.5
                    ${temEdit ? 'border-indigo-200 bg-white text-slate-700' : 'border-transparent bg-transparent text-slate-500'}
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
        {clientesAbertos && clientes.map((cli: any) => {
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
                      type="text"
                      value={fmtRs(val * (cel.pmv || 0))}
                      disabled={bloqueado || (cel.pmv || 0) <= 0}
                      onChange={e => {
                        const valor = parseInteiroFormatado(e.target.value);
                        setClienteValor(cli.nome, sku, m, valor, cel.pmv || 0);
                      }}
                      title={(cel.pmv || 0) <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                      inputMode="numeric"
                      className={`w-full text-right text-xs font-bold rounded-md px-2 py-1 border transition-colors
                        ${editado ? 'border-violet-300 bg-violet-50 text-violet-700' : 'border-transparent bg-transparent text-indigo-600'}
                        ${bloqueado || (cel.pmv || 0) <= 0 ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-100 focus:border-violet-400 focus:bg-white focus:outline-none'}`}
                    />
                    <input
                      type="text"
                      value={fmtCx(val)}
                      disabled={bloqueado}
                      onChange={e => setCliente(cli.nome, sku, m, parseInteiroFormatado(e.target.value))}
                      inputMode="numeric"
                      className={`w-full text-right text-[11px] font-bold rounded-md px-2 py-0.5 border transition-colors mt-0.5
                        ${editado ? 'border-violet-200 bg-white text-slate-700' : 'border-transparent bg-transparent text-slate-500'}
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
                paramsExtra={{ razao_social: dossieAlvo.razao, vendedor_nome: node.executivoNome }}
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
        <div
          className="w-full grid gap-2 px-3 py-2 items-center rounded-lg hover:bg-white transition-colors"
          style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
          <button onClick={() => !buscaAtv && toggle(idPath)}
            className={`flex items-center gap-1.5 min-w-0 text-left ${INDENT.executivo}`}>
            {aberta ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
            <span className="truncate font-bold text-slate-600 text-xs">{node.nome}</span>
            <span className="text-[9px] font-black uppercase tracking-wider text-slate-300 ml-1 shrink-0">executivo</span>
          </button>
          {meses.map((m: string) => (
            <div key={m} className="text-right">
              <input
                type="text"
                value={fmtRs(fatMes(m))}
                disabled={bloqueado}
                onChange={e => setNodeValor(node, m, parseInteiroFormatado(e.target.value))}
                inputMode="numeric"
                className={`w-full text-right text-xs font-black rounded-md px-2 py-1 border transition-colors
                  ${bloqueado ? 'cursor-not-allowed opacity-60 border-transparent bg-transparent text-slate-500' : 'border-transparent bg-transparent text-indigo-600 hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
              />
              <input
                type="text"
                value={fmtCx(somaMes(m))}
                disabled={bloqueado}
                onChange={e => {
                  const volAtual = somaMes(m);
                  const valorAtual = fatMes(m);
                  const pmvMedio = volAtual > 0 ? valorAtual / volAtual : 0;
                  if (pmvMedio > 0) setNodeValor(node, m, parseInteiroFormatado(e.target.value) * pmvMedio);
                }}
                inputMode="numeric"
                className={`w-full text-right text-[11px] font-bold rounded-md px-2 py-0.5 border transition-colors mt-0.5
                  ${bloqueado ? 'cursor-not-allowed opacity-60 border-transparent bg-transparent text-slate-500' : 'border-transparent bg-transparent text-slate-500 hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
              />
            </div>
          ))}
          <div />
        </div>
        {aberta && skusAgrupados.map(skuNode => (
          <NoArvore
            key={skuNode.sku}
            node={{ ...skuNode, tipo: 'produto_executivo', executivoNome: node.nome }}
            nivel={nivel + 1}
            meses={meses} abertas={abertas} toggle={toggle}
            valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
            somaSkuExecutivo={somaSkuExecutivo}
            bloqueado={bloqueado} edits={edits}
            setClienteValor={setClienteValor}
            setNodeValor={setNodeValor}
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
      <div
        className={`w-full grid gap-2 px-3 py-2 items-center rounded-lg transition-colors
          ${nivel === 0 ? 'bg-white border border-slate-100 hover:border-slate-200' : 'hover:bg-white'}`}
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
        <button onClick={() => !buscaAtv && toggle(idPath)}
          className={`flex items-center gap-1.5 min-w-0 text-left ${INDENT[node.tipo] || ''}`}>
          {aberta ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                   : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
          <span className={`truncate ${nivel === 0 ? 'font-black text-slate-800 text-sm' : 'font-bold text-slate-600 text-xs'}`}>
            {node.nome}
          </span>
          <span className="text-[9px] font-black uppercase tracking-wider text-slate-300 ml-1 shrink-0">
            {tipoLabel[node.tipo] || node.tipo}
          </span>
        </button>
        {meses.map((m: string) => (
          <div key={m} className="text-right">
            <input
              type="text"
              value={fmtRs(fatMes(m))}
              disabled={bloqueado}
              onChange={e => setNodeValor(node, m, parseInteiroFormatado(e.target.value))}
              inputMode="numeric"
              className={`w-full text-right text-xs font-black rounded-md px-2 py-1 border transition-colors
                ${bloqueado ? 'cursor-not-allowed opacity-60 border-transparent bg-transparent text-slate-500' : 'border-transparent bg-transparent text-indigo-600 hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
            />
            <input
              type="text"
              value={fmtCx(somaMes(m))}
              disabled={bloqueado}
              onChange={e => {
                const volAtual = somaMes(m);
                const valorAtual = fatMes(m);
                const pmvMedio = volAtual > 0 ? valorAtual / volAtual : 0;
                if (pmvMedio > 0) setNodeValor(node, m, parseInteiroFormatado(e.target.value) * pmvMedio);
              }}
              inputMode="numeric"
              className={`w-full text-right text-[11px] font-bold rounded-md px-2 py-0.5 border transition-colors mt-0.5
                ${bloqueado ? 'cursor-not-allowed opacity-60 border-transparent bg-transparent text-slate-500' : 'border-transparent bg-transparent text-slate-500 hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
            />
          </div>
        ))}
        <div />
      </div>
      {aberta && (node.subRows || []).map((f: any, i: number) => (
        <NoArvore key={(f.nome || f.sku) + i} node={f} nivel={nivel + 1}
          meses={meses} abertas={abertas} toggle={toggle}
          valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
          somaSkuExecutivo={somaSkuExecutivo}
          bloqueado={bloqueado} edits={edits}
          setNodeValor={setNodeValor}
          setDossieAlvo={setDossieAlvo}
          dossieAlvo={dossieAlvo}
          idPath={`${idPath}>${f.nome || f.sku}`}
          buscaAtiva={buscaAtv}
          setClienteValor={setClienteValor} />
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
function PainelCadeados({ fechar, recarregar, somenteTravar = false }: { fechar: () => void; recarregar: () => void; somenteTravar?: boolean }) {
  const [d, setD]         = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [em_acao, setEmAcao] = useState<string | null>(null);

  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/carteira/cadeados').then(r => setD(r.data)).finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

  const toggle = async (nome: string, bloqueado: boolean) => {
    const acao = bloqueado ? 'reabrir' : 'bloquear';
    const msg  = bloqueado
      ? `Reabrir a carteira de ${nome}?`
      : `Bloquear a carteira de ${nome}? O coordenador não poderá mais editar.`;
    if (!confirm(msg)) return;
    setEmAcao(nome);
    try {
      if (bloqueado) {
        await axios.post('/api/v1/carteira/reabrir-cadeado', { nome_alvo: nome });
      } else {
        await axios.post('/api/v1/carteira/bloquear', { nome_alvo: nome });
      }
      carregar(); recarregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || `Falha ao ${acao}.`);
    } finally { setEmAcao(null); }
  };

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-sm bg-white z-40 shadow-2xl flex flex-col">
        <div className="shrink-0 border-b border-slate-100 px-5 py-4 flex items-center justify-between">
          <div>
            <div className="text-sm font-black text-slate-900">Cadeados dos coordenadores</div>
            <div className="text-[10px] font-bold text-slate-400 mt-0.5">
              {somenteTravar ? 'Você pode trancar; somente o Administrador pode reabrir.' : 'Clique no botão para bloquear ou reabrir'}
            </div>
          </div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-slate-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : (
            <div className="space-y-2">
              {(d?.coordenadores || []).length === 0 && (
                <div className="text-center text-slate-400 text-sm py-8">
                  Nenhum coordenador encontrado.
                </div>
              )}
              {(d?.coordenadores || []).map((c: any) => (
                <div key={c.nome}
                  className={`flex items-center justify-between rounded-xl px-4 py-3 border transition-colors
                    ${c.bloqueado
                      ? 'bg-rose-50 border-rose-200'
                      : 'bg-white border-slate-100 hover:border-slate-200'}`}>
                  <div className="min-w-0">
                    <div className="text-xs font-bold text-slate-700 truncate flex items-center gap-1.5">
                      {c.bloqueado
                        ? <Lock className="w-3 h-3 text-rose-500 shrink-0" />
                        : <Unlock className="w-3 h-3 text-slate-300 shrink-0" />}
                      {c.nome}
                    </div>
                    {c.bloqueado && c.por && (
                      <div className="text-[10px] font-bold text-rose-400 mt-0.5">
                        bloqueado por {c.por}
                      </div>
                    )}
                  </div>
                  {(!somenteTravar || !c.bloqueado) && <button
                    onClick={() => toggle(c.nome, c.bloqueado)}
                    disabled={em_acao === c.nome}
                    className={`ml-3 shrink-0 flex items-center gap-1.5 text-[11px] font-black px-3 py-1.5 rounded-lg transition-colors
                      ${c.bloqueado
                        ? 'bg-white text-emerald-600 border border-emerald-200 hover:bg-emerald-50'
                        : 'bg-rose-600 text-white hover:bg-rose-700'}
                      disabled:opacity-40`}>
                    {em_acao === c.nome
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : c.bloqueado
                        ? <><Unlock className="w-3 h-3" /> Reabrir</>
                        : <><Lock className="w-3 h-3" /> Travar</>}
                  </button>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ── CONSOLIDADO — visão somente leitura: Meta Coordenadores vs Comercial ── */
function ConsolidadoMetas() {
  const [dados,   setDados]   = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  const [aprovando, setAprovando] = useState(false);

  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/carteira/consolidado')
      .then(r => setDados(r.data))
      .finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

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

  const aprovarMetas = async () => {
    if (!confirm('Aprovar Metas Comercial e liberar Demanda Irrestrita? Será baixado um XLSX de evidência.')) return;
    setAprovando(true);
    try {
      const r = await axios.post('/api/v1/carteira/aprovar-evidencia', { ajustes: [] }, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `metas_aprovadas_${dados?.ciclo?.replace('/', '_') || 'ciclo'}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao aprovar metas.');
    } finally { setAprovando(false); }
  };

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

        {/* CABEÇALHO */}
        <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
          <div className="px-6 pt-5 pb-3 flex items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-black text-slate-900 tracking-tight">Consolidado de Metas</h1>
              <p className="text-xs font-medium text-slate-400">
                Ciclo {dados?.ciclo} · compara o plano dos coordenadores com a Demanda Comercial
              </p>
              {(dados?.coordenadores_pendentes || []).length > 0 && (
                <div className="mt-2 text-[11px] font-bold text-orange-600">
                  Faltam trancar: {(dados?.coordenadores_pendentes || []).join(', ')}
                </div>
              )}
            </div>
            {dados?.pode_aprovar && (
              <button onClick={aprovarMetas}
                disabled={aprovando || (dados?.coordenadores_pendentes || []).length > 0}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black
                  ${(dados?.coordenadores_pendentes || []).length === 0
                    ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                    : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
                {aprovando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                Aprovar Metas
              </button>
            )}
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
                {catAberta && cat.skus.map((s: any) => {
                  const skuKey = `${cat.nome}>${s.sku}`;
                  const skuAberto = abertas.has(skuKey);
                  return (
                    <div key={s.sku}>
                      <button onClick={() => toggle(skuKey)}
                        className="w-full grid gap-2 px-3 py-2 items-center ml-3 mt-0.5 rounded-xl hover:bg-white"
                        style={{ gridTemplateColumns: colGrid }}>
                        <div className="min-w-0 pl-2 flex items-center gap-2">
                          {skuAberto ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                                     : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
                          <div className="min-w-0 text-left">
                            <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                            <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                          </div>
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
                      </button>
                      {skuAberto && (
                        <div className="ml-8 mr-2 mb-2 rounded-xl bg-white border border-slate-100 overflow-hidden">
                          <div className="px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400 border-b border-slate-100">
                            Impacto por coordenador neste SKU
                          </div>
                          {(s.impactos_coordenadores || []).map((i: any) => (
                            <div key={`${s.sku}-${i.coordenador}`}
                              className="grid grid-cols-4 gap-2 px-3 py-2 text-[11px] border-b border-slate-50 last:border-0">
                              <div className="font-bold text-slate-700 truncate">{i.coordenador}</div>
                              <div className="text-right text-violet-600 font-bold">{fmtRs(i.meta_rs)}</div>
                              <div className="text-right text-indigo-500 font-bold">{fmtRs(i.bu_rs)}</div>
                              <div className={`text-right font-black ${i.delta_rs > 0 ? 'text-emerald-600' : i.delta_rs < 0 ? 'text-red-600' : 'text-slate-400'}`}>
                                {fmtRs(i.delta_rs)}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
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