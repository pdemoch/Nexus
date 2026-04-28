import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Activity, Factory, ShieldCheck, Check, Hash, Search, Filter, Download, X, AlertTriangle, Lock, ShieldAlert
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function SupplyReviewArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  // STATUS DAS FASES
  const [isTopDownFechado, setIsTopDownFechado] = useState(false);
  const [isFase2Fechada, setIsFase2Fechada] = useState<boolean | null>(null);
  const [qtdPendentes, setQtdPendentes] = useState(0);
  const [isSupplyFechado, setIsSupplyFechado] = useState(false);

  const [busca, setBusca] = useState('');
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [res, statusRes] = await Promise.all([
        axios.get('/api/v1/consensus/supply'),
        axios.get('/api/v1/consensus/supply/status')
      ]);
      setDadosBrutos(res.data.dados || []);
      
      setIsTopDownFechado(statusRes.data.is_topdown_fechado);
      setIsFase2Fechada(statusRes.data.is_fase2_fechada);
      setQtdPendentes(statusRes.data.qtd_pendentes);
      setIsSupplyFechado(statusRes.data.is_supply_fechado);
      
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleExportExcel = () => {
    if (dadosBrutos.length === 0) return alert("Não há dados no ecrã para exportar.");
    const dadosExcel: any[] = [];
    
    dadosBrutos.forEach(prod => {
      const linha: any = {
        "SKU": prod.produto,
        "DESCRIÇÃO": prod.descricao,
        "CATEGORIA": prod.categoria,
        "SEGMENTO": prod.segmento
      };
      
      prod.meses.forEach((m: any) => {
        const edicao = celulasEditadas[prod.produto]?.[m.mes_banco];
        const volFinal = Math.round(Number(edicao !== undefined && edicao.novo_volume !== undefined && edicao.novo_volume !== '' ? edicao.novo_volume : m.vol_ajustado));
        
        linha[`${m.mes_str} (Pedido Comercial)`] = Math.round(Number(m.vol_ref || 0));
        linha[`${m.mes_str} (Fábrica)`] = volFinal;
        linha[`${m.mes_str} (Motivo)`] = edicao?.justificativa || m.justificativa || '';
      });
      dadosExcel.push(linha);
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Supply_Review");
    XLSX.writeFile(workbook, `SOP_Nexus_SupplyReview_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleSalvar = async () => {
    if (isSupplyFechado) return; 

    // =====================================================================
    // 1. VALIDAÇÃO EXPLÍCITA DE ERRO (A pedido do utilizador)
    // =====================================================================
    let erroValidacao = false;
    Object.values(celulasEditadas).forEach((meses: any) => {
      Object.values(meses).forEach((val: any) => {
        // Se ele tocou no volume e deixou em branco ou digitou algo inválido (NaN)
        if (val.novo_volume === '' || Number.isNaN(val.novo_volume)) {
          erroValidacao = true;
        }
      });
    });

    if (erroValidacao) {
      alert("⚠️ Atenção: Há campos de volume em branco ou com formato inválido. Por favor, preencha um número válido em todos os campos alterados antes de gravar.");
      return; // Trava a execução e impede de enviar ao Backend
    }

    setIsProcessing(true);
    const ajustes: any[] = [];
    
    Object.entries(celulasEditadas).forEach(([sku, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        
        let volFinal = val.novo_volume;
        // Só faz fallback se o volume estiver 100% intocado (ex: ele apenas digitou a justificativa)
        if (volFinal === undefined) {
            const prodOriginal = dadosBrutos.find(p => p.produto === sku);
            const mesOriginal = prodOriginal?.meses.find((m: any) => m.mes_banco === mes);
            volFinal = mesOriginal ? mesOriginal.vol_ajustado : 0;
        }

        ajustes.push({ 
          produto: sku, 
          mes_projetado: mes, 
          novo_volume: Number(volFinal),
          justificativa: val.justificativa || ''
        });
      });
    });

    try {
      await axios.post('/api/v1/consensus/supply/congelar', { origem_ajuste: "Supply Review", ajustes });
      alert("✅ Volume de Supply e Restrições salvos com sucesso! O rateio proporcional para as carteiras foi aplicado.");
      fetchData(); 
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao guardar restrições.");
    } finally {
      setIsProcessing(false);
    }
  };

  const dadosFiltrados = useMemo(() => {
    if (!busca) return dadosBrutos;
    const term = busca.toLowerCase();
    return dadosBrutos.filter(p => (p.produto + ' ' + p.descricao).toLowerCase().includes(term));
  }, [dadosBrutos, busca]);

  const totaisFaturamento = useMemo(() => {
    const totais: Record<string, number> = {};
    dadosFiltrados.forEach(prod => {
      prod.meses.forEach((mes: any) => {
        const edicaoProd = celulasEditadas[prod.produto]?.[mes.mes_banco];
        const hasNovoVol = edicaoProd !== undefined && edicaoProd.novo_volume !== undefined && edicaoProd.novo_volume !== '';
        const volumeFinal = Math.round(Number(hasNovoVol ? edicaoProd.novo_volume : mes.vol_ajustado));
        
        if (!totais[mes.mes_banco]) totais[mes.mes_banco] = 0;
        totais[mes.mes_banco] += hasNovoVol ? (volumeFinal * (mes.pmv || 0)) : (mes.receita || 0);
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const toggleChart = async (row: any) => {
    const chave = row.original.produto;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/micro/grafico', { params: { chave_matriz: `|${chave}`, tipo_linha: 'produto' } });
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
        id: 'nome', header: 'SKU / Descrição do Produto',
        accessorFn: (row: any) => row.descricao,
        cell: (info: any) => {
          const row = info.row.original;
          return (
            <div className="flex items-center gap-3 py-2 min-w-[300px]">
              <button onClick={() => toggleChart(info.row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === row.produto ? 'bg-indigo-100 border-indigo-200 text-indigo-600 shadow-sm' : 'hover:bg-slate-100 border-transparent text-slate-400 hover:text-slate-600'}`}>
                <Activity className="w-4 h-4" />
              </button>
              <div className="w-8 h-8 rounded-full bg-slate-50 border border-slate-100 flex items-center justify-center flex-shrink-0">
                <Hash className="w-4 h-4 text-slate-400" />
              </div>
              <div className="flex flex-col">
                 <span className="font-bold text-slate-700">{info.getValue()}</span>
                 <div className="flex items-center gap-2 mt-1">
                   <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{row.produto}</span>
                   <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">PMV: {formatMoeda(row.meses[0]?.pmv || 0)}</span>
                 </div>
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
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.produto]?.[m.mes_banco];
          
          const hasNovoVol = edicao !== undefined && edicao.novo_volume !== undefined;
          const erroNoCampo = hasNovoVol && (edicao.novo_volume === '' || Number.isNaN(edicao.novo_volume));
          const valorReal = hasNovoVol ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          
          // Se houver erro de digitação, mostramos o texto vazio/inválido. Se não, mostramos formatado.
          const valorInput = erroNoCampo ? valorReal : Math.round(Number(valorReal));
          const volBottomUp = Math.round(Number(dadosMes?.vol_ref || 0)); 
          
          const faturamentoPrevisto = (hasNovoVol && !erroNoCampo) ? (valorInput * (dadosMes?.pmv || 0)) : (dadosMes?.receita || 0);
          
          const bloqueado = meta.isFechado;
          const temCorte = (!erroNoCampo && valorInput < volBottomUp);

          return (
            <div className="flex flex-col w-36 gap-1.5 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest" title="Pedido Consolidado do Comercial">BU: {volBottomUp}</span>
                 {temCorte && <span className="text-[9px] text-rose-500 font-black uppercase tracking-widest bg-rose-50 px-1 rounded border border-rose-100" title="Corte de Fábrica">CORTE</span>}
                 {erroNoCampo && <span className="text-[9px] text-rose-500 font-black uppercase tracking-widest bg-rose-50 px-1 rounded border border-rose-100" title="Valor Inválido">INVÁLIDO</span>}
              </div>
              
              <input
                type="number" value={valorInput === 0 ? '' : valorInput} placeholder="0" disabled={bloqueado}
                onChange={(e) => meta.updateCell(row.produto, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-t-xl outline-none border-2 transition-all w-full
                  ${bloqueado ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed opacity-80' : 
                    erroNoCampo ? 'border-rose-500 bg-rose-50 text-rose-700' : 'bg-white border-slate-200 focus:border-indigo-500 text-slate-800'} 
                  ${hasNovoVol && !bloqueado && !erroNoCampo ? 'border-amber-400 bg-amber-50 shadow-sm' : ''}`}
                title={bloqueado ? "Aprovação de Supply Fechada" : erroNoCampo ? "Preencha um número válido" : "Definir capacidade de entrega"}
              />
              
              {!bloqueado && (edicao !== undefined || temCorte || dadosMes?.justificativa) && (
                 <input 
                   type="text"
                   value={edicao?.justificativa !== undefined ? edicao.justificativa : (dadosMes?.justificativa || '')}
                   onChange={(e) => meta.updateJustificativa(row.produto, m.mes_banco, e.target.value)}
                   placeholder="Motivo (Opcional)"
                   className={`text-[9px] p-1.5 rounded-b-xl border-x-2 border-b-2 outline-none w-full text-center font-bold transition-all
                     ${erroNoCampo ? 'border-rose-300 bg-rose-50 text-rose-700' : edicao?.justificativa !== undefined ? 'border-amber-400 bg-amber-50/50 text-amber-700' : 'border-slate-200 bg-white text-slate-500'}
                     focus:border-indigo-500 focus:bg-indigo-50`}
                 />
              )}

              <span className={`text-[10px] font-black text-center tracking-tight mt-1 ${bloqueado ? 'text-slate-400' : erroNoCampo ? 'text-rose-500' : 'text-emerald-600'}`}>
                {erroNoCampo ? 'R$ 0' : formatMoeda(faturamentoPrevisto)}
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
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      isFechado: isSupplyFechado, 
      updateCell: (sku: string, mes: string, val: string) => {
        if (isSupplyFechado) return; 
        
        // Se o utilizador apagar tudo (texto vazio) ou digitar algo inválido, mantemos o valor real para a validação apanhar
        const finalV = val === '' ? '' : Number.isNaN(Number(val)) ? NaN : Math.round(Number(val));

        setCelulasEditadas((prev: any) => ({ ...prev, [sku]: { ...(prev[sku] || {}), [mes]: { ...((prev[sku]||{})[mes]||{}), novo_volume: finalV } } }));
      },
      updateJustificativa: (sku: string, mes: string, just: string) => {
        if (isSupplyFechado) return;
        setCelulasEditadas((prev: any) => ({ ...prev, [sku]: { ...(prev[sku] || {}), [mes]: { ...((prev[sku]||{})[mes]||{}), justificativa: just } } }));
      }
    }
  });

  if (isFase2Fechada === null) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Verificando Status das Equipes Comerciais...</span>
      </div>
    );
  }

  if (isFase2Fechada === false) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans p-6">
        <div className="bg-white p-12 rounded-[40px] shadow-xl border border-slate-100 flex flex-col items-center max-w-lg text-center animate-in fade-in zoom-in duration-500">
          <div className="w-20 h-20 bg-amber-50 rounded-full flex items-center justify-center mb-6 border border-amber-100">
            <AlertTriangle className="w-10 h-10 text-amber-500" />
          </div>
          <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-4">Aguardando Fase Comercial</h2>
          <p className="text-slate-500 font-medium leading-relaxed">
            A fase de <strong>Supply Review (Fase 3)</strong> só pode ser iniciada após a aprovação e congelamento da demanda de toda a equipe de Vendas e Gerência.
          </p>
          <div className="mt-8 inline-flex flex-col items-center gap-2 p-4 bg-rose-50 border border-rose-100 rounded-2xl w-full">
            <span className="text-rose-600 font-black uppercase tracking-widest text-xs">Atenção</span>
            <span className="text-rose-700 font-bold text-sm">Faltam trancar as carteiras de <strong>{qtdPendentes} equipe(s)</strong> comercial(is).</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-8 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isSupplyFechado && !isLoading && (
              <div className="absolute top-0 left-0 w-full bg-slate-800 text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10">
                  <ShieldCheck className="w-3 h-3 text-emerald-400" /> SUPPLY CHAIN APROVADO E CONGELADO
              </div>
          )}
          
          <div className={isSupplyFechado ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Factory className="w-8 h-8 text-indigo-600" /> Supply Review
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Restrições de Fábrica e Rateio Automático S&OP
            </p>
          </div>
          
          <div className={`flex items-center gap-4 ${isSupplyFechado ? "pt-4" : ""}`}>
              <div className="flex items-center gap-3 h-full">
                {qtdEdicoes > 0 && !isSupplyFechado && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                
                <button onClick={handleSalvar} disabled={isProcessing || isSupplyFechado} className={`flex items-center gap-2 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg ${isSupplyFechado ? 'bg-slate-300 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-black shadow-slate-900/30'}`}>
                    {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : isSupplyFechado ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                    {isSupplyFechado ? 'Edição Bloqueada' : qtdEdicoes > 0 ? 'Aplicar Restrições de Fábrica' : 'Confirmar Capacidade Total'}
                </button>
              </div>
          </div>
        </div>

        <div className="flex flex-col md:flex-row gap-4 mb-6">
           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex flex-col md:flex-row items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Filtrar por SKU ou Descrição..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-3 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-indigo-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 ml-auto whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar Relatório
             </button>
           </div>
        </div>

        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">A carregar Capacidade de Fábrica...</span>
             </div>
          ) : dadosFiltrados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Filter className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">Nenhum SKU encontrado no radar</span>
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
                    <tr className={`border-b border-slate-50 transition-colors bg-white hover:bg-slate-50`}>
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-8 py-4">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {chartExpanded === row.original.produto && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500">
                            <div className="flex justify-between items-start mb-6 px-2">
                               <div className="flex flex-col gap-1">
                                 <h3 className="text-lg font-black text-white uppercase tracking-tighter flex items-center gap-2">
                                   <Activity className="w-5 h-5 text-emerald-400" /> Curva Histórica de Demanda
                                 </h3>
                                 <p className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                                   {row.original.produto} - {row.original.descricao}
                                 </p>
                               </div>
                            </div>

                            <div className="h-[250px] w-full">
                              {loadingGrafico === row.original.produto ? (
                                <div className="h-full flex items-center justify-center text-slate-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                              ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart 
                                    data={
                                      (dadosGraficoCache[row.original.produto] || []).map((p: any) => {
                                          const mesNaTab = row.original.meses.find((m: any) => m.mes_banco === p.data_iso);
                                          const edicao = celulasEditadas[row.original.produto]?.[p.data_iso];
                                          const valFinal = mesNaTab ? Math.round(Number(edicao !== undefined && edicao.novo_volume !== undefined && edicao.novo_volume !== '' ? edicao.novo_volume : mesNaTab.vol_ajustado)) : null;
                                          return { ...p, Consenso: valFinal !== null ? valFinal : p.Consenso };
                                      })
                                    }
                                    margin={{ top: 20, right: 30, left: 20, bottom: 10 }}
                                  >
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} />
                                    <Tooltip contentStyle={{borderRadius: '20px', backgroundColor: '#0f172a', border: '1px solid #1e293b', color: '#fff', boxShadow: '0 10px 30px rgba(0,0,0,0.5)'}} />
                                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: '900', color: '#cbd5e1'}} />
                                    
                                    <Line type="monotone" dataKey="Realizado" name="Venda Real" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls={false} />
                                    <Line type="monotone" dataKey="IA" name="Sinal IA (Base)" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                    <Line type="monotone" dataKey="Consenso" name="Capacidade Fábrica (Atual)" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#0f172a'}} connectNulls={false} />
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
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Receita Consolidada</span>
                          <span className="font-bold text-sm text-white">TOTAL NA TELA (R$)</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(totaisFaturamento[mesBanco] || 0)}</span>
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