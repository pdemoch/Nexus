import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, 
  getSortedRowModel, SortingState, ColumnDef 
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Layers, Lock, Download, AlertTriangle, ShieldCheck, Check, Globe, Package, 
  Users, Search, Filter, BarChart3, TrendingUp, TrendingDown, Activity, Hash, Target
} from 'lucide-react';
import axios from 'axios';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

const VarBadge = ({ atual = 0, anterior = 0 }: { atual?: number, anterior?: number }) => {
  const v = calcVar(atual, anterior);
  if (v === 0 || anterior === 0) return <span className="text-[10px] text-slate-400 font-bold">-</span>;
  const isPos = v > 0;
  return (
    <span className={`text-[10px] font-black flex items-center gap-0.5 px-1.5 py-0.5 rounded-md ${isPos ? 'text-emerald-700 bg-emerald-100' : 'text-rose-700 bg-rose-100'}`}>
      {isPos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
      {Math.abs(v).toFixed(1)}%
    </span>
  );
};

export default function GlobalDashboard() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [isApproving, setIsApproving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [globalLock, setGlobalLock] = useState(false);
  
  // ESTADOS DAS TABELAS E TOGGLE
  const [viewMode, setViewMode] = useState<'categorias' | 'clientes'>('categorias');
  const [sorting, setSorting] = useState<SortingState>([]);
  const [clientSorting, setClientSorting] = useState<SortingState>([]);

  const fetchDashboard = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get('/api/v1/dashboard/global');
      setData(res.data);
      // Verifica travamento
      const checkRes = await axios.get('/api/v1/consensus/gerenciamento/status-travas');
      const travas = checkRes.data.travas || [];
      const isLocked = travas.some((t: any) => t.origem === 'S&OP-Final');
      setGlobalLock(isLocked);
    } catch (e) {
      console.error("Erro ao carregar Dashboard Global", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  const handleAprovarSOP = async () => {
    if (!window.confirm("Assinar e CONGELAR o S&OP Global? \n\nIsto irá travar a base oficial de metas da companhia e impedir alterações futuras neste ciclo.")) return;
    setIsApproving(true);
    try {
      await axios.post('/api/v1/dashboard/aprovar', { ajustes: [] });
      alert("✅ S&OP Global assinado e congelado com sucesso!");
      fetchDashboard();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao aprovar S&OP");
    } finally {
      setIsApproving(false);
    }
  };

  const handleExportar = async () => {
    setIsExporting(true);
    try {
      const response = await axios.get('/api/v1/dashboard/exportar-oficial', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `SOP_Nexus_Oficial.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (e: any) {
      alert("Erro ao exportar base oficial.");
    } finally {
      setIsExporting(false);
    }
  };

  // ============================================================================
  // CONFIGURAÇÃO DA TABELA 1: CATEGORIAS E SKUs (Tipado corretamente)
  // ============================================================================
  const columns = useMemo<ColumnDef<any>[]>(() => {
    if (!data) return [];
    const cols: ColumnDef<any>[] = [
      {
        id: 'nome',
        header: 'Categoria / Produto (SKU)',
        accessorFn: (row: any) => row.nome,
        cell: ({ row, getValue }) => (
          <div 
            className={`flex items-center gap-2 cursor-pointer select-none ${row.depth === 0 ? 'font-bold text-slate-800' : 'pl-6 text-slate-600 text-sm'}`}
            onClick={row.getCanExpand() ? row.getToggleExpandedHandler() : undefined}
          >
            {row.getCanExpand() ? (
              row.getIsExpanded() ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />
            ) : (
              <div className="w-4" />
            )}
            {row.depth === 0 ? <Layers className="w-4 h-4 text-indigo-500" /> : <Package className="w-3 h-3 text-slate-400" />}
            {String(getValue())}
          </div>
        ),
      }
    ];

    data.meses_disponiveis.forEach((mes: string) => {
      cols.push({
        id: `mes_${mes}`,
        header: ({ column }) => (
          <div className="flex items-center justify-center gap-2 cursor-pointer" onClick={column.getToggleSortingHandler()}>
            <span>{mes}</span>
            {{ asc: <ArrowUp className="w-3 h-3"/>, desc: <ArrowDown className="w-3 h-3"/> }[column.getIsSorted() as string] ?? <ArrowUpDown className="w-3 h-3 text-slate-300"/>}
          </div>
        ),
        accessorFn: (row: any) => row.meses[mes]?.vol_final || 0,
        cell: ({ row }) => {
          const vol = row.original.meses[mes]?.vol_final || 0;
          const rec = row.original.meses[mes]?.rec_final || 0;
          return (
            <div className="flex flex-col items-center justify-center">
              <span className="font-bold text-slate-700">{formatVolume(vol)} <span className="text-[10px] text-slate-400 font-normal">CX</span></span>
              <span className="text-xs font-black text-emerald-600/90">{formatMoeda(rec)}</span>
            </div>
          );
        }
      });
    });

    return cols;
  }, [data]);

  const table = useReactTable({
    data: data?.categorias || [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getSubRows: row => row.skus,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // ============================================================================
  // CONFIGURAÇÃO DA TABELA 2: TOP CLIENTES (CURVA A) (Tipado corretamente)
  // ============================================================================
  const clientColumns = useMemo<ColumnDef<any>[]>(() => {
    if (!data) return [];
    const cols: ColumnDef<any>[] = [
      {
        id: 'nome',
        header: 'Razão Social / Produto Comprado',
        accessorFn: (row: any) => row.nome,
        cell: ({ row, getValue }) => (
          <div 
            className={`flex items-center gap-2 cursor-pointer select-none ${row.depth === 0 ? 'font-bold text-slate-800' : 'pl-6 text-slate-600 text-sm'}`}
            onClick={row.getCanExpand() ? row.getToggleExpandedHandler() : undefined}
          >
            {row.getCanExpand() ? (
              row.getIsExpanded() ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />
            ) : (
              <div className="w-4" />
            )}
            {row.depth === 0 ? <Users className="w-4 h-4 text-blue-500" /> : <Package className="w-3 h-3 text-slate-400" />}
            <span className="truncate max-w-[350px] block" title={String(getValue())}>{String(getValue())}</span>
          </div>
        ),
      }
    ];

    data.meses_disponiveis.forEach((mes: string) => {
      cols.push({
        id: `mes_${mes}`,
        header: ({ column }) => (
          <div className="flex items-center justify-center gap-2 cursor-pointer" onClick={column.getToggleSortingHandler()}>
            <span>{mes}</span>
            {{ asc: <ArrowUp className="w-3 h-3"/>, desc: <ArrowDown className="w-3 h-3"/> }[column.getIsSorted() as string] ?? <ArrowUpDown className="w-3 h-3 text-slate-300"/>}
          </div>
        ),
        accessorFn: (row: any) => row.meses[mes]?.vol_final || 0,
        cell: ({ row }) => {
          const vol = row.original.meses[mes]?.vol_final || 0;
          const rec = row.original.meses[mes]?.rec_final || 0;
          return (
            <div className="flex flex-col items-center justify-center">
              <span className="font-bold text-slate-700">{formatVolume(vol)} <span className="text-[10px] text-slate-400 font-normal">CX</span></span>
              <span className="text-xs font-black text-emerald-600/90">{formatMoeda(rec)}</span>
            </div>
          );
        }
      });
    });

    return cols;
  }, [data]);

  const clientTable = useReactTable({
    data: data?.clientes_80 || [],
    columns: clientColumns,
    state: { sorting: clientSorting },
    onSortingChange: setClientSorting,
    getSubRows: row => row.skus,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // ============================================================================
  // CÁLCULO DOS TOTAIS DO RODAPÉ (FOOTER)
  // ============================================================================
  const stats = useMemo(() => {
    if (!data) return null;
    const porMes: any = {};
    data.meses_disponiveis.forEach((m: string) => { porMes[m] = { vol_final: 0, rec_final: 0 }; });
    
    // Calcula sempre da visão de categorias para o Total Global ser fiel
    data.categorias.forEach((cat: any) => {
      data.meses_disponiveis.forEach((m: string) => {
        porMes[m].vol_final += (cat.meses[m]?.vol_final || 0);
        porMes[m].rec_final += (cat.meses[m]?.rec_final || 0);
      });
    });
    return { porMes };
  }, [data]);

  if (loading) return <div className="p-10 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-slate-400" /></div>;
  if (!data) return null;

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12">
        
        {/* HEADER EXECUTIVO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter uppercase">
              <Globe className="w-8 h-8 text-indigo-600" /> Executive S&OP Dashboard
            </h1>
            <p className="text-slate-500 font-medium text-sm mt-1 ml-11">Consolidação Executiva da Demanda e Faturamento Projetado.</p>
          </div>
          
          <div className="flex items-center gap-3">
            <button 
              onClick={handleExportar} disabled={isExporting}
              className="flex items-center gap-2 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-sm"
            >
              {isExporting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Exportar Oficial
            </button>
            <button 
              onClick={handleAprovarSOP} disabled={isApproving || globalLock}
              className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-md
                ${globalLock ? 'bg-slate-200 text-slate-400 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-emerald-600/20'}`}
            >
              {isApproving ? <Loader2 className="w-4 h-4 animate-spin" /> : globalLock ? <Lock className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />}
              {globalLock ? 'Plano Congelado' : 'Assinar & Congelar Plano'}
            </button>
          </div>
        </div>

        {/* CARDS DE KPIs GERAIS */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
          <div className="bg-white rounded-[32px] p-8 border border-slate-100 shadow-sm flex items-center justify-between relative overflow-hidden">
            <div className="absolute top-0 right-0 p-8 opacity-5"><Package className="w-32 h-32" /></div>
            <div>
              <p className="text-xs font-black text-slate-400 uppercase tracking-widest mb-1">Volume Total (Projetado M2-M4)</p>
              <h2 className="text-4xl font-black text-slate-800 tracking-tighter">
                {formatVolume(data.stats_gerais.vol_total)} <span className="text-lg text-slate-400 font-bold tracking-normal">CX</span>
              </h2>
            </div>
            <div className="p-4 bg-indigo-50 rounded-2xl"><Activity className="w-8 h-8 text-indigo-600" /></div>
          </div>

          <div className="bg-white rounded-[32px] p-8 border border-slate-100 shadow-sm flex items-center justify-between relative overflow-hidden">
            <div className="absolute top-0 right-0 p-8 opacity-5"><BarChart3 className="w-32 h-32" /></div>
            <div>
              <p className="text-xs font-black text-slate-400 uppercase tracking-widest mb-1">Faturamento Bruto (Projetado M2-M4)</p>
              <h2 className="text-4xl font-black text-emerald-600 tracking-tighter">
                {formatMoeda(data.stats_gerais.rec_total)}
              </h2>
            </div>
            <div className="p-4 bg-emerald-50 rounded-2xl"><Target className="w-8 h-8 text-emerald-600" /></div>
          </div>
        </div>

        {/* GRÁFICO DE TENDÊNCIA */}
        <div className="bg-white rounded-[32px] p-8 border border-slate-100 shadow-sm mb-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2 bg-indigo-50 rounded-xl"><TrendingUp className="w-5 h-5 text-indigo-600" /></div>
            <div>
              <h2 className="text-base font-black text-slate-800 uppercase tracking-widest">Tendência de Faturamento (Realizado vs Projetado)</h2>
              <p className="text-xs font-bold text-slate-400">Evolução do faturamento R$ nos últimos 9 meses e projeção S&OP para os próximos 3 meses.</p>
            </div>
          </div>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.grafico_tendencia} margin={{ top: 10, right: 10, left: 20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="mes" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#94a3b8', fontWeight: 'bold' }} dy={10} />
                <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#94a3b8', fontWeight: 'bold' }} tickFormatter={(val) => `R$ ${(val/1000000).toFixed(1)}M`} />
                <Tooltip 
                  cursor={{ stroke: '#e2e8f0', strokeWidth: 2, strokeDasharray: '4 4' }}
                  contentStyle={{ borderRadius: '16px', border: 'none', boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.1)', fontWeight: 'bold' }}
                  formatter={(val: any) => formatMoeda(Number(val))}
                />
                <Legend iconType="circle" wrapperStyle={{ fontSize: '12px', fontWeight: 'bold', paddingTop: '20px' }} />
                <Line type="monotone" name="Realizado (Histórico)" dataKey="rec_real" stroke="#cbd5e1" strokeWidth={4} dot={{ r: 4, fill: '#cbd5e1', strokeWidth: 0 }} activeDot={{ r: 6, fill: '#94a3b8' }} />
                <Line type="monotone" name="Projetado S&OP" dataKey="rec_proj" stroke="#10b981" strokeWidth={4} dot={{ r: 4, fill: '#10b981', strokeWidth: 0 }} activeDot={{ r: 6, fill: '#059669', stroke: '#fff', strokeWidth: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* ============================================================================ */}
        {/* TOGGLE DE VISÕES (CATEGORIAS vs TOP CLIENTES) */}
        {/* ============================================================================ */}
        <div className="bg-white rounded-[32px] border border-slate-100 shadow-sm overflow-hidden flex flex-col">
          <div className="p-6 lg:p-8 border-b border-slate-100 flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-indigo-50 rounded-xl">
                {viewMode === 'categorias' ? <Layers className="w-5 h-5 text-indigo-600" /> : <Users className="w-5 h-5 text-indigo-600" />}
              </div>
              <div>
                <h2 className="text-base font-black text-slate-800 uppercase tracking-widest">
                  {viewMode === 'categorias' ? 'Detalhamento por Categoria' : 'Detalhamento Top Clientes (Curva A - 80%)'}
                </h2>
                <p className="text-xs font-bold text-slate-400">
                  {viewMode === 'categorias' ? 'Visão hierárquica do S&OP por Família e SKU.' : 'Análise profunda dos clientes que representam 80% do faturamento.'}
                </p>
              </div>
            </div>

            {/* BOTÕES DO TOGGLE */}
            <div className="flex bg-slate-100 p-1.5 rounded-2xl w-full md:w-auto">
              <button 
                onClick={() => setViewMode('categorias')}
                className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all ${viewMode === 'categorias' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
              >
                <Layers className="w-4 h-4" /> Categorias
              </button>
              <button 
                onClick={() => setViewMode('clientes')}
                className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all ${viewMode === 'clientes' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
              >
                <Users className="w-4 h-4" /> Clientes Curva A
              </button>
            </div>
          </div>

          {/* ÁREA DA TABELA: RENDERIZA CONDICIONALMENTE */}
          {viewMode === 'categorias' ? (
            <div className="overflow-x-auto w-full custom-scrollbar pb-4">
              <table className="w-full text-left border-collapse min-w-[800px]">
                <thead>
                  {table.getHeaderGroups().map(hg => (
                    <tr key={hg.id} className="bg-slate-50 border-b border-slate-100">
                      {hg.headers.map(header => (
                        <th key={header.id} className="px-6 py-4 text-xs font-black text-slate-500 uppercase tracking-widest whitespace-nowrap text-center">
                          {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {table.getRowModel().rows.map(row => (
                    <Fragment key={row.id}>
                      <tr className={`hover:bg-slate-50/50 transition-colors ${row.depth === 0 ? 'bg-white' : 'bg-slate-50/30'}`}>
                        {row.getVisibleCells().map(cell => (
                          <td key={cell.id} className={`px-6 py-4 ${cell.column.id === 'nome' ? '' : 'text-center'}`}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                    </Fragment>
                  ))}
                </tbody>
                <tfoot className="bg-slate-900 sticky bottom-0">
                  <tr>
                    {table.getVisibleLeafColumns().map(header => {
                      if (header.id === 'nome') return (
                        <td key={header.id} className="px-6 py-5">
                          <div className="flex flex-col">
                            <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Fechamento do Mês</span>
                            <span className="font-bold text-sm text-white">TOTAL S&OP</span>
                          </div>
                        </td>
                      );
                      if (header.id.startsWith('mes_')) {
                        const mesBanco = header.id.replace('mes_', '');
                        const volM = stats?.porMes[mesBanco]?.vol_final || 0;
                        const recM = stats?.porMes[mesBanco]?.rec_final || 0;
                        return (
                          <td key={header.id} className="px-8 py-5">
                            <div className="flex flex-col items-center justify-center min-w-[100px]">
                              <span className="font-black text-white">{formatVolume(volM)} <span className="text-[9px] text-slate-400">CX</span></span>
                              <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(recM)}</span>
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
          ) : (
            <div className="overflow-x-auto w-full custom-scrollbar pb-4">
            <table className="w-full text-left border-collapse min-w-[800px]">
              <thead>
                {clientTable.getHeaderGroups().map(hg => (
                  <tr key={hg.id} className="bg-slate-50 border-b border-slate-100">
                    {hg.headers.map(header => (
                      <th key={header.id} className="px-6 py-4 text-xs font-black text-slate-500 uppercase tracking-widest whitespace-nowrap text-center">
                        {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-slate-50">
                {clientTable.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`hover:bg-slate-50/50 transition-colors ${row.depth === 0 ? 'bg-white' : 'bg-slate-50/30'}`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className={`px-6 py-4 ${cell.column.id === 'nome' ? '' : 'text-center'}`}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  </Fragment>
                ))}
              </tbody>
              <tfoot className="bg-slate-900 sticky bottom-0">
                <tr>
                  {clientTable.getVisibleLeafColumns().map(header => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-6 py-5">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Fechamento do Mês</span>
                          <span className="font-bold text-sm text-white">TOTAL S&OP</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      const volM = stats?.porMes[mesBanco]?.vol_final || 0;
                      const recM = stats?.porMes[mesBanco]?.rec_final || 0;
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white">{formatVolume(volM)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(recM)}</span>
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