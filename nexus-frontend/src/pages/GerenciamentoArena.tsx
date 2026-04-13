import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  getSortedRowModel,
  getExpandedRowModel,
  SortingState
} from '@tanstack/react-table';
import { Users, Loader2, Search, ArrowUpDown, ArrowUp, ArrowDown, Lock, Unlock, ShieldAlert, Save, Store, Package, ChevronRight, ChevronDown, BarChart2, X, Filter } from 'lucide-react';
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

  const [busca, setBusca] = useState('');
  const [filtroVendedor, setFiltroVendedor] = useState('TODOS');
  const [filtroCliente, setFiltroCliente] = useState('TODOS');
  const [filtroProduto, setFiltroProduto] = useState('TODOS');

  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento/vendedores', {
        params: { gerente_nome: usuarioSessao?.gerente_nome }
      });
      setDadosBrutos(res.data.dados || []);
    } catch (e) { 
      console.error("Erro ao carregar gerenciamento", e); 
    } finally { 
      setIsLoading(false); 
    }
  }, [usuarioSessao]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const vendedoresUnicos = useMemo(() => [...new Set(dadosBrutos.map(v => v.nome))].sort(), [dadosBrutos]);
  const clientesUnicos = useMemo(() => {
    const cls = new Set<string>();
    dadosBrutos.forEach(v => v.subRows?.forEach((c: any) => cls.add(c.nome)));
    return Array.from(cls).sort();
  }, [dadosBrutos]);
  const produtosUnicos = useMemo(() => {
    const prds = new Set<string>();
    dadosBrutos.forEach(v => v.subRows?.forEach((c: any) => c.subRows?.forEach((p: any) => prds.add(p.nome))));
    return Array.from(prds).sort();
  }, [dadosBrutos]);

  const dadosFiltrados = useMemo(() => {
    let dados = dadosBrutos;

    if (filtroVendedor !== 'TODOS') {
      dados = dados.filter(v => v.nome === filtroVendedor);
    }

    if (filtroCliente !== 'TODOS' || filtroProduto !== 'TODOS' || busca) {
      const bl = busca.toLowerCase();
      dados = dados.map(v => {
        const matchV = v.nome.toLowerCase().includes(bl);
        
        const novosClientes = v.subRows.map((c: any) => {
          const matchC = matchV || c.nome.toLowerCase().includes(bl);
          if (filtroCliente !== 'TODOS' && c.nome !== filtroCliente) return null;

          const novosProdutos = c.subRows.filter((p: any) => {
             if (filtroProduto !== 'TODOS' && p.nome !== filtroProduto) return false;
             if (busca && !matchC && !p.nome.toLowerCase().includes(bl)) return false;
             return true;
          });

          if (filtroProduto !== 'TODOS' && novosProdutos.length === 0) return null;
          if (busca && !matchC && novosProdutos.length === 0) return null;

          return { ...c, subRows: novosProdutos };
        }).filter(Boolean);

        if ((filtroCliente !== 'TODOS' || filtroProduto !== 'TODOS') && novosClientes.length === 0) return null;
        if (busca && !matchV && novosClientes.length === 0) return null;

        return { ...v, subRows: novosClientes };
      }).filter(Boolean);
    }
    return dados;
  }, [dadosBrutos, filtroVendedor, filtroCliente, filtroProduto, busca]);

  const totaisFaturamento = useMemo(() => {
    if (dadosFiltrados.length === 0) return {};
    const totais: Record<string, number> = {};
    dadosFiltrados.forEach(row => {
      row.meses.forEach((mes: any) => {
        if (totais[mes.mes_banco] === undefined) totais[mes.mes_banco] = 0;
        totais[mes.mes_banco] += mes.receita || 0;
      });
    });
    return totais;
  }, [dadosFiltrados]);

  const handleToggleLock = async (vendedor: string) => {
    try {
      await axios.post('/api/v1/consensus/gerenciamento/toggle-lock', { origem: vendedor });
      fetchData(); 
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao alterar o status do vendedor."); 
    }
  };

  const handleLockAll = async (acao: 'Trancar' | 'Destrancar') => {
    if (!window.confirm(`Deseja realmente ${acao.toLowerCase()} todos os vendedores listados para a sua gerência?`)) return;
    setIsProcessing(true);
    try {
      await axios.post('/api/v1/consensus/gerenciamento/lock-all', {
        gerente_nome: usuarioSessao?.gerente_nome || "",
        acao
      });
      alert(`✅ Vendedores ${acao === 'Trancar' ? 'trancados' : 'destrancados'} com sucesso!`);
      fetchData();
    } catch (e: any) { 
      alert(`Erro ao ${acao.toLowerCase()} os vendedores: ${JSON.stringify(e.response?.data?.detail)}`); 
    } finally { 
      setIsProcessing(false); 
    }
  };

  const handleSalvarAjustes = async () => {
    if (!window.confirm("Isso aplicará um rateio forçado sobre as cotas. Continuar?")) return;
    setIsProcessing(true);
    
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chaveMatriz, dados]: any) => {
      Object.entries(dados.meses).forEach(([mes, val]: any) => {
        ajustes.push({ nivel: dados.tipo, chave: chaveMatriz, mes_projetado: mes, novo_volume: val.novo_volume === '' ? 0 : val.novo_volume });
      });
    });

    try {
      await axios.post('/api/v1/consensus/gerenciamento/aprovar', { ajustes });
      alert("✅ Ajustes Sincronizados (Rateio Aplicado)!");
      setCelulasEditadas({});
      fetchData();
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao processar as alterações."); 
    } finally { 
      setIsProcessing(false); 
    }
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
        const res = await axios.get('/api/v1/consensus/gerenciamento/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const columns = useMemo(() => {
    if (dadosBrutos.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Hierarquia de Vendas', accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const isVendedor = row.original.tipo === 'vendedor';
          const isCliente = row.original.tipo === 'cliente';
          const isProduto = row.original.tipo === 'produto';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2">
              {!isProduto ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-100 rounded">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5 text-slate-500" /> : <ChevronRight className="w-5 h-5 text-slate-400" />}
                </button>
              ) : (
                <button onClick={() => toggleChart(row)} className={`p-1 rounded transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 text-indigo-600' : 'hover:bg-slate-100 text-slate-400'}`}>
                  <BarChart2 className="w-5 h-5" />
                </button>
              )}
              
              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${isVendedor ? 'bg-indigo-50 border-indigo-100' : isCliente ? 'bg-amber-50 border-amber-100' : 'bg-slate-50 border-slate-100'}`}>
                {isVendedor ? <Users className="w-4 h-4 text-indigo-500" /> : isCliente ? <Store className="w-4 h-4 text-amber-500" /> : <Package className="w-4 h-4 text-slate-400" />}
              </div>
              <span className={`text-sm ${isVendedor ? 'font-black text-slate-800' : isCliente ? 'font-bold text-slate-700' : 'font-medium text-slate-600 truncate max-w-[300px]'}`}>
                {info.getValue()}
              </span>
            </div>
          )
        }
      },
      {
        id: 'status', header: 'Status', accessorFn: (row: any) => row.status,
        cell: (info: any) => {
          if (info.row.original.tipo !== 'vendedor') return null;
          const isFechado = info.getValue() === 'Fechado';
          return (
            <div className={`inline-flex items-center gap-2 px-3 py-1 rounded-xl font-black text-[10px] uppercase tracking-widest ${isFechado ? 'bg-emerald-50 text-emerald-600 border border-emerald-200' : 'bg-rose-50 text-rose-600 border border-rose-200'}`}>
              {isFechado ? <Lock className="w-3 h-3" /> : <Unlock className="w-3 h-3" />}
              {isFechado ? 'Congelado' : 'Aberto'}
            </div>
          );
        }
      }
    ];

    const mesesUnicosMap = new Map();
    dadosBrutos.forEach(row => row.meses.forEach((m: any) => { if (!mesesUnicosMap.has(m.mes_banco)) mesesUnicosMap.set(m.mes_banco, m); }));
    
    Array.from(mesesUnicosMap.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)).forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_ajustado || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.chave_matriz]?.meses?.[m.mes_banco];
          
          const isFechado = row.status === 'Fechado';
          
          const valorReal = edicao !== undefined ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const baseTD = Math.round(Number(dadosMes?.vol_td || 0));
          
          const isPendente = edicao !== undefined;
          const isDiferenteTD = !isPendente && valorInteiro !== baseTD;
          
          let inputClass = 'bg-slate-50 border-transparent text-slate-800 focus:bg-white focus:border-slate-300';
          if (isFechado) {
            inputClass = 'cursor-not-allowed opacity-50 bg-slate-100 border-slate-200 text-slate-500';
          } else if (isPendente) {
            inputClass = 'bg-slate-800 border-slate-700 text-white shadow-md';
          } else if (isDiferenteTD) {
            inputClass = 'bg-blue-50 border-blue-200 text-blue-700 shadow-sm';
          }

          return (
            <div className="flex flex-col w-28 gap-1">
              <span className="text-[9px] text-slate-400 font-black opacity-60 uppercase text-center tracking-widest truncate">
                IA: {formatVolume(baseIA)} | TD: {formatVolume(baseTD)}
              </span>
              <input
                type="number" disabled={isFechado} value={valorReal === 0 ? '' : valorReal} placeholder="0"
                onChange={(e) => meta.updateCell(row.tipo, row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full ${inputClass}`}
              />
              <span className={`text-[10px] font-black text-center pr-1 tracking-tight ${isPendente ? 'text-slate-400' : 'text-emerald-600'}`}>
                {isPendente ? 'A calcular...' : formatMoeda(dadosMes?.receita || 0)}
              </span>
            </div>
          );
        }
      });
    });

    baseCols.push({
      id: 'acoes', header: 'Ação',
      cell: (info: any) => {
        if (info.row.original.tipo !== 'vendedor') return null;
        const isFechado = info.row.original.status === 'Fechado';
        return (
          <button onClick={() => handleToggleLock(info.row.original.id)} className={`px-4 py-2 rounded-xl text-[9px] font-black uppercase tracking-widest transition-all border shadow-sm ${isFechado ? 'bg-white border-slate-200 text-slate-500 hover:bg-slate-50' : 'bg-slate-800 text-white border-slate-800 hover:bg-slate-700'}`}>
            {isFechado ? 'Reabrir' : 'Trancar'}
          </button>
        )
      }
    });

    return baseCols;
  }, [dadosBrutos, chartExpanded]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { sorting, expanded },
    onSortingChange: setSorting, onExpandedChange: setExpanded, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getExpandedRowModel: getExpandedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (tipo: string, chaveMatriz: string, mes: string, val: string) => {
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chaveMatriz]: { tipo: tipo, meses: { ...(prev[chaveMatriz]?.meses || {}), [mes]: { novo_volume: finalV } } } }));
      }
    }
  });

  if (isLoading) return <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-indigo-500" /><span className="text-slate-400 font-black text-xs tracking-widest uppercase mt-4">Carregando Hierarquias...</span></div>;

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative pb-24">
        
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <ShieldAlert className="w-8 h-8 text-rose-600" /> CENTRAL DE GERENCIAMENTO (S&OP)
            </h1>
            <p className="text-slate-500 font-medium text-sm mt-1 ml-11">Visão Hierárquica: Force o Rateio de cotas em qualquer nível e tranque telas.</p>
          </div>
          
          <div className="flex items-center gap-2 w-full md:w-auto">
            <button onClick={() => handleLockAll('Destrancar')} disabled={isProcessing} className="flex-1 md:flex-none flex items-center justify-center gap-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 px-5 py-3 rounded-xl text-[10px] font-black tracking-widest uppercase transition-all">
                <Unlock className="w-4 h-4" /> Destrancar Todos
            </button>
            <button onClick={() => handleLockAll('Trancar')} disabled={isProcessing} className="flex-1 md:flex-none flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-5 py-3 rounded-xl text-[10px] font-black tracking-widest uppercase transition-all shadow-md">
                <Lock className="w-4 h-4" /> Trancar Todos
            </button>
          </div>
        </div>

        <div className="flex flex-col lg:flex-row items-center gap-3 mb-6 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100">
           <div className="flex items-center gap-2 w-full lg:w-1/4 px-3 py-1">
             <Search className="w-4 h-4 text-slate-400" />
             <input type="text" placeholder="Busca livre..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full bg-transparent text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400" />
           </div>
           <div className="hidden lg:block w-px h-6 bg-slate-100"></div>

           <div className="flex items-center gap-2 w-full lg:w-1/4 px-3 py-1">
             <Users className="w-4 h-4 text-slate-400" />
             <select value={filtroVendedor} onChange={e => setFiltroVendedor(e.target.value)} className="bg-transparent text-sm font-bold text-slate-700 outline-none w-full cursor-pointer truncate">
                <option value="TODOS">TODOS OS VENDEDORES</option>
                {vendedoresUnicos.map((v: any) => <option key={v} value={v}>{v}</option>)}
             </select>
           </div>
           <div className="hidden lg:block w-px h-6 bg-slate-100"></div>
           
           <div className="flex items-center gap-2 w-full lg:w-1/4 px-3 py-1">
             <Store className="w-4 h-4 text-slate-400" />
             <select value={filtroCliente} onChange={e => setFiltroCliente(e.target.value)} className="bg-transparent text-sm font-bold text-slate-700 outline-none w-full cursor-pointer truncate">
                <option value="TODOS">TODOS OS CLIENTES</option>
                {clientesUnicos.map((c: any) => <option key={c} value={c}>{c}</option>)}
             </select>
           </div>
           <div className="hidden lg:block w-px h-6 bg-slate-100"></div>

           <div className="flex items-center gap-2 w-full lg:w-1/4 px-3 py-1">
             <Package className="w-4 h-4 text-slate-400" />
             <select value={filtroProduto} onChange={e => setFiltroProduto(e.target.value)} className="bg-transparent text-sm font-bold text-slate-700 outline-none w-full cursor-pointer truncate">
                <option value="TODOS">TODOS OS PRODUTOS</option>
                {produtosUnicos.map((p: any) => <option key={p} value={p}>{p}</option>)}
             </select>
           </div>
        </div>

        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative">
          <div className="overflow-x-auto pb-4 min-h-[400px]">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 bg-white/95 backdrop-blur-xl z-10 border-b border-slate-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className={`px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest ${header.column.getCanSort() ? 'cursor-pointer hover:bg-slate-50 transition-colors group' : ''}`} onClick={header.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getCanSort() && (
                            <span className="text-slate-300">
                              {{ asc: <ArrowUp className="w-4 h-4 text-indigo-500" />, desc: <ArrowDown className="w-4 h-4 text-indigo-500" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-0 group-hover:opacity-100 transition-opacity" />}
                            </span>
                          )}
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              
              <tbody>
                {dadosFiltrados.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="text-center py-20 text-slate-400 font-bold uppercase tracking-widest text-sm">
                      Nenhum resultado encontrado para os filtros selecionados.
                    </td>
                  </tr>
                ) : (
                  table.getRowModel().rows.map(row => (
                    <Fragment key={row.id}>
                      <tr className={`border-b transition-colors ${row.original.tipo === 'vendedor' ? 'bg-white border-slate-100' : row.original.tipo === 'cliente' ? 'bg-slate-50/50 border-slate-100' : 'bg-slate-50/80 border-white hover:bg-slate-100'}`}>
                        {row.getVisibleCells().map(cell => (
                          <td key={cell.id} className="px-8 py-3">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>

                      {/* EXPANSÃO DO GRÁFICO NO NÍVEL DO PRODUTO */}
                      {chartExpanded === row.original.chave_matriz && row.original.tipo === 'produto' && (
                        <tr>
                          <td colSpan={table.getAllColumns().length} className="bg-indigo-50/20 p-8 border-b border-slate-100">
                            <div className="bg-white rounded-[40px] p-8 shadow-inner border border-indigo-100 animate-in fade-in duration-500">
                              <h3 className="text-lg font-black text-gray-900 uppercase tracking-tighter mb-6">
                                 Detalhe: <span className="text-indigo-600">{row.original.nome}</span>
                              </h3>

                              <div className="h-[200px] w-full -ml-4">
                                {loadingGrafico === row.original.chave_matriz ? (
                                  <div className="h-full flex items-center justify-center text-indigo-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                                ) : (
                                  <ResponsiveContainer width="100%" height="100%">
                                    <LineChart data={
                                        (dadosGraficoCache[row.original.chave_matriz] || []).map((p: any) => {
                                            const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                            const edicao = celulasEditadas[row.original.chave_matriz]?.meses?.[p.data_iso];
                                            // CORREÇÃO: Agora lê o 'novo_volume' em tempo real!
                                            const valConsenso = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                            return { ...p, Consenso: valConsenso !== null ? valConsenso : p.Consenso };
                                        })
                                    }>
                                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                      <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900}} axisLine={false} tickLine={false} />
                                      <YAxis tick={{fontSize: 10}} axisLine={false} tickLine={false} />
                                      <Tooltip contentStyle={{borderRadius: '20px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                      <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900'}} />
                                      
                                      <Line type="monotone" dataKey="CicloAnterior" name="Proposta Mês Passado" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                                      
                                      <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={4} dot={{r: 3, fill: '#0f172a'}} connectNulls={false} />
                                      <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                      
                                      {/* CORREÇÃO: Linha de ajuste gerencial ajustada para cor Azul (#3b82f6) */}
                                      <Line type="monotone" dataKey="Consenso" name="Ajuste Gerencial" stroke="#3b82f6" strokeWidth={5} dot={{r: 6, fill: '#3b82f6', strokeWidth: 2, stroke: '#fff'}} connectNulls={false} />
                                    </LineChart>
                                  </ResponsiveContainer>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))
                )}
              </tbody>

              <tfoot className="bg-slate-900 text-white">
                <tr>
                  {table.getHeaderGroups()[0].headers.map((header: any) => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Receita Consolidada</span>
                          <span className="font-bold text-sm text-white">TOTAL (R$)</span>
                        </div>
                      </td>
                    );
                    if (header.id === 'status' || header.id === 'acoes') return <td key={header.id} className="px-8 py-5"></td>;
                    
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="bg-slate-800/80 inline-block px-3 py-1.5 rounded-xl border border-slate-700/50">
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight">
                              {formatMoeda(totaisFaturamento[mesBanco] || 0)}
                            </span>
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

        {Object.keys(celulasEditadas).length > 0 && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
            <div className="bg-rose-600 text-white px-10 py-5 rounded-full shadow-[0_20px_50px_rgba(225,29,72,0.4)] flex items-center gap-8 border border-rose-500 animate-in slide-in-from-bottom-10">
              <div className="text-sm font-black tracking-widest uppercase flex items-center gap-3">
                <span className="bg-white text-rose-600 px-3 py-1 rounded-lg text-lg">{Object.keys(celulasEditadas).length}</span> Edições Pendentes
              </div>
              <div className="h-8 w-[2px] bg-rose-400/50" />
              <button onClick={() => setCelulasEditadas({})} className="text-xs font-black text-rose-200 hover:text-white transition tracking-widest uppercase">Descartar</button>
              <button onClick={handleSalvarAjustes} disabled={isProcessing} className="bg-white text-rose-700 hover:bg-gray-50 px-8 py-3 rounded-full text-xs font-black flex items-center gap-2 shadow-lg tracking-widest uppercase">
                {isProcessing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Salvar Rateio Hierárquico
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}