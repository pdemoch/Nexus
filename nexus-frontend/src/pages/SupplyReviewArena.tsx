import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronUp, ArrowUpDown, ArrowUp, ArrowDown, 
  Activity, Factory, ShieldCheck, Check, Hash, Search, Filter, Download, X, AlertTriangle 
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));

export default function SupplyReviewArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  // Status das Fases
  const [isTopDownFechado, setIsTopDownFechado] = useState(false);
  const [isSupplyFechado, setIsSupplyFechado] = useState(false);
  
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);
  
  const [busca, setBusca] = useState('');
  const [categoriaSelecionada, setCategoriaSelecionada] = useState('TODAS');
  const [segmentoSelecionado, setSegmentoSelecionado] = useState('TODOS');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [res, statusRes] = await Promise.all([
        axios.get('/api/v1/consensus/supply'),
        axios.get('/api/v1/consensus/supply/status')
      ]);
      setDadosBrutos(res.data.dados || []);
      setIsTopDownFechado(statusRes.data.is_topdown_fechado);
      setIsSupplyFechado(statusRes.data.is_supply_fechado);
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
        "SEGMENTO": row.segmento, "PMV PONDERADO (R$)": pmvBase
      };
      row.meses.forEach((m: any) => {
        const edicao = celulasEditadas[row.produto]?.[m.mes_banco];
        const volFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : m.vol_ajustado));
        const justFinal = edicao !== undefined ? edicao.justificativa : (m.justificativa || "");
        linha[`${m.mes_str} (Top-Down Ref)`] = Math.round(Number(m.vol_ref));
        linha[`${m.mes_str} (Supply Restrito)`] = volFinal;
        linha[`${m.mes_str} (Justificativa)`] = justFinal;
      });
      return linha;
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Supply_Review");
    XLSX.writeFile(workbook, `SOP_Nexus_Supply_${isSnapshot ? 'CONGELADO' : 'DRAFT'}_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleCongelarCiclo = async () => {
    if (!window.confirm("Atenção: Ao assinar o Supply Review, a meta restrita será passada para a equipe Comercial. Deseja continuar?")) return;
    setIsProcessing(true);
    
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([produto, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
            produto: produto, 
            mes_projetado: mes, 
            novo_volume: val.novo_volume === '' ? 0 : val.novo_volume,
            justificativa: val.justificativa || ""
        });
      });
    });

    try {
      handleExportExcel(true); 
      await axios.post('/api/v1/consensus/supply/congelar', { origem_ajuste: "Supply Review", ajustes });
      alert("🔒 Ciclo de Supply Review congelado com sucesso!");
      setCelulasEditadas({});
      fetchData(); 
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao processar Supply Review."); 
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

  const totaisFaturamento = useMemo(() => {
    const totais: Record<string, number> = {};
    dadosFiltrados.forEach(row => row.meses.forEach((m: any) => totais[m.mes_banco] = 0));
    
    dadosFiltrados.forEach(row => {
      const pmvBase = row.meses[0]?.pmv || 0;
      row.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[row.produto]?.[mes.mes_banco];
        const isPendente = edicao !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicao.novo_volume : mes.vol_ajustado));
        const receitaReal = isPendente ? (volumeFinal * pmvBase) : (mes.receita || 0);
        totais[mes.mes_banco] += receitaReal;
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
            <span className="font-bold text-gray-900 text-sm truncate max-w-[320px]">{info.row.original.descricao}</span>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] text-slate-400 font-black uppercase tracking-tighter bg-slate-100 px-2 py-0.5 rounded-md">{info.row.original.produto}</span>
              <span className="text-[10px] text-indigo-400 font-black uppercase tracking-tighter">{info.row.original.categoria}</span>
            </div>
          </div>
        )
      },
      {
        id: 'pmv_base', header: 'PMV Base',
        accessorFn: (row: any) => row.meses[0]?.pmv || 0,
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
          const justReal = isPendente ? edicao.justificativa : (dadosMes?.justificativa || "");
          
          const valorInteiro = Math.round(Number(valorReal));
          const baseTD = Math.round(Number(dadosMes?.vol_ref || 0)); // A Base agora é o Top-Down!
          const isChanged = valorInteiro !== baseTD;
          const isCorte = valorInteiro < baseTD;
          
          const pmvBase = row.meses[0]?.pmv || 0;
          const faturamentoPrevisto = isPendente ? (valorInteiro * pmvBase) : (dadosMes?.receita || 0);

          return (
            <div className="flex flex-col w-32 gap-1.5 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest">Base TD: {baseTD.toLocaleString('pt-BR')}</span>
              </div>
              
              <input
                type="number" disabled={isSupplyFechado || !isTopDownFechado} value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                onChange={(e) => meta.updateCell(row.produto, m.mes_banco, e.target.value, justReal)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${isSupplyFechado || !isTopDownFechado ? 'cursor-not-allowed opacity-50 bg-slate-100 border-gray-200' : isCorte ? 'bg-rose-50 border-rose-300 text-rose-900 shadow-sm' : isChanged ? 'bg-slate-800 border-slate-700 text-white shadow-md' : 'bg-gray-50 border-transparent text-gray-800 focus:bg-white focus:border-indigo-300'}`}
              />
              
              <input
                type="text" disabled={isSupplyFechado || !isTopDownFechado} value={justReal} placeholder={isCorte ? "Justifique o corte..." : "Justificativa..."}
                onChange={(e) => meta.updateCell(row.produto, m.mes_banco, valorReal, e.target.value)}
                className={`text-[9px] font-bold p-1.5 rounded-lg outline-none border transition-all w-full placeholder:text-slate-300
                  ${isSupplyFechado || !isTopDownFechado ? 'cursor-not-allowed opacity-50 bg-slate-50 border-slate-100' : isCorte && !justReal ? 'bg-rose-50 border-rose-200 focus:border-rose-400 text-rose-800' : 'bg-white border-slate-200 focus:border-indigo-300 text-slate-600'}`}
              />

              <span className="text-[10px] font-black text-emerald-600 text-center tracking-tight mt-1">
                {formatMoeda(faturamentoPrevisto)}
              </span>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, isSupplyFechado, isTopDownFechado]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (chave: string, mes: string, valVol: any, valJust: string) => {
        if (isSupplyFechado || !isTopDownFechado) return;
        const v = valVol === '' ? '' : Math.round(Number(valVol));
        const finalV = Number.isNaN(v as any) && valVol !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ 
            ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV, justificativa: valJust } } 
        }));
      }
    }
  });

  if (isLoading) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Iniciando Supply Review...</span>
      </div>
    );
  }

  // TELA DE BLOQUEIO SE O TOP-DOWN NÃO ESTIVER PRONTO
  if (!isTopDownFechado) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans p-6">
        <div className="bg-white p-12 rounded-[40px] shadow-xl border border-slate-100 flex flex-col items-center max-w-lg text-center">
          <div className="w-20 h-20 bg-amber-50 rounded-full flex items-center justify-center mb-6 border border-amber-100">
            <AlertTriangle className="w-10 h-10 text-amber-500" />
          </div>
          <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-4">Aguardando Diretoria (Top-Down)</h2>
          <p className="text-slate-500 font-medium leading-relaxed">
            A fase de <strong>Supply Review</strong> só pode ser iniciada após a aprovação e congelamento da demanda comercial pelo nível Gerencial (Fase 2).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100">
          <div>
              <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
                <Factory className="w-8 h-8 text-slate-800" /> SUPPLY REVIEW (RESTRIÇÃO)
              </h1>
              <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">Ajuste de Capacidade e Trade-offs de Produção</p>
          </div>
          <div className="flex items-center gap-4">
            {isSupplyFechado ? (
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2 bg-slate-800 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-slate-800/20">
                      <Check className="w-5 h-5 text-emerald-400" /> Supply Review Congelado
                  </div>
                </div>
            ) : (
                <div className="flex items-center gap-3 h-full">
                  {qtdEdicoes > 0 && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                  <button onClick={handleCongelarCiclo} disabled={isProcessing} className="flex items-center gap-2 bg-slate-900 hover:bg-black text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-slate-900/30 transition-all disabled:opacity-50">
                      {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : qtdEdicoes > 0 ? 'Gravar Restrições' : <ShieldCheck className="w-5 h-5" />}
                      {qtdEdicoes > 0 ? '' : 'Aprovar Plano Irrestrito'}
                  </button>
                </div>
            )}
          </div>
        </div>

        {/* BARRA DE FILTROS */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
           <div className="bg-emerald-600 text-white px-8 py-4 rounded-[24px] flex items-center gap-4 shadow-lg shadow-emerald-600/20 flex-shrink-0">
             <div className="bg-emerald-500 p-2 rounded-full"><Activity className="w-6 h-6 text-white" /></div>
             <div>
               <p className="text-[10px] font-black uppercase tracking-widest text-emerald-200 mb-0.5">Skus Avaliados</p>
               <p className="text-2xl font-black leading-none">{dadosFiltrados.length}</p>
             </div>
           </div>

           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex flex-col md:flex-row items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Buscar por SKU..." value={busca} onChange={e => setBusca(e.target.value)}
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
             <button onClick={() => handleExportExcel(false)} className="flex items-center gap-2 bg-slate-100 hover:bg-emerald-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 ml-auto whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS E GRÁFICOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative">
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
                  <React.Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-slate-50/60' : 'hover:bg-slate-50/30'}`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-4 align-top">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {row.getIsExpanded() && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-50/50 p-8 border-b border-slate-100">
                          <div className="bg-white rounded-[40px] p-8 shadow-inner border border-slate-100 animate-in fade-in duration-500">
                            
                            <div className="flex justify-between items-start mb-6 px-2">
                               <div className="flex flex-col gap-3">
                                 <h3 className="text-lg font-black text-slate-800 uppercase tracking-tighter">
                                   Curva Consolidada Brasil (Real x Supply)
                                 </h3>
                               </div>
                            </div>

                            <div className="h-[250px] w-full -ml-4">
                              {loadingGrafico === row.original.produto ? (
                                <div className="h-full flex items-center justify-center text-slate-800"><Loader2 className="animate-spin w-8 h-8" /></div>
                              ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart data={
                                      (dadosGraficoCache[row.original.produto] || []).map((p: any) => {
                                          const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                          const edicao = celulasEditadas[row.original.produto]?.[p.data_iso];
                                          const valSupply = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                          return { ...p, Consenso: valSupply !== null ? valSupply : p.Consenso };
                                      })
                                  }>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10}} axisLine={false} tickLine={false} />
                                    <Tooltip contentStyle={{borderRadius: '20px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900'}} />
                                    
                                    <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={4} dot={{r: 3, fill: '#0f172a'}} connectNulls={false} />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                    <Line type="monotone" dataKey="Consenso" name="Meta Restrita (Supply)" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#fff'}} connectNulls={false} />
                                  </LineChart>
                                </ResponsiveContainer>
                              )}
                            </div>
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
                    if (header.id === 'expander' || header.id === 'pmv_base') return <td key={header.id} className="px-8 py-5"></td>;
                    if (header.id === 'info') return (
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Receita Consolidada</span>
                          <span className="font-bold text-sm text-white">TOTAL NA TELA (R$)</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="bg-slate-800/80 inline-block px-3 py-1.5 rounded-xl border border-slate-700/50">
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight">{formatMoeda(totaisFaturamento[mesBanco] || 0)}</span>
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