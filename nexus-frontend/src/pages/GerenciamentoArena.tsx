import React, { useState, useMemo, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, ColumnDef
} from '@tanstack/react-table';
import { 
  ChevronRight, ChevronDown, Package, Boxes, LayoutGrid, 
  Target, Save, Shield, ShieldAlert, BarChart3, TrendingUp, TrendingDown, ArrowUp, ArrowDown,
  Activity, Wand2
} from 'lucide-react';
import { 
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,  
} from 'recharts';

const formatVolume = (val: any) => {
  const num = Number(val);
  if (isNaN(num)) return '0';
  return new Intl.NumberFormat('pt-BR').format(Math.round(num));
};

const formatMoeda = (val: any) => {
  const num = Number(val);
  if (isNaN(num)) return 'R$ 0,00';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(num));
};

const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

// =========================================================================
// COMPONENTE DE INPUT (SMART)
// =========================================================================
const SmartInput = ({ value, onChange, disabled }: { value: number, onChange: (val: number) => void, disabled: boolean }) => {
  const [localVal, setLocalVal] = useState(value !== undefined && value !== null ? formatVolume(value) : '0');
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (!isFocused) setLocalVal(value !== undefined && value !== null ? formatVolume(value) : '0');
  }, [value, isFocused]);

  const handleFocus = () => { setIsFocused(true); setLocalVal(localVal.replace(/\./g, '')); };
  const handleBlur = () => { 
      setIsFocused(false); 
      const num = parseInt(localVal.replace(/\D/g, ''), 10) || 0;
      setLocalVal(formatVolume(num));
      onChange(num);
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };

  return (
    <input
      type="text" value={localVal} disabled={disabled} onFocus={handleFocus} onBlur={handleBlur} onKeyDown={handleKeyDown} onChange={(e) => setLocalVal(e.target.value)}
      className={`w-full bg-transparent border-none text-center outline-none text-sm
        ${disabled ? 'text-slate-400 font-medium cursor-not-allowed' : 'text-blue-700 font-black'}`}
    />
  );
};

