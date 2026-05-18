import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import axios from 'axios';
import { 
  ChevronRight, ChevronDown, Lock, Unlock, Search, X, 
  Package, Boxes, LayoutGrid, Download, BarChart2, Activity, Shield,
  Wand2, Target, TrendingUp, TrendingDown, Users
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
      const prompt = `Gere uma análise executiva de S&OP (máx 3 parágrafos) para a carteira comercial de ${alvo} (Nível: ${tipo}). O volume proposto pelo time é de ${volume} CX, com PMV médio de R$ ${pmv.toFixed(2)} e Receita Projetada de R$ ${receita.toFixed(2)}. Foque em rentabilidade e tendências comerciais.`;
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

export default function GerenciamentoArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [busca, setBusca] = useState("");
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);
  
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/gerenciamento', { params: { nocache: new Date().getTime() } });
      setDadosBrutos(res.data.dados || []);
      setCelulasEditadas({});
    } catch (e) {
      console.error("Erro ao carregar gerenciamento:", e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Checa se as bases da tela estão trancadas
  const isAllClosed = dadosBrutos.length > 0 && dadosBrutos.every(coord => coord.status === 'Fechado');

  const handleCongelar = async () => {
    if (!confirm("Atenção Gerência: Isso rateará o volume pelos clientes baseado no histórico real de pedidos e trancará as regionais editadas. Deseja prosseguir?")) return;
    try {
      const payload = {
        origem_ajuste: "Gerência Comercial",
        ajustes: Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
          Object.entries(meses).map(([mes_projetado, val]: any) => ({
            nivel: chave.split('|').length === 4 ? 'produto' : 'agrupamento',
            chave,
            mes_projetado,
            novo_volume: parseInt(val.novo_volume, 10)
          }))
        )
      };

      await axios.post(`/api/v1/consensus/gerenciamento/congelar`, payload);
      alert("Gestão Comercial Congelada com Sucesso!");
      fetchData();
    } catch (e: any) {
      alert("Erro ao congelar: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    // Busca o status do coordenador dono desta célula
    const coordRoot = chaveStr.split('|')[0];
    const nodeCoord = dadosBrutos.find(c => c.nome === coordRoot);
    if (nodeCoord && nodeCoord.status === 'Fechado') return; // Bloqueia edição se regional fechada

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
        const res = await axios.get('/api/v1/consensus/gerenciamento/grafico', { params: { chave_matriz: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); }
      finally { setLoadingGrafico(null); }
    }
  };

  // =====================================================================
  // PAINEL DE SAUDABILIDADE
  // =====================================================================
  const PainelSaudabilidade = ({ rowData }: { rowData: any }) => {
    const chave = rowData.chave_matriz;
    const chartData = dadosGraficoCache[chave];

    const kpis = useMemo(() => {
      let volAtual = 0; let volIA = 0; let rec = 0; let pmvAcc = 0; let count = 0;
      (rowData?.meses || []).forEach((m: any) => {
          const edicao = celulasEditadas[rowData.chave_matriz]?.[m.mes_banco];
          const vFinal = edicao !== undefined ? parseInt(edicao.novo_volume) : (m.vol_ajustado || 0);
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
          <h3 className="font-bold text-lg text-white">
            Dossiê Tático Executivo <span className="text-slate-500 font-normal">| {rowData.nome}</span>
          </h3>
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
    dadosBrutos.forEach(coord => {
      coord.meses?.forEach((m: any) => {
        if (!totais[m.mes_banco]) totais[m.mes_banco] = { vol: 0, fat: 0 };
        const edicao = celulasEditadas[coord.chave_matriz]?.[m.mes_banco];
        const vAtual = edicao !== undefined ? parseInt(edicao.novo_volume) : (m.vol_ajustado || 0);
        totais[m.mes_banco].vol += vAtual;
        totais[m.mes_banco].fat += (vAtual * (m.pmv || 0));
      });
    });
    return totais;
  }, [dadosBrutos, celulasEditadas]);

  const renderRow = (row: any, depth = 0) => {
    const isExpanded = expanded[row.chave_matriz];
    const hasChildren = row.subRows && row.subRows.length > 0;
    const isProduto = row.tipo === 'produto';
    
    // Herdando o status da Regional (Coordenador)
    const coordRoot = row.chave_matriz.split('|')[0];
    const nodeCoord = dadosBrutos.find(c => c.nome === coordRoot);
    const rowStatus = nodeCoord ? nodeCoord.status : 'Aberto';
    const isRowFechado = rowStatus === 'Fechado';

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
                {depth === 0 ? <Users className="w-4 h-4" /> : depth === 1 ? <LayoutGrid className="w-4 h-4" /> : depth === 2 ? <Boxes className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm pr-4 ${!isProduto ? 'font-black text-slate-800 tracking-tight' : 'font-semibold text-slate-600'}`}>
                   {row.nome || "INDEFINIDO"}
                 </span>
                 {depth === 0 && (
                   <span className={`text-[10px] font-bold mt-0.5 flex items-center gap-1 ${isRowFechado ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {isRowFechado ? <Lock className="w-3 h-3"/> : <Unlock className="w-3 h-3"/>}
                      {isRowFechado ? 'BASE TRANCADA' : 'LIVRE PARA EDIÇÃO'}
                   </span>
                 )}
              </div>
            </div>
          </td>

          {row.meses?.map((m: any, idx: number) => {
            const edicao = celulasEditadas[row.chave_matriz]?.[m.mes_banco];
            const isEdited = edicao !== undefined;
            const valorExibicao = isEdited ? edicao.novo_volume : (m.vol_ajustado || 0);

            return (
              <td key={idx} className="p-0 border-l border-slate-100 align-top">
                <div className={`flex flex-col h-full min-h-[76px] ${isRowFechado ? 'bg-slate-50' : isEdited ? 'bg-blue-50/40' : 'hover:bg-slate-50'}`}>
                  
                  {/* CABEÇALHO DA CÉLULA: IA vs LAG 1 CENTRALIZADOS */}
                  <div className="px-2 py-1.5 border-b border-slate-100/50 flex justify-center gap-3 items-center bg-slate-50/80">
                    <span className="text-[10px] font-bold text-slate-500 bg-slate-200/50 px-2 py-0.5 rounded whitespace-nowrap" title="Projeção IA Original">
                      IA: {formatVolume(m.vol_ia)}
                    </span>
                    <span className="text-[10px] font-bold text-purple-600 bg-purple-100/50 border border-purple-200/50 px-2 py-0.5 rounded whitespace-nowrap" title="Aprovado no Ciclo Anterior">
                      Lag 1: {formatVolume(m.vol_anterior)}
                    </span>
                  </div>
                  
                  {/* CORPO DA CÉLULA: VOLUME + RECEITA DINÂMICA */}
                  <div className="px-4 py-2 flex flex-col items-end justify-center flex-1">
                    <div className="w-24">
                      {/* Permite editar em TODOS os níveis se estiver aberto */}
                      <SmartInput 
                         value={valorExibicao} 
                         disabled={isRowFechado} 
                         onChange={(novoVol) => handleEditCell(row.chave_matriz, m.mes_banco, novoVol)} 
                      />
                    </div>
                    {/* Faturamento Previsto Dinâmico */}
                    <span className="text-[10px] font-bold text-emerald-500 tracking-tight pr-1 mt-0.5" title="Receita (R$) Prevista">
                      {formatMoeda(valorExibicao * (m.pmv || 0))}
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

  const colunasData = dadosBrutos.length > 0 ? dadosBrutos[0].meses : [];

  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32">
      <div className="max-w-[1600px] mx-auto mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
            <Users className="w-8 h-8 text-blue-600" /> Macrociclo <span className="text-blue-600">Comercial</span>
          </h1>
          <p className="text-slate-500 mt-1 font-medium">Gestão de Carteira e Planejamento Regional (Gerência)</p>
        </div>

        <div className="flex items-center gap-4">
          <div className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold border shadow-sm ${isAllClosed ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}>
            {isAllClosed ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
            {isAllClosed ? 'REGIONAIS FECHADAS' : 'REGIONAIS ABERTAS'}
          </div>

          <button onClick={handleCongelar} disabled={isAllClosed && Object.keys(celulasEditadas).length === 0} className="px-6 py-2.5 bg-slate-900 hover:bg-blue-600 text-white font-bold rounded-xl shadow-lg shadow-slate-900/20 transition-all disabled:opacity-50 flex items-center gap-2">
            <Shield className="w-4 h-4" /> Aprovar Carteira Comercial
          </button>
        </div>
      </div>

      <div className="max-w-[1600px] mx-auto bg-white rounded-2xl shadow-xl shadow-slate-200/50 border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr>
                <th className="bg-slate-900 p-0 border-b border-slate-800 w-[400px]">
                  <div className="flex items-center gap-3 px-6 py-5">
                    <Search className="w-5 h-5 text-slate-400" />
                    <input type="text" placeholder="Procurar carteira, cliente ou produto..." value={busca} onChange={(e) => setBusca(e.target.value)} className="bg-transparent border-none text-white focus:outline-none placeholder-slate-500 text-sm font-medium w-full" />
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
                <tr><td colSpan={10} className="p-12 text-center text-slate-400 font-medium">Mapeando Árvore Comercial...</td></tr>
              ) : dadosBrutos.length === 0 ? (
                <tr><td colSpan={10} className="p-12 text-center text-slate-400 font-medium">Nenhuma carteira comercial encontrada para gestão.</td></tr>
              ) : (
                dadosBrutos.filter(d => d.nome?.toLowerCase().includes(busca.toLowerCase()) || busca === "").map(row => renderRow(row))
              )}
            </tbody>
            
            {dadosBrutos.length > 0 && (
              <tfoot className="bg-slate-900 sticky bottom-0 z-20 shadow-[0_-10px_40px_rgba(0,0,0,0.1)]">
                <tr>
                  <td className="px-6 py-5 border-r border-slate-800/50">
                    <div className="flex flex-col">
                      <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total Consolidação</span>
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