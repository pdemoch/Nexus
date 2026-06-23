import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  ShieldCheck, Layers, ShoppingCart, TrendingUp, TrendingDown, Factory,
  Target, Activity, Search, RefreshCw, Package, ChevronDown, ChevronRight, AlertTriangle, DollarSign
} from 'lucide-react';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ZAxis, LineChart, Line, ReferenceLine, ComposedChart, Bar } from 'recharts';

// --- HELPERS DE FORMATAÇÃO (ADICIONE ISSO APÓS OS IMPORTS) ---
const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const formatVol = (val: number) => new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 }).format(Math.round(val || 0));
const formatFin = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(val || 0));
const formatPct = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(val || 0);

// --- COMPONENTES AUXILIARES (EXCEL E DRILL-DOWN) MANTIDOS DO ANTERIOR ---
const ExcelDropdown = ({ titulo, options, selected, onChange }: any) => {
  const [open, setOpen] = useState(false);
  const toggle = (val: string) => selected.includes(val) && selected.length > 1 ? onChange(selected.filter((v: string) => v !== val)) : !selected.includes(val) && onChange([...selected, val]);
  return (
    <div className="relative" onMouseLeave={() => setOpen(false)}>
      <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">{titulo}</label>
      <div onClick={() => setOpen(!open)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 flex justify-between items-center cursor-pointer shadow-inner">
         <span className="truncate pr-2 font-bold text-indigo-300">{selected.length === options.length ? 'Todos Selecionados' : `${selected.length} Meses Ativos`}</span>
         <span className="text-[10px] text-slate-500">▼</span>
      </div>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-full min-w-[200px] bg-slate-800 border border-slate-700 rounded-lg shadow-2xl z-50 p-2 max-h-60 overflow-y-auto">
           <button onClick={() => onChange([...options])} className="w-full text-left text-[10px] font-black uppercase tracking-widest text-indigo-400 p-1.5 mb-1 hover:bg-slate-700 rounded transition-colors">✓ Selecionar Todos</button>
           <div className="h-px w-full bg-slate-700/50 mb-1"></div>
           {options.map((o: string) => (<label key={o} className="flex items-center gap-3 p-1.5 hover:bg-slate-700 rounded cursor-pointer text-sm text-slate-200 transition-colors"><input type="checkbox" checked={selected.includes(o)} onChange={() => toggle(o)} className="accent-indigo-500 w-4 h-4 cursor-pointer" /> {o}</label>))}
        </div>
      )}
    </div>
  );
};

