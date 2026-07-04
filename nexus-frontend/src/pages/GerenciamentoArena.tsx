import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel,
  getSortedRowModel, SortingState, ColumnDef
} from '@tanstack/react-table';
import {
  Loader2, ChevronDown, ChevronRight, Layers, Lock, Package, Search,
  BarChart3, TrendingUp, TrendingDown, Bot, Target, Wand2, Check, ShieldCheck,
  Save, Users, AlertTriangle
} from 'lucide-react';
import axios from 'axios';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer
} from 'recharts';

// =====================================================================
// HELPERS (padrão GlobalDashboard)
// =====================================================================
const formatMoeda = (valor: any) => {
  const num = Number(valor);
  if (isNaN(num)) return 'R$ 0';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(num));
};
const formatVolume = (val: any) => {
  const num = Number(val);
  if (isNaN(num)) return '0';
  return Math.round(num).toLocaleString('pt-BR');
};
const calcVar = (atual: number, base: number) => base > 0 ? ((atual - base) / base) * 100 : 0;

// Badge de variação vs Orçamento (âncora) — colorido.
const VarBadge = ({ atual = 0, base = 0 }: { atual?: number, base?: number }) => {
  const v = calcVar(atual, base);
  if (base === 0) return null;
  const isPos = v >= 0;
  return (
    <span className={`text-[9px] font-black flex items-center gap-0.5 px-1 py-0.5 rounded ${isPos ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
      {isPos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />} {Math.abs(v).toFixed(1)}%
    </span>
  );
};

// Badge de divergência vs TopDown (proeminente, secundário) — neutro/rotulado.
const DivBadge = ({ atual = 0, base = 0 }: { atual?: number, base?: number }) => {
  const v = calcVar(atual, base);
  if (base === 0) return null;
  const isPos = v >= 0;
  return (
    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${isPos ? 'border-indigo-200 text-indigo-600 bg-indigo-50' : 'border-amber-200 text-amber-600 bg-amber-50'}`} title="Divergência vs Marketing (Top-Down)">
      vs Mkt: {isPos ? '+' : ''}{v.toFixed(1)}%
    </span>
  );
};

// Input de volume em caixas (máscara pt-BR).
const SmartInput = ({ value, onChange, disabled }: { value: number, onChange: (val: number) => void, disabled: boolean }) => {
  const [localVal, setLocalVal] = useState((value !== undefined && value !== null) ? formatVolume(value) : '0');
  const [isFocused, setIsFocused] = useState(false);
  useEffect(() => { if (!isFocused) setLocalVal((value !== undefined && value !== null) ? formatVolume(value) : '0'); }, [value, isFocused]);
  const handleFocus = () => { setIsFocused(true); setLocalVal(localVal.replace(/\./g, '')); };
  const handleBlur = () => {
    setIsFocused(false);
    const num = parseInt(localVal.replace(/\D/g, ''), 10) || 0;
    setLocalVal(formatVolume(num));
    onChange(num);
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };
  return (
    <input
      type="text" value={localVal} disabled={disabled} onFocus={handleFocus} onBlur={handleBlur} onKeyDown={handleKeyDown} onChange={(e) => setLocalVal(e.target.value)}
      className={`w-full bg-transparent border-none text-center focus:outline-none focus:ring-2 focus:ring-indigo-500 rounded-lg py-1
        ${disabled ? 'text-slate-900 font-black cursor-not-allowed' : 'text-indigo-700 font-black bg-indigo-50 shadow-inner'}`}
    />
  );
};

