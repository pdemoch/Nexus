import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Check, Filter, Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Lock, Unlock, Search, X, Store, Package, Users, Download, BarChart2, Activity, Shield, Wand2, Target, AlertTriangle, TrendingUp, TrendingDown
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');

// =====================================================================
// COMPONENTE: NEXUS AI INSIGHT 360°
// =====================================================================
const AiInsightBox = ({ alvo, tipo, pmv }: { alvo: string, tipo: string, pmv: number }) => {
  const [insight, setInsight] = useState('');
  const [loading, setLoading] = useState(false);

  const getInsight = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/ai-sql/perguntar', {
        pergunta: `Faça o dossiê de saudabilidade e risco para o ${tipo} "${alvo}". Avalie o faturamento, volume de caixas e PMV. O PMV base é ${pmv}. Apresente o diagnóstico financeiro (R$) e estratégico utilizando a metodologia dos 4 pilares.`,
        contexto: { tela_ativa: 'Bottom-Up Gerencial', ciclo_status: 'Em Ajuste' }
      });
      setInsight(res.data.resposta);
    } catch (e) {
      setInsight('Erro ao gerar insight. Verifique a ligação com o Agente SQL.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-slate-900/50 rounded-3xl p-6 border border-slate-700 flex flex-col gap-4 h-full shadow-inner">
      <div className="flex items-center gap-3">
         <div className="p-2.5 bg-blue-500/20 text-blue-400 rounded-xl">
           <Wand2 className="w-5 h-5" />
         </div>
         <h4 className="text-sm font-black text-white uppercase tracking-widest">Nexus AI Insight 360°</h4>
      </div>
      
      {insight ? (
        <div className="text-sm font-medium text-slate-300 leading-relaxed whitespace-pre-wrap overflow-y-auto custom-scrollbar pr-2 max-h-[250px]" dangerouslySetInnerHTML={{ __html: insight.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-white">$1</strong>').replace(/\n/g, '<br/>') }} />
      ) : (
        <div className="flex flex-col items-start gap-4 mt-2 h-full justify-center">
           <p className="text-xs text-slate-400 font-medium leading-relaxed">
             Acione o assistente executivo para cruzar o histórico de vendas com as metas atuais e gerar um diagnóstico automático de risco e rentabilidade para este {tipo.toLowerCase()}.
           </p>
           <button onClick={getInsight} disabled={loading} className="mt-2 text-xs font-black bg-blue-600 text-white px-4 py-3 rounded-xl hover:bg-blue-500 transition flex items-center gap-2 disabled:opacity-50 w-full justify-center shadow-lg shadow-blue-900/20">
             {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Analisar Saudabilidade (IA)'}
           </button>
        </div>
      )}
    </div>
  );
};

// =====================================================================
// COMPONENTE: DOSSIÊ DE SAUDABILIDADE (NÚMEROS EXECUTIVOS)
// =====================================================================
const PainelSaudabilidade = ({ rowData, celulasEditadas }: { rowData: any, celulasEditadas: any }) => {
    const kpis = useMemo(() => {
        let volBU = 0; let volTD = 0; let rec = 0; let pmvMedio = 0; let count = 0;

        (rowData?.meses || []).forEach((m: any) => {
            const edicao = celulasEditadas[rowData.chave_matriz]?.[m.mes_banco];
            const vFinal = edicao !== undefined ? parseInt(edicao.novo_volume || 0) : (m.vol_ajustado || 0);
            
            volBU += vFinal;
            volTD += (m.vol_td || 0);
            rec += (vFinal * (m.pmv || 0));
            if (m.pmv > 0) { pmvMedio += m.pmv; count++; }
        });

        return {
            volumeComercial: volBU,
            volumeTopDown: volTD,
            gapTopDown: volBU - volTD,
            receitaProjetada: rec,
            pmvMedio: count > 0 ? pmvMedio / count : 0
        };
    }, [rowData, celulasEditadas]);

    return (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="bg-slate-900 p-5 rounded-2xl border border-slate-700 shadow-sm flex flex-col justify-between">
                <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1 flex items-center gap-2"><Activity className="w-3 h-3"/> Receita Projetada S&OP</h4>
                <p className="text-2xl font-black text-emerald-400 mt-2">{formatMoeda(kpis.receitaProjetada)}</p>
                <div className="mt-3 pt-3 border-t border-slate-800 flex justify-between items-center">
                    <span className="text-[9px] text-slate-500 font-bold uppercase">PMV Ponderado</span>
                    <span className="text-[11px] text-slate-300 font-black">{formatMoeda(kpis.pmvMedio)}/cx</span>
                </div>
            </div>

            <div className="bg-slate-900 p-5 rounded-2xl border border-slate-700 shadow-sm flex flex-col justify-between">
                <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1 flex items-center gap-2"><Package className="w-3 h-3"/> Força de Vendas</h4>
                <div className="flex items-end gap-2 mt-2">
                    <p className="text-2xl font-black text-white">{formatVolume(kpis.volumeComercial)}</p>
                    <span className="text-xs text-slate-500 font-bold mb-1">Caixas</span>
                </div>
                <div className="mt-3 pt-3 border-t border-slate-800 flex justify-between items-center">
                    <span className="text-[9px] text-slate-500 font-bold uppercase">Base IA (Sinal)</span>
                    <span className="text-[11px] text-slate-300 font-black">{formatVolume((rowData?.meses || []).reduce((a:any,b:any)=>a+(b.vol_ia||0),0))} cx</span>
                </div>
            </div>

            <div className={`p-5 rounded-2xl border shadow-sm flex flex-col justify-between ${kpis.gapTopDown < 0 ? 'bg-rose-950/30 border-rose-900/50' : kpis.gapTopDown > 0 ? 'bg-emerald-950/30 border-emerald-900/50' : 'bg-slate-900 border-slate-700'}`}>
                <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1 flex items-center gap-2"><Target className="w-3 h-3"/> Aderência à Diretoria (Top-Down)</h4>
                <div className="flex items-center gap-3 mt-2">
                    <p className="text-2xl font-black text-white">{formatVolume(kpis.volumeTopDown)}</p>
                    {kpis.gapTopDown !== 0 && (
                        <span className={`flex items-center gap-1 text-[10px] font-black px-2 py-1 rounded ${kpis.gapTopDown < 0 ? 'bg-rose-500/20 text-rose-400' : 'bg-emerald-500/20 text-emerald-400'}`}>
                            {kpis.gapTopDown < 0 ? <TrendingDown className="w-3 h-3"/> : <TrendingUp className="w-3 h-3"/>}
                            {formatVolume(Math.abs(kpis.gapTopDown))} cx
                        </span>
                    )}
                </div>
                <div className="mt-3 pt-3 border-t border-slate-800/50 flex justify-start items-center">
                    {kpis.gapTopDown < 0 ? (
                        <span className="text-[10px] font-bold text-rose-400 flex items-center gap-1"><AlertTriangle className="w-3 h-3"/> Risco de Quebra de Meta</span>
                    ) : kpis.gapTopDown > 0 ? (
                        <span className="text-[10px] font-bold text-emerald-400 flex items-center gap-1"><Check className="w-3 h-3"/> Cobertura Excedente</span>
                    ) : (
                        <span className="text-[10px] font-bold text-slate-400 flex items-center gap-1"><Check className="w-3 h-3"/> Meta Alinhada</span>
                    )}
                </div>
            </div>
        </div>
    );
}

// =====================================================================
// MAIN COMPONENT: GERENCIAMENTO ARENA
// =====================================================================
export default function GerenciamentoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const [nivelHierarquia, setNivelHierarquia] = useState('regional');
  const [nomeResponsavel, setNomeResponsavel] = useState('');

  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [isTopDownFechado, setIsTopDownFechado] = useState<boolean | null>(null);
  
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  const [busca, setBusca] = useState('');
  const [opcoesBusca, setOpcoesBusca] = useState<{coordenadores: string[], vendedores: string[], regionais: string[]}>({coordenadores: [], vendedores: [], regionais: []});

  useEffect(() => {
    axios.get('/api/v1/consensus/gerenciamento/filtros')
         .then(res => setOpcoesBusca(res.data)).catch(console.error);

    axios.get('/api/v1/consensus/macro/status')
         .then(res => setIsTopDownFechado(res.data.is_topdown_fechado))
         .catch(() => setIsTopDownFechado(false));
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      // O 'nocache' obriga o Cloudflare a ignorar o cache antigo!
      const params = { 
          nivel_filtro: nivelHierarquia, 
          valor_filtro: nomeResponsavel || '',
          nocache: new Date().getTime() 
      };
      
      const res = await axios.get('/api/v1/consensus/gerenciamento', { params });
      
      console.log("DADOS FRESCOS DA API:", res.data.dados);
      
      setDadosBrutos(res.data.dados || []);
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) {
      console.error("ERRO NA API DE MICRO:", e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, [nivelHierarquia, nomeResponsavel]);

  useEffect(() => { 
    if (isTopDownFechado) fetchData(); 
  }, [fetchData, isTopDownFechado]);

  // =====================================================================
  // FILTRO SEGURO: Devolve os dados originais se não houver busca
  // =====================================================================
  const dadosFiltrados = useMemo(() => {
    if (!dadosBrutos || dadosBrutos.length === 0) return [];
    if (!busca || busca.trim() === '') return dadosBrutos; // Se a busca está vazia, retorna a árvore intocada!
    
    const term = busca.toLowerCase();
    
    return dadosBrutos.map(coord => {
      if (coord?.nome?.toLowerCase().includes(term)) return coord;
      
      const vendedoresFiltrados = (coord?.subRows || []).map((vendedor: any) => {
        if (vendedor?.nome?.toLowerCase().includes(term)) return vendedor;
        
        const clientesFiltrados = (vendedor?.subRows || []).map((cliente: any) => {
          if (cliente?.nome?.toLowerCase().includes(term)) return cliente;
          
          const prodsFiltrados = (cliente?.subRows || []).filter((p: any) => 
            (p?.nome?.toLowerCase().includes(term) || (p?.produto?.toLowerCase().includes(term)))
          );
          
          if (prodsFiltrados.length > 0) return { ...cliente, subRows: prodsFiltrados };
          return null;
        }).filter(Boolean);

        if (clientesFiltrados.length > 0) return { ...vendedor, subRows: clientesFiltrados };
        return null;
      }).filter(Boolean);

      if (vendedoresFiltrados.length > 0) return { ...coord, subRows: vendedoresFiltrados };
      return null;
    }).filter(Boolean);
  }, [dadosBrutos, busca]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, {vol: number, fat: number}> = {};
    
    (dadosFiltrados || []).forEach(coord => {
      (coord?.meses || []).forEach((m: any) => {
          if (!totais[m.mes_banco]) totais[m.mes_banco] = {vol: 0, fat: 0};
      });
    });
    
    (dadosFiltrados || []).forEach(coord => {
      (coord?.subRows || []).forEach((vendedor: any) => {
        (vendedor?.subRows || []).forEach((cliente: any) => {
          (cliente?.subRows || []).forEach((prod: any) => {
            const pmvBase = (prod?.meses && prod.meses.length > 0) ? (prod.meses[0]?.pmv || 0) : 0;
            
            (prod?.meses || []).forEach((mes: any) => {
              const chaveEdicao = celulasEditadas[prod?.chave_matriz]?.[mes.mes_banco];
              const isPendente = chaveEdicao !== undefined;
              
              const volCru = isPendente ? chaveEdicao.novo_volume : mes.vol_ajustado;
              const volumeFinal = Math.round(Number(volCru || 0));
              
              if(totais[mes.mes_banco]) {
                totais[mes.mes_banco].vol += volumeFinal;
                totais[mes.mes_banco].fat += isPendente ? (volumeFinal * pmvBase) : (mes.receita || 0);
              }
            });
          });
        });
      });
    });
    return totais;
  }, [dadosFiltrados, celulasEditadas]);

  const handleExportExcel = () => {
    if (!dadosBrutos || dadosBrutos.length === 0) return alert("Não há dados na tela para exportar.");
    const dadosExcel: any[] = [];
    
    (dadosBrutos || []).forEach(coord => {
      (coord?.subRows || []).forEach((vendedor: any) => {
        (vendedor?.subRows || []).forEach((cliente: any) => {
          (cliente?.subRows || []).forEach((prod: any) => {
            const linha: any = {
              "COORDENADOR": coord?.nome, "VENDEDOR": vendedor?.nome,
              "RAZÃO SOCIAL": cliente?.nome, "CÓDIGO SKU": prod?.produto, "DESCRIÇÃO": prod?.nome, 
              "PMV PONDERADO (R$)": (prod?.meses && prod.meses.length > 0) ? prod.meses[0].pmv : 0
            };
            (prod?.meses || []).forEach((m: any) => {
              const edicao = celulasEditadas[prod.chave_matriz]?.[m.mes_banco];
              const volFinal = Math.round(Number(edicao !== undefined ? edicao.novo_volume : m.vol_ajustado));
              linha[`${m.mes_str} (Base IA)`] = Math.round(Number(m.vol_ia));
              linha[`${m.mes_str} (Top-Down)`] = Math.round(Number(m.vol_td || 0));
              linha[`${m.mes_str} (Comercial)`] = volFinal;
            });
            dadosExcel.push(linha);
          });
        });
      });
    });

    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Gerencial_Consenso");
    XLSX.writeFile(workbook, `SOP_Nexus_Gerencial_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleSalvar = async () => {
    setIsProcessing(true);
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([chave, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        const partes = chave.split('|');
        const nivel = partes.length === 4 ? 'produto' : partes.length === 3 ? 'cliente' : partes.length === 2 ? 'vendedor' : 'coordenador';

        ajustes.push({ 
          nivel: nivel, 
          chave: chave, 
          mes_projetado: mes, 
          novo_volume: val.novo_volume === '' ? 0 : val.novo_volume 
        });
      });
    });

    try {
      await axios.post(`//api/v1/consensus/gerenciamento/congelar`, { origem_ajuste: "Comercial", ajustes });
      alert("✅ Ajustes Gerenciais salvos e consolidados!");
      setCelulasEditadas({});
      fetchData(); 
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao guardar.");
    } finally {
      setIsProcessing(false);
    }
  };

  const alternarTrancaRegional = async () => {
    if (!window.confirm("Atenção: Ao destrancar a Regional, os coordenadores e vendedores poderão alterar as cotas novamente. Confirma?")) return;
    setIsProcessing(true);
    try {
      await axios.post(`/api/v1/consensus/gerenciamento/destrancar`, { regional: nomeResponsavel || "TODAS" });
      alert("🔓 Regional(is) destrancada(s) para ajustes da base.");
      fetchData();
    } catch(e) { alert("Erro ao destrancar regional."); } 
    finally { setIsProcessing(false); }
  };

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
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

  const qtdEdicoes = Object.keys(celulasEditadas).length;
  const isAllFechado = dadosBrutos?.length > 0 && dadosBrutos[0]?.status === 'Fechado';

  const columns = useMemo(() => {
    if (!dadosFiltrados || dadosFiltrados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Supervisão / Vendedor / Cliente / Produto',
        accessorFn: (row: any) => row?.nome,
        cell: (info: any) => {
          const row = info.row;
          const isCoordenador = row.original?.tipo === 'coordenador';
          const isVendedor = row.original?.tipo === 'vendedor';
          const isCliente = row.original?.tipo === 'cliente';
          const isProduto = row.original?.tipo === 'produto';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[320px]">
              {!isProduto ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-200 text-slate-500 rounded-lg transition-colors">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : (
                <div className="w-8"></div>
              )}
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg transition-colors border ${chartExpanded === row.original?.chave_matriz ? 'bg-blue-100 border-blue-200 text-blue-600 shadow-sm' : 'hover:bg-slate-100 border-transparent text-slate-400 hover:text-slate-600'}`} title="Ver Dossiê Analítico">
                <BarChart2 className="w-4 h-4" />
              </button>

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 
                 ${isCoordenador ? 'bg-slate-800 border-slate-700 text-white' : 
                   isVendedor ? 'bg-blue-50 border-blue-100 text-blue-600' : 
                   isCliente ? 'bg-indigo-50 border-indigo-100 text-indigo-600' : 'bg-slate-50 border-slate-100 text-slate-400'}`}>
                {isCoordenador ? <Shield className="w-4 h-4" /> : isVendedor ? <Users className="w-4 h-4" /> : isCliente ? <Store className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm ${!isProduto ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[250px]'}`}>
                   {info.getValue()}
                 </span>
                 {isCoordenador && (
                     <div className="flex items-center gap-1 mt-1">
                         {row.original?.status === 'Fechado' ? <Lock className="w-3 h-3 text-rose-500"/> : <Unlock className="w-3 h-3 text-emerald-500"/>}
                         <span className={`text-[9px] font-black uppercase tracking-widest ${row.original?.status === 'Fechado' ? 'text-rose-500' : 'text-emerald-500'}`}>{row.original?.status}</span>
                     </div>
                 )}
                 {isProduto && (
                   <div className="flex items-center gap-2 mt-1">
                     <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{row.original?.produto}</span>
                     <span className="text-[9px] text-emerald-600 font-black uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">PMV: {formatMoeda(row.original?.meses?.[0]?.pmv || 0)}</span>
                   </div>
                 )}
              </div>
            </div>
          )
        }
      }
    ];

    const mesesMap = new Map();
    (dadosFiltrados || []).forEach(v => (v?.meses || []).forEach((m: any) => mesesMap.set(m.mes_banco, m)));
    
    Array.from(mesesMap.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)).forEach((m: any) => {
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: m.mes_str,
        accessorFn: (row: any) => row?.meses?.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_ajustado || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const isCoordenador = row?.tipo === 'coordenador';
          const isVendedor = row?.tipo === 'vendedor';
          const isCliente = row?.tipo === 'cliente';
          
          const dadosMes = (row?.meses || []).find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta?.celulasEditadas?.[row?.chave_matriz]?.[m.mes_banco];
          
          const isPendente = edicao !== undefined;
          const valorReal = isPendente ? edicao.novo_volume : (dadosMes?.vol_ajustado || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const baseIA = Math.round(Number(dadosMes?.vol_ia || 0));
          const baseTopDown = Math.round(Number(dadosMes?.vol_td || 0));
          const isChanged = valorInteiro !== baseIA;
          
          const faturamentoPrevisto = isPendente ? (valorInteiro * (dadosMes?.pmv || 0)) : (dadosMes?.receita || 0);
          
          const temPressao = baseTopDown > valorInteiro;
          const bloqueadoPelaEquipa = isCoordenador ? false : row?.status === 'Fechado';
          
          if (isCoordenador || isVendedor) {
            return (
              <div className="flex flex-col items-center justify-center py-2 w-28 relative">
                 {temPressao && <span className="absolute -top-1 text-[8px] text-rose-500 font-black uppercase tracking-widest bg-rose-50 px-1 rounded border border-rose-100" title="Meta da Diretoria (Top-Down)">TD: {baseTopDown}</span>}
                 <span className="text-sm font-black text-slate-800">{formatVolume(valorInteiro)}</span>
                 <span className="text-[10px] font-black text-emerald-600 mt-1">{formatMoeda(dadosMes?.receita || 0)}</span>
              </div>
            );
          }

          return (
            <div className="flex flex-col w-28 gap-1.5 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest" title="Sugestão IA">IA: {baseIA}</span>
                 {temPressao && <span className="text-[9px] text-rose-500 font-black uppercase tracking-widest bg-rose-50 px-1 rounded border border-rose-100" title="Meta da Diretoria (Top-Down)">TD: {baseTopDown}</span>}
              </div>
              <input
                type="number" value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0"
                onChange={(e) => meta.updateCell(row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${bloqueadoPelaEquipa ? 'bg-amber-50 border-amber-200 text-amber-700 focus:border-amber-500' : 
                    isCliente ? 'border-dashed border-blue-200 focus:border-blue-500 focus:bg-blue-50 text-blue-900 bg-white' : 'border-solid'} 
                  ${isChanged && !isCliente ? 'bg-blue-600 border-blue-700 text-white shadow-md' : !isCliente ? 'bg-slate-50 border-transparent text-slate-800 focus:bg-white focus:border-slate-300' : ''}`}
                title={bloqueadoPelaEquipa ? "Equipa Trancada! Edição Gerencial Forçada" : isCliente ? "Editar Matriz (Rateia por todos os SKUs)" : "Editar SKU"}
              />
              <span className={`text-[10px] font-black text-center tracking-tight ${isPendente ? 'text-indigo-500 animate-pulse' : isCliente ? 'text-blue-500' : 'text-emerald-600'}`}>
                {isPendente ? 'Calculando...' : formatMoeda(faturamentoPrevisto)}
              </span>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosFiltrados, chartExpanded, celulasEditadas]);

  const table = useReactTable({
    data: dadosFiltrados, 
    columns, 
    state: { expanded, sorting },
    onExpandedChange: setExpanded, 
    onSortingChange: setSorting,
    // A correção mestra da expansão das linhas (Não renderiza nós nulos)
    getSubRows: row => row?.subRows && row.subRows.length > 0 ? row.subRows : undefined,
    getCoreRowModel: getCoreRowModel(), 
    getExpandedRowModel: getExpandedRowModel(), 
    getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      updateCell: (chave: string, mes: string, val: string) => {
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV } } }));
      }
    }
  });

  if (isTopDownFechado === null) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500 mb-4" />
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
            A fase Gerencial só pode ser iniciada após a aprovação e congelamento da demanda macro pela Diretoria.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Activity className="w-8 h-8 text-blue-600" /> Cockpit Gerencial
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Visão Panorâmica de Vendas e Correções da Base
            </p>
          </div>
          
          <div className="flex items-center gap-4">
              <div className="flex items-center gap-3 h-full">
                {isAllFechado && (
                   <button onClick={alternarTrancaRegional} disabled={isProcessing} className="flex items-center gap-2 bg-rose-50 hover:bg-rose-100 text-rose-600 px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all border border-rose-200 shadow-sm disabled:opacity-50">
                      <Unlock className="w-5 h-5" /> Destrancar Bases
                   </button>
                )}
                {qtdEdicoes > 0 && (<button onClick={() => setCelulasEditadas({})} className="flex items-center gap-1 text-xs font-black text-rose-500 hover:text-rose-700 transition tracking-widest uppercase px-4 py-3 rounded-2xl hover:bg-rose-50"><X className="w-4 h-4" /> Descartar</button>)}
                <button onClick={handleSalvar} disabled={isProcessing} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg shadow-blue-600/30">
                    {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : <Check className="w-5 h-5" />}
                    {qtdEdicoes > 0 ? 'Gravar Alterações' : 'Aprovar Ciclo'}
                </button>
              </div>
          </div>
        </div>

        {/* CONTROLES E BUSCA */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
            <div className="flex items-center gap-4 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex-shrink-0 px-6">
                <span className="text-xs font-black text-slate-500 uppercase tracking-widest">Visualizar Base:</span>
                <select 
                    value={nivelHierarquia} onChange={e => { setNivelHierarquia(e.target.value); setNomeResponsavel(''); }} 
                    className="bg-slate-50 border border-slate-200 p-2.5 rounded-xl text-sm font-bold text-slate-700 outline-none cursor-pointer"
                >
                    <option value="regional">Por Regionais</option>
                    <option value="coordenador">Por Coordenações</option>
                    <option value="vendedor">Por Vendedores</option>
                </select>
                <select 
                    value={nomeResponsavel} onChange={e => { setNomeResponsavel(e.target.value); }} 
                    className="bg-slate-50 border border-slate-200 p-2.5 rounded-xl text-sm font-bold text-slate-700 outline-none cursor-pointer max-w-[200px] truncate"
                >
                    <option value="">-- Todos --</option>
                    {nivelHierarquia === 'regional' && opcoesBusca?.regionais?.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                    {nivelHierarquia === 'coordenador' && opcoesBusca?.coordenadores?.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                    {nivelHierarquia === 'vendedor' && opcoesBusca?.vendedores?.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                </select>
                <button 
                  onClick={fetchData} disabled={isLoading} 
                  className="bg-blue-50 text-blue-600 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-blue-100 transition disabled:opacity-50 flex items-center gap-2"
                >
                   {isLoading ? <Loader2 className="w-4 h-4 animate-spin"/> : <Filter className="w-4 h-4"/>} Filtrar
                </button>
            </div>

           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 h-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input 
                 type="text" placeholder="Pesquisar livremente por Vendedor, Cliente ou Produto..." value={busca} onChange={e => setBusca(e.target.value)}
                 className="w-full bg-transparent py-2 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400"
               />
             </div>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-blue-50 text-slate-700 px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200 h-full whitespace-nowrap">
                <Download className="w-4 h-4" /> Exportar Planilha
             </button>
           </div>
        </div>

        {/* TABELA DE DADOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-blue-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Processando Sumário Gerencial...</span>
             </div>
          ) : !dadosFiltrados || dadosFiltrados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Filter className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">Nenhum dado encontrado</span>
             </div>
          ) : (
          <div className="overflow-x-auto pb-4 custom-scrollbar">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm sticky top-0 z-10">
                {table.getHeaderGroups()?.map(hg => (
                  <tr key={hg.id}>
                    {hg?.headers?.map(header => (
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
              
              <tbody className="mb-20">
                {table.getRowModel()?.rows?.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-blue-50/20' : row.original?.tipo === 'coordenador' ? 'bg-slate-50 hover:bg-slate-100' : row.original?.tipo === 'vendedor' ? 'bg-white hover:bg-slate-50' : 'bg-slate-50/50 hover:bg-slate-100'}`}>
                      {row.getVisibleCells()?.map(cell => (
                        <td key={cell.id} className="px-8 py-3">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    
                    {/* AQUI COMEÇA O DOSSIÊ DE SAUDABILIDADE EXPANDIDO */}
                    {chartExpanded === row.original?.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                          <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500">
                            
                            <div className="flex justify-between items-start mb-6 px-2">
                                <div className="flex flex-col gap-1">
                                    <h3 className="text-lg font-black text-white uppercase tracking-tighter flex items-center gap-2">
                                    <Activity className="w-5 h-5 text-blue-400" /> Dossiê de Saudabilidade ({row.original?.tipo === 'coordenador' ? 'Visão Supervisão' : row.original?.tipo === 'vendedor' ? 'Visão Carteira de Vendas' : row.original?.tipo === 'cliente' ? 'Visão Conta Global' : 'Visão Produto'})
                                    </h3>
                                    <p className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                                    {row.original?.nome}
                                    </p>
                                </div>
                            </div>

                            <PainelSaudabilidade rowData={row.original} celulasEditadas={celulasEditadas} />
                            
                            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                               <div className="lg:col-span-2">
                                 <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2"><BarChart2 className="w-3 h-3"/> Timeline S&OE (Lag Forecast)</h4>
                                 <div className="h-[250px] w-full">
                                   {loadingGrafico === row.original?.chave_matriz ? (
                                     <div className="h-full flex items-center justify-center text-slate-600"><Loader2 className="animate-spin w-8 h-8" /></div>
                                   ) : (
                                     <ResponsiveContainer width="100%" height="100%">
                                       <LineChart 
                                         data={
                                           (dadosGraficoCache[row.original?.chave_matriz] || [])?.map((p: any) => {
                                               const mesNaTab = (row.original?.meses || []).find((m: any) => m.mes_banco === p.data_iso);
                                               const edicao = celulasEditadas[row.original?.chave_matriz]?.[p.data_iso];
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
                                         
                                         <Line type="monotone" dataKey="CicloAnterior" name="Ciclo Anterior (Lag)" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                                         <Line type="monotone" dataKey="Realizado" name="Venda Real" stroke="#f8fafc" strokeWidth={4} dot={{r: 3, fill: '#f8fafc'}} connectNulls={false} />
                                         <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#475569" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls={false} />
                                         <Line type="monotone" dataKey="Consenso" name="Proposta Comercial" stroke="#3b82f6" strokeWidth={5} dot={{r: 6, fill: '#3b82f6', strokeWidth: 2, stroke: '#0f172a'}} connectNulls={false} />
                                       </LineChart>
                                     </ResponsiveContainer>
                                   )}
                                 </div>
                               </div>

                               <div className="flex flex-col justify-start">
                                 <AiInsightBox 
                                    alvo={row.original?.nome} 
                                    tipo={row.original?.tipo === 'coordenador' ? 'Coordenador' : row.original?.tipo === 'vendedor' ? 'Vendedor' : row.original?.tipo === 'cliente' ? 'Cliente' : 'SKU'} 
                                    pmv={(row.original?.meses && row.original.meses.length > 0) ? row.original.meses[0].pmv : 0}
                                 />
                               </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
              <tfoot className="bg-slate-900 text-white shadow-inner sticky bottom-0 z-20">
                <tr>
                  {table.getHeaderGroups()?.[0]?.headers?.map(header => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-8 py-5 text-right rounded-bl-[40px]">
                        <div className="flex flex-col"><span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total Consolidação</span><span className="font-bold text-sm text-white">SUMÁRIO GERENCIAL</span></div>
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