import React, { useState, useMemo, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, ColumnDef 
} from '@tanstack/react-table';
import { 
  ChevronRight, ChevronDown, Package, Boxes, LayoutGrid, 
  Target, Save, Shield, ShieldAlert, BarChart3, Activity, Wand2
} from 'lucide-react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
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

// =========================================================================
// COMPONENTES AUXILIARES
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

const AiInsightBox = ({ alvo, tipo, pmv, volume, receita }: { alvo: string, tipo: string, pmv: number, volume: number, receita: number }) => {
  const [insight, setInsight] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchInsight = async () => {
    setLoading(true);
    try {
      const prompt = `Gere uma análise executiva de S&OP para ${alvo} (Nível: ${tipo}). Volume BU: ${volume} CX. Receita Projetada: R$ ${receita.toFixed(2)}. Foque em rentabilidade e tendências comerciais comparadas ao Top-Down.`;
      const res = await axios.post('/api/v1/ai-sql/perguntar', { pergunta: prompt });
      setInsight(res.data.resposta);
    } catch (e) { setInsight("Erro ao comunicar com a IA Nexus."); } 
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800 rounded-xl p-5 border border-slate-700 flex flex-col h-full shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-slate-200 flex items-center gap-2"><Wand2 className="w-4 h-4 text-blue-400" /> Nexus AI Insight 360°</h4>
        <button onClick={fetchInsight} disabled={loading} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors">
          {loading ? "Processando..." : "Gerar Diagnóstico"}
        </button>
      </div>
      <div className="flex-1 text-sm text-slate-300 leading-relaxed overflow-y-auto pr-2">
        {loading ? (
          <div className="animate-pulse flex flex-col gap-2"><div className="h-2 bg-slate-700 rounded w-full"></div><div className="h-2 bg-slate-700 rounded w-5/6"></div></div>
        ) : insight ? <div className="whitespace-pre-wrap">{insight}</div> : (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 opacity-50"><Shield className="w-8 h-8 mb-2" /><span>Nenhuma análise gerada.</span></div>
        )}
      </div>
    </div>
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
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

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

  // =========================================================================
  // LÓGICA DE RATEIO HISTÓRICO E EXTRAÇÃO DINÂMICA
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

  const toggleChart = async (node: any) => {
    const chave = node.chave_matriz;
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

  // =========================================================================
  // O DOSSIÊ EXPANSÍVEL DA LINHA (Gráfico 100% Linhas e Cards Embutidos)
  // =========================================================================
  const PainelSaudabilidade = ({ rowData }: { rowData: any }) => {
    const chave = rowData.chave_matriz;
    const chartData = dadosGraficoCache[chave];

    const kpis = useMemo(() => {
      let recBU = 0; let recIA = 0; let recTD = 0; let recMeta = 0; let volBU = 0;
      let pmvAcc = 0; let count = 0;

      (rowData?.meses || []).forEach((m: any) => {
          const vBU = getDynamicVol(rowData, m.mes_banco);
          volBU += vBU;
          recBU += vBU * (m.pmv || 0);
          recIA += (m.vol_ia || 0) * (m.pmv || 0);
          recTD += (m.vol_td || 0) * (m.pmv || 0);
          recMeta += (m.vol_meta || 0) * (m.pmv || 0);

          if(m.pmv) { pmvAcc += m.pmv; count++; }
      });
      return { recBU, recIA, recTD, recMeta, volBU, pmvMedio: count > 0 ? (pmvAcc / count) : 0 };
    }, [rowData, celulasEditadas, getDynamicVol]);

    const chartDataDynamic = useMemo(() => {
        if (!chartData) return [];
        return chartData.map((d: any) => {
            let dynamicBU = d.BottomUpBase;
            if (d.TopDown !== null) {
               const m = rowData.meses?.find((x: any) => x.mes_str === d.name);
               if (m) dynamicBU = getDynamicVol(rowData, m.mes_banco);
            }
            return { ...d, BottomUp: dynamicBU };
        });
    }, [chartData, rowData, celulasEditadas, getDynamicVol]);

    return (
      <div className="w-full bg-slate-900 shadow-inner px-8 py-8 border-y border-slate-800">
        <div className="flex items-center gap-2 mb-6">
          <Activity className="w-5 h-5 text-blue-400" />
          <h3 className="font-bold text-lg text-white">Dossiê Analítico: <span className="text-slate-400 font-normal">{rowData.nome}</span></h3>
        </div>

        {/* 4 CARDS DE RECEITA (Orçamento garantido por categoria/nível) */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-blue-900/30 border border-blue-500/50 p-4 rounded-xl shadow-[0_0_15px_rgba(59,130,246,0.15)]">
            <div className="text-[10px] font-black text-blue-400 mb-1 uppercase tracking-widest">Sua Proposta (Vol_BU)</div>
            <div className="text-xl font-black text-white">{formatMoeda(kpis.recBU)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="text-[10px] font-black text-slate-400 mb-1 uppercase tracking-widest">Sinal de IA</div>
            <div className="text-xl font-black text-slate-300">{formatMoeda(kpis.recIA)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="text-[10px] font-black text-slate-400 mb-1 uppercase tracking-widest">Âncora Diretoria (TD)</div>
            <div className="text-xl font-black text-slate-300">{formatMoeda(kpis.recTD)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="text-[10px] font-black text-slate-400 mb-1 uppercase tracking-widest">Orçamento/Budget Oficial</div>
            <div className="text-xl font-black text-slate-300">{formatMoeda(kpis.recMeta)}</div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-slate-800 border border-slate-700 p-4 rounded-xl h-[340px]">
            {loadingGrafico === chave ? (
              <div className="h-full flex items-center justify-center text-slate-500 font-bold animate-pulse">Extraindo inteligência temporal (24 meses)...</div>
            ) : chartDataDynamic.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartDataDynamic} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" />
                  <XAxis dataKey="name" tick={{fill: '#94a3b8', fontSize: 12}} tickMargin={10} axisLine={false} />
                  <YAxis tickFormatter={(val) => formatVolume(val)} tick={{fill: '#94a3b8', fontSize: 12}} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{backgroundColor: '#0f172a', borderColor: '#334155', color: '#fff'}} itemStyle={{color: '#fff'}} />
                  <Legend iconType="circle" wrapperStyle={{ paddingTop: '20px' }} />
                  
                  {/* TODAS AS 5 CURVAS EM LINHA */}
                  <Line type="monotone" dataKey="Realizado" name="Realizado (Histórico)" stroke="#64748b" strokeWidth={2} dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="IA" name="Modelo IA (Baseline)" stroke="#ec4899" strokeDasharray="3 3" strokeWidth={2} dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="CicloAnterior" name="Lag 1 (Ciclo Passado)" stroke="#f59e0b" strokeWidth={2} dot={false} connectNulls={false} />
                  
                  <Line type="monotone" dataKey="TopDown" name="Top-Down (Meta Fixo)" stroke="#cbd5e1" strokeDasharray="6 4" strokeWidth={2} dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="BottomUp" name="Sua Proposta Dinâmica (BU)" stroke="#3b82f6" strokeWidth={4} dot={{r: 5, fill: '#3b82f6', stroke: '#fff', strokeWidth: 2}} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : null}
          </div>
          <div className="col-span-1 h-[340px]">
             <AiInsightBox alvo={rowData.nome} tipo={rowData.tipo} pmv={kpis.pmvMedio} volume={kpis.volBU} receita={kpis.recBU} />
          </div>
        </div>
      </div>
    );
  };

  // =========================================================================
  // COLUNAS TANSTACK TABLE (Apenas M2, M3 e M4)
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
            <div style={{ paddingLeft: `${depth * 1.5}rem` }} className="flex items-center gap-3 py-2">
              {hasChildren ? (
                <button onClick={() => row.toggleExpanded()} className="p-1 hover:bg-slate-200 text-slate-500 rounded transition-colors">
                  {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
              ) : <div className="w-6" />}
              
              {/* BOTÃO DO GRÁFICO - ACIONA O DOSSIÊ DA LINHA */}
              <button 
                 onClick={(e) => { e.stopPropagation(); toggleChart(row.original); }} 
                 className={`p-1.5 rounded-lg border transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-blue-100 text-blue-600 border-blue-200' : 'bg-white hover:bg-slate-100 text-slate-400'}`}
              >
                <BarChart3 className="w-4 h-4" />
              </button>

              <div className={`w-7 h-7 rounded flex items-center justify-center border shrink-0 transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-blue-600 border-blue-700 text-white' : depth === 0 ? 'bg-slate-800 text-white border-slate-700' : depth === 1 ? 'bg-slate-100 text-slate-600' : 'bg-white border-slate-200 text-slate-400'}`}>
                {depth === 0 ? <LayoutGrid className="w-3.5 h-3.5" /> : depth === 1 ? <Boxes className="w-3.5 h-3.5" /> : <Package className="w-3.5 h-3.5" />}
              </div>
              
              <span className={`text-sm pr-4 truncate max-w-[300px] ${chartExpanded === row.original.chave_matriz ? 'text-blue-700 font-black' : !isProduto ? 'font-black text-slate-800' : 'font-semibold text-slate-600'}`}>
                {getValue() as string}
              </span>
            </div>
          );
        },
      }
    ];

    // Colunas exclusivas de M2, M3 e M4
    colunasData.forEach((mes: any) => {
      cols.push({
        id: mes.mes_banco,
        header: mes.mes_str,
        cell: ({ row }) => {
          const vBU = getDynamicVol(row.original, mes.mes_banco);
          const tdData = getStaticTD(row.original, mes.mes_banco);
          const pmv = row.original.meses?.find((x: any) => x.mes_banco === mes.mes_banco)?.pmv || 0;
          const rBU = vBU * pmv;

          return (
            <div className="flex flex-col items-center justify-center p-1.5 min-w-[140px]">
              <div className="flex items-center gap-1 text-[10px] font-black text-slate-400 mb-1.5">
                 <Target className="w-3 h-3 text-slate-300" /> TD: {formatVolume(tdData.vol)}
              </div>
              
              <div className={`w-full max-w-[120px] border rounded-lg px-2 py-1.5 shadow-sm transition-colors focus-within:ring-2 focus-within:ring-blue-500/50 focus-within:border-blue-500 ${!isTopDownFechado ? 'bg-slate-50 border-slate-200' : 'bg-white border-blue-200 hover:border-blue-400'}`}>
                 <SmartInput value={vBU} onChange={(val) => handleEditCell(row.original.chave_matriz, mes.mes_banco, val)} disabled={!isTopDownFechado} />
              </div>

              <span className="text-[10px] font-bold text-blue-600 bg-blue-50/50 px-2 py-0.5 rounded mt-1.5 tracking-tight border border-blue-100">
                 {formatMoeda(rBU)}
              </span>
            </div>
          );
        },
      });
    });

    return cols;
  }, [colunasData, celulasEditadas, chartExpanded, isTopDownFechado]);

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
    if (!confirm("Esta ação gravará definitivamente os volumes em vol_supply, vol_final e vol_meta. Deseja prosseguir?")) return;
    try {
      await axios.post(`/api/v1/consensus/gerenciamento/congelar`, gerarPayloadFolhas());
      alert("Volumes travados e bastão passado com sucesso!");
      fetchData();
    } catch (e: any) { alert("Erro ao aprovar: " + (e.response?.data?.detail || e.message)); }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32 font-sans">
      <div className="max-w-[1400px] mx-auto mb-8 flex flex-col gap-6">
        
        {/* HEADER LIMPO (Sem Cards) */}
        <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
                  <BarChart3 className="w-8 h-8 text-blue-600" /> S&OP <span className="text-blue-600">Comercial</span>
              </h1>
              <p className="text-slate-500 mt-1 font-medium">Modelagem e Decisão de Portfólio Global (Apenas Vol_BU)</p>
            </div>

            <div className="flex items-center gap-4">
              <button onClick={handleSalvarRascunho} disabled={!isTopDownFechado} className="px-5 py-2.5 font-bold rounded-xl transition-all flex items-center gap-2 border bg-white text-slate-700 border-slate-300 hover:bg-slate-50 disabled:opacity-50">
                  <Save className="w-4 h-4" /> Salvar Rascunho
              </button>
              <button onClick={handleCongelar} disabled={!isTopDownFechado} className="px-6 py-2.5 font-bold rounded-xl shadow-lg transition-all flex items-center gap-2 text-white bg-blue-600 hover:bg-blue-500 shadow-blue-900/20 disabled:opacity-50">
                  <Shield className="w-4 h-4" /> Travar e Enviar Volumes
              </button>
            </div>
        </div>

        {!isTopDownFechado && !isLoading && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 p-5 rounded-xl flex items-center gap-4 shadow-sm">
                <ShieldAlert className="w-6 h-6 text-amber-500" />
                <div><h3 className="font-bold uppercase text-xs">Bloqueio de Ciclo</h3><p className="text-sm">O modelo Bottom-Up aguarda a formalização Top-Down pela Diretoria.</p></div>
            </div>
        )}

        {/* TANSTACK TABLE - VISÃO ÚNICA (M2, M3 e M4 COM DOSSIÊ EXPANSÍVEL) */}
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
                    <React.Fragment key={row.id}>
                      <tr className={`border-b border-slate-100 transition-colors hover:bg-blue-50/50 ${chartExpanded === row.original.chave_matriz ? 'bg-blue-50/80' : row.depth === 0 ? 'bg-slate-50/50' : 'bg-white'}`}>
                        {row.getVisibleCells().map((cell, idx) => (
                          <td key={cell.id} className={`align-middle ${idx === 0 ? 'border-r border-slate-100' : 'border-l border-slate-100'}`}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                      {/* DOSSIÊ NA LINHA (COM CARDS E GRÁFICO 100% LINHAS) */}
                      {chartExpanded === row.original.chave_matriz && (
                        <tr>
                          <td colSpan={columns.length} className="p-0 border-b-2 border-blue-500">
                             <PainelSaudabilidade rowData={row.original} />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
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