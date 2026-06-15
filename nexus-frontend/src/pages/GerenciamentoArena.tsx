import React, { useState, useMemo, useEffect, useCallback } from 'react';
import axios from 'axios';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, ColumnDef 
} from '@tanstack/react-table';
import { 
  ChevronRight, ChevronDown, Package, Boxes, LayoutGrid, 
  Target, Save, Shield, ShieldAlert, BarChart3, Activity, Wand2, TrendingUp, TrendingDown, ShieldCheck
} from 'lucide-react';
import { 
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
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

const VarBadge = ({ atual = 0, anterior = 0, dark = false }: { atual?: number, anterior?: number, dark?: boolean }) => {
  const v = calcVar(atual, anterior);
  if (v === 0 || anterior === 0) return null;
  const isPos = v >= 0;
  
  if (dark) {
    return (
      <span className={`text-[9px] font-black flex items-center gap-0.5 px-1 py-0.5 rounded ${isPos ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
        {isPos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />} {Math.abs(v).toFixed(1)}%
      </span>
    );
  }
  
  return (
    <span className={`text-[9px] font-black flex items-center gap-0.5 px-1.5 py-0.5 rounded ${isPos ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
      {isPos ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />} {Math.abs(v).toFixed(1)}%
    </span>
  );
};

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

export default function GerenciamentoArena({ usuarioSessao }: any) {
  const [dadosBase, setDadosBase] = useState<any[]>([]);
  const [isTopDownFechado, setIsTopDownFechado] = useState(true);
  const [isDemandFechado, setIsDemandFechado] = useState(false);
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
      setIsDemandFechado(res.data.is_demand_fechado); 
      setCelulasEditadas({});
    } catch (e) { console.error("Erro ao carregar:", e); } 
    finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

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

  const getStaticTD = useCallback((row: any, mesBanco: string): { vol: number, rec: number, metaRec: number } => {
    if (row.tipo === 'produto') {
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return { vol: m?.vol_td || 0, rec: m?.receita_td || 0, metaRec: m?.receita_meta || 0 };
    }
    return (row.subRows || []).reduce((acc: any, child: any) => {
        const c = getStaticTD(child, mesBanco);
        return { vol: acc.vol + c.vol, rec: acc.rec + c.rec, metaRec: acc.metaRec + c.metaRec };
    }, { vol: 0, rec: 0, metaRec: 0 });
  }, []);

  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    if (!isTopDownFechado || isDemandFechado) return;

    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };

      const findNode = (nodes: any[]): any => {
        for (const n of nodes) {
          if (n.chave_matriz === chaveStr) return n;
          if (n.subRows) {
            const found = findNode(n.subRows);
            if (found) return found;
          }
        }
        return null;
      };

      const targetNode = findNode(dadosBase);
      if (!targetNode) return nextEdits;

      const leaves: any[] = [];
      const getLeaves = (n: any) => {
        if (n.tipo === 'produto') leaves.push(n);
        else if (n.subRows) n.subRows.forEach(getLeaves);
      };
      getLeaves(targetNode);

      const totalHist = leaves.reduce((sum, leaf) => sum + (leaf.vol_historico_mix || 1), 0);

      const fractions = leaves.map(leaf => {
          const weight = leaf.vol_historico_mix || 1;
          const exact = (weight / totalHist) * novoValor;
          const intVal = Math.floor(exact);
          return { leaf, intVal, rem: exact - intVal };
      });

      let allocated = fractions.reduce((sum, item) => sum + item.intVal, 0);
      let remainder = novoValor - allocated;

      fractions.sort((a, b) => b.rem - a.rem);
      for (let i = 0; i < remainder; i++) {
          if (i < fractions.length) fractions[i].intVal++;
      }

      fractions.forEach(item => {
          if (!nextEdits[item.leaf.chave_matriz]) nextEdits[item.leaf.chave_matriz] = {};
          nextEdits[item.leaf.chave_matriz][mesBanco] = { novo_volume: item.intVal };
      });

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

  const totalSKUs = useMemo(() => {
    let count = 0;
    const countLeaves = (nodes: any[]) => {
      nodes.forEach(n => {
        if (n.tipo === 'produto') count++;
        else if (n.subRows) countLeaves(n.subRows);
      });
    };
    countLeaves(dadosBase);
    return count;
  }, [dadosBase]);

  const totaisGeraisTelaAtual = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number, orc: number }> = {};
    colunasData?.forEach((m: any) => {
      let vol = 0; let fat = 0; let orc = 0;
      dadosBase.forEach((rootNode: any) => {
        vol += getDynamicVol(rootNode, m.mes_banco);
        fat += getDynamicRec(rootNode, m.mes_banco);
        orc += getStaticTD(rootNode, m.mes_banco).metaRec;
      });
      totais[m.mes_banco] = { vol, fat, orc };
    });
    return totais;
  }, [dadosBase, getDynamicVol, getDynamicRec, getStaticTD, colunasData]);


  const PainelSaudabilidade = ({ rowData }: { rowData: any }) => {
    const chave = rowData.chave_matriz;
    const chartData = dadosGraficoCache[chave];

    const kpis = useMemo(() => {
      let recBU = 0; let recIA = 0; let recTD = 0; let recMeta = 0; let volBU = 0;
      
      (rowData?.meses || []).forEach((m: any) => {
          const vBU = getDynamicVol(rowData, m.mes_banco);
          volBU += vBU;
          recBU += vBU * (m.pmv || 0);
          recIA += (m.vol_ia || 0) * (m.pmv || 0);
          recTD += (m.vol_td || 0) * (m.pmv || 0);
          recMeta += (m.receita_meta || 0);
      });
      
      const pmvMedio = volBU > 0 ? (recBU / volBU) : 0;
      return { recBU, recIA, recTD, recMeta, volBU, pmvMedio };
    }, [rowData, celulasEditadas, getDynamicVol]);

    const chartDataDynamic = useMemo(() => {
        if (!chartData) return [];
        return chartData.map((d: any) => {
            let dynamicBU = d.BottomUpBase;
            if (d.TopDown !== null) {
               const m = rowData.meses?.find((x: any) => x.mes_banco === d.data_iso);
               if (m) dynamicBU = getDynamicVol(rowData, m.mes_banco);
            }
            return { ...d, BottomUp: dynamicBU };
        });
    }, [chartData, rowData, celulasEditadas, getDynamicVol]);

    return (
      <div className="w-full bg-[#1e2336] shadow-inner px-8 py-8 border-y border-slate-800">
        <div className="flex items-center gap-2 mb-6">
          <Activity className="w-5 h-5 text-blue-400" />
          <h3 className="font-bold text-lg text-white">Dossiê Analítico: <span className="text-slate-400 font-normal">{rowData.nome}</span></h3>
        </div>

        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-blue-900/30 border border-blue-500/50 p-4 rounded-xl shadow-[0_0_15px_rgba(59,130,246,0.15)]">
            <div className="text-[10px] font-black text-blue-400 mb-1 uppercase tracking-widest">Fat. Comercial</div>
            <div className="text-2xl font-black text-white">{formatMoeda(kpis.recBU)}</div>
          </div>
          <div className="bg-[#2a3045] border border-slate-700 p-4 rounded-xl">
            <div className="text-[10px] font-black text-slate-400 mb-1 uppercase tracking-widest">Fat. IA</div>
            <div className="text-2xl font-black text-slate-300">{formatMoeda(kpis.recIA)}</div>
          </div>
          <div className="bg-[#2a3045] border border-slate-700 p-4 rounded-xl">
            <div className="text-[10px] font-black text-slate-400 mb-1 uppercase tracking-widest">Fat. Marketing</div>
            <div className="text-2xl font-black text-slate-300">{formatMoeda(kpis.recTD)}</div>
          </div>
          <div className="bg-[#2a3045] border border-slate-700 p-4 rounded-xl">
            <div className="text-[10px] font-black text-slate-400 mb-1 uppercase tracking-widest">Orçamento</div>
            <div className="text-2xl font-black text-slate-300">{formatMoeda(kpis.recMeta)}</div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-[#23283d] border border-slate-700 p-4 rounded-xl h-[340px]">
            {loadingGrafico === chave ? (
              <div className="h-full flex items-center justify-center text-slate-500 font-bold animate-pulse">Extraindo inteligência temporal...</div>
            ) : chartDataDynamic.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartDataDynamic} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" />
                  <XAxis dataKey="name" tick={{fill: '#94a3b8', fontSize: 12}} tickMargin={10} axisLine={false} />
                  <YAxis tickFormatter={(val) => formatVolume(val)} tick={{fill: '#94a3b8', fontSize: 12}} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{backgroundColor: '#0f172a', borderColor: '#334155', color: '#fff'}} itemStyle={{color: '#fff'}} />
                  <Legend iconType="circle" wrapperStyle={{ paddingTop: '20px' }} />
                  
                  <Line type="monotone" dataKey="CicloAnterior" name="Ciclo Passado" stroke="#a855f7" strokeDasharray="4 4" strokeWidth={2} dot={false} connectNulls={true} />
                  <Line type="monotone" dataKey="IA" name="Modelo IA" stroke="#64748b" strokeDasharray="5 5" strokeWidth={2} dot={false} connectNulls={true} />
                  <Line type="monotone" dataKey="Realizado" name="Realizado" stroke="#94a3b8" strokeWidth={3} dot={{r: 4, strokeWidth: 2}} connectNulls={true} />
                  
                  <Line type="monotone" dataKey="BottomUp" name="Comercial" stroke="#3b82f6" strokeWidth={4} dot={{r: 5, fill: '#3b82f6', stroke: '#fff', strokeWidth: 2}} connectNulls={true} />
                  <Line type="monotone" dataKey="TopDown" name="Marketing" stroke="#a2711d" strokeDasharray="6 4" strokeWidth={2} dot={false} connectNulls={true} />
                </ComposedChart>
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

          // Exibe o PMV Médio Ponderado no frontend (Rec / Vol)
          let vTotal = 0; let rTotal = 0;
          (row.original.meses || []).forEach((m: any) => {
             vTotal += m.vol_bu;
             rTotal += m.vol_bu * m.pmv;
          });
          const pmvMedioNode = vTotal > 0 ? (rTotal / vTotal) : ((row.original.meses && row.original.meses[0]?.pmv) || 0);

          return (
            <div style={{ paddingLeft: `${depth * 1.5}rem` }} className="flex items-center gap-3 py-2">
              {hasChildren ? (
                <button onClick={() => row.toggleExpanded()} className="p-1 hover:bg-slate-200 text-slate-500 rounded transition-colors">
                  {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
              ) : <div className="w-6" />}
              
              <button 
                 onClick={(e) => { e.stopPropagation(); toggleChart(row.original); }} 
                 className={`p-1.5 rounded-lg border transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-blue-100 text-blue-600 border-blue-200' : 'bg-white hover:bg-slate-100 text-slate-400'}`}
              >
                <BarChart3 className="w-4 h-4" />
              </button>

              <div className={`w-7 h-7 rounded flex items-center justify-center border shrink-0 transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-blue-600 border-blue-700 text-white' : depth === 0 ? 'bg-slate-800 text-white border-slate-700' : depth === 1 ? 'bg-slate-100 text-slate-600' : 'bg-white border-slate-200 text-slate-400'}`}>
                {depth === 0 ? <LayoutGrid className="w-3.5 h-3.5" /> : depth === 1 ? <Boxes className="w-3.5 h-3.5" /> : <Package className="w-3.5 h-3.5" />}
              </div>
              
              <div className="flex flex-col overflow-hidden">
                <span className={`text-sm pr-4 truncate max-w-[300px] ${chartExpanded === row.original.chave_matriz ? 'text-blue-700 font-black' : !isProduto ? 'font-black text-slate-800' : 'font-semibold text-slate-600'}`}>
                  {getValue() as string}
                </span>
                {pmvMedioNode > 0 && (
                  <span className="text-[10px] text-slate-400 font-bold mt-0.5 tracking-widest uppercase">
                    PMV Médio: {formatMoeda(pmvMedioNode)}
                  </span>
                )}
              </div>
            </div>
          );
        },
      }
    ];

    colunasData.forEach((mes: any) => {
      cols.push({
        id: mes.mes_banco,
        header: mes.mes_str,
        cell: ({ row }) => {
          const vBU = getDynamicVol(row.original, mes.mes_banco);
          const tdData = getStaticTD(row.original, mes.mes_banco);
          const pmv = row.original.meses?.find((x: any) => x.mes_banco === mes.mes_banco)?.pmv || 0;
          const rBU = vBU * pmv;
          const rMeta = tdData.metaRec;

          return (
            <div className="flex flex-col items-center justify-center p-1.5 min-w-[140px]">
              <div className="flex items-center gap-1 text-[10px] font-black text-slate-400 mb-1.5">
                 <Target className="w-3 h-3 text-slate-300" /> Marketing: {formatVolume(tdData.vol)}
              </div>
              
              <div className={`w-full max-w-[120px] border rounded-lg px-2 py-1.5 shadow-sm transition-colors focus-within:ring-2 focus-within:ring-blue-500/50 focus-within:border-blue-500 ${!isTopDownFechado || isDemandFechado ? 'bg-slate-50 border-slate-200' : 'bg-white border-blue-200 hover:border-blue-400'}`}>
                 <SmartInput value={vBU} onChange={(val) => handleEditCell(row.original.chave_matriz, mes.mes_banco, val)} disabled={!isTopDownFechado || isDemandFechado} />
              </div>

              <div className="flex items-center gap-1.5 mt-1.5">
                 <span className="text-[11px] font-bold text-blue-600 bg-blue-50/50 px-2 py-0.5 rounded tracking-tight border border-blue-100">
                    {formatMoeda(rBU)}
                 </span>
                 {rMeta > 0 && <VarBadge atual={rBU} anterior={rMeta} />}
              </div>

              <div className="flex flex-col items-center mt-1.5 pt-1.5 border-t border-slate-100 w-full">
                 <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest">Orç: {formatMoeda(rMeta)}</span>
              </div>
            </div>
          );
        },
      });
    });

    return cols;
  }, [colunasData, celulasEditadas, chartExpanded, isTopDownFechado, isDemandFechado]);

  const table = useReactTable({
    data: dadosBase,
    columns,
    state: { expanded },
    onExpandedChange: setExpanded,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

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
        
        <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
                  <BarChart3 className="w-8 h-8 text-blue-600" /> S&OP <span className="text-blue-600">Comercial</span>
              </h1>
              <p className="text-slate-500 mt-1 font-medium">Proposta Comercial</p>
            </div>

            <div className="flex items-center gap-4">
              <button onClick={handleSalvarRascunho} disabled={!isTopDownFechado || isDemandFechado} className="px-5 py-2.5 font-bold rounded-xl transition-all flex items-center gap-2 border bg-white text-slate-700 border-slate-300 hover:bg-slate-50 disabled:opacity-50">
                  <Save className="w-4 h-4" /> Salvar Rascunho
              </button>
              <button onClick={handleCongelar} disabled={!isTopDownFechado || isDemandFechado} className="px-6 py-2.5 font-bold rounded-xl shadow-lg transition-all flex items-center gap-2 text-white bg-blue-600 hover:bg-blue-500 shadow-blue-900/20 disabled:opacity-50">
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

        {isDemandFechado && !isLoading && (
            <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 p-5 rounded-xl flex items-center gap-4 shadow-sm">
                <ShieldCheck className="w-6 h-6 text-emerald-500" />
                <div><h3 className="font-bold uppercase text-xs">Etapa Concluída</h3><p className="text-sm">O modelo Bottom-Up foi cravado e os volumes já foram transmitidos para a etapa de Supply.</p></div>
            </div>
        )}

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

              {dadosBase.length > 0 && (
                <tfoot className="bg-slate-900 sticky bottom-0 z-20 shadow-[0_-10px_40px_rgba(0,0,0,0.1)] border-t border-slate-800">
                  <tr>
                    <td className="px-6 py-5 border-r border-slate-800/50">
                      <div className="flex flex-col">
                        <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total do Ciclo ({totalSKUs} SKUs)</span>
                        <span className="font-bold text-sm text-white">SUMÁRIO GERENCIAL</span>
                      </div>
                    </td>
                    {colunasData?.map((m: any, i: number) => {
                       const simuladoFat = totaisGeraisTelaAtual[m.mes_banco]?.fat || 0;
                       const simuladoVol = totaisGeraisTelaAtual[m.mes_banco]?.vol || 0;
                       const orcamentoFat = totaisGeraisTelaAtual[m.mes_banco]?.orc || 0;

                       return (
                          <td key={i} className="px-4 py-4 border-l border-slate-800/50 text-center">
                            <div className="flex flex-col items-center">
                              <span className="font-black text-white text-base">
                                {formatVolume(simuladoVol)} <span className="text-[10px] text-slate-400 font-medium ml-0.5">cx</span>
                              </span>
                              
                              <div className="flex items-center gap-1.5 mt-1">
                                 <span className="font-bold text-blue-400 text-xs tracking-tight bg-blue-400/10 px-2 py-0.5 rounded">
                                   {formatMoeda(simuladoFat)}
                                 </span>
                                 {orcamentoFat > 0 && <VarBadge atual={simuladoFat} anterior={orcamentoFat} dark />}
                              </div>

                              <div className="flex items-center justify-center gap-1 mt-1.5 pt-1.5 border-t border-slate-800 w-full">
                                <span className="text-[9px] text-slate-500 font-bold tracking-widest uppercase">Orç: {formatMoeda(orcamentoFat)}</span>
                              </div>
                            </div>
                          </td>
                      );
                    })}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}