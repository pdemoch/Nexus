import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import axios from 'axios';
import { 
  ChevronRight, ChevronDown, Lock, Unlock, Search, X, 
  Package, Boxes, LayoutGrid, BarChart2, Activity, Shield,
  Wand2, Target, TrendingUp, TrendingDown, Users, ShieldAlert, Save, Layers
} from 'lucide-react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
} from 'recharts';

const formatVolume = (val: number) => new Intl.NumberFormat('pt-BR').format(Math.round(val || 0));
const formatMoeda = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val || 0);

// =========================================================================
// COMPONENTES DE INPUT E IA
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
      className={`w-full bg-transparent border-none text-right focus:outline-none focus:ring-1 focus:ring-blue-500 rounded px-1
        ${disabled ? 'text-slate-400 font-medium cursor-not-allowed' : 'text-blue-700 font-bold bg-blue-50/50'}`}
    />
  );
};

const PercentInput = ({ pct, onChange, disabled }: { pct: number, onChange: (val: number) => void, disabled: boolean }) => {
  const [localVal, setLocalVal] = useState(pct.toFixed(2));
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (!isFocused) setLocalVal(pct.toFixed(2));
  }, [pct, isFocused]);

  const handleFocus = () => { setIsFocused(true); };
  const handleBlur = () => {
      setIsFocused(false);
      const parsed = parseFloat(localVal.replace(',', '.')) || 0;
      onChange(parsed);
      setLocalVal(parsed.toFixed(2));
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };

  return (
    <div className={`flex items-center gap-1 border rounded px-1.5 py-1 w-20 justify-between transition-colors shadow-inner ${disabled ? 'bg-slate-100 border-slate-200' : 'bg-white border-blue-300 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500'}`}>
      <input
        type="text" value={localVal} disabled={disabled} onFocus={handleFocus} onBlur={handleBlur} onChange={(e) => setLocalVal(e.target.value)} onKeyDown={handleKeyDown}
        className="w-full bg-transparent border-none text-right text-xs font-bold text-blue-700 outline-none p-0 focus:ring-0"
      />
      <span className="text-[10px] font-bold text-slate-400">%</span>
    </div>
  );
};

