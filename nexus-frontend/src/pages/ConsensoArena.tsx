import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Download, X, Lock, Unlock,
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

/* ── STEPPER DE FASE (cascata SKU → Executivo → Razão Social) ──────
   Gerente só tem a fase SKU (rateia direto para Coordenadores).
   Coordenador percorre as 3 fases dentro da própria tela. */
function StepperFase({ funcao, faseAtual }: { funcao: string; faseAtual: string }) {
  const passos = funcao === 'Gerente'
    ? [{ id: 'SKU', label: 'SKU' }]
    : [
        { id: 'SKU', label: 'SKU' },
        { id: 'EXECUTIVO', label: 'Executivo' },
        { id: 'RAZAO_SOCIAL', label: 'Razão Social' },
      ];
  const idxAtual = passos.findIndex(p => p.id === faseAtual);
  return (
    <div className="flex items-center gap-1.5">
      {passos.map((p, i) => {
        const concluido = i < idxAtual;
        const atual     = i === idxAtual;
        return (
          <React.Fragment key={p.id}>
            <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide
              ${concluido ? 'bg-emerald-100 text-emerald-700' : atual ? 'bg-violet-100 text-violet-700' : 'bg-slate-100 text-slate-400'}`}>
              {concluido && <Lock className="w-2.5 h-2.5" />}
              {p.label}
            </div>
            {i < passos.length - 1 && <div className="w-3 h-px bg-slate-200" />}
          </React.Fragment>
        );
      })}
    </div>
  );
}

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
  const [fase,           setFase]           = useState<any>(null);
  const [avancando,      setAvancando]      = useState(false);
  const [adminAlvo,      setAdminAlvo]      = useState<{ nome: string; nivel: string } | null>(null);
  const [modoEdicao,     setModoEdicao]     = useState<'caixas' | 'valor'>('caixas');
  const [carregandoAuditoria, setCarregandoAuditoria] = useState(false);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const params = adminAlvo
        ? { responsavel: adminAlvo.nome, nivel_responsavel: adminAlvo.nivel }
        : {};
      const r = await axios.get('/api/v1/carteira/tabela', { params });
      setDados(r.data); setEdits({});
      setFase(r.data?.minha_fase || null);
    } finally { setLoading(false); }
  }, [adminAlvo]);
  useEffect(() => { carregar(); }, [carregar]);

  const carregarAuditoria = async () => {
    setCarregandoAuditoria(true);
    try {
      const params = adminAlvo
        ? { responsavel: adminAlvo.nome, nivel_responsavel: adminAlvo.nivel }
        : {};
      const r = await axios.get('/api/v1/carteira/auditoria-impacto/exportar', {
        params,
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `auditoria_impacto_${dados?.ciclo?.replace('/', '_') || 'ciclo'}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao carregar auditoria.');
    } finally { setCarregandoAuditoria(false); }
  };

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

  // Fase da cascata (Gerente: só SKU; Coordenador: SKU -> EXECUTIVO -> RAZAO_SOCIAL).
  // Admin não tem fase própria (fase === null): trabalha em modo livre (compatibilidade).
  const faseAtual: string | null = fase?.fase_atual ?? null;
  const naFaseRazaoSocial = (souAdmin && !adminOperando) ||
    ((!souAdmin || adminOperando) &&
      (faseAtual === 'RAZAO_SOCIAL' || faseAtual === null));
  const naFaseSku         = (!souAdmin || adminOperando) && faseAtual === 'SKU';
  const naFaseExecutivo   = (!souAdmin || adminOperando) && faseAtual === 'EXECUTIVO';
  const faseFuncao = adminAlvo?.nivel || funcao;
  const responsaveis = (dados?.responsaveis || []) as Array<{ nome: string; nivel: string }>;

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

  const setClienteValor = (razao: string, sku: string, mes: string, valor: number, pmv: number) => {
    if (pmv <= 0) return;
    setCliente(razao, sku, mes, Math.round(Math.max(0, valor || 0) / pmv));
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

  /* Soma de um SKU na carteira INTEIRA do responsável logado (todos executivos),
     usada na fase SKU, onde o coordenador ajusta o total do SKU antes de ratear
     para executivos (que só existem como agregação — não há input próprio aqui). */
  const somaSkuCarteira = (sku: string, mes: string): number => {
    let total = 0;
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') {
        if (n.sku === sku) total += valorCliente(rz || '', sku, mes, n.meses[mes]?.meta || 0);
        return;
      }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((f: any) => walk(f, c));
    };
    (dados?.arvore || []).forEach((g: any) => walk(g, null));
    return total;
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

  /* Salvar: envia por razão social (contrato do backend) — só a carteira do coordenador.
     Só é usado na fase RAZAO_SOCIAL (última fase) ou pelo Admin (modo livre). */
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

  /* Avança a fase corrente (SKU ou EXECUTIVO): grava o rateio em cascata via
     /ratear-fase e move a máquina de estado para a próxima etapa. */
  const avancarFase = async () => {
    if (!faseAtual || (!adminOperando && souAdmin)) return;
    const mensagem = naFaseSku
      ? 'Concluir a fase de SKU? Os totais serão rateados para os Executivos e você passará a editar por Executivo.'
      : 'Concluir a fase de Executivo? Os totais serão rateados para as Razões Sociais e você passará à edição final por cliente.';
    if (!confirm(mensagem)) return;
    setAvancando(true);
    try {
      // Reúne todos os (sku, mes) tocados na árvore para montar os ajustes da fase.
      const chaves = new Set<string>();
      const walk = (n: any) => {
        if (n.tipo === 'produto') { meses.forEach(m => chaves.add(`${n.sku}||${m}`)); return; }
        (n.subRows || []).forEach(walk);
      };
      (dados?.arvore || []).forEach(walk);

      if (naFaseSku) {
        const ajustes = Array.from(chaves).map(k => {
          const [sku, mes] = k.split('||');
          return { sku, mes_projetado: mes, novo_volume: Math.round(somaSkuCarteira(sku, mes)) };
        });
        const r = await axios.post('/api/v1/carteira/ratear-fase', {
          ajustes,
          ...(adminAlvo ? {
            responsavel_nome: adminAlvo.nome,
            responsavel_nivel: adminAlvo.nivel,
          } : {}),
        });
        setFase(r.data?.fase || null);
      } else if (naFaseExecutivo) {
        // Na fase Executivo, cada executivo ajusta seus próprios SKUs — percorremos
        // por executivo (nó 'vendedor') e enviamos um POST por executivo.
        const porExecutivo: Record<string, { sku: string; mes: string; total: number }[]> = {};
        const walkExec = (n: any) => {
          if (n.tipo === 'vendedor') {
            const skuMap: Record<string, number> = {};
            (n.subRows || []).forEach((cli: any) => {
              (cli.subRows || []).forEach((prod: any) => {
                meses.forEach(m => {
                  const cel = prod.meses[m]; if (!cel) return;
                  const key = `${prod.sku}||${m}`;
                  skuMap[key] = (skuMap[key] || 0) + valorCliente(cli.nome, prod.sku, m, cel.meta);
                });
              });
            });
            porExecutivo[n.nome] = Object.entries(skuMap).map(([k, total]) => {
              const [sku, mes] = k.split('||');
              return { sku, mes, total };
            });
            return;
          }
          (n.subRows || []).forEach(walkExec);
        };
        (dados?.arvore || []).forEach(walkExec);

        let ultimaFase: any = fase;
        for (const [executivo, itens] of Object.entries(porExecutivo)) {
          const ajustes = itens.map(i => ({ sku: i.sku, mes_projetado: i.mes, novo_volume: Math.round(i.total) }));
          if (!ajustes.length) continue;
          const r = await axios.post('/api/v1/carteira/ratear-fase', {
            ajustes, executivo_nome: executivo,
            ...(adminAlvo ? {
              responsavel_nome: adminAlvo.nome,
              responsavel_nivel: adminAlvo.nivel,
            } : {}),
          });
          ultimaFase = r.data?.fase || ultimaFase;
        }
        setFase(ultimaFase);
      }
      setEdits({});
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao avançar a fase.');
    } finally { setAvancando(false); }
  };

  const congelarEtapa = async () => {
    if (!confirm('Congelar Metas Comercial? A etapa Supply será liberada.')) return;
    try {
      if (temEdicoes) await salvar();
      await axios.post('/api/v1/carteira/congelar', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao congelar.'); }
  };

  /* Lista agregada de SKUs da carteira INTEIRA — usada só na fase SKU
     (Gerente e Coordenador ajustam o total do SKU antes de qualquer rateio
     por executivo existir). */
  const skusAgregadosCarteira = useMemo(() => {
    if (!naFaseSku) return [];
    const map: Record<string, {
      sku: string; descricao: string;
      pmvPorMes: Record<string, number>;
      receitaPorMes: Record<string, number>;
      volumePorMes: Record<string, number>;
      iaPorMes: Record<string, number>;
    }> = {};
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') {
        if (!map[n.sku]) map[n.sku] = {
          sku: n.sku, descricao: n.descricao, pmvPorMes: {},
          receitaPorMes: {}, volumePorMes: {}, iaPorMes: {},
        };
        meses.forEach(m => {
          const cel = n.meses[m]; if (!cel) return;
          const volume = cel.meta || 0;
          map[n.sku].volumePorMes[m] = (map[n.sku].volumePorMes[m] || 0) + volume;
          map[n.sku].receitaPorMes[m] = (map[n.sku].receitaPorMes[m] || 0) + volume * (cel.pmv || 0);
          map[n.sku].iaPorMes[m] = (map[n.sku].iaPorMes[m] || 0) + (cel.ia || 0);
        });
        return;
      }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((f: any) => walk(f, c));
    };
    (dados?.arvore || []).forEach((g: any) => walk(g, null));
    return Object.values(map).map(s => ({
      ...s,
      pmvPorMes: Object.fromEntries(meses.map(m => [
        m, s.volumePorMes[m] > 0 ? s.receitaPorMes[m] / s.volumePorMes[m] : 0,
      ])),
    })).sort((a, b) => a.descricao.localeCompare(b.descricao));
  }, [dados, naFaseSku, meses]);

  /* Edição do SKU no nível carteira inteira: rateia entre TODOS os clientes
     da carteira que compram esse SKU, proporcionalmente ao histórico. */
  const setSkuCarteira = (sku: string, mes: string, novoTotal: number) => {
    const clientesDoSku: Array<{ razao: string; pesoHist: number }> = [];
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') {
        if (n.sku === sku && n.meses[mes]) {
          clientesDoSku.push({ razao: rz || '', pesoHist: n.meses[mes].peso_historico ?? 0 });
        }
        return;
      }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((f: any) => walk(f, c));
    };
    (dados?.arvore || []).forEach((g: any) => walk(g, null));
    const pesos  = clientesDoSku.map(c => c.pesoHist);
    const partes = ratearMaiorResto(Math.max(0, Math.round(novoTotal)), pesos);
    setEdits(prev => {
      const next = { ...prev };
      clientesDoSku.forEach((c, i) => { next[keyOf(c.razao, sku, mes)] = partes[i]; });
      return next;
    });
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

  const exportarExcel = async () => {
    if (temEdicoes) await salvar();
    try {
      const resp = await axios.get('/api/v1/carteira/exportar', {
        responseType: 'blob',
        params: adminAlvo ? {
          responsavel: adminAlvo.nome,
          nivel_responsavel: adminAlvo.nivel,
        } : undefined,
      });
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
            {faseAtual && (!souAdmin || adminOperando) && (
              <div className="mt-2">
                <StepperFase funcao={faseFuncao} faseAtual={faseAtual} />
              </div>
            )}
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
            <div className="flex items-center rounded-xl border border-slate-200 bg-white p-0.5">
              <button onClick={() => setModoEdicao('caixas')}
                className={`px-2.5 py-1.5 rounded-lg text-[10px] font-black transition-colors
                  ${modoEdicao === 'caixas' ? 'bg-indigo-100 text-indigo-700' : 'text-slate-400 hover:text-slate-600'}`}>
                Caixas
              </button>
              <button onClick={() => setModoEdicao('valor')}
                className={`px-2.5 py-1.5 rounded-lg text-[10px] font-black transition-colors
                  ${modoEdicao === 'valor' ? 'bg-indigo-100 text-indigo-700' : 'text-slate-400 hover:text-slate-600'}`}>
                R$
              </button>
            </div>
            <button onClick={carregarAuditoria} disabled={carregandoAuditoria}
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-60">
              {carregandoAuditoria ? <Loader2 className="w-4 h-4 animate-spin" /> : <BarChart2 className="w-4 h-4" />}
              Auditoria
            </button>
            {/* Salvar — só existe como ação livre na fase Razão Social (última) ou para Admin */}
            {naFaseRazaoSocial && (
              <button onClick={salvar} disabled={!temEdicoes || salvando || bloqueado}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                  ${temEdicoes && !bloqueado ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
                {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
              </button>
            )}
            {/* Trancar/Avançar fase — SKU e Executivo (Gerente e Coordenador) */}
            {(naFaseSku || naFaseExecutivo) && (!bloqueado || (souAdmin && adminOperando)) && (
              <button onClick={avancarFase} disabled={avancando}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-60">
                {avancando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                {naFaseSku ? 'Trancar SKU e ratear p/ Executivos' : 'Trancar Executivo e ratear p/ Razão Social'}
              </button>
            )}
            {(naFaseSku || naFaseExecutivo) && bloqueado && !(souAdmin && adminOperando) && (
              <button disabled
                title="A Demanda Comercial precisa congelar o plano antes do rateio."
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-slate-100 text-slate-400 cursor-not-allowed">
                <Lock className="w-4 h-4" /> Aguardando liberação
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
                  <Lock className="w-4 h-4" /> Congelar etapa
                </button>
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

      {/* Na fase SKU o contrato é SKU agregado; Executivo só aparece depois
          do rateio. Nas fases seguintes a árvore detalhada é exibida. */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-8">
        {arvoreVisivelOuCompleta.length === 0 && busca.trim()
          ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Search className="w-8 h-8 mb-2 opacity-30" />
              <div className="text-sm font-bold">Nenhum resultado para "{busca}"</div>
              <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">Limpar busca</button>
            </div>
          )
          : naFaseSku ? <ListaSkuCarteira
            skus={skusAgregadosCarteira}
            meses={meses}
            somaSkuCarteira={somaSkuCarteira}
            setSkuCarteira={setSkuCarteira}
            bloqueado={bloqueado && !(souAdmin && adminOperando)}
            modoEdicao={modoEdicao}
            edits={edits}
            setDossieAlvo={setDossieAlvo}
            dossieAlvo={dossieAlvo}
          /> : arvoreVisivelOuCompleta.map((g: any) => (
            <NoArvore key={g.nome} node={g} nivel={0}
              meses={meses} abertas={abertas} toggle={toggle}
              valorCliente={valorCliente} setSkuExecutivo={setSkuExecutivo} setCliente={setCliente}
              somaSkuExecutivo={somaSkuExecutivo}
              bloqueado={bloqueado || faseAtual === 'RAZAO_SOCIAL'} edits={edits}
              modoEdicao={modoEdicao} setClienteValor={setClienteValor}
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

function PainelAuditoria({ dados, fechar }: { dados: any; fechar: () => void }) {
  const itens = dados?.itens || [];
  return (
    <div className="fixed inset-0 z-30 bg-slate-900/20" onClick={fechar}>
      <div className="absolute right-0 top-0 bottom-0 w-full max-w-3xl bg-white shadow-2xl overflow-y-auto p-6"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-base font-black text-slate-900">Auditoria de impacto</h2>
            <p className="text-xs text-slate-400">Meta atual comparada ao vol_bottomup · ciclo {dados?.ciclo}</p>
          </div>
          <button onClick={fechar} className="p-2 rounded-lg text-slate-400 hover:bg-slate-100"><X className="w-4 h-4" /></button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead><tr className="border-b border-slate-200 text-[10px] uppercase tracking-wider text-slate-400">
              <th className="py-2 pr-3">Coordenador / SKU</th><th>Mês</th><th>BottomUP cx</th><th>Meta cx</th>
              <th>Impacto cx</th><th>Impacto %</th><th>BottomUP R$</th><th>Meta R$</th><th>Impacto R$</th>
            </tr></thead>
            <tbody>{itens.map((i: any, n: number) => (
              <tr key={`${i.coordenador}-${i.sku}-${i.mes}-${n}`} className="border-b border-slate-100">
                <td className="py-2 pr-3"><b>{i.coordenador}</b><br /><span className="text-slate-400">{i.sku} · {i.descricao}</span></td>
                <td>{mesLabel(i.mes)}</td><td>{fmtCx(i.bottomup)}</td><td>{fmtCx(i.meta)}</td>
                <td className={i.delta_caixas > 0 ? 'text-emerald-600' : i.delta_caixas < 0 ? 'text-red-600' : ''}>{fmtCx(i.delta_caixas)}</td>
                <td>{i.impacto_percentual == null ? '—' : `${i.impacto_percentual.toFixed(2)}%`}</td>
                <td>{fmtRs(i.bottomup_rs)}</td><td>{fmtRs(i.meta_rs)}</td><td>{fmtRs(i.delta_rs)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {!itens.length && <div className="py-12 text-center text-sm text-slate-400">Nenhum dado de auditoria para esta carteira.</div>}
      </div>
    </div>
  );
}

/* ── LISTA AGREGADA DE SKUs DA CARTEIRA (fase SKU) ──────────────────
   Edição no nível SKU × Mês, um único total por SKU para toda a
   carteira (não há executivo/cliente ainda — só existem após o rateio
   desta fase). Dossiê disponível aqui é o dossiê agregado da carteira. */
function ListaSkuCarteira({ skus, meses, somaSkuCarteira, setSkuCarteira,
  bloqueado, modoEdicao, edits, setDossieAlvo, dossieAlvo }: any) {
  if (!skus.length) {
    return <div className="py-16 text-center text-slate-400 text-sm font-bold">Nenhum SKU na carteira.</div>;
  }
  return (
    <div>
      {skus.map((s: any) => (
        <div key={s.sku} className="mb-0.5 grid gap-2 px-3 py-2 items-center rounded-lg bg-white border border-slate-100 hover:border-slate-200"
          style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(130px, 1fr)) 40px` }}>
          <div className="min-w-0">
            <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
            <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
          </div>
          {meses.map((m: string) => {
            const total   = somaSkuCarteira(s.sku, m);
            const pmv     = s.pmvPorMes[m] || 0;
            const ia      = s.iaPorMes[m] || 0;
            const valor   = s.receitaPorMes[m] || 0;
            return (
              <div key={m} className="text-right">
                <div className="text-[9px] font-bold text-indigo-400 pr-2 mb-0.5">
                  {pmv ? fmtRs(total * pmv) : '—'}
                </div>
                <input
                  type="number"
                  value={modoEdicao === 'valor' ? Math.round(valor) : total}
                  disabled={bloqueado || (modoEdicao === 'valor' && pmv <= 0)}
                  onChange={e => {
                    const input = parseFloat(e.target.value) || 0;
                    const volume = modoEdicao === 'valor'
                      ? (pmv > 0 ? Math.round(input / pmv) : total)
                      : Math.round(input);
                    setSkuCarteira(s.sku, m, volume);
                  }}
                  title={modoEdicao === 'valor' && pmv <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                  inputMode={modoEdicao === 'valor' ? 'decimal' : 'numeric'}
                  className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                    border-transparent bg-transparent text-slate-700
                    ${bloqueado || (modoEdicao === 'valor' && pmv <= 0) ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
                />
                <div className="text-[9px] font-bold text-slate-300 pr-2">IA {fmtCx(ia)} cx</div>
              </div>
            );
          })}
          <button
            onClick={() => setDossieAlvo((prev: any) => prev?.sku === s.sku ? null : { sku: s.sku, descricao: s.descricao })}
            className={`justify-self-center p-1.5 rounded-lg transition-colors
              ${dossieAlvo?.sku === s.sku ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
            title="Ver dossiê do SKU (agregado da sua carteira)">
            <LineIcon className="w-4 h-4" />
          </button>
          {dossieAlvo?.sku === s.sku && (
            <div className="col-span-full ml-4 mr-2 mb-1">
              <DossieInferior
                prefixoApi="/api/v1/carteira"
                tipo="sku"
                id={s.sku}
                titulo={s.descricao}
                subtitulo={`${s.sku} · sua carteira`}
                paramsExtra={{}}
                onFechar={() => setDossieAlvo(null)}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ── NÓ RECURSIVO DA ÁRVORE ──────────────────────────────────────── */
function NoArvore({ node, nivel, meses, abertas, toggle,
  valorCliente, setSkuExecutivo, setCliente, somaSkuExecutivo,
  bloqueado, edits, setDossieAlvo, dossieAlvo, idPath, buscaAtiva,
  modoEdicao, setClienteValor }: any) {

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
                <div className="text-[9px] font-bold text-indigo-400 pr-2 mb-0.5">
                  {pmv ? fmtRs(totalSku * pmv) : '—'}
                </div>
                <input
                  type="number"
                  value={modoEdicao === 'valor' ? Math.round(valorAtual) : totalSku}
                  disabled={bloqueado}
                  onChange={e => {
                    const valor = parseFloat(e.target.value) || 0;
                    const volume = modoEdicao === 'valor'
                      ? (pmv > 0 ? Math.round(valor / pmv) : totalSku)
                      : Math.round(valor);
                    setSkuExecutivo(clientesInfo, sku, m, volume);
                  }}
                  title={modoEdicao === 'valor' && pmv <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                  inputMode={modoEdicao === 'valor' ? 'decimal' : 'numeric'}
                  className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                    ${temEdit ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                    ${bloqueado || (modoEdicao === 'valor' && pmv <= 0) ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
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
                      type="number"
                      value={modoEdicao === 'valor' ? Math.round(val * (cel.pmv || 0)) : val}
                      disabled={bloqueado || (modoEdicao === 'valor' && (cel.pmv || 0) <= 0)}
                      onChange={e => {
                        const valor = parseFloat(e.target.value) || 0;
                        if (modoEdicao === 'valor') {
                          setClienteValor(cli.nome, sku, m, valor, cel.pmv || 0);
                        } else {
                          setCliente(cli.nome, sku, m, Math.round(valor));
                        }
                      }}
                      title={modoEdicao === 'valor' && (cel.pmv || 0) <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                      inputMode={modoEdicao === 'valor' ? 'decimal' : 'numeric'}
                      className={`w-full text-right text-xs font-bold rounded-md px-2 py-1 border transition-colors
                        ${editado ? 'border-violet-300 bg-violet-50 text-violet-700' : 'border-transparent bg-transparent text-slate-500'}
                        ${bloqueado || (modoEdicao === 'valor' && (cel.pmv || 0) <= 0) ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-100 focus:border-violet-400 focus:bg-white focus:outline-none'}`}
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
            modoEdicao={modoEdicao} setClienteValor={setClienteValor}
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
          buscaAtiva={buscaAtv}
          modoEdicao={modoEdicao} setClienteValor={setClienteValor} />
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

        {/* CABEÇALHO */}
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