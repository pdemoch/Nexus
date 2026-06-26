import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { 
  ShoppingCart, TrendingUp, TrendingDown, Factory, Target, Activity, 
  Search, RefreshCw, Package, ChevronDown, ChevronRight, AlertTriangle, DollarSign, Users, Download
} from 'lucide-react';
import { ResponsiveContainer, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, LineChart, Line, ReferenceLine, ComposedChart, Bar, Legend } from 'recharts';

// --- HELPERS DE FORMATAÇÃO ---
const formatFin = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVol = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const formatPct = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(val || 0);

// --- COMPONENTE DROPDOWN MESES ---
const ExcelTreeDropdown = ({ titulo, options, selected, onChange }: any) => {
  const [open, setOpen] = useState(false);
  const groupedOptions = useMemo(() => {
    return options.reduce((acc: any, mes: string) => {
      const ano = mes.split('/')[1];
      if (!acc[ano]) acc[ano] = [];
      acc[ano].push(mes);
      return acc;
    }, {});
  }, [options]);

  const toggleMonth = (val: string) => selected.includes(val) && selected.length > 1 ? onChange(selected.filter((v: string) => v !== val)) : !selected.includes(val) && onChange([...selected, val]);
  const toggleYear = (ano: string, mesesDoAno: string[]) => {
    const todosSelecionados = mesesDoAno.every(m => selected.includes(m));
    if (todosSelecionados) {
      if (selected.length > mesesDoAno.length) onChange(selected.filter((m: string) => !mesesDoAno.includes(m)));
    } else {
      const novos = new Set([...selected, ...mesesDoAno]);
      onChange(Array.from(novos));
    }
  };

  return (
    <div className="relative" onMouseLeave={() => setOpen(false)}>
      <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">{titulo}</label>
      <div onClick={() => setOpen(!open)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 flex justify-between items-center cursor-pointer shadow-inner">
         <span className="truncate pr-2 font-bold text-indigo-300">{selected.length} Meses Selecionados</span>
         <span className="text-[10px] text-slate-500">▼</span>
      </div>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-full min-w-[220px] bg-slate-800 border border-slate-700 rounded-lg shadow-2xl z-50 p-2 max-h-72 overflow-y-auto">
           {Object.entries(groupedOptions).sort(([a], [b]) => Number(b) - Number(a)).map(([ano, meses]: any) => {
             const todosSelecionados = meses.every((m: string) => selected.includes(m));
             return (
               <div key={ano} className="mb-2">
                 <div className="flex items-center gap-2 p-1.5 bg-slate-900/50 rounded cursor-pointer hover:bg-slate-700 transition-colors" onClick={() => toggleYear(ano, meses)}>
                   <input type="checkbox" readOnly checked={todosSelecionados} className="accent-indigo-500 w-4 h-4" />
                   <span className="font-black text-sm text-indigo-400">Ano {ano}</span>
                 </div>
                 <div className="pl-6 pt-1 flex flex-col gap-1">
                   {meses.map((m: string) => (
                     <label key={m} className="flex items-center gap-3 p-1 hover:bg-slate-700 rounded cursor-pointer text-xs text-slate-200">
                       <input type="checkbox" checked={selected.includes(m)} onChange={() => toggleMonth(m)} className="accent-indigo-500 w-3.5 h-3.5 cursor-pointer" /> {m}
                     </label>
                   ))}
                 </div>
               </div>
             )
           })}
        </div>
      )}
    </div>
  );
};

// --- COMPONENTE SORT HEADER ---
const SortableHeader = ({ field, label, currentSort, requestSort, className = "text-right" }: any) => {
  const isSorted = currentSort.key === field;
  return (
    <th onClick={() => requestSort(field)} className={`px-3 py-4 cursor-pointer hover:text-white transition-colors select-none ${className}`}>
      {label} {isSorted ? (currentSort.direction === 'asc' ? '↑' : '↓') : ''}
    </th>
  );
}