const AiInsightBox = ({ alvo, tipo, pmv, volume, receita }: { alvo: string, tipo: string, pmv: number, volume: number, receita: number }) => {
  const [insight, setInsight] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchInsight = async () => {
    setLoading(true);
    try {
      const prompt = `Gere uma análise executiva de S&OP (máx 3 parágrafos) para a carteira comercial de ${alvo} (Nível: ${tipo}). O volume proposto pelo time é de ${volume} CX, com PMV médio de R$ ${pmv.toFixed(2)} e Receita Projetada de R$ ${receita.toFixed(2)}. Foque em rentabilidade e tendências comerciais.`;
      const res = await axios.post('/api/v1/ai-sql/perguntar', { pergunta: prompt });
      setInsight(res.data.resposta);
    } catch (e) { setInsight("Erro ao comunicar com a IA Nexus. Tente novamente."); } 
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800 rounded-xl p-5 border border-slate-700 flex flex-col h-full shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-slate-200 flex items-center gap-2"><Wand2 className="w-4 h-4 text-blue-400" /> Nexus AI Insight 360°</h4>
        <button onClick={fetchInsight} disabled={loading} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors flex items-center gap-2">
          {loading ? "Processando..." : "Gerar Diagnóstico"}
        </button>
      </div>
      <div className="flex-1 text-sm text-slate-300 leading-relaxed overflow-y-auto pr-2">
        {loading ? (
          <div className="animate-pulse flex flex-col gap-2">
             <div className="h-2 bg-slate-700 rounded w-full"></div><div className="h-2 bg-slate-700 rounded w-5/6"></div><div className="h-2 bg-slate-700 rounded w-4/6"></div>
          </div>
        ) : insight ? <div className="whitespace-pre-wrap">{insight}</div> : (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 opacity-50">
            <Shield className="w-12 h-12 mb-2" /><span>Nenhuma análise gerada.</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default function GerenciamentoArena({ usuarioSessao }: any) {
  const [visaoAtiva, setVisaoAtiva] = useState<'carteira' | 'portfolio'>('carteira');
  const [dadosBase, setDadosBase] = useState<{ carteira: any[], portfolio: any[] }>({ carteira: [], portfolio: [] });
  const [isTopDownFechado, setIsTopDownFechado] = useState(true);
  
  const [busca, setBusca] = useState("");
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);
  
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const dadosBrutos = visaoAtiva === 'carteira' ? dadosBase.carteira : dadosBase.portfolio;
  const colunasData = dadosBrutos.length > 0 ? dadosBrutos[0].meses : [];

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento', { params: { nocache: new Date().getTime() } });
      setDadosBase({ carteira: res.data.dados.carteira || [], portfolio: res.data.dados.portfolio || [] });
      setIsTopDownFechado(res.data.is_topdown_fechado);
      setCelulasEditadas({});
    } catch (e) { console.error("Erro ao carregar gerenciamento:", e); } 
    finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleToggleVisao = (novaVisao: 'carteira' | 'portfolio') => {
      if (Object.keys(celulasEditadas).length > 0) {
          if (!confirm("Ao alternar de aba, os rascunhos não salvos serão perdidos. Deseja continuar ou prefere clicar em 'Salvar Rascunho' antes?")) return;
      }
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
      setBusca(""); 
      setVisaoAtiva(novaVisao);
  };

  const isAllClosed = dadosBase.carteira.length > 0 && dadosBase.carteira.every(coord => coord.status === 'Fechado');

  const handleToggleLock = async (regionalNome: string, statusAtual: string) => {
    const nextStatus = statusAtual === 'Fechado' ? 'Aberto' : 'Fechado';
    try {
      await axios.post('/api/v1/consensus/gerenciamento/toggle-lock', { regional: regionalNome, status: nextStatus });
      fetchData();
    } catch (e: any) { alert("Erro ao alterar trava da regional: " + e.message); }
  };

  const handleSalvarRascunho = async () => {
    if (Object.keys(celulasEditadas).length === 0) return alert("Nenhuma alteração para salvar.");
    try {
      const payload = {
        origem_ajuste: "Gerência Comercial (Bottom-Up)",
        visao: visaoAtiva,
        ajustes: Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
          Object.entries(meses).map(([mes_projetado, val]: any) => ({
              nivel: visaoAtiva,
              chave,
              mes_projetado,
              novo_volume: parseInt(val.novo_volume, 10)
          }))
        )
      };
      await axios.post(`/api/v1/consensus/gerenciamento/salvar`, payload);
      alert(visaoAtiva === 'portfolio' ? "Rateio Global do Portfólio concluído e distribuído na base!" : "Proposta Bottom-Up consolidada na matriz da sua Carteira!");
      fetchData();
    } catch (e: any) { alert("Erro ao salvar: " + (e.response?.data?.detail || e.message)); }
  };

  const handleCongelar = async () => {
    if (!confirm("Atenção Gerência: Esta ação salvará as edições, trancará as regionais visíveis na tela e passará a meta para a Fábrica (Supply). Deseja prosseguir?")) return;
    try {
      const payload = {
        origem_ajuste: "Gerência Comercial (Bottom-Up Final)",
        visao: visaoAtiva,
        ajustes: Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
          Object.entries(meses).map(([mes_projetado, val]: any) => ({
              nivel: visaoAtiva,
              chave,
              mes_projetado,
              novo_volume: parseInt(val.novo_volume, 10)
          }))
        )
      };
      await axios.post(`/api/v1/consensus/gerenciamento/congelar`, payload);
      alert("Gestão Comercial Ratificada com Sucesso!");
      fetchData();
    } catch (e: any) { alert("Erro ao aprovar: " + (e.response?.data?.detail || e.message)); }
  };

  const dadosProcessados = useMemo(() => {
    let processados = dadosBrutos;
    if (busca) {
        const lowerTerm = busca.toLowerCase();
        const filtrarArvore = (nodes: any[]): any[] => {
            return nodes.map(node => {
                const matchSelf = (node.nome && String(node.nome).toLowerCase().includes(lowerTerm)) ||
                                  (node.produto && String(node.produto).toLowerCase().includes(lowerTerm));
                let childMatches: any[] = []; 
                if (node.subRows?.length > 0) childMatches = filtrarArvore(node.subRows);
                if (matchSelf || childMatches.length > 0) return { ...node, subRows: matchSelf ? node.subRows : childMatches };
                return null;
            }).filter(Boolean);
        };
        processados = filtrarArvore(dadosBrutos);
    }
    return processados;
  }, [dadosBrutos, busca]);

  // =========================================================================
  // LEITURA RECURSIVA COM EDIÇÕES EM MEMÓRIA
  // =========================================================================
  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const edicao = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (edicao !== undefined) return isNaN(Number(edicao.novo_volume)) ? 0 : Number(edicao.novo_volume);
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.vol_ajustado !== undefined ? Number(m.vol_ajustado) : 0;
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

  // =========================================================================
  // MATEMÁTICA: ÂNCORAS SEPARADAS PARA PORTFÓLIO E CARTEIRA (VOL_TOPDOWN)
  // =========================================================================
  const totaisBaseAbaAtiva = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number }> = {};
    colunasData?.forEach((m: any) => {
      let vol = 0; let fat = 0;
      dadosBrutos.forEach((node: any) => {
        const mesData = node.meses?.find((x: any) => x.mes_banco === m.mes_banco);
        vol += (mesData?.vol_base || 0); // Lendo o vol_topdown
        fat += (mesData?.receita_base || 0);
      });
      totais[m.mes_banco] = { vol, fat };
    });
    return totais;
  }, [dadosBrutos, colunasData]);

  const totaisGeraisTelaAtual = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number }> = {};
    colunasData?.forEach((m: any) => {
      let vol = 0; let fat = 0;
      dadosProcessados.forEach((rootNode: any) => {
        vol += getDynamicVol(rootNode, m.mes_banco);
        fat += getDynamicRec(rootNode, m.mes_banco);
      });
      totais[m.mes_banco] = { vol, fat };
    });
    return totais;
  }, [dadosProcessados, getDynamicVol, getDynamicRec, colunasData]);

  const allColumns100Percent = useMemo(() => {
    if (visaoAtiva === 'portfolio') return true; 
    return colunasData.every((m: any) => {
      const ancora = totaisBaseAbaAtiva[m.mes_banco]?.fat || 0;
      const simulado = totaisGeraisTelaAtual[m.mes_banco]?.fat || 0;
      if (ancora === 0) return true;
      const percentual = (simulado / ancora) * 100;
      return percentual >= 99.0 && percentual <= 101.0; // Nova Zona de Tolerância
    });
  }, [visaoAtiva, colunasData, totaisBaseAbaAtiva, totaisGeraisTelaAtual]);

  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    if (!isTopDownFechado) return;
    
    if (visaoAtiva === 'carteira') {
        const coordRoot = chaveStr.split('|')[0];
        const nodeCoord = dadosBase.carteira.find(c => c.nome === coordRoot);
        if (nodeCoord && nodeCoord.status === 'Fechado') return;
    } else {
        if (isAllClosed) return;
    }

    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };

      const getVolActual = (chave: string, originalVol: number) => {
        if (nextEdits[chave]?.[mesBanco] !== undefined) return nextEdits[chave][mesBanco].novo_volume;
        return originalVol;
      };

      const distributeDown = (node: any, targetVolume: number) => {
        if (!nextEdits[node.chave_matriz]) nextEdits[node.chave_matriz] = {};
        nextEdits[node.chave_matriz][mesBanco] = { novo_volume: targetVolume };

        if (node.subRows && node.subRows.length > 0) {
          const currentTotalChildren = node.subRows.reduce((acc: number, child: any) => {
            const childMes = child.meses.find((m: any) => m.mes_banco === mesBanco);
            return acc + getVolActual(child.chave_matriz, childMes ? childMes.vol_ajustado : 0);
          }, 0);

          node.subRows.forEach((child: any, idx: number) => {
            const childMes = child.meses.find((m: any) => m.mes_banco === mesBanco);
            const childCurrent = getVolActual(child.chave_matriz, childMes ? childMes.vol_ajustado : 0);
            
            let childTarget = 0;
            if (currentTotalChildren > 0) {
              if (idx === node.subRows.length - 1) {
                const allocatedSoFar = node.subRows.slice(0, idx).reduce((sum: number, c: any) => sum + (nextEdits[c.chave_matriz]?.[mesBanco]?.novo_volume || 0), 0);
                childTarget = targetVolume - allocatedSoFar;
              } else {
                childTarget = Math.round((childCurrent / currentTotalChildren) * targetVolume);
              }
            } else {
              if (idx === node.subRows.length - 1) {
                const allocatedSoFar = Math.round(targetVolume / node.subRows.length) * idx;
                childTarget = targetVolume - allocatedSoFar;
              } else {
                childTarget = Math.round(targetVolume / node.subRows.length);
              }
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
            const foundInChild = rollupUp(node.subRows);
            if (foundInChild) {
              const newTotal = node.subRows.reduce((acc: number, child: any) => {
                const childMes = child.meses.find((m: any) => m.mes_banco === mesBanco);
                return acc + getVolActual(child.chave_matriz, childMes ? childMes.vol_ajustado : 0);
              }, 0);
              if (!nextEdits[node.chave_matriz]) nextEdits[node.chave_matriz] = {};
              nextEdits[node.chave_matriz][mesBanco] = { novo_volume: newTotal };
              return true;
            }
          }
        }
        return false;
      };

      rollupUp(dadosBrutos);
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
        const res = await axios.get('/api/v1/consensus/gerenciamento/grafico', { params: { chave_matriz: chave, visao: visaoAtiva } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  // =========================================================================
  // PAINEL DE SAUDABILIDADE (RESTAURADO COM RECHARTS)
  // =========================================================================
  const PainelSaudabilidade = ({ rowData }: { rowData: any }) => {
    const chave = rowData.chave_matriz;
    const chartData = dadosGraficoCache[chave];

    const kpis = useMemo(() => {
      let volAtual = 0; let volIA = 0; let rec = 0; let pmvAcc = 0; let count = 0;
      (rowData?.meses || []).forEach((m: any) => {
          const vFinal = getDynamicVol(rowData, m.mes_banco);
          volAtual += vFinal;
          volIA += (m.vol_ia || 0);
          rec += vFinal * (m.pmv || 0);
          if(m.pmv) { pmvAcc += m.pmv; count++; }
      });
      const gap = volAtual - volIA;
      return { volAtual, volIA, rec, gap, pmvMedio: count > 0 ? (pmvAcc / count) : 0 };
    }, [rowData, celulasEditadas]);

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
          <h3 className="font-bold text-lg text-white">Dossiê Tático Executivo <span className="text-slate-500 font-normal">| {rowData.nome}</span></h3>
        </div>

        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               <Target className="w-4 h-4 text-emerald-400" /> <span className="text-xs font-bold uppercase tracking-wider">Receita Prevista</span>
            </div>
            <div className="text-2xl font-black text-white">{formatMoeda(kpis.rec)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               <Activity className="w-4 h-4 text-blue-400" /> <span className="text-xs font-bold uppercase tracking-wider">Volume Proposto</span>
            </div>
            <div className="text-2xl font-black text-white">{formatVolume(kpis.volAtual)} <span className="text-sm font-normal text-slate-500">CX</span></div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="flex items-center gap-2 text-slate-400 mb-2">
               <LayoutGrid className="w-4 h-4 text-purple-400" /> <span className="text-xs font-bold uppercase tracking-wider">PMV Comercial</span>
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
          <div className="col-span-2 bg-slate-800 border border-slate-700 p-4 rounded-xl h-[340px]">
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
                  
                  <Line type="monotone" dataKey="Realizado" name="Histórico Faturado" stroke="#0f172a" strokeWidth={3} dot={{r: 4, strokeWidth: 2}} connectNulls={false} />
                  <Line type="monotone" dataKey="IA" name="Projeção IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="CicloAnterior" name="Ciclo Anterior (Lag 1)" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="TopDown" name="Proposta Comercial" stroke="#3b82f6" strokeWidth={3} dot={{r: 5, fill: '#3b82f6', stroke: '#fff', strokeWidth: 2}} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : null}
          </div>
          <div className="col-span-1 h-[340px]">
             <AiInsightBox alvo={rowData.nome} tipo={rowData.tipo} pmv={kpis.pmvMedio} volume={kpis.volAtual} receita={kpis.rec} />
          </div>
        </div>
      </div>
    );
  };

  const renderRow = (row: any, depth = 0) => {
    const isExpanded = expanded[row.chave_matriz];
    const hasChildren = row.subRows && row.subRows.length > 0;
    const isProduto = row.tipo === 'produto';
    
    let rowStatus = 'Aberto';
    let isRowFechado = false;
    
    if (visaoAtiva === 'carteira') {
        const coordRoot = row.chave_matriz.split('|')[0];
        const nodeCoord = dadosBase.carteira.find(c => c.nome === coordRoot);
        rowStatus = nodeCoord ? nodeCoord.status : 'Aberto';
        isRowFechado = rowStatus === 'Fechado';
    } else {
        isRowFechado = isAllClosed;
    }

    const renderIcon = () => {
        if (visaoAtiva === 'carteira') return depth === 0 ? <Users className="w-4 h-4" /> : depth === 1 ? <LayoutGrid className="w-4 h-4" /> : depth === 2 ? <Boxes className="w-4 h-4" /> : <Package className="w-4 h-4" />;
        return depth === 0 ? <LayoutGrid className="w-4 h-4" /> : depth === 1 ? <Boxes className="w-4 h-4" /> : <Package className="w-4 h-4" />;
    };

    return (
      <React.Fragment key={row.chave_matriz}>
        <tr className={`border-b transition-colors hover:bg-slate-50 ${depth === 0 ? 'bg-white shadow-[inset_0_-1px_0_rgba(0,0,0,0.05)]' : depth === 1 ? 'bg-slate-50/50' : 'bg-white'}`}>
          
          <td className="p-0 align-middle border-r border-slate-200">
            <div style={{ paddingLeft: `${depth * 2 + 1}rem` }} className={`flex items-center gap-3 py-3 min-w-[320px] h-full ${depth === 0 ? 'border-l-4 border-blue-500' : ''}`}>
              {hasChildren ? (
                <button onClick={() => setExpanded(p => ({ ...p, [row.chave_matriz]: !p[row.chave_matriz] }))} className="p-1.5 hover:bg-slate-200 text-slate-500 rounded-lg transition-colors">
                  {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : !isProduto ? <div className="w-8 text-center text-slate-300">•</div> : <div className="w-8" />}
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg border transition-colors ${chartExpanded === row.chave_matriz ? 'bg-blue-100 text-blue-600 border-blue-200' : 'hover:bg-slate-100 text-slate-400'}`}>
                <BarChart2 className="w-4 h-4" />
              </button>

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border shrink-0 ${depth === 0 ? 'bg-slate-800 text-white' : depth === 1 ? 'bg-slate-100 text-slate-600' : 'bg-blue-50 text-blue-600'}`}>
                {renderIcon()}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm pr-4 ${!isProduto ? 'font-black text-slate-800 tracking-tight' : 'font-semibold text-slate-600'}`}>
                   {row.nome || "INDEFINIDO"}
                 </span>
                 {depth === 0 && visaoAtiva === 'carteira' && (
                   <button 
                     onClick={(e) => { e.stopPropagation(); handleToggleLock(row.nome, rowStatus); }}
                     className={`text-[10px] font-bold mt-1 flex items-center gap-1 px-2 py-0.5 rounded border transition-colors max-w-max
                       ${isRowFechado ? 'bg-rose-50 text-rose-600 border-rose-200 hover:bg-rose-100' : 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'}`}
                   >
                      {isRowFechado ? <Lock className="w-3 h-3"/> : <Unlock className="w-3 h-3"/>}
                      {isRowFechado ? 'TRANCADA (Clique p/ Abrir)' : 'ABERTA (Clique p/ Trancar)'}
                   </button>
                 )}
              </div>
            </div>
          </td>

          {colunasData?.map((m: any, idx: number) => {
            const mBase = row.meses?.find((x: any) => x.mes_banco === m.mes_banco);
            const volBase = mBase?.vol_base || 0; // BASE AGORA É O VOL_TOPDOWN
            const recBase = mBase?.receita_base || 0;

            const isEdited = celulasEditadas[row.chave_matriz]?.[m.mes_banco] !== undefined;
            const volSimulado = getDynamicVol(row, m.mes_banco);
            const recSimulado = getDynamicRec(row, m.mes_banco);

            const anchorFat = totaisBaseAbaAtiva[m.mes_banco]?.fat || 1; 

            return (
              <React.Fragment key={idx}>
                {/* 1. SUBCOLUNA: BASE ATUAL (TOP-DOWN) */}
                <td className="p-3 border-r border-slate-200 bg-slate-50/80 align-middle">
                   <div className="flex flex-col items-end opacity-70">
                      <span className="text-xs font-bold text-slate-600">{formatVolume(volBase)} cx</span>
                      <span className="text-[10px] font-bold text-slate-500 mt-0.5">{formatMoeda(recBase)}</span>
                   </div>
                </td>

                {/* 2. SUBCOLUNA: SIMULAÇÃO */}
                <td className={`p-0 border-l border-slate-100 align-top border-r-2 border-r-slate-200 ${isRowFechado || !isTopDownFechado ? 'bg-slate-50' : isEdited ? 'bg-blue-50/40' : 'bg-white'}`}>
                  
                  {visaoAtiva === 'carteira' && depth === 0 ? (
                    <div className="flex flex-col h-full items-center justify-center p-3">
                        <PercentInput
                            pct={(recSimulado / anchorFat) * 100}
                            disabled={!isTopDownFechado || isRowFechado}
                            onChange={(newPct) => {
                                const targetFat = anchorFat * (newPct / 100);
                                const factor = targetFat / (recBase || 1);
                                const newVol = Math.round(volBase * factor);
                                handleEditCell(row.chave_matriz, m.mes_banco, newVol);
                            }}
                        />
                        <div className="flex flex-col items-center mt-2">
                           <span className="text-xs font-bold text-slate-600">{formatVolume(volSimulado)} cx</span>
                           <span className="text-[10px] font-bold text-emerald-600">{formatMoeda(recSimulado)}</span>
                        </div>
                    </div>
                  ) : (
                    <div className="flex flex-col h-full">
                      <div className="px-2 py-1 border-b border-slate-100/50 flex justify-center gap-2 items-center bg-slate-50/50">
                        <span className="text-[9px] font-bold text-slate-400 bg-slate-200/40 px-1.5 py-0.5 rounded" title="IA">IA: {formatVolume(mBase?.vol_ia || 0)}</span>
                        <span className="text-[9px] font-bold text-purple-400 bg-purple-50 px-1.5 py-0.5 rounded" title="Ciclo Anterior">Lag: {formatVolume(mBase?.vol_anterior || 0)}</span>
                      </div>
                      
                      <div className="px-4 py-2 flex flex-col items-end justify-center flex-1">
                        <div className="w-24">
                          <SmartInput 
                            value={volSimulado} 
                            disabled={!isTopDownFechado || isRowFechado} /* EDITÁVEL EM QUALQUER NÍVEL DO PORTFÓLIO */
                            onChange={(novoVol) => handleEditCell(row.chave_matriz, m.mes_banco, novoVol)} 
                          />
                        </div>
                        <span className="text-[10px] font-bold text-emerald-500 tracking-tight pr-1 mt-0.5">
                          {formatMoeda(recSimulado)}
                        </span>
                      </div>
                    </div>
                  )}
                </td>
              </React.Fragment>
            );
          })}
        </tr>
        
        {chartExpanded === row.chave_matriz && (
          <tr>
            <td colSpan={(colunasData?.length * 2 || 0) + 1} className="p-0">
               <PainelSaudabilidade rowData={row} />
            </td>
          </tr>
        )}
        
        {isExpanded && hasChildren && row.subRows.map((child: any) => renderRow(child, depth + 1))}
      </React.Fragment>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32">
      <div className="max-w-[1600px] mx-auto mb-8 flex flex-col gap-6">
        
        <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
                  <Users className="w-8 h-8 text-blue-600" /> Macrociclo <span className="text-blue-600">Comercial</span>
              </h1>
              <p className="text-slate-500 mt-1 font-medium">Gestão Bottom-Up (Top-Down Herança e RLS Regional)</p>
            </div>

            <div className="flex items-center gap-4">
              <div className="flex items-center bg-slate-200/50 p-1 rounded-xl">
                  <button onClick={() => handleToggleVisao('portfolio')} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${visaoAtiva === 'portfolio' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                      <Layers className="w-4 h-4" /> Portfólio Global (Âncora Corporativa)
                  </button>
                  <button onClick={() => handleToggleVisao('carteira')} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${visaoAtiva === 'carteira' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                      <Users className="w-4 h-4" /> Carteira Regional (Sua Alçada)
                  </button>
              </div>

              {visaoAtiva === 'carteira' && (
                  <div className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold border shadow-sm ${isAllClosed ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}>
                      {isAllClosed ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
                      {isAllClosed ? 'SUA CARTEIRA FECHADA' : 'CARTEIRA ABERTA'}
                  </div>
              )}

              <button 
                 onClick={handleSalvarRascunho} 
                 disabled={!isTopDownFechado || isAllClosed || (!allColumns100Percent && visaoAtiva === 'carteira')} 
                 className={`px-5 py-2.5 font-bold rounded-xl transition-all flex items-center gap-2 
                   ${allColumns100Percent || visaoAtiva === 'portfolio' ? 'bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100' : 'bg-slate-200 text-slate-400 cursor-not-allowed opacity-60'}`}
              >
                  <Save className="w-4 h-4" /> 
                  {!allColumns100Percent && visaoAtiva === 'carteira' ? 'Rateio ≠ 100%' : visaoAtiva === 'portfolio' ? 'Ratear Portfólio Global' : 'Salvar Rateio da Carteira'}
              </button>
            </div>
        </div>

        {!isTopDownFechado && !isLoading && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 p-5 rounded-2xl flex items-center gap-4 shadow-sm">
                <ShieldAlert className="w-8 h-8 text-amber-500" />
                <div>
                    <h3 className="font-black uppercase tracking-widest text-sm">Aguardando Diretoria (Fase 1)</h3>
                    <p className="font-medium text-sm mt-0.5">O processo Bottom-Up encontra-se bloqueado até que o nível Top-Down seja formalizado.</p>
                </div>
            </div>
        )}

      </div>

      <div className="max-w-[1600px] mx-auto bg-white rounded-2xl shadow-xl shadow-slate-200/50 border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            
            <thead>
              <tr>
                <th rowSpan={2} className="bg-slate-900 p-0 border-b border-r border-slate-800 w-[400px] align-bottom">
                  <div className="flex items-center gap-3 px-6 py-4">
                    <Search className="w-5 h-5 text-slate-400" />
                    <input type="text" placeholder="Pesquisar..." value={busca} onChange={(e) => setBusca(e.target.value)} className="bg-transparent border-none text-white focus:outline-none text-sm w-full" />
                  </div>
                </th>
                {colunasData?.map((m: any, i: number) => (
                  <th key={`head-${i}`} colSpan={2} className="bg-slate-900 px-0 py-3 border-b border-l-2 border-l-slate-800 border-slate-800 text-center">
                      <span className="text-white font-bold text-sm tracking-widest">{m.mes_str}</span>
                  </th>
                ))}
              </tr>
              <tr>
                {colunasData?.map((m: any, i: number) => (
                  <React.Fragment key={`subhead-${i}`}>
                    <th className="bg-slate-800/80 px-4 py-2 border-b border-l-2 border-l-slate-800 border-r border-slate-700/50 text-center text-[10px] text-slate-300 font-bold uppercase tracking-wider">
                      Base Atual (Âncora) 🔒
                    </th>
                    <th className="bg-slate-800 px-4 py-2 border-b border-slate-700 text-center text-[10px] text-blue-300 font-bold uppercase tracking-wider">
                      Simulação ✏️
                    </th>
                  </React.Fragment>
                ))}
              </tr>
            </thead>

            <tbody>
              {isLoading ? (
                <tr><td colSpan={10} className="p-12 text-center text-slate-400 font-medium">Mapeando Matriz Financeira...</td></tr>
              ) : dadosProcessados.length === 0 ? (
                <tr><td colSpan={10} className="p-12 text-center text-slate-400 font-medium">Nenhum dado encontrado para a sua busca.</td></tr>
              ) : (
                dadosProcessados.map(row => renderRow(row))
              )}
            </tbody>
            
            {dadosProcessados.length > 0 && (
              <tfoot className="bg-slate-900 sticky bottom-0 z-20 shadow-[0_-10px_40px_rgba(0,0,0,0.1)]">
                <tr>
                  <td className="px-6 py-5 border-r border-slate-800/50">
                    <div className="flex flex-col">
                      <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">
                        {visaoAtiva === 'portfolio' ? 'Faturamento Global Corporativo' : 'Faturamento Total da sua Carteira'}
                      </span>
                      <span className="font-bold text-sm text-white">SUMÁRIO GERENCIAL</span>
                    </div>
                  </td>
                  {colunasData?.map((m: any, i: number) => {
                     
                     const ancoraBaseFat = totaisBaseAbaAtiva[m.mes_banco]?.fat || 0;
                     const ancoraBaseVol = totaisBaseAbaAtiva[m.mes_banco]?.vol || 0;

                     const simuladoFat = totaisGeraisTelaAtual[m.mes_banco]?.fat || 0;
                     const simuladoVol = totaisGeraisTelaAtual[m.mes_banco]?.vol || 0;

                     const currentPct = ancoraBaseFat > 0 ? (simuladoFat / ancoraBaseFat) * 100 : 0;
                     const is100 = ancoraBaseFat === 0 || Math.abs(currentPct - 100) <= 0.05;

                     return (
                      <React.Fragment key={`foot-${i}`}>
                        <td className="px-4 py-4 border-l-2 border-l-slate-800 border-r border-slate-800/50 text-right bg-slate-900/50 opacity-80">
                           <div className="flex flex-col items-end">
                              <span className="text-xs font-bold text-slate-300">{formatVolume(ancoraBaseVol)} cx</span>
                              <span className="font-bold text-slate-400 text-xs mt-1">{formatMoeda(ancoraBaseFat)}</span>
                              {visaoAtiva === 'carteira' && <span className="text-[9px] font-black text-slate-500 mt-1 uppercase">Sua Âncora 100%</span>}
                           </div>
                        </td>

                        <td className="px-4 py-4 border-slate-800 text-right">
                          <div className="flex flex-col items-end">
                            <span className="font-black text-white text-sm">
                              {formatVolume(simuladoVol)} <span className="text-[10px] text-slate-400 font-medium ml-0.5">cx</span>
                            </span>
                            <span className="font-bold text-emerald-400 text-xs tracking-tight bg-emerald-400/10 px-2 py-0.5 rounded mt-1">
                              {formatMoeda(simuladoFat)}
                            </span>
                            
                            {visaoAtiva === 'carteira' && (
                              <span className={`font-black text-[10px] px-2 py-0.5 rounded mt-1.5 tracking-wider ${is100 ? 'bg-blue-500/20 text-blue-400' : 'bg-rose-500 text-white animate-bounce'}`}>
                                {is100 ? '100.00% OK' : `ALERTA: ${currentPct.toFixed(2)}%`}
                              </span>
                            )}
                          </div>
                        </td>
                      </React.Fragment>
                    );
                  })}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>
    </div>
  );
}