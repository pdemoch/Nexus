import React, { useState, useMemo, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  Activity, AlertCircle, ArrowUpDown, DollarSign, Loader2, Factory,
  Package, RefreshCcw, Search, TrendingUp, TrendingDown, ChevronDown, ChevronRight, UserMinus
} from 'lucide-react';
import { 
  useReactTable, getCoreRowModel, flexRender, getSortedRowModel, 
  SortingState, ColumnFiltersState, getFilteredRowModel, getExpandedRowModel 
} from '@tanstack/react-table';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function SoeDashboard() {
  const [dados, setDados] = useState<any[]>([]);
  const [oportunidades, setOportunidades] = useState<any[]>([]);
  const [metaGlobal, setMetaGlobal] = useState<any>({ dia: new Date().getDate(), total: 0 });
  const [isLoading, setIsLoading] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [resRadar, resOportunidades] = await Promise.all([
        axios.get('https://api.lineanexus.com.br/api/v1/soe/radar'),
        axios.get('https://api.lineanexus.com.br/api/v1/soe/oportunidades')
      ]);
      setDados(resRadar.data.dados || []);
      setOportunidades(resOportunidades.data || []);
      setMetaGlobal({ dia: new Date().getDate(), total: resRadar.data.dados?.length || 0 });
    } catch (e) {
      console.error("Erro ao carregar War Room S&OE", e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleSyncStock = async () => {
    setIsSyncing(true);
    try {
      await axios.post('https://api.lineanexus.com.br/api/v1/soe/sync-stock');
      await fetchData();
    } catch (e) {
      alert("Erro ao sincronizar estoque com a API 90.");
    } finally {
      setIsSyncing(false);
    }
  };

  useEffect(() => { fetchData(); }, [fetchData]);

  // --- CÁLCULO DOS CARDS (STATS) ---
  const stats = useMemo(() => {
    const emRisco = dados.filter(d => d.logistica.saldo_projetado < 0);
    const fatTotalRisco = emRisco.reduce((acc, curr) => acc + curr.logistica.fat_em_risco, 0);
    
    // Pacing Vendas = Pedidos / Meta S&OP
    const pacingMedioVendas = dados.reduce((acc, curr) => {
      const vol_sop = curr.metas.vol_sop || 1;
      return acc + ((curr.execucao.pedidos_qtd / vol_sop) * 100);
    }, 0) / (dados.length || 1);
    
    // Pacing Produção = Faturado / Meta S&OP (que vem do backend como pacing)
    const pacingMedioProd = dados.reduce((acc, curr) => acc + curr.execucao.pacing, 0) / (dados.length || 1);

    return { countRisco: emRisco.length, fatTotalRisco, pacingMedioVendas, pacingMedioProd };
  }, [dados]);

  // --- COLUNAS DA TABELA PRINCIPAL ---
  const columns = useMemo(() => [
    {
      id: 'expander',
      header: () => null,
      cell: ({ row }: any) => (
        <button 
          onClick={row.getToggleExpandedHandler()} 
          className="p-1 rounded-full hover:bg-indigo-100 text-indigo-600 transition-colors"
        >
          {row.getIsExpanded() ? <ChevronDown className="w-5 h-5"/> : <ChevronRight className="w-5 h-5"/>}
        </button>
      ),
    },
    {
      accessorKey: 'curva',
      header: 'Curva',
      cell: (info: any) => (
        <span className={`px-2 py-1 rounded text-[10px] font-black ${
          info.getValue() === 'AA' ? 'bg-indigo-100 text-indigo-700' : 
          info.getValue() === 'AB' ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500'
        }`}>
          {info.getValue()}
        </span>
      )
    },
    {
      accessorKey: 'sku',
      header: 'SKU / Descrição',
      cell: (info: any) => (
        <div className="flex flex-col max-w-[200px]">
          <span className="text-[10px] font-black text-slate-400 uppercase tracking-tighter">{info.row.original.sku}</span>
          <span className="text-xs font-bold text-slate-700 truncate" title={info.row.original.descricao}>{info.row.original.descricao}</span>
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
      accessorFn: (row: any) => (row.execucao.pedidos_qtd / (row.metas.vol_sop || 1)) * 100,
      cell: (info: any) => {
        const val = info.getValue();
        const esperado = (metaGlobal?.dia / 30) * 100;
        return (
          <div className="flex flex-col gap-1 w-32">
            <div className="flex justify-between text-[9px] font-black">
              <span className={val > esperado ? 'text-indigo-600' : 'text-slate-400'}>{val.toFixed(1)}%</span>
              <span className="text-slate-300">Meta: {Math.round(esperado)}%</span>
            </div>
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div 
                className={`h-full transition-all ${val > esperado ? 'bg-indigo-500' : 'bg-slate-400'}`} 
                style={{ width: `${Math.min(val, 100)}%` }}
              />
            </div>
          </div>
        );
      }
    },
    {
      accessorKey: 'execucao.pacing',
      header: 'Ating. Produção',
      cell: (info: any) => {
        const val = info.getValue();
        const vendas = (info.row.original.execucao.pedidos_qtd / (info.row.original.metas.vol_sop || 1)) * 100;
        return (
          <div className="flex flex-col gap-1 w-32">
            <div className="flex justify-between text-[9px] font-black">
              <span className={val < vendas ? 'text-rose-500' : 'text-emerald-500'}>{val.toFixed(1)}%</span>
            </div>
            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div 
                className={`h-full transition-all ${val < vendas ? 'bg-rose-500' : 'bg-emerald-500'}`} 
                style={{ width: `${Math.min(val, 100)}%` }}
              />
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
      header: 'Fat. em Risco',
      cell: (info: any) => (
        <span className={`font-black ${info.getValue() > 0 ? 'text-rose-600 bg-rose-50 px-2 py-1 rounded-lg' : 'text-slate-300'}`}>
          {info.getValue() > 0 ? formatMoeda(info.getValue()) : '-'}
        </span>
      )
    }
  ], [metaGlobal]);

  const table = useReactTable({
    data: dados, columns, state: { sorting, columnFilters },
    onSortingChange: setSorting, onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(), getExpandedRowModel: getExpandedRowModel()
  });

  if (isLoading) return <div className="h-screen w-full flex flex-col items-center justify-center bg-slate-50"><Loader2 className="w-10 h-10 animate-spin text-indigo-600 mb-4"/><span className="text-slate-400 font-black text-xs uppercase tracking-widest">Sincronizando War Room...</span></div>;

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
              <h1 className="text-3xl font-black text-slate-900 tracking-tighter">RADAR S&OE <span className="text-indigo-600">.</span></h1>
              <p className="text-slate-500 font-bold text-sm">Monitoramento de Atendimento e Ruptura em Tempo Real</p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex flex-col items-end mr-4">
              <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Status do Dia</span>
              <span className="text-sm font-black text-slate-700">DIA {metaGlobal?.dia} DE 30</span>
            </div>
            <button 
              onClick={handleSyncStock} disabled={isSyncing}
              className="flex items-center gap-2 bg-slate-900 hover:bg-black text-white px-8 py-4 rounded-2xl text-xs font-black uppercase tracking-widest transition-all shadow-xl shadow-slate-200"
            >
              {isSyncing ? <Loader2 className="w-4 h-4 animate-spin"/> : <RefreshCcw className="w-4 h-4" />}
              {isSyncing ? 'Sincronizando...' : 'Atualizar Estoque API 90'}
            </button>
          </div>
        </div>

        {/* CARDS DE RESUMO */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
          <div className="bg-white p-8 rounded-[40px] border border-slate-100 shadow-sm">
             <div className="flex justify-between items-start mb-4">
                <AlertCircle className="w-6 h-6 text-rose-500" />
                <span className="text-[10px] font-black text-rose-500 bg-rose-50 px-2 py-1 rounded-lg">CRÍTICO</span>
             </div>
             <h3 className="text-3xl font-black text-slate-900">{stats.countRisco} <span className="text-xs text-slate-400">SKUS</span></h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Com Risco de Ruptura</p>
          </div>
          
          <div className="bg-white p-8 rounded-[40px] border-2 border-rose-100 shadow-sm">
             <div className="flex justify-between items-start mb-4">
                <DollarSign className="w-6 h-6 text-rose-600" />
                <span className="text-[10px] font-black text-rose-600 uppercase tracking-widest">Faturamento</span>
             </div>
             <h3 className="text-3xl font-black text-rose-600">{formatMoeda(stats.fatTotalRisco)}</h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Em Risco de Corte no Mês</p>
          </div>

          <div className="bg-white p-8 rounded-[40px] border border-slate-100 shadow-sm">
             <div className="flex justify-between items-start mb-4">
                <TrendingUp className="w-6 h-6 text-indigo-500" />
                <span className="text-[10px] font-black text-indigo-500 uppercase tracking-widest">Ritmo Comercial</span>
             </div>
             <h3 className="text-3xl font-black text-slate-900">{Math.round(stats.pacingMedioVendas)}%</h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Atingimento de Vendas</p>
          </div>

          <div className="bg-white p-8 rounded-[40px] border border-slate-100 shadow-sm">
             <div className="flex justify-between items-start mb-4">
                <Factory className="w-6 h-6 text-emerald-500" />
                <span className="text-[10px] font-black text-emerald-500 uppercase tracking-widest">Ritmo Fábrica</span>
             </div>
             <h3 className="text-3xl font-black text-slate-900">{Math.round(stats.pacingMedioProd)}%</h3>
             <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">Entrega de Produção</p>
          </div>
        </div>

        {/* --- TABELA PRINCIPAL (RADAR) --- */}
        <div className="bg-white rounded-[40px] shadow-sm border border-slate-100 overflow-hidden mb-10">
          <div className="p-8 border-b border-slate-50 flex flex-col md:flex-row justify-between items-center gap-4">
            <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3">
              <Package className="w-5 h-5 text-indigo-600"/> Pilha de Atendimento (Mês Atual)
            </h3>
            <div className="relative w-full md:w-96">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <input 
                type="text" placeholder="Filtrar por SKU ou Descrição..."
                value={(table.getColumn('sku')?.getFilterValue() as string) ?? ''}
                onChange={e => table.getColumn('sku')?.setFilterValue(e.target.value)}
                className="w-full bg-slate-50 border-none pl-12 pr-6 py-4 rounded-2xl text-sm font-bold focus:ring-2 focus:ring-indigo-500 transition-all outline-none"
              />
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id} className="bg-slate-50/50">
                    {hg.headers.map(h => (
                      <th 
                        key={h.id} 
                        className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest cursor-pointer hover:text-indigo-600 transition-colors"
                        onClick={h.column.getToggleSortingHandler()}
                      >
                        <div className="flex items-center gap-2">
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {h.column.getCanSort() && <ArrowUpDown className="w-3 h-3" />}
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <React.Fragment key={row.id}>
                    <tr className="hover:bg-slate-50/80 transition-colors border-b border-slate-50 group">
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-6 py-5">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {/* --- EXPANSÃO DA LINHA (DRILL-DOWN) --- */}
                    {row.getIsExpanded() && (
                      <tr className="bg-indigo-50/30 border-b border-indigo-100">
                        <td colSpan={columns.length} className="px-14 py-6">
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <div className="bg-white p-5 rounded-2xl shadow-sm border border-indigo-50 flex flex-col gap-2">
                              <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2">
                                <DollarSign className="w-4 h-4 text-emerald-500"/> Financeiro
                              </h4>
                              <div className="flex justify-between"><span className="text-sm font-bold text-slate-500">Meta:</span> <span className="text-sm font-black text-slate-800">{formatMoeda(row.original.metas.val_meta)}</span></div>
                              <div className="flex justify-between"><span className="text-sm font-bold text-slate-500">Faturado:</span> <span className="text-sm font-black text-emerald-600">{formatMoeda(row.original.execucao.faturado_val)}</span></div>
                              <div className="flex justify-between"><span className="text-sm font-bold text-slate-500">Corte R$:</span> <span className="text-sm font-black text-rose-500">{formatMoeda(row.original.execucao.corte_val)}</span></div>
                            </div>
                            
                            <div className="bg-white p-5 rounded-2xl shadow-sm border border-indigo-50 flex flex-col gap-2">
                              <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2">
                                <Activity className="w-4 h-4 text-indigo-500"/> Funil (Caixas)
                              </h4>
                              <div className="flex justify-between"><span className="text-sm font-bold text-slate-500">Pedidos Inseridos:</span> <span className="text-sm font-black text-slate-800">{formatVolume(row.original.execucao.pedidos_qtd)}</span></div>
                              <div className="flex justify-between"><span className="text-sm font-bold text-slate-500">Backlog (Fila):</span> <span className="text-sm font-black text-indigo-600">{formatVolume(row.original.logistica.backlog_total)}</span></div>
                              <div className="flex justify-between"><span className="text-sm font-bold text-slate-500">Cortado:</span> <span className="text-sm font-black text-rose-500">{formatVolume(row.original.execucao.corte_qtd)}</span></div>
                            </div>

                            <div className="bg-white p-5 rounded-2xl shadow-sm border border-indigo-50 flex flex-col gap-2 justify-center items-center text-center">
                               <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">PMV Atual</span>
                               <span className="text-3xl font-black text-indigo-600">{formatMoeda(row.original.pmv)}</span>
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

        {/* --- TABELA OPORTUNIDADES / CHURN --- */}
        <div className="bg-white rounded-[40px] shadow-sm border border-slate-100 overflow-hidden">
          <div className="p-8 border-b border-slate-50 flex justify-between items-center">
            <div>
              <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3">
                <UserMinus className="w-5 h-5 text-orange-500"/> Oportunidades & Risco de Churn
              </h3>
              <p className="text-xs font-bold text-slate-500 mt-1">Clientes com meta S&OP que ainda não compraram no mês.</p>
            </div>
            <div className="bg-orange-50 px-4 py-2 rounded-xl border border-orange-100">
               <span className="text-xs font-black text-orange-600 uppercase tracking-widest">
                 {oportunidades.length} Clientes Pendentes
               </span>
            </div>
          </div>
          
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50/50">
                  <th className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest">Cliente</th>
                  <th className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest">Regional</th>
                  <th className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest">Meta Volume</th>
                  <th className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest">Perda Estimada</th>
                  <th className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">Dias Sem Compra</th>
                  <th className="px-6 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">Ação</th>
                </tr>
              </thead>
              <tbody>
                {oportunidades.map((op, idx) => (
                  <tr key={idx} className="hover:bg-slate-50/80 transition-colors border-b border-slate-50">
                    <td className="px-6 py-4 text-xs font-bold text-slate-700">{op.cliente}</td>
                    <td className="px-6 py-4 text-xs font-bold text-slate-500">{op.regional}</td>
                    <td className="px-6 py-4 text-xs font-black text-slate-700">{formatVolume(op.meta_vol)} Cx</td>
                    <td className="px-6 py-4 text-sm font-black text-rose-500">{formatMoeda(op.valor_estimado)}</td>
                    <td className="px-6 py-4 text-center">
                      <span className="bg-slate-100 text-slate-600 px-3 py-1 rounded-full text-xs font-black">
                        {op.dias_sem_compra}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`px-3 py-1 rounded text-[10px] font-black uppercase tracking-widest ${op.status === 'Crítico' ? 'bg-rose-100 text-rose-700' : 'bg-orange-100 text-orange-700'}`}>
                        {op.status}
                      </span>
                    </td>
                  </tr>
                ))}
                {oportunidades.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-6 py-10 text-center text-sm font-bold text-emerald-500">
                      Tudo verde! Todos os clientes com meta já realizaram pedidos este mês.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}