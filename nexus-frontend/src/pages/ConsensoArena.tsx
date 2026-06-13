import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Lock, Search, X, Store, Package, Users, Download, BarChart2, Activity, ShieldAlert, Wand2, Layers, Target, ShieldCheck
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(valor);
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

// COMPONENTE DE INPUT DE DINHEIRO (Converte a digitação para número puro)
const SmartCurrencyInput = ({ value, onChange, disabled, blocked }: { value: number, onChange: (val: number) => void, disabled: boolean, blocked: boolean }) => {
  const [localVal, setLocalVal] = useState(value !== undefined ? formatMoeda(value).replace('R$', '').trim() : '0');
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (!isFocused) setLocalVal(value !== undefined ? formatMoeda(value).replace('R$', '').trim() : '0');
  }, [value, isFocused]);

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

// =====================================================================
// GERADOR DE INSIGHTS COM IA SOB DEMANDA (TEMA ESCURO ORIGINAL RESTAURADO)
// =====================================================================
const AiInsightBox = ({ alvo, tipo, pmv }: { alvo: string, tipo: string, pmv: number }) => {
  const [insight, setInsight] = useState('');
  const [loading, setLoading] = useState(false);

  const getInsight = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/ai-sql/perguntar', {
        pergunta: `Faça o dossiê executivo 360° para o ${tipo} "${alvo}". Extraia a cascata de volumes, o histórico de vendas e o pmv_aplicado. Apresente o diagnóstico financeiro (R$) e estratégico utilizando a metodologia dos 4 pilares.`,
        contexto: { tela_ativa: 'Bottom-Up Comercial (Gestão de Carteira)', ciclo_status: 'Em Ajuste' }
      });
      setInsight(res.data.resposta);
    } catch (e) {
      setInsight('Erro ao gerar insight.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-slate-900/50 rounded-2xl p-6 border border-slate-700 flex flex-col gap-4 h-full">
      <div className="flex items-center gap-3">
         <div className="p-2.5 bg-indigo-500/20 text-indigo-400 rounded-xl">
           <Wand2 className="w-5 h-5" />
         </div>
         <h4 className="text-sm font-black text-white uppercase tracking-widest">Nexus AI Insight 360°</h4>
      </div>
      
      {insight ? (
        <div className="text-sm font-medium text-slate-300 leading-relaxed whitespace-pre-wrap overflow-y-auto custom-scrollbar pr-2" dangerouslySetInnerHTML={{ __html: insight.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-white">$1</strong>') }} />
      ) : (
        <div className="flex flex-col items-start gap-3 mt-2">
           <p className="text-xs text-slate-500 font-medium">Acione o consultor executivo para cruzar dados de S&OP, Faturamento Histórico e metas do ciclo.</p>
           <button onClick={getInsight} disabled={loading} className="mt-2 text-xs font-bold bg-indigo-600 text-white px-4 py-3 rounded-xl hover:bg-indigo-700 transition flex items-center gap-2 disabled:opacity-50 w-full justify-center shadow-lg shadow-indigo-900/20">
             {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Gerar Parecer Estratégico 360°'}
           </button>
        </div>
      )}
    </div>
  );
};

export default function ConsensoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const isGerenteOrAdmin = ['Administrador', 'Diretoria', 'Gerente'].includes(usuarioSessao?.funcao);

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

  const colunasData = dadosBrutos.length > 0 ? dadosBrutos[0].meses : [];

  useEffect(() => {
    axios.get('/api/v1/consensus/micro/filtros')
         .then(res => setOpcoesBusca(res.data)).catch(console.error);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/micro', { params: { nome_responsavel: nomeResponsavel }});
      setDadosBrutos(res.data.dados || []);
      setIsFechado(res.data.is_fechado);
      setIsPortfolioFechado(res.data.is_portfolio_fechado);
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [nomeResponsavel]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // LÓGICA DE EXTRAÇÃO FINANCEIRA E CAIXAS
  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const ed = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (ed !== undefined) return ed.novo_volume; // Caixas físicas
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.vol_sim || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicVol(child, mesBanco), 0);
  }, [celulasEditadas]);

  const getDynamicRec = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const vol = getDynamicVol(row, mesBanco);
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return vol * (m?.pmv || 0);
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicRec(child, mesBanco), 0);
  }, [getDynamicVol]);

  const getStaticMeta = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.rec_meta || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getStaticMeta(child, mesBanco), 0);
  }, []);

  // LÓGICA DE RATEIO DO DINHEIRO PARA CAIXAS
  const handleEditCell = (chaveStr: string, mesBanco: string, novoValorRS: number) => {
    if (isFechado) return;

    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };

      const findNode = (nodes: any[]): any => {
        for (const n of nodes) {
          if (n.chave_matriz === chaveStr) return n;
          if (n.subRows) {
            const found = findNode(n.subRows);
            if (found) return found;
          }
        }
        return null;
      };

      const targetNode = findNode(dadosBrutos);
      if (!targetNode) return nextEdits;

      const leaves: any[] = [];
      const getLeaves = (n: any) => {
        if (n.tipo === 'produto') leaves.push(n);
        else if (n.subRows) n.subRows.forEach(getLeaves);
      };
      getLeaves(targetNode);

      const totalPesoFat = leaves.reduce((sum, leaf) => sum + (leaf.peso_fat || 1), 0);

      const fractions = leaves.map(leaf => {
          const weight = leaf.peso_fat || 1;
          const target_RS = totalPesoFat > 0 ? (weight / totalPesoFat) * novoValorRS : (1 / leaves.length) * novoValorRS;
          const pmv = leaf.meses.find((m: any) => m.mes_banco === mesBanco)?.pmv || 1;
          
          const exactBoxes = target_RS / pmv;
          const intVal = Math.floor(exactBoxes);
          return { leaf, intVal, pmv, remRS: (exactBoxes - intVal) * pmv };
      });

      let currentRS = fractions.reduce((sum, item) => sum + (item.intVal * item.pmv), 0);

      fractions.sort((a, b) => b.remRS - a.remRS);
      for (let i = 0; i < fractions.length; i++) {
          if (currentRS + fractions[i].pmv <= novoValorRS + (fractions[i].pmv / 2)) {
              fractions[i].intVal++;
              currentRS += fractions[i].pmv;
          }
      }

      fractions.forEach(item => {
          if (!nextEdits[item.leaf.chave_matriz]) nextEdits[item.leaf.chave_matriz] = {};
          nextEdits[item.leaf.chave_matriz][mesBanco] = { novo_volume: item.intVal };
      });

      return nextEdits;
    });
  };

  // VALIDADOR: REGRA DOS 99% - 101%
  const isSaveBlocked = useMemo(() => {
    if (dadosBrutos.length === 0) return false;
    for (const m of colunasData) {
      let totalMetaRS = 0;
      let totalSimRS = 0;
      dadosBrutos.forEach(rootNode => {
        totalMetaRS += getStaticMeta(rootNode, m.mes_banco);
        totalSimRS += getDynamicRec(rootNode, m.mes_banco);
      });
      if (totalMetaRS > 0) {
        const percent = (totalSimRS / totalMetaRS) * 100;
        if (percent < 99 || percent > 101) return true; 
      }
    }
    return false;
  }, [dadosBrutos, colunasData, celulasEditadas, getStaticMeta, getDynamicRec]);

  // FILTRO DA BARRA DE PESQUISA (Original Restaurado)
  const dadosFiltrados = useMemo(() => {
    if (!busca) return dadosBrutos;
    const term = busca.toLowerCase();
    
    return dadosBrutos.map(raiz => {
      if (raiz.nome.toLowerCase().includes(term)) return raiz;
      
      const sub1Filtrados = (raiz.subRows || []).map((s1: any) => {
        if (s1.nome.toLowerCase().includes(term)) return s1;
        
        const sub2Filtrados = (s1.subRows || []).map((s2: any) => {
           if (s2.nome.toLowerCase().includes(term)) return s2;
           const prodsFiltrados = (s2.subRows || []).filter((p: any) => 
              p.nome.toLowerCase().includes(term) || (p.produto && p.produto.toLowerCase().includes(term))
           );
           if (prodsFiltrados.length > 0) return { ...s2, subRows: prodsFiltrados };
           return null;
        }).filter(Boolean);

        if (sub2Filtrados.length > 0) return { ...s1, subRows: sub2Filtrados };
        return null;
      }).filter(Boolean);

      if (sub1Filtrados.length > 0) return { ...raiz, subRows: sub1Filtrados };
      return null;
    }).filter(Boolean);
  }, [dadosBrutos, busca]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, {vol: number, fat: number, orc: number}> = {};
    colunasData.forEach((m: any) => totais[m.mes_banco] = {vol: 0, fat: 0, orc: 0});
    
    dadosFiltrados.forEach(root => {
      colunasData.forEach((m: any) => {
        totais[m.mes_banco].vol += getDynamicVol(root, m.mes_banco);
        totais[m.mes_banco].fat += getDynamicRec(root, m.mes_banco);
        totais[m.mes_banco].orc += getStaticMeta(root, m.mes_banco);
      });
    });
    return totais;
  }, [dadosFiltrados, colunasData, getDynamicVol, getDynamicRec, getStaticMeta]);

  // EXPORTAÇÃO PARA EXCEL (Original Restaurado e Adaptado para R$)
  const handleExportExcel = () => {
    if (dadosFiltrados.length === 0) return alert("Não há dados no ecrã para exportar.");
    const dadosExcel: any[] = [];
    
    const extrairFolhas = (nodes: any[], path: any) => {
        nodes.forEach(n => {
            const currentPath = {...path};
            if (n.tipo === 'coordenador') currentPath.COORDENADOR = n.nome;
            if (n.tipo === 'vendedor') currentPath.VENDEDOR = n.nome;
            if (n.tipo === 'cliente') currentPath.CLIENTE = n.nome;
            
            if (n.tipo === 'produto') {
                const linha: any = {
                   "COORDENADOR": currentPath.COORDENADOR || '-',
                   "VENDEDOR": currentPath.VENDEDOR || '-',
                   "RAZÃO SOCIAL": currentPath.CLIENTE || '-',
                   "CÓDIGO SKU": n.produto,
                   "DESCRIÇÃO": n.nome,
                   "PMV APLICADO (R$)": n.meses[0]?.pmv || 0
                };
                n.meses.forEach((m: any) => {
                    const volFinal = getDynamicVol(n, m.mes_banco);
                    linha[`${m.mes_str} - TETO META (R$)`] = Math.round(m.rec_meta || 0);
                    linha[`${m.mes_str} - SIMULADO (R$)`] = Math.round(volFinal * m.pmv);
                    linha[`${m.mes_str} - SIMULADO (CX)`] = volFinal;
                });
                dadosExcel.push(linha);
            } else if (n.subRows) {
                extrairFolhas(n.subRows, currentPath);
            }
        });
    };
    extrairFolhas(dadosFiltrados, {});

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Gestao_Carteira_Equipa");
    XLSX.writeFile(workbook, `SOP_Nexus_Equipa_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) {
      setChartExpanded(null); 
      return;
    }
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/micro/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const columns = useMemo(() => {
    if (dadosFiltrados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Equipa e Carteira',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, produto } = row.original;
          const isProduto = tipo === 'produto';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[320px]">
              {!isProduto ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-200 text-slate-500 rounded-lg transition-colors">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : (
                <div className="w-8"></div>
              )}
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 border-indigo-200 text-indigo-600 shadow-sm' : 'hover:bg-slate-100 border-transparent text-slate-400 hover:text-slate-600'}`} title="Ver Gráfico S&OE">
                <BarChart2 className="w-4 h-4" />
              </button>

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${tipo === 'coordenador' || tipo === 'gerente' ? 'bg-slate-900 border-slate-800 text-white' : tipo === 'vendedor' ? 'bg-blue-50 border-blue-100 text-blue-600' : tipo === 'cliente' ? 'bg-indigo-50 border-indigo-100 text-indigo-600' : 'bg-slate-50 border-slate-100 text-slate-400'}`}>
                {tipo === 'vendedor' || tipo === 'coordenador' || tipo === 'gerente' ? <Users className="w-4 h-4" /> : tipo === 'cliente' ? <Store className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm ${!isProduto ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[250px]'}`}>
                   {info.getValue()}
                 </span>
                 {isProduto && (
                   <div className="flex items-center gap-2 mt-1">
                     <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{produto}</span>
                     <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">PMV: {formatMoeda(row.original.meses[0]?.pmv || 0)}</span>
                   </div>
                 )}
              </div>
            </div>
          )
        }
      }
    ];

    colunasData.forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        accessorFn: (row: any) => getDynamicRec(row, m.mes_banco),
        cell: (info: any) => {
          const row = info.row.original;
          const mBanco = m.mes_banco;
          const isProduto = row.tipo === 'produto';
          
          const metaRS = getStaticMeta(row, mBanco);
          const simRS = getDynamicRec(row, mBanco);
          const simCX = getDynamicVol(row, mBanco);
          
          const percent = metaRS > 0 ? (simRS / metaRS) * 100 : (simRS > 0 ? 999 : 100);
          const isValid = percent >= 99 && percent <= 101;
          const blockedInput = row.tipo !== 'vendedor' && row.tipo !== 'cliente' && row.tipo !== 'coordenador' && row.tipo !== 'gerente';

          return (
            <div className="flex flex-col items-center justify-center py-2 w-32 relative">
              <div className="flex justify-between items-center px-1 mb-1">
                 <span className="text-[10px] text-slate-400 font-black uppercase tracking-widest flex items-center gap-1" title="Teto Heradado">
                    <Target className="w-3 h-3" /> Meta: {formatMoeda(metaRS)}
                 </span>
              </div>
              
              <SmartCurrencyInput 
                 value={simRS} 
                 onChange={(val) => handleEditCell(row.chave_matriz, mBanco, val)} 
                 disabled={isFechado} 
                 blocked={isProduto} // SKUs não recebem dinheiro direto, recebem o rateio de cima.
              />
              
              <div className="flex flex-col items-center mt-1.5 w-full">
                 <span className="text-[11px] font-black text-slate-600 bg-slate-100 px-2 py-0.5 rounded tracking-widest border border-slate-200">
                    {formatVolume(simCX)} CX
                 </span>
                 
                 {/* O Semáforo de Tolerância */}
                 <div className={`mt-1.5 w-[80%] h-1 rounded-full transition-colors ${isValid ? 'bg-emerald-400' : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)]'}`} title={isValid ? 'Dentro da Margem (99-101%)' : 'Fora da Tolerância'} />
              </div>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, chartExpanded, celulasEditadas, isFechado]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
  });

  const handleSalvar = async () => {
    if (isSaveBlocked) return alert("Erro de Tolerância! Ajuste a distribuição financeira da equipa. O simulado deve ficar entre 99% e 101% da Meta herdada.");
    if (Object.keys(celulasEditadas).length === 0) return alert("Nenhuma alteração para salvar.");
    
    setIsProcessing(true);
    const leafEdits = Object.entries(celulasEditadas).filter(([chave]) => chave.split('|').length >= 2);
    const ajustes = leafEdits.flatMap(([chave, meses]: any) => Object.entries(meses).map(([mes_projetado, val]: any) => ({ chave, mes_projetado, novo_volume: val.novo_volume })));
    
    try {
      await axios.post(`/api/v1/consensus/micro/congelar`, { origem_ajuste: 'Carteira', ajustes });
      alert("✅ Faturamento distribuído com sucesso! Volumes das Carteiras Consolidados.");
      fetchData(); 
    } catch (e: any) { alert("Erro ao salvar."); }
    finally { setIsProcessing(false); }
  };

  // TELA DE BLOQUEIO DE FASE DA DIRETORIA
  if (isPortfolioFechado === null) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Verificando Status do Ciclo...</span>
      </div>
    );
  }

  if (isPortfolioFechado === false) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans p-6">
        <div className="bg-white p-12 rounded-[40px] shadow-xl border border-slate-100 flex flex-col items-center max-w-lg text-center animate-in fade-in zoom-in duration-500">
          <div className="w-20 h-20 bg-blue-50 rounded-full flex items-center justify-center mb-6 border border-blue-100">
            <Lock className="w-10 h-10 text-blue-500" />
          </div>
          <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-4">Aguardando Fase Anterior</h2>
          <p className="text-slate-500 font-medium leading-relaxed">
            A distribuição de carteiras só pode ocorrer após o Gerente Comercial travar o Portfólio Global (Fase 2).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO RESTAURADO COM TRAVA DE TOLERÂNCIA */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isFechado && !isLoading && (
              <div className="absolute top-0 left-0 w-full bg-emerald-500 text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10">
                  <ShieldCheck className="w-3 h-3" /> DISTRIBUIÇÃO CONCLUÍDA NO CICLO ATUAL
              </div>
          )}
          
          <div className={isFechado ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <TrendingUp className="w-8 h-8 text-indigo-600" /> Gestão de Carteiras
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Distribuição Financeira Tática M-2
            </p>
          </div>
          
          <div className={`flex items-center gap-4 ${isFechado ? "pt-4" : ""}`}>
              <div className="flex items-center gap-3 h-full">
                {Object.keys(celulasEditadas).length > 0 && !isFechado && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                
                <button onClick={handleSalvar} disabled={isProcessing || isFechado || isSaveBlocked} className={`flex items-center gap-2 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg ${isFechado || isSaveBlocked ? 'bg-slate-300 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-black shadow-slate-900/30'}`}>
                    {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : isFechado ? <Lock className="w-5 h-5" /> : isSaveBlocked ? <ShieldAlert className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                    {isFechado ? 'Distribuição Submetida' : isSaveBlocked ? 'Ajuste a Tolerância' : 'Gravar e Consolidar Equipa'}
                </button>
              </div>
          </div>
        </div>

        {/* ALERTA DE TOLERÂNCIA */}
        {isSaveBlocked && dadosBrutos.length > 0 && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-5 rounded-2xl flex items-center gap-4 shadow-sm mb-6 animate-in fade-in slide-in-from-top-4">
                <ShieldAlert className="w-6 h-6 text-rose-500" />
                <div>
                  <h3 className="font-black uppercase tracking-widest text-xs">Aviso de Regra de Ouro (99% - 101%)</h3>
                  <p className="text-sm font-medium mt-1">O Faturamento simulado (após a conversão em caixas físicas) não corresponde à Meta imposta. Ajuste a distribuição até que os indicadores verdes validem o rateio.</p>
                </div>
            </div>
        )}

        {/* CONTROLES E BUSCA (Original Restaurado) */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
            <div className="flex items-center gap-4 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex-shrink-0 px-6">
                <span className="text-xs font-black text-slate-500 uppercase tracking-widest">Filtro de Equipa:</span>
                <select 
                    value={nomeResponsavel} 
                    onChange={e => { setNomeResponsavel(e.target.value); }} 
                    className="bg-slate-50 border border-slate-200 p-2.5 rounded-xl text-sm font-bold text-slate-700 outline-none cursor-pointer"
                >
                    <option value="">-- Toda a Equipa --</option>
                    {isGerenteOrAdmin ? opcoesBusca.coordenadores.map(opt => <option key={opt} value={opt}>{opt}</option>) : opcoesBusca.vendedores.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                </select>
                <button 
                  onClick={fetchData} 
                  disabled={isLoading} 
                  className="bg-indigo-50 text-indigo-600 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-indigo-100 transition disabled:opacity-50 flex items-center gap-2"
                >
                   {isLoading ? <Loader2 className="w-4 h-4 animate-spin"/> : <Filter className="w-4 h-4"/>} 
                   Filtrar
                </button>
            </div>

           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 h-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Pesquisar livremente na árvore..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-2 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-indigo-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 h-full whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar Planilha
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS (Original Restaurada) */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">A carregar Matriz Comercial da Equipa...</span>
             </div>
          ) : dadosFiltrados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Filter className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">Nenhum dado encontrado</span>
             </div>
          ) : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className={`px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest ${header.column.getCanSort() ? 'cursor-pointer hover:bg-slate-50 transition-colors' : ''}`} onClick={header.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getCanSort() && (
                            <span className="text-slate-300">
                              {{ asc: <ArrowUp className="w-4 h-4 text-slate-500" />, desc: <ArrowDown className="w-4 h-4 text-slate-500" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-30" />}
                            </span>
                          )}
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-indigo-50/20' : row.depth === 0 ? 'bg-slate-50 hover:bg-slate-100' : 'bg-white hover:bg-slate-50'}`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-3">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {/* DOSSIÊ EXECUTIVO TEMA ESCURO (Original Restaurado) */}
                    {chartExpanded === row.original.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500">
                            
                            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                               <div className="lg:col-span-2">
                                 <div className="flex justify-between items-start mb-6 px-2">
                                    <div className="flex flex-col gap-1">
                                      <h3 className="text-lg font-black text-white uppercase tracking-tighter flex items-center gap-2">
                                        <Activity className="w-5 h-5 text-emerald-400" /> Curva S&OE Analítica
                                      </h3>
                                      <p className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                                        {row.original.nome}
                                      </p>
                                    </div>
                                 </div>

                                 <div className="h-[250px] w-full">
                                   {loadingGrafico === row.original.chave_matriz ? (
                                     <div className="h-full flex items-center justify-center text-slate-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                                   ) : (
                                     <ResponsiveContainer width="100%" height="100%">
                                       <LineChart 
                                         data={
                                           (dadosGraficoCache[row.original.chave_matriz] || []).map((p: any) => {
                                               const m = row.original.meses?.find((x: any) => x.mes_banco === p.data_iso);
                                               let dynamicBU = p.Consenso;
                                               if (m) dynamicBU = getDynamicVol(row.original, m.mes_banco);
                                               return { ...p, Consenso: dynamicBU };
                                           })
                                         }
                                         margin={{ top: 20, right: 30, left: 20, bottom: 10 }}
                                       >
                                         <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                         <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                         <YAxis tickFormatter={(val) => formatVolume(val)} tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                         <Tooltip contentStyle={{borderRadius: '20px', backgroundColor: '#0f172a', border: '1px solid #1e293b', color: '#fff', boxShadow: '0 10px 30px rgba(0,0,0,0.5)'}} />
                                         <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900', color: '#cbd5e1'}} />
                                         
                                         <Line type="monotone" dataKey="CicloAnterior" name="Lag 1 (Ciclo Passado)" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={true} />
                                         <Line type="monotone" dataKey="IA" name="Modelo IA (Baseline)" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={true} />
                                         <Line type="monotone" dataKey="Realizado" name="Realizado (Histórico)" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls={true} />
                                         
                                         <Line type="monotone" dataKey="Consenso" name="Sua Proposta Dinâmica" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#0f172a'}} connectNulls={true} />
                                       </LineChart>
                                     </ResponsiveContainer>
                                   )}
                                 </div>
                               </div>

                               <div className="flex flex-col justify-start">
                                 <AiInsightBox 
                                    alvo={row.original.nome} 
                                    tipo={row.original.tipo} 
                                    pmv={row.original.meses[0]?.pmv || 0}
                                 />
                               </div>
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
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-8 py-5 text-right border-r border-slate-800">
                        <div className="flex flex-col"><span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Totalização</span><span className="font-bold text-sm text-white">EQUIPA COMERCIAL</span></div>
                      </td>
                    );
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
                    return <td key={header.id} className="px-8 py-5"></td>;
                  })}
                </tr>
              </tfoot>
            </table>
          </div>
          )}
        </div>
      </div>
    </div>
  );
}