// =========================================================================
// COMPONENTE PRINCIPAL
// =========================================================================
export default function GerenciamentoArena({ usuarioSessao }: any) {
  const [dadosBase, setDadosBase] = useState<any[]>([]);
  const [isTopDownFechado, setIsTopDownFechado] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [rowSelecionada, setRowSelecionada] = useState<any | null>(null);
  
  const [chartData, setChartData] = useState<any[]>([]);
  const [loadingChart, setLoadingChart] = useState(false);

  const colunasData = dadosBase.length > 0 ? dadosBase[0].meses : [];

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento', { params: { nocache: new Date().getTime() } });
      setDadosBase(res.data.dados.portfolio || []);
      setIsTopDownFechado(res.data.is_topdown_fechado);
      setCelulasEditadas({});
    } catch (e) { console.error("Erro ao carregar:", e); } 
    finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Carregar Gráfico Global ou da Linha Selecionada
  useEffect(() => {
    const fetchChart = async () => {
      setLoadingChart(true);
      try {
        const chave = rowSelecionada ? rowSelecionada.chave_matriz : 'ROOT';
        const res = await axios.get('/api/v1/consensus/gerenciamento/grafico', { params: { chave_matriz: chave } });
        setChartData(res.data.dados);
      } catch (e) { console.error(e); }
      finally { setLoadingChart(false); }
    };
    fetchChart();
  }, [rowSelecionada]);

  // =========================================================================
  // LÓGICA DE RATEIO HISTÓRICO (MAIOR RESTO)
  // =========================================================================
  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const ed = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (ed !== undefined) return isNaN(Number(ed.novo_volume)) ? 0 : Number(ed.novo_volume);
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.vol_bu || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicVol(child, mesBanco), 0);
  }, [celulasEditadas]);

  const getDynamicRec = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const vol = getDynamicVol(row, mesBanco);
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return vol * (m?.pmv || 0);
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicRec(child, mesBanco), 0);
  }, [getDynamicVol]);

  const getStaticTD = useCallback((row: any, mesBanco: string): { vol: number, rec: number } => {
    if (row.tipo === 'produto') {
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return { vol: m?.vol_td || 0, rec: m?.receita_td || 0 };
    }
    return (row.subRows || []).reduce((acc: any, child: any) => {
        const c = getStaticTD(child, mesBanco);
        return { vol: acc.vol + c.vol, rec: acc.rec + c.rec };
    }, { vol: 0, rec: 0 });
  }, []);

  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    if (!isTopDownFechado) return;
    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };

      const distributeDown = (node: any, targetVolume: number) => {
        if (!nextEdits[node.chave_matriz]) nextEdits[node.chave_matriz] = {};
        nextEdits[node.chave_matriz][mesBanco] = { novo_volume: targetVolume };

        if (node.subRows && node.subRows.length > 0) {
           const totalHist = node.subRows.reduce((acc: number, child: any) => acc + (child.vol_historico_mix || 1), 0);
           node.subRows.forEach((child: any, idx: number) => {
             let childTarget = 0;
             if (idx === node.subRows.length - 1) {
               const allocatedSoFar = node.subRows.slice(0, idx).reduce((sum: number, c: any) => sum + (nextEdits[c.chave_matriz]?.[mesBanco]?.novo_volume || 0), 0);
               childTarget = targetVolume - allocatedSoFar;
             } else {
               childTarget = Math.round(((child.vol_historico_mix || 1) / totalHist) * targetVolume);
             }
             distributeDown(child, Math.max(0, childTarget));
           });
        }
      };

      const rollupUp = (treeNodes: any[]): boolean => {
        for (const node of treeNodes) {
          if (node.chave_matriz === chaveStr) {
            distributeDown(node, novoValor);
            return true;
          }
          if (node.subRows && node.subRows.length > 0) {
            if (rollupUp(node.subRows)) {
              const newTotal = node.subRows.reduce((acc: number, child: any) => acc + getDynamicVol(child, mesBanco), 0);
              if (!nextEdits[node.chave_matriz]) nextEdits[node.chave_matriz] = {};
              nextEdits[node.chave_matriz][mesBanco] = { novo_volume: newTotal };
              return true;
            }
          }
        }
        return false;
      };

      rollupUp(dadosBase);
      return nextEdits;
    });
  };

  // =========================================================================
  // PREPARAÇÃO DO GRÁFICO (REAL-TIME)
  // =========================================================================
  const chartDataDynamic = useMemo(() => {
    if (!chartData) return [];
    return chartData.map((d: any) => {
        // Encontrar o mês correspondente se houver projeção
        const isProjected = d.TopDown !== null;
        let dynamicBU = d.BottomUpBase;
        
        if (isProjected) {
           const rootList = rowSelecionada ? [rowSelecionada] : dadosBase;
           // Calcula o Bottom-up somando em tempo real da tela
           let totalM = 0;
           rootList.forEach(r => {
              const m = r.meses?.find((x: any) => x.mes_str === d.name);
              if (m) totalM += getDynamicVol(r, m.mes_banco);
           });
           dynamicBU = totalM > 0 ? totalM : d.BottomUpBase;
        }

        return { ...d, BottomUp: dynamicBU };
    });
  }, [chartData, rowSelecionada, celulasEditadas, dadosBase, getDynamicVol]);

  const kpiTotais = useMemo(() => {
    let volTDFull = 0; let volBUFull = 0;
    colunasData.forEach((m: any) => {
       dadosBase.forEach(r => {
          volTDFull += getStaticTD(r, m.mes_banco).vol;
          volBUFull += getDynamicVol(r, m.mes_banco);
       });
    });
    return { volTDFull, volBUFull };
  }, [dadosBase, colunasData, getStaticTD, getDynamicVol]);

  // =========================================================================
  // COLUNAS TANSTACK TABLE (Idêntico ao Dashboard)
  // =========================================================================
  const columns = useMemo<ColumnDef<any>[]>(() => {
    const cols: ColumnDef<any>[] = [
      {
        accessorKey: 'nome',
        header: 'Hierarquia de Produto',
        cell: ({ row, getValue }) => {
          const depth = row.depth;
          const isExpanded = row.getIsExpanded();
          const hasChildren = row.getCanExpand();
          const isProduto = row.original.tipo === 'produto';

          return (
            <div style={{ paddingLeft: `${depth * 1.5}rem` }} className="flex items-center gap-3 py-2 cursor-pointer group" onClick={() => { setRowSelecionada(row.original); if(hasChildren) row.toggleExpanded(); }}>
              {hasChildren ? (
                <button className="p-1 hover:bg-slate-200 text-slate-500 rounded transition-colors">
                  {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
              ) : <div className="w-6" />}
              
              <div className={`w-7 h-7 rounded flex items-center justify-center border shrink-0 transition-colors ${rowSelecionada?.chave_matriz === row.original.chave_matriz ? 'bg-blue-600 border-blue-700 text-white' : depth === 0 ? 'bg-slate-800 text-white border-slate-700' : depth === 1 ? 'bg-slate-100 text-slate-600' : 'bg-white border-slate-200 text-slate-400 group-hover:bg-blue-50 group-hover:text-blue-600 group-hover:border-blue-200'}`}>
                {depth === 0 ? <LayoutGrid className="w-3.5 h-3.5" /> : depth === 1 ? <Boxes className="w-3.5 h-3.5" /> : <Package className="w-3.5 h-3.5" />}
              </div>
              
              <span className={`text-sm pr-4 truncate max-w-[200px] ${rowSelecionada?.chave_matriz === row.original.chave_matriz ? 'text-blue-700 font-black' : !isProduto ? 'font-black text-slate-800' : 'font-semibold text-slate-600'}`}>
                {getValue() as string}
              </span>
            </div>
          );
        },
      },
      {
        id: 'hist_m1',
        header: 'Realizado M-1',
        cell: ({ row }) => {
          const v = row.original.historico?.vol_m1 || 0;
          return (
            <div className="flex flex-col items-center justify-center py-1">
              <span className="font-bold text-slate-700 text-sm">{formatVolume(v)}</span>
              <span className="text-[9px] text-slate-400 font-bold uppercase tracking-widest mt-0.5">Vol Faturado</span>
            </div>
          );
        }
      },
      {
        id: 'hist_a1',
        header: 'Realizado A-1',
        cell: ({ row }) => {
          const vAtual = row.original.historico?.vol_m1 || 0;
          const vAnt = row.original.historico?.vol_a1 || 0;
          const variacao = calcVar(vAtual, vAnt);
          
          return (
            <div className="flex flex-col items-center justify-center py-1">
              <span className="font-bold text-slate-500 text-sm">{formatVolume(vAnt)}</span>
              {vAnt > 0 && (
                <div className={`flex items-center gap-0.5 text-[10px] font-black px-1.5 py-0.5 rounded mt-1 ${variacao >= 0 ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600'}`}>
                  {variacao >= 0 ? <ArrowUp className="w-2.5 h-2.5" /> : <ArrowDown className="w-2.5 h-2.5" />}
                  {Math.abs(variacao).toFixed(1)}% YoY
                </div>
              )}
            </div>
          );
        }
      }
    ];

    colunasData.forEach((mes: any) => {
      cols.push({
        id: mes.mes_banco,
        header: mes.mes_str,
        cell: ({ row }) => {
          const vBU = getDynamicVol(row.original, mes.mes_banco);
          const rBU = getDynamicRec(row.original, mes.mes_banco);
          const tdData = getStaticTD(row.original, mes.mes_banco);

          return (
            <div className="flex flex-col items-center justify-center p-1.5 min-w-[120px]">
              {/* Referência Fixa Top-Down */}
              <div className="flex items-center gap-1 text-[10px] font-black text-slate-400 mb-1.5">
                 <Target className="w-3 h-3 text-slate-300" /> TD: {formatVolume(tdData.vol)}
              </div>
              
              {/* Input Dinâmico Bottom-Up */}
              <div className={`w-full max-w-[100px] border rounded-lg px-2 py-1.5 shadow-sm transition-colors focus-within:ring-2 focus-within:ring-blue-500/50 focus-within:border-blue-500 ${!isTopDownFechado ? 'bg-slate-50 border-slate-200' : 'bg-white border-blue-200 hover:border-blue-400'}`}>
                 <SmartInput 
                    value={vBU} 
                    onChange={(val) => handleEditCell(row.original.chave_matriz, mes.mes_banco, val)} 
                    disabled={!isTopDownFechado} 
                 />
              </div>

              {/* Receita Bottom-Up */}
              <span className="text-[10px] font-bold text-blue-600 bg-blue-50/50 px-2 py-0.5 rounded mt-1.5 tracking-tight border border-blue-100">
                 {formatMoeda(rBU)}
              </span>
            </div>
          );
        },
      });
    });

    return cols;
  }, [colunasData, celulasEditadas, rowSelecionada, isTopDownFechado]);

  const table = useReactTable({
    data: dadosBase,
    columns,
    state: { expanded },
    onExpandedChange: setExpanded,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  // =========================================================================
  // ACTIONS API
  // =========================================================================
  const gerarPayloadFolhas = () => {
    const leafEdits = Object.entries(celulasEditadas).filter(([chave]) => chave.split('|').length === 3);
    return {
        origem_ajuste: "Gerência Comercial (Portfólio Global)", visao: 'portfolio',
        ajustes: leafEdits.flatMap(([chave, meses]: any) => Object.entries(meses).map(([mes_projetado, val]: any) => ({ chave, mes_projetado, novo_volume: parseInt(val.novo_volume, 10) })))
    };
  };

  const handleSalvarRascunho = async () => {
    if (Object.keys(celulasEditadas).length === 0) return alert("Nenhuma alteração para salvar.");
    const p = gerarPayloadFolhas();
    if (p.ajustes.length === 0) return alert("Edite ao menos um volume.");
    try {
      await axios.post(`/api/v1/consensus/gerenciamento/salvar`, p);
      alert("Rateio Absoluto salvo com sucesso no Banco de Dados!");
      fetchData();
    } catch (e: any) { alert("Erro ao salvar: " + (e.response?.data?.detail || e.message)); }
  };

  const handleCongelar = async () => {
    if (!confirm("Esta ação passará o bastão oficial para Supply Review. Deseja prosseguir?")) return;
    try {
      await axios.post(`/api/v1/consensus/gerenciamento/congelar`, gerarPayloadFolhas());
      alert("Portfólio Trancado e enviado para a Fábrica.");
      fetchData();
    } catch (e: any) { alert("Erro ao aprovar: " + (e.response?.data?.detail || e.message)); }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32 font-sans">
      <div className="max-w-[1600px] mx-auto mb-8 flex flex-col gap-6">
        
        {/* HEADER & ACTIONS */}
        <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
                  <BarChart3 className="w-8 h-8 text-blue-600" /> S&OP <span className="text-blue-600">Comercial</span>
              </h1>
              <p className="text-slate-500 mt-1 font-medium">Gestão Bottom-Up (Decisão de Portfólio Global)</p>
            </div>

            <div className="flex items-center gap-4">
              <button onClick={handleSalvarRascunho} disabled={!isTopDownFechado} className="px-5 py-2.5 font-bold rounded-xl transition-all flex items-center gap-2 border bg-white text-slate-700 border-slate-300 hover:bg-slate-50 disabled:opacity-50">
                  <Save className="w-4 h-4" /> Salvar Rascunho
              </button>
              <button onClick={handleCongelar} disabled={!isTopDownFechado} className="px-6 py-2.5 font-bold rounded-xl shadow-lg transition-all flex items-center gap-2 text-white bg-blue-600 hover:bg-blue-500 shadow-blue-900/20 disabled:opacity-50">
                  <Shield className="w-4 h-4" /> Finalizar Portfólio (Aprovar)
              </button>
            </div>
        </div>

        {!isTopDownFechado && !isLoading && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 p-5 rounded-xl flex items-center gap-4 shadow-sm">
                <ShieldAlert className="w-6 h-6 text-amber-500" />
                <div><h3 className="font-bold uppercase text-xs">Bloqueio de Ciclo</h3><p className="text-sm">O modelo Bottom-Up aguarda a formalização Top-Down pela Diretoria.</p></div>
            </div>
        )}

        {/* TOP KPI CARDS */}
        <div className="grid grid-cols-3 gap-6">
           <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 flex flex-col justify-center">
              <div className="text-xs font-black text-slate-400 uppercase tracking-widest mb-1 flex items-center gap-2"><Target className="w-4 h-4"/> Âncora Diretoria (Total TD)</div>
              <div className="text-3xl font-black text-slate-800">{formatVolume(kpiTotais.volTDFull)} <span className="text-sm text-slate-400 font-semibold">Caixas</span></div>
           </div>
           <div className="bg-blue-600 rounded-2xl shadow-xl shadow-blue-900/10 border border-blue-500 p-6 flex flex-col justify-center relative overflow-hidden">
              <div className="absolute right-[-10px] top-[-10px] opacity-10"><BarChart3 className="w-32 h-32 text-white" /></div>
              <div className="text-xs font-black text-blue-200 uppercase tracking-widest mb-1 relative z-10 flex items-center gap-2"><ArrowUp className="w-4 h-4"/> Sua Proposta Atual (Total BU)</div>
              <div className="text-3xl font-black text-white relative z-10">{formatVolume(kpiTotais.volBUFull)} <span className="text-sm text-blue-200 font-semibold">Caixas</span></div>
           </div>
           <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 flex flex-col justify-center">
              <div className="text-xs font-black text-slate-400 uppercase tracking-widest mb-1">Gap Operacional (BU vs TD)</div>
              <div className={`text-3xl font-black ${kpiTotais.volBUFull >= kpiTotais.volTDFull ? 'text-emerald-500' : 'text-rose-500'}`}>
                 {formatVolume(kpiTotais.volBUFull - kpiTotais.volTDFull)} <span className="text-sm font-semibold opacity-70">Caixas</span>
              </div>
           </div>
        </div>

        {/* MAIN CHART AREA (Like Dashboard) */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
            <div className="flex justify-between items-center mb-6">
              <div>
                 <h2 className="text-lg font-black text-slate-800 flex items-center gap-2">
                    <Activity className="w-5 h-5 text-blue-600" /> 
                    {rowSelecionada ? `Modelagem Temporal: ${rowSelecionada.nome}` : 'Modelagem Temporal: Portfólio Global (Empresa)'}
                 </h2>
                 <p className="text-xs font-semibold text-slate-400 mt-1 uppercase tracking-widest">
                    Clique em qualquer linha da tabela para analisar o seu comportamento.
                 </p>
              </div>
              {rowSelecionada && (
                 <button onClick={() => setRowSelecionada(null)} className="text-xs font-bold text-blue-600 bg-blue-50 px-3 py-1.5 rounded-lg hover:bg-blue-100 transition-colors">
                    Ver Empresa Total
                 </button>
              )}
            </div>

            {loadingChart ? (
               <div className="h-[300px] flex items-center justify-center text-slate-400 font-bold animate-pulse">Consultando Motor Analítico Nexus...</div>
            ) : chartDataDynamic.length > 0 ? (
               <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={chartDataDynamic} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                    <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{fill: '#64748b', fontSize: 12, fontWeight: 600}} />
                    <YAxis tickFormatter={(val) => formatVolume(val)} axisLine={false} tickLine={false} tick={{fill: '#64748b', fontSize: 12, fontWeight: 600}} />
                    <Tooltip 
                       contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)', backgroundColor: '#1e293b', color: '#fff' }} 
                       itemStyle={{ color: '#f8fafc', fontWeight: 'bold' }} 
                       labelStyle={{ color: '#94a3b8', marginBottom: '8px' }}
                    />
                    <Legend wrapperStyle={{ paddingTop: '20px' }} iconType="circle" />
                    
                    <Bar dataKey="Realizado" name="Histórico Faturado" fill="#cbd5e1" radius={[4, 4, 0, 0]} maxBarSize={40} />
                    <Line type="monotone" dataKey="TopDown" name="Meta Top-Down (Diretoria)" stroke="#94a3b8" strokeWidth={2} strokeDasharray="6 4" dot={false} connectNulls={false} />
                    <Line type="monotone" dataKey="BottomUp" name="Sua Proposta Dinâmica (Bottom-Up)" stroke="#3b82f6" strokeWidth={4} dot={{ r: 5, strokeWidth: 2, fill: '#fff', stroke: '#3b82f6' }} activeDot={{ r: 7 }} connectNulls={false} />
                  </ComposedChart>
               </ResponsiveContainer>
            ) : (
               <div className="h-[300px] flex items-center justify-center text-slate-400">Nenhum dado temporal encontrado.</div>
            )}
        </div>

        {/* TANSTACK TABLE (Like Dashboard) */}
        <div className="bg-white rounded-2xl shadow-xl shadow-slate-200/50 border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse whitespace-nowrap">
              <thead>
                {table.getHeaderGroups().map(headerGroup => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header, idx) => (
                      <th key={header.id} className={`bg-slate-900 py-4 font-black uppercase tracking-widest text-[11px] text-white border-b border-slate-800 ${idx === 0 ? 'px-6 border-r border-slate-800/50' : 'text-center border-l border-slate-800/50'}`}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              
              <tbody>
                {isLoading ? (
                  <tr><td colSpan={columns.length} className="p-12 text-center text-slate-400 font-bold">Modelando Estrutura de Rateio...</td></tr>
                ) : (
                  table.getRowModel().rows.map(row => (
                    <tr key={row.id} className={`border-b border-slate-100 transition-colors hover:bg-blue-50/50 ${rowSelecionada?.chave_matriz === row.original.chave_matriz ? 'bg-blue-50/80' : row.depth === 0 ? 'bg-slate-50/50' : 'bg-white'}`}>
                      {row.getVisibleCells().map((cell, idx) => (
                        <td key={cell.id} className={`align-middle ${idx === 0 ? 'border-r border-slate-100' : 'border-l border-slate-100'}`}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}