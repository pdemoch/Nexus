import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  getExpandedRowModel,
  getSortedRowModel,
  SortingState
} from '@tanstack/react-table';
import { Loader2, ChevronDown, ChevronUp, ArrowUpDown, ArrowUp, ArrowDown, Activity, BrainCircuit, ShieldCheck, Check, Hash, Search, Filter, Download, X, Target, History, Wand2 } from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

// =====================================================================
// NOVO COMPONENTE: GERADOR DE INSIGHTS COM IA SOB DEMANDA
// =====================================================================
const AiInsightBox = ({ produto, pmv, mediaHist, ia, anterior }: { produto: string, pmv: number, mediaHist: number, ia: number, anterior: number }) => {
  const [insight, setInsight] = useState('');
  const [loading, setLoading] = useState(false);

  const getInsight = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/ai-sql/perguntar', {
        pergunta: `Faça o dossiê executivo 360° para o SKU ${produto}. Extraia a cascata de volumes, o histórico de vendas e o pmv_aplicado. Apresente o diagnóstico financeiro (R$) e estratégico utilizando a metodologia dos 4 pilares.`,
        contexto: { tela_ativa: 'Top-Down Arena', ciclo_status: 'Aberto' }
      });
      setInsight(res.data.resposta);
    } catch (e) {
      setInsight('Erro ao gerar insight.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-6 bg-indigo-50/50 rounded-2xl p-6 border border-indigo-100 flex items-start gap-4">
      <div className="p-3 bg-indigo-100 text-indigo-600 rounded-xl">
        <Wand2 className="w-6 h-6" />
      </div>
      <div className="flex-1">
        <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-2">Nexus AI Insight 360°</h4>
        {insight ? (
          <div className="text-sm font-medium text-slate-600 leading-relaxed whitespace-pre-wrap" dangerouslySetInnerHTML={{ __html: insight.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-slate-900">$1</strong>') }} />
        ) : (
          <button onClick={getInsight} disabled={loading} className="text-xs font-bold bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 transition flex items-center gap-2 disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Gerar Parecer Estratégico 360°'}
          </button>
        )}
      </div>
    </div>
  );
};

export default function TopDownArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isCicloFechado, setIsCicloFechado] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);
  
  const [busca, setBusca] = useState('');
  const [categoriaSelecionada, setCategoriaSelecionada] = useState('TODAS');
  const [segmentoSelecionado, setSegmentoSelecionado] = useState('TODOS');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [res, statusRes] = await Promise.all([
        axios.get('/api/v1/consensus/macro/list'), // ROTA ATUALIZADA PARA BUSCAR DOSSIÊ
        axios.get('/api/v1/consensus/macro/status')
      ]);
      setDadosBrutos(res.data.dados || []);
      setIsCicloFechado(statusRes.data.is_topdown_fechado);
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleExportExcel = (isSnapshot = false) => {
    if (dadosFiltrados.length === 0) return alert("Não há dados na tela para exportar.");
    const dadosExcel = dadosFiltrados.map(row => {
      const pmvBase = row.meses[0]?.pmv || 0;
      const linha: any = {
        "CÓDIGO SKU": row.produto, "DESCRIÇÃO": row.descricao, "CATEGORIA": row.categoria,
        "SEGMENTO": row.segmento, "MODELO IA": row.modelo_vencedor, "ACURÁCIA IA (%)": row.acuracia_ia,
        "PMV PONDERADO (R$)": pmvBase
      };
      row.meses.forEach((m: any) => {
        const edicao = celulasEditadas[row.produto]?.[m.mes_banco];
        const volFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : m.vol_ajustado));
        linha[`${m.mes_str} (Base IA)`] = Math.round(Number(m.vol_ia));
        linha[`${m.mes_str} (Mês Passado)`] = Math.round(Number(m.vol_anterior || 0)); // EXPORTA O MÊS PASSADO
        linha[`${m.mes_str} (Top-Down)`] = volFinal;
      });
      return linha;
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Top_Down_Consenso");
    worksheet['!cols'] = [{ wch: 15 }, { wch: 40 }, { wch: 20 }, { wch: 20 }, { wch: 20 }, { wch: 15 }, { wch: 18 }];
    
    const fileName = `SOP_Nexus_TopDown_${isSnapshot ? 'CONGELADO' : 'DRAFT'}_${new Date().toISOString().split('T')[0]}.xlsx`;
    XLSX.writeFile(workbook, fileName);
  };

  const handleCongelarCiclo = async () => {
    if (!window.confirm("Atenção: Ao gravar e assinar o ciclo Executivo, a meta será rateada para toda a operação. Deseja continuar?")) return;
    setIsProcessing(true);
    
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([produto, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ produto: produto, mes_projetado: mes, novo_volume: val.novo_volume === '' ? 0 : val.novo_volume });
      });
    });

    try {
      handleExportExcel(true); 
      await axios.post('/api/v1/consensus/macro/congelar', { origem_ajuste: "Top-Down", ajustes });
      alert("🔒 Ciclo Top-Down congelado e rateado com sucesso!");
      setCelulasEditadas({});
      fetchData(); 
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao processar rateio."); 
    } finally { 
      setIsProcessing(false); 
    }
  };

  const categoriasUnicas = useMemo(() => Array.from(new Set(dadosBrutos.map(d => d.categoria).filter(Boolean))).sort(), [dadosBrutos]);
  const segmentosUnicos = useMemo(() => Array.from(new Set(dadosBrutos.map(d => d.segmento).filter(Boolean))).sort(), [dadosBrutos]);

  const dadosFiltrados = useMemo(() => dadosBrutos.filter(d => 
    (d.produto + ' ' + d.descricao).toLowerCase().includes(busca.toLowerCase()) &&
    (categoriaSelecionada === 'TODAS' || d.categoria === categoriaSelecionada) &&
    (segmentoSelecionado === 'TODOS' || d.segmento === segmentoSelecionado)
  ), [dadosBrutos, busca, categoriaSelecionada, segmentoSelecionado]);

  // =========================================================================
  // A MÁGICA DA RECEITA E VOLUME DINÂMICO
  // =========================================================================
  const totaisCalculados = useMemo(() => {
    const totais: Record<string, { faturamento: number, volume: number }> = {};
    dadosFiltrados.forEach(row => row.meses.forEach((m: any) => {
      totais[m.mes_banco] = { faturamento: 0, volume: 0 };
    }));
    
    dadosFiltrados.forEach(row => {
      const pmvBase = row.pmv_base || row.meses[0]?.pmv || 0;
      
      row.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[row.produto]?.[mes.mes_banco];
        const isPendente = edicao !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicao.novo_volume : mes.vol_ajustado));
        
        const receitaReal = isPendente ? (volumeFinal * pmvBase) : (mes.receita || (volumeFinal * pmvBase));
        
        if (!totais[mes.mes_banco]) totais[mes.mes_banco] = { faturamento: 0, volume: 0 };
        totais[mes.mes_banco].faturamento += receitaReal;
        totais[mes.mes_banco].volume += volumeFinal;
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const toggleRow = async (row: any) => {
    const isExpanding = !row.getIsExpanded();
    const chave = row.original.produto;
    
    if (isExpanding && !dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/macro/grafico', { params: { produto: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
    row.toggleExpanded();
  };

  const qtdEdicoes = Object.keys(celulasEditadas).length;

  const columns = useMemo(() => {
    if (dadosFiltrados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'expander', enableSorting: false, header: () => null,
        cell: ({ row }: any) => (
          <button onClick={() => toggleRow(row)} className="p-2 hover:bg-slate-100 rounded-xl transition-all">
            {row.getIsExpanded() ? <ChevronUp className="w-5 h-5 text-indigo-600" /> : <ChevronDown className="w-5 h-5 text-slate-400" />}
          </button>
        ),
      },
      {
        id: 'info', header: 'SKU (Visão Brasil)',
        accessorFn: (row: any) => row.descricao,
        cell: (info: any) => (
          <div className="flex flex-col py-1">
            <span className="font-bold text-gray-900 text-sm truncate max-w-[320px]" title={info.row.original.descricao}>{info.row.original.descricao}</span>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] text-slate-400 font-black uppercase tracking-tighter bg-slate-100 px-2 py-0.5 rounded-md">{info.row.original.produto}</span>
              <span className="text-[10px] text-indigo-400 font-black uppercase tracking-tighter">{info.row.original.categoria}</span>
            </div>
          </div>
        )
      },
      {
        id: 'acuracia', header: 'Motor IA', accessorFn: (row: any) => row.acuracia_ia,
        cell: (info: any) => {
          const acc = info.getValue();
          const modelo = info.row.original.modelo_vencedor;
          const cor = acc >= 80 ? 'text-emerald-500' : acc >= 60 ? 'text-amber-500' : 'text-rose-500';
          return (
            <div className="flex flex-col items-start w-28">
               <span className={`text-xs font-black flex items-center gap-1 ${cor}`}><BrainCircuit className="w-3 h-3"/> {acc.toFixed(1)}%</span>
               <span className="text-[8px] text-slate-400 font-bold uppercase tracking-widest truncate max-w-full" title={modelo}>{modelo}</span>
            </div>
          );
        }
      },
      {
        id: 'pmv_base', header: 'PMV Ponderado',
        accessorFn: (row: any) => row.pmv_base || row.meses[0]?.pmv || 0,
        cell: (info: any) => (
          <div className="flex flex-col py-1">
            <span className="font-bold text-slate-700 text-[13px] bg-slate-100 px-3 py-1.5 rounded-lg inline-block w-max border border-slate-200">
              {formatMoeda(info.getValue())}
            </span>
          </div>
        )
      }
    ];

    const mesesMap = new Map();
    dadosFiltrados.forEach(r => r.meses.forEach((m: any) => mesesMap.set(m.mes_banco, m)));
    
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
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const volAnterior = Math.round(Number(dadosMes?.vol_anterior || 0)); // VALOR DO MÊS PASSADO
          const isChanged = valorInteiro !== baseIA;
          
          const pmvBase = row.pmv_base || row.meses[0]?.pmv || 0;
          const faturamentoPrevisto = isPendente ? (valorInteiro * pmvBase) : (dadosMes?.receita || (valorInteiro * pmvBase));

          return (
            <div className="flex flex-col min-w-[130px] gap-1">
              
              {/* O NOVO CABEÇALHO DA CÉLULA (IA vs ANTERIOR) */}
              <div className="flex items-center justify-between px-1 mb-0.5">
                <div className="flex items-center gap-1" title="Sinal da IA">
                  <Target className="w-3 h-3 text-slate-300" />
                  <span className="text-[9px] font-bold text-slate-400">{formatVolume(baseIA)}</span>
                </div>
                <div className="flex items-center gap-1" title="Volume Acordado no Ciclo Anterior">
                  <History className="w-3 h-3 text-purple-300" />
                  <span className="text-[9px] font-bold text-purple-500">{formatVolume(volAnterior)}</span>
                </div>
              </div>

              <input
                type="number" disabled={isCicloFechado} value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                onChange={(e) => meta.updateCell(row.produto, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2 rounded-xl outline-none border-2 transition-all w-full
                  ${isCicloFechado ? 'cursor-not-allowed opacity-50 bg-slate-100 border-gray-200' : isChanged ? 'bg-slate-800 border-slate-700 text-white shadow-md' : 'bg-gray-50 border-transparent text-gray-800 focus:bg-white focus:border-slate-300'}`}
              />
              <span className="text-[10px] font-black text-emerald-600 text-center pr-1 tracking-tight mt-0.5">
                {formatMoeda(faturamentoPrevisto)}
              </span>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, isCicloFechado]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (chave: string, mes: string, val: string) => {
        if (isCicloFechado) return;
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV } } }));
      }
    }
  });

  if (isLoading) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Iniciando Visão Executiva...</span>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
          <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
            <Activity className="w-8 h-8 text-slate-800" /> Plano de Demanda Irrestrita
          </h1>
          <div className="flex items-center gap-4">
            {isCicloFechado ? (
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2 bg-slate-800 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-slate-800/20">
                      <Check className="w-5 h-5 text-emerald-400" /> Top-Down Congelado
                  </div>
                </div>
            ) : (
                <div className="flex items-center gap-3 h-full">
                  {qtdEdicoes > 0 && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                  <button onClick={handleCongelarCiclo} disabled={isProcessing} className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-indigo-600/30 transition-all disabled:opacity-50">
                      {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : qtdEdicoes > 0 ? 'Gravar e Ratear Lojas' : <ShieldCheck className="w-5 h-5" />}
                      {qtdEdicoes > 0 ? '' : 'Aprovar Ciclo Base IA'}
                  </button>
                </div>
            )}
          </div>
        </div>

        {/* BARRA DE FILTROS */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
           <div className="bg-slate-900 text-white px-8 py-4 rounded-[24px] flex items-center gap-4 shadow-lg flex-shrink-0">
             <div className="bg-slate-800 p-2 rounded-full"><Hash className="w-6 h-6 text-indigo-400" /></div>
             <div>
               <p className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-0.5">Skus na Tela</p>
               <p className="text-2xl font-black leading-none">{dadosFiltrados.length}</p>
             </div>
           </div>

           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-gray-100 flex flex-col md:flex-row items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Buscar por código ou descrição do SKU..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-3 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <div className="flex items-center gap-2 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full md:w-auto">
               <Filter className="w-4 h-4 text-slate-400" />
               <select value={categoriaSelecionada} onChange={e => setCategoriaSelecionada(e.target.value)} className="bg-transparent py-3 text-sm font-bold text-slate-700 outline-none cursor-pointer">
                 <option value="TODAS">TODAS AS CATEGORIAS</option>
                 {categoriasUnicas.map(c => <option key={c as string} value={c as string}>{c as string}</option>)}
               </select>
             </div>
             <div className="flex items-center gap-2 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full md:w-auto">
               <Filter className="w-4 h-4 text-slate-400" />
               <select value={segmentoSelecionado} onChange={e => setSegmentoSelecionado(e.target.value)} className="bg-transparent py-3 text-sm font-bold text-slate-700 outline-none cursor-pointer">
                 <option value="TODOS">TODOS OS SEGMENTOS</option>
                 {segmentosUnicos.map(s => <option key={s as string} value={s as string}>{s as string}</option>)}
               </select>
             </div>
             <button onClick={() => handleExportExcel(false)} className="flex items-center gap-2 bg-slate-100 hover:bg-indigo-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 ml-auto whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS E GRÁFICOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-gray-100 overflow-hidden relative">
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-gray-100 shadow-sm">
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
                  <React.Fragment key={row.id}>
                    <tr className={`border-b border-gray-50 transition-colors ${row.getIsExpanded() ? 'bg-slate-50/60' : 'hover:bg-slate-50/30'}`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-4">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {row.getIsExpanded() && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-50/50 p-8 border-b border-gray-100">
                          <div className="bg-white rounded-[40px] p-8 shadow-inner border border-gray-100 animate-in fade-in duration-500">
                            
                            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 mb-6">
                               {/* BLOCO 1: INFO E KPIs DO DOSSIÊ */}
                               <div className="flex flex-col gap-4">
                                 <h3 className="text-lg font-black text-slate-800 uppercase tracking-tighter">
                                   Dossiê Estratégico: {row.original.produto}
                                 </h3>
                                 <div className="grid grid-cols-2 gap-4">
                                    <div className="bg-slate-50 p-4 rounded-2xl border border-slate-100">
                                       <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest block mb-1">Média Mensal (Últ. 3 Meses)</span>
                                       <span className="text-xl font-black text-slate-800">{formatVolume(row.original.media_vendas_3m || 0)} <span className="text-xs text-slate-500">CX</span></span>
                                    </div>
                                    <div className="bg-slate-50 p-4 rounded-2xl border border-slate-100">
                                       <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest block mb-1">PMV Histórico (3M)</span>
                                       <span className="text-xl font-black text-slate-800">{formatMoeda(row.original.pmv_historico_3m || 0)}</span>
                                    </div>
                                 </div>
                                 <div className="flex gap-2">
                                    <div className="flex items-center gap-1.5 bg-slate-100 text-slate-700 px-3 py-1.5 rounded-xl border border-slate-200">
                                      <BrainCircuit className="w-4 h-4"/>
                                      <span className="text-[10px] font-black uppercase tracking-widest">
                                        Modelo: {row.original.modelo_vencedor || 'IA Padrão'}
                                      </span>
                                    </div>
                                    <div className="flex items-center gap-1.5 bg-emerald-50 text-emerald-700 px-3 py-1.5 rounded-xl border border-emerald-100">
                                      <ShieldCheck className="w-4 h-4"/>
                                      <span className="text-[10px] font-black uppercase tracking-widest">
                                        Acurácia: {row.original.acuracia_ia || 0}%
                                      </span>
                                    </div>
                                 </div>
                               </div>

                               {/* BLOCO 2: GRÁFICO (OCUPA 2 COLUNAS) */}
                               <div className="lg:col-span-2 h-[250px] w-full bg-slate-50 rounded-3xl p-4 border border-slate-100">
                                 {loadingGrafico === row.original.produto ? (
                                   <div className="h-full flex flex-col items-center justify-center text-slate-400 gap-3">
                                     <Loader2 className="animate-spin w-8 h-8 text-indigo-500" />
                                     <span className="text-[10px] font-black uppercase tracking-widest">Construindo Horizonte...</span>
                                   </div>
                                 ) : (
                                   <ResponsiveContainer width="100%" height="100%">
                                     <LineChart 
                                       data={
                                         (dadosGraficoCache[row.original.produto] || []).map((p: any) => {
                                             const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                             const edicao = celulasEditadas[row.original.produto]?.[p.data_iso];
                                             const valDiretoria = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                             return { ...p, Consenso: valDiretoria !== null ? valDiretoria : p.Consenso };
                                         })
                                       }
                                       margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                                     >
                                       <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                                       <XAxis dataKey="name" tick={{fontSize: 9, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                       <YAxis tick={{fontSize: 9, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                                       <Tooltip contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)', fontSize: '12px'}} formatter={(val: any) => formatVolume(val)} />
                                       <Legend iconType="circle" wrapperStyle={{paddingTop: '10px', fontSize: '10px', fontWeight: '900'}} />
                                       
                                       <Line type="monotone" dataKey="CicloAnterior" name="Proposta Mês Passado" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                                       <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={3} dot={{r: 3, fill: '#0f172a'}} connectNulls={false} />
                                       <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                       <Line type="monotone" dataKey="Consenso" name="Meta Gerencial" stroke="#3b82f6" strokeWidth={4} dot={{r: 5, fill: '#3b82f6', strokeWidth: 2, stroke: '#fff'}} connectNulls={false} />
                                     </LineChart>
                                   </ResponsiveContainer>
                                 )}
                               </div>
                            </div>

                            {/* BLOCO 3: INSIGHT DA IA (NOVO) */}
                            <AiInsightBox 
                              produto={row.original.descricao}
                              pmv={row.original.pmv_base || row.original.meses[0]?.pmv || 0}
                              mediaHist={row.original.media_vendas_3m || 0}
                              ia={row.original.meses[0]?.vol_ia || 0}
                              anterior={row.original.meses[0]?.vol_anterior || 0}
                            />
                            
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
              
              <tfoot className="bg-slate-900 text-white">
                <tr>
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'expander' || header.id === 'pmv_base' || header.id === 'acuracia') return <td key={header.id} className="px-8 py-5"></td>;
                    if (header.id === 'info') return (
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Totais da Tela</span>
                          <span className="font-bold text-sm text-white">VOLUME E RECEITA</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      const tot = totaisCalculados[mesBanco] || { volume: 0, faturamento: 0 };
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center gap-1 bg-slate-800/80 px-3 py-1.5 rounded-xl border border-slate-700/50">
                            <span className="font-black text-white text-[11px] tracking-tight">{formatVolume(tot.volume)} cx</span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight">{formatMoeda(tot.faturamento)}</span>
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
        </div>
      </div>
    </div>
  );
}