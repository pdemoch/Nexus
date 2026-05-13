import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Activity, Factory, ShieldCheck, Check, Hash, Search, Filter, Download, X, AlertTriangle, Lock, ShieldAlert, Wand2
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

// =====================================================================
// NOVO COMPONENTE: GERADOR DE INSIGHTS COM IA SOB DEMANDA (TEMA ESCURO)
// =====================================================================
const AiInsightBox = ({ alvo, pmv }: { alvo: string, pmv: number }) => {
  const [insight, setInsight] = useState('');
  const [loading, setLoading] = useState(false);

  const getInsight = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/ai-sql/perguntar', {
        pergunta: `Faça o dossiê executivo 360° para o SKU "${alvo}". Extraia a cascata de volumes, valide se o corte da fábrica (Supply) está alinhado com o histórico de vendas, e qual o impacto financeiro (R$) da restrição produtiva.`,
        contexto: { tela_ativa: 'Supply Review (Fábrica)', ciclo_status: 'Aberto/Em Ajuste' }
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
         <div className="p-2.5 bg-amber-500/20 text-amber-400 rounded-xl">
           <Wand2 className="w-5 h-5" />
         </div>
         <h4 className="text-sm font-black text-white uppercase tracking-widest">Nexus AI Insight 360°</h4>
      </div>
      
      {insight ? (
        <div className="text-sm font-medium text-slate-300 leading-relaxed whitespace-pre-wrap overflow-y-auto custom-scrollbar pr-2" dangerouslySetInnerHTML={{ __html: insight.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-white">$1</strong>') }} />
      ) : (
        <div className="flex flex-col items-start gap-3 mt-2">
           <p className="text-xs text-slate-500 font-medium">Acione o consultor executivo para avaliar o risco de ruptura e o impacto da restrição fabril no faturamento projetado.</p>
           <button onClick={getInsight} disabled={loading} className="mt-2 text-xs font-bold bg-amber-600 text-white px-4 py-3 rounded-xl hover:bg-amber-700 transition flex items-center gap-2 disabled:opacity-50 w-full justify-center shadow-lg shadow-amber-900/20">
             {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Avaliar Impacto Financeiro (R$)'}
           </button>
        </div>
      )}
    </div>
  );
};

export default function SupplyReviewArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  // STATUS DAS FASES
  const [isTopDownFechado, setIsTopDownFechado] = useState<boolean | null>(null);
  const [isBottomUpFechado, setIsBottomUpFechado] = useState<boolean | null>(null);
  const [isSupplyFechado, setIsSupplyFechado] = useState(false);
  
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [busca, setBusca] = useState('');
  const [categoriaSelecionada, setCategoriaSelecionada] = useState('TODAS');

  useEffect(() => {
    Promise.all([
        axios.get('/api/v1/consensus/macro/status'),
        axios.get('/api/v1/consensus/micro/status')
    ]).then(([resMacro, resMicro]) => {
        setIsTopDownFechado(resMacro.data.is_topdown_fechado);
        setIsBottomUpFechado(resMicro.data.is_fechado);
    }).catch(console.error);
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [resLista, resStatus] = await Promise.all([
         axios.get('/api/v1/consensus/supply/list'),
         axios.get('/api/v1/consensus/supply/status')
      ]);
      setDadosBrutos(resLista.data.dados || []);
      setIsSupplyFechado(resStatus.data.is_fechado);
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { 
    if (isTopDownFechado && isBottomUpFechado) fetchData(); 
  }, [fetchData, isTopDownFechado, isBottomUpFechado]);

  const handleExportExcel = () => {
    if (dadosBrutos.length === 0) return alert("Não há dados na tela para exportar.");
    const dadosExcel: any[] = [];
    
    dadosFiltrados.forEach(prod => {
      const linha: any = {
        "CÓDIGO SKU": prod.produto, "DESCRIÇÃO": prod.nome, 
        "CATEGORIA": prod.categoria, "SEGMENTO": prod.segmento, 
        "PMV PONDERADO (R$)": prod.meses[0]?.pmv || 0
      };
      prod.meses.forEach((m: any) => {
        const edicao = celulasEditadas[prod.produto]?.[m.mes_banco];
        const volFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : m.vol_ajustado));
        linha[`${m.mes_str} (Base IA)`] = Math.round(Number(m.vol_ia));
        linha[`${m.mes_str} (Acordo Comercial)`] = Math.round(Number(m.vol_bu || 0));
        linha[`${m.mes_str} (Capacidade Fábrica)`] = volFinal;
      });
      dadosExcel.push(linha);
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Supply_Review");
    worksheet['!cols'] = [{ wch: 15 }, { wch: 40 }, { wch: 20 }, { wch: 20 }, { wch: 18 }];
    XLSX.writeFile(workbook, `SOP_Nexus_Supply_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleSalvar = async () => {
    setIsProcessing(true);
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([sku, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
          produto: sku, 
          mes_projetado: mes, 
          novo_volume: val.novo_volume === '' ? 0 : val.novo_volume 
        });
      });
    });

    try {
      await axios.post(`/api/v1/consensus/supply/congelar`, { origem_ajuste: "Supply", ajustes });
      alert("✅ Restrições de fábrica aplicadas! Ciclo de Supply consolidado.");
      fetchData(); 
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao guardar.");
    } finally {
      setIsProcessing(false);
    }
  };

  const categoriasUnicas = useMemo(() => Array.from(new Set(dadosBrutos.map(d => d.categoria).filter(Boolean))).sort(), [dadosBrutos]);

  const dadosFiltrados = useMemo(() => {
    return dadosBrutos.filter(d => {
        const matchBusca = (d.produto + ' ' + d.nome).toLowerCase().includes(busca.toLowerCase());
        const matchCat = categoriaSelecionada === 'TODAS' || d.categoria === categoriaSelecionada;
        return matchBusca && matchCat;
    });
  }, [dadosBrutos, busca, categoriaSelecionada]);

  const totaisCalculados = useMemo(() => {
    const totais: Record<string, { faturamento: number, volume: number }> = {};
    
    dadosFiltrados.forEach(prod => {
      const pmvBase = prod.meses[0]?.pmv || 0;
      prod.meses.forEach((mes: any) => {
        if (!totais[mes.mes_banco]) totais[mes.mes_banco] = { faturamento: 0, volume: 0 };
        
        const edicaoProd = celulasEditadas[prod.produto]?.[mes.mes_banco];
        const hasNovoVol = edicaoProd !== undefined && edicaoProd.novo_volume !== undefined && edicaoProd.novo_volume !== '';
        const volumeFinal = Math.round(Number(hasNovoVol ? edicaoProd.novo_volume : mes.vol_ajustado));
        
        totais[mes.mes_banco].volume += volumeFinal;
        totais[mes.mes_banco].faturamento += hasNovoVol ? (volumeFinal * pmvBase) : (mes.receita || (volumeFinal * pmvBase));
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const toggleChart = async (row: any) => {
    const chave = row.original.produto;
    if (chartExpanded === chave) {
      setChartExpanded(null); 
      return;
    }
    
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/macro/grafico', { 
            params: { produto: chave } 
        });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const qtdEdicoes = Object.keys(celulasEditadas).length;

  const columns = useMemo(() => {
    if (dadosFiltrados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Produto / SKU',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          return (
            <div className="flex items-center gap-3 py-2 min-w-[320px]">
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === row.original.produto ? 'bg-amber-100 border-amber-200 text-amber-600 shadow-sm' : 'hover:bg-slate-100 border-transparent text-slate-400 hover:text-slate-600'}`} title="Ver Histórico">
                <Activity className="w-4 h-4" />
              </button>

              <div className="w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 bg-slate-800 border-slate-700 text-slate-300">
                <Factory className="w-4 h-4" />
              </div>
              
              <div className="flex flex-col">
                 <span className="text-sm font-black text-slate-800 uppercase tracking-tighter truncate max-w-[250px]">
                   {info.getValue()}
                 </span>
                 <div className="flex items-center gap-2 mt-1">
                     <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{row.original.produto}</span>
                     <span className="text-[9px] text-indigo-500 font-black uppercase tracking-widest">{row.original.categoria}</span>
                     <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">PMV: {formatMoeda(row.original.meses[0]?.pmv || 0)}</span>
                 </div>
              </div>
            </div>
          )
        }
      }
    ];

    const mesesMap = new Map();
    dadosFiltrados.forEach(v => v.meses.forEach((m: any) => mesesMap.set(m.mes_banco, m)));
    
    Array.from(mesesMap.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)).forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_ajustado || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.produto]?.[m.mes_banco];
          
          const isPendente = edicao !== undefined;
          const valorReal = isPendente ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const baseComercial = Math.round(Number(dadosMes?.vol_bu || 0)); // ACORDO COMERCIAL
          const isChanged = valorInteiro !== baseComercial;
          
          const faturamentoPrevisto = isPendente ? (valorInteiro * (dadosMes?.pmv || 0)) : (dadosMes?.receita || 0);
          const isCortado = valorInteiro < baseComercial;
          
          const bloqueado = meta.isSupplyFechado;

          return (
            <div className="flex flex-col w-28 gap-1.5 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest" title="Acordo Comercial Consolidado">COMERCIAL: {baseComercial}</span>
              </div>
              <input
                type="number" value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                disabled={bloqueado}
                onChange={(e) => meta.updateCell(row.produto, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${bloqueado ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed opacity-80' : 'border-solid'} 
                  ${isChanged && !bloqueado ? 'bg-amber-500 border-amber-600 text-white shadow-md' : !bloqueado ? 'bg-slate-50 border-transparent text-slate-800 focus:bg-white focus:border-slate-300' : ''}`}
                title={bloqueado ? "Restrições de Fábrica Congeladas" : "Editar Capacidade de Fornecimento"}
              />
              <span className={`text-[10px] font-black text-center tracking-tight flex items-center justify-center gap-1 ${bloqueado ? 'text-slate-400' : isCortado ? 'text-rose-500' : 'text-emerald-600'}`}>
                {isCortado && <AlertTriangle className="w-3 h-3" />}
                {formatMoeda(faturamentoPrevisto)}
              </span>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, chartExpanded]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      isSupplyFechado, 
      updateCell: (chave: string, mes: string, val: string) => {
        if (isSupplyFechado) return; 
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV } } }));
      }
    }
  });

  // TELA DE BLOQUEIO DE FASE
  if (isTopDownFechado === null || isBottomUpFechado === null) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-amber-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Verificando Integração de Fases...</span>
      </div>
    );
  }

  if (isTopDownFechado === false || isBottomUpFechado === false) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans p-6">
        <div className="bg-white p-12 rounded-[40px] shadow-xl border border-slate-100 flex flex-col items-center max-w-lg text-center animate-in fade-in zoom-in duration-500">
          <div className="w-20 h-20 bg-amber-50 rounded-full flex items-center justify-center mb-6 border border-amber-100">
            <Lock className="w-10 h-10 text-amber-500" />
          </div>
          <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-4">Fase de Supply Trancada</h2>
          <p className="text-slate-500 font-medium leading-relaxed">
            A etapa de Supply Review só será liberada após a Diretoria aprovar o plano macro e o Comercial consolidar a carteira nas Fases 1 e 2.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isSupplyFechado && !isLoading && (
              <div className="absolute top-0 left-0 w-full bg-slate-900 text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10">
                  <ShieldCheck className="w-3 h-3 text-emerald-400" /> CICLO GERAL ENCERRADO E CONGELADO (PLANO S&OP ASSINADO)
              </div>
          )}
          
          <div className={isSupplyFechado ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Factory className="w-8 h-8 text-slate-800" /> Supply Review
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Restrições de Capacidade e Cortes de Produção
            </p>
          </div>
          
          <div className={`flex items-center gap-4 ${isSupplyFechado ? "pt-4" : ""}`}>
              <div className="flex items-center gap-3 h-full">
                {qtdEdicoes > 0 && !isSupplyFechado && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                
                <button onClick={handleSalvar} disabled={isProcessing || isSupplyFechado} className={`flex items-center gap-2 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg ${isSupplyFechado ? 'bg-slate-300 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-black shadow-slate-900/30'}`}>
                    {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : isSupplyFechado ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                    {isSupplyFechado ? 'S&OP Fechado' : qtdEdicoes > 0 ? 'Gravar Cortes e Fechar Ciclo' : 'Assinar Plano sem Cortes'}
                </button>
              </div>
          </div>
        </div>

        {/* CONTROLES E BUSCA */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
            <div className="flex items-center gap-4 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex-shrink-0 px-6">
                <span className="text-xs font-black text-slate-500 uppercase tracking-widest">Categoria:</span>
                <select 
                    value={categoriaSelecionada} 
                    onChange={e => { setCategoriaSelecionada(e.target.value); }} 
                    className="bg-slate-50 border border-slate-200 p-2.5 rounded-xl text-sm font-bold text-slate-700 outline-none cursor-pointer"
                >
                    <option value="TODAS">-- Todas as Categorias --</option>
                    {categoriasUnicas.map(opt => (
                        <option key={opt as string} value={opt as string}>{opt as string}</option>
                    ))}
                </select>
                <button 
                  onClick={fetchData} 
                  disabled={isLoading} 
                  className="bg-slate-100 text-slate-700 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-slate-200 transition disabled:opacity-50 flex items-center gap-2"
                >
                   {isLoading ? <Loader2 className="w-4 h-4 animate-spin"/> : <Filter className="w-4 h-4"/>} 
                   Atualizar Base
                </button>
            </div>

           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 h-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Filtrar por SKU ou Descrição do Produto..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-2 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 h-full whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar Planilha
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-slate-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Carregando volumes comerciais...</span>
             </div>
          ) : dadosFiltrados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Filter className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">Nenhum produto listado</span>
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
                    <tr className={`border-b border-slate-50 transition-colors bg-white hover:bg-slate-50`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-4">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {chartExpanded === row.original.produto && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500">
                            
                            {/* LAYOUT EM GRADE PARA GRÁFICO + IA INSIGHT */}
                            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                               
                               {/* COLUNA ESQUERDA: GRÁFICO */}
                               <div className="lg:col-span-2">
                                 <div className="flex justify-between items-start mb-6 px-2">
                                    <div className="flex flex-col gap-1">
                                      <h3 className="text-lg font-black text-white uppercase tracking-tighter flex items-center gap-2">
                                        <Activity className="w-5 h-5 text-emerald-400" /> Histórico e Tendência
                                      </h3>
                                      <p className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                                        {row.original.nome}
                                      </p>
                                    </div>
                                 </div>

                                 <div className="h-[250px] w-full">
                                   {loadingGrafico === row.original.produto ? (
                                     <div className="h-full flex items-center justify-center text-slate-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                                   ) : (
                                     <ResponsiveContainer width="100%" height="100%">
                                       <LineChart 
                                         data={
                                           (dadosGraficoCache[row.original.produto] || []).map((p: any) => {
                                               const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                               const edicao = celulasEditadas[row.original.produto]?.[p.data_iso];
                                               const valAtual = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                               return { ...p, Consenso: valAtual !== null ? valAtual : p.Consenso };
                                           })
                                         }
                                         margin={{ top: 20, right: 30, left: 20, bottom: 10 }}
                                       >
                                         <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                         <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                         <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                         <Tooltip contentStyle={{borderRadius: '20px', backgroundColor: '#0f172a', border: '1px solid #1e293b', color: '#fff', boxShadow: '0 10px 30px rgba(0,0,0,0.5)'}} />
                                         <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900', color: '#cbd5e1'}} />
                                         
                                         <Line type="monotone" dataKey="Realizado" name="Venda Real" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls={false} />
                                         <Line type="monotone" dataKey="IA" name="Sinal IA (Base)" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                         <Line type="monotone" dataKey="Consenso" name="Capacidade Fábrica (Atual)" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#0f172a'}} connectNulls={false} />
                                       </LineChart>
                                     </ResponsiveContainer>
                                   )}
                                 </div>
                               </div>

                               {/* COLUNA DIREITA: BOTÃO IA 360° */}
                               <div className="flex flex-col justify-start">
                                 <AiInsightBox 
                                    alvo={row.original.produto} 
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
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Receita Consolidada</span>
                          <span className="font-bold text-sm text-white">TOTAL NA TELA</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      const tot = totaisCalculados[mesBanco] || { volume: 0, faturamento: 0 };
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white text-[11px] tracking-tight">{formatVolume(tot.volume)} cx</span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(tot.faturamento)}</span>
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