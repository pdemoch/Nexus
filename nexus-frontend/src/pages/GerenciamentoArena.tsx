import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  getSortedRowModel,
  getExpandedRowModel,
  SortingState
} from '@tanstack/react-table';
import { Users, Loader2, Search, ArrowUpDown, ArrowUp, ArrowDown, Lock, Unlock, ShieldAlert, Save, Store, Package, ChevronRight, ChevronDown } from 'lucide-react';
import axios from 'axios';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));

export default function GerenciamentoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState({});
  const [busca, setBusca] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await axios.get('http://localhost:8000/api/v1/consensus/gerenciamento/vendedores', {
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

  const dadosFiltrados = useMemo(() => dadosBrutos.filter(d => d.nome.toLowerCase().includes(busca.toLowerCase())), [dadosBrutos, busca]);

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
      await axios.post('http://localhost:8000/api/v1/consensus/gerenciamento/toggle-lock', { origem: vendedor });
      fetchData(); 
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao alterar o status do vendedor."); 
    }
  };

  // CORREÇÃO: Ação em Lote Blindada contra [object Object]
  const handleLockAll = async (acao: 'Trancar' | 'Destrancar') => {
    if (!window.confirm(`Deseja realmente ${acao.toLowerCase()} todos os vendedores listados para a sua gerência?`)) return;
    setIsProcessing(true);
    try {
      await axios.post('http://localhost:8000/api/v1/consensus/gerenciamento/lock-all', {
        gerente_nome: usuarioSessao?.gerente_nome || "", // FastAPI Pydantic exige String
        acao
      });
      alert(`✅ Vendedores ${acao === 'Trancar' ? 'trancados' : 'destrancados'} com sucesso!`);
      fetchData();
    } catch (e: any) { 
      const erroBack = e.response?.data?.detail;
      const msgErro = typeof erroBack === 'string' ? erroBack : JSON.stringify(erroBack);
      alert(`Erro ao ${acao.toLowerCase()} os vendedores: ${msgErro}`); 
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
      await axios.post('http://localhost:8000/api/v1/consensus/gerenciamento/aprovar', { ajustes });
      alert("✅ Ajustes Sincronizados (Rateio Aplicado)!");
      setCelulasEditadas({});
      fetchData();
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao processar as alterações."); 
    } finally { 
      setIsProcessing(false); 
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

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2">
              {row.getCanExpand() ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-100 rounded">
                  {row.getIsExpanded() ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                </button>
              ) : <div className="w-6" />}
              
              <div className={`w-8 h-8 rounded-full flex items-center justify-center border ${isVendedor ? 'bg-indigo-50 border-indigo-100' : isCliente ? 'bg-amber-50 border-amber-100' : 'bg-slate-50 border-slate-100'}`}>
                {isVendedor ? <Users className="w-4 h-4 text-indigo-500" /> : isCliente ? <Store className="w-4 h-4 text-amber-500" /> : <Package className="w-4 h-4 text-slate-400" />}
              </div>
              <span className={`text-sm ${isVendedor ? 'font-black text-slate-800' : isCliente ? 'font-bold text-slate-700' : 'font-medium text-slate-600 truncate max-w-[200px]'}`}>
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
          
          const valorReal = edicao !== undefined ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const isChanged = edicao !== undefined;

          return (
            <div className="flex flex-col w-28 gap-1">
              <input
                type="number" value={valorReal === 0 ? '' : valorReal} placeholder="0"
                onChange={(e) => meta.updateCell(row.tipo, row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${isChanged ? 'bg-slate-800 border-slate-700 text-white shadow-md' : 'bg-gray-50 border-transparent text-gray-800 focus:bg-white focus:border-slate-300'}`}
              />
              <span className={`text-[10px] font-black text-center pr-1 tracking-tight ${isChanged ? 'text-slate-400' : 'text-emerald-600'}`}>
                {isChanged ? 'A calcular...' : formatMoeda(dadosMes?.receita || 0)}
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
          <button onClick={() => handleToggleLock(info.row.original.id)} className={`px-4 py-2 rounded-xl text-[9px] font-black uppercase tracking-widest transition-all border ${isFechado ? 'bg-white border-slate-200 text-slate-500 hover:bg-slate-50' : 'bg-slate-800 text-white border-slate-800 hover:bg-slate-700'}`}>
            {isFechado ? 'Reabrir' : 'Trancar'}
          </button>
        )
      }
    });

    return baseCols;
  }, [dadosBrutos]);

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

  if (isLoading) return <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-slate-400" /></div>;

  return (
    <div className="flex h-full w-full bg-[#f8fafc] overflow-hidden p-6 font-sans min-h-screen">
      <div className="flex-1 flex flex-col max-w-[1600px] mx-auto relative pb-24">
        
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <ShieldAlert className="w-8 h-8 text-rose-600" /> CENTRAL DE GERENCIAMENTO (S&OP)
            </h1>
            <p className="text-slate-500 font-medium text-sm mt-1 ml-11">Visão Hierárquica: Force o Rateio de cotas em qualquer nível e tranque telas.</p>
          </div>
          
          <div className="flex flex-col md:flex-row items-center gap-4 w-full lg:w-auto">
            {/* BOTÕES DE AÇÃO EM LOTE */}
            <div className="flex items-center gap-2 w-full md:w-auto">
              <button onClick={() => handleLockAll('Destrancar')} disabled={isProcessing} className="flex-1 md:flex-none flex items-center justify-center gap-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 px-5 py-3 rounded-xl text-[10px] font-black tracking-widest uppercase transition-all">
                  <Unlock className="w-4 h-4" /> Destrancar Todos
              </button>
              <button onClick={() => handleLockAll('Trancar')} disabled={isProcessing} className="flex-1 md:flex-none flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-5 py-3 rounded-xl text-[10px] font-black tracking-widest uppercase transition-all shadow-md">
                  <Lock className="w-4 h-4" /> Trancar Todos
              </button>
            </div>

            <div className="flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full lg:w-64">
              <Search className="w-5 h-5 text-slate-400" />
              <input 
                type="text" placeholder="Buscar vendedor..." value={busca} onChange={e => setBusca(e.target.value)}
                className="w-full bg-transparent py-3 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
              />
            </div>
          </div>
        </div>

        <div className="bg-white rounded-[45px] shadow-2xl border border-slate-100 overflow-hidden flex-1 overflow-y-auto relative">
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 bg-white/95 backdrop-blur-xl z-10 border-b border-slate-100 shadow-sm">
              {table.getHeaderGroups().map(hg => (
                <tr key={hg.id}>
                  {hg.headers.map(header => (
                    <th key={header.id} className={`px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-widest ${header.column.getCanSort() ? 'cursor-pointer hover:bg-slate-50 transition-colors' : ''}`} onClick={header.column.getToggleSortingHandler()}>
                      <div className="flex items-center gap-2">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getCanSort() && (
                          <span className="text-slate-300">
                            {{ asc: <ArrowUp className="w-4 h-4" />, desc: <ArrowDown className="w-4 h-4" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-50" />}
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
                <tr key={row.id} className={`border-b transition-colors ${row.original.tipo === 'vendedor' ? 'bg-white border-slate-100' : row.original.tipo === 'cliente' ? 'bg-slate-50/50 border-slate-100' : 'bg-slate-50/80 border-white hover:bg-slate-100'}`}>
                  {row.getVisibleCells().map(cell => (
                    <td key={cell.id} className="px-8 py-3">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>

            <tfoot className="sticky bottom-0 bg-slate-900 text-white z-20 shadow-[0_-20px_40px_rgba(0,0,0,0.2)]">
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