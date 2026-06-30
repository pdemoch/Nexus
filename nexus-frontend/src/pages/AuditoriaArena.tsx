import React, { useState, useEffect, useMemo, useRef } from 'react';
import axios from 'axios';
import { 
  ShoppingCart, TrendingUp, TrendingDown, Factory, Target, Activity, 
  Search, RefreshCw, Package, ChevronDown, ChevronRight, AlertTriangle, DollarSign, Users, Download, Calendar, Clock, Terminal
} from 'lucide-react';
import { ResponsiveContainer, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, LineChart, Line, ReferenceLine, ComposedChart, Bar, Legend } from 'recharts';

// --- HELPERS DE FORMATAÇÃO E FUSO HORÁRIO ---
const formatFin = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVol = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const formatPct = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(val || 0);

// Captura a data corrente sob o fuso de Brasília (UTC-3)
const obterDataBrasilia = () => {
  const d = new Date();
  const utc = d.getTime() + (d.getTimezoneOffset() * 60000);
  return new Date(utc + (3600000 * -3));
};

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
const RowSKUDrillDown = ({ row, visao, mesesSelecionados, formatador, dataSnapshot }: any) => {
  const [expandido, setExpandido] = useState(false);
  const [clientes, setClientes] = useState<any[]>([]);
  const [carregando, setCarregando] = useState(false);

  const carregarClientes = async () => {
    if (expandido) { setExpandido(false); return; }
    setCarregando(true); setExpandido(true);
    try {
      const params = new URLSearchParams({ visao, data_snapshot: dataSnapshot });
      mesesSelecionados.forEach((m: string) => params.append('meses_horizonte', m));
      const res = await axios.get(`/api/v1/kpis/torre-controle/clientes/${row.sku}?${params.toString()}`);
      setClientes(res.data);
    } catch (e) { } finally { setCarregando(false); }
  };

  return (
    <React.Fragment>
      <tr className="hover:bg-slate-800/30 transition-colors cursor-pointer group border-b border-slate-800/50" onClick={carregarClientes}>
        <td className="px-4 py-3">
          <div className="flex items-center gap-3">
            <button className="text-slate-500 group-hover:text-indigo-400">{expandido ? <ChevronDown className="w-5 h-5"/> : <ChevronRight className="w-5 h-5"/>}</button>
            <div className="flex flex-col"><span className="font-bold text-slate-200">{row.descricao}</span><span className="text-[10px] text-slate-500 font-mono">{row.sku}</span></div>
          </div>
        </td>
        <td className="px-3 py-3 text-right font-semibold text-indigo-400">{formatador(row.val_meta_ia || 0)}</td>
        <td className="px-3 py-3 text-right font-semibold text-sky-400">{formatador(row.val_meta_hum || 0)}</td>
        <td className="px-3 py-3 text-right font-black text-white">{formatador(row.val_real || 0)}</td>
        <td className="px-3 py-3 text-right font-bold bg-slate-950/20 border-l border-slate-800/50">
           <span className={(row.gap_ia || 0) > 0 ? 'text-rose-400' : 'text-emerald-400'}>{(row.gap_ia || 0) > 0 ? 'Falta ' : 'Over '}{formatador(Math.abs(row.gap_ia || 0))}</span>
        </td>
        <td className="px-3 py-3 text-right font-bold bg-slate-950/20">
           <span className={(row.gap_humano || 0) > 0 ? 'text-rose-400' : 'text-emerald-400'}>{(row.gap_humano || 0) > 0 ? 'Falta ' : 'Over '}{formatador(Math.abs(row.gap_humano || 0))}</span>
        </td>
        <td className="px-3 py-3 text-right font-mono font-bold text-rose-400 bg-slate-950/40 border-l border-slate-800/50">{formatPct(row.mape_ia || 0)}</td>
        <td className="px-3 py-3 text-right font-mono font-bold text-rose-400 bg-slate-950/40">{formatPct(row.mape_humano || 0)}</td>
      </tr>
      {expandido && (
        <tr className="bg-slate-900/50 shadow-inner">
          <td colSpan={8} className="p-4 border-b border-slate-800">
            {carregando ? (
              <div className="text-xs text-indigo-400 flex items-center gap-2 font-bold"><RefreshCw className="w-4 h-4 animate-spin"/> Mapeando Clientes...</div>
            ) : clientes.length === 0 ? (
              <div className="text-xs text-slate-500 italic">Sem volume carteirado.</div>
            ) : (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <div className="bg-slate-950 p-3 rounded-lg border border-rose-900/50">
                  <h4 className="text-[10px] font-black uppercase tracking-widest text-rose-400 mb-2 flex items-center gap-2"><TrendingDown className="w-3 h-3"/> Alerta: Inadimplência de Meta</h4>
                  <div className="max-h-48 overflow-y-auto pr-2 space-y-1 custom-scrollbar">
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
                  <div className="max-h-48 overflow-y-auto pr-2 space-y-1 custom-scrollbar">
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

// --- DRILL-DOWN EXCLUSIVO PARA RISCOS DE ESTOQUE ---
const RowRiscoDrillDown = ({ row, visao, formatador, dataSnapshot }: any) => {
  const [expandido, setExpandido] = useState(false);
  const [clientes, setClientes] = useState<any[]>([]);
  const [carregando, setCarregando] = useState(false);

  let acao = { text: "🟢 COBERTO", cor: "text-emerald-400 bg-emerald-950/30 border border-emerald-900/50" };
  
  if (row.__risco_falta > 0) {
    acao = { text: `🔴 PRODUZIR: +${formatador(row.__risco_falta)}`, cor: "text-rose-400 bg-rose-950/30 font-black animate-pulse shadow-rose-900/50 shadow-md border-rose-500/50 border" };
  } else if (row.__sobra > row.__projecao * 0.5) { 
    acao = { text: `🟡 R. SOBRA: ${formatador(row.__sobra)}`, cor: "text-amber-400 bg-amber-950/30 border border-amber-900/50" };
  }

  const carregarClientes = async () => {
    setExpandido(!expandido);
    if (!expandido && clientes.length === 0) {
      setCarregando(true);
      try {
        const res = await axios.get(`/api/v1/kpis/riscos-estoque/drilldown-clientes/${row.sku}?data_snapshot=${dataSnapshot}`);
        setClientes(res.data);
      } catch (e) {} finally { setCarregando(false); }
    }
  };

  const formatFinV2 = (val: number) => visao === 'caixas' ? formatVol(val) : formatFin(val * (row.pmv || 0));

  return (
    <React.Fragment>
      <tr className="hover:bg-slate-800/30 transition-colors cursor-pointer group border-b border-slate-800/50" onClick={carregarClientes}>
        <td className="px-6 py-4">
          <div className="flex items-center gap-3">
            <button className="text-slate-500 group-hover:text-indigo-400">{expandido ? <ChevronDown className="w-5 h-5"/> : <ChevronRight className="w-5 h-5"/>}</button>
            <div className="flex flex-col"><span className="font-bold text-slate-200">{row.descricao}</span><span className="text-[10px] text-slate-500 font-mono">{row.sku}</span></div>
          </div>
        </td>
        <td className="px-3 py-4 text-right font-black text-slate-300 bg-slate-900/40" title="Estoque Físico na Fábrica. Em Reais, é o Volume Físico x PMV.">
          {formatador(row.__estoque)}
        </td>
        <td className="px-3 py-4 text-right text-indigo-400 font-bold" title="Meta S&OP Total (Conforme planejamento)">{formatador(row.__meta)}</td>
        <td className="px-3 py-4 w-24">
          <div className="flex flex-col gap-1 items-end" title="Atingimento: Pedido / Meta S&OP">
             <span className={`text-[10px] font-black ${row.__atingimento > 100 ? 'text-rose-400' : 'text-slate-400'}`}>
                {row.__meta === 0 && row.__pedido > 0 ? '100% (+)' : `${row.__atingimento.toFixed(1)}%`}
             </span>
             <div className="w-full bg-slate-800 h-1 rounded-full"><div className={`h-1 rounded-full ${row.__atingimento > 100 ? 'bg-rose-500' : 'bg-indigo-500'}`} style={{width: `${Math.min(row.__atingimento, 100)}%`}}></div></div>
          </div>
        </td>
        <td className="px-3 py-4 text-right text-slate-200 border-l border-slate-800/50">{formatador(row.__pedido)}</td>
        <td className="px-3 py-4 text-right text-emerald-400">{formatador(row.__faturado)}</td>
        <td className="px-3 py-4 text-right text-rose-400">{formatador(row.__corte)}</td>
        <td className="px-3 py-4 text-right text-amber-500 font-bold">{formatador(row.__carteira)}</td>
        <td className="px-3 py-4 text-right text-fuchsia-400 font-bold border-l border-slate-800/50 bg-fuchsia-950/10 cursor-help" title="Fórmula: Σ MAX(0, Média(M-3 a M-1) - Realizado MTD) por Razão Social.">
          {formatador(row.__previsao)}
        </td>
        <td className="px-3 py-4 text-right text-white font-black bg-slate-800/30 cursor-help" title="Fórmula: Pedido Implantado MTD + Previsão de Entrada Oculta.">
          {formatador(row.__projecao)}
        </td>
        <td className={`px-3 py-4 text-right font-bold ${row.__gap > 0 ? 'text-emerald-400' : 'text-rose-400'}`} title="Fórmula: Projeção Fim do Mês - Meta S&OP.">
          {row.__gap > 0 ? '+' : ''}{formatador(row.__gap)}
        </td>
        <td className="px-4 py-4 text-right border-l border-slate-800/50 cursor-help" title="Fórmula: Demanda Futura (Carteira + Previsão) - Estoque Atual.">
          <span className={`px-2 py-1 rounded text-[10px] tracking-wider whitespace-nowrap ${acao.cor}`}>{acao.text}</span>
        </td>
      </tr>

      {expandido && (
        <tr className="bg-slate-950/90 shadow-inner">
          <td colSpan={12} className="p-6 border-b border-slate-800">
             <div className="flex flex-col gap-3">
                <h4 className="text-[10px] font-black uppercase tracking-widest text-fuchsia-400 flex items-center gap-2"><Target className="w-4 h-4"/> Detalhamento da Demanda Oculta de Clientes Leais (Média Histórica M-3 a M-1)</h4>
                {carregando ? (
                   <div className="text-xs text-slate-400 flex gap-2"><RefreshCw className="w-4 h-4 animate-spin"/> Mapeando Data Lake (MTRIX & PMR)...</div>
                ) : clientes.length === 0 ? (
                   <div className="text-xs text-slate-600">Nenhum cliente qualificado (Frequência Mínima 4/6) com demanda pendente.</div>
                ) : (
                   <div className="border border-slate-800 rounded-lg">
                      <table className="w-full text-left text-xs whitespace-nowrap">
                         <thead className="bg-slate-900 text-[9px] text-slate-500 uppercase">
                            <tr>
                               <th className="px-4 py-2 border-b border-slate-800">Regional</th>
                               <th className="px-4 py-2 border-b border-slate-800">Razão Social</th>
                               <th className="px-4 py-2 text-right border-b border-slate-800" title="Quantidade de meses com pedidos nos últimos 6 meses">Freq. Compras (Últ 6m)</th>
                               <th className="px-4 py-2 text-right border-b border-slate-800" title="Média de compras dos meses M-3 a M-1">Média Hist. M-3 a M-1 ({visao})</th>
                               <th className="px-4 py-2 text-right border-b border-slate-800">Realizado MTD ({visao})</th>
                               <th className="px-4 py-2 text-right text-fuchsia-400 border-b border-slate-800" title="Zera automaticamente se MTD bater 60% da Média Histórica">Previsão Faltante ({visao})</th>
                               <th className="px-4 py-2 text-right text-sky-400 border-b border-slate-800" title="Estoque Físico no Distribuidor (S3)">Estoque MTRIX (Cx)</th>
                               <th className="px-4 py-2 text-right text-purple-400 border-b border-slate-800" title="Prazo Médio de Recebimento (S3)">PMR (Dias)</th>
                            </tr>
                         </thead>
                         <tbody className="divide-y divide-slate-800/50">
                            {clientes.map((c, idx) => (
                               <tr key={idx} className="hover:bg-slate-800/30 text-slate-300 transition-colors">
                                  <td className="px-4 py-2 font-mono text-slate-500">{c.regional}</td>
                                  <td className="px-4 py-2 font-bold">{c.razaosocial}</td>
                                  <td className="px-4 py-2 text-right font-mono text-slate-400">{c.freq_meses}/6</td>
                                  <td className="px-4 py-2 text-right text-indigo-300">{formatFinV2(c.media_vol || 0)}</td>
                                  <td className="px-4 py-2 text-right">{formatFinV2(c.mtd_vol || 0)}</td>
                                  <td className="px-4 py-2 text-right font-black text-fuchsia-400">
                                     {(c.previsao_vol || 0) > 0 ? `+ ${formatFinV2(c.previsao_vol)}` : <span className="text-emerald-500">Atendido ({'>='} 60%)</span>}
                                  </td>
                                  {/* BLINDAGEM DA UX AQUI */}
                                  <td className="px-4 py-2 text-right">
                                    {c.estoque_mtrix === "sem mtrix" ? (
                                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800/50 text-slate-500 italic border border-slate-700/50">
                                        Sem MTRIX
                                      </span>
                                    ) : (
                                      <span className="text-sky-400 font-bold">
                                        {formatVol(Number(c.estoque_mtrix))}
                                      </span>
                                    )}
                                  </td>
                                  <td className="px-4 py-2 text-right">
                                    <span className={`font-bold ${c.pmr > 60 ? 'text-rose-400' : 'text-purple-400'}`}>
                                      {c.pmr > 0 ? `${c.pmr} dias` : '--'}
                                    </span>
                                  </td>
                               </tr>
                            ))}
                         </tbody>
                      </table>
                   </div>
                )}
             </div>
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
  
  const [categoriaSel, setCategoriaSel] = useState('Todas');
  const [segmentoSel, setSegmentoSel] = useState('Todos');
  const [clienteSel, setClienteSel] = useState('Todos');
  const [buscaSku, setBuscaSku] = useState('');
  
  // MÁQUINA DO TEMPO EM CASCATA DIÁRIA
  const dataHojeFC = obterDataBrasilia();
  const [selDia, setSelDia] = useState<string>(String(dataHojeFC.getDate()).padStart(2, '0'));
  const [selMes, setSelMês] = useState<string>(String(dataHojeFC.getMonth() + 1).padStart(2, '0'));
  const [selAno, setSelAno] = useState<string>(String(dataHojeFC.getFullYear()));

  const dataSnapshot = useMemo(() => {
    return `${selAno}-${selMes}-${selDia}`;
  }, [selDia, selMes, selAno]);

  const listaDiasDisponiveis = useMemo(() => {
    const numDias = new Date(Number(selAno), Number(selMes), 0).getDate();
    return Array.from({ length: numDias }, (_, i) => String(i + 1).padStart(2, '0'));
  }, [selMes, selAno]);
  
  const [paresCatSeg, setParesCatSeg] = useState<any[]>([]);
  const [listaClientes, setListaClientes] = useState<string[]>([]);

  const [graficosKpi, setGraficosKpi] = useState<any[]>([]);
  const [skus, setSkus] = useState<any[]>([]);
  const [kpisGerais, setKpisGerais] = useState<any>({});
  const [carregando, setCarregando] = useState(false);
  
  // POLLING DO STATUS DO BACKEND
  const [isSyncing, setIsSyncing] = useState(false);
  const [pipelineLog, setPipelineLog] = useState<string>('');
  const pollInterval = useRef<ReturnType<typeof setInterval> | null>(null);

  const [sortConfig, setSortConfig] = useState({ key: '__risco_falta', direction: 'desc' });
  const formatador = visao === 'caixas' ? formatVol : formatFin;

  useEffect(() => {
    const fetchFiltros = async () => {
      try {
        const res = await axios.get(`/api/v1/kpis/filtros-auditoria?lente=${lente}`);
        setParesCatSeg(res.data.pares_cat_seg || []);
        setListaClientes(res.data.clientes || []);
        setMesesDisponiveis(res.data.meses_disponiveis || []);
        
        if (res.data.meses_disponiveis?.length > 0) {
          const mesAtualStr = `${String(new Date().getMonth() + 1).padStart(2, '0')}/${new Date().getFullYear()}`;
          if (res.data.meses_disponiveis.includes(mesAtualStr)) {
            setMesesSelecionados([mesAtualStr]);
          } else {
            setMesesSelecionados([res.data.meses_disponiveis[0]]);
          }
        } 
      } catch (err) {}
    };
    fetchFiltros();
  }, [lente]);

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
      params.append('data_snapshot', dataSnapshot); 

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
        setSortConfig({ key: '__risco_falta', direction: 'desc' });
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

  useEffect(() => { carregarDadosCore(); }, [lente, visao, categoriaSel, segmentoSel, clienteSel, mesesSelecionados, dataSnapshot]);

  const monitorarPipeline = () => {
    if (pollInterval.current) clearInterval(pollInterval.current);
    
    pollInterval.current = setInterval(async () => {
      try {
        const res = await axios.get('/api/v1/admin/pipeline/status');
        
        if (res.data.logs && res.data.logs.length > 0) {
          const ultimoLog = res.data.logs[res.data.logs.length - 1];
          setPipelineLog(ultimoLog);
          
          if (!res.data.is_running || ultimoLog.includes('🏁')) {
            if (pollInterval.current) clearInterval(pollInterval.current);
            setIsSyncing(false);
            setPipelineLog('');
            
            const hojeAtual = obterDataBrasilia();
            setSelAno(String(hojeAtual.getFullYear()));
            setSelMês(String(hojeAtual.getMonth() + 1).padStart(2, '0'));
            setSelDia(String(hojeAtual.getDate()).padStart(2, '0'));

            setTimeout(() => carregarDadosCore(), 500);
          }
        }
      } catch (e) {
        if (pollInterval.current) clearInterval(pollInterval.current);
        setIsSyncing(false);
      }
    }, 2000); 
  };

  const handleSyncAll = () => {
    if (isSyncing) return; 
    setIsSyncing(true);
    setPipelineLog('Acionando Maestro Noturno...');
    setTimeout(monitorarPipeline, 500);
    axios.post('/api/v1/kpis/sync-all').catch(() => {
        console.warn("Monitoramento em background ativo.");
    });
  };

  useEffect(() => {
    return () => { if (pollInterval.current) clearInterval(pollInterval.current); };
  }, []);

  const requestSort = (key: string) => {
    let direction = 'desc';
    if (sortConfig.key === key && sortConfig.direction === 'desc') direction = 'asc';
    setSortConfig({ key, direction });
  };

  const enrichedSkus = useMemo(() => {
    if (lente !== 'estoque') return skus;
    return skus.map((row: any) => {
      const meta = visao === 'caixas' ? (row.meta_mes_vol || 0) : (row.meta_mes_rs || 0);
      const pedido = visao === 'caixas' ? (row.vendas_mtd_vol || 0) : (row.vl_pedido || 0);
      const faturado = visao === 'caixas' ? (row.faturado_mtd_vol || 0) : (row.vl_faturado || 0);
      const corte = visao === 'caixas' ? (row.corte_mtd_vol || 0) : (row.vl_corte || 0);
      const estoque = visao === 'caixas' ? (row.estoque_atual || 0) : (row.estoque_rs || 0);
      const carteira = visao === 'caixas' ? (row.carteira_aberto_vol || 0) : (row.carteira_aberto_rs || 0);
      const previsao = visao === 'caixas' ? (row.previsao_entrada_vol || 0) : (row.previsao_entrada_rs || 0);
      const projecao = visao === 'caixas' ? (row.projecao_fim_mes_vol || 0) : (row.projecao_fim_mes_rs || 0);
      const gap = visao === 'caixas' ? (row.gap_meta_vol || 0) : (row.gap_meta_rs || 0);
      
      const atingimento = meta > 0 ? (pedido / meta) * 100 : (pedido > 0 ? 100 : 0);
      const demanda_futura = carteira + previsao;
      const risco_falta = demanda_futura - estoque;
      const sobra = estoque > demanda_futura ? (estoque - demanda_futura) : 0;

      return { 
        ...row, 
        __estoque: estoque, __meta: meta, __pedido: pedido, __faturado: faturado,
        __corte: corte, __carteira: carteira, __previsao: previsao, __projecao: projecao,
        __gap: gap, __atingimento: atingimento, __demanda_futura: demanda_futura,
        __risco_falta: risco_falta, __sobra: sobra
      };
    });
  }, [skus, visao, lente]);

  const sortedSkus = useMemo(() => {
    let baseData = lente === 'estoque' ? enrichedSkus : skus;
    let sortable = [...baseData].filter(r => r.descricao?.toLowerCase().includes(buscaSku.toLowerCase()) || r.sku?.toLowerCase().includes(buscaSku.toLowerCase()));
    
    sortable.sort((a, b) => {
      if (a[sortConfig.key] < b[sortConfig.key]) return sortConfig.direction === 'asc' ? -1 : 1;
      if (a[sortConfig.key] > b[sortConfig.key]) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
    return sortable;
  }, [enrichedSkus, skus, sortConfig, buscaSku, lente]);

  const handleExportCSV = () => {
    if (!sortedSkus || sortedSkus.length === 0) return;

    let headers: string[] = [];
    let rows: any[] = [];

    if (lente === 'estoque') {
      headers = [
        "SKU", "Descrição", "Categoria", `Estoque Fábrica (${visao})`, 
        `Meta S&OP (${visao})`, `Pedido (${visao})`, `Faturado (${visao})`, 
        `Corte (${visao})`, `Carteira em Aberto (${visao})`, 
        `Previsão de Entrada (${visao})`, `Projeção Fim do Mês (${visao})`, `Gap vs Meta (${visao})`
      ];
      rows = sortedSkus.map((d: any) => [
        d.sku, d.descricao, d.categoria, d.__estoque || 0, d.__meta || 0, d.__pedido || 0,
        d.__faturado || 0, d.__corte || 0, d.__carteira || 0, d.__previsao || 0, d.__projecao || 0, d.__gap || 0
      ]);
    } 
    else if (lente === 'sellout') {
      headers = [
        "SKU", "Descrição", "Categoria", "Segmento", 
        "Realizado Sell-out (Cx)", "Meta IA (Cx)", "Meta S&OP (Cx)", 
        "Estoque Canal (Cx)", "% MAPE IA", "% MAPE S&OP", "FVA (Melhoria %)"
      ];
      rows = sortedSkus.map((d: any) => [
        d.sku, d.descricao, d.categoria, d.segmento,
        d.vol_real || 0, d.vol_ia_congelado || 0, d.vol_comercial_congelado || 0,
        d.estoque_canal || 0, d.mape_ia || 0, d.mape_comercial || 0, d.fva || 0
      ]);
    }
    else if (lente === 'kpis') {
      headers = [
        "SKU", "Descrição", "Categoria", "Segmento", 
        `Meta IA (${visao})`, `Meta S&OP (${visao})`, `Realizado MTD (${visao})`, 
        `Gap IA (${visao})`, `Gap S&OP (${visao})`, "% MAPE IA", "% MAPE S&OP"
      ];
      rows = sortedSkus.map((d: any) => [
        d.sku, d.descricao, d.categoria, d.segmento,
        d.val_meta_ia || 0, d.val_meta_hum || 0, d.val_real || 0,
        d.gap_ia || 0, d.gap_humano || 0, d.mape_ia || 0, d.mape_humano || 0
      ]);
    }

    const csvContent = [
      headers.join(";"),
      ...rows.map(row => row.map((val: any) => typeof val === 'number' ? val.toString().replace('.', ',') : `"${val || ''}"`).join(";"))
    ].join("\n");

    const blob = new Blob(["\uFEFF" + csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `Extracao_${lente.toUpperCase()}_${visao.toUpperCase()}_${new Date().toISOString().slice(0,10)}.csv`;
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="p-6 bg-slate-950 min-h-screen flex-1 w-full text-slate-100 font-sans">
      
      {/* HEADER PRINCIPAL */}
      <div className="w-full flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 bg-slate-900 p-5 rounded-2xl border border-slate-800 mb-6 shadow-xl">
        <div>
          <h1 className="text-2xl font-black text-white tracking-tight flex items-center gap-3">
            <Activity className="text-indigo-500 w-7 h-7" /> Torre de Controle S&OP
          </h1>
          <p className="text-xs text-slate-400 mt-1 font-medium">Cockpit Executivo: Consequências Financeiras e Mapeamento de Erro</p>
        </div>

        <div className="flex flex-col md:flex-row gap-3 items-center">
          <div className="flex items-center gap-3 bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner mr-2">
            <div className="flex flex-col items-end px-3">
              <span className="text-[9px] font-black uppercase tracking-widest text-slate-500 mb-1">Última Atualização</span>
              <div className="flex items-center gap-1.5 text-xs font-mono text-emerald-400">
                <Clock className="w-3 h-3" />
                {kpisGerais.ultima_atualizacao || 'Ao Vivo'}
              </div>
            </div>
            <div className="h-6 w-px bg-slate-800"></div>
            {isSyncing ? (
               <div className="flex items-center px-4 py-2 bg-slate-900 text-amber-400 rounded-lg font-black tracking-wider text-[10px] uppercase h-full border border-amber-900/30">
                  <RefreshCw className="w-3.5 h-3.5 mr-2 animate-spin" />
                  <span className="max-w-[180px] truncate font-mono" title={pipelineLog}>{pipelineLog || 'Processando...'}</span>
               </div>
            ) : (
               <button onClick={handleSyncAll} className="flex items-center px-4 py-2 bg-slate-800 hover:bg-slate-700 text-sky-400 rounded-lg font-black tracking-wider transition-all text-[10px] uppercase h-full">
                 <Terminal className="w-3.5 h-3.5 mr-2" /> Atualizar Dados
               </button>
            )}
          </div>

          <button onClick={handleExportCSV} className="flex items-center px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl font-black tracking-wider transition-all shadow-lg shadow-emerald-900/20 text-[10px] uppercase">
            <Download className="w-4 h-4 mr-2" /> Extrair CSV
          </button>

          <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner">
            <button onClick={() => setVisao('caixas')} className={`px-4 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${visao === 'caixas' ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><Package className="w-4 h-4"/> Caixas</button>
            <button onClick={() => setVisao('financeiro')} className={`px-4 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${visao === 'financeiro' ? 'bg-emerald-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><DollarSign className="w-4 h-4"/> Reais</button>
          </div>
          
          <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner">
            <button onClick={() => setLente('kpis')} className={`px-4 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${lente === 'kpis' ? 'bg-rose-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><Target className="w-4 h-4" /> Desvios MTD</button>
            <button onClick={() => setLente('estoque')} className={`px-4 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${lente === 'estoque' ? 'bg-amber-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><Factory className="w-4 h-4" /> Riscos Estoque</button>
            <button onClick={() => setLente('sellout')} className={`px-4 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${lente === 'sellout' ? 'bg-teal-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}><ShoppingCart className="w-4 h-4" /> MTRIX</button>
          </div>
        </div>
      </div>

      {/* FILTROS GLOBAIS */}
      <div className="w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        
        {lente === 'estoque' ? (
          <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
            <label className="text-[10px] font-black uppercase tracking-widest text-emerald-500 mb-2 flex items-center gap-2">
              <Calendar className="w-3 h-3"/> Posição (Snapshot)
            </label>
            <div className="flex gap-1.5">
              <select value={selDia} onChange={(e) => setSelDia(e.target.value)} className="w-1/3 bg-slate-950 border border-slate-800 p-2 text-xs font-mono rounded text-emerald-400 focus:outline-none">
                {listaDiasDisponiveis.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
              <select value={selMes} onChange={(e) => setSelMês(e.target.value)} className="w-1/3 bg-slate-950 border border-slate-800 p-2 text-xs font-mono rounded text-emerald-400 focus:outline-none">
                {['01','02','03','04','05','06','07','08','09','10','11','12'].map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={selAno} onChange={(e) => setSelAno(e.target.value)} className="w-1/3 bg-slate-950 border border-slate-800 p-2 text-xs font-mono rounded text-emerald-400 focus:outline-none">
                {['2025', '2026', '2027'].map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>
        ) : (
          <div className="lg:col-span-1"><ExcelTreeDropdown titulo="Horizonte S&OP" options={mesesDisponiveis} selected={mesesSelecionados} onChange={setMesesSelecionados} /></div>
        )}
        
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

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex justify-between">Pesquisar SKU <span className="text-indigo-400 font-bold">{sortedSkus.length} Itens</span></label>
          <div className="relative"><Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" /><input type="text" value={buscaSku} onChange={(e) => setBuscaSku(e.target.value)} placeholder="Código ou nome..." className="w-full bg-slate-950 border border-slate-800 text-sm py-2 pl-9 pr-3 rounded-lg text-slate-300 focus:outline-none" /></div>
        </div>
      </div>

      {/* GRÁFICOS */}
      {lente === 'kpis' || lente === 'sellout' ? (
        <div className="w-full grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-indigo-500 shadow-md"><div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">% WMAPE IA</div><div className="text-3xl font-black text-rose-400">{formatPct(kpisGerais.wmape_ia || 0)}</div></div>
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-sky-500 shadow-md"><div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">{lente === 'kpis' ? '% WMAPE S&OP' : 'Erro Escoamento'}</div><div className="text-3xl font-black text-rose-400">{formatPct(kpisGerais.wmape_comercial || 0)}</div></div>
          <div className={`bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md border-l-4 ${(kpisGerais.fva || 0) >= 0 ? 'border-l-emerald-500' : 'border-l-rose-500'}`}><div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2 flex justify-between">FVA (Melhoria) {(kpisGerais.fva || 0) >= 0 ? <TrendingUp className="w-4 h-4 text-emerald-500"/> : <TrendingDown className="w-4 h-4 text-rose-500"/>}</div><div className={`text-3xl font-black ${(kpisGerais.fva || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{formatPct(kpisGerais.fva || 0)}</div></div>
          <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
            <div className="text-[10px] font-black uppercase text-slate-400 tracking-wider mb-2 flex justify-between border-b border-slate-700 pb-1">
              <span>{lente === 'sellout' ? 'BIAS Canal' : 'BIAS S&OP'}</span><span>{lente === 'sellout' ? 'Dias Cob.' : 'BIAS IA'}</span>
            </div>
            <div className="flex justify-between items-center mt-2">
              <span className={`text-xl font-black ${(kpisGerais.bias_humano || 0) > 0 ? 'text-amber-400' : 'text-rose-400'}`}>{formatPct(kpisGerais.bias_humano || 0)}</span>
              <span className={`text-xl font-black ${(kpisGerais.bias_ia || 0) > 0 ? 'text-amber-400' : 'text-rose-400'}`}>{lente === 'sellout' ? `${kpisGerais.cobertura_media_canal || 0} D` : formatPct(kpisGerais.bias_ia || 0)}</span>
            </div>
          </div>
        </div>
      ) : null}
      
      {lente === 'kpis' ? (
        <div className="w-full grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
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
        <div className="w-full grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-rose-500 shadow-md relative overflow-hidden"><h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2"><TrendingDown className="w-4 h-4 text-rose-500"/> Risco Produtivo (Projeção)</h3><p className="text-3xl font-black text-white z-10 relative">{visao === 'financeiro' ? formatFin(kpisGerais.total_ruptura_rs || 0) : formatVol(kpisGerais.total_ruptura_vol || 0) + ' Cx'}</p></div>
          <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-amber-500 shadow-md relative overflow-hidden"><h3 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-2 flex items-center gap-2"><Package className="w-4 h-4 text-amber-500"/> Capital Imobilizado (Projeção)</h3><p className="text-3xl font-black text-white z-10 relative">{visao === 'financeiro' ? formatFin(kpisGerais.total_sobra_rs || 0) : formatVol(kpisGerais.total_sobra_vol || 0) + ' Cx'}</p></div>
        </div>
      ) : null}

      {/* TABELAS - LIVRE DE BARRA DE SCROLL DE CONTAINERS */}
      <div className="w-full bg-slate-900 rounded-2xl border border-slate-800 shadow-2xl flex flex-col mb-10">
        {carregando ? (
          <div className="p-20 flex justify-center items-center gap-3 text-indigo-400 font-bold uppercase text-xs"><RefreshCw className="w-6 h-6 animate-spin" /> Processando Tabelas...</div>
        ) : lente === 'estoque' ? (
          
          <table className="w-full text-left whitespace-nowrap table-auto">
            <thead className="bg-slate-950">
              <tr className="bg-slate-900 border-b border-slate-800">
                <th colSpan={4} className="px-6 py-2 text-center text-[10px] font-black tracking-widest text-slate-500 uppercase border-r border-slate-800/50">📦 Planejamento & Fábrica</th>
                <th colSpan={4} className="px-6 py-2 text-center text-[10px] font-black tracking-widest text-emerald-500/70 uppercase border-r border-slate-800/50">🤝 Realidade Comercial (MTD)</th>
                <th colSpan={4} className="px-6 py-2 text-center text-[10px] font-black tracking-widest text-fuchsia-400/80 uppercase">🔮 Projeção Bottom-Up & Ação</th>
              </tr>
              <tr className="bg-slate-950 text-slate-400 text-[10px] font-black uppercase tracking-widest border-b border-slate-800">
                <SortableHeader field="descricao" label="Produto" currentSort={sortConfig} requestSort={requestSort} className="px-6 text-left" />
                <SortableHeader field="__estoque" label={`Estoque Fáb. (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-slate-300 text-right bg-slate-900/40" />
                <SortableHeader field="__meta" label={`Meta S&OP (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-indigo-400 text-right" />
                <SortableHeader field="__atingimento" label="Atingido %" currentSort={sortConfig} requestSort={requestSort} className="text-right" />
                
                <SortableHeader field="__pedido" label={`Pedido (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-white text-right border-l border-slate-800/50" />
                <SortableHeader field="__faturado" label={`Faturado (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-emerald-400 text-right" />
                <SortableHeader field="__corte" label={`Corte (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-rose-400 text-right" />
                <SortableHeader field="__carteira" label={`Carteira Aberto (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-amber-500 text-right" />
                
                <SortableHeader field="__previsao" label={`Prev. Entrada (${visao})`} currentSort={sortConfig} requestSort={requestSort} className="text-fuchsia-400 text-right border-l border-slate-800/50 bg-fuchsia-950/10" />
                <SortableHeader field="__projecao" label={`Projeção Fim do Mês`} currentSort={sortConfig} requestSort={requestSort} className="text-white text-right bg-slate-800/30" />
                <SortableHeader field="__gap" label={`Gap vs Meta`} currentSort={sortConfig} requestSort={requestSort} className="text-slate-300 text-right" />
                <SortableHeader field="__risco_falta" label="Alerta de Supply" currentSort={sortConfig} requestSort={requestSort} className="text-right border-l border-slate-800/50" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-xs">
              {sortedSkus.map((row) => <RowRiscoDrillDown key={row.sku} row={row} visao={visao} formatador={formatador} dataSnapshot={dataSnapshot} />)}
            </tbody>
            <tfoot className="bg-slate-950 font-black text-white border-t-2 border-slate-700 text-xs">
              <tr>
                <td className="px-6 py-4 uppercase tracking-widest text-indigo-400">Totais da Visão ({visao})</td>
                <td className="px-3 py-4 text-right bg-slate-900/40">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__estoque || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-indigo-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__meta || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-slate-500">-</td>
                
                <td className="px-3 py-4 text-right border-l border-slate-800/50">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__pedido || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-emerald-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__faturado || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-rose-400">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__corte || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-amber-500">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__carteira || 0), 0))}</td>
                
                <td className="px-3 py-4 text-right text-fuchsia-400 border-l border-slate-800/50 bg-fuchsia-950/10">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__previsao || 0), 0))}</td>
                <td className="px-3 py-4 text-right bg-slate-800/30">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__projecao || 0), 0))}</td>
                <td className="px-3 py-4 text-right text-slate-300">{formatador(sortedSkus.reduce((sum, r) => sum + (r.__gap || 0), 0))}</td>
                <td className="px-4 py-4 text-right border-l border-slate-800/50 text-slate-500">-</td>
              </tr>
            </tfoot>
          </table>

        ) : lente === 'sellout' ? (
           <table className="w-full text-left whitespace-nowrap table-auto">
             <thead className="bg-slate-950">
               <tr className="text-slate-400 border-b border-slate-800 text-[10px] font-black uppercase tracking-widest">
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
                   <td className="px-4 py-3.5 text-right font-black text-white">{formatVol(row.vol_real || 0)}</td>
                   <td className="px-4 py-3.5 text-right font-semibold text-indigo-400 bg-indigo-950/10">{formatVol(row.vol_ia_congelado || 0)}</td>
                   <td className="px-4 py-3.5 text-right font-semibold text-sky-400 bg-sky-950/10">{formatVol(row.vol_comercial_congelado || 0)}</td>
                   <td className="px-4 py-3.5 text-right font-semibold text-amber-400">{formatVol(row.estoque_canal || 0)}</td>
                   <td className="px-4 py-3.5 text-right font-mono font-bold text-rose-400 border-l border-slate-800 bg-slate-950/40">{formatPct(row.mape_ia || 0)}</td>
                   <td className="px-4 py-3.5 text-right font-mono font-bold text-rose-400 bg-slate-950/40">{formatPct(row.mape_comercial || 0)}</td>
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
           <table className="w-full text-left whitespace-nowrap table-auto">
             <thead className="bg-slate-950">
               <tr className="text-slate-400 border-b border-slate-800 text-[10px] font-black uppercase tracking-widest">
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
               {sortedSkus.map((row) => <RowSKUDrillDown key={row.sku} row={row} visao={visao} mesesSelecionados={mesesSelecionados} formatador={formatador} dataSnapshot={dataSnapshot} />)}
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