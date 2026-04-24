import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Lock, Search, X, Store, Package, Download, BarChart2, Activity, ShieldAlert, AlertTriangle
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
  
  // =========================================================================
  // CORREÇÃO: O Bottom-Up agora espera pelo Top-Down (Fase 1)
  // =========================================================================
  const [isFechado, setIsFechado] = useState(false);
  const [isTopDownFechado, setIsTopDownFechado] = useState<boolean | null>(null);
  
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [busca, setBusca] = useState('');
  const [opcoesBusca, setOpcoesBusca] = useState<{vendedores: string[], regionais: string[]}>({vendedores: [], regionais: []});

  useEffect(() => {
    // Busca os filtros
    axios.get('/api/v1/consensus/micro/filtros', { params: { gerente_nome: usuarioSessao?.gerente_nome } })
         .then(res => setOpcoesBusca(res.data)).catch(console.error);

    // CORREÇÃO: Consulta a rota macro (Top-Down) para saber se pode liberar a tela
    axios.get('/api/v1/consensus/macro/status')
         .then(res => setIsTopDownFechado(res.data.is_topdown_fechado))
         .catch(() => setIsTopDownFechado(false));
  }, [usuarioSessao]);

  const fetchData = useCallback(async () => {
    if (!nomeResponsavel) return;
    setIsLoading(true);
    try {
      const [dadosRes, statusRes] = await Promise.all([
        axios.get('/api/v1/consensus/micro', { params: { nivel_hierarquia: nivelHierarquia, nome_responsavel: nomeResponsavel } }),
        axios.get('/api/v1/consensus/micro/status', { params: { nivel_hierarquia: nivelHierarquia, nome_responsavel: nomeResponsavel } })
      ]);
      
      setDadosBrutos(dadosRes.data.dados || []);
      setIsFechado(statusRes.data.is_fechado); 
      
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [nivelHierarquia, nomeResponsavel]);

  useEffect(() => { 
    if (isExecutivo && nomeResponsavel && isTopDownFechado) fetchData(); 
  }, [fetchData, isExecutivo, nomeResponsavel, isTopDownFechado]);

  const handleExportExcel = () => {
    if (dadosBrutos.length === 0) return alert("Não há dados no ecrã para exportar.");
    const dadosExcel: any[] = [];
    
    dadosBrutos.forEach(cliente => {
      cliente.subRows.forEach((prod: any) => {
        const linha: any = {
          "RAZÃO SOCIAL": cliente.nome, "CÓDIGO SKU": prod.produto, "DESCRIÇÃO": prod.descricao, 
          "CATEGORIA": prod.categoria, "SEGMENTO": prod.segmento, "PMV PONDERADO (R$)": prod.meses[0]?.pmv || 0
        };
        prod.meses.forEach((m: any) => {
          const edicao = celulasEditadas[prod.chave_matriz]?.[m.mes_banco];
          const volFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : m.vol_ajustado));
          linha[`${m.mes_str} (Base IA)`] = Math.round(Number(m.vol_ia));
          linha[`${m.mes_str} (Top-Down)`] = Math.round(Number(m.vol_td || 0));
          linha[`${m.mes_str} (Comercial)`] = volFinal;
        });
        dadosExcel.push(linha);
      });
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Bottom_Up_Consenso");
    worksheet['!cols'] = [{ wch: 30 }, { wch: 15 }, { wch: 40 }, { wch: 20 }, { wch: 20 }, { wch: 18 }];
    XLSX.writeFile(workbook, `SOP_Nexus_BottomUp_${nomeResponsavel}_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleSalvar = async () => {
    if (isFechado) return; 
    setIsProcessing(true);
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chave, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
          nivel: chave.includes('|') ? 'produto' : 'cliente', 
          chave: chave, 
          mes_projetado: mes, 
          novo_volume: val.novo_volume === '' ? 0 : val.novo_volume 
        });
      });
    });

    try {
      await axios.post(`/api/v1/consensus/micro/congelar?nivel_hierarquia=${nivelHierarquia}&nome_responsavel=${nomeResponsavel}`, { origem_ajuste: "Comercial", ajustes });
      alert("✅ Volume comercial salvo com sucesso! O rateio (cascata) foi processado.");
      fetchData(); 
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao guardar.");
    } finally {
      setIsProcessing(false);
    }
  };

  const dadosFiltrados = useMemo(() => {
    if (!busca) return dadosBrutos;
    const term = busca.toLowerCase();
    
    return dadosBrutos.map(cliente => {
      if (cliente.nome.toLowerCase().includes(term)) return cliente;
      
      const subRowsFiltradas = cliente.subRows.filter((p: any) => 
        (p.produto + ' ' + p.descricao).toLowerCase().includes(term)
      );
      
      if (subRowsFiltradas.length > 0) return { ...cliente, subRows: subRowsFiltradas };
      return null;
    }).filter(Boolean);
  }, [dadosBrutos, busca]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, {vol: number, fat: number}> = {};
    dadosFiltrados.forEach(cliente => {
      cliente.meses.forEach((m: any) => totais[m.mes_banco] = {vol: 0, fat: 0});
    });
    
    dadosFiltrados.forEach(cliente => {
      cliente.subRows.forEach((prod: any) => {
        const pmvBase = prod.meses[0]?.pmv || 0;
        prod.meses.forEach((mes: any) => {
          const edicaoProd = celulasEditadas[prod.chave_matriz]?.[mes.mes_banco];
          const isPendente = edicaoProd !== undefined;
          const volumeFinal = Math.round(Number(isPendente ? edicaoProd.novo_volume : mes.vol_ajustado));
          
          totais[mes.mes_banco].vol += volumeFinal;
          totais[mes.mes_banco].fat += isPendente ? (volumeFinal * pmvBase) : (mes.receita || 0);
        });
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

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
        const res = await axios.get('/api/v1/consensus/micro/grafico', { 
            params: { 
                chave_matriz: chave, 
                nivel_hierarquia: nivelHierarquia, 
                nome_responsavel: nomeResponsavel 
            } 
        });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const qtdEdicoes = Object.keys(celulasEditadas).length;

  const columns = useMemo(() => {
    if (dadosFiltrados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Cliente / Produto',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const isCliente = row.original.tipo === 'cliente';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[300px]">
              {isCliente ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-indigo-50 text-indigo-500 rounded-lg transition-colors">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : (
                <div className="w-8"></div>
              )}
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 border-indigo-200 text-indigo-600 shadow-sm' : 'hover:bg-slate-100 border-transparent text-slate-400 hover:text-slate-600'}`} title="Ver Gráfico S&OE">
                <BarChart2 className="w-4 h-4" />
              </button>

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${isCliente ? 'bg-indigo-50 border-indigo-100' : 'bg-slate-50 border-slate-100'}`}>
                {isCliente ? <Store className="w-4 h-4 text-indigo-600" /> : <Package className="w-4 h-4 text-slate-400" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm ${isCliente ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[250px]'}`}>
                   {info.getValue()}
                 </span>
                 {!isCliente && (
                   <div className="flex items-center gap-2 mt-1">
                     <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{row.original.produto}</span>
                     <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">PMV: {formatMoeda(row.original.meses[0]?.pmv || 0)}</span>
                   </div>
                 )}
              </div>
            </div>
          )
        }
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
          const isCliente = row.tipo === 'cliente';
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.chave_matriz]?.[m.mes_banco];
          
          const isPendente = edicao !== undefined;
          const valorReal = isPendente ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const baseTopDown = Math.round(Number(dadosMes?.vol_td || 0));
          const isChanged = valorInteiro !== baseIA;
          
          const faturamentoPrevisto = isPendente ? (valorInteiro * (dadosMes?.pmv || 0)) : (dadosMes?.receita || 0);
          const temPressao = baseTopDown > valorInteiro;
          
          const bloqueado = meta.isFechado;

          return (
            <div className="flex flex-col w-28 gap-1.5 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest" title="Sugestão IA">IA: {baseIA}</span>
                 {temPressao && <span className="text-[9px] text-rose-500 font-black uppercase tracking-widest bg-rose-50 px-1 rounded border border-rose-100" title="Meta da Diretoria (Top-Down)">TD: {baseTopDown}</span>}
              </div>
              <input
                type="number" value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                disabled={bloqueado}
                onChange={(e) => meta.updateCell(row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${bloqueado ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed opacity-80' : 
                    isCliente ? 'border-dashed border-indigo-200 focus:border-indigo-500 focus:bg-indigo-50 text-indigo-900 bg-white' : 'border-solid'} 
                  ${isChanged && !isCliente && !bloqueado ? 'bg-indigo-600 border-indigo-700 text-white shadow-md' : !isCliente && !bloqueado ? 'bg-slate-50 border-transparent text-slate-800 focus:bg-white focus:border-slate-300' : ''}`}
                title={bloqueado ? "Carteira Trancada pela Gerência" : isCliente ? "Editar Matriz (Rateia por todos os SKUs)" : "Editar SKU"}
              />
              <span className={`text-[10px] font-black text-center tracking-tight ${bloqueado ? 'text-slate-400' : isCliente ? 'text-indigo-500' : 'text-emerald-600'}`}>
                {formatMoeda(faturamentoPrevisto)}
              </span>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, chartExpanded]);

  const table = useReactTable({
    data: dadosFiltrados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      isFechado, 
      updateCell: (chave: string, mes: string, val: string) => {
        if (isFechado) return; 
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV } } }));
      }
    }
  });

  // =========================================================================
  // CORREÇÃO: TELA DE BLOQUEIO DE FASE (AGUARDANDO TOP-DOWN)
  // =========================================================================
  if (isTopDownFechado === null) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Verificando Status do Ciclo...</span>
      </div>
    );
  }

  if (isTopDownFechado === false) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans p-6">
        <div className="bg-white p-12 rounded-[40px] shadow-xl border border-slate-100 flex flex-col items-center max-w-lg text-center animate-in fade-in zoom-in duration-500">
          <div className="w-20 h-20 bg-blue-50 rounded-full flex items-center justify-center mb-6 border border-blue-100">
            <Lock className="w-10 h-10 text-blue-500" />
          </div>
          <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-4">Aguardando Diretoria (Top-Down)</h2>
          <p className="text-slate-500 font-medium leading-relaxed">
            A fase Comercial (Bottom-Up) só pode ser iniciada após a aprovação e congelamento da demanda macro pela <strong>Diretoria (Fase 1)</strong>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO COM FEEDBACK DE TRAVA */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isFechado && !isLoading && (
              <div className="absolute top-0 left-0 w-full bg-rose-500 text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10">
                  <ShieldAlert className="w-3 h-3" /> CARTEIRA TRANCADA PELA GERÊNCIA - APENAS LEITURA
              </div>
          )}
          
          <div className={isFechado ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <TrendingUp className="w-8 h-8 text-indigo-600" /> Metas por Cliente
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Visão Executiva: {nivelHierarquia === 'vendedor' ? 'Vendedor(a)' : 'Regional'} <span className="text-indigo-600">{nomeResponsavel}</span>
            </p>
          </div>
          
          <div className={`flex items-center gap-4 ${isFechado ? "pt-4" : ""}`}>
              <div className="flex items-center gap-3 h-full">
                {qtdEdicoes > 0 && !isFechado && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                <button onClick={handleSalvar} disabled={isProcessing || isFechado} className={`flex items-center gap-2 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg ${isFechado ? 'bg-slate-300 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-black shadow-slate-900/30'}`}>
                    {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : isFechado ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                    {isFechado ? 'Edição Bloqueada' : qtdEdicoes > 0 ? 'Gravar Proposta Comercial' : 'Proposta Atualizada'}
                </button>
              </div>
          </div>
        </div>

        {/* CONTROLES E BUSCA */}
        {!isExecutivo && (
            <div className="flex items-center gap-4 mb-6 bg-white p-4 rounded-2xl shadow-sm border border-slate-100">
              <span className="text-xs font-black text-slate-500 uppercase tracking-widest">Simular Visão:</span>
              
              <select 
                 value={nivelHierarquia} 
                 onChange={e => {setNivelHierarquia(e.target.value); setNomeResponsavel('');}} 
                 className="bg-slate-50 border border-slate-200 text-sm font-bold p-2.5 rounded-xl outline-none text-slate-700 cursor-pointer"
              >
                  <option value="regional">Regional</option>
                  <option value="vendedor">Vendedor Específico</option>
              </select>
              
              <select 
                  value={nomeResponsavel} 
                  onChange={e => setNomeResponsavel(e.target.value)} 
                  className="flex-1 bg-slate-50 border border-slate-200 p-2.5 rounded-xl text-sm font-bold text-slate-700 outline-none cursor-pointer"
              >
                  <option value="">{nivelHierarquia === 'vendedor' ? '-- Selecione um Vendedor --' : '-- Selecione uma Regional --'}</option>
                  {(nivelHierarquia === 'vendedor' ? opcoesBusca.vendedores : opcoesBusca.regionais).map(opt => (
                      <option key={opt} value={opt}>{opt}</option>
                  ))}
              </select>

              <button 
                onClick={fetchData} 
                disabled={!nomeResponsavel || isLoading} 
                className="bg-indigo-50 text-indigo-600 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-indigo-100 transition disabled:opacity-50 flex items-center gap-2"
              >
                 {isLoading ? <Loader2 className="w-4 h-4 animate-spin"/> : <Search className="w-4 h-4"/>} 
                 Buscar
              </button>
            </div>
        )}

        <div className="flex flex-col md:flex-row gap-4 mb-6">
           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex flex-col md:flex-row items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Pesquisar por cliente ou produto..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-3 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-indigo-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 ml-auto whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar Planilha
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">A carregar Matriz Comercial...</span>
             </div>
          ) : dadosFiltrados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Filter className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">Nenhum dado encontrado para esta visão</span>
             </div>
          ) : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm">
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
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-indigo-50/30' : row.original.tipo === 'cliente' ? 'bg-white hover:bg-slate-50' : 'bg-slate-50/50 hover:bg-slate-100'}`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-3">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {chartExpanded === row.original.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500">
                            <div className="flex justify-between items-start mb-6 px-2">
                               <div className="flex flex-col gap-1">
                                 <h3 className="text-lg font-black text-white uppercase tracking-tighter flex items-center gap-2">
                                   <Activity className="w-5 h-5 text-emerald-400" /> Curva S&OE ({row.original.tipo === 'cliente' ? 'Visão Conta Global' : 'Visão Produto'})
                                 </h3>
                                 <p className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                                   {row.original.nome}
                                 </p>
                               </div>
                            </div>

                            <div className="h-[250px] w-full">
                              {loadingGrafico === row.original.chave_matriz ? (
                                <div className="h-full flex items-center justify-center text-slate-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                              ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart 
                                    data={
                                      (dadosGraficoCache[row.original.chave_matriz] || []).map((p: any) => {
                                          const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                          const edicao = celulasEditadas[row.original.chave_matriz]?.[p.data_iso];
                                          const valComercial = mesNaTab ? Math.round(Number(edicao !== undefined ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                          return { ...p, Consenso: valComercial !== null ? valComercial : p.Consenso };
                                      })
                                    }
                                    margin={{ top: 20, right: 30, left: 20, bottom: 10 }}
                                  >
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                    <Tooltip contentStyle={{borderRadius: '20px', backgroundColor: '#0f172a', border: '1px solid #1e293b', color: '#fff', boxShadow: '0 10px 30px rgba(0,0,0,0.5)'}} />
                                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900', color: '#cbd5e1'}} />
                                    
                                    <Line type="monotone" dataKey="CicloAnterior" name="Sua Promessa (Mês Passado)" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />

                                    <Line type="monotone" dataKey="Realizado" name="Faturamento Real" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls={false} />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                    <Line type="monotone" dataKey="Consenso" name="Sua Proposta (Atual)" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#0f172a'}} connectNulls={false} />
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
          )}
        </div>
      </div>
    </div>
  );
}