// =====================================================================
// CONTRATO DO ROUTER
// =====================================================================
interface MesData {
  mes_banco: string; mes_str: string;
  vol_bu: number; fat_bu: number;
  vol_td: number; fat_td: number;
  vol_ia: number; fat_ia: number;
  rec_orcada: number; pmv: number;
  variacao_orcamento: number; divergencia_topdown: number;
}
interface SkuData {
  sku: string; descricao: string; categoria?: string;
  meses: MesData[];
  grafico: {
    labels: string[];
    realizado: (number | null)[];
    ia: (number | null)[];
    lag1: (number | null)[];
    topdown: (number | null)[];
    bottomup: (number | null)[];
  };
}
interface TotalMes {
  mes_banco: string; mes_str: string;
  fat_bu: number; fat_td: number; fat_ia: number; rec_orcada: number;
  vol_bu: number; vol_td: number; vol_ia: number; variacao_orcamento: number;
}

// =====================================================================
// COMPONENTE
// =====================================================================
export default function GerenciamentoArena(_props: { usuarioSessao?: any } = {}) {
  const [dados, setDados] = useState<SkuData[]>([]);
  const [totais, setTotais] = useState<{ por_mes: TotalMes[]; consolidado: TotalMes | null }>({ por_mes: [], consolidado: null });
  const [contador, setContador] = useState({ total_skus: 0, skus_ajustados: 0, skus_intocados: 0 });
  const [ciclo, setCiclo] = useState('');
  const [statusEtapa, setStatusEtapa] = useState('ABERTO');
  const [topdownLiberado, setTopdownLiberado] = useState(true);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busca, setBusca] = useState('');
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [celulasEditadas, setCelulasEditadas] = useState<{ [sku: string]: { [mes: string]: number } }>({});

  const isLocked = statusEtapa === 'CONGELADO';
  const editDisabled = isLocked || !topdownLiberado;

  const carregarDados = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento/dados');
      setDados(res.data.dados || []);
      setTotais(res.data.totais || { por_mes: [], consolidado: null });
      setContador(res.data.contador || { total_skus: 0, skus_ajustados: 0, skus_intocados: 0 });
      setCiclo(res.data.ciclo_ativo || '');
      setStatusEtapa(res.data.status_etapa || 'ABERTO');
      setTopdownLiberado(res.data.topdown_liberado !== false);
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) {
      console.error('Erro ao carregar Gerenciamento:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { carregarDados(); }, [carregarDados]);

  const mesesDisponiveis = useMemo(() => {
    if (!dados.length) return [] as { mes_banco: string; mes_str: string }[];
    return dados[0].meses.map(m => ({ mes_banco: m.mes_banco, mes_str: m.mes_str }));
  }, [dados]);

  const getVolVivo = useCallback((sku: string, mesBanco: string): number => {
    const editado = celulasEditadas[sku]?.[mesBanco];
    if (editado !== undefined) return Number(editado);
    const sk = dados.find(d => d.sku === sku);
    return sk?.meses.find(m => m.mes_banco === mesBanco)?.vol_bu || 0;
  }, [celulasEditadas, dados]);

  const getPmvExib = useCallback((sku: string, mesBanco: string): number => {
    const sk = dados.find(d => d.sku === sku);
    const m = sk?.meses.find(x => x.mes_banco === mesBanco);
    if (!m) return 0;
    if (m.vol_bu > 0) return m.fat_bu / m.vol_bu;
    if (m.vol_td > 0) return m.fat_td / m.vol_td;
    return m.pmv || 0;
  }, [dados]);

  const getFatEstimado = useCallback((sku: string, mesBanco: string): number => {
    return getVolVivo(sku, mesBanco) * getPmvExib(sku, mesBanco);
  }, [getVolVivo, getPmvExib]);

  const handleEditCell = (sku: string, mesBanco: string, novoVol: number) => {
    setCelulasEditadas(prev => ({ ...prev, [sku]: { ...(prev[sku] || {}), [mesBanco]: novoVol } }));
  };
  const temPendencia = (sku: string, mesBanco: string) => celulasEditadas[sku]?.[mesBanco] !== undefined;
  const hasPendingChanges = Object.keys(celulasEditadas).length > 0;

  // Árvore categoria -> SKU
  const arvore = useMemo(() => {
    const term = busca.toLowerCase();
    const filtrados = dados.filter(d => (d.sku + ' ' + d.descricao + ' ' + (d.categoria || '')).toLowerCase().includes(term));
    const cats = new Map<string, any>();
    filtrados.forEach(sku => {
      const cat = sku.categoria || 'SEM CATEGORIA';
      if (!cats.has(cat)) cats.set(cat, { id: cat, chave: cat, nome: cat, tipo: 'categoria', subRows: [] });
      cats.get(cat).subRows.push({
        id: `${cat}|${sku.sku}`, chave: sku.sku, nome: sku.descricao,
        produto: sku.sku, tipo: 'produto', grafico: sku.grafico, subRows: undefined
      });
    });
    return Array.from(cats.values());
  }, [dados, busca]);

  // Agregadores de nó (categoria soma filhos; produto usa vivo).
  const somaFilhos = (row: any, fn: (produto: string) => number) =>
    row.subRows.reduce((acc: number, f: any) => acc + fn(f.produto), 0);

  const getNodeVol = useCallback((row: any, mes: string): number =>
    row.tipo === 'produto' ? getVolVivo(row.produto, mes) : somaFilhos(row, p => getVolVivo(p, mes)), [getVolVivo]);
  const getNodeFat = useCallback((row: any, mes: string): number =>
    row.tipo === 'produto' ? getFatEstimado(row.produto, mes) : somaFilhos(row, p => getFatEstimado(p, mes)), [getFatEstimado]);

  const campoSku = useCallback((produto: string, mes: string, campo: keyof MesData): number => {
    const sk = dados.find(d => d.sku === produto);
    return Number(sk?.meses.find(m => m.mes_banco === mes)?.[campo] || 0);
  }, [dados]);

  const getNodeCampo = useCallback((row: any, mes: string, campo: keyof MesData): number =>
    row.tipo === 'produto' ? campoSku(row.produto, mes, campo) : somaFilhos(row, p => campoSku(p, mes, campo)), [campoSku]);

  // Cards de KPI (consolidado 3 meses).
  const cards = useMemo(() => {
    let volBu = 0, fatBu = 0, volTd = 0, fatTd = 0, volIa = 0, fatIa = 0, orc = 0;
    dados.forEach(sku => sku.meses.forEach(m => {
      volBu += getVolVivo(sku.sku, m.mes_banco);
      fatBu += getFatEstimado(sku.sku, m.mes_banco);
      volTd += m.vol_td; fatTd += m.fat_td;
      volIa += m.vol_ia; fatIa += m.fat_ia;
      orc += m.rec_orcada;
    }));
    return { volBu, fatBu, volTd, fatTd, volIa, fatIa, orc };
  }, [dados, getVolVivo, getFatEstimado]);

  // Macro: 4 barras por mês (IA, Marketing, Comercial, Orçamento) em faturamento.
  const chartMacro = useMemo(() => {
    return mesesDisponiveis.map(mes => {
      let fatBu = 0, fatTd = 0, fatIa = 0, orc = 0;
      dados.forEach(sku => {
        fatBu += getFatEstimado(sku.sku, mes.mes_banco);
        const m = sku.meses.find(x => x.mes_banco === mes.mes_banco);
        fatTd += m?.fat_td || 0; fatIa += m?.fat_ia || 0; orc += m?.rec_orcada || 0;
      });
      return { name: mes.mes_str, IA: Math.round(fatIa), Marketing: Math.round(fatTd), Comercial: Math.round(fatBu), Orcamento: Math.round(orc) };
    });
  }, [dados, mesesDisponiveis, getFatEstimado]);

  // Dossiê: por SKU direto; por categoria soma as séries dos filhos.
  const getDossieData = useCallback((row: any) => {
    const montar = (g: any) => {
      if (!g?.labels) return [];
      return g.labels.map((label: string, i: number) => ({
        name: label,
        realizado: g.realizado?.[i] ?? null,
        ia: g.ia?.[i] ?? null,
        lag1: g.lag1?.[i] ?? null,
        topdown: g.topdown?.[i] ?? null,
        bottomup: g.bottomup?.[i] ?? null,
      }));
    };
    if (row.tipo === 'produto') return montar(row.grafico);
    const filhos = row.subRows || [];
    if (!filhos.length) return [];
    const labels = filhos[0].grafico?.labels || [];
    return labels.map((label: string, i: number) => {
      const campos = ['realizado', 'ia', 'lag1', 'topdown', 'bottomup'];
      const obj: any = { name: label };
      campos.forEach(c => {
        const todosNull = filhos.every((f: any) => { const v = f.grafico?.[c]?.[i]; return v === null || v === undefined; });
        obj[c] = todosNull ? null : filhos.reduce((acc: number, f: any) => { const v = f.grafico?.[c]?.[i]; return (v === null || v === undefined) ? acc : acc + v; }, 0);
      });
      return obj;
    });
  }, []);

  const toggleChart = (chave: string) => setChartExpanded(prev => prev === chave ? null : chave);

  const handleSalvar = async () => {
    const alteracoes = Object.entries(celulasEditadas).flatMap(([sku, meses]) =>
      Object.entries(meses).map(([mes_banco, novo_vol]) => ({ sku, mes_banco, novo_vol })));
    if (!alteracoes.length) return alert('Nenhuma alteração pendente para salvar.');
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/gerenciamento/salvar', { alteracoes });
      alert('Rascunho Bottom-Up salvo e rateado (Maior Resto) conforme o peso do Top-Down.');
      await carregarDados();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao salvar rascunho.');
    } finally { setSaving(false); }
  };

  const handleCongelar = async () => {
    if (!window.confirm('Congelar o Bottom-Up? Os volumes serão propagados como PARTIDA para Metas, Supply e Final (exceto IA e Top-Down). Cada etapa poderá divergir depois. Confirmar?')) return;
    if (hasPendingChanges && !window.confirm('Há alterações não salvas que serão perdidas. Continuar?')) return;
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/gerenciamento/congelar');
      alert('Etapa Bottom-Up congelada com sucesso.');
      await carregarDados();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao congelar etapa.');
    } finally { setSaving(false); }
  };

  // Colunas.
  const columns = useMemo<ColumnDef<any>[]>(() => {
    if (!arvore.length) return [];
    const cols: ColumnDef<any>[] = [{
      id: 'nome',
      header: 'Categoria > SKU',
      accessorFn: (row: any) => row.nome,
      cell: (info: any) => {
        const row = info.row;
        const { tipo, nome, produto, chave } = row.original;
        return (
          <div style={{ paddingLeft: `${row.depth * 28}px` }} className="flex items-center gap-3 py-2">
            {row.getCanExpand() ? (
              <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-500 transition-colors">
                {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
            ) : <div className="w-7" />}
            <button onClick={() => toggleChart(chave)} className={`p-1.5 rounded-lg border transition-all shadow-sm ${chartExpanded === chave ? 'bg-indigo-100 text-indigo-600 border-indigo-200' : 'bg-white hover:bg-slate-50 text-slate-400 border-slate-200'}`} title="Abrir dossiê (histórico 24m)">
              <BarChart3 className="w-3.5 h-3.5" />
            </button>
            <div className={`w-8 h-8 flex items-center justify-center rounded-xl shadow-sm border ${tipo === 'categoria' ? 'bg-slate-900 text-white border-slate-800' : 'bg-white text-slate-500 border-slate-200'}`}>
              {tipo === 'categoria' ? <Layers className="w-3.5 h-3.5" /> : <Package className="w-3.5 h-3.5" />}
            </div>
            <div className="flex flex-col overflow-hidden">
              <span className={`text-[13px] truncate ${tipo === 'categoria' ? 'font-black uppercase tracking-tighter' : 'font-bold text-slate-600'}`} title={nome}>{nome}</span>
              {tipo === 'produto' && <span className="text-[10px] text-slate-400 font-black tracking-widest">{produto}</span>}
            </div>
          </div>
        );
      }
    }];

    mesesDisponiveis.forEach(mes => {
      cols.push({
        id: `mes_${mes.mes_banco}`,
        header: mes.mes_str,
        accessorFn: (row: any) => getNodeVol(row, mes.mes_banco),
        cell: (info: any) => {
          const row = info.row.original;
          const vol = getNodeVol(row, mes.mes_banco);
          const fat = getNodeFat(row, mes.mes_banco);
          const volIa = getNodeCampo(row, mes.mes_banco, 'vol_ia');
          const volTd = getNodeCampo(row, mes.mes_banco, 'vol_td');
          const fatTd = getNodeCampo(row, mes.mes_banco, 'fat_td');
          const orc = getNodeCampo(row, mes.mes_banco, 'rec_orcada');
          const pend = row.tipo === 'produto' && temPendencia(row.produto, mes.mes_banco);

          return (
            <div className="flex flex-col items-center justify-center min-w-[160px]">
              <div className="w-28 mb-1">
                {row.tipo === 'produto' ? (
                  <SmartInput value={vol} disabled={editDisabled} onChange={(val) => handleEditCell(row.produto, mes.mes_banco, val)} />
                ) : (
                  <div className="font-black text-sm text-center text-slate-900">{formatVolume(vol)}</div>
                )}
              </div>

              {/* Duas referências: IA e TopDown */}
              <div className="flex items-center gap-1.5 mb-1.5">
                <span className="text-[9px] font-bold bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded shadow-sm" title="Sinal IA">IA: {formatVolume(volIa)}</span>
                <span className="text-[9px] font-bold bg-indigo-50 text-indigo-500 px-1.5 py-0.5 rounded shadow-sm" title="Proposta Marketing (Top-Down)">TD: {formatVolume(volTd)}</span>
              </div>

              {/* Faturamento + âncora (vs orçamento) + divergência (vs TopDown) */}
              <div className="flex flex-col items-center mt-1 gap-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-bold text-emerald-600" title={pend ? 'Estimado (salve para o exato)' : 'Faturamento previsto (micro)'}>
                    {pend ? '~' : ''}{formatMoeda(fat)}
                  </span>
                  {orc > 0 && <VarBadge atual={fat} base={orc} />}
                </div>
                <div className="flex items-center gap-1.5">
                  {fatTd > 0 && <DivBadge atual={fat} base={fatTd} />}
                  {orc > 0 && <span className="text-[9px] font-bold text-slate-400" title="Orçamento">Orç: {formatMoeda(orc)}</span>}
                </div>
              </div>
            </div>
          );
        }
      });
    });
    return cols;
  }, [arvore, mesesDisponiveis, chartExpanded, editDisabled, getNodeVol, getNodeFat, getNodeCampo, celulasEditadas]);

  const table = useReactTable({
    data: arvore, columns,
    state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: (row: any) => row.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) {
    return <div className="min-h-screen bg-[#f8fafc] flex items-center justify-center"><Loader2 className="w-12 h-12 text-indigo-600 animate-spin" /></div>;
  }

  const varConsolidada = calcVar(cards.fatBu, cards.orc);

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">

        {/* HEADER */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isLocked && (
            <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase z-10 bg-emerald-500">
              <ShieldCheck className="w-4 h-4" /> ETAPA BOTTOM-UP CONGELADA
            </div>
          )}
          <div className={isLocked ? 'pt-6' : ''}>
            <div className="flex items-center gap-4">
              <div className="p-3 bg-indigo-50 rounded-2xl"><Users className="w-8 h-8 text-indigo-600" /></div>
              <div>
                <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">Arena Bottom-Up</h1>
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mt-1">Ciclo {ciclo} · Gerência Comercial sobre o Top-Down · Meta = Orçamento</p>
              </div>
            </div>
          </div>
          <div className={`flex items-center gap-4 ${isLocked ? 'pt-6' : ''}`}>
            {!isLocked && (
              <>
                <button onClick={handleSalvar} disabled={saving || !hasPendingChanges || !topdownLiberado}
                  className={`flex items-center gap-2 font-bold py-3 px-5 rounded-2xl transition-all shadow-sm border ${hasPendingChanges && topdownLiberado ? 'bg-slate-50 hover:bg-slate-100 text-slate-700 border-slate-200' : 'bg-slate-50 text-slate-300 border-slate-100 cursor-not-allowed'}`}>
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Salvar Rascunho
                </button>
                <button onClick={handleCongelar} disabled={saving || !topdownLiberado}
                  className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white font-bold py-3 px-8 rounded-2xl shadow-lg shadow-indigo-600/30 transition-all">
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />} Congelar Arena
                </button>
              </>
            )}
          </div>
        </div>

        {/* AVISO se TopDown não liberado */}
        {!topdownLiberado && (
          <div className="mb-8 bg-amber-50 border border-amber-200 rounded-3xl p-5 flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0" />
            <p className="text-sm font-bold text-amber-700">O Top-Down (Marketing) ainda não foi congelado. A edição do Bottom-Up fica bloqueada até a etapa anterior fechar.</p>
          </div>
        )}

        {/* CARDS: IA / Marketing / Comercial / Orçamento / Âncora */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 mb-8">
          <div className="p-6 rounded-[32px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[170px] hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="flex justify-between items-start">
              <h3 className="text-[11px] font-black uppercase tracking-widest text-slate-400">Baseline IA</h3>
              <div className="p-2 rounded-xl bg-slate-50"><Bot className="w-5 h-5 opacity-70" /></div>
            </div>
            <div className="mt-auto pt-4">
              <p className="text-2xl font-black leading-none tracking-tighter">{formatVolume(cards.volIa)} <span className="text-[10px] opacity-60">CX</span></p>
              <p className="text-xs font-bold text-slate-400 mt-1">{formatMoeda(cards.fatIa)}</p>
            </div>
          </div>

          <div className="p-6 rounded-[32px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[170px] hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="flex justify-between items-start">
              <h3 className="text-[11px] font-black uppercase tracking-widest text-slate-400">Marketing (TD)</h3>
              <div className="p-2 rounded-xl bg-slate-50"><Wand2 className="w-5 h-5 opacity-70" /></div>
            </div>
            <div className="mt-auto pt-4">
              <p className="text-2xl font-black leading-none tracking-tighter">{formatVolume(cards.volTd)} <span className="text-[10px] opacity-60">CX</span></p>
              <p className="text-xs font-bold text-slate-400 mt-1">{formatMoeda(cards.fatTd)}</p>
            </div>
          </div>

          <div className="p-6 rounded-[32px] shadow-sm bg-white border border-indigo-100 flex flex-col justify-between min-h-[170px] hover:-translate-y-1 hover:shadow-md transition-all ring-1 ring-indigo-100">
            <div className="flex justify-between items-start">
              <h3 className="text-[11px] font-black uppercase tracking-widest text-indigo-400">Proposta Comercial</h3>
              <div className="p-2 rounded-xl bg-indigo-50"><Users className="w-5 h-5 text-indigo-500" /></div>
            </div>
            <div className="mt-auto pt-4">
              <p className="text-2xl font-black leading-none tracking-tighter text-indigo-700">{formatVolume(cards.volBu)} <span className="text-[10px] opacity-60">CX</span></p>
              <p className="text-xs font-bold text-slate-400 mt-1">{formatMoeda(cards.fatBu)}</p>
            </div>
          </div>

          <div className="p-6 rounded-[32px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[170px] hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="flex justify-between items-start">
              <h3 className="text-[11px] font-black uppercase tracking-widest text-slate-400">Orçamento (Meta)</h3>
              <div className="p-2 rounded-xl bg-slate-50"><Target className="w-5 h-5 opacity-70" /></div>
            </div>
            <div className="mt-auto pt-4">
              <p className="text-xl font-black leading-none tracking-tighter">{formatMoeda(cards.orc)}</p>
              <p className="text-[10px] font-bold text-slate-400 mt-1 uppercase tracking-widest">Compromisso financeiro</p>
            </div>
          </div>

          <div className="p-6 rounded-[32px] shadow-sm bg-slate-900 text-white flex flex-col justify-between min-h-[170px] relative overflow-hidden hover:-translate-y-1 hover:shadow-md transition-all">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-indigo-500/20 to-transparent rounded-full -mr-10 -mt-10 blur-xl" />
            <div className="flex justify-between items-start z-10">
              <h3 className="text-[11px] font-black uppercase tracking-widest text-indigo-300">Comercial vs Orçamento</h3>
              <div className="p-2 rounded-xl bg-slate-800">{varConsolidada >= 0 ? <TrendingUp className="w-5 h-5" /> : <TrendingDown className="w-5 h-5" />}</div>
            </div>
            <div className="mt-auto pt-4 z-10">
              <p className={`text-3xl font-black leading-none tracking-tighter ${varConsolidada >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {varConsolidada >= 0 ? '+' : ''}{varConsolidada.toFixed(1)}%
              </p>
              <p className="text-[10px] font-bold text-slate-400 mt-2 uppercase tracking-widest">{contador.skus_ajustados}/{contador.total_skus} SKUs ajustados</p>
            </div>
          </div>
        </div>

        {/* MACRO: 4 barras por mês */}
        <div className="bg-white rounded-[32px] shadow-sm border border-slate-100 p-8 mb-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 bg-indigo-50 text-indigo-600 rounded-xl"><BarChart3 className="w-5 h-5" /></div>
            <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest">Faturamento por Mês — IA vs Marketing vs Comercial vs Orçamento</h4>
          </div>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartMacro} margin={{ top: 10, right: 20, bottom: 5, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="name" stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 12, fontWeight: 700 }} tickLine={false} axisLine={false} />
                <YAxis stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={false} tickFormatter={(v) => new Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(v)} />
                <Tooltip contentStyle={{ backgroundColor: '#fff', borderColor: '#e2e8f0', borderRadius: '12px', fontSize: '13px' }} formatter={(v: any) => formatMoeda(v)} />
                <Legend wrapperStyle={{ paddingTop: '16px', fontSize: '12px', fontWeight: 700 }} iconType="circle" />
                <Bar dataKey="IA" name="IA" fill="#cbd5e1" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Marketing" name="Marketing" fill="#a5b4fc" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Comercial" name="Comercial" fill="#6366f1" radius={[6, 6, 0, 0]} />
                <Bar dataKey="Orcamento" name="Orçamento" fill="#0f172a" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* BUSCA + CONTADOR */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 mb-4">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar SKU, descrição ou categoria..."
              className="w-full bg-white border border-slate-200 rounded-2xl pl-11 pr-4 py-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 shadow-sm" />
          </div>
          <div className="flex items-center gap-2 text-xs font-bold">
            <span className="bg-white border border-slate-200 text-slate-600 px-3 py-2 rounded-xl shadow-sm">{contador.total_skus} SKUs</span>
            <span className="bg-indigo-50 border border-indigo-100 text-indigo-600 px-3 py-2 rounded-xl shadow-sm flex items-center gap-1.5"><Check className="w-3.5 h-3.5" /> {contador.skus_ajustados} ajustados</span>
            <span className="bg-slate-50 border border-slate-200 text-slate-400 px-3 py-2 rounded-xl shadow-sm">{contador.skus_intocados} intocados</span>
          </div>
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
                  return (
                    <React.Fragment key={row.id}>
                      <tr className={`border-b border-slate-50 transition-colors ${row.original.tipo === 'categoria' ? 'bg-slate-50/50 hover:bg-slate-100/50' : 'hover:bg-indigo-50/30'}`}>
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
                            <div className="h-64 w-full">
                              <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={getDossieData(row.original)} margin={{ top: 5, right: 30, bottom: 5, left: 0 }}>
                                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                                  <XAxis dataKey="name" stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} />
                                  <YAxis stroke="#94a3b8" tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => new Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(v)} />
                                  <Tooltip contentStyle={{ backgroundColor: '#fff', borderColor: '#e2e8f0', borderRadius: '12px', fontSize: '13px' }} />
                                  <Legend wrapperStyle={{ paddingTop: '16px', fontSize: '12px', fontWeight: 700 }} iconType="circle" />
                                  <Line type="monotone" dataKey="realizado" name="Realizado (Cx)" stroke="#64748b" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                                  <Line type="monotone" dataKey="ia" name="IA Oficial" stroke="#a855f7" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls />
                                  <Line type="monotone" dataKey="lag1" name="Ciclo Anterior" stroke="#f59e0b" strokeWidth={2} strokeDasharray="3 3" dot={false} connectNulls />
                                  <Line type="monotone" dataKey="topdown" name="Top-Down" stroke="#a5b4fc" strokeWidth={2} dot={false} connectNulls />
                                  <Line type="monotone" dataKey="bottomup" name="Bottom-Up" stroke="#6366f1" strokeWidth={3} dot={{ r: 4 }} activeDot={{ r: 6 }} connectNulls />
                                </LineChart>
                              </ResponsiveContainer>
                            </div>
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

        {/* TOTALIZADOR */}
        {totais.consolidado && (
          <div className="mt-8 bg-white rounded-[32px] shadow-sm border border-slate-100 p-8">
            <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6">Totalizador · Comercial (Bottom-Up) vs Orçamento</h4>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
              {totais.por_mes.map(t => (
                <div key={t.mes_banco} className="p-5 rounded-3xl bg-slate-50 border border-slate-100">
                  <p className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-3">{t.mes_str}</p>
                  <p className="text-lg font-black text-slate-900 tracking-tighter">{formatMoeda(t.fat_bu)}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[10px] font-bold text-slate-400">Orç: {formatMoeda(t.rec_orcada)}</span>
                    <VarBadge atual={t.fat_bu} base={t.rec_orcada} />
                  </div>
                  <div className="mt-1">
                    {t.fat_td > 0 && <DivBadge atual={t.fat_bu} base={t.fat_td} />}
                  </div>
                </div>
              ))}
              <div className="p-5 rounded-3xl bg-slate-900 text-white relative overflow-hidden">
                <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-indigo-500/20 to-transparent rounded-full -mr-8 -mt-8 blur-xl" />
                <p className="text-[11px] font-black text-indigo-300 uppercase tracking-widest mb-3 z-10 relative">Consolidado</p>
                <p className="text-lg font-black tracking-tighter z-10 relative">{formatMoeda(totais.consolidado.fat_bu)}</p>
                <div className="flex items-center gap-2 mt-1 z-10 relative">
                  <span className="text-[10px] font-bold text-slate-400">Orç: {formatMoeda(totais.consolidado.rec_orcada)}</span>
                  <VarBadge atual={totais.consolidado.fat_bu} base={totais.consolidado.rec_orcada} />
                </div>
              </div>
            </div>
            <p className="text-[10px] text-slate-400 font-medium mt-4">
              Os totais refletem o último rascunho salvo (faturamento micro exato = Σ volume × PMV por cliente). Edições não salvas aparecem como estimativa (~) nas células. "Ajustado" = Bottom-Up divergiu do Top-Down.
            </p>
          </div>
        )}

      </div>
    </div>
  );
}