// --- DRILL-DOWN CLIENTES ---
const RowSKUDrillDown = ({ row, visao, mesesSelecionados, formatador }: any) => {
  const [expandido, setExpandido] = useState(false);
  const [clientes, setClientes] = useState<any[]>([]);
  const [carregando, setCarregando] = useState(false);

  const carregarClientes = async () => {
    if (expandido) { setExpandido(false); return; }
    setCarregando(true); setExpandido(true);
    try {
      const params = new URLSearchParams({ visao });
      mesesSelecionados.forEach((m: string) => params.append('meses_horizonte', m));
      const res = await axios.get(`/api/v1/kpis/torre-controle/clientes/${row.sku}?${params.toString()}`);
      setClientes(res.data);
    } catch (e) { } finally { setCarregando(false); }
  };

  return (
    <React.Fragment>
      <tr className="hover:bg-slate-800/30 transition-colors cursor-pointer group" onClick={carregarClientes}>
        <td className="px-4 py-3 border-b border-slate-800">
          <div className="flex items-center gap-3">
            <button className="text-slate-500 group-hover:text-indigo-400">{expandido ? <ChevronDown className="w-5 h-5"/> : <ChevronRight className="w-5 h-5"/>}</button>
            <div className="flex flex-col"><span className="font-bold text-slate-200">{row.descricao}</span><span className="text-[10px] text-slate-500 font-mono">{row.sku}</span></div>
          </div>
        </td>
        <td className="px-3 py-3 text-right font-semibold text-indigo-400 border-b border-slate-800">{formatador(row.val_meta_ia)}</td>
        <td className="px-3 py-3 text-right font-semibold text-sky-400 border-b border-slate-800">{formatador(row.val_meta_hum)}</td>
        <td className="px-3 py-3 text-right font-black text-white border-b border-slate-800">{formatador(row.val_real)}</td>
        <td className="px-3 py-3 text-right font-bold border-b border-slate-800 bg-slate-950/20">
           <span className={row.gap_ia > 0 ? 'text-rose-400' : 'text-emerald-400'}>{row.gap_ia > 0 ? 'Falta ' : 'Over '}{formatador(Math.abs(row.gap_ia))}</span>
        </td>
        <td className="px-3 py-3 text-right font-bold border-b border-slate-800 bg-slate-950/20">
           <span className={row.gap_humano > 0 ? 'text-rose-400' : 'text-emerald-400'}>{row.gap_humano > 0 ? 'Falta ' : 'Over '}{formatador(Math.abs(row.gap_humano))}</span>
        </td>
        <td className="px-3 py-3 text-right font-mono font-bold text-rose-400 border-b border-slate-800 bg-slate-950/40">{formatPct(row.mape_ia)}</td>
        <td className="px-3 py-3 text-right font-mono font-bold text-rose-400 border-b border-slate-800 bg-slate-950/40">{formatPct(row.mape_humano)}</td>
      </tr>
      {expandido && (
        <tr className="bg-slate-900/50 shadow-inner">
          <td colSpan={8} className="p-4 border-b border-slate-800">
            {carregando ? (
              <div className="text-xs text-indigo-400 flex items-center gap-2 font-bold"><RefreshCw className="w-4 h-4 animate-spin"/> Mapeando Clientes GOBI...</div>
            ) : clientes.length === 0 ? (
              <div className="text-xs text-slate-500 italic">Sem volume carteirado.</div>
            ) : (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <div className="bg-slate-950 p-3 rounded-lg border border-rose-900/50">
                  <h4 className="text-[10px] font-black uppercase tracking-widest text-rose-400 mb-2 flex items-center gap-2"><TrendingDown className="w-3 h-3"/> Alerta: Inadimplência de Meta</h4>
                  <div className="max-h-48 overflow-y-auto pr-2 space-y-1">
                    {clientes.filter(c => c.gap_humano > 0).map(c => (
                      <div key={c.cgc} className="flex justify-between items-center bg-slate-900 p-2 rounded border border-slate-800 text-xs">
                         <div className="truncate pr-4"><span className="font-bold text-slate-300 block truncate">{c.razaosocial}</span><span className="text-[9px] text-slate-500 font-mono block">{c.cgc}</span></div>
                         <div className="text-right shrink-0">
                            <span className="block text-slate-400 text-[10px]">Gap S&OP: <span className="font-black text-rose-400">{formatador(Math.abs(c.gap_humano))}</span></span>
                            <span className="block text-slate-500 text-[9px]">Gap IA: {formatador(Math.abs(c.gap_ia))}</span>
                         </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="bg-slate-950 p-3 rounded-lg border border-emerald-900/50">
                  <h4 className="text-[10px] font-black uppercase tracking-widest text-emerald-400 mb-2 flex items-center gap-2"><AlertTriangle className="w-3 h-3"/> Alerta: Over-Forecast</h4>
                  <div className="max-h-48 overflow-y-auto pr-2 space-y-1">
                    {clientes.filter(c => c.gap_humano < 0).map(c => (
                      <div key={c.cgc} className="flex justify-between items-center bg-slate-900 p-2 rounded border border-slate-800 text-xs">
                         <div className="truncate pr-4"><span className="font-bold text-slate-300 block truncate">{c.razaosocial}</span><span className="text-[9px] text-slate-500 font-mono block">{c.cgc}</span></div>
                         <div className="text-right shrink-0">
                            <span className="block text-slate-400 text-[10px]">Over S&OP: <span className="font-black text-emerald-400">+{formatador(Math.abs(c.gap_humano))}</span></span>
                            <span className="block text-slate-500 text-[9px]">Over IA: +{formatador(Math.abs(c.gap_ia))}</span>
                         </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </React.Fragment>
  );
};

// --- TELA PRINCIPAL ---
export default function AuditoriaArena() {
  const [lente, setLente] = useState<'kpis' | 'estoque' | 'sellout'>('kpis');
  const [visao, setVisao] = useState<'caixas' | 'financeiro'>('financeiro');
  
  const [mesesDisponiveis, setMesesDisponiveis] = useState<string[]>([]);
  const [mesesSelecionados, setMesesSelecionados] = useState<string[]>([]);
  
  // Filtros Globais da Tela
  const [categoriaSel, setCategoriaSel] = useState('Todas');
  const [segmentoSel, setSegmentoSel] = useState('Todos');
  const [clienteSel, setClienteSel] = useState('Todos');
  const [buscaSku, setBuscaSku] = useState('');
  
  // Dicionários para o Filtro Dinâmico em Cascata
  const [paresCatSeg, setParesCatSeg] = useState<any[]>([]);
  const [listaClientes, setListaClientes] = useState<string[]>([]);

  const [graficosKpi, setGraficosKpi] = useState<any[]>([]);
  const [skus, setSkus] = useState<any[]>([]);
  const [kpisGerais, setKpisGerais] = useState<any>({});
  const [carregando, setCarregando] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [sortConfig, setSortConfig] = useState({ key: 'erro_absoluto', direction: 'desc' });

  const formatador = visao === 'caixas' ? formatVol : formatFin;

  useEffect(() => {
    const fetchFiltros = async () => {
      try {
        const res = await axios.get(`/api/v1/kpis/filtros-auditoria?lente=${lente}`);
        setParesCatSeg(res.data.pares_cat_seg || []);
        setListaClientes(res.data.clientes || []);
        setMesesDisponiveis(res.data.meses_disponiveis || []);
        
        // 🔥 CORREÇÃO: Força a seleção sempre no Mês Atual
        if (res.data.meses_disponiveis?.length > 0) {
          const dataAtual = new Date();
          const mesAtualStr = `${String(dataAtual.getMonth() + 1).padStart(2, '0')}/${dataAtual.getFullYear()}`;
          
          if (res.data.meses_disponiveis.includes(mesAtualStr)) {
            setMesesSelecionados([mesAtualStr]);
          } else {
            // Se o mês atual ainda não existir na base, marca o mais recente
            setMesesSelecionados([res.data.meses_disponiveis[0]]);
          }
        } 
      } catch (err) {}
    };
    fetchFiltros();
  }, [lente]);

  // 🔥 LÓGICA DE FILTRO EM CASCATA INSTANTÂNEA NO FRONTEND
  const categoriasExibidas = useMemo(() => {
      if (segmentoSel === 'Todos') return Array.from(new Set(paresCatSeg.map(f => f.categoria))).sort();
      return Array.from(new Set(paresCatSeg.filter(f => f.segmento === segmentoSel).map(f => f.categoria))).sort();
  }, [paresCatSeg, segmentoSel]);

  const segmentosExibidos = useMemo(() => {
      if (categoriaSel === 'Todas') return Array.from(new Set(paresCatSeg.map(f => f.segmento))).sort();
      return Array.from(new Set(paresCatSeg.filter(f => f.categoria === categoriaSel).map(f => f.segmento))).sort();
  }, [paresCatSeg, categoriaSel]);

  const carregarDadosCore = async () => {
    if (lente !== 'estoque' && mesesSelecionados.length === 0) return;
    setCarregando(true);
    try {
      const params = new URLSearchParams();
      params.append('categoria', categoriaSel);
      params.append('segmento', segmentoSel);
      params.append('razaosocial', clienteSel);

      if (lente === 'kpis') {
        params.append('visao', visao);
        mesesSelecionados.forEach(m => params.append('meses_horizonte', m));
        const res = await axios.get(`/api/v1/kpis/torre-controle?${params.toString()}`);
        setGraficosKpi(res.data.graficos || []); 
        setSkus(res.data.skus || []);
        setKpisGerais(res.data.kpis_globais || {});
        setSortConfig({ key: 'erro_absoluto', direction: 'desc' });
      } 
      else if (lente === 'estoque') {
        const res = await axios.get(`/api/v1/kpis/riscos-estoque?${params.toString()}`);
        setSkus(res.data.estoque_sku || []); 
        setKpisGerais(res.data.kpis_globais || {});
        setSortConfig({ key: 'ruptura_rs', direction: 'desc' });
      }
      else {
        params.append('lente', lente);
        mesesSelecionados.forEach(m => params.append('meses_horizonte', m));
        const res = await axios.get(`/api/v1/kpis/auditoria-dinamica?${params.toString()}`);
        setSkus(res.data.tabela_skus || []); 
        setKpisGerais(res.data.kpis_globais || {});
        setSortConfig({ key: 'erro_abs_comercial', direction: 'desc' });
      }
    } catch (err) {} finally { setCarregando(false); }
  };

  useEffect(() => { carregarDadosCore(); }, [lente, visao, categoriaSel, segmentoSel, clienteSel, mesesSelecionados]);

  const handleSyncStock = async () => {
    setIsSyncing(true);
    try {
      await axios.post('/api/v1/kpis/sync-stock');
      await carregarDadosCore();
    } catch (e) {
      alert("Erro ao sincronizar com ERP GOBI.");
    } finally { setIsSyncing(false); }
  };

  const requestSort = (key: string) => {
    let direction = 'desc';
    if (sortConfig.key === key && sortConfig.direction === 'desc') direction = 'asc';
    setSortConfig({ key, direction });
  };

  const sortedSkus = useMemo(() => {
    let sortable = [...skus].filter(r => r.descricao?.toLowerCase().includes(buscaSku.toLowerCase()) || r.sku?.toLowerCase().includes(buscaSku.toLowerCase()));
    sortable.sort((a, b) => {
      if (a[sortConfig.key] < b[sortConfig.key]) return sortConfig.direction === 'asc' ? -1 : 1;
      if (a[sortConfig.key] > b[sortConfig.key]) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
    return sortable;
  }, [skus, sortConfig, buscaSku]);

  // 🔥 FUNÇÃO DE EXPORTAÇÃO CSV DE ALTA FIDELIDADE
  const handleExportCSV = () => {
    if (!sortedSkus || sortedSkus.length === 0) return;

    const headers = [
      "SKU", "Descrição", "Categoria", "Meta S&OP (Cx)", "Estoque Fábrica (Cx)",
      "Pedido (Cx)", "Pedido (R$)", "Faturado (Cx)", "Faturado (R$)", 
      "Corte (Cx)", "Corte (R$)", "Carteira em Aberto (Cx)", "Meta To-Go Restante (Cx)", 
      "Ruptura (Cx)", "Ruptura (R$)", "Sobra (Cx)", "Sobra (R$)"
    ];

    const rows = sortedSkus.map((r: any) => [
      r.sku, r.descricao, r.categoria, r.meta_mes || 0, r.estoque_atual || 0,
      r.vendas_mtd || 0, r.vl_pedido || 0, r.faturado_mtd || 0, r.vl_faturado || 0,
      r.corte_mtd || 0, r.vl_corte || 0, r.carteira_aberto || 0, r.meta_togo || 0,
      r.ruptura_vol || 0, r.ruptura_rs || 0, r.sobra_vol || 0, r.sobra_rs || 0
    ]);

    const csvContent = [
      headers.join(";"),
      ...rows.map(row => row.map(val => 
        typeof val === 'number' ? val.toString().replace('.', ',') : `"${val}"`
      ).join(";"))
    ].join("\n");

    const blob = new Blob(["\uFEFF" + csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Riscos_Estoque_${new Date().toISOString().slice(0,10)}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="p-6 bg-slate-950 min-h-screen text-slate-100 font-sans">
      
      <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 bg-slate-900 p-5 rounded-2xl border border-slate-800 mb-6 shadow-xl">
        <div>
          <h1 className="text-2xl font-black text-white tracking-tight flex items-center gap-3">
            <Activity className="text-indigo-500 w-7 h-7" /> Torre de Controle S&OP
          </h1>
          <p className="text-xs text-slate-400 mt-1 font-medium">Cockpit Executivo: Consequências Financeiras e Mapeamento de Erro</p>
        </div>

        <div className="flex flex-col md:flex-row gap-3 items-center">
          {lente === 'estoque' && (
            <button 
              onClick={handleExportCSV}
              className="flex items-center px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-black tracking-wider transition-all shadow-lg shadow-emerald-900/20 text-[10px] sm:text-xs uppercase h-full"
            >
              <Download className="w-4 h-4 mr-2" />
              Extrair CSV
            </button>
          )}

          {(lente === 'kpis' || lente === 'estoque') && (
            <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner h-full">
              <button onClick={() => setVisao('caixas')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${visao === 'caixas' ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><Package className="w-4 h-4"/> Caixas</button>
              <button onClick={() => setVisao('financeiro')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${visao === 'financeiro' ? 'bg-emerald-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><DollarSign className="w-4 h-4"/> Reais (R$)</button>
            </div>
          )}
          
          <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner overflow-x-auto h-full">
            <button onClick={() => setLente('kpis')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'kpis' ? 'bg-rose-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><Target className="w-4 h-4" /> Desvios MTD</button>
            <button onClick={() => setLente('estoque')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'estoque' ? 'bg-amber-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><Factory className="w-4 h-4" /> Riscos Estoque</button>
            <button onClick={() => setLente('sellout')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'sellout' ? 'bg-teal-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><ShoppingCart className="w-4 h-4" /> Retroativo MTRIX</button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        {lente !== 'estoque' && <div className="lg:col-span-1"><ExcelTreeDropdown titulo="Horizonte S&OP" options={mesesDisponiveis} selected={mesesSelecionados} onChange={setMesesSelecionados} /></div>}
        
        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2"><Users className="w-3 h-3"/> Razão Social (Cliente)</label>
          <select value={clienteSel} onChange={(e) => setClienteSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none"><option value="Todos">Todos os Clientes</option>{listaClientes.map((c:any) => <option key={c} value={c}>{c}</option>)}</select>
        </div>

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Categoria</label>
          <select value={categoriaSel} onChange={(e) => setCategoriaSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none"><option value="Todas">Todas as Categorias</option>{categoriasExibidas.map((c:any) => <option key={c} value={c}>{c}</option>)}</select>
        </div>

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Segmento</label>
          <select value={segmentoSel} onChange={(e) => setSegmentoSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none"><option value="Todos">Todos os Segmentos</option>{segmentosExibidos.map((s:any) => <option key={s} value={s}>{s}</option>)}</select>
        </div>

        <div className={`bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md ${lente === 'estoque' ? 'col-span-2' : ''}`}>
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex justify-between">Pesquisar SKU <span className="text-indigo-400 font-bold">{sortedSkus.length} Itens</span></label>
          <div className="relative"><Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" /><input type="text" value={buscaSku} onChange={(e) => setBuscaSku(e.target.value)} placeholder="Código ou nome..." className="w-full bg-slate-950 border border-slate-800 text-sm py-2 pl-9 pr-3 rounded-lg text-slate-300 focus:outline-none" /></div>
        </div>
        
        {lente === 'estoque' && (
          <button onClick={handleSyncStock} disabled={isSyncing} className="col-span-1 h-full bg-slate-800 hover:bg-slate-700 rounded-xl border border-slate-700 text-xs font-black uppercase text-sky-400 flex items-center justify-center gap-2 transition-all">
            {isSyncing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Sync ERP (API 90)
          </button>
        )}
      </div>

      {/* RENDERIZAÇÃO DOS GRÁFICOS */}
      {lente === 'kpis' || lente === 'sellout' ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-indigo-500 shadow-md"><div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">% WMAPE IA</div><div className="text-3xl font-black text-rose-400">{formatPct(kpisGerais.wmape_ia)}</div></div>
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-sky-500 shadow-md"><div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">{lente === 'kpis' ? '% WMAPE S&OP' : 'Erro Escoamento'}</div><div className="text-3xl font-black text-rose-400">{formatPct(kpisGerais.wmape_comercial)}</div></div>
          <div className={`bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md border-l-4 ${kpisGerais.fva >= 0 ? 'border-l-emerald-500' : 'border-l-rose-500'}`}><div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2 flex justify-between">FVA (Melhoria) {kpisGerais.fva >= 0 ? <TrendingUp className="w-4 h-4 text-emerald-500"/> : <TrendingDown className="w-4 h-4 text-rose-500"/>}</div><div className={`text-3xl font-black ${kpisGerais.fva >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{formatPct(kpisGerais.fva)}</div></div>
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
            <div className="text-[10px] font-black uppercase text-slate-400 tracking-wider mb-2 flex justify-between border-b border-slate-700 pb-1">
              <span>{lente === 'sellout' ? 'BIAS Canal' : 'BIAS S&OP'}</span><span>{lente === 'sellout' ? 'Dias Cob.' : 'BIAS IA'}</span>
            </div>
            <div className="flex justify-between items-center mt-2">
              <span className={`text-xl font-black ${kpisGerais.bias_humano > 0 ? 'text-amber-400' : 'text-rose-400'}`}>{formatPct(kpisGerais.bias_humano)}</span>
              <span className={`text-xl font-black ${kpisGerais.bias_ia > 0 ? 'text-amber-400' : 'text-rose-400'}`}>{lente === 'sellout' ? `${kpisGerais.cobertura_media_canal} D` : formatPct(kpisGerais.bias_ia)}</span>
            </div>
          </div>
        </div>
      ) : null}
      
      {lente === 'kpis' ? (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
            <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">% WMAPE Temporal (IA vs S&OP)</h3>
            <div className="h-[220px] w-full">
              <ResponsiveContainer>
                <ComposedChart data={graficosKpi} margin={{ top: 10, right: 10, bottom: 0, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="mes" stroke="#64748b" tick={{fontSize: 10}} />
                  <YAxis stroke="#64748b" tickFormatter={(v) => `${(v*100).toFixed(0)}%`} tick={{fontSize: 10}} />
                  <RechartsTooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155' }} formatter={(v:any) => formatPct(v)} />
                  <Legend verticalAlign="top" height={36} wrapperStyle={{ fontSize: '10px', fontWeight: 'bold' }}/>
                  <Bar dataKey="wmape_ia" name="WMAPE IA" fill="#a855f7" radius={[4,4,0,0]} barSize={20} />
                  <Bar dataKey="wmape_humano" name="WMAPE S&OP" fill="#f59e0b" radius={[4,4,0,0]} barSize={20} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
            <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">Bias Temporal (Interferência Humana vs IA)</h3>
            <div className="h-[220px] w-full">
              <ResponsiveContainer>
                <LineChart data={graficosKpi} margin={{ top: 10, right: 10, bottom: 0, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="mes" stroke="#64748b" tick={{fontSize: 10}} />
                  <YAxis stroke="#64748b" tickFormatter={(v) => `${(v*100).toFixed(0)}%`} tick={{fontSize: 10}} />
                  <RechartsTooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155' }} formatter={(v:any) => formatPct(v)} />
                  <Legend verticalAlign="top" height={36} wrapperStyle={{ fontSize: '10px', fontWeight: 'bold' }}/>
                  <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={2} />
                  <Line type="monotone" dataKey="bias_ia" name="Viés (BIAS) IA" stroke="#a855f7" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 4 }} />
                  <Line type="monotone" dataKey="bias_humano" name="Viés (BIAS) S&OP" stroke="#f59e0b" strokeWidth={3} dot={{ r: 5 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      ) : lente === 'estoque' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-rose-500 shadow-md relative overflow-hidden"><h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2"><TrendingDown className="w-4 h-4 text-rose-500"/> Faturamento em Risco</h3><p className="text-3xl font-black text-white z-10 relative">{visao === 'financeiro' ? formatFin(kpisGerais.total_ruptura_rs) : formatVol(kpisGerais.total_ruptura_rs)}</p></div>
          <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-amber-500 shadow-md relative overflow-hidden"><h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2"><Package className="w-4 h-4 text-amber-500"/> Capital Imobilizado</h3><p className="text-3xl font-black text-white z-10 relative">{visao === 'financeiro' ? formatFin(kpisGerais.total_sobra_rs) : formatVol(kpisGerais.total_sobra_rs)}</p></div>
        </div>
      ) : null}

      {/* RENDERIZAÇÃO DAS TABELAS COM SORT E TFOOT */}
      <div className="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden shadow-2xl">
        {carregando ? (
          <div className="p-20 flex justify-center items-center gap-3 text-indigo-400 font-bold uppercase text-xs"><RefreshCw className="w-6 h-6 animate-spin" /> Processando Tabelas...</div>
        ) : lente === 'estoque' ? (
          
          <table className="w-full text-left whitespace-nowrap">
            <thead>
              <tr className="bg-slate-950 text-slate-400 text-[10px] font-black uppercase tracking-widest border-b border-slate-800">
                <SortableHeader field="descricao" label="Produto" currentSort={sortConfig} requestSort={requestSort} className="px-6 text-left" />
                <SortableHeader field="meta_mes" label="Meta S&OP" currentSort={sortConfig} requestSort={requestSort} className="text-indigo-400 text-right" />
                <SortableHeader field="estoque_atual" label="Estoque Fab." currentSort={sortConfig} requestSort={requestSort} className="text-slate-300 text-right" />
                <th className="px-3 py-4 text-right cursor-pointer hover:text-white" onClick={() => requestSort('vendas_mtd')}>Pedido <br/><span className="text-[10px] opacity-70">(Cx / R$)</span></th>
                <th className="px-3 py-4 text-right cursor-pointer hover:text-white text-emerald-400" onClick={() => requestSort('faturado_mtd')}>Faturado <br/><span className="text-[10px] opacity-70">(Cx / R$)</span></th>
                <th className="px-3 py-4 text-right cursor-pointer hover:text-white text-rose-400" onClick={() => requestSort('corte_mtd')}>Corte <br/><span className="text-[10px] opacity-70">(Cx / R$)</span></th>
                <SortableHeader field="carteira_aberto" label="Carteira em Aberto" currentSort={sortConfig} requestSort={requestSort} className="text-amber-500 text-right bg-amber-900/10" />
                <SortableHeader field="meta_togo" label="Meta To-Go Restante" currentSort={sortConfig} requestSort={requestSort} className="text-amber-300 text-right bg-amber-900/20" />
                <SortableHeader field="ruptura_rs" label="Ruptura (Risco)" currentSort={sortConfig} requestSort={requestSort} className="bg-rose-950/10 text-right text-rose-500" />
                <SortableHeader field="sobra_rs" label="Sobra (Imobilizado)" currentSort={sortConfig} requestSort={requestSort} className="px-6 bg-sky-950/10 text-right text-sky-400" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-xs">
              {sortedSkus.map((d, i) => (
                <tr key={i} className="hover:bg-slate-800/30">
                  <td className="px-6 py-3.5"><div className="flex flex-col"><span className="font-bold text-slate-200">{d.descricao}</span><span className="text-[10px] text-slate-500 font-mono">{d.sku}</span></div></td>
                  <td className="px-3 py-3.5 text-right font-medium text-indigo-300">{formatVol(d.meta_mes)}</td>
                  <td className="px-3 py-3.5 text-right font-bold text-slate-300">{formatVol(d.estoque_atual)}</td>
                  
                  <td className="px-3 py-3.5 text-right bg-slate-800/10">
                    <div className="font-bold text-white">{formatVol(d.vendas_mtd)}</div>
                    <div className="text-[11px] text-slate-500 mt-0.5">{formatFin(d.vl_pedido)}</div>
                  </td>
                  <td className="px-3 py-3.5 text-right bg-emerald-900/10">
                    <div className="font-bold text-emerald-400">{formatVol(d.faturado_mtd)}</div>
                    <div className="text-[11px] text-emerald-500/60 mt-0.5">{formatFin(d.vl_faturado)}</div>
                  </td>
                  <td className="px-3 py-3.5 text-right bg-rose-900/10">
                    <div className="font-bold text-rose-400">{formatVol(d.corte_mtd)}</div>
                    <div className="text-[11px] text-rose-500/60 mt-0.5">{formatFin(d.vl_corte)}</div>
                  </td>

                  <td className="px-3 py-3.5 text-right font-bold text-amber-500 bg-amber-900/10">
                    {formatVol(d.carteira_aberto)}
                  </td>
                  <td className="px-3 py-3.5 text-right font-bold text-amber-300 bg-amber-900/20">
                    {formatVol(d.meta_togo)}
                  </td>

                  <td className="px-4 py-3.5 text-right bg-rose-500/10">
                    <div className="font-black text-rose-500">{formatFin(d.ruptura_rs)}</div>
                    <div className="text-xs text-rose-400/80 mt-0.5">{formatVol(d.ruptura_vol)} cx</div>
                  </td>

                  <td className="px-6 py-3.5 text-right bg-sky-500/10">
                    <div className="font-black text-sky-400">{formatFin(d.sobra_rs)}</div>
                    <div className="text-xs text-sky-400/80 mt-0.5">{formatVol(d.sobra_vol)} cx</div>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-slate-950 font-black text-white border-t-2 border-slate-700 text-xs">
              <tr>
                <td className="px-6 py-4 uppercase tracking-widest text-indigo-400">Totais da Visão</td>
                <td className="px-3 py-4 text-right text-indigo-400">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.meta_mes || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-slate-300">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.estoque_atual || 0), 0))}</td>
                
                <td className="px-3 py-4 text-right bg-slate-800/10">
                  <div className="font-bold text-white">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.vendas_mtd || 0), 0))}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">{formatFin(sortedSkus.reduce((sum, r) => sum + (r.vl_pedido || 0), 0))}</div>
                </td>
                <td className="px-3 py-4 text-right bg-emerald-900/10">
                  <div className="font-bold text-emerald-400">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.faturado_mtd || 0), 0))}</div>
                  <div className="text-[10px] text-emerald-500/60 mt-0.5">{formatFin(sortedSkus.reduce((sum, r) => sum + (r.vl_faturado || 0), 0))}</div>
                </td>
                <td className="px-3 py-4 text-right bg-rose-900/10">
                  <div className="font-bold text-rose-400">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.corte_mtd || 0), 0))}</div>
                  <div className="text-[10px] text-rose-500/60 mt-0.5">{formatFin(sortedSkus.reduce((sum, r) => sum + (r.vl_corte || 0), 0))}</div>
                </td>

                <td className="px-3 py-4 text-right text-amber-500 bg-amber-900/10">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.carteira_aberto || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-amber-300 bg-amber-900/20">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.meta_togo || 0), 0))}</td>

                <td className="px-4 py-4 text-right bg-rose-950/10">
                  <div className="text-rose-500">{formatFin(sortedSkus.reduce((sum, r) => sum + (r.ruptura_rs || 0), 0))}</div>
                  <div className="text-[10px] text-rose-400/80">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.ruptura_vol || 0), 0))} cx</div>
                </td>
                <td className="px-6 py-4 text-right bg-sky-950/10">
                  <div className="text-sky-400">{formatFin(sortedSkus.reduce((sum, r) => sum + (r.sobra_rs || 0), 0))}</div>
                  <div className="text-[10px] text-sky-400/80">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.sobra_vol || 0), 0))} cx</div>
                </td>
              </tr>
            </tfoot>
          </table>

        ) : lente === 'sellout' ? (
           <table className="w-full text-left whitespace-nowrap">
             <thead>
               <tr className="bg-slate-950 text-slate-400 border-b border-slate-800 text-[10px] font-black uppercase tracking-widest">
                 <SortableHeader field="descricao" label="Produto" currentSort={sortConfig} requestSort={requestSort} className="px-6 text-left" />
                 <SortableHeader field="vol_real" label="Realizado (Sell-out)" currentSort={sortConfig} requestSort={requestSort} />
                 <SortableHeader field="vol_ia_congelado" label="Meta IA" currentSort={sortConfig} requestSort={requestSort} />
                 <SortableHeader field="vol_comercial_congelado" label="Meta S&OP" currentSort={sortConfig} requestSort={requestSort} />
                 <SortableHeader field="estoque_canal" label="Estoque Canal" currentSort={sortConfig} requestSort={requestSort} />
                 <SortableHeader field="mape_ia" label="% MAPE IA" currentSort={sortConfig} requestSort={requestSort} className="border-l border-slate-800 bg-slate-950/40 text-rose-400 text-right" />
                 <SortableHeader field="mape_comercial" label="% MAPE S&OP" currentSort={sortConfig} requestSort={requestSort} className="bg-slate-950/40 text-rose-400 text-right" />
               </tr>
             </thead>
             <tbody className="divide-y divide-slate-800/60 text-xs">
               {sortedSkus.map(row => (
                 <tr key={row.sku} className="hover:bg-slate-800/30">
                   <td className="px-6 py-3.5"><div className="flex flex-col"><span className="font-bold text-slate-200">{row.descricao}</span><span className="text-[10px] text-slate-500 font-mono mt-0.5">{row.sku}</span></div></td>
                   <td className="px-4 py-3.5 text-right font-black text-white">{formatVol(row.vol_real)}</td>
                   <td className="px-4 py-3.5 text-right font-semibold text-indigo-400 bg-indigo-950/10">{formatVol(row.vol_ia_congelado)}</td>
                   <td className="px-4 py-3.5 text-right font-semibold text-sky-400 bg-sky-950/10">{formatVol(row.vol_comercial_congelado)}</td>
                   <td className="px-4 py-3.5 text-right font-semibold text-amber-400">{formatVol(row.estoque_canal)}</td>
                   <td className="px-4 py-3.5 text-right font-mono font-bold text-rose-400 border-l border-slate-800 bg-slate-950/40">{formatPct(row.mape_ia)}</td>
                   <td className="px-4 py-3.5 text-right font-mono font-bold text-rose-400 bg-slate-950/40">{formatPct(row.mape_comercial)}</td>
                 </tr>
               ))}
             </tbody>
             <tfoot className="bg-slate-950 font-black text-white border-t-2 border-slate-700 text-xs">
              <tr>
                <td className="px-6 py-4 uppercase tracking-widest text-indigo-400">Somas do Portfólio</td>
                <td className="px-4 py-4 text-right">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.vol_real || 0), 0))}</td>
                <td className="px-4 py-4 text-right text-indigo-400">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.vol_ia_congelado || 0), 0))}</td>
                <td className="px-4 py-4 text-right text-sky-400">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.vol_comercial_congelado || 0), 0))}</td>
                <td className="px-4 py-4 text-right text-amber-400">{formatVol(sortedSkus.reduce((sum, r) => sum + (r.estoque_canal || 0), 0))}</td>
                <td colSpan={2} className="px-4 py-4 bg-slate-950/40 text-right text-slate-500 text-[10px]">Médias Ponderadas nos Cards Topo</td>
              </tr>
             </tfoot>
            </table>
        ) : (
           <table className="w-full text-left whitespace-nowrap">
             <thead>
               <tr className="bg-slate-950 text-slate-400 border-b border-slate-800 text-[10px] font-black uppercase tracking-widest">
                 <SortableHeader field="descricao" label="Produto (Clique p/ Clientes)" currentSort={sortConfig} requestSort={requestSort} className="px-6 text-left" />
                 <SortableHeader field="val_meta_ia" label="Meta IA" currentSort={sortConfig} requestSort={requestSort} className="text-indigo-400 text-right" />
                 <SortableHeader field="val_meta_hum" label="Meta S&OP" currentSort={sortConfig} requestSort={requestSort} className="text-sky-400 text-right" />
                 <SortableHeader field="val_real" label="Realizado MTD" currentSort={sortConfig} requestSort={requestSort} className="text-white text-right" />
                 <SortableHeader field="gap_ia" label="Gap IA" currentSort={sortConfig} requestSort={requestSort} className="bg-slate-950/20 text-right" />
                 <SortableHeader field="gap_humano" label="Gap S&OP" currentSort={sortConfig} requestSort={requestSort} className="bg-slate-950/20 text-right" />
                 <SortableHeader field="mape_ia" label="% MAPE IA" currentSort={sortConfig} requestSort={requestSort} className="border-l border-slate-800 bg-slate-950/40 text-rose-400 text-right" />
                 <SortableHeader field="mape_humano" label="% MAPE S&OP" currentSort={sortConfig} requestSort={requestSort} className="bg-slate-950/40 text-rose-400 text-right" />
               </tr>
             </thead>
             <tbody className="divide-y divide-slate-800/60 text-xs">
               {sortedSkus.map((row) => <RowSKUDrillDown key={row.sku} row={row} visao={visao} mesesSelecionados={mesesSelecionados} formatador={formatador} />)}
             </tbody>
             <tfoot className="bg-slate-950 font-black text-white border-t-2 border-slate-700 text-xs">
              <tr>
                <td className="px-6 py-4 uppercase tracking-widest text-indigo-400">Acumulado MTD</td>
                <td className="px-3 py-4 text-right text-indigo-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.val_meta_ia || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-sky-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.val_meta_hum || 0), 0))}</td>
                <td className="px-3 py-4 text-right">{formatador(sortedSkus.reduce((sum, r) => sum + (r.val_real || 0), 0))}</td>
                <td className="px-3 py-4 text-right bg-slate-950/20 text-slate-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.gap_ia || 0), 0))}</td>
                <td className="px-3 py-4 text-right bg-slate-950/20 text-slate-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.gap_humano || 0), 0))}</td>
                <td colSpan={2} className="px-3 py-4 bg-slate-950/40 text-right text-slate-500 text-[10px]">Gap Consolidado</td>
              </tr>
             </tfoot>
            </table>
        )}
      </div>
    </div>
  );
}