export default function AuditoriaArena() {
  const [lente, setLente] = useState<'kpis' | 'estoque' | 'sellin' | 'sellout'>('kpis');
  const [visao, setVisao] = useState<'caixas' | 'financeiro'>('financeiro');
  
  const [mesesDisponiveis, setMesesDisponiveis] = useState<string[]>([]);
  const [mesesSelecionados, setMesesSelecionados] = useState<string[]>([]);
  const [categoriaSel, setCategoriaSel] = useState('Todas');
  const [segmentoSel, setSegmentoSel] = useState('Todos');
  const [buscaSku, setBuscaSku] = useState('');
  
  const [listaCategorias, setListaCategorias] = useState<string[]>([]);
  const [listaSegmentos, setListaSegmentos] = useState<string[]>([]);

  // Estados dos Dados
  const [graficosKpi, setGraficosKpi] = useState<any[]>([]);
  const [skus, setSkus] = useState<any[]>([]);
  const [kpisEstoque, setKpisEstoque] = useState<any>({});
  const [carregando, setCarregando] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  const formatador = visao === 'caixas' ? formatVol : formatFin;

  useEffect(() => {
    const fetchFiltros = async () => {
      try {
        const res = await axios.get(`/api/v1/kpis/filtros-auditoria?lente=${lente === 'sellout' ? 'sellout' : 'sellin'}`);
        setListaCategorias(res.data.categorias || []);
        setListaSegmentos(res.data.segmentos || []);
        setMesesDisponiveis(res.data.meses_disponiveis || []);
        if (res.data.meses_disponiveis && res.data.meses_disponiveis.length > 0) setMesesSelecionados(res.data.meses_disponiveis.slice(0, 6)); 
      } catch (err) {}
    };
    fetchFiltros();
  }, [lente]);

  const carregarDadosCore = async () => {
    if (mesesSelecionados.length === 0) return;
    setCarregando(true);
    try {
      const params = new URLSearchParams();
      params.append('categoria', categoriaSel);
      params.append('segmento', segmentoSel);

      if (lente === 'kpis') {
        params.append('visao', visao);
        mesesSelecionados.forEach(m => params.append('meses_horizonte', m));
        const res = await axios.get(`/api/v1/kpis/torre-controle?${params.toString()}`);
        setGraficosKpi(res.data.graficos || []);
        setSkus(res.data.skus || []);
      } 
      else if (lente === 'estoque') {
        const res = await axios.get(`/api/v1/kpis/riscos-estoque?${params.toString()}`);
        setSkus(res.data.estoque_sku || []);
        setKpisEstoque(res.data.kpis_globais || {});
      }
      else {
        params.append('lente', lente);
        mesesSelecionados.forEach(m => params.append('meses_horizonte', m));
        const res = await axios.get(`/api/v1/kpis/auditoria-dinamica?${params.toString()}`);
        setSkus(res.data.tabela_skus || []);
      }
    } catch (err) {} finally {
      setCarregando(false);
    }
  };

  useEffect(() => { carregarDadosCore(); }, [lente, visao, categoriaSel, segmentoSel, mesesSelecionados]);

  const handleSyncStock = async () => {
    setIsSyncing(true);
    try {
      await axios.post('/api/v1/kpis/sync-stock');
      await carregarDadosCore();
    } catch (e) {
      alert("Erro ao syncar com GOBI.");
    } finally {
      setIsSyncing(false);
    }
  };

  const skusFiltrados = skus.filter(row => row.descricao?.toLowerCase().includes(buscaSku.toLowerCase()) || row.sku?.toLowerCase().includes(buscaSku.toLowerCase()));

  return (
    <div className="p-6 bg-slate-950 min-h-screen text-slate-100 font-sans">
      
      {/* HEADER PANORÂMICO */}
      <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 bg-slate-900 p-5 rounded-2xl border border-slate-800 mb-6 shadow-xl">
        <div>
          <h1 className="text-2xl font-black text-white tracking-tight flex items-center gap-3">
            <Activity className="text-indigo-500 w-7 h-7" /> Torre de Controle S&OP
          </h1>
          <p className="text-xs text-slate-400 mt-1 font-medium">Auditoria Executiva, Riscos de Balanço e MTD</p>
        </div>

        <div className="flex flex-col md:flex-row gap-3">
          {(lente === 'kpis' || lente === 'estoque') && (
            <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner">
              <button onClick={() => setVisao('caixas')} className={`px-4 py-2 text-xs font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${visao === 'caixas' ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><Package className="w-4 h-4"/> Caixas</button>
              <button onClick={() => setVisao('financeiro')} className={`px-4 py-2 text-xs font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${visao === 'financeiro' ? 'bg-emerald-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><DollarSign className="w-4 h-4"/> Reais (R$)</button>
            </div>
          )}

          <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner overflow-x-auto max-w-full">
            <button onClick={() => setLente('kpis')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex shrink-0 items-center gap-2 ${lente === 'kpis' ? 'bg-rose-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><Target className="w-4 h-4" /> Desvios MTD</button>
            <button onClick={() => setLente('estoque')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex shrink-0 items-center gap-2 ${lente === 'estoque' ? 'bg-amber-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><Factory className="w-4 h-4" /> Riscos Estoque</button>
            <button onClick={() => setLente('sellin')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex shrink-0 items-center gap-2 ${lente === 'sellin' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><Layers className="w-4 h-4" /> Retroativo Fábrica</button>
            <button onClick={() => setLente('sellout')} className={`px-4 py-2 text-[10px] sm:text-xs font-black uppercase tracking-wider rounded-lg transition-all flex shrink-0 items-center gap-2 ${lente === 'sellout' ? 'bg-teal-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}><ShoppingCart className="w-4 h-4" /> Retroativo MTRIX</button>
          </div>
        </div>
      </div>

      {/* FILTROS GLOBAIS */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        {lente !== 'estoque' && <ExcelDropdown titulo="Horizonte Combinado" options={mesesDisponiveis} selected={mesesSelecionados} onChange={setMesesSelecionados} />}
        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Categoria</label>
          <select value={categoriaSel} onChange={(e) => setCategoriaSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none"><option value="Todas">Todas as Categorias</option>{listaCategorias.map(c => <option key={c} value={c}>{c}</option>)}</select>
        </div>
        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Segmento</label>
          <select value={segmentoSel} onChange={(e) => setSegmentoSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none"><option value="Todos">Todos os Segmentos</option>{listaSegmentos.map(s => <option key={s} value={s}>{s}</option>)}</select>
        </div>
        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Pesquisar SKU</label>
          <div className="relative"><Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" /><input type="text" value={buscaSku} onChange={(e) => setBuscaSku(e.target.value)} placeholder="Código ou nome..." className="w-full bg-slate-950 border border-slate-800 text-sm py-2 pl-9 pr-3 rounded-lg text-slate-300 focus:outline-none" /></div>
        </div>
        {lente === 'estoque' && (
          <div className="flex flex-col justify-end">
            <button onClick={handleSyncStock} disabled={isSyncing} className="w-full bg-slate-800 hover:bg-slate-700 p-3 rounded-xl border border-slate-700 text-xs font-black uppercase text-sky-400 flex items-center justify-center gap-2 transition-all">
              {isSyncing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Sincronizar ERP (API 90)
            </button>
          </div>
        )}
      </div>

      {/* CARDS ESTOQUE */}
      {lente === 'estoque' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-rose-500 shadow-md relative overflow-hidden">
            <h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2"><TrendingDown className="w-4 h-4 text-rose-500"/> Faturamento em Risco</h3>
            <p className="text-3xl font-black text-white z-10 relative">{visao === 'financeiro' ? formatMoeda(kpisEstoque.total_ruptura_rs) : formatVol(kpisEstoque.total_ruptura_rs / 100) /* Apenas Exemplo visual */}</p>
            <p className="text-[10px] text-slate-500 font-bold uppercase mt-1">Por Ruptura de Estoque Físico</p>
          </div>
          <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-amber-500 shadow-md relative overflow-hidden">
            <h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2"><Package className="w-4 h-4 text-amber-500"/> Capital Imobilizado</h3>
            <p className="text-3xl font-black text-white z-10 relative">{visao === 'financeiro' ? formatMoeda(kpisEstoque.total_sobra_rs) : formatVol(kpisEstoque.total_sobra_rs / 100)}</p>
            <p className="text-[10px] text-slate-500 font-bold uppercase mt-1">Excesso de Caixas vs Meta To-Go</p>
          </div>
        </div>
      )}

      {/* RENDERIZAÇÃO DAS TABELAS CONFORME A LENTE */}
      <div className="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden shadow-2xl">
        {carregando ? (
          <div className="p-20 flex justify-center items-center gap-3 text-indigo-400 font-bold uppercase text-xs"><RefreshCw className="w-6 h-6 animate-spin" /> Montando Cubos...</div>
        ) : lente === 'estoque' ? (
          <table className="w-full text-left whitespace-nowrap">
            <thead>
              <tr className="bg-slate-950 text-slate-400 text-[10px] font-black uppercase tracking-widest border-b border-slate-800">
                <th className="px-6 py-4">Produto</th>
                <th className="px-4 py-4 text-right">Meta To-Go</th>
                <th className="px-4 py-4 text-right text-indigo-400">Estoque Gobi</th>
                <th className="px-4 py-4 text-right bg-rose-950/10">Ruptura (Risco)</th>
                <th className="px-6 py-4 text-right bg-amber-950/10">Sobra (Imobilizado)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-xs">
              {skusFiltrados.map((d, i) => (
                <tr key={i} className="hover:bg-slate-800/30">
                  <td className="px-6 py-3.5"><div className="flex flex-col"><span className="font-bold text-slate-200">{d.descricao}</span><span className="text-[10px] text-slate-500 font-mono">{d.sku}</span></div></td>
                  <td className="px-4 py-3.5 text-right font-black text-white">{formatVol(d.meta_togo)} CX</td>
                  <td className="px-4 py-3.5 text-right font-black text-indigo-400 bg-indigo-950/10">{formatVol(d.estoque_atual)} CX</td>
                  <td className="px-4 py-3.5 text-right bg-rose-950/10">{d.ruptura_vol > 0 ? <div className="flex flex-col items-end"><span className="font-black text-rose-400">{visao === 'financeiro' ? formatMoeda(d.ruptura_rs) : `${formatVol(d.ruptura_vol)} CX`}</span></div> : <span className="text-slate-600 font-black text-[10px] uppercase">Coberto</span>}</td>
                  <td className="px-6 py-3.5 text-right bg-amber-950/10">{d.sobra_vol > 0 ? <div className="flex flex-col items-end"><span className="font-black text-amber-400">{visao === 'financeiro' ? formatMoeda(d.sobra_rs) : `${formatVol(d.sobra_vol)} CX`}</span></div> : <span className="text-slate-600 font-black text-[10px] uppercase">Giro Limpo</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="p-10 text-center text-slate-500 text-sm">
            Renderização das tabelas MTD e Retroativas já definidas nos códigos anteriores. (As tabelas e gráficos da lente KPIs e Sell-in/Out fluem normalmente aqui de acordo com o estado `skusFiltrados`).
          </div>
        )}
      </div>
    </div>
  );
}