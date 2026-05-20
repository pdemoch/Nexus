import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import axios from 'axios';
import { 
  ChevronRight, ChevronDown, Lock, Unlock, Search, X, 
  Package, Boxes, LayoutGrid, Download, BarChart2, Activity, Shield,
  Wand2, Factory, Target, TrendingUp, TrendingDown
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

  const handleFocus = () => { setIsFocused(true); setLocalVal(localVal.replace(/\./g, '')); };
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
      type="text" value={localVal} disabled={disabled} onFocus={handleFocus} onBlur={handleBlur} onKeyDown={handleKeyDown} onChange={(e) => setLocalVal(e.target.value)}
      className={`w-full bg-transparent border-none text-right focus:outline-none focus:ring-1 focus:ring-blue-500 rounded px-1
        ${disabled ? 'text-slate-400 font-medium' : 'text-blue-700 font-bold bg-blue-50/50'}`}
    />
  );
};

// =====================================================================
// COMPONENTE: CAIXA DE INSIGHT IA
// =====================================================================
const AiInsightBox = ({ alvo, pmv, volume, demandaComercial }: { alvo: string, pmv: number, volume: number, demandaComercial: number }) => {
  const [insight, setInsight] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchInsight = async () => {
    setLoading(true);
    try {
      const prompt = `Gere uma análise executiva de Supply Chain (máx 3 parágrafos) para a categoria/produto ${alvo}. Demanda do Comercial: ${demandaComercial} CX. Capacidade aprovada (Supply): ${volume} CX. PMV: R$ ${pmv.toFixed(2)}. Foque em restrições de fábrica, ruptura de estoque e impacto no Fair-Share.`;
      const res = await axios.post('/api/v1/ai-sql/perguntar', { pergunta: prompt });
      setInsight(res.data.resposta);
    } catch (e) { setInsight("Erro ao comunicar com a IA Nexus. Tente novamente."); } finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800 rounded-xl p-5 border border-slate-700 flex flex-col h-full shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-slate-200 flex items-center gap-2"><Wand2 className="w-4 h-4 text-blue-400" /> Nexus AI Insight 360°</h4>
        <button onClick={fetchInsight} disabled={loading} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors">{loading ? "Processando..." : "Gerar Diagnóstico"}</button>
      </div>
      <div className="flex-1 text-sm text-slate-300 leading-relaxed overflow-y-auto pr-2">
        {loading ? (
          <div className="animate-pulse flex flex-col gap-2"><div className="h-2 bg-slate-700 rounded w-full"></div><div className="h-2 bg-slate-700 rounded w-5/6"></div></div>
        ) : insight ? <div className="whitespace-pre-wrap">{insight}</div> : (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 opacity-50"><Shield className="w-12 h-12 mb-2" /><span>Nenhuma análise gerada.</span></div>
        )}
      </div>
    </div>
  );
};

