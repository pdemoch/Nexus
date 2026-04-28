import React, { useState, useMemo, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  Activity, AlertCircle, ArrowUpDown, DollarSign, Loader2, Factory,
  Package, RefreshCcw, Search, TrendingUp, TrendingDown, ChevronDown, ChevronRight, 
  Users, LayoutGrid, Target, UserCheck, UserMinus
} from 'lucide-react';
import { 
  useReactTable, getCoreRowModel, flexRender, getSortedRowModel, 
  SortingState, ColumnFiltersState, getFilteredRowModel, getExpandedRowModel,
  ExpandedState
} from '@tanstack/react-table';

// --- HELPERS DE FORMATAÇÃO ---
const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function SoeDashboard() {
  const [viewMode, setViewMode] = useState<'sku' | 'comercial'>('sku');
  const [dados, setDados] = useState<any[]>([]);
  const [oportunidades, setOportunidades] = useState<any[]>([]);
  const [metaGlobal, setMetaGlobal] = useState<any>({ dia: new Date().getDate(), total: 0 });
  const [isLoading, setIsLoading] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [expanded, setExpanded] = useState<ExpandedState>({});

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [resRadar, resOportunidades] = await Promise.all([
        axios.get('https://api.lineanexus.com.br/api/v1/soe/radar'),
        axios.get('https://api.lineanexus.com.br/api/v1/soe/oportunidades')
      ]);
      setDados(resRadar.data.dados || []);
      setOportunidades(resOportunidades.data || []);
      setMetaGlobal({ dia: resRadar.data.dia || new Date().getDate(), total: resRadar.data.dados?.length || 0 });
    } catch (e) {
      console.error("Erro ao carregar War Room S&OE", e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSyncStock = async () => {
    setIsSyncing(true);
    try {
      await axios.post('https://api.lineanexus.com.br/api/v1/soe/sync-stock');
      await fetchData();
    } catch (e) {
      alert("Erro ao sincronizar estoque.");
    } finally {
      setIsSyncing(false);
    }
  };

  // --- ESTATÍSTICAS DOS CARDS ---
  const stats = useMemo(() => {
    const emRisco = dados.filter(d => d.logistica.saldo_projetado < 0);
    const fatTotalRisco = emRisco.reduce((acc, curr) => acc + curr.logistica.fat_em_risco, 0);
    const pacingMedioVendas = dados.reduce((acc, curr) => acc + (curr.execucao.pedidos_qtd / (curr.metas.vol_sop || 1) * 100), 0) / (dados.length || 1);
    const pacingMedioProd = dados.reduce((acc, curr) => acc + curr.execucao.pacing, 0) / (dados.length || 1);

    return { countRisco: emRisco.length, fatTotalRisco, pacingMedioVendas, pacingMedioProd };
  }, [dados]);

  // --- COLUNAS: VISÃO SKU ---
  const columnsSku = useMemo(() => [
    {
      id: 'expander',
      header: () => null,
      cell: ({ row }: any) => (
        <button onClick={row.getToggleExpandedHandler()} className="p-1 rounded-full hover:bg-indigo-100 text-indigo-600 transition-colors">
          {row.getIsExpanded() ? <ChevronDown className="w-5 h-5"/> : <ChevronRight className="w-5 h-5"/>}
        </button>
      ),
    },
    {
      accessorKey: 'curva',
      header: 'Curva',
      cell: (info: any) => (
        <span className={`px-2 py-1 rounded text-[10px] font-black ${info.getValue() === 'AA' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
          {info.getValue()}
        </span>
      )
    },
    {
      accessorKey: 'sku',
      header: 'SKU / Descrição',
      cell: (info: any) => (
        <div className="flex flex-col max-w-[220px]">
          <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">{info.row.original.sku}</span>
          <span className="text-xs font-bold text-slate-700 truncate">{info.row.original.descricao}</span>
        </div>
      )
    },
    {
      accessorKey: 'metas.vol_sop',
      header: 'Meta S&OP',
      cell: (info: any) => <span className="font-bold text-slate-600">{formatVolume(info.getValue())}</span>
    },
    {
      id: 'pacing_vendas',
      header: 'Ating. Vendas',
      cell: (info: any) => {
        const val = (info.row.original.execucao.pedidos_qtd / (info.row.original.metas.vol_sop || 1)) * 100;
        const esperado = (metaGlobal?.dia / 30) * 100;
        return (
          <div className="flex flex-col gap-1 w-28">
            <div className="flex justify-between text-[9px] font-black">
              <span className={val > esperado ? 'text-indigo-600' : 'text-slate-400'}>{val.toFixed(1)}%</span>
              <span className="text-slate-300">Meta: {Math.round(esperado)}%</span>
            </div>
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div className={`h-full transition-all ${val > esperado ? 'bg-indigo-500' : 'bg-slate-400'}`} style={{ width: `${Math.min(val, 100)}%` }} />
            </div>
          </div>
        );
      }
    },
    {
      accessorKey: 'logistica.estoque_d0',
      header: 'Estoque D0',
      cell: (info: any) => <span className="font-bold text-slate-800">{formatVolume(info.getValue())}</span>
    },
    {
      accessorKey: 'logistica.saldo_projetado',
      header: 'Saldo (S&OE)',
      cell: (info: any) => {
        const val = info.getValue();
        return (
          <div className={`flex items-center gap-1.5 font-black ${val < 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
            {val < 0 ? <TrendingDown className="w-3 h-3"/> : <TrendingUp className="w-3 h-3"/>}
            {formatVolume(val)}
          </div>
        );
      }
    },
    {
      accessorKey: 'logistica.fat_em_risco',
      header: 'Vlr em Risco',
      cell: (info: any) => (
        <span className={`font-black ${info.getValue() > 0 ? 'text-rose-600 bg-rose-50 px-2 py-1 rounded-lg' : 'text-slate-300'}`}>
          {info.getValue() > 0 ? formatMoeda(info.getValue()) : '-'}
        </span>
      )
    }
  ], [metaGlobal]);

  const table = useReactTable({
    data: dados, columns: columnsSku, state: { sorting, columnFilters, expanded },
    onExpandedChange: setExpanded, getExpandedRowModel: getExpandedRowModel(),
    onSortingChange: setSorting, onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(),
  });

  if (isLoading) return <div className="h-screen w-full flex flex-col items-center justify-center bg-slate-50"><Loader2 className="w-10 h-10 animate-spin text-indigo-600 mb-4"/><span className="text-slate-400 font-black text-xs uppercase tracking-widest">Sincronizando War Room 2.0...</span></div>;

  return (
    <div className="w-full bg-slate-50 min-h-screen pb-20 font-sans">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12">
        
        {/* HEADER */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-10 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100">
          <div className="flex items-center gap-6">
            <div className="bg-indigo-600 p-4 rounded-3xl shadow-lg shadow-indigo-200">
              <Activity className="w-8 h-8 text-white" />
            </div>
            <div>
              <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">War Room S&OE <span className="text-indigo-600">.</span></h1>
              <p className="text-slate-500 font-bold text-sm">Monitoramento de Execução e Churn Comercial</p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            {/* TOGGLE VISUALIZAÇÃO */}
            <div className="bg-slate-100 p-1.5 rounded-2xl flex gap-1 mr-4">
               <button 
                onClick={() => setViewMode('sku')}
                className={`flex items-center gap-2 px-6 py-3 rounded-xl text-[10px] font-black uppercase transition-all ${viewMode === 'sku' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
               >
                 <LayoutGrid className="w-4 h-4"/> Visão SKU
               </button>
               <button 
                onClick={() => setViewMode('comercial')}
                className={`flex items-center gap-2 px-6 py-3 rounded-xl text-[10px] font-black uppercase transition-all ${viewMode === 'comercial' ? 'bg-white text-orange-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
               >
                 <Users className="w-4 h-4"/> Visão Comercial
               </button>
            </div>

            <button onClick={handleSyncStock} disabled={isSyncing} className="flex items-center gap-2 bg-slate-900 hover:bg-black text-white px-8 py-4 rounded-2xl text-xs font-black uppercase tracking-widest transition-all shadow-xl shadow-slate-200">
              {isSyncing ? <Loader2 className="w-4 h-4 animate-spin"/> : <RefreshCcw className="w-4 h-4" />}
              {isSyncing ? 'Sincronizando...' : 'Sincronizar D0'}
            </button>
          </div>
        </div>

        {/* CARDS DE RESUMO (Sempre Visíveis) */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
          <div className="bg-white p-8 rounded-[40px] border border-slate-100 shadow-sm">
             <div className="flex justify-between items-start mb-4"><AlertCircle className="w-6 h-6 text-rose-500" /><span className="text-[10px] font-black text-rose-500 bg-rose-50 px-2 py-1 rounded-lg">CRÍTICO</span></div>
             <h3 className="text-3xl font-black text-slate-900">{stats.countRisco} <span className="text-xs text-slate-400 uppercase tracking-widest ml-1">SKUS</span></h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Com Risco de Ruptura</p>
          </div>
          
          <div className="bg-white p-8 rounded-[40px] border-2 border-rose-100 shadow-sm">
             <div className="flex justify-between items-start mb-4"><DollarSign className="w-6 h-6 text-rose-600" /><span className="text-[10px] font-black text-rose-600 uppercase tracking-widest">Prejuízo Potencial</span></div>
             <h3 className="text-3xl font-black text-rose-600">{formatMoeda(stats.fatTotalRisco)}</h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Faturamento em Risco D0</p>
          </div>

          <div className="bg-white p-8 rounded-[40px] border border-slate-100 shadow-sm">
             <div className="flex justify-between items-start mb-4"><Target className="w-6 h-6 text-indigo-500" /><span className="text-[10px] font-black text-indigo-500 uppercase tracking-widest">Execução Vendas</span></div>
             <h3 className="text-3xl font-black text-slate-900">{Math.round(stats.pacingMedioVendas)}%</h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Atingimento Médio</p>
          </div>

          <div className="bg-white p-8 rounded-[40px] border border-slate-100 shadow-sm">
             <div className="flex justify-between items-start mb-4"><Factory className="w-6 h-6 text-emerald-500" /><span className="text-[10px] font-black text-emerald-500 uppercase tracking-widest">Atendimento Fábrica</span></div>
             <h3 className="text-3xl font-black text-slate-900">{Math.round(stats.pacingMedioProd)}%</h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Faturado vs Meta S&OP</p>
          </div>
        </div>

        {/* CONTEÚDO DINÂMICO (TOGGLE) */}
        {viewMode === 'sku' ? (
          <div className="bg-white rounded-[40px] shadow-sm border border-slate-100 overflow-hidden">
            <div className="p-8 border-b border-slate-50 flex flex-col md:flex-row justify-between items-center gap-4">
              <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3"><Package className="w-5 h-5 text-indigo-600"/> Pilha de Atendimento (Mês Atual)</h3>
              <div className="relative w-full md:w-96">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input type="text" placeholder="Filtrar por SKU ou Descrição..." className="w-full bg-slate-50 border-none pl-12 pr-6 py-4 rounded-2xl text-sm font-bold focus:ring-2 focus:ring-indigo-500 outline-none transition-all" value={(table.getColumn('sku')?.getFilterValue() as string) ?? ''} onChange={e => table.getColumn('sku')?.setFilterValue(e.target.value)} />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  {table.getHeaderGroups().map(hg => (
                    <tr key={hg.id} className="bg-slate-50/50">
                      {hg.headers.map(h => (
                        <th key={h.id} className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest cursor-pointer hover:text-indigo-600 transition-colors" onClick={h.column.getToggleSortingHandler()}>
                          <div className="flex items-center gap-2">{flexRender(h.column.columnDef.header, h.getContext())}<ArrowUpDown className="w-3 h-3" /></div>
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {table.getRowModel().rows.map(row => (
                    <React.Fragment key={row.id}>
                      <tr className="hover:bg-slate-50/80 transition-colors border-b border-slate-50 group">
                        {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-6 py-5">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                      </tr>
                      {/* --- DRILL-DOWN DO SKU --- */}
                      {row.getIsExpanded() && (
                        <tr className="bg-indigo-50/20 border-b border-indigo-100">
                          <td colSpan={columnsSku.length} className="px-14 py-8">
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
                               <div className="bg-white p-6 rounded-3xl shadow-sm border border-indigo-50">
                                  <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2"><DollarSign className="w-4 h-4 text-emerald-500"/> Financeiro do Item</h4>
                                  <div className="space-y-3">
                                     <div className="flex justify-between border-b border-slate-50 pb-2"><span className="text-xs font-bold text-slate-500">Meta Planejada:</span> <span className="text-xs font-black text-slate-800">{formatMoeda(row.original.metas.val_meta)}</span></div>
                                     <div className="flex justify-between border-b border-slate-50 pb-2"><span className="text-xs font-bold text-slate-500">Realizado (Faturado):</span> <span className="text-xs font-black text-emerald-600">{formatMoeda(row.original.execucao.faturado_val)}</span></div>
                                     <div className="flex justify-between"><span className="text-xs font-bold text-slate-500">Corte Comercial:</span> <span className="text-xs font-black text-rose-500">{formatMoeda(row.original.execucao.corte_val)}</span></div>
                                  </div>
                               </div>
                               <div className="bg-white p-6 rounded-3xl shadow-sm border border-indigo-50">
                                  <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2"><Activity className="w-4 h-4 text-indigo-500"/> Funil de Atendimento (Cx)</h4>
                                  <div className="space-y-3">
                                     <div className="flex justify-between border-b border-slate-50 pb-2"><span className="text-xs font-bold text-slate-500">Pedidos Recebidos:</span> <span className="text-xs font-black text-slate-800">{formatVolume(row.original.execucao.pedidos_qtd)}</span></div>
                                     <div className="flex justify-between border-b border-slate-50 pb-2"><span className="text-xs font-bold text-slate-500">Backlog (Em aberto):</span> <span className="text-xs font-black text-indigo-600">{formatVolume(row.original.logistica.backlog_total)}</span></div>
                                     <div className="flex justify-between"><span className="text-xs font-bold text-slate-500">PMV de Venda (Média):</span> <span className="text-xs font-black text-slate-400">{formatMoeda(row.original.pmv)}</span></div>
                                  </div>
                               </div>
                            </div>
                            
                            {/* LISTA DE CLIENTES DO SKU */}
                            <div className="bg-white rounded-3xl border border-slate-100 overflow-hidden shadow-sm">
                               <div className="bg-slate-50 px-6 py-4 border-b border-slate-100 flex justify-between items-center">
                                  <h5 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center gap-2"><UserMinus className="w-4 h-4 text-rose-500"/> Clientes Pendentes para este SKU</h5>
                                  <span className="text-[9px] font-black bg-rose-50 text-rose-600 px-2 py-1 rounded-lg">Ação Necessária: {row.original.clientes_pendentes.length}</span>
                               </div>
                               <div className="max-h-60 overflow-y-auto">
                                  <table className="w-full text-left text-xs">
                                     <thead className="sticky top-0 bg-white">
                                        <tr className="text-[9px] font-black text-slate-400 uppercase border-b border-slate-50">
                                           <th className="px-6 py-3">Cliente</th>
                                           <th className="px-6 py-3">Regional / Vendedor</th>
                                           <th className="px-6 py-3 text-right">Meta Cx</th>
                                           <th className="px-6 py-3 text-right">Pedido Cx</th>
                                           <th className="px-6 py-3 text-right">Gap (R$)</th>
                                        </tr>
                                     </thead>
                                     <tbody>
                                        {row.original.clientes_pendentes.map((c: any, i: number) => (
                                           <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                                              <td className="px-6 py-3 font-bold text-slate-700">{c.cliente}</td>
                                              <td className="px-6 py-3 text-slate-400 font-medium">{c.regional} <span className="mx-1">•</span> {c.vendedor}</td>
                                              <td className="px-6 py-3 text-right font-bold">{c.meta_vol}</td>
                                              <td className="px-6 py-3 text-right font-bold text-indigo-600">{c.pedidos_qtd}</td>
                                              <td className="px-6 py-3 text-right font-black text-rose-500">{formatMoeda(c.gap_financeiro)}</td>
                                           </tr>
                                        ))}
                                     </tbody>
                                  </table>
                               </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          /* --- VISÃO COMERCIAL (CHURN / TAXA DE COMPRA) --- */
          <div className="bg-white rounded-[40px] shadow-sm border border-slate-100 overflow-hidden">
             <div className="p-8 border-b border-slate-50 flex justify-between items-center bg-orange-50/30">
                <div>
                  <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3"><UserMinus className="w-5 h-5 text-orange-500"/> Radar de Churn (S/ Compra no Mês)</h3>
                  <p className="text-xs font-bold text-slate-500 mt-1">Clientes consolidados por Razão Social que ainda não emitiram pedidos.</p>
                </div>
                <div className="bg-white px-6 py-3 rounded-2xl border border-orange-100 shadow-sm flex flex-col items-end">
                   <span className="text-[10px] font-black text-orange-600 uppercase tracking-widest">Total em Risco</span>
                   <span className="text-xl font-black text-slate-900">{formatMoeda(oportunidades.reduce((acc, curr) => acc + curr.valor_estimado, 0))}</span>
                </div>
             </div>
             
             <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                   <thead>
                      <tr className="bg-slate-50/50 border-b border-slate-100">
                         <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest">Cliente (Razão Social)</th>
                         <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">Regional</th>
                         <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-right">Meta Mês (Cx)</th>
                         <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-right">Perda Estimada (R$)</th>
                         <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">Dias sem Pedido</th>
                         <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">Status Comercial</th>
                      </tr>
                   </thead>
                   <tbody>
                      {oportunidades.map((op, idx) => (
                         <tr key={idx} className="hover:bg-orange-50/30 transition-colors border-b border-slate-50">
                            <td className="px-8 py-5 font-black text-slate-700 text-sm">{op.cliente}</td>
                            <td className="px-8 py-5 text-center font-bold text-slate-400 text-xs">{op.regional}</td>
                            <td className="px-8 py-5 text-right font-black text-slate-700 text-sm">{formatVolume(op.meta_vol)} Cx</td>
                            <td className="px-8 py-5 text-right font-black text-rose-500 text-sm">{formatMoeda(op.valor_estimado)}</td>
                            <td className="px-8 py-5 text-center">
                               <div className="flex flex-col items-center">
                                  <span className="text-xs font-black text-slate-800">{op.dias_sem_compra}</span>
                                  <span className="text-[9px] font-black text-slate-300 uppercase tracking-tighter">Dias</span>
                               </div>
                            </td>
                            <td className="px-8 py-5 text-center">
                               <span className={`px-4 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest ${op.status === 'Crítico' ? 'bg-rose-100 text-rose-700' : 'bg-orange-100 text-orange-700'}`}>
                                  {op.status}
                               </span>
                            </td>
                         </tr>
                      ))}
                      {oportunidades.length === 0 && (
                        <tr><td colSpan={6} className="px-8 py-20 text-center font-black text-emerald-500 uppercase tracking-widest text-xs">Parabéns! Todos os clientes com meta já realizaram pedidos este mês.</td></tr>
                      )}
                   </tbody>
                </table>
             </div>
          </div>
        )}
      </div>
    </div>
  );
}