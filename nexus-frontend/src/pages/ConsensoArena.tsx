import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel,
  getSortedRowModel, SortingState, ColumnDef
} from '@tanstack/react-table';
import {
  Loader2, ChevronDown, ChevronRight, Lock, Unlock, Search, BarChart3,
  TrendingUp, TrendingDown, Target, Users, Store, Package, Check, ShieldCheck,
  Save, Layers, DollarSign, Box, AlertTriangle, History, Briefcase, Sparkles
} from 'lucide-react';
import axios from 'axios';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';

// =====================================================================
// HELPERS
// =====================================================================
const fmtMoeda = (v: any) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(Number(v) || 0));
const fmtVol = (v: any) => (Math.round(Number(v) || 0)).toLocaleString('pt-BR');
const calcVar = (atual: number, base: number) => base > 0 ? ((atual - base) / base) * 100 : 0;

const VarBadge = ({ atual = 0, base = 0, titulo = 'vs Orçamento' }: { atual?: number, base?: number, titulo?: string }) => {
  if (base === 0) return null;
  const v = calcVar(atual, base);
  const pos = v >= 0;
  return (
    <span title={titulo} className={`text-[9px] font-black flex items-center gap-0.5 px-1.5 py-0.5 rounded ${pos ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
      {pos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />} {Math.abs(v).toFixed(1)}%
    </span>
  );
};

// Input dual: alterna entre CAIXAS e REAIS. Preserva o valor exato.
const DualInput = ({
  volume, pmvMedio, disabled, unidade, onCommitVolume, onCommitValor
}: {
  volume: number; pmvMedio: number; disabled: boolean; unidade: 'cx' | 'rs';
  onCommitVolume: (v: number) => void; onCommitValor: (v: number) => void;
}) => {
  const valorAtual = unidade === 'cx' ? volume : Math.round(volume * pmvMedio);
  const [local, setLocal] = useState(fmtVol(valorAtual));
  const [foco, setFoco] = useState(false);

  useEffect(() => {
    if (!foco) setLocal(unidade === 'cx' ? fmtVol(volume) : fmtVol(Math.round(volume * pmvMedio)));
  }, [volume, pmvMedio, unidade, foco]);

  const onFocus = () => { setFoco(true); setLocal(local.replace(/\./g, '')); };
  const onBlur = () => {
    setFoco(false);
    const num = parseInt(local.replace(/\D/g, ''), 10) || 0;
    if (unidade === 'cx') { setLocal(fmtVol(num)); onCommitVolume(num); }
    else { setLocal(fmtVol(num)); onCommitValor(num); }
  };
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };

  return (
    <div className="relative">
      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[9px] font-black text-slate-400">{unidade === 'rs' ? 'R$' : ''}</span>
      <input
        type="text" value={local} disabled={disabled}
        onFocus={onFocus} onBlur={onBlur} onKeyDown={onKey} onChange={(e) => setLocal(e.target.value)}
        className={`w-full text-center rounded-lg py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500
          ${unidade === 'rs' ? 'pl-6' : ''}
          ${disabled ? 'text-slate-900 font-black bg-transparent cursor-not-allowed' : 'text-indigo-700 font-black bg-indigo-50 shadow-inner'}`}
      />
    </div>
  );
};

// =====================================================================
// TIPAGEM
// =====================================================================
interface MesNode {
  mes_banco: string; mes_str: string;
  vol_meta: number; fat_meta: number;
  vol_comercial: number; fat_comercial: number;
  rec_orcada: number;
  vol_hist: number; fat_hist: number;
}
interface Folha { id: number; pmv: number; vol_meta: number; }
interface Node {
  tipo: 'gerente' | 'coordenador' | 'vendedor' | 'cliente' | 'produto';
  nome: string; produto?: string; chave: string;
  meses: MesNode[];
  folhas?: { [mes: string]: Folha[] };
  subRows?: Node[];
}

// =====================================================================
// COMPONENTE
// =====================================================================
export default function ConsensoArena(_props: { usuarioSessao?: any } = {}) {
  const [dados, setDados] = useState<Node[]>([]);
  const [meses, setMeses] = useState<{ mes_banco: string; mes_str: string }[]>([]);
  const [ciclo, setCiclo] = useState('');
  const [escopo, setEscopo] = useState<{ nivel: string; nome: string | null }>({ nivel: '', nome: null });
  const [meuCongelamento, setMeuCongelamento] = useState('ABERTO');
  const [etapaBloqueada, setEtapaBloqueada] = useState(false);
  const [etapaAnteriorPendente, setEtapaAnteriorPendente] = useState(false);
  const [cadeados, setCadeados] = useState<any[]>([]);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busca, setBusca] = useState('');
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [unidade, setUnidade] = useState<'cx' | 'rs'>('cx');
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dossieCache, setDossieCache] = useState<{ [chave: string]: any }>({});
  const [dossieLoading, setDossieLoading] = useState<string | null>(null);

  // Edições locais por folha: { [id]: novoVol }
  const [edicoes, setEdicoes] = useState<{ [id: number]: number }>({});

  const isAdmin = escopo.nivel === 'Administrador';
  const isGerente = escopo.nivel === 'Gerente';
  const isCoordenador = escopo.nivel === 'Coordenador';
  const modoHierarquia = isAdmin || isGerente;

  const isLocked = etapaBloqueada || etapaAnteriorPendente || (meuCongelamento === 'CONGELADO' && !isAdmin && !isGerente);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const [dRes, cRes] = await Promise.all([
        axios.get('/api/v1/consensus/micro'),
        axios.get('/api/v1/consensus/micro/cadeados'),
      ]);
      setDados(dRes.data.dados || []);
      setMeses(dRes.data.meses || []);
      setCiclo(dRes.data.ciclo_ativo || '');
      setEscopo(dRes.data.escopo || { nivel: '', nome: null });
      setMeuCongelamento(dRes.data.meu_congelamento || 'ABERTO');
      setEtapaBloqueada(dRes.data.etapa_bloqueada || false);
      setEtapaAnteriorPendente(dRes.data.etapa_anterior_pendente || false);
      setCadeados(cRes.data.cadeados || []);
      setEdicoes({});
      setExpanded({});
    } catch (e) {
      console.error('Erro ao carregar Consenso:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  const temPend = Object.keys(edicoes).length > 0;

  // --- volume vivo de uma folha (edição ou banco) ---
  const volFolha = useCallback((f: Folha) => {
    const e = edicoes[f.id];
    return e !== undefined ? e : f.vol_meta;
  }, [edicoes]);

  // --- agrega um nó no mês: soma folhas vivas de todas as subárvores ---
  const coletaFolhas = useCallback((node: Node, mes: string): Folha[] => {
    if (node.tipo === 'produto') return node.folhas?.[mes] || [];
    let acc: Folha[] = [];
    (node.subRows || []).forEach(s => { acc = acc.concat(coletaFolhas(s, mes)); });
    return acc;
  }, []);

  const volNode = useCallback((node: Node, mes: string) => {
    return coletaFolhas(node, mes).reduce((a, f) => a + volFolha(f), 0);
  }, [coletaFolhas, volFolha]);

  const fatNode = useCallback((node: Node, mes: string) => {
    return coletaFolhas(node, mes).reduce((a, f) => a + volFolha(f) * f.pmv, 0);
  }, [coletaFolhas, volFolha]);

  const pmvMedioNode = useCallback((node: Node, mes: string) => {
    const folhas = coletaFolhas(node, mes);
    const v = folhas.reduce((a, f) => a + volFolha(f), 0);
    const fat = folhas.reduce((a, f) => a + volFolha(f) * f.pmv, 0);
    return v > 0 ? fat / v : (folhas.length ? folhas[0].pmv : 0);
  }, [coletaFolhas, volFolha]);

  const refMes = useCallback((node: Node, mes: string): MesNode | undefined => {
    return node.meses.find(m => m.mes_banco === mes);
  }, []);

  // Dossiê sob demanda: busca o histórico do nó só ao expandir (não pesa o /dados).
  const toggleDossie = useCallback(async (node: Node) => {
    const chave = node.chave;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    if (dossieCache[chave]) return; // já carregado
    setDossieLoading(chave);
    try {
      const params: any = { tipo: node.tipo };
      if (node.tipo === 'produto') { params.sku = node.produto; params.cliente = node.nome; }
      else if (node.tipo === 'cliente') { params.cliente = node.nome; }
      const res = await axios.get('/api/v1/consensus/micro/dossie', { params });
      setDossieCache(prev => ({ ...prev, [chave]: res.data }));
    } catch (e) {
      console.error('Erro ao carregar dossiê:', e);
      setDossieCache(prev => ({ ...prev, [chave]: { labels: [], realizado: [], ia: [], lag1: [] } }));
    } finally {
      setDossieLoading(null);
    }
  }, [chartExpanded, dossieCache]);

  // Monta os dados do gráfico: histórico (do cache) + linha Meta dinâmica (M2-M4).
  const getDossieChartData = useCallback((node: Node) => {
    const g = dossieCache[node.chave];
    if (!g?.labels) return [];
    const totalPts = g.labels.length;
    const idxPlano = [totalPts - 3, totalPts - 2, totalPts - 1]; // M2, M3, M4
    return g.labels.map((label: string, i: number) => {
      let meta: number | null = null;
      const pos = idxPlano.indexOf(i);
      if (pos >= 0 && meses[pos]) meta = volNode(node, meses[pos].mes_banco);
      return {
        name: label,
        realizado: g.realizado?.[i] ?? null,
        ia: g.ia?.[i] ?? null,
        lag1: g.lag1?.[i] ?? null,
        meta,
      };
    });
  }, [dossieCache, meses, volNode]);

  // --- editar um nó em CAIXAS: rateia proporcional ao vol_meta atual das folhas ---
  const editarNodeVolume = (node: Node, mes: string, novoTotal: number) => {
    const folhas = coletaFolhas(node, mes);
    if (!folhas.length) return;
    const pesos = folhas.map(f => volFolha(f));
    const soma = pesos.reduce((a, b) => a + b, 0);
    const partes = maiorRestoLocal(novoTotal, soma > 0 ? pesos : folhas.map(() => 1));
    const novo = { ...edicoes };
    folhas.forEach((f, i) => { novo[f.id] = partes[i]; });
    setEdicoes(novo);
  };

  // --- editar um nó em REAIS: distribui por faturamento atual, converte por PMV ---
  const editarNodeValor = (node: Node, mes: string, novoValorRS: number) => {
    const folhas = coletaFolhas(node, mes);
    if (!folhas.length) return;
    const fatAtual = folhas.map(f => volFolha(f) * f.pmv);
    const somaFat = fatAtual.reduce((a, b) => a + b, 0);
    let volsEq: number[];
    if (somaFat > 0) {
      volsEq = folhas.map((f, i) => f.pmv > 0 ? (novoValorRS * (fatAtual[i] / somaFat)) / f.pmv : 0);
    } else {
      const validas = folhas.filter(f => f.pmv > 0).length || 1;
      volsEq = folhas.map(f => f.pmv > 0 ? (novoValorRS / validas) / f.pmv : 0);
    }
    const totalCx = Math.round(volsEq.reduce((a, b) => a + b, 0));
    const partes = maiorRestoLocal(totalCx, volsEq);
    const novo = { ...edicoes };
    folhas.forEach((f, i) => { novo[f.id] = partes[i]; });
    setEdicoes(novo);
  };

  // --- coleta ids+volumes editados para enviar ao backend ---
  const montarAjustes = () => {
    // Agrupa por mês não é necessário: enviamos por folha com o novo volume,
    // mas o backend rateia por nó. Aqui mandamos as folhas afetadas diretamente
    // como um único ajuste "manual" por conjunto de ids com novo_volume=soma.
    // Simplificação robusta: um ajuste por folha individual (id única).
    return Object.entries(edicoes).map(([id, vol]) => ({
      ids_folhas: [Number(id)],
      novo_volume: Number(vol),
      mes_projetado: '',   // não usado quando ids_folhas é explícito por folha
    }));
  };

  // ============ ÁRVORE (filtrada por busca) ============
  const arvore = useMemo(() => {
    const term = busca.toLowerCase();
    if (!term) return dados;
    const filtra = (nodes: Node[]): Node[] => {
      const out: Node[] = [];
      for (const n of nodes) {
        const selfMatch = (n.nome + ' ' + (n.produto || '')).toLowerCase().includes(term);
        const kids = n.subRows ? filtra(n.subRows) : undefined;
        if (selfMatch || (kids && kids.length)) {
          out.push({ ...n, subRows: kids && kids.length ? kids : n.subRows });
        }
      }
      return out;
    };
    return filtra(dados);
  }, [dados, busca]);

  // ============ CARDS DE COMANDO ============
  const comando = useMemo(() => {
    let volMeta = 0, fatMeta = 0, volCom = 0, fatCom = 0, volHist = 0, fatHist = 0;
    // Orçamento é por SKU (não por cliente): dedup por sku|mes, conta uma vez.
    const orcPorSkuMes = new Map<string, number>();
    const percorre = (nodes: Node[]) => {
      nodes.forEach(n => {
        if (n.tipo === 'produto') {
          n.meses.forEach(m => {
            volCom += m.vol_comercial; fatCom += m.fat_comercial;
            volHist += m.vol_hist; fatHist += m.fat_hist;
            orcPorSkuMes.set(`${n.produto}|${m.mes_banco}`, m.rec_orcada);
          });
          meses.forEach(mc => {
            volMeta += volNode(n, mc.mes_banco);
            fatMeta += fatNode(n, mc.mes_banco);
          });
        }
        if (n.subRows) percorre(n.subRows);
      });
    };
    percorre(dados);
    let orc = 0;
    orcPorSkuMes.forEach(v => { orc += v; });
    return { volMeta, fatMeta, volCom, fatCom, orc, volHist, fatHist };
  }, [dados, meses, volNode, fatNode]);

  // ============ MACRO (barras por mês: Meta vs Comercial vs Orçamento vs Hist) ============
  const chartMacro = useMemo(() => {
    return meses.map(mc => {
      let fatMeta = 0, fatCom = 0, fatHist = 0;
      const orcPorSku = new Map<string, number>();
      const percorre = (nodes: Node[]) => nodes.forEach(n => {
        if (n.tipo === 'produto') {
          fatMeta += fatNode(n, mc.mes_banco);
          const m = n.meses.find(x => x.mes_banco === mc.mes_banco);
          fatCom += m?.fat_comercial || 0; fatHist += m?.fat_hist || 0;
          if (m && n.produto) orcPorSku.set(n.produto, m.rec_orcada);
        }
        if (n.subRows) percorre(n.subRows);
      });
      percorre(dados);
      let orc = 0;
      orcPorSku.forEach(v => { orc += v; });
      return {
        name: mc.mes_str,
        Meta: Math.round(fatMeta), Comercial: Math.round(fatCom),
        Orcamento: Math.round(orc), 'Ano Anterior': Math.round(fatHist),
      };
    });
  }, [dados, meses, fatNode]);

  // ============ PROGRESSO DE CADEADOS ============
  const progresso = useMemo(() => {
    const total = cadeados.length;
    const fechados = cadeados.filter(c => c.status === 'CONGELADO').length;
    return { total, fechados };
  }, [cadeados]);

  // ============ AÇÕES ============
  const salvar = async () => {
    if (!temPend) return alert('Nenhuma alteração pendente.');
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/micro/salvar', { ajustes: montarAjustes() });
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao salvar.');
    } finally { setSaving(false); }
  };

  const congelar = async (nomeAlvo?: string, nivelAlvo?: string) => {
    const alvoTxt = nomeAlvo || escopo.nome || 'sua carteira';
    // Cerimônia: resumo do compromisso antes de confirmar.
    const totalFat = comando.fatMeta;
    const varOrc = calcVar(comando.fatMeta, comando.orc);
    const msg = `CONGELAR ${alvoTxt}\n\n` +
      `Faturamento comprometido: ${fmtMoeda(totalFat)}\n` +
      `Variação vs Orçamento: ${varOrc >= 0 ? '+' : ''}${varOrc.toFixed(1)}%\n\n` +
      `Ao congelar, sua carteira fica somente-leitura. Um superior pode reabri-la se necessário. Confirmar?`;
    if (!window.confirm(msg)) return;
    if (temPend && !window.confirm('Há alterações não salvas que serão perdidas. Continuar?')) return;

    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/micro/congelar', { nome_alvo: nomeAlvo || null, nivel_alvo: nivelAlvo || null });
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao congelar.');
    } finally { setSaving(false); }
  };

  const reabrir = async (nomeAlvo: string) => {
    if (!window.confirm(`Reabrir a carteira de "${nomeAlvo}"? Ela poderá ser editada novamente.`)) return;
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/micro/reabrir', { nome_alvo: nomeAlvo });
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao reabrir.');
    } finally { setSaving(false); }
  };

  const congelarResponsavel = async (nomeAlvo: string, nivelAlvo: string) => {
    if (!window.confirm(`Congelar a carteira de "${nomeAlvo}"? Ela ficará somente-leitura até ser reaberta.`)) return;
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/micro/congelar', { nome_alvo: nomeAlvo, nivel_alvo: nivelAlvo });
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao congelar.');
    } finally { setSaving(false); }
  };

  const publicarEtapa = async () => {
    const msg = `PUBLICAR A ETAPA DE METAS\n\n` +
      `Isto passa o bastão para o Supply e encerra as edições de todos.\n` +
      `${progresso.fechados}/${progresso.total} carteiras congeladas.\n\n` +
      `Confirmar a publicação do consenso?`;
    if (!window.confirm(msg)) return;
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/micro/publicar-etapa');
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao publicar etapa.');
    } finally { setSaving(false); }
  };

  // ============ COLUNAS ============
  const columns = useMemo<ColumnDef<any>[]>(() => {
    if (!dados.length) return [];
    const iconePorTipo = (t: string) =>
      t === 'gerente' ? <Briefcase className="w-3.5 h-3.5 text-purple-600" /> :
      t === 'coordenador' ? <Users className="w-3.5 h-3.5 text-indigo-600" /> :
      t === 'vendedor' ? <Users className="w-3.5 h-3.5 text-blue-500" /> :
      t === 'cliente' ? <Store className="w-3.5 h-3.5 text-slate-500" /> :
      <Package className="w-3.5 h-3.5 text-slate-400" />;

    const cols: ColumnDef<any>[] = [{
      id: 'nome',
      header: modoHierarquia ? 'Hierarquia > Cliente > SKU' : 'Cliente > SKU',
      accessorFn: (r: any) => r.nome,
      cell: (info: any) => {
        const row = info.row;
        const { tipo, nome, produto, chave } = row.original;
        const temDossie = tipo === 'cliente' || tipo === 'produto';
        return (
          <div style={{ paddingLeft: `${row.depth * 24}px` }} className="flex items-center gap-2.5 py-2 min-w-[280px]">
            {row.getCanExpand() ? (
              <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-200 rounded-lg text-slate-500">
                {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
            ) : <div className="w-6" />}
            {temDossie ? (
              <button onClick={() => toggleDossie(row.original)} className={`p-1 rounded-lg border transition-all ${chartExpanded === chave ? 'bg-indigo-100 text-indigo-600 border-indigo-200' : 'bg-white hover:bg-slate-50 text-slate-400 border-slate-200'}`} title="Dossiê (histórico 24m)">
                <BarChart3 className="w-3.5 h-3.5" />
              </button>
            ) : <div className="w-6" />}
            <div className={`w-7 h-7 flex items-center justify-center rounded-lg border ${tipo === 'gerente' || tipo === 'coordenador' ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
              {iconePorTipo(tipo)}
            </div>
            <div className="flex flex-col overflow-hidden">
              <span className={`text-[12px] truncate ${tipo === 'produto' ? 'font-bold text-slate-600' : 'font-black text-slate-800'}`} title={nome}>{nome}</span>
              {produto && <span className="text-[9px] text-slate-400 font-black tracking-widest">{produto}</span>}
            </div>
          </div>
        );
      }
    }];

    meses.forEach(mc => {
      cols.push({
        id: `mes_${mc.mes_banco}`,
        header: mc.mes_str,
        accessorFn: (r: any) => volNode(r, mc.mes_banco),
        cell: (info: any) => {
          const node: Node = info.row.original;
          const vol = volNode(node, mc.mes_banco);
          const fat = fatNode(node, mc.mes_banco);
          const pmvM = pmvMedioNode(node, mc.mes_banco);
          const ref = refMes(node, mc.mes_banco);
          const orc = ref?.rec_orcada || 0;
          const com = ref?.vol_comercial || 0;
          const hist = ref?.vol_hist || 0;
          const editavel = !isLocked;

          return (
            <div className="flex flex-col items-center min-w-[150px] gap-1">
              <div className="w-32">
                <DualInput
                  volume={vol} pmvMedio={pmvM} disabled={!editavel} unidade={unidade}
                  onCommitVolume={(v) => editarNodeVolume(node, mc.mes_banco, v)}
                  onCommitValor={(v) => editarNodeValor(node, mc.mes_banco, v)}
                />
              </div>
              {/* Faturamento sempre visível + âncora orçamento */}
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-bold text-emerald-600">{fmtMoeda(fat)}</span>
                {orc > 0 && <VarBadge atual={fat} base={orc} titulo="Meta vs Orçamento" />}
              </div>
              {/* Referências: Comercial e Ano anterior */}
              <div className="flex items-center gap-1.5 text-[9px] font-bold text-slate-400">
                <span title="Comercial (Bottom-Up)">Com: {fmtVol(com)}</span>
                <span title="Realizado ano anterior">Ant: {fmtVol(hist)}</span>
              </div>
            </div>
          );
        }
      });
    });
    return cols;
  }, [dados, meses, unidade, isLocked, modoHierarquia, volNode, fatNode, pmvMedioNode, refMes, edicoes, chartExpanded, toggleDossie]);

  const table = useReactTable({
    data: arvore, columns,
    state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: (r: any) => r.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) {
    return <div className="min-h-screen bg-[#f8fafc] flex items-center justify-center"><Loader2 className="w-12 h-12 text-indigo-600 animate-spin" /></div>;
  }

  const varComando = calcVar(comando.fatMeta, comando.orc);

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">

        {/* HEADER */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 relative overflow-hidden">
          {etapaBloqueada && (
            <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase z-10 bg-emerald-500">
              <ShieldCheck className="w-4 h-4" /> CONSENSO PUBLICADO · BASTÃO NO SUPPLY
            </div>
          )}
          {etapaAnteriorPendente && !etapaBloqueada && (
            <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase z-10 bg-amber-500">
              <AlertTriangle className="w-4 h-4" /> O BOTTOM-UP (GERÊNCIA COMERCIAL) AINDA NÃO FOI CONGELADO · EDIÇÃO BLOQUEADA
            </div>
          )}
          {!etapaBloqueada && !etapaAnteriorPendente && meuCongelamento === 'CONGELADO' && !modoHierarquia && (
            <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase z-10 bg-indigo-500">
              <Lock className="w-4 h-4" /> SUA CARTEIRA ESTÁ CONGELADA
            </div>
          )}
          <div className={etapaBloqueada || (meuCongelamento === 'CONGELADO' && !modoHierarquia) ? 'pt-6' : ''}>
            <div className="flex items-center gap-4">
              <div className="p-3 bg-indigo-50 rounded-2xl"><Sparkles className="w-8 h-8 text-indigo-600" /></div>
              <div>
                <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">Consenso de Metas</h1>
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mt-1">
                  Ciclo {ciclo} · {escopo.nivel} {escopo.nome ? `· ${escopo.nome}` : ''} · Meta = Orçamento
                </p>
              </div>
            </div>
          </div>

          <div className={`flex items-center gap-3 ${etapaBloqueada || (meuCongelamento === 'CONGELADO' && !modoHierarquia) ? 'pt-6' : ''}`}>
            {/* Alternador de unidade */}
            <div className="flex items-center bg-slate-100 rounded-2xl p-1">
              <button onClick={() => setUnidade('cx')} className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-black transition-all ${unidade === 'cx' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-400'}`}>
                <Box className="w-3.5 h-3.5" /> CAIXAS
              </button>
              <button onClick={() => setUnidade('rs')} className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-black transition-all ${unidade === 'rs' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-400'}`}>
                <DollarSign className="w-3.5 h-3.5" /> REAIS
              </button>
            </div>

            {!etapaBloqueada && !isLocked && (
              <>
                <button onClick={salvar} disabled={saving || !temPend}
                  className={`flex items-center gap-2 font-bold py-3 px-5 rounded-2xl transition-all shadow-sm border ${temPend ? 'bg-slate-50 hover:bg-slate-100 text-slate-700 border-slate-200' : 'bg-slate-50 text-slate-300 border-slate-100 cursor-not-allowed'}`}>
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Salvar
                </button>
                <button onClick={() => congelar()} disabled={saving}
                  className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white font-bold py-3 px-6 rounded-2xl shadow-lg shadow-indigo-600/30 transition-all">
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />} Congelar Carteira
                </button>
              </>
            )}
            {isAdmin && !etapaBloqueada && (
              <button onClick={publicarEtapa} disabled={saving}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-bold py-3 px-6 rounded-2xl shadow-lg shadow-emerald-600/30 transition-all">
                <ShieldCheck className="w-4 h-4" /> Publicar Etapa
              </button>
            )}
          </div>
        </div>

        {/* CARDS DE COMANDO */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="p-6 rounded-[28px] shadow-sm bg-white border-2 border-indigo-200 flex flex-col justify-between min-h-[160px] hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="flex justify-between items-start">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-indigo-500">Minha Meta</h3>
              <div className="p-2 rounded-xl bg-indigo-50"><Target className="w-4 h-4 text-indigo-500" /></div>
            </div>
            <div className="mt-auto pt-3">
              <p className="text-xl font-black leading-none tracking-tighter text-indigo-700">{fmtVol(comando.volMeta)} <span className="text-[9px] opacity-60">CX</span></p>
              <p className="text-[11px] font-bold text-slate-400 mt-1">{fmtMoeda(comando.fatMeta)}</p>
            </div>
          </div>
          <div className="p-6 rounded-[28px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[160px] hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="flex justify-between items-start">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Comercial (BU)</h3>
              <div className="p-2 rounded-xl bg-slate-50"><Users className="w-4 h-4 opacity-70" /></div>
            </div>
            <div className="mt-auto pt-3">
              <p className="text-xl font-black leading-none tracking-tighter">{fmtVol(comando.volCom)} <span className="text-[9px] opacity-60">CX</span></p>
              <p className="text-[11px] font-bold text-slate-400 mt-1">{fmtMoeda(comando.fatCom)}</p>
            </div>
          </div>
          <div className="p-6 rounded-[28px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[160px] hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="flex justify-between items-start">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Ano Anterior</h3>
              <div className="p-2 rounded-xl bg-slate-50"><History className="w-4 h-4 opacity-70" /></div>
            </div>
            <div className="mt-auto pt-3">
              <p className="text-xl font-black leading-none tracking-tighter">{fmtVol(comando.volHist)} <span className="text-[9px] opacity-60">CX</span></p>
              <p className="text-[11px] font-bold text-slate-400 mt-1">{fmtMoeda(comando.fatHist)}</p>
            </div>
          </div>
          <div className="p-6 rounded-[28px] shadow-sm bg-slate-900 text-white flex flex-col justify-between min-h-[160px] relative overflow-hidden hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="absolute top-0 right-0 w-28 h-28 bg-gradient-to-bl from-indigo-500/20 to-transparent rounded-full -mr-8 -mt-8 blur-xl" />
            <div className="flex justify-between items-start z-10">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-indigo-300">Meta vs Orçamento</h3>
              <div className="p-2 rounded-xl bg-slate-800">{varComando >= 0 ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}</div>
            </div>
            <div className="mt-auto pt-3 z-10">
              <p className={`text-2xl font-black leading-none tracking-tighter ${varComando >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {varComando >= 0 ? '+' : ''}{varComando.toFixed(1)}%
              </p>
              <p className="text-[9px] font-bold text-slate-400 mt-2 uppercase tracking-widest">Orçamento: {fmtMoeda(comando.orc)}</p>
            </div>
          </div>
        </div>

        {/* MACRO */}
        <div className="bg-white rounded-[32px] shadow-sm border border-slate-100 p-8 mb-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 bg-indigo-50 text-indigo-600 rounded-xl"><BarChart3 className="w-5 h-5" /></div>
            <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest">Faturamento por Mês — Meta · Comercial · Orçamento · Ano Anterior</h4>
          </div>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartMacro} margin={{ top: 10, right: 20, bottom: 5, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="name" stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 12, fontWeight: 700 }} tickLine={false} axisLine={false} />
                <YAxis stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={false} tickFormatter={(v) => new Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(v)} />
                <Tooltip contentStyle={{ backgroundColor: '#fff', borderColor: '#e2e8f0', borderRadius: '12px', fontSize: '13px' }} formatter={(v: any) => fmtMoeda(v)} />
                <Legend wrapperStyle={{ paddingTop: '16px', fontSize: '12px', fontWeight: 700 }} iconType="circle" />
                <Bar dataKey="Ano Anterior" fill="#cbd5e1" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Comercial" fill="#a5b4fc" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Meta" fill="#6366f1" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Orcamento" name="Orçamento" fill="#0f172a" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* PAINEL DE CADEADOS (gerente/admin) */}
        {modoHierarquia && (
          <div className="bg-white rounded-[32px] shadow-sm border border-slate-100 p-8 mb-8">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-indigo-50 text-indigo-600 rounded-xl"><Lock className="w-5 h-5" /></div>
                <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest">Progresso do Consenso</h4>
              </div>
              <span className="text-sm font-black text-slate-600">{progresso.fechados}/{progresso.total} carteiras congeladas</span>
            </div>
            {cadeados.length === 0 ? (
              <p className="text-sm text-slate-400 font-medium">Nenhum responsável com carteira neste ciclo.</p>
            ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {cadeados.map((c, i) => (
                <div key={i} className="flex items-center justify-between p-4 rounded-2xl border border-slate-100 bg-slate-50">
                  <div className="flex items-center gap-2.5 overflow-hidden">
                    <div className={`w-8 h-8 flex items-center justify-center rounded-xl shrink-0 ${c.status === 'CONGELADO' ? 'bg-emerald-100 text-emerald-600' : 'bg-slate-200 text-slate-400'}`}>
                      {c.status === 'CONGELADO' ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
                    </div>
                    <div className="flex flex-col overflow-hidden">
                      <span className="text-[12px] font-black text-slate-700 truncate" title={c.nome}>{c.nome}</span>
                      <span className="text-[9px] font-bold text-slate-400 uppercase tracking-wider">{c.nivel} · {c.status === 'CONGELADO' ? (c.quando || 'congelado') : 'aberto'}</span>
                    </div>
                  </div>
                  {!etapaBloqueada && (
                    c.status === 'CONGELADO' ? (
                      <button onClick={() => reabrir(c.nome)} className="flex items-center gap-1.5 text-[10px] font-black text-amber-600 bg-amber-50 border border-amber-200 px-2.5 py-1.5 rounded-lg hover:bg-amber-100 transition-all shrink-0">
                        <Unlock className="w-3 h-3" /> Reabrir
                      </button>
                    ) : (
                      <button onClick={() => congelarResponsavel(c.nome, c.nivel)} className="flex items-center gap-1.5 text-[10px] font-black text-indigo-600 bg-indigo-50 border border-indigo-200 px-2.5 py-1.5 rounded-lg hover:bg-indigo-100 transition-all shrink-0">
                        <Lock className="w-3 h-3" /> Congelar
                      </button>
                    )
                  )}
                </div>
              ))}
            </div>
            )}
          </div>
        )}

        {/* BUSCA */}
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar cliente, SKU ou responsável..."
              className="w-full bg-white border border-slate-200 rounded-2xl pl-11 pr-4 py-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 shadow-sm" />
          </div>
          {temPend && (
            <span className="flex items-center gap-1.5 text-[11px] font-black text-amber-600 bg-amber-50 border border-amber-200 px-3 py-2 rounded-xl">
              <AlertTriangle className="w-3.5 h-3.5" /> {Object.keys(edicoes).length} alterações não salvas
            </span>
          )}
        </div>

        {/* TABELA */}
        <div className="bg-white rounded-[32px] shadow-sm border border-slate-100 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id} className="border-b border-slate-100">
                    {hg.headers.map(header => (
                      <th key={header.id} onClick={header.column.getToggleSortingHandler()}
                        className="text-left px-4 py-4 text-[11px] font-black text-slate-400 uppercase tracking-widest cursor-pointer select-none hover:text-slate-600 whitespace-nowrap">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {{ asc: ' ↑', desc: ' ↓' }[header.column.getIsSorted() as string] ?? ''}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => {
                  const chave = row.original.chave;
                  const aberto = chartExpanded === chave;
                  const carregando = dossieLoading === chave;
                  return (
                    <React.Fragment key={row.id}>
                      <tr className={`border-b border-slate-50 transition-colors ${row.original.tipo === 'gerente' || row.original.tipo === 'coordenador' ? 'bg-slate-50/50' : 'hover:bg-indigo-50/30'}`}>
                        {row.getVisibleCells().map(cell => (
                          <td key={cell.id} className="px-4">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                        ))}
                      </tr>
                      {aberto && (
                        <tr>
                          <td colSpan={row.getVisibleCells().length} className="bg-slate-50/60 px-8 py-6 border-b border-slate-100">
                            <div className="flex items-center gap-2 mb-4">
                              <BarChart3 className="w-4 h-4 text-indigo-500" />
                              <span className="text-xs font-black text-slate-600 uppercase tracking-widest">Dossiê · Histórico 24 meses · {row.original.nome}</span>
                            </div>
                            {/* Cards de contexto: consolidado dos 3 meses do nó */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
                              {(() => {
                                const node = row.original;
                                let vMeta = 0, vCom = 0, vHist = 0;
                                meses.forEach(mc => {
                                  vMeta += volNode(node, mc.mes_banco);
                                  const m = refMes(node, mc.mes_banco);
                                  vCom += m?.vol_comercial || 0; vHist += m?.vol_hist || 0;
                                });
                                const cards = [
                                  { l: 'Meta (Consenso)', v: vMeta, cor: 'text-indigo-700' },
                                  { l: 'Comercial (BU)', v: vCom, cor: 'text-slate-700' },
                                  { l: 'Ano Anterior', v: vHist, cor: 'text-slate-500' },
                                  { l: 'Meta vs Ano Ant.', v: null, pct: vHist > 0 ? ((vMeta - vHist) / vHist) * 100 : 0, cor: '' },
                                ];
                                return cards.map((c, ci) => (
                                  <div key={ci} className={`rounded-2xl border p-3 shadow-sm ${ci === 3 ? 'bg-slate-900' : 'bg-white border-slate-100'}`}>
                                    <p className={`text-[9px] font-black uppercase tracking-widest mb-1 ${ci === 3 ? 'text-indigo-300' : 'text-slate-400'}`}>{c.l}</p>
                                    {c.v !== null ? (
                                      <p className={`text-sm font-black tracking-tighter ${c.cor}`}>{fmtVol(c.v)} <span className="text-[8px] opacity-60">cx</span></p>
                                    ) : (
                                      <p className={`text-base font-black tracking-tighter ${(c.pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{(c.pct || 0) >= 0 ? '+' : ''}{(c.pct || 0).toFixed(1)}%</p>
                                    )}
                                  </div>
                                ));
                              })()}
                            </div>
                            {carregando ? (
                              <div className="h-64 flex items-center justify-center"><Loader2 className="w-8 h-8 text-indigo-500 animate-spin" /></div>
                            ) : (
                              <div className="h-64 w-full">
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart data={getDossieChartData(row.original)} margin={{ top: 5, right: 30, bottom: 5, left: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                                    <XAxis dataKey="name" stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} />
                                    <YAxis stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => new Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(v)} />
                                    <Tooltip contentStyle={{ backgroundColor: '#fff', borderColor: '#e2e8f0', borderRadius: '12px', fontSize: '13px' }} />
                                    <Legend wrapperStyle={{ paddingTop: '16px', fontSize: '12px', fontWeight: 700 }} iconType="circle" />
                                    <Line type="monotone" dataKey="realizado" name="Realizado (Cx)" stroke="#64748b" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                                    <Line type="monotone" dataKey="ia" name="IA Oficial" stroke="#a855f7" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls />
                                    <Line type="monotone" dataKey="lag1" name="Ciclo Anterior" stroke="#f59e0b" strokeWidth={2} strokeDasharray="3 3" dot={false} connectNulls />
                                    <Line type="monotone" dataKey="meta" name="Meta (Consenso)" stroke="#6366f1" strokeWidth={3} dot={{ r: 4 }} activeDot={{ r: 6 }} connectNulls />
                                  </LineChart>
                                </ResponsiveContainer>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-[10px] text-slate-400 font-medium mt-4">
          Você edita em caixas ou reais — o outro lado é sempre consequência exata do PMV real de cada cliente. A soma reconcilia automaticamente; o valor digitado é preservado. Congelar sua carteira a torna somente-leitura até um superior reabrir.
        </p>

      </div>
    </div>
  );
}

// =====================================================================
// Maior Resto local (soma exata no cliente antes de enviar)
// =====================================================================
function maiorRestoLocal(total: number, pesos: number[]): number[] {
  const n = pesos.length;
  if (n === 0) return [];
  const soma = pesos.reduce((a, b) => a + b, 0);
  if (soma <= 0) {
    const base = Math.floor(total / n);
    const resto = total - base * n;
    const p = new Array(n).fill(base);
    for (let i = 0; i < resto; i++) p[i]++;
    return p;
  }
  const dist = pesos.map(p => (p / soma) * total);
  const piso = dist.map(x => Math.floor(x));
  const frac = dist.map((x, i) => x - piso[i]);
  let sobra = total - piso.reduce((a, b) => a + b, 0);
  const ordem = frac.map((f, i) => [f, i] as [number, number]).sort((a, b) => b[0] - a[0]);
  for (let k = 0; k < sobra; k++) piso[ordem[k][1]]++;
  return piso;
}