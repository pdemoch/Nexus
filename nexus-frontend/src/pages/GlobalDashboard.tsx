import React, { useState, useMemo, useEffect, useCallback, Fragment, useRef } from 'react';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, 
  getSortedRowModel, SortingState, ColumnDef 
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Layers, Lock, Download, AlertTriangle, ShieldCheck, Check, Globe, Package, 
  Users, Search, Filter, BarChart3, TrendingUp, TrendingDown, Activity, Hash, Target,
  ArrowRightLeft, AlertCircle, Info, Bot, Send, X, Sparkles, Lightbulb, History, Zap
} from 'lucide-react';
import axios from 'axios';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, AreaChart, Area } from 'recharts';

// --- HELPERS DE FORMATAÇÃO ---
const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

const VarBadge = ({ atual = 0, anterior = 0 }: { atual?: number, anterior?: number }) => {
  const v = calcVar(atual, anterior);
  if (v === 0 || anterior === 0) return <span className="text-[10px] text-slate-400 font-bold">-</span>;
  const isPos = v > 0;
  return (
    <span className={`text-[10px] font-black flex items-center gap-0.5 px-1.5 py-0.5 rounded ${isPos ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
      {isPos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />} {Math.abs(v).toFixed(1)}%
    </span>
  );
};

const tooltipSorterRow = (item: any) => {
  const order: Record<string, number> = { 'Histórico Real': 1, 'Proposta Mês Passado': 2, 'Sinal IA': 3, 'Proposta Comercial': 4, 'Restrição Supply': 5, 'Global S&OP': 6 };
  return order[item.name] || 99;
};

// =========================================================
// COMPONENTE: DOSSIÊ DO SKU (SUB-ROW)
// =========================================================
const SKUPerformanceDossier = ({ rowData }: { rowData: any }) => {
  const meses = rowData.meses || [];
  
  // Agregados do Dossiê
  const totais = useMemo(() => {
    return meses.reduce((acc: any, m: any) => ({
      ia: acc.ia + (m.vol_ia || 0),
      bu: acc.bu + (m.vol_bu || 0),
      sp: acc.sp + (m.vol_sp || 0),
      final: acc.final + (m.vol_final || 0),
      rec: acc.rec + (m.rec_final || 0),
      ant: acc.ant + (m.vol_anterior || 0),
      hist: acc.hist + (m.vol_hist_media || 0)
    }), { ia:0, bu:0, sp:0, final:0, rec:0, ant:0, hist:0 });
  }, [meses]);

  const justificativas = useMemo(() => {
    const set = new Set<string>();
    meses.forEach((m: any) => m.justificativas?.forEach((j: string) => set.add(j)));
    return Array.from(set);
  }, [meses]);

  return (
    <div className="p-8 bg-slate-50/80 border-y border-slate-200 animate-in fade-in slide-in-from-top-2 duration-300">
      <div className="max-w-[1400px] mx-auto grid grid-cols-1 lg:grid-cols-4 gap-6">
        
        {/* COL 1: KPIs DE SAÚDE */}
        <div className="bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 flex flex-col gap-4">
          <div className="flex items-center gap-2 text-indigo-600 mb-2">
             <Target className="w-5 h-5" />
             <span className="text-[10px] font-black uppercase tracking-widest">Resumo do Ciclo</span>
          </div>
          <div>
            <p className="text-[10px] font-bold text-slate-400 uppercase">Receita Total Projetada</p>
            <p className="text-xl font-black text-slate-900">{formatMoeda(totais.rec)}</p>
          </div>
          <div className="grid grid-cols-2 gap-4 pt-4 border-t border-slate-50">
             <div>
                <p className="text-[9px] font-bold text-slate-400 uppercase">vs Ciclo Anterior</p>
                <div className="flex items-center gap-2">
                   <span className="text-sm font-black">{formatVolume(totais.final)}</span>
                   <VarBadge atual={totais.final} anterior={totais.ant} />
                </div>
             </div>
             <div>
                <p className="text-[9px] font-bold text-slate-400 uppercase">vs Méd. Histórica</p>
                <div className="flex items-center gap-2">
                   <span className="text-sm font-black">{formatVolume(totais.final)}</span>
                   <VarBadge atual={totais.final} anterior={totais.hist} />
                </div>
             </div>
          </div>
        </div>

        {/* COL 2 & 3: CONSTRUÇÃO DO VOLUME (WATERFALL VISUAL) */}
        <div className="lg:col-span-2 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100">
           <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-2 text-emerald-600">
                <Zap className="w-5 h-5" />
                <span className="text-[10px] font-black uppercase tracking-widest">Aderência e Construção do Plano</span>
              </div>
              <span className="text-[10px] font-bold text-slate-300">Volume Acumulado (M2-M4)</span>
           </div>
           
           <div className="flex items-end justify-between h-[120px] px-4">
              {[
                { label: 'IA', val: totais.ia, color: 'bg-slate-200' },
                { label: 'Comercial', val: totais.bu, color: 'bg-indigo-400' },
                { label: 'Supply', val: totais.sp, color: 'bg-rose-400' },
                { label: 'Final', val: totais.final, color: 'bg-slate-900' }
              ].map((step, idx) => (
                <div key={idx} className="flex flex-col items-center gap-2 flex-1">
                   <div className="text-[10px] font-black text-slate-400">{formatVolume(step.val)}</div>
                   <div className={`w-12 rounded-t-xl transition-all duration-700 ${step.color}`} style={{ height: `${(step.val / Math.max(totais.ia, totais.bu, totais.final, 1)) * 100}%`, minHeight: '4px' }} />
                   <div className="text-[9px] font-bold text-slate-500 uppercase mt-1">{step.label}</div>
                </div>
              ))}
           </div>
        </div>

        {/* COL 4: NOTAS E AÇÕES */}
        <div className="bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 overflow-hidden">
          <div className="flex items-center gap-2 text-rose-500 mb-4">
             <AlertCircle className="w-5 h-5" />
             <span className="text-[10px] font-black uppercase tracking-widest">Ações de Supply</span>
          </div>
          <div className="flex flex-col gap-3 h-[140px] overflow-y-auto custom-scrollbar pr-2">
             {justificativas.length > 0 ? justificativas.map((j: string, i: number) => (
               <div key={i} className="p-3 bg-rose-50 border-l-4 border-rose-400 rounded-r-xl">
                  <p className="text-[11px] font-bold text-rose-800 leading-tight italic">"{j}"</p>
               </div>
             )) : (
               <div className="h-full flex items-center justify-center border-2 border-dashed border-slate-100 rounded-2xl">
                  <span className="text-[10px] font-bold text-slate-300 uppercase">Sem restrições registradas</span>
               </div>
             )}
          </div>
        </div>

      </div>
    </div>
  );
};

export default function GlobalDashboard() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [lockMessage, setLockMessage] = useState('');
  
  const [viewMode, setViewMode] = useState<'categorias' | 'clientes'>('categorias');
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [busca, setBusca] = useState('');

  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

  // --- ESTADOS DA IA ---
  const [isAiOpen, setIsAiOpen] = useState(false);
  const [aiInput, setAiInput] = useState('');
  const [isAiTyping, setIsAiTyping] = useState(false);
  const [aiMessages, setAiMessages] = useState<{role: 'ai' | 'user', content: string}[]>([
    { role: 'ai', content: 'Olá! Sou o **Nexus AI Copilot**. Já processei a matemática de Supply, Volatilidade, PMV e Assertividade deste S&OP. Como posso apoiar a sua análise?' }
  ]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // --- SUGESTÕES DINÂMICAS IA ---
  const sugestoesIA = viewMode === 'categorias' 
    ? ["Qual categoria teve maior corte de Supply?", "A IA sugeriu inovação em qual linha?", "Houve queda no preço médio (PMV)?"]
    : ["Quais clientes perderam faturamento?", "Explique as justificativas da fábrica", "Qual cliente mais cresceu vs Histórico?"];

  // --- CARGA DE DADOS ---
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/dashboard/global');
      setDadosBrutos(res.data.dados || []);
      setIsLocked(res.data.is_locked);
      setLockMessage(res.data.lock_message);
      setExpanded({});
      setChartExpanded(null);
    } catch (e) { console.error(e); } finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const mesesDisponiveis = useMemo(() => {
    const meses = new Set(dadosBrutos.map(d => d.mes_projetado));
    return Array.from(meses).sort() as string[];
  }, [dadosBrutos]);

  const handleExportExcel = async () => {
    try {
        const response = await axios.get('/api/v1/dashboard/export', { responseType: 'blob' });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url; link.setAttribute('download', 'Plano_Oficial_Nexus.xlsx');
        document.body.appendChild(link); link.click(); link.remove();
    } catch (e) { alert("Erro ao exportar."); }
  };

  const handleAprovar = async () => {
    if (isLocked) return; 
    if (!window.confirm("Confirmar a publicação do plano oficial? Isto irá congelar as metas do ciclo.")) return;
    setIsProcessing(true);
    try {
      await axios.post('/api/v1/dashboard/aprovar', { ajustes: [] });
      alert("✅ S&OP Global Aprovado!");
      fetchData(); 
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao aprovar."); } 
    finally { setIsProcessing(false); }
  };

  // --- MOTOR DE AGRUPAMENTO (CATEGORIAS) ---
  const dadosAgrupadosCategorias = useMemo(() => {
    if (!dadosBrutos.length || viewMode !== 'categorias') return [];
    const term = busca.toLowerCase();
    const filtered = dadosBrutos.filter(d => (d.sku + ' ' + d.descricao + ' ' + d.razaosocial + ' ' + d.categoria).toLowerCase().includes(term));
    const tree = new Map();
    
    filtered.forEach(r => {
      if (!tree.has(r.categoria)) {
        tree.set(r.categoria, { id: r.categoria, chave_matriz: r.categoria, nome: r.categoria, tipo: 'categoria', meses: new Map(), skus: new Map() });
      }
      const cat = tree.get(r.categoria);
      if (!cat.skus.has(r.sku)) {
        cat.skus.set(r.sku, { id: `${r.categoria}|${r.sku}`, chave_matriz: `${r.categoria}|${r.sku}`, nome: r.descricao, produto: r.sku, tipo: 'produto', meses: new Map(), subRows: [] });
      }
      const skuNode = cat.skus.get(r.sku);
      const m = r.mes_projetado;

      [cat, skuNode].forEach(node => {
        if (!node.meses.has(m)) node.meses.set(m, { mes_banco: m, vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0, unmet_brl: 0, delta_ia_brl: 0, vol_anterior:0, vol_hist_media:0, justificativas: new Set() });
        const target = node.meses.get(m);
        target.vol_ia += (r.vol_ia || 0); target.vol_bu += (r.vol_bottomup || 0); target.vol_sp += (r.vol_supply || 0); target.vol_final += (r.vol_final || 0);
        target.rec_final += (r.rec_final || 0); target.unmet_brl += (r.unmet_demand_brl || 0); target.delta_ia_brl += (r.impacto_financeiro_ia_brl || 0);
        target.vol_anterior += (r.vol_anterior || 0); target.vol_hist_media += (r.vol_hist_media || 0);
        if (r.justificativa_supply) target.justificativas.add(r.justificativa_supply);
      });
    });

    return Array.from(tree.values()).map(cat => ({
      ...cat, meses: Array.from(cat.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
      subRows: Array.from(cat.skus.values()).map((s: any) => ({
        ...s, meses: Array.from(s.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
      }))
    }));
  }, [dadosBrutos, busca, viewMode]);

  // --- MOTOR DE AGRUPAMENTO (CLIENTES PARETO 80%) ---
  const dadosAgrupadosClientes = useMemo(() => {
    if (!dadosBrutos.length || viewMode !== 'clientes') return [];
    
    const faturamentoPorCliente = new Map();
    let totalGlobal = 0;
    dadosBrutos.forEach(d => {
      const nome = d.razaosocial || 'N/A';
      faturamentoPorCliente.set(nome, (faturamentoPorCliente.get(nome) || 0) + (d.rec_final || 0));
      totalGlobal += (d.rec_final || 0);
    });

    const sortedClientes = Array.from(faturamentoPorCliente.entries()).sort((a, b) => b[1] - a[1]);
    let acumulado = 0;
    const topClientesSet = new Set();
    for (const [nome, valor] of sortedClientes) {
      topClientesSet.add(nome);
      acumulado += valor;
      if (totalGlobal > 0 && acumulado >= totalGlobal * 0.8) break;
    }

    const tree = new Map();
    const term = busca.toLowerCase();
    
    dadosBrutos
      .filter(d => topClientesSet.has(d.razaosocial))
      .filter(d => (d.sku + ' ' + d.descricao + ' ' + d.razaosocial).toLowerCase().includes(term))
      .forEach(r => {
        if (!tree.has(r.razaosocial)) tree.set(r.razaosocial, { id: r.razaosocial, chave_matriz: r.razaosocial, nome: r.razaosocial, tipo: 'cliente_pai', meses: new Map(), skus: new Map() });
        const cli = tree.get(r.razaosocial);
        if (!cli.skus.has(r.sku)) cli.skus.set(r.sku, { id: `${r.razaosocial}|${r.sku}`, chave_matriz: `${r.razaosocial}|${r.sku}`, nome: r.descricao, produto: r.sku, tipo: 'produto', meses: new Map() });
        const skuNode = cli.skus.get(r.sku);
        const m = r.mes_projetado;

        [cli, skuNode].forEach(node => {
          if (!node.meses.has(m)) node.meses.set(m, { mes_banco: m, vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0, unmet_brl: 0, delta_ia_brl: 0, vol_anterior:0, vol_hist_media:0, justificativas: new Set() });
          const target = node.meses.get(m);
          target.vol_ia += (r.vol_ia || 0); target.vol_bu += (r.vol_bottomup || 0); target.vol_sp += (r.vol_supply || 0); target.vol_final += (r.vol_final || 0);
          target.rec_final += (r.rec_final || 0); target.unmet_brl += (r.unmet_demand_brl || 0); target.delta_ia_brl += (r.impacto_financeiro_ia_brl || 0);
          target.vol_anterior += (r.vol_anterior || 0); target.vol_hist_media += (r.vol_hist_media || 0);
          if (r.justificativa_supply) target.justificativas.add(r.justificativa_supply);
        });
      });

    return Array.from(tree.values())
      .sort((a, b) => {
        const recA = Array.from(a.meses.values() as any).reduce((acc: any, curr: any) => acc + curr.rec_final, 0);
        const recB = Array.from(b.meses.values() as any).reduce((acc: any, curr: any) => acc + curr.rec_final, 0);
        return (recB as number) - (recA as number);
      })
      .map(cli => ({
        ...cli, meses: Array.from(cli.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
        subRows: Array.from(cli.skus.values()).map((s: any) => ({
          ...s, meses: Array.from(s.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
        }))
      }));
  }, [dadosBrutos, busca, viewMode]);

  // --- STATS GERAIS ---
  const stats = useMemo(() => {
    let t_ia = 0, t_td = 0, t_bu = 0, t_sp = 0, t_final = 0;
    let r_ia = 0, r_td = 0, r_bu = 0, r_sp = 0, r_final = 0;
    let risco_total_brl = 0, oportunidade_ia_total_brl = 0;

    dadosBrutos.forEach(r => {
      t_ia += (r.vol_ia || 0); t_td += (r.vol_topdown || 0); t_bu += (r.vol_bottomup || 0); t_sp += (r.vol_supply || 0); t_final += (r.vol_final || 0);
      r_ia += ((r.vol_ia || 0) * (r.pmv_aplicado || 0)); 
      r_td += ((r.vol_topdown || 0) * (r.pmv_aplicado || 0)); 
      r_bu += ((r.vol_bottomup || 0) * (r.pmv_aplicado || 0)); 
      r_sp += ((r.vol_supply || 0) * (r.pmv_aplicado || 0)); 
      r_final += (r.rec_final || 0);
      
      risco_total_brl += (r.unmet_demand_brl || 0);
      oportunidade_ia_total_brl += (r.impacto_financeiro_ia_brl || 0);
    });

    return { 
      risco_total_brl, 
      oportunidade_ia_total_brl, 
      cards: { 
        vol: { ia: t_ia, td: t_td, bu: t_bu, sp: t_sp, final: t_final }, 
        rec: { ia: r_ia, td: r_td, bu: r_bu, sp: r_sp, final: r_final } 
      } 
    };
  }, [dadosBrutos]);

  // --- FUNÇÕES DA IA (CHAT) ---
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [aiMessages, isAiTyping]);

  const handleSendAiMessage = async () => {
    if (!aiInput.trim()) return;
    const userText = aiInput;
    setAiInput('');
    setAiMessages(prev => [...prev, { role: 'user', content: userText }]);
    setIsAiTyping(true);

    try {
      const dadosAtivos = viewMode === 'categorias' ? dadosAgrupadosCategorias : dadosAgrupadosClientes;
      const topResumidos = dadosAtivos.slice(0, 5).map(item => {
          const recFinal = Array.from(item.meses).reduce((acc:any, m:any) => acc + m.rec_final, 0);
          const risco = Array.from(item.meses).reduce((acc:any, m:any) => acc + m.unmet_brl, 0);
          const inovacao = Array.from(item.meses).reduce((acc:any, m:any) => acc + m.delta_ia_brl, 0);
          
          let sumVarPmv = 0, sumCresc = 0, sumAccIa = 0, cnt = 0;
          const justificativas = new Set<string>();

          Array.from(item.meses).forEach((m: any) => {
              if (m.var_pmv_pct !== undefined) { sumVarPmv += m.var_pmv_pct; sumCresc += m.crescimento_hist_pct; sumAccIa += m.assertividade_ia; cnt++; }
              Array.from(m.justificativas || []).forEach(j => justificativas.add(j as string));
          });

          return {
              nome: item.nome, 
              delta_ia_bu: Array.from(item.meses).reduce((acc:any, m:any) => acc + m.vol_ia - m.vol_bu, 0),
              impacto_ia_brl: inovacao,
              var_pmv: cnt > 0 ? sumVarPmv / cnt : 0,
              crescimento: cnt > 0 ? sumCresc / cnt : 0,
              assertividade_ia: cnt > 0 ? sumAccIa / cnt : 0,
              justificativa: justificativas.size > 0 ? Array.from(justificativas).join('; ') : "",
              rec_final: recFinal,
              unmet_brl: risco
          };
      });

      const contextSummary = {
        visao_ativa: viewMode,
        risco_supply_total_brl: stats.risco_total_brl,
        oportunidade_ia_total_brl: stats.oportunidade_ia_total_brl,
        dados_resumidos: topResumidos
      };

      const res = await axios.post('/api/v1/ai/analise-sop', {
        pergunta: userText,
        contexto_dashboard: contextSummary
      });
      
      setAiMessages(prev => [...prev, { role: 'ai', content: res.data.resposta }]);
    } catch (error) {
      setAiMessages(prev => [...prev, { role: 'ai', content: '❌ *Erro de comunicação com o motor Nexus AI.* Verifique a conexão com o backend ou a chave de API.' }]);
    } finally {
      setIsAiTyping(false);
    }
  };

  // --- LÓGICA DE GRÁFICO EXPANSÍVEL ---
  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const nivel = viewMode === 'clientes' ? 'cliente' : 'categoria';
        const res = await axios.get('/api/v1/dashboard/grafico', { params: { chave_matriz: chave, nivel_hierarquia: nivel } });
        setDadosGraficoCache((p: any) => ({ ...p, [chave]: res.data.dados || [] }));
      } catch (e) {
        console.error("Erro no gráfico", e);
      } finally { setLoadingGrafico(null); }
    }
  };

  // --- CONFIGURAÇÃO DAS COLUNAS DA TABELA ---
  const columns = useMemo<ColumnDef<any>[]>(() => {
    const activeData = viewMode === 'categorias' ? dadosAgrupadosCategorias : dadosAgrupadosClientes;
    if (activeData.length === 0) return [];

    const baseCols: ColumnDef<any>[] = [
      {
        id: 'nome', header: viewMode === 'categorias' ? 'Hierarquia / Portfólio' : 'Top Clientes (Pareto 80%)',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, nome, produto, meses } = row.original;
          
          // Indicadores de Bolinha (Resumo na Seta do SKU)
          const hasInnovation = meses.some((m:any) => (m.vol_ia - m.vol_bu) > 100);
          const hasSupplyCut = meses.some((m:any) => (m.vol_bu - m.vol_sp) > 100);
          const hasPriceErosion = meses.some((m:any) => m.rec_final < (m.vol_final * m.pmv_hist_media * 0.95));

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-1.5 min-w-[380px]">
              
              <div className="relative">
                <button 
                  onClick={(e) => { e.stopPropagation(); row.toggleExpanded(); }} 
                  className={`p-1.5 rounded-lg border transition-all ${row.getIsExpanded() ? 'bg-slate-900 border-slate-900 text-white shadow-lg' : 'hover:bg-slate-100 border-transparent text-slate-500'}`}
                >
                  {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
                {/* Bolinhas de Alerta só aparecem nos produtos */}
                {tipo === 'produto' && (
                  <div className="absolute -top-1 -right-1 flex gap-0.5 pointer-events-none">
                    {hasInnovation && <div className="w-2 h-2 bg-indigo-500 rounded-full border border-white shadow-sm" title="Inovação IA" />}
                    {hasSupplyCut && <div className="w-2 h-2 bg-rose-500 rounded-full border border-white shadow-sm animate-pulse" title="Corte de Fábrica" />}
                    {hasPriceErosion && <div className="w-2 h-2 bg-amber-400 rounded-full border border-white shadow-sm" title="Erosão de PMV" />}
                  </div>
                )}
              </div>
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg border transition-all ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-600 border-indigo-600 text-white shadow-md' : 'hover:bg-slate-100 border-transparent text-slate-400'}`}>
                <Activity className="w-3.5 h-3.5" />
              </button>

              <div className={`w-7 h-7 rounded-full flex items-center justify-center border flex-shrink-0 
                ${tipo === 'categoria' ? 'bg-slate-900 border-slate-700 text-white' : 
                  tipo === 'cliente_pai' ? 'bg-blue-600 border-blue-400 text-white' :
                  'bg-indigo-50 border-indigo-100 text-indigo-600'}`}>
                {tipo === 'categoria' ? <Layers className="w-3.5 h-3.5" /> : 
                 tipo === 'cliente_pai' ? <Users className="w-3.5 h-3.5" /> : <Package className="w-3.5 h-3.5" />}
              </div>

              <div className="flex flex-col overflow-hidden">
                <span className={`text-[13px] truncate ${tipo !== 'produto' ? 'font-black uppercase tracking-tighter' : 'font-bold text-slate-600'}`} title={nome}>{nome}</span>
                {tipo === 'produto' && <span className="text-[10px] text-slate-400 font-black tracking-widest">{produto}</span>}
              </div>
            </div>
          )
        }
      }
    ];

    mesesDisponiveis.forEach((mBanco: any) => {
      const mesStr = mBanco.split('-').reverse().slice(1).join('/');
      baseCols.push({
        id: `mes_${mBanco}`, header: mesStr,
        accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === mBanco)?.vol_final || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === mBanco);
          const valorInteiro = Math.round(Number(dadosMes?.vol_final || 0));
          const receitaExibida = dadosMes?.rec_final || 0;

          const corteSupply = (dadosMes?.vol_bu || 0) - (dadosMes?.vol_sp || 0);
          const acrescimoIA = (dadosMes?.vol_ia || 0) - (dadosMes?.vol_bu || 0);

          return (
            <div className="flex flex-col items-center justify-center min-w-[120px] group relative">
              <div className="flex items-center gap-2">
                 <div className="font-black text-slate-900 text-sm">{formatVolume(valorInteiro)}</div>
                 <div className="flex flex-col gap-0.5">
                    {corteSupply > 10 && <div className="w-1.5 h-1.5 bg-rose-500 rounded-full animate-pulse" title={`Corte: -${formatVolume(corteSupply)} CX\nRisco: ${formatMoeda(dadosMes?.unmet_brl)}`} />}
                    {acrescimoIA > 10 && <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full" title={`Inovação IA: +${formatVolume(acrescimoIA)} CX\nOportunidade: ${formatMoeda(dadosMes?.delta_ia_brl)}`} />}
                 </div>
              </div>
              <div className="text-[10px] font-bold text-emerald-600 mt-0.5">{formatMoeda(receitaExibida)}</div>
              
              {row.tipo === 'produto' && dadosMes?.justificativas?.size > 0 && (
                <div className="absolute -top-1 -right-4 cursor-help" title={Array.from(dadosMes.justificativas).join('\n')}>
                  <Info className="w-3.5 h-3.5 text-rose-400 hover:text-rose-600 transition-colors" />
                </div>
              )}
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [viewMode, dadosAgrupadosCategorias, dadosAgrupadosClientes, chartExpanded, mesesDisponiveis]);

  const table = useReactTable({
    data: viewMode === 'categorias' ? dadosAgrupadosCategorias : dadosAgrupadosClientes,
    columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
  });

  const totaisMesTfoot = useMemo(() => {
    const rows = table.getFilteredRowModel().rows.filter(r => r.depth === 0);
    const totals: Record<string, {vol: number, rec: number}> = {};
    mesesDisponiveis.forEach(m => {
        totals[m] = { vol: 0, rec: 0 };
        rows.forEach(r => {
            const mData = r.original.meses.find((mes: any) => mes.mes_banco === m);
            if (mData) {
                totals[m].vol += (mData.vol_final || 0);
                totals[m].rec += (mData.rec_final || 0);
            }
        });
    });
    return totals;
  }, [table.getFilteredRowModel().rows, mesesDisponiveis]);

  if (isLoading) return (
    <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
      <div className="relative">
        <Loader2 className="w-12 h-12 animate-spin text-indigo-600" />
        <Globe className="w-6 h-6 text-indigo-300 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
      </div>
      <span className="text-slate-400 font-black text-[10px] tracking-[0.2em] uppercase mt-6 animate-pulse">Cruzando Dados Estratégicos...</span>
    </div>
  );

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20 relative">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">
        
        {/* --- HEADER EXECUTIVO --- */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isLocked && (
              <div className={`absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase shadow-sm z-10 ${lockMessage.includes('Publicada') ? "bg-emerald-500" : "bg-amber-500"}`}>
                  {lockMessage.includes('Publicada') ? <ShieldCheck className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />} {lockMessage}
              </div>
          )}
          <div className={isLocked ? "pt-6" : ""}>
            <div className="flex items-center gap-4">
              <div className="p-3 bg-indigo-50 rounded-2xl"><Globe className="w-8 h-8 text-indigo-600" /></div>
              <div>
                <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">Executive S&OP Panel</h1>
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mt-1">Consolidação e Gestão de Riscos da Demanda</p>
              </div>
            </div>
          </div>
          <div className={`flex items-center gap-4 ${isLocked ? "pt-6" : ""}`}>
              <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-50 hover:bg-slate-100 text-slate-700 px-6 py-4 rounded-2xl text-[10px] font-black tracking-widest uppercase border border-slate-200 transition-all shadow-sm"><Download className="w-4 h-4" /> Exportar Oficial</button>
              <button onClick={handleAprovar} disabled={isProcessing || (isLocked && !lockMessage.includes('Publicada'))} className={`flex items-center gap-3 text-white px-10 py-4 rounded-2xl text-xs font-black tracking-widest uppercase shadow-xl transition-all hover:scale-105 active:scale-95 ${lockMessage.includes('Publicada') ? 'bg-emerald-500 shadow-emerald-200' : isLocked ? 'bg-slate-300 shadow-none' : 'bg-slate-900 hover:bg-black shadow-slate-200'}`}>
                  {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : lockMessage.includes('Publicada') ? <ShieldCheck className="w-5 h-5" /> : isLocked ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                  {lockMessage.includes('Publicada') ? 'Meta Congelada' : 'Aprovar Plano Final'}
              </button>
          </div>
        </div>

        {/* --- CARDS DE PERFORMANCE --- */}
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-8">
          {[
            { label: 'Sinal IA', v: stats.cards.vol.ia, r: stats.cards.rec.ia, bV: 0, bR: 0 },
            { label: 'Top-Down', v: stats.cards.vol.td, r: stats.cards.rec.td, bV: stats.cards.vol.ia, bR: stats.cards.rec.ia },
            { label: 'Comercial', v: stats.cards.vol.bu, r: stats.cards.rec.bu, bV: stats.cards.vol.td, bR: stats.cards.rec.td },
            { label: 'Supply', v: stats.cards.vol.sp, r: stats.cards.rec.sp, bV: stats.cards.vol.bu, bR: stats.cards.rec.bu },
            { label: 'Final S&OP', v: stats.cards.vol.final, r: stats.cards.rec.final, bV: stats.cards.vol.sp, bR: stats.cards.rec.sp }
          ].map((card, i) => (
            <div key={i} className={`p-6 rounded-[32px] shadow-sm border border-slate-100 flex flex-col justify-between transition-all hover:shadow-md ${i === 4 ? 'bg-indigo-600 text-white' : 'bg-white'}`}>
               <span className={`text-[10px] font-black uppercase tracking-widest mb-4 flex items-center justify-between ${i === 4 ? 'text-indigo-200' : 'text-slate-400'}`}>
                  {card.label}
                  {i === 4 && <ShieldCheck className="w-4 h-4 text-emerald-400" />}
               </span>
               <div className="space-y-4">
                  <div><p className="text-2xl font-black leading-none tracking-tighter">{formatVolume(card.v)} <span className="text-[10px] opacity-60">CX</span></p>{i > 0 && <VarBadge atual={card.v} anterior={card.bV} />}</div>
                  <div className={`pt-3 border-t ${i === 4 ? 'border-white/10' : 'border-slate-100'}`}><p className="text-[13px] font-black">{formatMoeda(card.r)}</p>{i > 0 && <VarBadge atual={card.r} anterior={card.bR} />}</div>
               </div>
            </div>
          ))}
        </div>

        {/* --- TOGGLE E BUSCA --- */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8 items-center">
            <div className="lg:col-span-2 flex bg-white p-1.5 rounded-[24px] shadow-sm border border-slate-100">
                <button onClick={() => setViewMode('categorias')} className={`flex-1 flex items-center justify-center gap-3 py-4 rounded-[18px] text-[11px] font-black uppercase tracking-widest transition-all ${viewMode === 'categorias' ? 'bg-slate-900 text-white shadow-lg' : 'text-slate-400 hover:text-slate-600'}`}>
                    <Layers className="w-4 h-4" /> Portfólio de Categorias
                </button>
                <button onClick={() => setViewMode('clientes')} className={`flex-1 flex items-center justify-center gap-3 py-4 rounded-[18px] text-[11px] font-black uppercase tracking-widest transition-all ${viewMode === 'clientes' ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-400 hover:text-slate-600'}`}>
                    <Users className="w-4 h-4" /> Clientes Curva A (80%)
                </button>
            </div>
            <div className="relative">
                <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-300" />
                <input type="text" placeholder="Filtrar por SKU, Cliente..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full bg-white border border-slate-100 rounded-[24px] py-5 pl-14 pr-6 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all shadow-sm" />
            </div>
        </div>

        {/* --- TABELA MESTRE --- */}
        <div className="bg-white rounded-[48px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[500px]">
          <div className="overflow-x-auto custom-scrollbar">
            <table className="w-full text-left border-collapse">
              <thead className="bg-slate-50/50 border-b-2 border-slate-100">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (
                      <th key={h.id} className="px-10 py-8 text-[10px] font-black text-slate-400 uppercase tracking-widest cursor-pointer hover:bg-slate-100/50 transition-colors" onClick={h.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-3">{flexRender(h.column.columnDef.header, h.getContext())} {h.column.getIsSorted() && (h.column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3 text-indigo-500"/> : <ArrowDown className="w-3 h-3 text-indigo-500"/>)}</div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-all ${row.depth === 0 ? 'bg-white font-bold' : 'bg-slate-50/30 hover:bg-slate-100/50'}`}>
                      {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-10 py-5">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                    </tr>
                    
                    {/* DOSSIÊ DO SKU EXPANDIDO */}
                    {row.getIsExpanded() && row.original.tipo === 'produto' && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="p-0">
                          <SKUPerformanceDossier rowData={row.original} />
                        </td>
                      </tr>
                    )}

                    {/* GRÁFICO S&OE EXPANSÍVEL */}
                    {chartExpanded === row.original.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-100/30 p-8 border-b border-slate-200">
                           <div className="bg-white rounded-[40px] p-10 border border-slate-200 shadow-2xl h-[450px] relative">
                              <div className="flex justify-between items-center mb-10">
                                <div>
                                   <div className="flex items-center gap-3 mb-1">
                                      <div className="w-2 h-2 bg-indigo-500 rounded-full animate-ping" />
                                      <h3 className="text-lg font-black text-slate-800 uppercase tracking-tighter flex items-center gap-3">Análise de Horizonte S&OP: {row.original.nome}</h3>
                                   </div>
                                   <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest ml-5">Histórico Real vs Proposta Mês Passado vs Projeções</p>
                                </div>
                                <button onClick={() => setChartExpanded(null)} className="p-3 bg-slate-50 hover:bg-rose-50 text-slate-400 hover:text-rose-500 rounded-full transition-all shadow-sm"><AlertCircle className="w-6 h-6" /></button>
                              </div>
                              <div className="w-full h-[280px]">
                                 {loadingGrafico === row.original.chave_matriz ? <div className="h-full flex flex-col items-center justify-center text-slate-400 gap-4"><Loader2 className="animate-spin w-8 h-8" /><span className="font-black text-[10px] uppercase tracking-widest">Reconstruindo Horizonte...</span></div> : (
                                   <ResponsiveContainer width="100%" height="100%">
                                      <LineChart data={dadosGraficoCache[row.original.chave_matriz] || []} margin={{ top: 10, right: 30, left: 10, bottom: 0 }}>
                                         <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                         <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} dy={10} />
                                         <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                                         <Tooltip contentStyle={{backgroundColor: '#fff', border: 'none', borderRadius: '20px', boxShadow: '0 20px 40px rgba(0,0,0,0.1)', fontWeight: 900}} formatter={(val: any) => formatVolume(val)} itemSorter={tooltipSorterRow} />
                                         <Legend iconType="circle" wrapperStyle={{paddingTop: '30px', fontSize: '11px', fontWeight: 900}} />
                                         <Line type="monotone" dataKey="CicloAnterior" name="Proposta Mês Passado" stroke="#a855f7" strokeWidth={2} strokeDasharray="6 4" dot={false} connectNulls />
                                         <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#0f172a" strokeWidth={4} dot={{r: 4, fill: '#0f172a'}} connectNulls />
                                         <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="10 6" dot={false} connectNulls />
                                         <Line type="monotone" dataKey="Comercial" name="Proposta Comercial" stroke="#6366f1" strokeWidth={3} dot={{r: 4}} connectNulls />
                                         <Line type="monotone" dataKey="Supply" name="Restrição Supply" stroke="#f59e0b" strokeWidth={3} dot={{r: 4}} connectNulls />
                                         <Line type="monotone" dataKey="Final" name="Global S&OP" stroke="#10b981" strokeWidth={5} dot={{r: 6, fill: '#10b981', strokeWidth: 2, stroke: '#fff'}} connectNulls />
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
              
              <tfoot className="bg-slate-900 text-white sticky bottom-0 z-20 shadow-[0_-10px_30px_rgba(0,0,0,0.3)]">
                <tr>
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-10 py-6 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-[0.2em] text-[9px] text-indigo-400 mb-1">Cálculo Visível na Tela</span>
                          <span className="font-bold text-sm text-white uppercase tracking-tighter">Total {viewMode}</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      const volM = totaisMesTfoot[mesBanco]?.vol || 0;
                      const recM = totaisMesTfoot[mesBanco]?.rec || 0;
                      return (
                        <td key={header.id} className="px-10 py-6 border-l border-white/5">
                          <div className="flex flex-col items-center justify-center min-w-[120px]">
                            <span className="font-black text-white text-base tracking-tighter">{formatVolume(volM)} <span className="text-[10px] text-slate-500 font-normal">CX</span></span>
                            <span className="font-black text-emerald-400 text-[12px] tracking-tight">{formatMoeda(recM)}</span>
                          </div>
                        </td>
                      );
                    }
                    return <td key={header.id} className="px-10 py-6"></td>;
                  })}
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </div>

      {/* ========================================================= */}
      {/* NEXUS AI COPILOT (FLOATING CHAT)                          */}
      {/* ========================================================= */}
      
      <button 
        onClick={() => setIsAiOpen(true)}
        className={`fixed bottom-8 right-8 p-4 bg-slate-900 text-white rounded-full shadow-2xl hover:scale-110 hover:bg-black transition-all z-40 group ${isAiOpen ? 'hidden' : 'flex'}`}
      >
        <Sparkles className="w-6 h-6 text-indigo-400 group-hover:text-indigo-300" />
      </button>

      {isAiOpen && (
        <div className="fixed top-0 right-0 h-full w-[450px] bg-white shadow-2xl z-50 flex flex-col animate-in slide-in-from-right duration-300 border-l border-slate-200">
          
          <div className="p-6 bg-slate-900 text-white flex justify-between items-center">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-indigo-500/20 rounded-xl"><Bot className="w-6 h-6 text-indigo-400" /></div>
              <div>
                <h2 className="font-black text-lg tracking-tighter flex items-center gap-2">Nexus AI <span className="text-[9px] bg-indigo-600 px-2 py-0.5 rounded-full uppercase">Analyst</span></h2>
                <p className="text-[10px] text-indigo-300 uppercase tracking-widest font-bold">Copiloto Executivo S&OP</p>
              </div>
            </div>
            <button onClick={() => setIsAiOpen(false)} className="p-2 hover:bg-white/10 rounded-full transition-colors"><X className="w-5 h-5" /></button>
          </div>

          <div className="flex-1 overflow-y-auto p-6 bg-slate-50 flex flex-col gap-4 custom-scrollbar">
            {aiMessages.map((msg, idx) => (
              <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[85%] p-4 rounded-2xl text-sm leading-relaxed ${msg.role === 'user' ? 'bg-indigo-600 text-white rounded-tr-sm' : 'bg-white border border-slate-200 text-slate-700 shadow-sm rounded-tl-sm'}`}>
                  <span dangerouslySetInnerHTML={{ __html: msg.content.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-slate-900">$1</strong>') }} />
                </div>
              </div>
            ))}
            {isAiTyping && (
              <div className="flex justify-start">
                <div className="bg-white border border-slate-200 p-4 rounded-2xl rounded-tl-sm shadow-sm flex items-center gap-2">
                  <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className="p-6 bg-white border-t border-slate-100 flex flex-col gap-3">
            <div className="flex gap-2 overflow-x-auto pb-2 custom-scrollbar">
              {sugestoesIA.map(sugestao => (
                <button 
                  key={sugestao} onClick={() => setAiInput(sugestao)}
                  className="whitespace-nowrap px-3 py-1.5 bg-slate-100 hover:bg-indigo-50 text-slate-600 hover:text-indigo-600 text-[10px] font-black uppercase tracking-widest rounded-full transition-colors border border-slate-200"
                >
                  {sugestao}
                </button>
              ))}
            </div>
            <div className="relative flex items-center">
              <input 
                type="text" value={aiInput} onChange={(e) => setAiInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSendAiMessage()}
                placeholder="Ex: Analise o risco de ruptura..."
                className="w-full bg-slate-50 border border-slate-200 rounded-[20px] py-4 pl-4 pr-12 text-sm font-bold text-slate-800 outline-none focus:border-indigo-500 focus:bg-white transition-colors"
              />
              <button 
                onClick={handleSendAiMessage} disabled={!aiInput.trim() || isAiTyping}
                className="absolute right-2 p-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white rounded-xl transition-colors"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>

        </div>
      )}

    </div>
  );
}