import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getSortedRowModel, getExpandedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Users, Loader2, Search, ArrowUpDown, ArrowUp, ArrowDown, Lock, Unlock, 
  ShieldAlert, Save, Store, Package, ChevronRight, ChevronDown, BarChart2, X, Filter, Activity 
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function GerenciamentoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState({});
  const [isProcessing, setIsProcessing] = useState(false);

  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [isTopDownFechado, setIsTopDownFechado] = useState(false);
  const [busca, setBusca] = useState('');

  // ESTADO DOS FILTROS DINÂMICOS
  const [nivelHierarquia, setNivelHierarquia] = useState('vendedor');
  const [nomeResponsavel, setNomeResponsavel] = useState('');
  const [opcoesBusca, setOpcoesBusca] = useState<{vendedores: string[], regionais: string[]}>({vendedores: [], regionais: []});

  // 1. Busca Filtros e Status Inicial
  useEffect(() => {
    const carregarConfig = async () => {
      try {
        const [filtrosRes, statusRes] = await Promise.all([
          axios.get('/api/v1/consensus/gerenciamento/filtros'),
          axios.get('/api/v1/consensus/macro/status').catch(() => ({ data: { is_topdown_fechado: false } }))
        ]);
        setOpcoesBusca(filtrosRes.data);
        setIsTopDownFechado(statusRes.data.is_topdown_fechado);
      } catch (e) { console.error("Erro ao carregar configurações", e); }
    };
    carregarConfig();
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento/vendedores', { 
        params: { 
          nivel_filtro: nivelHierarquia, 
          valor_filtro: nomeResponsavel 
        } 
      });
      setDadosBrutos(res.data.dados || []);
      setCelulasEditadas({});
      setExpanded({});
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [nivelHierarquia, nomeResponsavel]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSalvarAjustes = async () => {
    if (!window.confirm("Deseja aplicar estas alterações na carteira dos vendedores afetados?")) return;
    setIsProcessing(true);
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chave, dados]: any) => {
      Object.entries(dados.meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
          nivel: dados.tipo, chave: chave, mes_projetado: mes, 
          novo_volume: val.novo_volume === '' ? 0 : val.novo_volume 
        });
      });
    });

    try {
      await axios.post('/api/v1/consensus/gerenciamento/aprovar', { ajustes });
      alert("✅ Ajustes gravados com sucesso!");
      setCelulasEditadas({});
      fetchData();
    } catch (e: any) { alert("Erro ao gravar: " + (e.response?.data?.detail || e.message)); }
    finally { setIsProcessing(false); }
  };

  const handleToggleLock = async (origem: string) => {
    try {
      await axios.post('/api/v1/consensus/gerenciamento/toggle-lock', { origem });
      fetchData();
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao alterar trava."); }
  };

  const handleLockAll = async (acao: 'Trancar' | 'Destrancar') => {
    if (!window.confirm(`Deseja ${acao.toLowerCase()} as carteiras de todos os vendedores?`)) return;
    try {
      await axios.post('/api/v1/consensus/gerenciamento/lock-all', { acao });
      fetchData();
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao executar ação em lote."); }
  };

  const dadosFiltrados = useMemo(() => {
    if (!busca) return dadosBrutos;
    const term = busca.toLowerCase();
    return dadosBrutos.filter(v => v.nome.toLowerCase().includes(term));
  }, [dadosBrutos, busca]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number }> = {};
    dadosFiltrados.forEach(v => v.meses.forEach((m: any) => totais[m.mes_banco] = { vol: 0, fat: 0 }));
    
    dadosFiltrados.forEach(vendedor => {
      vendedor.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[vendedor.chave_matriz]?.meses?.[mes.mes_banco];
        const isPendente = edicao !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicao.novo_volume : mes.vol_ajustado));
        const pmvSimulado = mes.vol_ajustado > 0 ? (mes.receita / mes.vol_ajustado) : 0;
        
        totais[mes.mes_banco].vol += volumeFinal;
        totais[mes.mes_banco].fat += isPendente ? (volumeFinal * pmvSimulado) : mes.receita;
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/gerenciamento/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const columns = useMemo(() => {
    if (dadosBrutos.length === 0) return [];
    return [
      {
        id: 'nome', header: 'Hierarquia Comercial',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, nome, status, chave_matriz } = row.original;
          const isVendedor = tipo === 'vendedor';
          const isCliente = tipo === 'cliente';
          const isFechado = status === 'Fechado';

          return (
            <div style={{ paddingLeft: `${row.depth * 2.5}rem` }} className="flex items-center gap-3 py-2 relative">
              {isVendedor && (
                <button onClick={() => handleToggleLock(nome)} className={`absolute left-0 p-1.5 rounded-lg border transition-all ${isFechado ? 'bg-rose-50 border-rose-200 text-rose-500 hover:bg-rose-100' : 'bg-emerald-50 border-emerald-200 text-emerald-600 hover:bg-emerald-100'}`} title={isFechado ? 'Destrancar' : 'Trancar'}>
                  {isFechado ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
                </button>
              )}
              
              <div className={`${isVendedor ? 'ml-8' : ''} flex items-center gap-3`}>
                <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === chave_matriz ? 'bg-indigo-100 border-indigo-200 text-indigo-600' : 'hover:bg-slate-100 border-transparent text-slate-400'}`}>
                  <BarChart2 className="w-4 h-4" />
                </button>

                <button onClick={row.getToggleExpandedHandler()} className={`p-1 rounded-md transition-colors ${row.getCanExpand() ? 'hover:bg-slate-100 text-slate-500' : 'opacity-0 cursor-default'}`}>
                  {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>

                <div className={`w-8 h-8 rounded-xl flex items-center justify-center border shadow-sm ${isVendedor ? 'bg-blue-50 border-blue-100' : isCliente ? 'bg-indigo-50 border-indigo-100' : 'bg-slate-50 border-slate-200'}`}>
                  {isVendedor ? <Users className="w-4 h-4 text-blue-500" /> : isCliente ? <Store className="w-4 h-4 text-indigo-500" /> : <Package className="w-4 h-4 text-slate-400" />}
                </div>

                <div className="flex flex-col">
                  <span className={`text-sm truncate max-w-[250px] ${isVendedor ? 'font-black text-slate-900 tracking-tighter' : isCliente ? 'font-bold text-slate-700' : 'font-medium text-slate-500'}`}>{nome}</span>
                  {isVendedor && <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{isFechado ? '🔒 Assinado' : '✏️ Em Aberto'}</span>}
                </div>
              </div>
            </div>
          );
        }
      },
      ...Array.from(new Set(dadosBrutos.flatMap(d => d.meses.map((m: any) => m.mes_banco))))
        .sort()
        .map(mesId => ({
          id: `mes_${mesId}`,
          header: dadosBrutos[0]?.meses.find((m: any) => m.mes_banco === mesId)?.mes_str || mesId,
          cell: (info: any) => {
            const row = info.row.original;
            const dadosMes = row.meses.find((m: any) => m.mes_banco === mesId);
            if (!dadosMes) return null;

            const meta = info.table.options.meta as any;
            const edicao = meta.celulasEditadas[row.chave_matriz]?.meses?.[mesId];
            const isPendente = edicao !== undefined;
            const volExibicao = Math.round(Number(isPendente ? edicao.novo_volume : dadosMes.vol_ajustado));
            const pmvSimulado = dadosMes.vol_ajustado > 0 ? (dadosMes.receita / dadosMes.vol_ajustado) : 0;

            return (
              <div className="flex flex-col items-center justify-center min-w-[130px]">
                <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter mb-1">IA: {formatVolume(dadosMes.vol_ia)}</span>
                <input
                  type="number" value={volExibicao === 0 ? '' : volExibicao}
                  onChange={(e) => meta.updateCell(row.tipo, row.chave_matriz, mesId, e.target.value)}
                  className={`text-sm text-center font-black p-2 rounded-xl border-2 transition-all w-full
                    ${isPendente ? 'bg-amber-50 border-amber-300 text-amber-800' : 'bg-white border-slate-100 text-slate-800 focus:border-indigo-400'}`}
                />
                <span className="text-[10px] font-black text-emerald-600 mt-1">{formatMoeda(isPendente ? volExibicao * pmvSimulado : dadosMes.receita)}</span>
              </div>
            );
          }
        }))
    ];
  }, [dadosBrutos, chartExpanded, celulasEditadas]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getExpandedRowModel: getExpandedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (tipo: string, chave: string, mes: string, val: string) => {
        const v = val === '' ? '' : Math.round(Number(val));
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { tipo, meses: { ...(prev[chave]?.meses || {}), [mes]: { novo_volume: v } } } }));
      }
    }
  });

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-24">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* FILTROS E BUSCA (ESTILO NEXUS) */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100">
          <div>
            <h1 className="text-3xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Users className="w-8 h-8 text-blue-600" /> VISÃO GERENCIAL
            </h1>
            <p className="text-slate-500 font-medium mt-1">Supervisão, controle e aprovação da equipa comercial.</p>
          </div>

          <div className="flex flex-wrap items-center gap-4 bg-slate-50 p-4 rounded-[32px] border border-slate-100">
              <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest pl-2">Filtrar:</span>
              <select value={nivelHierarquia} onChange={e => {setNivelHierarquia(e.target.value); setNomeResponsavel('');}} className="bg-white border border-slate-200 text-xs font-bold p-2.5 rounded-2xl outline-none text-slate-700 cursor-pointer">
                  <option value="regional">Regional</option>
                  <option value="vendedor">Vendedor</option>
              </select>
              <select value={nomeResponsavel} onChange={e => setNomeResponsavel(e.target.value)} className="bg-white border border-slate-200 text-xs font-bold p-2.5 rounded-2xl outline-none text-slate-700 cursor-pointer min-w-[200px]">
                  <option value="">{nivelHierarquia === 'vendedor' ? '-- Todos os Vendedores --' : '-- Todas as Regionais --'}</option>
                  {(nivelHierarquia === 'vendedor' ? opcoesBusca.vendedores : opcoesBusca.regionais).map(opt => <option key={opt} value={opt}>{opt}</option>)}
              </select>
              <button onClick={fetchData} className="bg-indigo-600 text-white px-5 py-2.5 rounded-2xl text-xs font-black uppercase tracking-widest hover:bg-indigo-700 transition flex items-center gap-2 shadow-lg shadow-indigo-200">
                <Search className="w-4 h-4" /> Buscar
              </button>
          </div>
        </div>

        {/* TABELA CENTRAL */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative">
          {isLoading ? (
            <div className="h-64 flex flex-col items-center justify-center">
              <Loader2 className="w-10 h-10 animate-spin text-indigo-500 mb-4" />
              <span className="text-slate-400 font-black text-xs uppercase tracking-widest">Sincronizando Hierarquia...</span>
            </div>
          ) : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white/95 backdrop-blur-xl border-b-2 border-slate-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className="px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest cursor-pointer hover:bg-slate-50 transition-colors" onClick={header.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getIsSorted() ? (header.column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3 text-indigo-500"/> : <ArrowDown className="w-3 h-3 text-indigo-500"/>) : <ArrowUpDown className="w-3 h-3 opacity-20"/>}
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-indigo-50/20' : 'hover:bg-slate-50/50'}`}>
                      {row.getVisibleCells().map(cell => <td key={cell.id} className="px-8 py-4">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
                    </tr>

                    {/* GRÁFICO MULTI-NÍVEL (S&OE) */}
                    {chartExpanded === row.original.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[40px] p-8 border border-slate-800 animate-in fade-in duration-500">
                             <div className="flex justify-between items-start mb-8 px-2">
                               <div className="flex flex-col gap-1">
                                 <h3 className="text-xl font-black text-white uppercase tracking-tighter flex items-center gap-3">
                                   <Activity className="w-6 h-6 text-emerald-400" /> Monitor S&OE: {row.original.nome}
                                 </h3>
                                 <p className="text-xs font-bold text-slate-500 tracking-widest uppercase pl-9">Visão Integrada (Histórico vs Projeção)</p>
                               </div>
                               <button onClick={() => setChartExpanded(null)} className="p-2 hover:bg-slate-800 rounded-full text-slate-500 transition"><X className="w-6 h-6"/></button>
                             </div>

                             <div className="h-[300px] w-full">
                               {loadingGrafico === row.original.chave_matriz ? (
                                 <div className="h-full flex items-center justify-center"><Loader2 className="animate-spin text-white w-10 h-10"/></div>
                               ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart data={dadosGraficoCache[row.original.chave_matriz] || []}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                    <Tooltip contentStyle={{borderRadius: '20px', backgroundColor: '#0f172a', border: '1px solid #1e293b'}} />
                                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900', color: '#cbd5e1'}} />
                                    <Line type="monotone" dataKey="CicloAnterior" name="Promessa Passada" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls />
                                    <Line type="monotone" dataKey="Realizado" name="Faturamento Real" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls />
                                    <Line type="monotone" dataKey="Consenso" name="Proposta Atual" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#0f172a'}} connectNulls />
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
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'nome') return <td key={header.id} className="px-8 py-6 text-right font-black uppercase text-xs text-slate-400">Total Equipa Comercial</td>;
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-6 text-center">
                          <div className="flex flex-col">
                            <span className="font-black text-white">{formatVolume(totaisGerais[m]?.vol || 0)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-sm tracking-tight">{formatMoeda(totaisGerais[m]?.fat || 0)}</span>
                          </div>
                        </td>
                      );
                    }
                    return <td key={header.id} className="px-8 py-6"></td>;
                  })}
                </tr>
              </tfoot>
            </table>
          </div>
          )}
        </div>

        {/* BARRA DE AÇÕES FLUTUANTE */}
        {Object.keys(celulasEditadas).length > 0 && (
          <div className="fixed bottom-8 left-1/2 -translate-x-1/2 z-50">
            <div className="bg-amber-500 text-white px-10 py-5 rounded-[32px] shadow-[0_20px_50px_rgba(245,158,11,0.5)] flex items-center gap-10 border border-amber-400 animate-in slide-in-from-bottom-10">
              <div className="flex flex-col">
                <span className="text-[10px] font-black uppercase tracking-widest opacity-80">Ajustes Gerenciais</span>
                <span className="text-lg font-black">{Object.keys(celulasEditadas).length} alterações pendentes</span>
              </div>
              <div className="h-10 w-[2px] bg-amber-400/50" />
              <div className="flex items-center gap-4">
                <button onClick={() => setCelulasEditadas({})} className="text-xs font-black text-amber-100 hover:text-white transition tracking-widest uppercase">Descartar</button>
                <button onClick={handleSalvarAjustes} disabled={isProcessing} className="bg-slate-900 text-white hover:bg-black px-10 py-3.5 rounded-2xl text-xs font-black tracking-widest uppercase transition shadow-lg flex items-center gap-2">
                  {isProcessing ? <Loader2 className="w-4 h-4 animate-spin"/> : <Save className="w-4 h-4"/>} Aplicar na Carteira
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}