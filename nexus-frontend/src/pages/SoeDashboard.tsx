import { useState, useMemo, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  Activity, AlertCircle, ArrowDown, ArrowUp, ArrowUpDown, 
  ChevronDown, ChevronRight, DollarSign, Filter, Loader2, Factory,
  Package, RefreshCcw, Search, TrendingUp, TrendingDown, Info
} from 'lucide-react';
import { 
  useReactTable, getCoreRowModel, flexRender, getSortedRowModel, 
  SortingState, ColumnFiltersState, getFilteredRowModel 
} from '@tanstack/react-table';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function SoeDashboard() {
  const [dados, setDados] = useState<any[]>([]);
  const [metaGlobal, setMetaGlobal] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/soe/radar');
      setDados(res.data.dados || []);
      setMetaGlobal({ dia: res.data.dia_mes, total: res.data.total_skus });
    } catch (e) {
      console.error("Erro ao carregar Radar S&OE", e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleSyncStock = async () => {
    setIsSyncing(true);
    try {
      await axios.post('/api/v1/soe/sync-stock');
      await fetchData();
    } catch (e) {
      alert("Erro ao sincronizar estoque com a API 90.");
    } finally {
      setIsSyncing(false);
    }
  };

  useEffect(() => { fetchData(); }, [fetchData]);

  const stats = useMemo(() => {
    const emRisco = dados.filter(d => d.saldo_projetado < 0);
    const fatTotalRisco = emRisco.reduce((acc, curr) => acc + curr.fat_em_risco, 0);
    const pacingMedioVendas = dados.reduce((acc, curr) => acc + curr.pacing_vendas, 0) / (dados.length || 1);
    const pacingMedioProd = dados.reduce((acc, curr) => acc + curr.pacing_producao, 0) / (dados.length || 1);

    return { countRisco: emRisco.length, fatTotalRisco, pacingMedioVendas, pacingMedioProd };
  }, [dados]);

  const columns = useMemo(() => [
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
          <span className="text-xs font-bold text-slate-700 truncate">{info.row.original.descricao}</span>
        </div>
      )
    },
    {
      accessorKey: 'forecast_sop',
      header: 'Meta S&OP',
      cell: (info: any) => <span className="font-bold text-slate-600">{formatVolume(info.getValue())}</span>
    },
    {
      accessorKey: 'pacing_vendas',
      header: 'Ating. Vendas',
      cell: (info: any) => {
        const val = info.getValue();
        const esperado = (metaGlobal?.dia / 30) * 100;
        return (
          <div className="flex flex-col gap-1 w-32">
            <div className="flex justify-between text-[9px] font-black">
              <span className={val > esperado ? 'text-indigo-600' : 'text-slate-400'}>{val}%</span>
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
      accessorKey: 'pacing_producao',
      header: 'Ating. Produção',
      cell: (info: any) => {
        const val = info.getValue();
        const vendas = info.row.original.pacing_vendas;
        return (
          <div className="flex flex-col gap-1 w-32">
            <div className="flex justify-between text-[9px] font-black">
              <span className={val < vendas ? 'text-rose-500' : 'text-emerald-500'}>{val}%</span>
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
      accessorKey: 'estoque_d0',
      header: 'Estoque D0',
      cell: (info: any) => <span className="font-bold text-slate-800">{formatVolume(info.getValue())}</span>
    },
    {
      accessorKey: 'saldo_projetado',
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
      accessorKey: 'fat_em_risco',
      header: 'Faturamento em Risco',
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
    getFilteredRowModel: getFilteredRowModel()
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

        {/* TABELA PRINCIPAL */}
        <div className="bg-white rounded-[40px] shadow-sm border border-slate-100 overflow-hidden">
          <div className="p-8 border-b border-slate-50 flex flex-col md:flex-row justify-between items-center gap-4">
            <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3">
              <Package className="w-5 h-5 text-indigo-600"/> Pilha de Atendimento (M-1 + M0)
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
                          <ArrowUpDown className="w-3 h-3" />
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className="hover:bg-slate-50/80 transition-colors border-b border-slate-50 group">
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-6 py-5">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}