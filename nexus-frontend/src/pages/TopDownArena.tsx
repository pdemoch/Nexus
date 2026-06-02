import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import { 
  ChevronRight, ChevronDown, Lock, Unlock, Search, X, 
  Package, Boxes, LayoutGrid, Download, BarChart2, Activity, Shield,
  Wand2, Target, TrendingUp, TrendingDown, Save
} from 'lucide-react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
} from 'recharts';

// =====================================================================
// HELPERS DE FORMATAÇÃO
// =====================================================================
const formatVolume = (val: number) => new Intl.NumberFormat('pt-BR').format(Math.round(val || 0));
const formatMoeda = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val || 0);

// =====================================================================
// COMPONENTE: SMART INPUT (Sem Perda de Foco)
// =====================================================================
const SmartInput = ({ value, onChange, disabled }: { value: number, onChange: (val: number) => void, disabled: boolean }) => {
  const [localVal, setLocalVal] = useState(value ? formatVolume(value) : '0');
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (!isFocused) setLocalVal(value ? formatVolume(value) : '0');
  }, [value, isFocused]);

  const handleFocus = () => {
    setIsFocused(true);
    setLocalVal(localVal.replace(/\./g, ''));
  };

  const handleBlur = () => {
    setIsFocused(false);
    const num = parseInt(localVal.replace(/\D/g, ''), 10) || 0;
    setLocalVal(formatVolume(num));
    onChange(num);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.currentTarget.blur();
  };

  return (
    <input
      type="text"
      value={localVal}
      disabled={disabled}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      onChange={(e) => setLocalVal(e.target.value)}
      className={`w-full bg-transparent border-none text-right focus:outline-none focus:ring-1 focus:ring-blue-500 rounded px-1
        ${disabled ? 'text-slate-400 font-medium' : 'text-blue-700 font-bold bg-blue-50/50'}`}
    />
  );
};

