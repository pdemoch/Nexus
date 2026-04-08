import { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, Lock, Clock, Search, X, Store, Package, BarChart2 } from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function ConsensoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const isExecutivo = usuarioSessao?.funcao === 'Executivo';

  const [nivelHierarquia, setNivelHierarquia] = useState(isExecutivo ? 'vendedor' : 'regional');
  const [nomeResponsavel, setNomeResponsavel] = useState(isExecutivo ? usuarioSessao?.nome_vendedor : '');

  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  
  // NOVO ESTADO: Controla qual gráfico de Produto está aberto
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  
  const [isCicloFechado, setIsCicloFechado] = useState(false);
  const [isTopDownFechado, setIsTopDownFechado] = useState(false); 
  
  const [clienteFiltro, setClienteFiltro] = useState<string>("TODOS");
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  const [opcoesBusca, setOpcoesBusca] = useState<{vendedores: string[], regionais: string[]}>({vendedores: [], regionais: []});

  useEffect(() => {
    axios.get('/api/v1/consensus/filtros', { params: { gerente_nome: usuarioSessao?.gerente_nome } })
         .then(res => setOpcoesBusca(res.data)).catch(console.error);

    axios.get('/api/v1/consensus/status', { params: { origem: 'Check-Inicial' } })
         .then(res => setIsTopDownFechado(res.data.is_topdown_fechado)).catch(console.error);
  }, [usuarioSessao]);

  const fetchData = useCallback(async () => {
    if (!nomeResponsavel && !isExecutivo) { setIsLoading(false); return; }
    setIsLoading(true);
    try {
      const [statusRes, microRes] = await Promise.all([
        axios.get('/api/v1/consensus/status', { params: { origem: nomeResponsavel } }),
        axios.get('/api/v1/consensus/micro', { params: { nivel_hierarquia: nivelHierarquia, nome_responsavel: nomeResponsavel } })
      ]);
      setIsCicloFechado(statusRes.data.is_fechado);
      setIsTopDownFechado(statusRes.data.is_topdown_fechado); 
      setDadosBrutos(microRes.data.dados || []);
    } catch (e) { setDadosBrutos([]); } finally { setIsLoading(false); }
  }, [nivelHierarquia, nomeResponsavel, isExecutivo]);

  useEffect(() => { if (isExecutivo) fetchData(); }, [fetchData, isExecutivo]);

  const handleCongelarCiclo = async () => {
    if (!window.confirm("ATENÇÃO: Ao gravar e assinar o ciclo, o volume será distribuído para as filiais do cliente e a tela ficará bloqueada. Continuar?")) return;
    setIsProcessing(true);
    
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chaveMatriz, dados]: any) => {
      Object.entries(dados.meses).forEach(([mes, val]: any) => {
        ajustes.push({ nivel: dados.tipo, chave: chaveMatriz, mes_projetado: mes, novo_volume: val.novo_volume === '' ? 0 : val.novo_volume, pmv_aplicado: val.pmv_aplicado });
      });
    });

    try {
      await axios.post(`/api/v1/consensus/micro/congelar?nome_responsavel=${nomeResponsavel}`, { origem_ajuste: "Nexus UI", ajustes });
      alert("🔒 Ciclo Comercial gravado com sucesso!");
      setCelulasEditadas({});
      fetchData();
    } catch (e: any) { alert("Erro ao gravar: " + (e.response?.data?.detail || e.message)); } finally { setIsProcessing(false); }
  };

  const clientesUnicos = useMemo(() => [...new Set(dadosBrutos.map(d => d.razaosocial).filter(Boolean))].sort(), [dadosBrutos]);
  const dadosFiltrados = useMemo(() => clienteFiltro === "TODOS" ? dadosBrutos : dadosBrutos.filter(d => d.razaosocial === clienteFiltro), [dadosBrutos, clienteFiltro]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number }> = {};
    dadosFiltrados.forEach(row => { row.meses.forEach((m: any) => { totais[m.mes_banco] = { vol: 0, fat: 0 }; }); });
    dadosFiltrados.forEach(row => {
      row.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[row.chave_matriz]?.meses?.[mes.mes_banco];
        const volumeFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : mes.vol_ajustado));
        totais[mes.mes_banco].vol += volumeFinal;
        totais[mes.mes_banco].fat += volumeFinal * mes.pmv; 
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) {
      setChartExpanded(null); // Fecha se clicar de novo
      return;
    }
    
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/micro/grafico', { params: { chave_matriz: chave, nivel_hierarquia: nivelHierarquia, nome_responsavel: nomeResponsavel } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const isTelaBloqueada = isCicloFechado || !isTopDownFechado;
  const qtdEdicoes = Object.keys(celulasEditadas).length;

  const columns = useMemo(() => {
    if (dadosBrutos.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Razão Social / Produto', accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const isCliente = row.original.tipo === 'cliente';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2">
              {isCliente ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-100 rounded">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5 text-slate-500" /> : <ChevronRight className="w-5 h-5 text-slate-400" />}
                </button>
              ) : (
                <button onClick={() => toggleChart(row)} className={`p-1 rounded transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 text-indigo-600' : 'hover:bg-slate-100 text-slate-400'}`}>
                  <BarChart2 className="w-5 h-5" />
                </button>
              )}
              
              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${isCliente ? 'bg-indigo-50 border-indigo-100' : 'bg-slate-50 border-slate-100'}`}>
                {isCliente ? <Store className="w-4 h-4 text-indigo-500" /> : <Package className="w-4 h-4 text-slate-400" />}
              </div>
              <span className={`text-sm ${isCliente ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[300px]'}`}>
                {info.getValue()}
              </span>
            </div>
          )
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
          const valorInteiro = Math.round(Number(valorReal));
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const isChanged = valorInteiro !== baseIA;
          const faturamentoPrevisto = valorInteiro * (dadosMes?.pmv || 0);

          return (
            <div className="flex flex-col w-28 gap-1">
              <span className="text-[9px] text-gray-400 font-black opacity-60 uppercase text-center tracking-widest">Ref. IA: {formatVolume(baseIA)}</span>
              <input
                type="number" disabled={isTelaBloqueada} value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                onChange={(e) => meta.updateCell(row.tipo, row.chave_matriz, m.mes_banco, e.target.value, (dadosMes?.pmv || 0))}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${isTelaBloqueada ? 'cursor-not-allowed opacity-50 bg-gray-100 border-gray-200 text-gray-500' : isChanged ? 'bg-blue-50 border-blue-200 text-blue-700 shadow-sm' : 'bg-gray-50 border-transparent text-gray-700 focus:bg-white focus:border-gray-200'}`}
              />
              <span className="text-[10px] font-black text-emerald-600 text-center pr-1 tracking-tight">{formatMoeda(faturamentoPrevisto)}</span>
            </div>
          );
        }
      });
    });

    return baseCols;
  }, [dadosBrutos, isTelaBloqueada, chartExpanded]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (tipo: string, chave: string, mes: string, val: string, pmv: number) => {
        if (isTelaBloqueada) return;
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { tipo: tipo, meses: { ...(prev[chave]?.meses || {}), [mes]: { novo_volume: finalV, pmv_aplicado: pmv } } } }));
      }
    }
  });

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative pb-24">
        
        {/* CABEÇALHOS OMISSOS PARA POUPAR CARACTERES (SÃO OS MESMOS) */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
          <div className="flex flex-col gap-4">
            <h1 className="text-2xl font-black text-gray-900 flex items-center gap-2 tracking-tighter">
              <TrendingUp className="w-7 h-7 text-blue-600" /> ARENA DE CONSENSO
            </h1>
            {!isExecutivo && (
              <div className="flex flex-col md:flex-row md:items-center gap-3 bg-slate-50 p-2 rounded-2xl border border-slate-200 shadow-sm w-max">
                <select value={nivelHierarquia} onChange={e => { setNivelHierarquia(e.target.value); setNomeResponsavel(''); setDadosBrutos([]); }} className="bg-transparent text-sm font-bold text-slate-700 outline-none cursor-pointer pl-2">
                  <option value="regional">Regional</option><option value="vendedor">Vendedor</option>
                </select>
                <div className="hidden md:block w-px h-6 bg-slate-200"></div>
                <input list="lista-responsaveis" type="text" value={nomeResponsavel} onChange={e => setNomeResponsavel(e.target.value.toUpperCase())} placeholder={`Buscar...`} className="bg-transparent text-sm font-bold text-slate-700 outline-none w-56 md:w-64 placeholder:text-slate-400 pl-2"/>
                <datalist id="lista-responsaveis">{(nivelHierarquia === 'vendedor' ? opcoesBusca.vendedores : opcoesBusca.regionais).map(opt => <option key={opt} value={opt} />)}</datalist>
                <button onClick={() => fetchData()} disabled={!nomeResponsavel} className="flex items-center gap-1 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-md ml-auto disabled:opacity-50">
                  {isLoading ? <Loader2 className="w-3 h-3 animate-spin"/> : <Search className="w-3 h-3" />} Buscar
                </button>
              </div>
            )}
          </div>
          {(nomeResponsavel || isExecutivo) && (
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-3 bg-gray-50 px-5 py-3 rounded-2xl border border-gray-100 h-full">
                <Filter className="w-4 h-4 text-gray-400" />
                <select className="bg-transparent text-sm font-bold text-gray-700 outline-none w-56 cursor-pointer" value={clienteFiltro} onChange={(e) => setClienteFiltro(e.target.value)}>
                  <option value="TODOS">TODOS OS CLIENTES</option>{clientesUnicos.map(c => <option key={c as string} value={c as string}>{c as string}</option>)}
                </select>
              </div>
              {isCicloFechado ? (
                  <div className="flex items-center gap-2 bg-slate-800 text-white px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-slate-800/20 h-full"><Check className="w-5 h-5 text-emerald-400" /> Ciclo Congelado</div>
              ) : (
                  <div className="flex items-center gap-3 h-full">
                    {qtdEdicoes > 0 && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                    <button onClick={handleCongelarCiclo} disabled={isProcessing} className="flex items-center gap-2 bg-rose-500 hover:bg-rose-400 text-white px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-rose-500/30 transition-all h-full">
                        {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : qtdEdicoes > 0 ? 'Gravar e Ratear Ciclo' : 'Gravar Ciclo Definitivo'}
                    </button>
                  </div>
              )}
            </div>
          )}
        </div>

        <div className="bg-white rounded-[40px] shadow-2xl border border-gray-100 overflow-hidden relative">
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 bg-white/95 backdrop-blur-xl z-10 border-b border-gray-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className="px-8 py-6 text-[10px] font-black text-gray-400 uppercase tracking-widest">
                         {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-gray-50 transition-colors ${row.original.tipo === 'cliente' ? 'bg-slate-50/50 hover:bg-slate-100' : 'bg-white hover:bg-blue-50/30'}`}>
                      {row.getVisibleCells().map(cell => <td key={cell.id} className="px-8 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
                    </tr>
                    
                    {/* EXPANSÃO DO GRÁFICO NO NÍVEL DO PRODUTO */}
                    {chartExpanded === row.original.chave_matriz && row.original.tipo === 'produto' && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-indigo-50/20 p-8 border-b border-gray-100">
                          <div className="bg-white rounded-[40px] p-8 shadow-inner border border-indigo-100 animate-in fade-in duration-500">
                            <h3 className="text-lg font-black text-gray-900 uppercase tracking-tighter mb-6">
                               Detalhe: <span className="text-indigo-600">{row.original.nome}</span>
                            </h3>

                            <div className="h-[200px] w-full -ml-4">
                              {loadingGrafico === row.original.chave_matriz ? (
                                <div className="h-full flex items-center justify-center text-blue-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                              ) : (
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
                                    
                                    {/* A MÁGICA: A LINHA DO CICLO ANTERIOR AQUI */}
                                    <Line type="monotone" dataKey="CicloAnterior" name="Proposta Mês Passado" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                                    
                                    <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={4} dot={{r: 3, fill: '#0f172a'}} connectNulls={false} />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                    <Line type="monotone" dataKey="Consenso" name="Visão Comercial" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#fff'}} connectNulls={false} />
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
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col"><span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Meta de Receita</span><span className="font-bold text-sm text-white">TOTAL COMERCIAL</span></div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white">{formatVolume(totaisGerais[m]?.vol || 0)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(totaisGerais[m]?.fat || 0)}</span>
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