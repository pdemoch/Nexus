import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, 
  getSortedRowModel, SortingState, ColumnDef 
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Layers, Lock, Download, AlertTriangle, ShieldCheck, Check, Globe, Package, 
  Users, Search, Filter, BarChart3, TrendingUp, TrendingDown, Activity, Hash, Target,
  ArrowRightLeft, AlertCircle
} from 'lucide-react';
import axios from 'axios';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

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

// --- ORDENADORES DE TOOLTIP ---
const tooltipSorterGlobal = (item: any) => {
  const order: Record<string, number> = { 'Sinal IA': 1, 'Top-Down': 2, 'Comercial': 3, 'Supply': 4, 'Final S&OP': 5 };
  return order[item.name] || 99;
};

const tooltipSorterRow = (item: any) => {
  const order: Record<string, number> = { 'Histórico Real': 1, 'Proposta Mês Passado': 2, 'Sinal IA': 3, 'Proposta Comercial': 4, 'Restrição Supply': 5, 'Global S&OP': 6 };
  return order[item.name] || 99;
};

export default function GlobalDashboard() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [lockMessage, setLockMessage] = useState('');
  
  const [viewMode, setViewMode] = useState<'categorias' | 'clientes'>('categorias');
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [busca, setBusca] = useState('');

  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);
  const [mesPareto, setMesPareto] = useState<string>('');

  // --- CARGA DE DADOS ---
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/dashboard/global');
      setDadosBrutos(res.data.dados || []);
      setIsLocked(res.data.is_locked);
      setLockMessage(res.data.lock_message);
      setCelulasEditadas({});
      setExpanded({});
      setChartExpanded(null);
    } catch (e) { console.error(e); } finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const mesesDisponiveis = useMemo(() => {
    const meses = new Set(dadosBrutos.map(d => d.mes_projetado));
    return Array.from(meses).sort() as string[];
  }, [dadosBrutos]);

  useEffect(() => {
    if (mesesDisponiveis.length > 0 && !mesPareto) {
      setMesPareto(mesesDisponiveis[0]);
    }
  }, [mesesDisponiveis, mesPareto]);

  const handleExportExcel = async () => {
    try {
        const response = await axios.get('/api/v1/dashboard/export', { responseType: 'blob' });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url; link.setAttribute('download', 'Consenso_Global_Nexus.xlsx');
        document.body.appendChild(link); link.click(); link.remove();
    } catch (e) { alert("Erro ao exportar."); }
  };

  const handleAprovar = async () => {
    if (isLocked) return; 
    if (!window.confirm("Deseja publicar e congelar o plano S&OP Global para toda a companhia?")) return;
    setIsProcessing(true);
    const ajustes = Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
      Object.entries(meses).map(([mes, val]: any) => ({
        nivel: chave.split('|').length === 3 ? 'cliente' : 'produto',
        chave: chave, 
        mes_projetado: mes, 
        novo_volume: val.novo_volume === '' ? 0 : Number(val.novo_volume || 0)
      }))
    );
    try {
      await axios.post('/api/v1/dashboard/aprovar', { ajustes });
      alert("✅ S&OP Global Publicado com Sucesso!");
      fetchData(); 
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao aprovar."); } 
    finally { setIsProcessing(false); }
  };

  // --- MOTOR DE AGRUPAMENTO (VIEW 1: CATEGORIAS) ---
  const dadosAgrupadosCategorias = useMemo(() => {
    if (!dadosBrutos.length) return [];
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
        if (!node.meses.has(m)) node.meses.set(m, { mes_banco: m, vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0 });
        const target = node.meses.get(m);
        target.vol_ia += (r.vol_ia || 0); target.vol_td += (r.vol_topdown || 0); target.vol_bu += (r.vol_bottomup || 0); 
        target.vol_sp += (r.vol_supply || 0); target.vol_final += (r.vol_final || 0);
        target.rec_ia += (r.rec_ia || 0); target.rec_td += (r.rec_td || 0); target.rec_bu += (r.rec_bu || 0); 
        target.rec_sp += (r.rec_supply || 0); target.rec_final += (r.rec_final || 0);
      });
    });

    return Array.from(tree.values()).map(cat => ({
      ...cat, meses: Array.from(cat.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
      subRows: Array.from(cat.skus.values()).map((s: any) => ({
        ...s, meses: Array.from(s.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
      }))
    }));
  }, [dadosBrutos, busca]);

  // --- MOTOR DE AGRUPAMENTO (VIEW 2: TOP CLIENTES PARETO) ---
  const dadosAgrupadosClientes = useMemo(() => {
    if (!dadosBrutos.length) return [];
    
    // 1. Identificar Top 80% (Pareto)
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
      if (acumulado >= totalGlobal * 0.8 && topClientesSet.size > 0) break;
    }

    // 2. Construir Hierarquia (Cliente -> SKU)
    const tree = new Map();
    const term = busca.toLowerCase();
    
    dadosBrutos
      .filter(d => topClientesSet.has(d.razaosocial))
      .filter(d => (d.sku + ' ' + d.descricao + ' ' + d.razaosocial).toLowerCase().includes(term))
      .forEach(r => {
        if (!tree.has(r.razaosocial)) {
          tree.set(r.razaosocial, { id: r.razaosocial, chave_matriz: r.razaosocial, nome: r.razaosocial, tipo: 'cliente_pai', meses: new Map(), skus: new Map() });
        }
        const cli = tree.get(r.razaosocial);
        if (!cli.skus.has(r.sku)) {
          cli.skus.set(r.sku, { id: `${r.razaosocial}|${r.sku}`, chave_matriz: `${r.razaosocial}|${r.sku}`, nome: r.descricao, produto: r.sku, tipo: 'produto', meses: new Map() });
        }
        const skuNode = cli.skus.get(r.sku);
        const m = r.mes_projetado;

        [cli, skuNode].forEach(node => {
          if (!node.meses.has(m)) node.meses.set(m, { mes_banco: m, vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0 });
          const target = node.meses.get(m);
          target.vol_ia += (r.vol_ia || 0); target.vol_td += (r.vol_topdown || 0); target.vol_bu += (r.vol_bottomup || 0); 
          target.vol_sp += (r.vol_supply || 0); target.vol_final += (r.vol_final || 0);
          target.rec_ia += (r.rec_ia || 0); target.rec_td += (r.rec_td || 0); target.rec_bu += (r.rec_bu || 0); 
          target.rec_sp += (r.rec_supply || 0); target.rec_final += (r.rec_final || 0);
        });
      });

    return Array.from(tree.values())
      .sort((a, b) => {
        const volA = Array.from(a.meses.values() as any).reduce((acc: any, curr: any) => acc + curr.rec_final, 0);
        const volB = Array.from(b.meses.values() as any).reduce((acc: any, curr: any) => acc + curr.rec_final, 0);
        return (volB as number) - (volA as number);
      })
      .map(cli => ({
        ...cli, meses: Array.from(cli.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
        subRows: Array.from(cli.skus.values()).map((s: any) => ({
          ...s, meses: Array.from(s.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
        }))
      }));
  }, [dadosBrutos, busca]);

  // --- STATS GERAIS E GRÁFICOS ---
  const stats = useMemo(() => {
    let t_ia = 0, t_td = 0, t_bu = 0, t_sp = 0, t_final = 0;
    let r_ia = 0, r_td = 0, r_bu = 0, r_sp = 0, r_final = 0;
    const porMes: Record<string, any> = {};

    dadosBrutos.forEach(r => {
      if (!porMes[r.mes_projetado]) porMes[r.mes_projetado] = { vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0 };
      const m = porMes[r.mes_projetado];
      m.vol_ia += (r.vol_ia || 0); m.vol_td += (r.vol_topdown || 0); m.vol_bu += (r.vol_bottomup || 0); m.vol_sp += (r.vol_supply || 0); m.vol_final += (r.vol_final || 0);
      m.rec_ia += (r.rec_ia || 0); m.rec_td += (r.rec_td || 0); m.rec_bu += (r.rec_bu || 0); m.rec_sp += (r.rec_supply || 0); m.rec_final += (r.rec_final || 0);
      t_ia += (r.vol_ia || 0); t_td += (r.vol_topdown || 0); t_bu += (r.vol_bottomup || 0); t_sp += (r.vol_supply || 0); t_final += (r.vol_final || 0);
      r_ia += (r.rec_ia || 0); r_td += (r.rec_td || 0); r_bu += (r.rec_bu || 0); r_sp += (r.rec_supply || 0); r_final += (r.rec_final || 0);
    });

    const chartData = Object.keys(porMes).sort().map(mes => ({
      name: mes.split('-').reverse().slice(1).join('/'),
      IA: porMes[mes].vol_ia, TD: porMes[mes].vol_td, BU: porMes[mes].vol_bu, SP: porMes[mes].vol_sp, Final: porMes[mes].vol_final,
      RIA: porMes[mes].rec_ia, RTD: porMes[mes].rec_td, RBU: porMes[mes].rec_bu, RSP: porMes[mes].rec_sp, RFinal: porMes[mes].rec_final
    }));

    return { porMes, chartData, cards: { vol: { ia: t_ia, td: t_td, bu: t_bu, sp: t_sp, final: t_final }, rec: { ia: r_ia, td: r_td, bu: r_bu, sp: r_sp, final: r_final } } };
  }, [dadosBrutos]);

  const paretoData = useMemo(() => {
    if (!mesPareto || dadosBrutos.length === 0) return [];
    const dadosMes = dadosBrutos.filter(d => d.mes_projetado === mesPareto);
    const agrupado = new Map();
    let totalFaturamentoMes = 0;
    dadosMes.forEach(d => {
      const cliente = d.razaosocial || 'N/A';
      if (!agrupado.has(cliente)) agrupado.set(cliente, { razaosocial: cliente, receita: 0, volume: 0 });
      const r = agrupado.get(cliente);
      r.receita += (d.rec_final || 0); r.volume += (d.vol_final || 0);
      totalFaturamentoMes += (d.rec_final || 0);
    });
    const sorted = Array.from(agrupado.values()).sort((a, b) => b.receita - a.receita);
    const limit80 = totalFaturamentoMes * 0.8;
    let acumulado = 0;
    const result: any[] = [];
    for (const c of sorted) {
      if (acumulado >= limit80 && result.length > 0) break; 
      result.push({ name: c.razaosocial.length > 25 ? c.razaosocial.substring(0, 25) + '...' : c.razaosocial, Receita: c.receita, Volume: c.volume });
      acumulado += c.receita;
    }
    return result;
  }, [dadosBrutos, mesPareto]);

  // --- LOGICA DE GRÁFICO EXPANSÍVEL ---
  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/dashboard/grafico', { params: { chave_matriz: chave } });
        const historico = res.data.dados || [];
        const projectionData = row.original.meses.map((m: any) => ({
          name: m.mes_banco.split('-').reverse().slice(1).join('/'),
          data_iso: m.mes_banco,
          IA: m.vol_ia, Comercial: m.vol_bu, Supply: m.vol_sp, Final: m.vol_final, Realizado: null
        }));
        const merged: any[] = [];
        historico.forEach((h: any) => {
           const isProjection = projectionData.find((p: any) => p.data_iso === h.data_iso);
           if (!isProjection) merged.push({ ...h });
        });
        const finalTimeline = [...merged, ...projectionData].sort((a: any, b: any) => a.data_iso.localeCompare(b.data_iso));
        setDadosGraficoCache((p: any) => ({ ...p, [chave]: finalTimeline }));
      } catch (e) {
        setDadosGraficoCache((p: any) => ({ ...p, [chave]: row.original.meses.map((m: any) => ({ name: m.mes_banco, IA: m.vol_ia, Final: m.vol_final })) }));
      } finally { setLoadingGrafico(null); }
    }
  };

  // --- CONFIGURAÇÃO DAS COLUNAS (TANSTACK TABLE) ---
  const columns = useMemo<ColumnDef<any>[]>(() => {
    const activeData = viewMode === 'categorias' ? dadosAgrupadosCategorias : dadosAgrupadosClientes;
    if (activeData.length === 0) return [];

    const baseCols: ColumnDef<any>[] = [
      {
        id: 'nome', header: viewMode === 'categorias' ? 'Hierarquia Categorias / SKUs' : 'Top Clientes / SKUs Comprados',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, nome, produto } = row.original;
          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-1.5 min-w-[380px]">
              <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-100 rounded text-slate-500">
                {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg border transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 border-indigo-200 text-indigo-600' : 'hover:bg-slate-100 border-transparent text-slate-400'}`}>
                <Activity className="w-3.5 h-3.5" />
              </button>

              <div className={`w-7 h-7 rounded-full flex items-center justify-center border flex-shrink-0 
                ${tipo === 'categoria' ? 'bg-slate-900 border-slate-700 text-white' : 
                  tipo === 'cliente_pai' ? 'bg-blue-600 border-blue-400 text-white' :
                  tipo === 'produto' ? 'bg-indigo-50 border-indigo-100 text-indigo-600' : 'bg-slate-50 text-slate-400'}`}>
                {tipo === 'categoria' ? <Layers className="w-3.5 h-3.5" /> : 
                 tipo === 'cliente_pai' ? <Users className="w-3.5 h-3.5" /> : <Hash className="w-3.5 h-3.5" />}
              </div>

              <div className="flex flex-col">
                <span className={`text-[13px] ${tipo !== 'produto' ? 'font-black uppercase tracking-tighter' : 'font-bold text-slate-600'}`}>{nome}</span>
                {tipo === 'produto' && <span className="text-[10px] text-slate-400 font-bold">{produto}</span>}
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

          // Lógica de Variação (Corte/Acréscimo)
          const corte = (dadosMes?.vol_bu || 0) - (dadosMes?.vol_sp || 0);
          const acrescimo = (dadosMes?.vol_td || 0) - (dadosMes?.vol_ia || 0);

          return (
            <div className="flex flex-col items-center justify-center min-w-[120px] group relative">
              <div className="flex items-center gap-2">
                 <div className="font-black text-slate-900 text-sm">{formatVolume(valorInteiro)}</div>
                 <div className="flex flex-col gap-0.5">
                    {corte > 10 && <div className="w-1.5 h-1.5 bg-rose-500 rounded-full" title={`Corte de Supply: ${formatVolume(corte)} CX`} />}
                    {acrescimo > 10 && <div className="w-1.5 h-1.5 bg-blue-500 rounded-full" title={`Ajuste Estratégico: +${formatVolume(acrescimo)} CX`} />}
                 </div>
              </div>
              <div className="text-[10px] font-bold text-emerald-600 mt-0.5">{formatMoeda(receitaExibida)}</div>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [viewMode, dadosAgrupadosCategorias, dadosAgrupadosClientes, chartExpanded, isLocked, mesesDisponiveis]);

  const table = useReactTable({
    data: viewMode === 'categorias' ? dadosAgrupadosCategorias : dadosAgrupadosClientes,
    columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
  });

  if (isLoading) return (
    <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
      <Loader2 className="w-10 h-10 animate-spin text-indigo-500 mb-4" />
      <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Gerando Visão Executiva S&OP...</span>
    </div>
  );

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10 relative">
        
        {/* --- HEADER --- */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isLocked && (
              <div className={`absolute top-0 left-0 w-full text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10 ${lockMessage.includes('Publicada') ? "bg-emerald-500" : "bg-amber-500"}`}>
                  {lockMessage.includes('Publicada') ? <ShieldCheck className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />} {lockMessage}
              </div>
          )}
          <div className={isLocked ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter uppercase"><Globe className="w-8 h-8 text-indigo-600" /> S&OP Executive Dashboard</h1>
            <p className="text-xs font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">Consolidação e Publicação da Meta Oficial</p>
          </div>
          <div className={`flex items-center gap-4 ${isLocked ? "pt-4" : ""}`}>
              <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase border border-slate-200 transition-all"><Download className="w-4 h-4" /> Excel Oficial</button>
              <button onClick={handleAprovar} disabled={isProcessing || (isLocked && !lockMessage.includes('Publicada'))} className={`flex items-center gap-2 text-white px-8 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg transition-all ${lockMessage.includes('Publicada') ? 'bg-emerald-500 shadow-none' : isLocked ? 'bg-slate-300 shadow-none' : 'bg-slate-900 hover:bg-black'}`}>
                  {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : lockMessage.includes('Publicada') ? <ShieldCheck className="w-5 h-5" /> : isLocked ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                  {lockMessage.includes('Publicada') ? 'Plano Publicado' : 'Publicar Plano Oficial'}
              </button>
          </div>
        </div>

        {/* --- CARDS KPI --- */}
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
          {[
            { label: 'Sinal IA', v: stats.cards.vol.ia, r: stats.cards.rec.ia, bV: 0, bR: 0 },
            { label: 'Top-Down', v: stats.cards.vol.td, r: stats.cards.rec.td, bV: stats.cards.vol.ia, bR: stats.cards.rec.ia },
            { label: 'Comercial', v: stats.cards.vol.bu, r: stats.cards.rec.bu, bV: stats.cards.vol.td, bR: stats.cards.rec.td },
            { label: 'Supply', v: stats.cards.vol.sp, r: stats.cards.rec.sp, bV: stats.cards.vol.bu, bR: stats.cards.rec.bu },
            { label: 'Final S&OP', v: stats.cards.vol.final, r: stats.cards.rec.final, bV: stats.cards.vol.sp, bR: stats.cards.rec.sp }
          ].map((card, i) => (
            <div key={i} className={`p-5 rounded-[24px] shadow-sm border border-slate-100 flex flex-col justify-between transition-all hover:scale-[1.02] ${i === 4 ? 'bg-indigo-600 text-white shadow-indigo-200' : 'bg-white'}`}>
               <span className={`text-[10px] font-black uppercase tracking-widest mb-3 ${i === 4 ? 'text-indigo-200' : 'text-slate-400'}`}>{card.label}</span>
               <div className="space-y-4">
                  <div><p className="text-2xl font-black leading-none">{formatVolume(card.v)} <span className="text-[10px] opacity-60">CX</span></p>{i > 0 && <VarBadge atual={card.v} anterior={card.bV} />}</div>
                  <div className={`pt-2 border-t ${i === 4 ? 'border-white/10' : 'border-slate-100'}`}><p className="text-[13px] font-black">{formatMoeda(card.r)}</p>{i > 0 && <VarBadge atual={card.r} anterior={card.bR} />}</div>
               </div>
            </div>
          ))}
        </div>

        {/* --- GRÁFICOS GLOBAIS --- */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          {['Volume Global (CX)', 'Receita Global (R$)'].map((title, idx) => (
            <div key={idx} className="bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 flex flex-col min-h-[380px]">
              <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2"><BarChart3 className="w-4 h-4 text-indigo-500" /> {title}</h3>
              <div className="flex-1 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={stats.chartData}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                    <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => idx === 0 ? `${(v/1000).toFixed(0)}k` : `R$${(v/1000000).toFixed(1)}M`} />
                    <Tooltip contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)', fontWeight: 900}} formatter={(val: any) => idx === 0 ? formatVolume(val) : formatMoeda(val)} itemSorter={tooltipSorterGlobal} />
                    <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: 900}} />
                    <Bar dataKey={idx === 0 ? "IA" : "RIA"} name="Sinal IA" fill="#e2e8f0" radius={[4, 4, 0, 0]} />
                    <Bar dataKey={idx === 0 ? "TD" : "RTD"} name="Top-Down" fill="#94a3b8" radius={[4, 4, 0, 0]} />
                    <Bar dataKey={idx === 0 ? "BU" : "RBU"} name="Comercial" fill="#818cf8" radius={[4, 4, 0, 0]} />
                    <Bar dataKey={idx === 0 ? "SP" : "RSP"} name="Supply" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                    <Bar dataKey={idx === 0 ? "Final" : "RFinal"} name="Final S&OP" fill="#4f46e5" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ))}
        </div>

        {/* --- PARETO & SEARCH --- */}
        <div className="flex flex-col lg:flex-row gap-6 mb-6">
           <div className="lg:w-2/3 bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 flex flex-col relative">
              <div className="flex justify-between items-center mb-6">
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest flex items-center gap-2"><Target className="w-4 h-4 text-emerald-500" /> Pareto S&OP (Foco 80% Receita)</h3>
                <select value={mesPareto} onChange={e => setMesPareto(e.target.value)} className="bg-slate-50 border border-slate-200 text-xs font-black p-2 rounded-xl outline-none text-slate-700 cursor-pointer uppercase tracking-widest">
                  {mesesDisponiveis.map(m => (<option key={m} value={m}>{m.split('-').reverse().slice(1).join('/')}</option>))}
                </select>
              </div>
              <div className="w-full h-[300px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart layout="vertical" data={paretoData} margin={{ left: 40, right: 30 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#f1f5f9" />
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="name" tick={{fontSize: 9, fontWeight: 900, fill: '#64748b'}} width={120} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{borderRadius: '16px', border: 'none', fontWeight: 900}} formatter={(val: any) => formatMoeda(val)} />
                    <Bar dataKey="Receita" name="Receita Projetada" fill="#10b981" radius={[0, 4, 4, 0]} barSize={15} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
           </div>
           
           <div className="lg:w-1/3 flex flex-col gap-4">
              <div className="bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 flex flex-col justify-center h-full">
                 <h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-4">Filtragem Inteligente</h3>
                 <div className="relative">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-300" />
                    <input type="text" placeholder="SKU, Categoria ou Cliente..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full bg-slate-50 border-2 border-slate-100 rounded-2xl py-4 pl-12 pr-4 text-sm font-bold text-slate-800 outline-none focus:border-indigo-500 transition-all" />
                 </div>
              </div>
           </div>
        </div>

        {/* --- TABELA MESTRE COM TOGGLE --- */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative">
          
          {/* BOTÕES DE TOGGLE DE VISÃO */}
          <div className="flex bg-slate-50 p-2 border-b border-slate-100">
             <button onClick={() => setViewMode('categorias')} className={`flex-1 flex items-center justify-center gap-2 py-3 rounded-2xl text-[10px] font-black uppercase tracking-widest transition-all ${viewMode === 'categorias' ? 'bg-white text-indigo-600 shadow-sm border border-slate-200' : 'text-slate-400 hover:text-slate-600'}`}>
                <Layers className="w-4 h-4" /> Visão por Categorias
             </button>
             <button onClick={() => setViewMode('clientes')} className={`flex-1 flex items-center justify-center gap-2 py-3 rounded-2xl text-[10px] font-black uppercase tracking-widest transition-all ${viewMode === 'clientes' ? 'bg-white text-indigo-600 shadow-sm border border-slate-200' : 'text-slate-400 hover:text-slate-600'}`}>
                <Users className="w-4 h-4" /> Visão Top Clientes (Pareto 80%)
             </button>
          </div>

          <div className="overflow-x-auto custom-scrollbar">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (
                      <th key={h.id} className="px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest cursor-pointer hover:bg-slate-50" onClick={h.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">{flexRender(h.column.columnDef.header, h.getContext())} {h.column.getIsSorted() && (h.column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3"/> : <ArrowDown className="w-3 h-3"/>)}</div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-colors ${row.depth === 0 ? 'bg-slate-50/30' : 'bg-white hover:bg-slate-50'}`}>
                      {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-8 py-4">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                    </tr>
                    
                    {/* GRÁFICO EXPANSÍVEL S&OE */}
                    {chartExpanded === row.original.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-100/50 p-6 border-b border-slate-200">
                           <div className="bg-white rounded-[32px] p-8 border border-slate-200 shadow-xl h-[420px] animate-in slide-in-from-top-4 duration-500">
                              <div className="flex justify-between items-center mb-8">
                                <div>
                                   <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest flex items-center gap-2"><Activity className="w-5 h-5 text-indigo-500" /> Diagnóstico S&OE: {row.original.nome}</h3>
                                   <p className="text-[10px] font-bold text-slate-400 mt-1">Comparação entre Histórico, Proposta Anterior e Ciclo Atual</p>
                                </div>
                                <button onClick={() => setChartExpanded(null)} className="p-2 bg-slate-100 hover:bg-rose-100 text-slate-400 hover:text-rose-600 rounded-full transition-colors"><AlertCircle className="w-5 h-5" /></button>
                              </div>
                              <div className="w-full h-[280px]">
                                 {loadingGrafico === row.original.chave_matriz ? <div className="h-full flex items-center justify-center text-slate-600 font-black animate-pulse"><Loader2 className="animate-spin mr-2" /> Cruzando dados históricos...</div> : (
                                   <ResponsiveContainer width="100%" height="100%">
                                      <LineChart data={dadosGraficoCache[row.original.chave_matriz] || []} margin={{ top: 10, right: 30, left: 10, bottom: 0 }}>
                                         <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                         <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                                         <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                                         <Tooltip contentStyle={{backgroundColor: '#fff', border: 'none', borderRadius: '16px', boxShadow: '0 10px 30px rgba(0,0,0,0.1)', fontWeight: 900}} formatter={(val: any) => formatVolume(val)} itemSorter={tooltipSorterRow} />
                                         <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: 900}} />
                                         
                                         <Line type="monotone" dataKey="CicloAnterior" name="Proposta Mês Passado" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls />
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
              
              <tfoot className="bg-slate-900 text-white sticky bottom-0">
                <tr>
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total do Horizonte M2-M4</span>
                          <span className="font-bold text-sm text-white">CONSOLIDAÇÃO S&OP</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const mesBanco = header.id.replace('mes_', '');
                      const volM = stats.porMes[mesBanco]?.vol_final || 0;
                      const recM = stats.porMes[mesBanco]?.rec_final || 0;
                      return (
                        <td key={header.id} className="px-8 py-5 border-l border-white/5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white">{formatVolume(volM)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(recM)}</span>
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
        </div>
      </div>
    </div>
  );
}