// =====================================================================
// COMPONENTE: CAIXA DE INSIGHT IA
// =====================================================================
const AiInsightBox = ({ alvo, tipo, pmv, volume, receita }: { alvo: string, tipo: string, pmv: number, volume: number, receita: number }) => {
  const [insight, setInsight] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchInsight = async () => {
    setLoading(true);
    try {
      const prompt = `Gere uma análise executiva de S&OP (máx 3 parágrafos) para o alvo ${alvo} do tipo ${tipo}. O volume top-down planeado é ${volume} CX, com PMV médio de R$ ${pmv.toFixed(2)} e Receita de R$ ${receita.toFixed(2)}. Fale sobre sazonalidade e riscos.`;
      const res = await axios.post('/api/v1/ai-sql/perguntar', { pergunta: prompt });
      setInsight(res.data.resposta);
    } catch (e) {
      setInsight("Erro ao comunicar com a IA Nexus. Tente novamente.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-slate-800 rounded-xl p-5 border border-slate-700 flex flex-col h-full shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-slate-200 flex items-center gap-2">
          <Wand2 className="w-4 h-4 text-blue-400" /> Nexus AI Insight 360°
        </h4>
        <button onClick={fetchInsight} disabled={loading} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors flex items-center gap-2">
          {loading ? "Processando..." : "Gerar Diagnóstico"}
        </button>
      </div>
      <div className="flex-1 text-sm text-slate-300 leading-relaxed overflow-y-auto pr-2">
        {loading ? (
          <div className="animate-pulse flex flex-col gap-2">
            <div className="h-2 bg-slate-700 rounded w-full"></div>
            <div className="h-2 bg-slate-700 rounded w-5/6"></div>
            <div className="h-2 bg-slate-700 rounded w-4/6"></div>
          </div>
        ) : insight ? (
          <div className="whitespace-pre-wrap">{insight}</div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 opacity-50">
            <Shield className="w-12 h-12 mb-2" />
            <span>Nenhuma análise gerada.</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default function TopDownArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [filtrosDisponiveis, setFiltrosDisponiveis] = useState<{categorias: string[], segmentos: string[]}>({ categorias: [], segmentos: [] });
  const [filtros, setFiltros] = useState({ categoria: "TODAS", segmento: "TODOS" });
  const [busca, setBusca] = useState("");
  
  const [isFechado, setIsFechado] = useState(true);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);
  
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const params: any = { nocache: new Date().getTime() };
      if (filtros.categoria !== "TODAS") { params.categoria_filtro = 'categoria'; params.valor_filtro = filtros.categoria; }
      else if (filtros.segmento !== "TODOS") { params.categoria_filtro = 'segmento'; params.valor_filtro = filtros.segmento; }

      const [dadosRes, statusRes] = await Promise.all([
        axios.get('/api/v1/consensus/macro', { params }),
        axios.get('/api/v1/consensus/macro/status')
      ]);
      
      setDadosBrutos(dadosRes.data.dados || []);
      setIsFechado(statusRes.data.is_fechado); 
      setCelulasEditadas({});
      
      if (filtrosDisponiveis.categorias.length === 0 && dadosRes.data.dados) {
        const cats = Array.from(new Set(dadosRes.data.dados.map((c:any) => c.nome)));
        setFiltrosDisponiveis(p => ({ ...p, categorias: cats as string[] }));
      }
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [filtros]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Expande a árvore automaticamente quando o usuário faz uma busca
  useEffect(() => {
    if (busca.length > 2) {
      const newExpanded: Record<string, boolean> = {};
      const term = busca.toLowerCase();
      dadosBrutos.forEach(cat => {
        let catMatch = false;
        cat.subRows?.forEach((seg: any) => {
          let segMatch = false;
          seg.subRows?.forEach((prod: any) => {
             if (prod.nome?.toLowerCase().includes(term) || prod.produto?.toLowerCase().includes(term)) {
               segMatch = true; catMatch = true;
             }
          });
          if (segMatch || seg.nome?.toLowerCase().includes(term)) {
             newExpanded[seg.chave_matriz] = true; catMatch = true;
          }
        });
        if (catMatch || cat.nome?.toLowerCase().includes(term)) {
           newExpanded[cat.chave_matriz] = true;
        }
      });
      setExpanded(prev => ({ ...prev, ...newExpanded }));
    }
  }, [busca, dadosBrutos]);

  // Função recursiva para filtro de busca (Nome ou SKU)
  const filterData = (data: any[], search: string): any[] => {
    if (!search) return data;
    const term = search.toLowerCase();
    
    return data.map(cat => {
      const filteredSegs = (cat.subRows || []).map((seg: any) => {
        const filteredProds = (seg.subRows || []).filter((prod: any) => 
          prod.nome?.toLowerCase().includes(term) || prod.produto?.toLowerCase().includes(term)
        );
        if (filteredProds.length > 0 || seg.nome?.toLowerCase().includes(term)) {
          return { ...seg, subRows: filteredProds.length > 0 ? filteredProds : seg.subRows };
        }
        return null;
      }).filter(Boolean);

      if (filteredSegs.length > 0 || cat.nome?.toLowerCase().includes(term)) {
        return { ...cat, subRows: filteredSegs.length > 0 ? filteredSegs : cat.subRows };
      }
      return null;
    }).filter(Boolean);
  };

  const dadosFiltrados = useMemo(() => filterData(dadosBrutos, busca), [dadosBrutos, busca]);

  // Funções recursivas para somar volumes e receitas editadas em tempo real
  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const edicao = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (edicao !== undefined) return parseInt(edicao.novo_volume) || 0;
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.vol_ajustado || 0;
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

  const handleSalvar = async () => {
    if (Object.keys(celulasEditadas).length === 0) return alert("Nenhuma alteração para salvar.");
    try {
      const payload = {
        ajustes: Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
          Object.entries(meses).map(([mes_projetado, val]: any) => ({
            sku: chave.split('|').pop(),
            mes_projetado,
            novo_volume: parseInt(val.novo_volume, 10)
          }))
        )
      };
      await axios.post(`/api/v1/consensus/macro/salvar`, payload);
      alert("Alterações salvas no rascunho com sucesso!");
      fetchData(); 
    } catch (e: any) {
      alert("Erro ao salvar: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleCongelar = async () => {
    if (!confirm("Aviso Diretoria: Esta ação irá ratear os volumes aos clientes baseado no histórico real de vendas e fechará o ciclo. Deseja prosseguir?")) return;
    try {
      const payload = {
        ajustes: Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
          Object.entries(meses).map(([mes_projetado, val]: any) => ({
            sku: chave.split('|').pop(),
            mes_projetado,
            novo_volume: parseInt(val.novo_volume, 10)
          }))
        )
      };
      await axios.post(`/api/v1/consensus/macro/congelar`, payload);
      alert("Top-Down Congelado e Rateado com Sucesso!");
      fetchData();
    } catch (e: any) {
      alert("Erro ao congelar: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleExportExcel = () => {
    if (dadosBrutos.length === 0) return alert("Não há dados para exportar.");
    let csv = "Categoria,Segmento,SKU,Descrição,Mês,Projeção IA,Proposta Top-Down,PMV Médio,Receita\n";
    
    dadosBrutos.forEach(cat => {
      cat.subRows?.forEach((seg: any) => {
        seg.subRows?.forEach((prod: any) => {
          prod.meses.forEach((m: any) => {
             const edicao = celulasEditadas[prod.chave_matriz]?.[m.mes_banco];
             const volFinal = edicao !== undefined ? parseInt(edicao.novo_volume) : (m.vol_ajustado || 0);
             const rec = volFinal * (m.pmv || 0);
             csv += `"${cat.nome}","${seg.nome}","${prod.produto}","${prod.nome}","${m.mes_str}",${m.vol_ia},${volFinal},${m.pmv},${rec}\n`;
          });
        });
      });
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `Exportacao_TopDown_${new Date().getTime()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    if (isFechado) return;
    setCelulasEditadas((prev: any) => ({
      ...prev,
      [chaveStr]: { ...(prev[chaveStr] || {}), [mesBanco]: { novo_volume: novoValor } }
    }));
  };

  const toggleChart = async (node: any) => {
    const chave = node.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);

    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/macro/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  const PainelSaudabilidade = ({ rowData }: { rowData: any }) => {
    const chave = rowData.chave_matriz;
    const chartData = dadosGraficoCache[chave];

    const kpis = useMemo(() => {
      let volTD = 0; let volIA = 0; let rec = 0;
      (rowData?.meses || []).forEach((m: any) => {
          const vFinal = getDynamicVol(rowData, m.mes_banco);
          const rFinal = getDynamicRec(rowData, m.mes_banco);
          volTD += vFinal;
          volIA += (m.vol_ia || 0);
          rec += rFinal;
      });
      const gap = volTD - volIA;
      return { volTD, volIA, rec, gap, pmvMedio: volTD > 0 ? (rec / volTD) : 0 };
    }, [rowData, getDynamicVol, getDynamicRec]);

    const CustomTooltip = ({ active, payload, label }: any) => {
      if (active && payload && payload.length) {
        return (
          <div className="bg-slate-900 border border-slate-700 p-4 rounded-xl shadow-2xl z-50">
            <p className="text-white font-bold mb-3 pb-2 border-b border-slate-700">{label}</p>
            {payload.map((entry: any, idx: number) => (
              <div key={idx} className="flex items-center gap-3 py-1">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: entry.color }} />
                <span className="text-slate-300 text-sm w-36">{entry.name}:</span>
                <span className="text-white font-bold text-sm">{formatVolume(entry.value)} cx</span>
              </div>
            ))}
          </div>
        );
      }
      return null;
    };

    return (
      <div className="w-full bg-slate-900 shadow-inner px-8 py-8 border-y border-slate-800">
        <div className="flex items-center gap-2 mb-6">
          <Activity className="w-5 h-5 text-blue-400" />
          <h3 className="font-bold text-lg text-white">
            Dossiê Tático Executivo <span className="text-slate-500 font-normal">| {rowData.nome}</span>
          </h3>
        </div>

        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               <Target className="w-4 h-4 text-emerald-400" /> <span className="text-xs font-bold uppercase tracking-wider">Receita Projetada</span>
            </div>
            <div className="text-2xl font-black text-white">{formatMoeda(kpis.rec)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               <Activity className="w-4 h-4 text-blue-400" /> <span className="text-xs font-bold uppercase tracking-wider">Volume S&OP</span>
            </div>
            <div className="text-2xl font-black text-white">{formatVolume(kpis.volTD)} <span className="text-sm font-normal text-slate-500">CX</span></div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               <LayoutGrid className="w-4 h-4 text-purple-400" /> <span className="text-xs font-bold uppercase tracking-wider">PMV Médio</span>
            </div>
            <div className="text-2xl font-black text-white">{formatMoeda(kpis.pmvMedio)}</div>
          </div>
          <div className={`border p-4 rounded-xl ${kpis.gap < 0 ? 'bg-rose-900/20 border-rose-500/30' : 'bg-emerald-900/20 border-emerald-500/30'}`}>
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               {kpis.gap < 0 ? <TrendingDown className="w-4 h-4 text-rose-400" /> : <TrendingUp className="w-4 h-4 text-emerald-400" />} 
               <span className="text-xs font-bold uppercase tracking-wider">GAP Base IA</span>
            </div>
            <div className={`text-2xl font-black ${kpis.gap < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
              {kpis.gap > 0 ? '+' : ''}{formatVolume(kpis.gap)} <span className="text-sm font-normal opacity-70">CX</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-slate-800 border border-slate-700 p-4 rounded-xl" style={{ height: '340px' }}>
            {loadingGrafico === chave ? (
              <div className="h-full flex items-center justify-center text-slate-500">Extraindo inteligência temporal...</div>
            ) : chartData ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" />
                  <XAxis dataKey="name" tick={{fill: '#94a3b8', fontSize: 12}} tickMargin={10} axisLine={false} />
                  <YAxis tickFormatter={(val) => formatVolume(val)} tick={{fill: '#94a3b8', fontSize: 12}} tickLine={false} axisLine={false} />
                  <Tooltip content={<CustomTooltip />} />
                  <Legend iconType="circle" wrapperStyle={{ paddingTop: '20px' }} />
                  <Line type="monotone" dataKey="Realizado" name="Histórico Faturado" stroke="#f8fafc" strokeWidth={3} dot={{r: 4, strokeWidth: 2}} connectNulls={false} />
                  <Line type="monotone" dataKey="IA" name="Projeção IA" stroke="#64748b" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="CicloAnterior" name="Ciclo Anterior (Lag 1)" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="TopDown" name="Proposta Atual" stroke="#3b82f6" strokeWidth={3} dot={{r: 5, fill: '#3b82f6', stroke: '#fff', strokeWidth: 2}} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : null}
          </div>
          <div className="col-span-1" style={{ height: '340px' }}>
             <AiInsightBox alvo={rowData.nome} tipo={rowData.tipo} pmv={kpis.pmvMedio} volume={kpis.volTD} receita={kpis.rec} />
          </div>
        </div>
      </div>
    );
  };

  const colunasData = useMemo(() => dadosBrutos.length > 0 ? dadosBrutos[0].meses : [], [dadosBrutos]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number }> = {};
    colunasData.forEach((m: any) => {
      let vol = 0; let fat = 0;
      dadosBrutos.forEach((cat: any) => {
        vol += getDynamicVol(cat, m.mes_banco);
        fat += getDynamicRec(cat, m.mes_banco);
      });
      totais[m.mes_banco] = { vol, fat };
    });
    return totais;
  }, [dadosBrutos, getDynamicVol, getDynamicRec, colunasData]);

  const renderRow = (row: any, depth = 0) => {
    const isExpanded = expanded[row.chave_matriz];
    const hasChildren = row.subRows && row.subRows.length > 0;
    const isProduto = row.tipo === 'produto';

    return (
      <React.Fragment key={row.chave_matriz}>
        <tr className={`border-b border-slate-100 ${depth === 0 ? 'bg-white' : depth === 1 ? 'bg-slate-50/50' : 'bg-white'}`}>
          <td className="p-0 align-top">
            <div className="flex flex-col h-full">
              <div 
                className={`flex items-center justify-between px-6 py-4 cursor-pointer hover:bg-slate-50 transition-colors ${depth > 0 ? 'ml-' + (depth * 6) : ''}`}
                onClick={() => { if (hasChildren) setExpanded(p => ({ ...p, [row.chave_matriz]: !p[row.chave_matriz] })); }}
              >
                <div className="flex items-center gap-3">
                  {hasChildren ? (
                    isExpanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />
                  ) : (
                    <Package className="w-4 h-4 text-slate-300" />
                  )}
                  <div className="flex flex-col">
                    <span className={`font-bold ${isProduto ? 'text-slate-700 text-sm' : 'text-slate-800'}`}>
                      {row.nome}
                    </span>
                    {isProduto && <span className="text-[10px] font-medium text-slate-400 font-mono tracking-wider">{row.produto}</span>}
                  </div>
                </div>

                {isProduto && (
                  <button 
                    onClick={(e) => { e.stopPropagation(); toggleChart(row); }}
                    className={`p-2 rounded-lg border transition-colors ${chartExpanded === row.chave_matriz ? 'bg-blue-100 text-blue-600 border-blue-200' : 'hover:bg-slate-100 text-slate-400 border-transparent'}`}
                    title="Ver Gráficos e Insights"
                  >
                    <BarChart2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          </td>
          
          {row.meses?.map((m: any, idx: number) => {
            const isEdited = isProduto && celulasEditadas[row.chave_matriz]?.[m.mes_banco] !== undefined;
            const valorExibicao = getDynamicVol(row, m.mes_banco);
            const receitaExibicao = getDynamicRec(row, m.mes_banco);

            return (
              <td key={idx} className="p-0 border-l border-slate-100 align-top">
                <div className={`flex flex-col h-full min-h-[76px] ${isFechado ? 'bg-slate-50' : isEdited ? 'bg-blue-50/40' : 'hover:bg-slate-50'}`}>
                  
                  {isProduto && (
                    <div className="flex items-center justify-between px-3 py-1 bg-slate-50 border-b border-slate-100">
                      <span className="text-[10px] font-bold text-slate-400" title="Projeção IA Baseline">IA: {formatVolume(m.vol_ia)}</span>
                      <span className="text-[10px] font-bold text-slate-400" title="Volume Realizado Ciclo Anterior">Lag1: {formatVolume(m.vol_anterior)}</span>
                    </div>
                  )}

                  <div className="px-4 py-2 flex flex-col items-end justify-center flex-1">
                    <div className="w-24">
                      {isProduto ? (
                        <SmartInput value={valorExibicao} disabled={isFechado} onChange={(novoVol) => handleEditCell(row.chave_matriz, m.mes_banco, novoVol)} />
                      ) : (
                        <div className="w-full text-right font-bold text-slate-800 pr-1">{formatVolume(valorExibicao)}</div>
                      )}
                    </div>
                    <span className="text-[10px] font-bold text-emerald-500 tracking-tight pr-1 mt-0.5" title="Receita (R$) Prevista">
                      {formatMoeda(receitaExibicao)}
                    </span>
                  </div>

                </div>
              </td>
            );
          })}
        </tr>
        
        {isProduto && chartExpanded === row.chave_matriz && (
          <tr>
            <td colSpan={(colunasData?.length || 0) + 1} className="p-0">
              <PainelSaudabilidade rowData={row} />
            </td>
          </tr>
        )}

        {isExpanded && hasChildren && row.subRows.map((child: any) => renderRow(child, depth + 1))}
      </React.Fragment>
    );
  };

  return (
    <div className="w-full h-full flex flex-col bg-slate-50">
      <div className="bg-white border-b px-6 py-4 flex items-center justify-between shadow-sm z-10">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-blue-600 rounded-xl">
            <LayoutGrid className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-black text-slate-900 tracking-tight">Estratégia Top-Down</h1>
            <p className="text-sm text-slate-500 font-medium">Modelagem Executiva de Volume e Faturamento</p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button onClick={handleExportExcel} className="px-4 py-2 bg-white border shadow-sm text-slate-600 font-bold rounded-xl hover:bg-slate-50 flex items-center gap-2">
             <Download className="w-4 h-4" /> CSV
          </button>
          
          <div className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold border shadow-sm ${isFechado ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}>
            {isFechado ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
            {isFechado ? 'ESTRATÉGIA FECHADA' : 'ESTRATÉGIA ABERTA'}
          </div>

          <button onClick={handleSalvar} disabled={isFechado} className="px-4 py-2.5 bg-blue-50 hover:bg-blue-100 text-blue-700 font-bold border border-blue-200 rounded-xl transition-all disabled:opacity-50 flex items-center gap-2">
            <Save className="w-4 h-4" /> Salvar Rascunho
          </button>

          <button onClick={handleCongelar} disabled={isFechado} className="px-6 py-2.5 bg-slate-900 hover:bg-blue-600 text-white font-bold rounded-xl shadow-lg shadow-slate-900/20 transition-all disabled:opacity-50 flex items-center gap-2">
            <Shield className="w-4 h-4" /> Ratificar Top-Down
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto bg-slate-50/50 p-6">
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 flex flex-col max-h-[80vh]">
          
          <div className="p-4 border-b border-slate-100 flex items-center gap-4 bg-slate-50/50 rounded-t-2xl">
            <div className="relative flex-1 max-w-md">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input 
                type="text" 
                placeholder="Buscar produto ou SKU..." 
                value={busca}
                onChange={e => setBusca(e.target.value)}
                className="w-full pl-9 pr-4 py-2 rounded-xl border border-slate-200 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
            </div>
            <select 
              value={filtros.categoria} 
              onChange={e => setFiltros({categoria: e.target.value, segmento: "TODOS"})}
              className="px-4 py-2 rounded-xl border border-slate-200 text-sm font-bold text-slate-700 bg-white focus:ring-2 focus:ring-blue-500"
            >
              <option value="TODAS">Todas as Categorias</option>
              {filtrosDisponiveis.categorias.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>

          <div className="flex-1 overflow-auto relative">
            {isLoading ? (
              <div className="flex flex-col items-center justify-center h-64 text-slate-500">
                <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mb-4"></div>
                <span className="font-bold">Processando Cubo de Dados...</span>
              </div>
            ) : (
              <table className="w-full text-left border-collapse">
                <thead className="bg-white sticky top-0 z-20 shadow-sm">
                  <tr>
                    <th className="px-6 py-4 font-black text-xs text-slate-500 uppercase tracking-wider border-b w-[400px]">
                      Estrutura de Produto
                    </th>
                    {colunasData?.map((m: any, i: number) => (
                      <th key={i} className="px-6 py-4 font-black text-sm text-slate-800 border-b border-l text-right min-w-[160px]">
                        {m.mes_str}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {dadosFiltrados.map(cat => renderRow(cat, 0))}
                </tbody>
                
                {dadosBrutos.length > 0 && (
                  <tfoot className="bg-slate-900 sticky bottom-0 z-20 shadow-[0_-10px_40px_rgba(0,0,0,0.1)]">
                    <tr>
                      <td className="px-6 py-5">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total Consolidação</span>
                          <span className="font-bold text-sm text-white">SUMÁRIO GERENCIAL</span>
                        </div>
                      </td>
                      {colunasData?.map((m: any, i: number) => (
                        <td key={i} className="px-6 py-5 border-l border-slate-800/50 text-right">
                          <div className="flex flex-col items-end">
                            <span className="font-black text-white text-base">
                              {formatVolume(totaisGerais[m.mes_banco]?.vol || 0)} <span className="text-[10px] text-slate-400 font-medium ml-1">CX</span>
                            </span>
                            <span className="font-bold text-emerald-400 text-xs tracking-tight mt-1 bg-emerald-400/10 px-2 py-0.5 rounded">
                              {formatMoeda(totaisGerais[m.mes_banco]?.fat || 0)}
                            </span>
                          </div>
                        </td>
                      ))}
                    </tr>
                  </tfoot>
                )}
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}