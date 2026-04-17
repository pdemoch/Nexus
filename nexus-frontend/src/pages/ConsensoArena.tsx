import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronUp, ArrowUpDown, ArrowUp, ArrowDown, 
  Lock, Search, X, Store, Package, Download, BrainCircuit
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function ConsensoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const isExecutivo = usuarioSessao?.funcao === 'Executivo';

  const [nivelHierarquia, setNivelHierarquia] = useState(isExecutivo ? 'vendedor' : 'regional');
  const [nomeResponsavel, setNomeResponsavel] = useState(isExecutivo ? usuarioSessao?.nome_vendedor : '');

  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [busca, setBusca] = useState('');

  const fetchData = useCallback(async () => {
    if (!nomeResponsavel) return;
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/micro', {
        params: { nivel: nivelHierarquia, chave: nomeResponsavel }
      });
      setDadosBrutos(res.data.dados || []);
      setCelulasEditadas({});
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [nivelHierarquia, nomeResponsavel]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleExportExcel = () => {
    if (dadosFiltrados.length === 0) return alert("Não há dados na tela para exportar.");
    const dadosExcel = dadosFiltrados.map(row => {
      const pmvBase = row.meses[0]?.pmv || 0;
      const linha: any = {
        "RAZÃO SOCIAL": row.razaosocial, "CÓDIGO SKU": row.sku, "DESCRIÇÃO": row.descricao, 
        "CATEGORIA": row.categoria, "SEGMENTO": row.segmento, "PMV PONDERADO (R$)": pmvBase
      };
      row.meses.forEach((m: any) => {
        const edicao = celulasEditadas[row.chave_matriz]?.[m.mes_banco];
        const volFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : m.vol_ajustado));
        linha[`${m.mes_str} (Base IA)`] = Math.round(Number(m.vol_ia));
        linha[`${m.mes_str} (Comercial)`] = volFinal;
      });
      return linha;
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Bottom_Up_Consenso");
    worksheet['!cols'] = [{ wch: 30 }, { wch: 15 }, { wch: 40 }, { wch: 20 }, { wch: 20 }, { wch: 18 }];
    XLSX.writeFile(workbook, `SOP_Nexus_BottomUp_${nomeResponsavel}_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleSalvar = async () => {
    setIsProcessing(true);
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chave, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
          nivel: nivelHierarquia, 
          chave: chave, // Aqui vai a chave_matriz (Razão Social|SKU)
          mes_projetado: mes, 
          novo_volume: val.novo_volume === '' ? 0 : val.novo_volume 
        });
      });
    });

    try {
      await axios.post('/api/v1/consensus/micro/congelar', { origem_ajuste: "Bottom-Up", ajustes });
      alert("✅ Volume comercial salvo com sucesso! O rateio por lojas foi processado.");
      fetchData(); 
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao salvar.");
    } finally {
      setIsProcessing(false);
    }
  };

  const dadosFiltrados = useMemo(() => dadosBrutos.filter(d => 
    (d.razaosocial + ' ' + d.sku + ' ' + d.descricao).toLowerCase().includes(busca.toLowerCase())
  ), [dadosBrutos, busca]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, {vol: number, fat: number}> = {};
    dadosFiltrados.forEach(row => row.meses.forEach((m: any) => totais[m.mes_banco] = {vol: 0, fat: 0}));
    
    dadosFiltrados.forEach(row => {
      const pmvBase = row.meses[0]?.pmv || 0;
      row.meses.forEach((mes: any) => {
        const edicao = celulasEditadas[row.chave_matriz]?.[mes.mes_banco];
        const isPendente = edicao !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicao.novo_volume : mes.vol_ajustado));
        
        totais[mes.mes_banco].vol += volumeFinal;
        totais[mes.mes_banco].fat += isPendente ? (volumeFinal * pmvBase) : (mes.receita || 0);
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
        const res = await axios.get('/api/v1/consensus/micro/grafico', { params: { chave: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
    row.toggleExpanded();
  };

  const qtdEdicoes = Object.keys(celulasEditadas).length;

  const columns = useMemo(() => {
    if (dadosFiltrados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'expander', enableSorting: false, header: () => null,
        cell: ({ row }: any) => (
          <button onClick={() => toggleRow(row)} className="p-2 hover:bg-slate-100 rounded-xl transition-all">
            {row.getIsExpanded() ? <ChevronUp className="w-5 h-5 text-indigo-600" /> : <ChevronDown className="w-5 h-5 text-slate-400" />}
          </button>
        ),
      },
      {
        id: 'nome', header: 'Cliente / Produto',
        accessorFn: (row: any) => row.razaosocial + ' ' + row.descricao,
        cell: (info: any) => (
          <div className="flex flex-col py-1 min-w-[280px] max-w-[350px]">
            <span className="font-black text-slate-900 text-sm truncate uppercase tracking-tight flex items-center gap-1.5"><Store className="w-4 h-4 text-slate-400"/> {info.row.original.razaosocial}</span>
            <span className="font-bold text-slate-600 text-xs mt-1.5 truncate flex items-center gap-1.5"><Package className="w-3.5 h-3.5 text-indigo-400"/> {info.row.original.descricao}</span>
            <div className="flex items-center gap-2 mt-1 pl-5">
              <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-2 py-0.5 rounded-md">{info.row.original.sku}</span>
              <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-100">PMV: {formatMoeda(info.row.original.meses[0]?.pmv || 0)}</span>
            </div>
          </div>
        )
      }
    ];

    const mesesMap = new Map();
    dadosFiltrados.forEach(r => r.meses.forEach((m: any) => mesesMap.set(m.mes_banco, m)));
    
    Array.from(mesesMap.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)).forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_ajustado || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.chave_matriz]?.[m.mes_banco];
          
          const isPendente = edicao !== undefined;
          const valorReal = isPendente ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const baseTopDown = Math.round(Number(dadosMes?.vol_td || 0));
          const isChanged = valorInteiro !== baseIA;
          
          const pmvBase = row.meses[0]?.pmv || 0;
          const faturamentoPrevisto = isPendente ? (valorInteiro * pmvBase) : (dadosMes?.receita || 0);

          // Verifica se a meta da diretoria é maior (Pressão Comercial)
          const temPressao = baseTopDown > valorInteiro;

          return (
            <div className="flex flex-col w-28 gap-1.5 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest" title="Sugestão IA">IA: {baseIA}</span>
                 {temPressao && <span className="text-[9px] text-rose-500 font-black uppercase tracking-widest bg-rose-50 px-1 rounded border border-rose-100" title="Meta da Diretoria (Top-Down)">TD: {baseTopDown}</span>}
              </div>
              <input
                type="number" value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                onChange={(e) => meta.updateCell(row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${isChanged ? 'bg-indigo-600 border-indigo-700 text-white shadow-md' : 'bg-gray-50 border-transparent text-gray-800 focus:bg-white focus:border-slate-300'}`}
              />
              <span className="text-[10px] font-black text-emerald-600 text-center tracking-tight">
                {formatMoeda(faturamentoPrevisto)}
              </span>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (chave: string, mes: string, val: string) => {
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV } } }));
      }
    }
  });

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <TrendingUp className="w-8 h-8 text-indigo-600" /> ALINHAMENTO COMERCIAL (BOTTOM-UP)
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Visão Executiva: {nivelHierarquia === 'vendedor' ? 'Vendedor(a)' : 'Regional'} <span className="text-indigo-600">{nomeResponsavel}</span>
            </p>
          </div>
          
          <div className="flex items-center gap-4">
              <div className="flex items-center gap-3 h-full">
                {qtdEdicoes > 0 && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                <button onClick={handleSalvar} disabled={isProcessing} className="flex items-center gap-2 bg-slate-900 hover:bg-black text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-slate-900/30 transition-all disabled:opacity-50">
                    {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : <Check className="w-5 h-5" />}
                    {qtdEdicoes > 0 ? 'Gravar Proposta Comercial' : 'Proposta Atualizada'}
                </button>
              </div>
          </div>
        </div>

        {/* CONTROLES E BUSCA */}
        {!isExecutivo && (
            <div className="flex items-center gap-4 mb-6 bg-white p-4 rounded-2xl shadow-sm border border-gray-100">
              <span className="text-xs font-black text-slate-500 uppercase tracking-widest">Simular Visão:</span>
              <select value={nivelHierarquia} onChange={e => {setNivelHierarquia(e.target.value); setNomeResponsavel('');}} className="bg-slate-50 border border-slate-200 text-sm font-bold p-2.5 rounded-xl outline-none text-slate-700">
                  <option value="regional">Regional</option>
                  <option value="vendedor">Vendedor Específico</option>
              </select>
              <input type="text" placeholder={nivelHierarquia === 'vendedor' ? "Nome do Vendedor (ex: JOAO SILVA)" : "Nome da Regional (ex: SUL)"} value={nomeResponsavel} onChange={e => setNomeResponsavel(e.target.value.toUpperCase())} className="flex-1 bg-slate-50 border border-slate-200 p-2.5 rounded-xl text-sm font-bold text-slate-700 outline-none uppercase placeholder:normal-case"/>
              <button onClick={fetchData} className="bg-indigo-50 text-indigo-600 px-4 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-indigo-100 transition"><Search className="w-4 h-4"/></button>
            </div>
        )}

        <div className="flex flex-col md:flex-row gap-4 mb-6">
           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-gray-100 flex flex-col md:flex-row items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Buscar por cliente, razão social ou produto..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-3 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-indigo-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 ml-auto whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar Planilha
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-gray-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Carregando Matriz Comercial...</span>
             </div>
          ) : dadosFiltrados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Filter className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">Nenhum dado encontrado para esta visão</span>
             </div>
          ) : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-gray-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className={`px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest ${header.column.getCanSort() ? 'cursor-pointer hover:bg-slate-50 transition-colors' : ''}`} onClick={header.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getCanSort() && (
                            <span className="text-slate-300">
                              {{ asc: <ArrowUp className="w-4 h-4 text-slate-500" />, desc: <ArrowDown className="w-4 h-4 text-slate-500" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-30" />}
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
                    <tr className={`border-b border-gray-50 transition-colors ${row.getIsExpanded() ? 'bg-slate-50/60' : 'hover:bg-slate-50/30'}`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-4">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {row.getIsExpanded() && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-50/50 p-8 border-b border-gray-100">
                          <div className="bg-white rounded-[40px] p-8 shadow-inner border border-gray-100 animate-in fade-in duration-500">
                            <div className="flex justify-between items-start mb-6 px-2">
                               <div className="flex flex-col gap-1">
                                 <h3 className="text-lg font-black text-slate-800 uppercase tracking-tighter">
                                   Curva Atômica Comercial (S&OE)
                                 </h3>
                                 <p className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                                   {row.original.razaosocial} • {row.original.descricao}
                                 </p>
                               </div>
                            </div>

                            <div className="h-[250px] w-full -ml-4">
                              {loadingGrafico === row.original.chave_matriz ? (
                                <div className="h-full flex items-center justify-center text-slate-800"><Loader2 className="animate-spin w-8 h-8" /></div>
                              ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart data={
                                      (dadosGraficoCache[row.original.chave_matriz] || []).map((p: any) => {
                                          const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                          const edicao = celulasEditadas[row.original.chave_matriz]?.[p.data_iso];
                                          const valComercial = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                          return { ...p, Consenso: valComercial !== null ? valComercial : p.Consenso };
                                      })
                                  }>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10}} axisLine={false} tickLine={false} />
                                    <Tooltip contentStyle={{borderRadius: '20px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900'}} />
                                    
                                    <Line type="monotone" dataKey="CicloAnterior" name="Sua Promessa (Mês Passado)" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />

                                    <Line type="monotone" dataKey="Realizado" name="Faturamento Real" stroke="#0f172a" strokeWidth={4} dot={{r: 3, fill: '#0f172a'}} connectNulls={false} />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                    <Line type="monotone" dataKey="Consenso" name="Sua Proposta (Atual)" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#fff'}} connectNulls={false} />
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
          )}
        </div>
      </div>
    </div>
  );
}