import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState } from '@tanstack/react-table';
import { 
  Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Lock, Search, X, Store, Package, Users, Download, BarChart2, Activity, ShieldAlert, Target, ShieldCheck, PieChart, LayoutList
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number | string | undefined | null) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Number(valor) || 0);
const formatVolume = (val: number | string | undefined | null) => Math.round(Number(val) || 0).toLocaleString('pt-BR');

const SmartCurrencyInput = ({ value, onChange, disabled, blocked }: { value: number, onChange: (val: number) => void, disabled: boolean, blocked: boolean }) => {
  const [localVal, setLocalVal] = useState(value !== undefined ? formatMoeda(value).replace('R$', '').trim() : '0');
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => { if (!isFocused) setLocalVal(value !== undefined ? formatMoeda(value).replace('R$', '').trim() : '0'); }, [value, isFocused]);

  const handleFocus = () => { setIsFocused(true); setLocalVal(localVal.replace(/\./g, '')); };
  const handleBlur = () => { 
      setIsFocused(false); 
      const num = parseInt(localVal.replace(/\D/g, ''), 10) || 0;
      setLocalVal(formatMoeda(num).replace('R$', '').trim());
      onChange(num);
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };

  return (
    <div className="relative w-full flex items-center mt-1.5">
      <span className={`absolute left-3 font-black text-xs ${blocked ? 'text-slate-400' : 'text-indigo-400'}`}>R$</span>
      <input
        type="text" value={localVal} disabled={disabled || blocked} onFocus={handleFocus} onBlur={handleBlur} onKeyDown={handleKeyDown} onChange={(e) => setLocalVal(e.target.value)}
        className={`w-full pl-8 pr-2 py-2.5 rounded-xl border-2 text-center outline-none text-sm font-black transition-all shadow-sm
          ${disabled || blocked ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed' : 'bg-white border-indigo-200 text-indigo-700 focus:border-indigo-500 focus:bg-indigo-50'}`}
      />
    </div>
  );
};

