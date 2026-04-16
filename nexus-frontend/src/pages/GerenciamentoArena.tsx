import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getSortedRowModel, getExpandedRowModel, SortingState
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

  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [isTopDownFechado, setIsTopDownFechado] = useState(false);
  const [busca, setBusca] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statusMacro, gerenciaRes] = await Promise.all([
        axios.get('/api/v1/consensus/macro/status').catch(() => ({ data: { is_topdown_fechado: false } })),
        axios.get('/api/v1/consensus/gerenciamento/vendedores', { params: { gerente_nome: usuarioSessao?.gerente_nome } })
      ]);
      setIsTopDownFechado(statusMacro.data.is_topdown_fechado);
      setDadosBrutos(gerenciaRes.data.dados || []);
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [usuarioSessao]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSalvarAjustes = async () => {
    if (!window.confirm("Deseja aplicar estas alterações na carteira dos vendedores afetados?")) return;
    setIsProcessing(true);
    
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chave, dados]: any) => {
      Object.entries(dados.meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
          nivel: dados.tipo, 
          chave: chave, 
          mes_projetado: mes, 
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
    const b = busca.toLowerCase();
    return dadosBrutos.filter(v => v.nome.toLowerCase().includes(b));
  }, [dadosBrutos, busca]);

  // =========================================================================
  // CÁLCULO DE TOTAIS: Reflete 100% o que vem do Banco de Dados
  // =========================================================================
  const totaisGerais = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number, ia: number, td: number }> = {};
    dadosFiltrados.forEach(v => {
      v.meses.forEach((m: any) => { totais[m.mes_banco] = { vol: 0, fat: 0, ia: 0, td: 0 }; });
    });
    
    dadosFiltrados.forEach(vendedor => {
      vendedor.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[vendedor.chave_matriz]?.meses?.[mes.mes_banco];
        const isPendente = edicao !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicao.novo_volume : mes.vol_ajustado));
        
        // Simulação matemática SE estiver editando. Caso contrário, Receita Real!
        const pmvSimulado = mes.vol_ajustado > 0 ? (mes.receita / mes.vol_ajustado) : 0;
        const faturamento = isPendente ? (volumeFinal * pmvSimulado) : mes.receita;

        totais[mes.mes_banco].vol += volumeFinal;
        totais[mes.mes_banco].fat += faturamento;
        totais[mes.mes_banco].ia += mes.vol_ia || 0;
        totais[mes.mes_banco].td += mes.vol_td || 0;
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

    const baseCols: any[] = [
      {
        id: 'nome', header: 'Hierarquia S&OP (Vendedor > Cliente > Produto)',
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
                <button onClick={() => handleToggleLock(nome)} className={`absolute left-0 p-1.5 rounded-lg border transition-all ${isFechado ? 'bg-rose-50 border-rose-200 text-rose-500 hover:bg-rose-100' : 'bg-emerald-50 border-emerald-200 text-emerald-600 hover:bg-emerald-100'}`} title={isFechado ? 'Destrancar Carteira' : 'Trancar Carteira'}>
                  {isFechado ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
                </button>
              )}
              
              <div className={`${isVendedor ? 'ml-8' : ''} flex items-center gap-3`}>
                {!isVendedor && !isCliente ? (
                  <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors ${chartExpanded === chave_matriz ? 'bg-blue-100 text-blue-600' : 'hover:bg-slate-100 text-slate-400'}`}>
                    <BarChart2 className="w-4 h-4" />
                  </button>
                ) : (
                  <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-100 rounded-lg transition-colors">
                    {row.getIsExpanded() ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                  </button>
                )}

                <div className={`w-8 h-8 rounded-xl flex items-center justify-center border shadow-sm ${isVendedor ? 'bg-blue-50 border-blue-100' : isCliente ? 'bg-indigo-50 border-indigo-100' : 'bg-slate-50 border-slate-200'}`}>
                  {isVendedor ? <Users className="w-4 h-4 text-blue-500" /> : isCliente ? <Store className="w-4 h-4 text-indigo-500" /> : <Package className="w-4 h-4 text-slate-400" />}
                </div>

                <div className="flex flex-col">
                  <span className={`text-sm truncate max-w-[250px] ${isVendedor ? 'font-black text-slate-900 tracking-tighter' : isCliente ? 'font-bold text-slate-700' : 'font-medium text-slate-500'}`}>{nome}</span>
                  {isVendedor && <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{isFechado ? '🔒 Assinado' : '✏️ Em Ajuste'}</span>}
                </div>
              </div>
            </div>
          );
        }
      }
    ];

    const mesesMap = new Map();
    dadosBrutos.forEach(r => r.meses.forEach((m: any) => mesesMap.set(m.mes_banco, m)));
    
    Array.from(mesesMap.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)).forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_ajustado || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const isVendedor = row.tipo === 'vendedor';
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.chave_matriz]?.meses?.[m.mes_banco];
          
          const isPendente = edicao !== undefined;
          const valorReal = isPendente ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const baseTD = Math.round(Number(dadosMes?.vol_td || 0));
          const isChanged = valorInteiro !== baseTD;
          const isFechado = row.status === 'Fechado' && isVendedor;

          // A Mágica do PMV Dinâmico x Real
          const pmvSimulado = dadosMes?.vol_ajustado > 0 ? (dadosMes.receita / dadosMes.vol_ajustado) : 0;
          const fatExibicao = isPendente ? (valorInteiro * pmvSimulado) : (dadosMes?.receita || 0);

          return (
            <div className="flex flex-col items-center justify-center min-w-[140px] group">
              <div className="flex items-center gap-4 w-full px-2 mb-1">
                 <div className="flex flex-col items-center flex-1">
                    <span className="text-[8px] font-black uppercase text-slate-400 tracking-tighter">Diretoria</span>
                    <span className="text-[10px] font-bold text-slate-600">{formatVolume(baseTD)}</span>
                 </div>
              </div>
              
              <div className="w-full relative">
                <input
                  type="number" disabled={isFechado} value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                  onChange={(e) => meta.updateCell(row.tipo, row.chave_matriz, m.mes_banco, e.target.value)}
                  className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                    ${isFechado ? 'cursor-not-allowed bg-slate-100 border-slate-200 text-slate-500 opacity-60' : isChanged ? 'bg-amber-50 border-amber-300 text-amber-800 shadow-sm' : 'bg-white border-slate-200 text-slate-800 focus:border-blue-400'}`}
                />
              </div>
              <span className="text-[10px] font-black text-emerald-600 mt-1 tracking-tight">{formatMoeda(fatExibicao)}</span>
            </div>
          );
        }
      });
    });

    return baseCols;
  }, [dadosBrutos, chartExpanded]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getExpandedRowModel: getExpandedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (tipo: string, chave: string, mes: string, val: string) => {
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { tipo, meses: { ...(prev[chave]?.meses || {}), [mes]: { novo_volume: finalV } } } }));
      }
    }
  });

  if (isLoading) return <div className="h-screen flex items-center justify-center font-sans"><Loader2 className="w-8 h-8 animate-spin text-blue-500" /></div>;

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-24">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {!isTopDownFechado && (
          <div className="mb-6 bg-orange-100 border border-orange-200 text-orange-800 p-4 rounded-[24px] flex items-center gap-3 shadow-sm">
            <div className="bg-orange-200 p-2 rounded-full"><ShieldAlert className="w-5 h-5 text-orange-700" /></div>
            <div>
              <h4 className="font-black text-sm uppercase tracking-widest">Aguardando Top-Down</h4>
              <p className="text-sm font-medium opacity-80">A diretoria ainda não congelou a meta base. Os vendedores não devem iniciar as revisões.</p>
            </div>
          </div>
        )}

        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100">
          <div>
            <h1 className="text-3xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Users className="w-8 h-8 text-blue-600" /> VISÃO GERENCIAL
            </h1>
            <p className="text-slate-500 font-medium mt-1">Supervisão, controle e aprovação do consenso da equipa comercial.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={() => handleLockAll('Trancar')} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase transition border border-slate-200"><Lock className="w-4 h-4"/> Trancar Todos</button>
            <button onClick={() => handleLockAll('Destrancar')} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase transition border border-slate-200"><Unlock className="w-4 h-4"/> Destrancar Todos</button>
          </div>
        </div>

        <div className="mb-6 bg-white p-2 rounded-2xl shadow-sm border border-slate-100 flex items-center gap-3 w-max max-w-full">
           <div className="flex items-center gap-2 px-4 py-2">
             <Search className="w-5 h-5 text-slate-400" />
             <input type="text" placeholder="Buscar Vendedor..." value={busca} onChange={e => setBusca(e.target.value)} className="w-64 bg-transparent text-sm font-bold text-slate-700 outline-none placeholder:text-slate-400" />
           </div>
        </div>

        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative">
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 bg-white/95 backdrop-blur-xl z-10 border-b-2 border-slate-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className="px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.depth === 0 ? 'bg-slate-50/50' : row.depth === 1 ? 'hover:bg-indigo-50/20' : 'hover:bg-slate-100/50'}`}>
                      {row.getVisibleCells().map(cell => <td key={cell.id} className="px-8 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
                    </tr>

                    {chartExpanded === row.original.chave_matriz && row.original.tipo === 'produto' && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-50 p-8 border-b border-slate-100">
                          <div className="bg-white rounded-[32px] p-6 shadow-sm border border-slate-200 h-[250px]">
                            <ResponsiveContainer width="100%" height="100%">
                                  <LineChart data={
                                      (dadosGraficoCache[row.original.chave_matriz] || []).map((p: any) => {
                                          const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                          const edicao = celulasEditadas[row.original.chave_matriz]?.meses?.[p.data_iso];
                                          const valConsenso = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                          return { ...p, Consenso: valConsenso !== null ? valConsenso : p.Consenso };
                                      })
                                  }>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10}} axisLine={false} tickLine={false} />
                                    <Tooltip contentStyle={{borderRadius: '20px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900'}} />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="5 5" dot={false} />
                                    <Line type="monotone" dataKey="Consenso" name="Visão Vendedor" stroke="#3b82f6" strokeWidth={4} dot={{r: 4}} />
                                  </LineChart>
                            </ResponsiveContainer>
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
                      <td key={header.id} className="px-8 py-5 text-right font-black uppercase text-sm">TOTAL EQUIPA COMERCIAL</td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5 text-center">
                          <div className="flex flex-col items-center">
                            <span className="font-black text-white">{formatVolume(totaisGerais[m]?.vol || 0)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight">{formatMoeda(totaisGerais[m]?.fat || 0)}</span>
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
            <div className="bg-amber-500 text-white px-10 py-5 rounded-full shadow-[0_20px_50px_rgba(245,158,11,0.4)] flex items-center gap-8 border border-amber-400 animate-in slide-in-from-bottom-10">
              <div className="text-sm font-black tracking-widest uppercase flex items-center gap-3">
                <span className="bg-white text-amber-600 px-3 py-1 rounded-lg text-lg">{Object.keys(celulasEditadas).length}</span> Ajustes Manuais
              </div>
              <div className="h-8 w-[2px] bg-amber-400/50" />
              <button onClick={() => setCelulasEditadas({})} className="text-xs font-black text-amber-100 hover:text-white transition tracking-widest uppercase"><X className="w-4 h-4 inline mr-1"/>Descartar</button>
              <button onClick={handleSalvarAjustes} disabled={isProcessing} className="bg-slate-900 text-white hover:bg-black px-8 py-3 rounded-full text-xs font-black tracking-widest uppercase transition shadow-lg flex items-center gap-2">
                {isProcessing ? <Loader2 className="w-4 h-4 animate-spin"/> : <Save className="w-4 h-4"/>} Sobrescrever Visão
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}