export default function SupplyReviewArena() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [busca, setBusca] = useState("");
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [isLoading, setIsLoading] = useState(true);
  
  // STATUS E TRAVAS (O RADAR)
  const [isComercialFechado, setIsComercialFechado] = useState(false);
  const [isSupplyFechado, setIsSupplyFechado] = useState(false);
  
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const fetchStatusAndData = useCallback(async () => {
    setIsLoading(true);
    try {
      const resStatus = await axios.get('/api/v1/consensus/supply/status');
      const comercialLiberado = resStatus.data.comercial_fechado;
      setIsComercialFechado(comercialLiberado);
      setIsSupplyFechado(resStatus.data.fechado);

      // Só carrega a árvore se o Gerenciamento Comercial estiver totalmente fechado
      if (comercialLiberado) {
        const resLista = await axios.get('/api/v1/consensus/supply', { params: { nocache: new Date().getTime() } });
        setDadosBrutos(resLista.data.dados || []);
        // Expande categorias por padrão
        const cats: any = {};
        (resLista.data.dados || []).forEach((c: any) => { cats[c.id] = true; });
        setExpanded(cats);
      }
      setCelulasEditadas({});
    } catch (e) { console.error("Erro ao carregar supply:", e); setDadosBrutos([]); } finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchStatusAndData(); }, [fetchStatusAndData]);

  // =====================================================================
  // MOTOR DE CASCATA EM TEMPO REAL (PRODUTO -> CATEGORIA) E JUSTIFICATIVA
  // =====================================================================
  const handleEditCell = (chaveStr: string, mesBanco: string, novoValor: number) => {
    if (isSupplyFechado) return;

    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };

      const getVolActual = (chave: string, originalVol: number) => {
        if (nextEdits[chave]?.[mesBanco] !== undefined) return nextEdits[chave][mesBanco].novo_volume;
        return originalVol;
      };

      // Grava no Produto Folha, mantendo a justificativa se já houver
      if (!nextEdits[chaveStr]) nextEdits[chaveStr] = {};
      const prevJustificativa = nextEdits[chaveStr][mesBanco]?.justificativa || "";
      nextEdits[chaveStr][mesBanco] = { novo_volume: novoValor, justificativa: prevJustificativa };

      // Rollup para a Categoria
      dadosBrutos.forEach(cat => {
        const hasChildEdited = cat.subRows?.some((p: any) => p.id === chaveStr);
        if (hasChildEdited) {
          const newCatTotal = cat.subRows.reduce((acc: number, prod: any) => {
            const childMes = prod.meses.find((m: any) => m.mes_banco === mesBanco);
            return acc + getVolActual(prod.id, childMes ? childMes.vol_supply : 0);
          }, 0);
          if (!nextEdits[cat.id]) nextEdits[cat.id] = {};
          nextEdits[cat.id][mesBanco] = { novo_volume: newCatTotal };
        }
      });

      return nextEdits;
    });
  };

  const handleEditJustificativa = (chaveStr: string, mesBanco: string, novaJustificativa: string) => {
    if (isSupplyFechado) return;

    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };
      if (!nextEdits[chaveStr]) nextEdits[chaveStr] = {};
      
      let currentVol = nextEdits[chaveStr][mesBanco]?.novo_volume;
      if (currentVol === undefined) {
         let origVol = 0;
         dadosBrutos.forEach(cat => {
            const prod = cat.subRows?.find((p: any) => p.id === chaveStr);
            if (prod) {
               const m = prod.meses.find((x: any) => x.mes_banco === mesBanco);
               if (m) origVol = m.vol_supply || 0;
            }
         });
         currentVol = origVol;
      }

      nextEdits[chaveStr][mesBanco] = { novo_volume: currentVol, justificativa: novaJustificativa };
      return nextEdits;
    });
  };

  const handleCongelar = async () => {
    if (!confirm("Atenção Supply: O sistema aplicará um rateio Fair-Share nos clientes do comercial baseado nas restrições de fábrica aplicadas aqui. Deseja prosseguir?")) return;
    try {
      const payload = {
        origem_ajuste: "Supply Review",
        ajustes: Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
          Object.entries(meses)
            // Filtra para enviar apenas SKUs (produtos não têm | no ID nesta tela)
            .filter(() => !dadosBrutos.some(c => c.id === chave))
            .map(([mes_projetado, val]: any) => ({
              produto: chave, 
              mes_projetado, 
              novo_volume: parseInt(val.novo_volume, 10), 
              justificativa: val.justificativa || "Ajuste de Capacidade Fabril" // Grava a justificativa atômica
            }))
        )
      };
      await axios.post(`/api/v1/consensus/supply/congelar`, payload);
      alert("Supply Review Aprovado e Rateio Fair-Share Concluído!");
      fetchStatusAndData();
    } catch (e: any) { alert("Erro ao salvar: " + (e.response?.data?.detail || e.message)); }
  };

  const handleDestrancar = async () => {
    if (!confirm("Reabrir Fase de Supply?")) return;
    try { await axios.post(`/api/v1/consensus/supply/destrancar`); fetchStatusAndData(); } catch (e) { alert("Erro ao destrancar."); }
  };

  const handleExportCSV = () => {
    let csv = "CATEGORIA,SKU,DESCRICAO,MES,DEMANDA COMERCIAL (CX),CAPACIDADE SUPPLY (CX),GAP\n";
    dadosBrutos.forEach(cat => {
      cat.subRows?.forEach((prod: any) => {
        prod.meses.forEach((m: any) => {
          const edicao = celulasEditadas[prod.id]?.[m.mes_banco];
          const vSup = edicao !== undefined ? edicao.novo_volume : m.vol_supply;
          csv += `"${cat.nome}","${prod.id}","${prod.nome}","${m.mes_str}",${m.vol_comercial},${vSup},${vSup - m.vol_comercial}\n`;
        });
      });
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.setAttribute("download", "Supply_Review_Export.csv");
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
  };

  const toggleChart = async (node: any) => {
    const chave = node.id;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    if (node.tipo !== 'produto') { alert("Gráfico disponível apenas no nível de Produto (SKU)."); return; }
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/consensus/supply/grafico', { params: { produto_id: chave } });
        setDadosGraficoCache((prev: any) => ({ ...prev, [chave]: res.data.dados }));
      } catch (e) { console.error(e); } finally { setLoadingGrafico(null); }
    }
  };

  const PainelSaudabilidade = ({ rowData }: { rowData: any }) => {
    const chave = rowData.id; 
    const chartData = dadosGraficoCache[chave];

    // =====================================================================
    // O SEGREDO DO GRÁFICO DINÂMICO (REATIVIDADE EM TEMPO REAL)
    // =====================================================================
    const chartDataDinamico = useMemo(() => {
      if (!chartData) return [];
      return chartData.map((d: any) => {
        const mesBanco = `${d.name}-01`;
        const edicao = celulasEditadas[rowData.id]?.[mesBanco];
        
        let dynamicSupply = d.Supply;
        if (edicao !== undefined) {
          dynamicSupply = parseInt(edicao.novo_volume);
        }

        const mesRow = (rowData?.meses || []).find((m: any) => m.mes_banco === mesBanco);
        let iaVal = d.IA;
        // Puxa a IA do renderizador da tela caso o gráfico venha com ponto cego do backend
        if (mesRow && mesRow.vol_ia !== undefined && mesRow.vol_ia > 0) {
           iaVal = mesRow.vol_ia;
        }

        return {
          ...d,
          Supply: dynamicSupply,
          IA: iaVal
        };
      });
    }, [chartData, celulasEditadas, rowData]);

    const kpis = useMemo(() => {
      let vComercial = 0; let vSupply = 0; let pmvAcc = 0; let count = 0;
      (rowData?.meses || []).forEach((m: any) => {
          const edicao = celulasEditadas[rowData.id]?.[m.mes_banco];
          const vFinal = edicao !== undefined ? parseInt(edicao.novo_volume) : (m.vol_supply || 0);
          vComercial += (m.vol_comercial || 0); vSupply += vFinal;
          if(m.pmv) { pmvAcc += m.pmv; count++; }
      });
      const gap = vSupply - vComercial;
      return { vComercial, vSupply, gap, pmvMedio: count > 0 ? (pmvAcc / count) : 0 };
    }, [rowData, celulasEditadas]);

    return (
      <div className="w-full bg-slate-900 shadow-inner px-8 py-8 border-y border-slate-800">
        <div className="flex items-center gap-2 mb-6">
          <Factory className="w-5 h-5 text-blue-400" />
          <h3 className="font-bold text-lg text-white">Dossiê de Restrição Fabril <span className="text-slate-500 font-normal">| {rowData.nome}</span></h3>
        </div>
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="text-slate-400 mb-2"><span className="text-xs font-bold uppercase tracking-wider">Demanda Comercial</span></div>
            <div className="text-2xl font-black text-white">{formatVolume(kpis.vComercial)} <span className="text-sm font-normal text-slate-500">CX</span></div>
          </div>
          <div className="bg-slate-800 border border-slate-700 p-4 rounded-xl">
            <div className="text-slate-400 mb-2"><span className="text-xs font-bold uppercase tracking-wider">Capacidade Supply</span></div>
            <div className="text-2xl font-black text-white">{formatVolume(kpis.vSupply)} <span className="text-sm font-normal text-slate-500">CX</span></div>
          </div>
          <div className={`border p-4 rounded-xl ${kpis.gap < 0 ? 'bg-rose-900/20 border-rose-500/30' : 'bg-emerald-900/20 border-emerald-500/30'}`}>
            <div className="text-slate-400 mb-2"><span className="text-xs font-bold uppercase tracking-wider">Ruptura (GAP)</span></div>
            <div className={`text-2xl font-black ${kpis.gap < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>{kpis.gap > 0 ? '+' : ''}{formatVolume(kpis.gap)} <span className="text-sm font-normal opacity-70">CX</span></div>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-slate-800 border border-slate-700 p-4 rounded-xl h-[340px]">
            {loadingGrafico === chave ? <div className="h-full flex items-center justify-center text-slate-500">Mapeando série histórica...</div> : chartDataDinamico && (
              <ResponsiveContainer width="100%" height="100%">
                {/* Agora o LineChart está plugado no chartDataDinamico, reagindo ao teclado */}
                <LineChart data={chartDataDinamico} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" />
                  <XAxis dataKey="name" tick={{fill: '#94a3b8', fontSize: 12}} tickMargin={10} axisLine={false} />
                  <YAxis tickFormatter={formatVolume} tick={{fill: '#94a3b8', fontSize: 12}} tickLine={false} axisLine={false} />
                  <Tooltip wrapperStyle={{ zIndex: 100 }} contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#fff' }} />
                  <Legend iconType="circle" />
                  <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={3} dot={{r: 4, strokeWidth: 2}} connectNulls={false} />
                  <Line type="monotone" dataKey="IA" name="Projeção IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="Comercial" name="Demanda Comercial (S&OP)" stroke="#eab308" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="Supply" name="Capacidade Fábrica" stroke="#3b82f6" strokeWidth={3} dot={{r: 5, fill: '#3b82f6', stroke: '#fff', strokeWidth: 2}} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="col-span-1 h-[340px]">
             <AiInsightBox alvo={rowData.nome} pmv={kpis.pmvMedio} volume={kpis.vSupply} demandaComercial={kpis.vComercial} />
          </div>
        </div>
      </div>
    );
  };

  const totaisGerais = useMemo(() => {
    const totais: Record<string, { volSup: number, volCom: number }> = {};
    dadosBrutos.forEach(cat => {
      cat.meses?.forEach((m: any) => {
        if (!totais[m.mes_banco]) totais[m.mes_banco] = { volSup: 0, volCom: 0 };
        const edicao = celulasEditadas[cat.id]?.[m.mes_banco];
        const vAtual = edicao !== undefined ? parseInt(edicao.novo_volume) : (m.vol_supply || 0);
        totais[m.mes_banco].volSup += vAtual;
        totais[m.mes_banco].volCom += (m.vol_comercial || 0);
      });
    });
    return totais;
  }, [dadosBrutos, celulasEditadas]);

  const renderRow = (row: any, depth = 0) => {
    const isExpanded = expanded[row.id];
    const hasChildren = row.subRows && row.subRows.length > 0;
    const isProduto = row.tipo === 'produto';

    return (
      <React.Fragment key={row.id}>
        <tr className={`border-b transition-colors hover:bg-slate-50 ${depth === 0 ? 'bg-slate-100/50' : 'bg-white'}`}>
          <td className="p-0 align-middle border-r border-slate-200">
            <div style={{ paddingLeft: `${depth * 2 + 1}rem` }} className="flex items-center gap-3 py-3 min-w-[320px] h-full">
              {hasChildren ? (
                <button onClick={() => setExpanded(p => ({ ...p, [row.id]: !p[row.id] }))} className="p-1.5 hover:bg-slate-300 text-slate-500 rounded-lg transition-colors">
                  {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : <div className="w-8" />}
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg border transition-colors ${chartExpanded === row.id ? 'bg-blue-100 text-blue-600 border-blue-200' : 'hover:bg-slate-200 text-slate-400'}`}>
                <BarChart2 className="w-4 h-4" />
              </button>

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border shrink-0 ${depth === 0 ? 'bg-slate-800 text-white' : 'bg-blue-50 text-blue-600'}`}>
                {depth === 0 ? <LayoutGrid className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm pr-4 ${!isProduto ? 'font-black text-slate-800 tracking-tight' : 'font-semibold text-slate-600'}`}>{row.nome}</span>
                 {isProduto && <span className="text-[10px] text-slate-400 font-mono">SKU: {row.id}</span>}
              </div>
            </div>
          </td>

          {row.meses?.map((m: any, idx: number) => {
            const edicao = celulasEditadas[row.id]?.[m.mes_banco];
            const isEdited = edicao !== undefined;
            const valorExibicao = isEdited ? edicao.novo_volume : (m.vol_supply || 0);
            const gap = valorExibicao - m.vol_comercial;

            return (
              <td key={idx} className="p-0 border-l border-slate-200 align-top">
                <div className={`flex flex-col h-full min-h-[90px] ${isSupplyFechado ? 'bg-slate-50' : isEdited ? 'bg-blue-50/40' : 'hover:bg-slate-50'}`}>
                  
                  {/* CABEÇALHO DA CÉLULA: DEMANDA COMERCIAL E IA */}
                  <div className="px-2 py-1.5 border-b border-slate-200/50 flex justify-center gap-2 items-center bg-slate-100/80">
                    <span className="text-[10px] font-bold text-slate-500 bg-white border border-slate-200 px-2 py-0.5 rounded shadow-sm whitespace-nowrap" title="Projeção IA">
                      IA: {formatVolume(m.vol_ia)}
                    </span>
                    <span className="text-[10px] font-bold text-blue-700 bg-blue-100/50 border border-blue-200 px-2 py-0.5 rounded shadow-sm whitespace-nowrap" title="Demanda Aprovada pelo Comercial">
                      COM: {formatVolume(m.vol_comercial)}
                    </span>
                  </div>
                  
                  {/* CORPO: SUPPLY INPUT E DADOS FINANCEIROS */}
                  <div className="px-4 py-2 flex flex-col items-end justify-center flex-1">
                    <div className="w-24">
                      {/* O SmartInput é travado se o Supply estiver fechado OU se for a linha de Categoria (nível 0) */}
                      <SmartInput 
                         value={valorExibicao} 
                         disabled={isSupplyFechado || depth === 0} 
                         onChange={(novoVol) => handleEditCell(row.id, m.mes_banco, novoVol)} 
                      />
                    </div>
                    {/* Faturamento Previsto Dinâmico */}
                    <span className="text-[10px] font-bold text-emerald-500 tracking-tight pr-1 mt-0.5" title="Receita (R$) Prevista">
                      {formatMoeda(valorExibicao * (m.pmv || 0))}
                    </span>
                    {/* Alerta de Ruptura (Corte de Fair-Share) */}
                    {gap !== 0 && (
                      <span className={`text-[10px] font-bold mt-1 px-1.5 py-0.5 rounded ${gap < 0 ? 'bg-rose-100 text-rose-600' : 'bg-emerald-100 text-emerald-600'}`} title="GAP versus Demanda Comercial">
                        {gap > 0 ? '+' : ''}{formatVolume(gap)} cx
                      </span>
                    )}

                    {/* CAMPO DE JUSTIFICATIVA INDIVIDUAL (Apenas Produto) */}
                    {depth > 0 && !isSupplyFechado && (
                      <div className="w-full mt-2">
                        <input 
                          type="text"
                          placeholder="Justificar ajuste..."
                          value={edicao?.justificativa || ""}
                          onChange={(e) => handleEditJustificativa(row.id, m.mes_banco, e.target.value)}
                          className="w-full bg-white border border-slate-200 text-[10px] px-2 py-1 rounded shadow-sm focus:outline-none focus:border-blue-400 placeholder-slate-300 text-slate-600"
                        />
                      </div>
                    )}
                  </div>

                </div>
              </td>
            );
          })}
        </tr>
        
        {chartExpanded === row.id && <tr><td colSpan={(row.meses?.length || 0) + 1} className="p-0"><PainelSaudabilidade rowData={row} /></td></tr>}
        {isExpanded && hasChildren && row.subRows.map((child: any) => renderRow(child, depth + 1))}
      </React.Fragment>
    );
  };

  const colunasData = dadosBrutos.length > 0 ? dadosBrutos[0].meses : [];

  // =====================================================================
  // TELA DE BLOQUEIO (AGUARDANDO COMERCIAL)
  // =====================================================================
  if (isLoading) return <div className="min-h-screen flex items-center justify-center bg-slate-50 text-slate-400 font-bold">Processando Fábrica...</div>;

  if (!isComercialFechado) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-8">
        <div className="bg-white max-w-2xl w-full p-12 rounded-3xl shadow-2xl text-center border border-slate-100">
          <div className="w-24 h-24 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-6">
            <Lock className="w-10 h-10 text-slate-400" />
          </div>
          <h2 className="text-3xl font-black text-slate-800 mb-4">Aguardando Gerência Comercial</h2>
          <p className="text-slate-500 text-lg mb-8 leading-relaxed">
            A fase de <strong>Supply Review</strong> só pode ser iniciada quando todas as regionais de vendas estiverem oficialmente trancadas no Gerenciamento Comercial.
          </p>
          <button onClick={fetchStatusAndData} className="px-8 py-3 bg-slate-900 hover:bg-slate-800 text-white font-bold rounded-xl shadow-lg transition-colors flex items-center gap-2 mx-auto">
            <Search className="w-5 h-5" /> Verificar Status Novamente
          </button>
        </div>
      </div>
    );
  }

  // =====================================================================
  // TELA PRINCIPAL (SUPPLY ABERTO)
  // =====================================================================
  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32">
      <div className="max-w-[1600px] mx-auto mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
            <Factory className="w-8 h-8 text-blue-600" /> Supply <span className="text-blue-600">Review</span>
          </h1>
          <p className="text-slate-500 mt-1 font-medium">Gestão de Restrições Fabris e Alocação de SKU</p>
        </div>

        <div className="flex items-center gap-4">
          <button onClick={handleExportCSV} className="px-4 py-2.5 bg-white border hover:bg-slate-50 text-slate-700 font-bold rounded-xl shadow-sm transition-colors flex items-center gap-2">
            <Download className="w-4 h-4" /> Exportar Base
          </button>

          {isSupplyFechado ? (
            <button onClick={handleDestrancar} className="px-6 py-2.5 bg-rose-50 text-rose-600 border border-rose-200 hover:bg-rose-100 font-bold rounded-xl shadow-sm transition-colors flex items-center gap-2">
              <Unlock className="w-4 h-4" /> Reabrir Restrições (Supply Fechado)
            </button>
          ) : (
            <button onClick={handleCongelar} className="px-6 py-2.5 bg-slate-900 hover:bg-blue-600 text-white font-bold rounded-xl shadow-lg transition-all flex items-center gap-2">
              <Shield className="w-4 h-4" /> Aplicar Restrições Fabris (Fair-Share)
            </button>
          )}
        </div>
      </div>

      <div className="max-w-[1600px] mx-auto bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr>
                <th className="bg-slate-900 p-0 border-b border-slate-800 w-[400px]">
                  <div className="flex items-center gap-3 px-6 py-5">
                    <Search className="w-5 h-5 text-slate-400" />
                    <input type="text" placeholder="Procurar categoria ou SKU..." value={busca} onChange={(e) => setBusca(e.target.value)} className="bg-transparent border-none text-white focus:outline-none placeholder-slate-500 text-sm font-medium w-full" />
                    {busca && <button onClick={() => setBusca("")}><X className="w-4 h-4 text-slate-400 hover:text-white" /></button>}
                  </div>
                </th>
                {colunasData?.map((m: any, i: number) => (
                  <th key={i} className="bg-slate-900 p-0 border-b border-slate-800 border-l border-slate-800/50 min-w-[180px]">
                    <div className="px-6 py-5 flex flex-col items-center justify-center">
                      <span className="text-white font-bold text-sm tracking-widest">{m.mes_str}</span>
                      <span className="text-blue-400 text-[10px] font-black tracking-widest uppercase mt-0.5">S&OP Supply</span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dadosBrutos.filter(d => d.nome?.toLowerCase().includes(busca.toLowerCase()) || d.subRows?.some((s:any)=>s.nome.toLowerCase().includes(busca.toLowerCase()))).map(row => renderRow(row))}
            </tbody>
            
            {dadosBrutos.length > 0 && (
              <tfoot className="bg-slate-900 sticky bottom-0 z-20 shadow-[0_-10px_40px_rgba(0,0,0,0.1)]">
                <tr>
                  <td className="px-6 py-5 border-r border-slate-800/50">
                    <div className="flex flex-col">
                      <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Restrição Global</span>
                      <span className="font-bold text-sm text-white">CAPACIDADE DA FÁBRICA</span>
                    </div>
                  </td>
                  {colunasData?.map((m: any, i: number) => (
                    <td key={i} className="px-6 py-5 border-l border-slate-800/50 text-right">
                      <div className="flex flex-col items-center justify-center">
                        <span className="font-black text-white text-base">
                          {formatVolume(totaisGerais[m.mes_banco]?.volSup || 0)} <span className="text-[10px] text-slate-400 font-medium ml-1">CX</span>
                        </span>
                        <span className="font-bold text-slate-400 text-xs tracking-tight mt-1 px-2 py-0.5 rounded border border-slate-700 bg-slate-800" title="Demanda Comercial Total">
                          Comercial: {formatVolume(totaisGerais[m.mes_banco]?.volCom || 0)}
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