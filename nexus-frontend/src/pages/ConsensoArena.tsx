import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  getExpandedRowModel,
  getSortedRowModel,
  SortingState
} from '@tanstack/react-table';
import { Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronUp, ArrowUpDown, ArrowUp, ArrowDown, Lock, Clock, Search } from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));

export default function ConsensoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const isExecutivo = usuarioSessao?.funcao === 'Executivo';

  const [nivelHierarquia, setNivelHierarquia] = useState(isExecutivo ? 'vendedor' : 'regional');
  const [nomeResponsavel, setNomeResponsavel] = useState(isExecutivo ? usuarioSessao?.nome_vendedor : '');

  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  
  const [isCicloFechado, setIsCicloFechado] = useState(false);
  const [isTopDownFechado, setIsTopDownFechado] = useState(false); 
  
  const [clienteFiltro, setClienteFiltro] = useState<string>("TODOS");
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [opcoesBusca, setOpcoesBusca] = useState<{vendedores: string[], regionais: string[]}>({vendedores: [], regionais: []});

  useEffect(() => {
    axios.get('http://localhost:8000/api/v1/consensus/filtros', {
      params: { gerente_nome: usuarioSessao?.gerente_nome } 
    }).then(res => setOpcoesBusca(res.data)).catch(console.error);
  }, [usuarioSessao]);

  const fetchData = useCallback(async () => {
    if (!nomeResponsavel && !isExecutivo) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    
    try {
      const [statusRes, microRes] = await Promise.all([
        axios.get('http://localhost:8000/api/v1/consensus/status', { params: { origem: nomeResponsavel } }),
        axios.get('http://localhost:8000/api/v1/consensus/micro', { params: { nivel_hierarquia: nivelHierarquia, nome_responsavel: nomeResponsavel } })
      ]);
      setIsCicloFechado(statusRes.data.is_fechado);
      setIsTopDownFechado(statusRes.data.is_topdown_fechado); 
      setDadosBrutos(microRes.data.dados || []);
    } catch (e) { 
      console.error("Erro", e); 
      setDadosBrutos([]); 
    } finally { 
      setIsLoading(false); 
    }
  }, [nivelHierarquia, nomeResponsavel, isExecutivo]);

  useEffect(() => { if (isExecutivo) fetchData(); }, [fetchData, isExecutivo]);

  const handleCongelarCiclo = async () => {
    if (!window.confirm("ATENÇÃO: Ao gravar e assinar o ciclo, não poderá fazer mais alterações neste mês. Deseja continuar?")) return;
    
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chave, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ chave_matriz: chave, mes_projetado: mes, novo_volume: val.novo_volume === '' ? 0 : val.novo_volume, pmv_aplicado: val.pmv_aplicado });
      });
    });

    try {
      await axios.post(`http://localhost:8000/api/v1/consensus/micro/congelar?nome_responsavel=${nomeResponsavel}`, { origem_ajuste: "Nexus UI", ajustes });
      alert("🔒 Ciclo Bottom-Up gravado e congelado com sucesso!");
      setCelulasEditadas({});
      fetchData();
    } catch (e: any) { 
      alert("Erro ao gravar e fechar o ciclo: " + (e.response?.data?.detail || e.message)); 
    }
  };

  const clientesUnicos = useMemo(() => [...new Set(dadosBrutos.map(d => d.razaosocial).filter(Boolean))].sort(), [dadosBrutos]);
  const dadosFiltrados = useMemo(() => clienteFiltro === "TODOS" ? dadosBrutos : dadosBrutos.filter(d => d.razaosocial === clienteFiltro), [dadosBrutos, clienteFiltro]);

  const totaisFaturamento = useMemo(() => {
    const totais: Record<string, number> = {};
    dadosFiltrados.forEach(row => { row.meses.forEach((m: any) => { totais[m.mes_banco] = 0; }); });
    dadosFiltrados.forEach(row => {
      row.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[row.chave_matriz]?.[mes.mes_banco];
        const volumeFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : mes.vol_ajustado));
        totais[mes.mes_banco] += volumeFinal * mes.pmv; 
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const toggleRow = async (row: any) => {
    const isExpanding = !row.getIsExpanded();
    const chave = row.original.chave_matriz;
    
    if (isExpanding && !dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('http://localhost:8000/api/v1/consensus/micro/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
    row.toggleExpanded();
  };

  const isTelaBloqueada = isCicloFechado || !isTopDownFechado;

  const columns = useMemo(() => {
    if (dadosBrutos.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'expander', enableSorting: false, header: () => null,
        cell: ({ row }: any) => (
          <button onClick={() => toggleRow(row)} className="p-2 hover:bg-blue-50 rounded-xl transition-all">
            {row.getIsExpanded() ? <ChevronUp className="w-5 h-5 text-blue-600" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
          </button>
        ),
      },
      {
        id: 'info', header: 'Produto / Razão Social', accessorFn: (row: any) => row.descricao,
        cell: (info: any) => (
          <div className="flex flex-col py-1">
            <span className="font-bold text-gray-900 text-sm truncate max-w-[320px]">{info.row.original.descricao}</span>
            <span className="text-[10px] text-gray-400 font-black uppercase tracking-tighter">{info.row.original.razaosocial}</span>
          </div>
        )
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
          const edicao = meta.celulasEditadas[row.chave_matriz]?.[m.mes_banco];
          
          const valorReal = edicao !== undefined ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const isChanged = valorInteiro !== baseIA;

          return (
            <div className="flex flex-col w-24 gap-1">
              <span className="text-[9px] text-gray-400 font-black opacity-60 uppercase text-center tracking-widest">
                Ref. IA: {baseIA.toLocaleString('pt-BR')}
              </span>
              <input
                type="number" disabled={isTelaBloqueada} value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                onChange={(e) => meta.updateCell(row.chave_matriz, m.mes_banco, e.target.value, (dadosMes?.pmv || 0))}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all
                  ${isTelaBloqueada ? 'cursor-not-allowed opacity-50 bg-gray-100 border-gray-200 text-gray-500' 
                  : isChanged ? 'bg-blue-50 border-blue-200 text-blue-700 shadow-sm' : 'bg-gray-50 border-transparent text-gray-700 focus:bg-white focus:border-gray-200'}`}
              />
            </div>
          );
        }
      });
    });

    return baseCols;
  }, [dadosBrutos, isTelaBloqueada]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (chave: string, mes: string, val: string, pmv: number) => {
        if (isTelaBloqueada) return;
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV, pmv_aplicado: pmv } } }));
      }
    }
  });

  if (isLoading && isExecutivo) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">A iniciar Consenso de Vendas...</span>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full bg-[#f8fafc] overflow-hidden p-6 font-sans min-h-screen">
      <div className="flex-1 flex flex-col relative pb-20">
        
        {(!isTopDownFechado && (nomeResponsavel || isExecutivo)) && (
          <div className="mb-4 bg-orange-100 border border-orange-200 text-orange-800 p-4 rounded-[24px] flex items-center gap-3 shadow-sm">
            <div className="bg-orange-200 p-2 rounded-full"><Clock className="w-5 h-5 text-orange-700" /></div>
            <div>
              <h4 className="font-black text-sm uppercase tracking-widest">Aguardando Diretoria (Top-Down)</h4>
              <p className="text-sm font-medium opacity-80">A tela está em modo leitura. Só poderá fazer os seus ajustes de Consenso após o fechamento da meta executiva.</p>
            </div>
          </div>
        )}

        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
          <div className="flex flex-col gap-4">
            <h1 className="text-2xl font-black text-gray-900 flex items-center gap-2 tracking-tighter">
              <TrendingUp className="w-7 h-7 text-blue-600" /> ARENA DE CONSENSO
            </h1>
            
            {!isExecutivo && (
              <div className="flex flex-col md:flex-row md:items-center gap-3 bg-slate-50 p-2 rounded-2xl border border-slate-200 shadow-sm w-max">
                <select 
                  value={nivelHierarquia} 
                  onChange={e => { setNivelHierarquia(e.target.value); setNomeResponsavel(''); setDadosBrutos([]); }}
                  className="bg-transparent text-sm font-bold text-slate-700 outline-none cursor-pointer pl-2"
                >
                  <option value="regional">Regional</option>
                  <option value="vendedor">Vendedor</option>
                </select>
                <div className="hidden md:block w-px h-6 bg-slate-200"></div>
                
                <input 
                  list="lista-responsaveis" type="text" value={nomeResponsavel}
                  onChange={e => setNomeResponsavel(e.target.value.toUpperCase())}
                  placeholder={`Buscar ${nivelHierarquia}...`}
                  className="bg-transparent text-sm font-bold text-slate-700 outline-none w-56 md:w-64 placeholder:text-slate-400 pl-2"
                />
                <datalist id="lista-responsaveis">
                  {(nivelHierarquia === 'vendedor' ? opcoesBusca.vendedores : opcoesBusca.regionais).map(opt => <option key={opt} value={opt} />)}
                </datalist>

                <button 
                  onClick={() => fetchData()} disabled={!nomeResponsavel}
                  className="flex items-center gap-1 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-md ml-auto disabled:opacity-50"
                >
                  {isLoading ? <Loader2 className="w-3 h-3 animate-spin"/> : <Search className="w-3 h-3" />}
                  Buscar
                </button>
              </div>
            )}
          </div>

          {(nomeResponsavel || isExecutivo) && (
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-3 bg-gray-50 px-5 py-3 rounded-2xl border border-gray-100 h-full">
                <Filter className="w-4 h-4 text-gray-400" />
                <select className="bg-transparent text-sm font-bold text-gray-700 outline-none w-56 cursor-pointer" 
                  value={clienteFiltro} onChange={(e) => setClienteFiltro(e.target.value)}>
                  <option value="TODOS">TODOS OS CLIENTES</option>
                  {clientesUnicos.map(c => <option key={c as string} value={c as string}>{c as string}</option>)}
                </select>
              </div>

              {isCicloFechado ? (
                  <div className="flex items-center gap-2 bg-slate-800 text-white px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-slate-800/20 h-full">
                      <Check className="w-5 h-5 text-emerald-400" /> Ciclo Congelado
                  </div>
              ) : !isTopDownFechado ? (
                  <div className="flex items-center gap-2 bg-orange-100 text-orange-700 px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase border border-orange-200 cursor-not-allowed h-full">
                      <Lock className="w-4 h-4" /> Aguardando Top-Down
                  </div>
              ) : (
                  <button onClick={handleCongelarCiclo} className="flex items-center gap-2 bg-rose-500 hover:bg-rose-400 text-white px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-rose-500/30 transition-all h-full">
                      Gravar Ciclo Definitivo
                  </button>
              )}
            </div>
          )}
        </div>

        <div className="bg-white rounded-[45px] shadow-2xl border border-gray-100 overflow-hidden flex-1 overflow-y-auto relative">
          {dadosFiltrados.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-slate-400 gap-4 py-20">
              <Search className="w-12 h-12 opacity-20" />
              <p className="font-bold tracking-widest uppercase text-sm text-center px-4">
                {isExecutivo ? "Nenhum dado encontrado para a sua carteira." : "Selecione um vendedor ou regional e clique em Buscar."}
              </p>
            </div>
          ) : (
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 bg-white/95 backdrop-blur-xl z-10 border-b border-gray-100 shadow-sm">
              {table.getHeaderGroups().map(hg => (
                <tr key={hg.id}>
                  {hg.headers.map(header => (
                    <th key={header.id} className={`px-8 py-6 text-[10px] font-black text-gray-400 uppercase tracking-widest ${header.column.getCanSort() ? 'cursor-pointer select-none hover:bg-blue-50/50 transition-colors group' : ''}`} onClick={header.column.getToggleSortingHandler()}>
                      <div className="flex items-center gap-2">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getCanSort() && (
                          <span className="text-gray-300 transition-colors">
                            {{ asc: <ArrowUp className="w-4 h-4 text-blue-500" />, desc: <ArrowDown className="w-4 h-4 text-blue-500" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-0 group-hover:opacity-100 transition-opacity" />}
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
                  <tr className={`border-b border-gray-50 transition-colors ${row.getIsExpanded() ? 'bg-blue-50/40' : 'hover:bg-blue-50/10'}`}>
                    {row.getVisibleCells().map(cell => <td key={cell.id} className="px-8 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
                  </tr>
                  
                  {row.getIsExpanded() && (
                    <tr>
                      <td colSpan={table.getAllColumns().length} className="bg-gray-50/50 p-8 border-b border-gray-100">
                        <div className="bg-white rounded-[40px] p-8 shadow-inner border border-gray-100 animate-in fade-in duration-500">
                          <div className="flex justify-between items-center mb-6">
                             <div className="flex gap-4">
                               <h3 className="text-lg font-black text-gray-900 uppercase tracking-tighter">
                                 Histórico Específico (Cliente: <span className="text-blue-600">{row.original.razaosocial}</span>)
                               </h3>
                             </div>
                          </div>

                          <div className="h-[200px] w-full -ml-4">
                            {loadingGrafico === row.original.chave_matriz ? (
                              <div className="h-full flex items-center justify-center text-blue-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                            ) : (
                              <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={
                                    (dadosGraficoCache[row.original.chave_matriz] || []).map((p: any) => {
                                        const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                        const edicao = celulasEditadas[row.original.chave_matriz]?.[p.data_iso];
                                        const valConsenso = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                        return { ...p, Consenso: valConsenso !== null ? valConsenso : p.Consenso };
                                    })
                                }>
                                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                  <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900}} axisLine={false} tickLine={false} />
                                  <YAxis tick={{fontSize: 10}} axisLine={false} tickLine={false} />
                                  <Tooltip contentStyle={{borderRadius: '20px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                  <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900'}} />
                                  
                                  <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={4} dot={{r: 3, fill: '#0f172a'}} connectNulls={false} />
                                  <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                  <Line type="monotone" dataKey="Consenso" name="Consenso Bottom-Up" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#fff'}} connectNulls={false} />
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

            <tfoot className="sticky bottom-0 bg-slate-900 text-white z-20 shadow-[0_-20px_40px_rgba(0,0,0,0.2)]">
              <tr>
                {table.getHeaderGroups()[0].headers.map(header => {
                  if (header.id === 'expander') return <td key={header.id} className="px-8 py-5"></td>;
                  if (header.id === 'info') return (
                    <td key={header.id} className="px-8 py-5 text-right">
                      <div className="flex flex-col">
                        <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Meta de Receita</span>
                        <span className="font-bold text-sm text-white">TOTAL S&OP (R$)</span>
                      </div>
                    </td>
                  );
                  
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
          )}
        </div>
      </div>

      {!isTelaBloqueada && Object.keys(celulasEditadas).length > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
          <div className="bg-blue-600 text-white px-12 py-5 rounded-full shadow-[0_20px_50px_rgba(37,99,235,0.4)] flex items-center gap-10 border border-blue-500 animate-in slide-in-from-bottom-10">
            <div className="text-sm font-black tracking-widest uppercase flex items-center gap-3">
              <span className="bg-white text-blue-600 px-3 py-1 rounded-lg text-xl">{Object.keys(celulasEditadas).length}</span> SKUs
            </div>
            <div className="h-8 w-[2px] bg-blue-400/50" />
            <button onClick={() => setCelulasEditadas({})} className="text-xs font-black text-blue-200 hover:text-white transition tracking-widest uppercase">Descartar</button>
            <button onClick={handleCongelarCiclo} className="bg-white text-blue-700 hover:bg-gray-50 px-8 py-3 rounded-full text-xs font-black transition-all shadow-lg tracking-widest uppercase">Gravar e Congelar</button>
          </div>
        </div>
      )}
    </div>
  );
}