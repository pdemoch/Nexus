import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import axios from 'axios';
import { 
  ChevronRight, ChevronDown, Lock, Unlock, Search, X, 
  Package, Boxes, LayoutGrid, BarChart2, Activity, Shield,
  Wand2, Target, TrendingUp, TrendingDown, Users, ShieldAlert, Save, Layers,
  Sliders // <- Adicionado ícone do painel de rateio
} from 'lucide-react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
} from 'recharts';

const formatVolume = (val: number) => new Intl.NumberFormat('pt-BR').format(Math.round(val || 0));
const formatMoeda = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val || 0);

// Componente SmartInput: CORRIGIDO O BYPASS DO ZERO
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

  // =========================================================================
  // ESTADOS DO NOVO PAINEL TOP-DOWN PERCENTUAL
  // =========================================================================
  const [mesSelecionado, setMesSelecionado] = useState<string>('');
  const [showPainelPct, setShowPainelPct] = useState(false);
  const [distribuicaoPct, setDistribuicaoPct] = useState<{[key: string]: number}>({});
  const [isSalvandoPct, setIsSalvandoPct] = useState(false);

  const dadosBrutos = visaoAtiva === 'carteira' ? dadosBase.carteira : dadosBase.portfolio;
  const colunasData = dadosBrutos.length > 0 ? dadosBrutos[0].meses : [];

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento', { params: { nocache: new Date().getTime() } });
      setDadosBase({ carteira: res.data.dados.carteira || [], portfolio: res.data.dados.portfolio || [] });
      setIsTopDownFechado(res.data.is_topdown_fechado);
      setCelulasEditadas({});

      // Definir o mês alvo inicial para o rateio percentual
      if (res.data.dados.carteira?.length > 0 && res.data.dados.carteira[0].meses?.length > 0) {
        setMesSelecionado(prev => prev ? prev : res.data.dados.carteira[0].meses[0].mes_banco);
      }
    } catch (e) {
      console.error("Erro ao carregar gerenciamento:", e);
    } finally {
      setIsLoading(false);
    }
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
      setShowPainelPct(false); // Fecha painel de rateio ao trocar abas
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
        origem_ajuste: "Gerência Comercial (Rascunho)",
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
      alert("Rascunho salvo e rateado com sucesso! Ambas as visões (Carteira e Portfólio) foram sincronizadas.");
      fetchData();
    } catch (e: any) { alert("Erro ao salvar: " + (e.response?.data?.detail || e.message)); }
  };

  const handleCongelar = async () => {
    if (!confirm("Atenção Gerência: Esta ação salvará as edições, trancará as regionais visíveis na tela e passará a meta para a Fábrica (Supply). Deseja prosseguir?")) return;
    try {
      const payload = {
        origem_ajuste: "Gerência Comercial (Final)",
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

  // =========================================================================
  // MOTOR RECURSIVO: PESQUISA NAS CAMADAS (FILTRAGEM PROFUNDA)
  // =========================================================================
  const dadosProcessados = useMemo(() => {
    let processados = dadosBrutos;

    if (busca) {
        const lowerTerm = busca.toLowerCase();
        const filtrarArvore = (nodes: any[]): any[] => {
            return nodes.map(node => {
                const matchSelf = (node.nome && String(node.nome).toLowerCase().includes(lowerTerm)) ||
                                  (node.produto && String(node.produto).toLowerCase().includes(lowerTerm));
                
                let childMatches: any[] = []; 
                if (node.subRows?.length > 0) {
                    childMatches = filtrarArvore(node.subRows);
                }
                if (matchSelf || childMatches.length > 0) {
                    return { ...node, subRows: matchSelf ? node.subRows : childMatches };
                }
                return null;
            }).filter(Boolean);
        };
        processados = filtrarArvore(dadosBrutos);
    }
    return processados;
  }, [dadosBrutos, busca]);

  // =========================================================================
  // MATEMÁTICA VIVA: LEITURA DE VOLUMES E RECEITA (REFLETE EDIÇÕES E FILTROS)
  // =========================================================================
  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const edicao = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (edicao !== undefined) {
          const num = Number(edicao.novo_volume);
          return isNaN(num) ? 0 : num;
      }
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return (m?.vol_ajustado !== undefined && m?.vol_ajustado !== null) ? Number(m.vol_ajustado) : 0;
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
  // LÓGICA DO PAINEL TOP-DOWN PERCENTUAL
  // =========================================================================
  const sumarioCoordenadores = useMemo(() => {
    if (visaoAtiva !== 'carteira' || !dadosBase.carteira || !mesSelecionado) return [];
    
    let totalGlobalFat = 0;
    const resumo = dadosBase.carteira.map((coord: any) => {
      const fat = getDynamicRec(coord, mesSelecionado);
      const vol = getDynamicVol(coord, mesSelecionado);
      totalGlobalFat += fat;
      return { nome: coord.nome, faturamento: fat, volume: vol };
    });

    return resumo.map((c: any) => {
      const pctAtual = totalGlobalFat > 0 ? (c.faturamento / totalGlobalFat) * 100 : 0;
      return { ...c, pctOriginal: Number(pctAtual.toFixed(2)) };
    });
  }, [dadosBase.carteira, mesSelecionado, getDynamicRec, getDynamicVol, visaoAtiva]);

  useEffect(() => {
    if (showPainelPct && sumarioCoordenadores.length > 0) {
      const estadoInicial: {[key: string]: number} = {};
      sumarioCoordenadores.forEach((c: any) => {
        estadoInicial[c.nome] = distribuicaoPct[c.nome] ?? c.pctOriginal;
      });
      setDistribuicaoPct(estadoInicial);
    }
  }, [showPainelPct, sumarioCoordenadores]);

  const somaPercentuaisDigitados = useMemo(() => {
    return Object.values(distribuicaoPct).reduce((acc, curr) => acc + (curr || 0), 0);
  }, [distribuicaoPct]);

  const handleSalvarPercentuais = async () => {
    if (Math.abs(somaPercentuaisDigitados - 100) > 0.01) return;
    setIsSalvandoPct(true);
    try {
      const payload = {
        mes_projetado: mesSelecionado,
        distribuicao: Object.keys(distribuicaoPct).map(key => ({
          coordenador_nome: key,
          percentual: distribuicaoPct[key]
        }))
      };
      await axios.post('/api/v1/consensus/gerenciamento/ajustar-percentual', payload);
      setShowPainelPct(false);
      setDistribuicaoPct({});
      await fetchData(); // Recarrega do backend já rateado anti-dízima
    } catch (err: any) {
      alert("Erro ao rebalancear metas: " + (err.response?.data?.detail || err.message));
    } finally {
      setIsSalvandoPct(false);
    }
  };


  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    if (!isTopDownFechado) return;
    
    // BLINDAGEM DE EDIÇÃO (Seja em Carteira ou em Portfólio)
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
        const res = await axios.get('/api/v1/consensus/gerenciamento/grafico', { 
            params: { chave_matriz: chave, visao: visaoAtiva } 
        });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

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


  const totaisGerais = useMemo(() => {
    const totais: Record<string, { vol: number, fat: number }> = {};
    colunasData?.forEach((m: any) => {
      let vol = 0; let fat = 0;
      // Usando os dadosProcessados para refletir a filtragem exata no rodapé!
      dadosProcessados.forEach((rootNode: any) => {
        vol += getDynamicVol(rootNode, m.mes_banco);
        fat += getDynamicRec(rootNode, m.mes_banco);
      });
      totais[m.mes_banco] = { vol, fat };
    });
    return totais;
  }, [dadosProcessados, getDynamicVol, getDynamicRec, colunasData]);

  const renderRow = (row: any, depth = 0) => {
    const isExpanded = expanded[row.chave_matriz];
    const hasChildren = row.subRows && row.subRows.length > 0;
    const isProduto = row.tipo === 'produto';
    
    // BLINDAGEM VISUAL DE TRANCA
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
        if (visaoAtiva === 'carteira') {
            return depth === 0 ? <Users className="w-4 h-4" /> : depth === 1 ? <LayoutGrid className="w-4 h-4" /> : depth === 2 ? <Boxes className="w-4 h-4" /> : <Package className="w-4 h-4" />;
        } else {
            return depth === 0 ? <LayoutGrid className="w-4 h-4" /> : depth === 1 ? <Boxes className="w-4 h-4" /> : <Package className="w-4 h-4" />;
        }
    };

    return (
      <React.Fragment key={row.chave_matriz}>
        <tr className={`border-b transition-colors hover:bg-slate-50 ${depth === 0 ? 'bg-white' : depth === 1 ? 'bg-slate-50/50' : 'bg-white'}`}>
          <td className="p-0 align-middle border-r border-slate-100">
            <div style={{ paddingLeft: `${depth * 2 + 1}rem` }} className="flex items-center gap-3 py-3 min-w-[320px] h-full">
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

          {row.meses?.map((m: any, idx: number) => {
            const edicao = celulasEditadas[row.chave_matriz]?.[m.mes_banco];
            const isEdited = edicao !== undefined;
            const valorExibicao = getDynamicVol(row, m.mes_banco);
            const receitaExibicao = getDynamicRec(row, m.mes_banco);

            return (
              <td key={idx} className="p-0 border-l border-slate-100 align-top">
                <div className={`flex flex-col h-full min-h-[76px] ${isRowFechado || !isTopDownFechado ? 'bg-slate-50' : isEdited ? 'bg-blue-50/40' : 'hover:bg-slate-50'}`}>
                  
                  <div className="px-2 py-1.5 border-b border-slate-100/50 flex justify-center gap-3 items-center bg-slate-50/80">
                    <span className="text-[10px] font-bold text-slate-500 bg-slate-200/50 px-2 py-0.5 rounded whitespace-nowrap" title="Projeção IA Original">
                      IA: {formatVolume(m.vol_ia)}
                    </span>
                    <span className="text-[10px] font-bold text-purple-600 bg-purple-100/50 border border-purple-200/50 px-2 py-0.5 rounded whitespace-nowrap" title="Aprovado no Ciclo Anterior">
                      Lag 1: {formatVolume(m.vol_anterior)}
                    </span>
                  </div>
                  
                  <div className="px-4 py-2 flex flex-col items-end justify-center flex-1">
                    <div className="w-24">
                      {/* BLINDAGEM DE INPUT */}
                      <SmartInput 
                         value={valorExibicao} 
                         disabled={!isTopDownFechado || isRowFechado || (visaoAtiva === 'carteira' && row.tipo === "coordenador")} 
                         onChange={(novoVol) => handleEditCell(row.chave_matriz, m.mes_banco, novoVol)} 
                      />
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
        
        {chartExpanded === row.chave_matriz && (
          <tr>
            <td colSpan={(row.meses?.length || 0) + 1} className="p-0">
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
            <p className="text-slate-500 mt-1 font-medium">Gestão Tática (Fase 2) - Distribuição e Rateio</p>
            </div>

            <div className="flex items-center gap-4">
            
            <div className="flex items-center bg-slate-200/50 p-1 rounded-xl">
                <button onClick={() => handleToggleVisao('carteira')} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${visaoAtiva === 'carteira' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                    <Users className="w-4 h-4" /> Carteira de Clientes
                </button>
                <button onClick={() => handleToggleVisao('portfolio')} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${visaoAtiva === 'portfolio' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                    <Layers className="w-4 h-4" /> Hierarquia Portfólio
                </button>
            </div>

            {/* NOVOS CONTROLES DO PAINEL TOP-DOWN */}
            {visaoAtiva === 'carteira' && (
                <div className="flex items-center gap-2 bg-slate-200/50 p-1 rounded-xl">
                    <select
                        value={mesSelecionado}
                        onChange={(e) => setMesSelecionado(e.target.value)}
                        className="bg-white border-none text-slate-700 text-sm font-bold rounded-lg px-3 py-2 cursor-pointer focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        {colunasData?.map((c: any) => (
                            <option key={c.mes_banco} value={c.mes_banco}>{c.mes_str}</option>
                        ))}
                    </select>

                    <button
                        onClick={() => setShowPainelPct(!showPainelPct)}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${showPainelPct ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/20' : 'bg-white text-slate-700 hover:bg-slate-50'}`}
                    >
                        <Sliders className="w-4 h-4" /> Rateio (Top-Down)
                    </button>
                </div>
            )}

            {visaoAtiva === 'carteira' && (
                <div className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold border shadow-sm ${isAllClosed ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}>
                    {isAllClosed ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
                    {isAllClosed ? 'REGIONAIS FECHADAS' : 'REGIONAIS ABERTAS'}
                </div>
            )}

            {/* BOTÕES DESLIGAM SE TUDO ESTIVER TRANCADO */}
            <button onClick={handleSalvarRascunho} disabled={!isTopDownFechado || isAllClosed} className="px-4 py-2.5 bg-blue-50 hover:bg-blue-100 text-blue-700 font-bold border border-blue-200 rounded-xl transition-all disabled:opacity-50 flex items-center gap-2">
                <Save className="w-4 h-4" /> Salvar Rascunho
            </button>

            <button onClick={handleCongelar} disabled={!isTopDownFechado || isAllClosed} className="px-6 py-2.5 bg-slate-900 hover:bg-blue-600 text-white font-bold rounded-xl shadow-lg shadow-slate-900/20 transition-all disabled:opacity-50 flex items-center gap-2">
                <Shield className="w-4 h-4" /> Aprovar Carteira Comercial
            </button>
            </div>
        </div>

        {!isTopDownFechado && !isLoading && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 p-5 rounded-2xl flex items-center gap-4 shadow-sm animate-in fade-in slide-in-from-top-4">
                <ShieldAlert className="w-8 h-8 text-amber-500" />
                <div>
                    <h3 className="font-black uppercase tracking-widest text-sm">Aguardando Diretoria (Fase 1)</h3>
                    <p className="font-medium text-sm mt-0.5">O processo Top-Down ainda não foi ratificado no ciclo atual. A edição comercial está temporariamente bloqueada para evitar desalinhamento da meta.</p>
                </div>
            </div>
        )}

        {/* ========================================================= */}
        {/* NOVO PAINEL EXPANDÍVEL: AJUSTE DE METAS TOP-DOWN          */}
        {/* ========================================================= */}
        {visaoAtiva === 'carteira' && showPainelPct && (
          <div className="bg-white border border-indigo-200 rounded-2xl p-6 shadow-xl shadow-indigo-100/50 animate-in fade-in slide-in-from-top-4 mt-2">
            <div className="flex justify-between items-center border-b border-slate-100 pb-4 mb-5">
              <div className="flex items-center gap-2">
                <Target className="h-5 w-5 text-indigo-600" />
                <h2 className="font-bold text-lg text-slate-800 tracking-tight">Redistribuição Percentual de Metas (Top-Down)</h2>
              </div>
              <span className="text-xs font-bold text-slate-500 bg-slate-100 px-3 py-1.5 rounded-lg">
                Mês Base 100%: <span className="text-indigo-600">{colunasData.find((c: any) => c.mes_banco === mesSelecionado)?.mes_str || mesSelecionado}</span>
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 mb-6">
              {sumarioCoordenadores.map((coord: any) => (
                <div key={coord.nome} className="bg-slate-50 border border-slate-200 p-4 rounded-xl flex flex-col justify-between hover:border-indigo-300 transition-colors">
                  <div>
                    <div className="text-sm font-black text-slate-700 uppercase tracking-wide">{coord.nome}</div>
                    <div className="flex justify-between items-center mt-2">
                      <span className="text-xs font-bold text-slate-500">Share Atual: {coord.pctOriginal}%</span>
                      <span className="text-xs font-bold text-slate-500">Vol: {formatVolume(coord.volume)} CX</span>
                    </div>
                    <div className="text-lg font-black text-emerald-600 mt-1">{formatMoeda(coord.faturamento)}</div>
                  </div>
                  <div className="mt-4 flex items-center gap-3 border-t border-slate-200 pt-4">
                    <span className="text-sm font-bold text-slate-600 whitespace-nowrap">Novo Alvo (%):</span>
                    <input
                      type="number" step="0.01" min="0" max="100"
                      value={distribuicaoPct[coord.nome] ?? ''}
                      onChange={(e) => setDistribuicaoPct({ ...distribuicaoPct, [coord.nome]: parseFloat(e.target.value) || 0 })}
                      className="w-full bg-white border border-slate-300 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 rounded-lg px-3 py-2 text-right font-bold text-slate-800 transition-all"
                    />
                  </div>
                </div>
              ))}
            </div>

            <div className="flex flex-col sm:flex-row justify-between items-center bg-slate-100 p-4 rounded-xl gap-4 border border-slate-200">
              <div className="flex items-center gap-3">
                <div className={`h-4 w-4 rounded-full shadow-inner ${Math.abs(somaPercentuaisDigitados - 100) < 0.01 ? 'bg-emerald-500 shadow-emerald-500/50 animate-pulse' : 'bg-rose-500 shadow-rose-500/50'}`} />
                <div className="flex flex-col">
                  <span className="text-[10px] font-black text-slate-500 uppercase tracking-wider">Soma Total de Rateio Regional</span>
                  <span className={`text-lg font-black tracking-tight ${Math.abs(somaPercentuaisDigitados - 100) < 0.01 ? 'text-emerald-600' : 'text-rose-600'}`}>
                    {somaPercentuaisDigitados.toFixed(2)}% <span className="text-slate-400 text-sm font-bold">/ 100.00%</span>
                  </span>
                </div>
              </div>

              <button
                onClick={handleSalvarPercentuais}
                disabled={Math.abs(somaPercentuaisDigitados - 100) > 0.01 || isSalvandoPct}
                className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 text-white font-bold px-6 py-3 rounded-xl flex items-center justify-center gap-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-md"
              >
                <Save className="w-5 h-5" /> 
                {isSalvandoPct ? "Recalculando e Evitando Dízimas..." : "Subscrever Matriz Granular"}
              </button>
            </div>
          </div>
        )}

      </div>

      <div className="max-w-[1600px] mx-auto bg-white rounded-2xl shadow-xl shadow-slate-200/50 border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr>
                <th className="bg-slate-900 p-0 border-b border-slate-800 w-[400px]">
                  <div className="flex items-center gap-3 px-6 py-5">
                    <Search className="w-5 h-5 text-slate-400" />
                    <input type="text" placeholder={visaoAtiva === 'carteira' ? "Procurar carteira, cliente ou produto..." : "Procurar categoria ou produto..."} value={busca} onChange={(e) => setBusca(e.target.value)} className="bg-transparent border-none text-white focus:outline-none placeholder-slate-500 text-sm font-medium w-full" />
                    {busca && <button onClick={() => setBusca("")}><X className="w-4 h-4 text-slate-400 hover:text-white" /></button>}
                  </div>
                </th>
                {colunasData?.map((m: any, i: number) => (
                  <th key={i} className="bg-slate-900 p-0 border-b border-slate-800 border-l border-slate-800/50 min-w-[180px]">
                    <div className="px-6 py-5 flex flex-col items-center justify-center">
                      <span className="text-white font-bold text-sm tracking-widest">{m.mes_str}</span>
                      <span className="text-blue-400 text-[10px] font-black tracking-widest uppercase mt-0.5">S&OP Comercial</span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr><td colSpan={10} className="p-12 text-center text-slate-400 font-medium">Mapeando Árvore Comercial e Portfólio...</td></tr>
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
                      <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total Filtrado</span>
                      <span className="font-bold text-sm text-white">SUMÁRIO GERENCIAL</span>
                    </div>
                  </td>
                  {colunasData?.map((m: any, i: number) => (
                    <td key={i} className="px-6 py-5 border-l border-slate-800/50 text-right">
                      <div className="flex flex-col items-center justify-center">
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
        </div>
      </div>
    </div>
  );
}