export default function ConsensoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  // Verificação de perfil flexível para evitar erros de case/formatação
  const role = (usuarioSessao?.funcao || '').toLowerCase();
  const isGerenteOrAdmin = role.includes('admin') || role.includes('diretoria') || role.includes('gerente');

  const [viewMode, setViewMode] = useState<'carteira' | 'portfolio'>('carteira');
  const [chartMode, setChartMode] = useState<'CX' | 'RS'>('RS');

  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [isFechado, setIsFechado] = useState(false);
  const [isPortfolioFechado, setIsPortfolioFechado] = useState<boolean | null>(null);
  
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [busca, setBusca] = useState('');
  const [opcoesBusca, setOpcoesBusca] = useState<{vendedores: string[], coordenadores: string[]}>({vendedores: [], coordenadores: []});
  const [nomeResponsavel, setNomeResponsavel] = useState('');

  const colunasData = dadosBrutos?.length > 0 ? (dadosBrutos[0]?.meses || []) : [];

  useEffect(() => {
    axios.get('/api/v1/consensus/micro/filtros').then(res => {
        const opcoes = res.data || {vendedores: [], coordenadores: []};
        setOpcoesBusca(opcoes);
        // PROTEÇÃO CONTRA COLAPSO DE MEMÓRIA (Auto-seleciona para Admin não carregar 80k linhas)
        if (isGerenteOrAdmin && opcoes.coordenadores?.length > 0) {
            setNomeResponsavel(opcoes.coordenadores[0]);
        } else if (!isGerenteOrAdmin && opcoes.vendedores?.length > 0) {
            setNomeResponsavel(opcoes.vendedores[0]);
        }
    }).catch(console.error);
  }, [isGerenteOrAdmin]);

  const fetchData = useCallback(async () => {
    // Evita o disparo da API se for Admin e ainda não tiver auto-selecionado a equipa
    if (isGerenteOrAdmin && !nomeResponsavel) return; 
    
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/micro', { params: { nome_responsavel: nomeResponsavel }});
      setDadosBrutos(res.data?.dados || []);
      setIsFechado(res.data?.is_fechado || false);
      setIsPortfolioFechado(res.data?.is_portfolio_fechado ?? true);
      setCelulasEditadas({}); setExpanded({}); setChartExpanded(null); setViewMode('carteira');
    } catch (e) { 
      console.error("Erro na API:", e); 
      setDadosBrutos([]); 
    } finally { 
      setIsLoading(false); 
    }
  }, [nomeResponsavel, isGerenteOrAdmin]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (!row) return 0;
    if (row.tipo === 'produto' || row.tipo === 'produto_macro') {
      const ed = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (ed !== undefined) return ed.novo_volume;
      const m = (row.meses || []).find((x: any) => x.mes_banco === mesBanco);
      return m?.vol_sim || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicVol(child, mesBanco), 0);
  }, [celulasEditadas]);

  const getDynamicRec = useCallback((row: any, mesBanco: string): number => {
    if (!row) return 0;
    if (row.tipo === 'produto' || row.tipo === 'produto_macro') {
      const vol = getDynamicVol(row, mesBanco);
      const m = (row.meses || []).find((x: any) => x.mes_banco === mesBanco);
      return vol * (m?.pmv || 0);
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicRec(child, mesBanco), 0);
  }, [getDynamicVol]);

  const getStaticMeta = useCallback((row: any, mesBanco: string): number => {
    if (!row) return 0;
    if (row.tipo === 'produto' || row.tipo === 'produto_macro') {
      const m = (row.meses || []).find((x: any) => x.mes_banco === mesBanco);
      return m?.rec_meta || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getStaticMeta(child, mesBanco), 0);
  }, []);

  const handleEditCell = (chaveStr: string, mesBanco: string, novoValorRS: number) => {
    if (isFechado) return;
    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };
      const findNode = (nodes: any[]): any => {
        for (const n of (nodes || [])) {
          if (n.chave_matriz === chaveStr) return n;
          if (n.subRows) { const f = findNode(n.subRows); if (f) return f; }
        }
        return null;
      };
      const targetNode = findNode(dadosBrutos);
      if (!targetNode) return nextEdits;

      const leaves: any[] = [];
      const getLeaves = (n: any) => { if (n.tipo === 'produto') leaves.push(n); else if (n.subRows) (n.subRows || []).forEach(getLeaves); };
      getLeaves(targetNode);

      const totalPesoFat = leaves.reduce((sum, leaf) => sum + (leaf.peso_fat || 1), 0);
      const fractions = leaves.map(leaf => {
          const weight = leaf.peso_fat || 1;
          const target_RS = totalPesoFat > 0 ? (weight / totalPesoFat) * novoValorRS : (1 / leaves.length) * novoValorRS;
          const pmv = (leaf.meses || []).find((m: any) => m.mes_banco === mesBanco)?.pmv || 1;
          const exactBoxes = target_RS / pmv;
          const intVal = Math.floor(exactBoxes);
          return { leaf, intVal, pmv, remRS: (exactBoxes - intVal) * pmv };
      });

      let currentRS = fractions.reduce((sum, item) => sum + (item.intVal * item.pmv), 0);
      fractions.sort((a, b) => b.remRS - a.remRS);
      for (let i = 0; i < fractions.length; i++) {
          if (currentRS + fractions[i].pmv <= novoValorRS + (fractions[i].pmv / 2)) {
              fractions[i].intVal++; currentRS += fractions[i].pmv;
          }
      }

      fractions.forEach(item => {
          if (!nextEdits[item.leaf.chave_matriz]) nextEdits[item.leaf.chave_matriz] = {};
          nextEdits[item.leaf.chave_matriz][mesBanco] = { novo_volume: item.intVal };
      });
      return nextEdits;
    });
  };

  const dadosFiltrados = useMemo(() => {
    if (!busca) return dadosBrutos || [];
    const term = busca.toLowerCase();
    return (dadosBrutos || []).map(raiz => {
      if (raiz.nome.toLowerCase().includes(term)) return raiz;
      const sub1 = (raiz.subRows || []).map((s1: any) => {
        if (s1.nome.toLowerCase().includes(term)) return s1;
        const sub2 = (s1.subRows || []).map((s2: any) => {
           if (s2.nome.toLowerCase().includes(term)) return s2;
           const prods = (s2.subRows || []).filter((p: any) => p.nome.toLowerCase().includes(term) || (p.produto && p.produto.toLowerCase().includes(term)));
           if (prods.length > 0) return { ...s2, subRows: prods };
           return null;
        }).filter(Boolean);
        if (sub2.length > 0) return { ...s1, subRows: sub2 };
        return null;
      }).filter(Boolean);
      if (sub1.length > 0) return { ...raiz, subRows: sub1 };
      return null;
    }).filter(Boolean);
  }, [dadosBrutos, busca]);

  const dadosPortfolioMacro = useMemo(() => {
      if (viewMode !== 'portfolio') return [];
      const folhas: any[] = [];
      const extract = (nodes: any[]) => {
          (nodes || []).forEach(n => { if (n.tipo === 'produto') folhas.push(n); else if (n.subRows) extract(n.subRows); });
      };
      extract(dadosFiltrados);

      const mapa: any = {};
      folhas.forEach(f => {
          const cat = f.categoria || 'Geral';
          const seg = f.segmento || 'Sem Segmento';
          if (!mapa[cat]) mapa[cat] = { tipo: 'categoria', nome: cat, subRows: {}, chave_matriz: `CAT|${cat}` };
          if (!mapa[cat].subRows[seg]) mapa[cat].subRows[seg] = { tipo: 'segmento', nome: seg, subRows: [], chave_matriz: `SEG|${cat}|${seg}` };

          let skuNode = (mapa[cat].subRows[seg].subRows || []).find((s:any) => s.produto === f.produto);
          if (!skuNode) {
              skuNode = {
                  tipo: 'produto_macro', nome: f.nome, produto: f.produto, chave_matriz: `MACRO|${f.produto}`,
                  meses: (colunasData || []).map((m:any) => ({ mes_banco: m.mes_banco, mes_str: m.mes_str, vol_meta: 0, vol_sim: 0 }))
              };
              mapa[cat].subRows[seg].subRows.push(skuNode);
          }

          (colunasData || []).forEach((m:any) => {
              const mesIdx = (skuNode.meses || []).findIndex((x:any) => x.mes_banco === m.mes_banco);
              const originalMes = (f.meses || []).find((x:any) => x.mes_banco === m.mes_banco);
              if (mesIdx !== -1 && originalMes) {
                  skuNode.meses[mesIdx].vol_meta += (originalMes.vol_meta || 0);
                  skuNode.meses[mesIdx].vol_sim += getDynamicVol(f, m.mes_banco);
              }
          });
      });

      return Object.values(mapa).map((cat: any) => ({ ...cat, subRows: Object.values(cat.subRows || {}) }));
  }, [dadosFiltrados, viewMode, colunasData, getDynamicVol]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, {vol: number, fat: number, orc: number}> = {};
    (colunasData || []).forEach((m: any) => totais[m.mes_banco] = {vol: 0, fat: 0, orc: 0});
    (dadosFiltrados || []).forEach(root => {
      (colunasData || []).forEach((m: any) => {
        totais[m.mes_banco].vol += getDynamicVol(root, m.mes_banco);
        totais[m.mes_banco].fat += getDynamicRec(root, m.mes_banco);
        totais[m.mes_banco].orc += getStaticMeta(root, m.mes_banco);
      });
    });
    return totais;
  }, [dadosFiltrados, colunasData, getDynamicVol, getDynamicRec, getStaticMeta]);

  const isSaveBlocked = useMemo(() => {
    if (!dadosBrutos || dadosBrutos.length === 0) return false;
    for (const m of (colunasData || [])) {
      if (totaisGerais[m.mes_banco]?.orc > 0) {
        const percent = (totaisGerais[m.mes_banco].fat / totaisGerais[m.mes_banco].orc) * 100;
        if (percent < 99 || percent > 101) return true; 
      }
    }
    return false;
  }, [dadosBrutos, colunasData, totaisGerais]);

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/micro/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data?.dados || [] }));
      } catch (e) { console.error(e); } finally { setLoadingGrafico(null); }
    }
  };

  const columns = useMemo(() => {
    if ((viewMode === 'carteira' && dadosFiltrados.length === 0) || (viewMode === 'portfolio' && dadosPortfolioMacro.length === 0)) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: viewMode === 'carteira' ? 'Equipa e Carteira' : 'Impacto no Mix de Portfólio',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, produto } = row.original;
          const isFolha = tipo === 'produto' || tipo === 'produto_macro';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[320px]">
              {!isFolha ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-200 text-slate-500 rounded-lg transition-colors">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : <div className="w-8"></div>}
              
              {viewMode === 'carteira' && (
                  <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 border-indigo-200 text-indigo-600 shadow-sm' : 'hover:bg-slate-100 border-transparent text-slate-400 hover:text-slate-600'}`} title="Ver Dossiê S&OE">
                    <BarChart2 className="w-4 h-4" />
                  </button>
              )}

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${tipo === 'coordenador' || tipo === 'categoria' ? 'bg-slate-900 border-slate-800 text-white' : tipo === 'vendedor' || tipo === 'segmento' ? 'bg-blue-50 border-blue-100 text-blue-600' : tipo === 'cliente' ? 'bg-indigo-50 border-indigo-100 text-indigo-600' : 'bg-slate-50 border-slate-100 text-slate-400'}`}>
                {tipo === 'coordenador' || tipo === 'vendedor' ? <Users className="w-4 h-4" /> : tipo === 'cliente' ? <Store className="w-4 h-4" /> : tipo === 'categoria' ? <PieChart className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm ${!isFolha ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[250px]'}`}>
                   {info.getValue()}
                 </span>
                 {isFolha && viewMode === 'carteira' && (
                   <div className="flex flex-col gap-1 mt-1">
                      <div className="flex items-center gap-2">
                          <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">SKU: {produto}</span>
                          <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">PMV PONDERADO: {formatMoeda(row.original.meses?.[0]?.pmv || 0)}</span>
                      </div>
                   </div>
                 )}
              </div>
            </div>
          )
        }
      }
    ];

    (colunasData || []).forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        cell: (info: any) => {
          const row = info.row.original;
          const mBanco = m.mes_banco;
          
          if (viewMode === 'carteira') {
              const metaRS = getStaticMeta(row, mBanco);
              const simRS = getDynamicRec(row, mBanco);
              const simCX = getDynamicVol(row, mBanco);
              
              const percent = metaRS > 0 ? (simRS / metaRS) * 100 : (simRS > 0 ? 999 : 100);
              const isValid = percent >= 99 && percent <= 101;
              const isProduto = row.tipo === 'produto';

              return (
                <div className="flex flex-col items-center justify-center py-2 w-32 relative">
                  <div className="flex justify-between items-center px-1 mb-1">
                     <span className="text-[10px] text-slate-400 font-black uppercase tracking-widest flex items-center gap-1" title="Teto Heradado"><Target className="w-3 h-3" /> Meta: {formatMoeda(metaRS)}</span>
                  </div>
                  <SmartCurrencyInput value={simRS} onChange={(val) => handleEditCell(row.chave_matriz, mBanco, val)} disabled={isFechado} blocked={isProduto} />
                  <div className="flex flex-col items-center mt-1.5 w-full">
                     <span className="text-[11px] font-black text-slate-600 bg-slate-100 px-2 py-0.5 rounded tracking-widest border border-slate-200">{formatVolume(simCX)} CX</span>
                     <div className={`mt-1.5 w-[80%] h-1 rounded-full transition-colors ${isValid ? 'bg-emerald-400' : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)]'}`} />
                  </div>
                </div>
              );
          } else {
              const mesData = (row.meses || []).find((x:any) => x.mes_banco === mBanco) || { vol_meta: 0, vol_sim: 0 };
              const meta = mesData.vol_meta;
              const sim = mesData.vol_sim;
              const dif = sim - meta;
              const perc = meta > 0 ? (dif / meta) * 100 : 0;

              return (
                 <div className="flex flex-col items-end justify-center py-2 w-32">
                    <span className="text-xs font-black text-slate-800">{formatVolume(sim)} <span className="text-[9px] text-slate-400">CX</span></span>
                    <span className="text-[10px] text-slate-400 font-bold uppercase tracking-widest mt-0.5 border-b border-slate-200 border-dashed pb-1 w-full text-right">Meta: {formatVolume(meta)}</span>
                    <div className={`mt-1.5 flex items-center gap-1 text-[10px] font-black tracking-widest px-2 py-0.5 rounded ${dif === 0 ? 'bg-slate-100 text-slate-500' : dif > 0 ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' : 'bg-rose-50 text-rose-600 border border-rose-100'}`}>
                        {dif > 0 ? '+' : ''}{perc.toFixed(1)}%
                    </div>
                 </div>
              );
          }
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, dadosPortfolioMacro, viewMode, chartExpanded, celulasEditadas, isFechado, colunasData]);

  const table = useReactTable({
    data: viewMode === 'carteira' ? dadosFiltrados : dadosPortfolioMacro, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
  });

  const handleSalvar = async () => {
    if (isSaveBlocked) return alert("Erro de Tolerância! Ajuste a distribuição financeira. O simulado deve ficar entre 99% e 101% da Meta herdada.");
    if (Object.keys(celulasEditadas).length === 0) return alert("Nenhuma alteração para salvar.");
    
    setIsProcessing(true);
    const leafEdits = Object.entries(celulasEditadas).filter(([chave]) => chave.split('|').length === 5);
    const ajustes = leafEdits.flatMap(([chave, meses]: any) => Object.entries(meses).map(([mes_projetado, val]: any) => ({ chave, mes_projetado, novo_volume: val.novo_volume })));
    
    try {
      await axios.post(`/api/v1/consensus/micro/congelar`, { origem_ajuste: 'Carteira', ajustes });
      alert("✅ Volume distribuído com sucesso por CNPJ na Fato!");
      fetchData(); 
    } catch (e: any) { alert("Erro ao salvar."); }
    finally { setIsProcessing(false); }
  };

  if (isPortfolioFechado === null) return <div className="h-screen w-full flex flex-col justify-center items-center"><Loader2 className="animate-spin text-indigo-500 w-8 h-8 mb-4" /><span className="text-xs font-black tracking-widest text-slate-400 uppercase">A Ler Base de Dados...</span></div>;

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isFechado && !isLoading && (
              <div className="absolute top-0 left-0 w-full bg-emerald-500 text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10"><ShieldCheck className="w-3 h-3" /> DISTRIBUIÇÃO CONCLUÍDA NO CICLO ATUAL</div>
          )}
          <div className={isFechado ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter"><TrendingUp className="w-8 h-8 text-indigo-600" /> Gestão de Carteiras</h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">Conversão Faturação (R$) para Caixas (CX)</p>
          </div>
          <div className={`flex items-center gap-4 ${isFechado ? "pt-4" : ""}`}>
             {Object.keys(celulasEditadas).length > 0 && !isFechado && (<button onClick={() => setCelulasEditadas({})} className="text-xs font-black text-rose-500 px-4 py-3 rounded-2xl hover:bg-rose-50 uppercase"><X className="w-4 h-4 inline" /> Descartar</button>)}
             <button onClick={handleSalvar} disabled={isProcessing || isFechado || isSaveBlocked} className={`flex items-center gap-2 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg ${isFechado || isSaveBlocked ? 'bg-slate-300 cursor-not-allowed' : 'bg-slate-900 hover:bg-black shadow-slate-900/30'}`}>
                {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : <Check className="w-5 h-5" />} Gravar e Consolidar Equipa
             </button>
          </div>
        </div>

        {/* ALERTA DE TOLERÂNCIA */}
        {isSaveBlocked && dadosBrutos.length > 0 && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-5 rounded-2xl flex items-center gap-4 shadow-sm mb-6 animate-in fade-in"><ShieldAlert className="w-6 h-6 text-rose-500" /><div><h3 className="font-black uppercase tracking-widest text-xs">Aviso de Regra de Ouro (99% - 101%)</h3><p className="text-sm font-medium mt-1">O Faturamento não corresponde à Meta imposta. Ajuste a distribuição financeira.</p></div></div>
        )}

        {/* CONTROLES E TOGGLE DE VISÃO */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
            <div className="flex bg-slate-200 p-1.5 rounded-[20px] shadow-inner w-full md:w-auto">
               <button onClick={() => {setViewMode('carteira'); setExpanded({});}} className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2.5 rounded-[16px] text-xs font-black uppercase tracking-widest transition-all ${viewMode === 'carteira' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-300/50'}`}><Users className="w-4 h-4"/> Por Carteira (Edição)</button>
               <button onClick={() => {setViewMode('portfolio'); setExpanded({});}} className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2.5 rounded-[16px] text-xs font-black uppercase tracking-widest transition-all ${viewMode === 'portfolio' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-300/50'}`}><LayoutList className="w-4 h-4"/> Impacto no Portfólio (Leitura)</button>
            </div>
            
            {viewMode === 'carteira' && (
                <div className="flex items-center gap-4 bg-white p-2 rounded-[20px] shadow-sm border border-slate-100 px-4 flex-shrink-0">
                    <select value={nomeResponsavel} onChange={e => setNomeResponsavel(e.target.value)} className="bg-transparent text-sm font-bold text-slate-700 outline-none cursor-pointer">
                        <option value="">-- Selecione uma Equipa --</option>
                        {isGerenteOrAdmin ? (opcoesBusca?.coordenadores || []).map(opt => <option key={opt} value={opt}>{opt}</option>) : (opcoesBusca?.vendedores || []).map(opt => <option key={opt} value={opt}>{opt}</option>)}
                    </select>
                    <button onClick={fetchData} className="bg-indigo-50 text-indigo-600 px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-indigo-100 transition"><Filter className="w-4 h-4 inline"/></button>
                </div>
            )}
        </div>

        {/* TABELA DE DADOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm">
                {(table.getHeaderGroups() || []).map(hg => (
                  <tr key={hg.id}>
                    {(hg.headers || []).map(header => (
                      <th key={header.id} className="px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              
              <tbody>
                {(table.getRowModel().rows || []).map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-indigo-50/20' : row.depth === 0 ? 'bg-slate-50' : 'hover:bg-slate-50'}`}>
                      {(row.getVisibleCells() || []).map(cell => (<td key={cell.id} className="px-8 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                    </tr>
                    
                    {/* DOSSIÊ EXECUTIVO COM TOGGLE (CX vs R$) */}
                    {chartExpanded === row.original.chave_matriz && viewMode === 'carteira' && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500">
                            <div className="flex justify-between items-center mb-6">
                                <h3 className="text-lg font-black text-white uppercase tracking-tighter flex items-center gap-2"><Activity className="w-5 h-5 text-emerald-400" /> Curva S&OE Analítica ({row.original.nome})</h3>
                                <div className="flex bg-slate-800 p-1 rounded-xl">
                                    <button onClick={() => setChartMode('CX')} className={`px-4 py-1.5 rounded-lg text-xs font-black uppercase tracking-widest transition ${chartMode === 'CX' ? 'bg-emerald-500 text-white' : 'text-slate-400 hover:text-white'}`}>Volume (CX)</button>
                                    <button onClick={() => setChartMode('RS')} className={`px-4 py-1.5 rounded-lg text-xs font-black uppercase tracking-widest transition ${chartMode === 'RS' ? 'bg-emerald-500 text-white' : 'text-slate-400 hover:text-white'}`}>Faturamento (R$)</button>
                                </div>
                            </div>

                            <div className="h-[250px] w-full">
                                {loadingGrafico === row.original.chave_matriz ? <div className="h-full flex justify-center items-center"><Loader2 className="w-8 h-8 animate-spin text-slate-500"/></div> : (
                                  <ResponsiveContainer width="100%" height="100%">
                                    <LineChart data={dadosGraficoCache[row.original.chave_matriz] || []} margin={{ top: 20, right: 30, left: 20, bottom: 10 }}>
                                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                      <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                      <YAxis tickFormatter={(val: any) => chartMode === 'RS' ? formatMoeda(val) : formatVolume(val)} tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                      <Tooltip formatter={(value: any) => [chartMode === 'RS' ? formatMoeda(value) : formatVolume(value), ""]} contentStyle={{borderRadius: '20px', backgroundColor: '#0f172a', border: '1px solid #1e293b', color: '#fff'}} />
                                      <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900', color: '#cbd5e1'}} />
                                      
                                      <Line type="monotone" dataKey={`CicloAnterior_${chartMode}`} name="Lag 1 (Ciclo Passado)" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={true} />
                                      <Line type="monotone" dataKey={`IA_${chartMode}`} name="Modelo IA" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={true} />
                                      <Line type="monotone" dataKey={`Realizado_${chartMode}`} name="Realizado (Histórico)" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls={true} />
                                      <Line type="monotone" dataKey={`MetaBU_${chartMode}`} name="Meta (Teto Diretoria)" stroke="#a855f7" strokeWidth={3} strokeDasharray="3 3" dot={false} connectNulls={true} />
                                      <Line type="monotone" dataKey={`Consenso_${chartMode}`} name="Sua Proposta" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#0f172a'}} connectNulls={true} />
                                    </LineChart>
                                  </ResponsiveContainer>
                                )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
              
              <tfoot className="bg-slate-900 text-white">
                <tr>
                  {(table.getHeaderGroups()[0]?.headers || []).map(header => {
                    if (header.id === 'nome') return (<td key={header.id} className="px-8 py-5 text-right border-r border-slate-800"><div className="flex flex-col"><span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Totalização</span><span className="font-bold text-sm text-white">EQUIPA COMERCIAL</span></div></td>);
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5 border-l border-slate-800">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white text-base">{formatVolume(totaisGerais[m]?.vol || 0)} <span className="text-[10px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-xs tracking-tight mt-1 bg-emerald-400/10 px-2 py-0.5 rounded">{formatMoeda(totaisGerais[m]?.fat || 0)}</span>
                            <span className="text-[9px] text-slate-500 font-bold tracking-widest uppercase mt-1 pt-1 border-t border-slate-800 w-full text-center">Meta: {formatMoeda(totaisGerais[m]?.orc || 0)}</span>
                          </div>
                        </td>
                      );
                    }
                    return <td key={header.id}></td>;
                  })}
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}