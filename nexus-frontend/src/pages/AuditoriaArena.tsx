import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  ShieldCheck, Layers, ShoppingCart, TrendingUp, TrendingDown, 
  Target, Activity, Search, RefreshCw, BarChart2
} from 'lucide-react';
import { 
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, 
  Tooltip, ResponsiveContainer, ZAxis, LineChart, Line, ReferenceLine
} from 'recharts';

const formatVol = (val: number) => new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 }).format(val || 0);
const formatPct = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(val || 0);

// Tooltip Dinâmico Customizado que exibe os Nomes dos SKUs ao passar o mouse na Bolha do Gráfico
const CustomScatterTooltip = ({ active, payload }: any) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    return (
      <div className="bg-slate-900 border border-slate-700 p-4 rounded-xl shadow-2xl z-50 max-w-sm">
        <p className="font-bold text-white leading-tight mb-1">{data.descricao || 'Item Sem Descrição'}</p>
        <p className="text-[10px] font-mono text-slate-500 mb-2">CÓDIGO SKU: {data.sku}</p>
        <div className="space-y-1 text-xs border-t border-slate-800 pt-2">
          <p className="text-slate-300 flex justify-between gap-4">Volume Acumulado: <span className="font-bold text-white">{formatVol(data.vol_real)}</span></p>
          <p className="text-slate-300 flex justify-between gap-4">Acurácia Registrada: <span className="font-bold text-indigo-400">{formatPct(data.acc_ia)}</span></p>
          {data.fva !== 0 && (
            <p className="text-slate-300 flex justify-between gap-4">FVA Gerado: <span className={`font-bold ${data.fva >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{formatPct(data.fva)}</span></p>
          )}
        </div>
      </div>
    );
  }
  return null;
};

export default function AuditoriaArena() {
  const [lente, setLente] = useState<'sellin' | 'sellout'>('sellin');
  
  // Limitação de escopo homologada: Apenas a partir do mês 06/2026 de uso efetivo humano
  const [mesesDisponiveis] = useState(['06/2026', '07/2026', '08/2026', '09/2026', '10/2026']);
  const [mesesSelecionados, setMesesSelecionados] = useState<string[]>(['06/2026']);
  
  const [categoriaSel, setCategoriaSel] = useState('Todas');
  const [segmentoSel, setSegmentoSel] = useState('Todos');
  const [buscaSku, setBuscaSku] = useState('');
  
  const [listaCategorias, setListaCategorias] = useState<string[]>([]);
  const [listaSegmentos, setListaSegmentos] = useState<string[]>([]);

  const [kpisGlobais, setKpisGlobais] = useState<any>({});
  const [cronologia, setCronologia] = useState<any[]>([]);
  const [skusTabela, setSkusTabela] = useState<any[]>([]);
  const [carregando, setCarregando] = useState(false);

  const toggleMes = (mes: string) => {
    if (mesesSelecionados.includes(mes) && mesesSelecionados.length > 1) {
      setMesesSelecionados(mesesSelecionados.filter(m => m !== mes));
    } else if (!mesesSelecionados.includes(mes)) {
      setMesesSelecionados([...mesesSelecionados, mes]);
    }
  };

  const carregarDadosAuditoria = async () => {
    setCarregando(true);
    try {
      const params = new URLSearchParams();
      params.append('lente', lente);
      params.append('categoria', categoriaSel);
      params.append('segmento', segmentoSel);
      mesesSelecionados.forEach(m => params.append('meses_horizonte', m));

      const res = await axios.get(`/api/v1/kpis/auditoria-dinamica?${params.toString()}`);
      
      setKpisGlobais(res.data.kpis_globais || {});
      setCronologia(res.data.cronologia || []);
      setSkusTabela(res.data.tabela_skus || []);
      
      if (res.data.filtros_cascata) {
        setListaCategorias(res.data.filtros_cascata.categorias || []);
        setListaSegmentos(res.data.filtros_cascata.segmentos || []);
      }
    } catch (err) {
      console.error("Erro na comunicação com a API de KPIs Nexus:", err);
    } finally {
      setCarregando(false);
    }
  };

  useEffect(() => {
    carregarDadosAuditoria();
  }, [lente, categoriaSel, segmentoSel, mesesSelecionados]);

  // Filtro local reativo que respeita digitação por SKU ou descrição do Portfólio
  const dadosFiltradosLocais = skusTabela.filter(row => 
    row.descricao?.toLowerCase().includes(buscaSku.toLowerCase()) || 
    row.sku?.toLowerCase().includes(buscaSku.toLowerCase())
  );

  return (
    <div className="p-6 bg-slate-950 min-h-screen text-slate-100 font-sans">
      
      {/* HEADER PANORÂMICO */}
      <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 bg-slate-900 p-5 rounded-2xl border border-slate-800 mb-6 shadow-xl">
        <div>
          <h1 className="text-2xl font-black text-white tracking-tight flex items-center gap-3">
            <ShieldCheck className="text-indigo-500 w-7 h-7" /> Arena de Aderência e Auditoria S&OP
          </h1>
          <p className="text-xs text-slate-400 mt-1 font-medium">Lente {lente === 'sellin' ? 'Fábrica' : 'Canal Indireto'} • Análise Crítica Frozen via <span className="text-indigo-400 font-bold">Lag 2 (M-2)</span></p>
        </div>

        <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner">
          <button onClick={() => setLente('sellin')} className={`px-5 py-2.5 text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'sellin' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}>
            <Layers className="w-4 h-4" /> Fábrica (Sell-In)
          </button>
          <button onClick={() => setLente('sellout')} className={`px-5 py-2.5 text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'sellout' ? 'bg-teal-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}>
            <ShoppingCart className="w-4 h-4" /> Canal MTRIX (Sell-Out)
          </button>
        </div>
      </div>

      {/* SELETOR DE HORIZONTES */}
      <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 mb-6 flex flex-wrap items-center gap-3 shadow-md">
        <span className="text-xs font-black uppercase tracking-widest text-slate-500 flex items-center gap-1.5">
          <BarChart2 className="w-4 h-4 text-indigo-400" /> Horizontes Avaliados:
        </span>
        {mesesDisponiveis.map((mes) => (
          <button
            key={mes}
            onClick={() => toggleMes(mes)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all border ${mesesSelecionados.includes(mes) ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500 shadow-inner' : 'bg-slate-950 text-slate-400 border-slate-800 hover:bg-slate-800'}`}
          >
            {mes}
          </button>
        ))}
      </div>

      {/* FILTROS HIERÁRQUICOS ATIVOS */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Categoria</label>
          <select value={categoriaSel} onChange={(e) => setCategoriaSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500">
            <option value="Todas">Todas as Categorias</option>
            {listaCategorias.map(cat => <option key={cat} value={cat}>{cat}</option>)}
          </select>
        </div>

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Segmento</label>
          <select value={segmentoSel} onChange={(e) => setSegmentoSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500">
            <option value="Todos">Todos os Segmentos</option>
            {listaSegmentos.map(seg => <option key={seg} value={seg}>{seg}</option>)}
          </select>
        </div>

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Busca por SKU</label>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
            <input type="text" value={buscaSku} onChange={(e) => setBuscaSku(e.target.value)} placeholder="Mapear item na matriz..." className="w-full bg-slate-950 border border-slate-800 text-sm py-2 pl-9 pr-3 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500" />
          </div>
        </div>
      </div>

      {/* CARDS INDICADORES GLOBAIS CRÍTICAS */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-indigo-500 shadow-md">
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">Acurácia IA</div>
          <div className="text-3xl font-black text-white">{formatPct(kpisGlobais.acc_ia)}</div>
        </div>
        
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-sky-500 shadow-md">
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">{lente === 'sellin' ? 'Acurácia Humano' : 'Acurácia Escoamento'}</div>
          <div className="text-3xl font-black text-white">{formatPct(kpisGlobais.acc_comercial)}</div>
        </div>

        <div className={`bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md border-l-4 ${kpisGlobais.fva >= 0 ? 'border-l-emerald-500' : 'border-l-rose-500'}`}>
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2 flex justify-between">
            FVA (Valor Adicionado)
            {kpisGlobais.fva >= 0 ? <TrendingUp className="w-4 h-4 text-emerald-500"/> : <TrendingDown className="w-4 h-4 text-rose-500"/>}
          </div>
          <div className={`text-3xl font-black ${kpisGlobais.fva >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {lente === 'sellin' ? formatPct(kpisGlobais.fva) : 'Estável (Canal)'}
          </div>
        </div>

        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md border-l-4 border-l-amber-500">
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">{lente === 'sellin' ? 'BIAS Global' : 'Cobertura Média'}</div>
          <div className="text-3xl font-black text-slate-200">
            {lente === 'sellin' ? formatPct(kpisGlobais.bias_global) : `${kpisGlobais.cobertura_media_canal || 0} Dias`}
          </div>
        </div>
      </div>

      {/* BLOCO GRÁFICO RESGATADO */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
          <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
            <Target className="w-4 h-4 text-rose-500" /> Matriz de Ofensores (Volume Real vs Acurácia)
          </h3>
          <div className="h-[280px] w-full">
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" dataKey="vol_real" name="Volume" stroke="#64748b" tickFormatter={(v) => formatVol(v)} />
                <YAxis type="number" dataKey="acc_ia" name="Acurácia" stroke="#64748b" tickFormatter={(v) => formatPct(v)} domain={[0, 1]} />
                <ZAxis type="number" range={[60, 300]} />
                <Tooltip content={<CustomScatterTooltip />} cursor={{ strokeDasharray: '3 3' }} />
                <Scatter name="SKUs" data={dadosFiltradosLocais} fill="#6366f1" fillOpacity={0.6} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
          <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
            <Activity className="w-4 h-4 text-sky-400" /> Tracking Signal Continuo (Tendência de Viés)
          </h3>
          <div className="h-[280px] w-full">
            <ResponsiveContainer>
              <LineChart data={cronologia} margin={{ top: 10, right: 10, bottom: 10, left: -15 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="mes" stroke="#64748b" />
                <YAxis stroke="#64748b" tickFormatter={(v) => `${v}%`} />
                <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155' }} />
                <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={2} />
                <Line type="monotone" dataKey="bias_ia" name="IA (%)" stroke="#6366f1" strokeWidth={3} dot={{ r: 4 }} />
                {lente === 'sellin' && <Line type="monotone" dataKey="bias_comercial" name="Humano (%)" stroke="#0ea5e9" strokeWidth={3} dot={{ r: 4 }} />}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* TABELA DE ALTA GRANULARIDADE INTEGRADA */}
      <div className="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden shadow-2xl">
        {carregando ? (
          <div className="p-16 flex justify-center items-center gap-3 text-slate-500 font-bold uppercase tracking-widest text-xs">
            <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" /> Sincronizando Matrizes Relacionais PostgreSQL...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="bg-slate-950 text-slate-400 border-b border-slate-800 text-[10px] font-black uppercase tracking-widest">
                  <th className="px-6 py-4">Estrutura de Portfólio (SKU)</th>
                  <th className="px-4 py-4 text-right">{lente === 'sellin' ? 'Real Vendido' : 'Faturado (Sell-In)'}</th>
                  <th className="px-4 py-4 text-right">
                    <span>{lente === 'sellin' ? 'IA (Frozen)' : 'Escoado (Sell-Out)'}</span>
                  </th>
                  <th className="px-4 py-4 text-right">
                    {lente === 'sellin' ? 'Humano (Frozen)' : 'Estoque Canal (Últ. Dia)'}
                  </th>
                  {lente === 'sellout' && <th className="px-4 py-4 text-right">Giro (Cobertura)</th>}
                  <th className="px-4 py-4 text-right border-l border-slate-800">Acurácia IA</th>
                  {lente === 'sellin' && <th className="px-4 py-4 text-right">Acurácia Humano</th>}
                  <th className="px-6 py-4 text-right bg-slate-950/40">{lente === 'sellin' ? 'FVA (Valor Acc)' : 'Status Canal'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-xs">
                {dadosFiltradosLocais.map((row) => (
                  <tr key={row.sku} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-6 py-3.5">
                      <div className="flex flex-col">
                        <span className="font-bold text-slate-200 tracking-tight">{row.descricao || 'Item Sem Descrição'}</span>
                        <span className="text-[10px] text-slate-500 font-mono mt-0.5">{row.sku}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-right font-black text-white">{formatVol(row.vol_real)}</td>
                    <td className="px-4 py-3.5 text-right font-semibold text-indigo-400 bg-indigo-950/10">{formatVol(row.vol_ia_congelado)}</td>
                    
                    {lente === 'sellin' ? (
                      <td className="px-4 py-3.5 text-right font-semibold text-sky-400 bg-sky-950/10">{formatVol(row.vol_comercial_congelado)}</td>
                    ) : (
                      <td className="px-4 py-3.5 text-right font-bold text-amber-400 bg-amber-950/10">{formatVol(row.estoque_canal)}</td>
                    )}

                    {lente === 'sellout' && (
                      <td className={`px-4 py-3.5 text-right font-black ${row.dias_cobertura > 60 ? 'text-rose-400' : row.dias_cobertura < 15 ? 'text-rose-500' : 'text-emerald-400'}`}>
                        {row.dias_cobertura === 999 ? 'Sem Giro' : `${Math.round(row.dias_cobertura)} Dias`}
                      </td>
                    )}

                    <td className="px-4 py-3.5 text-right font-mono font-bold text-slate-300 border-l border-slate-800">{formatPct(row.acc_ia)}</td>
                    {lente === 'sellin' && <td className="px-4 py-3.5 text-right font-mono font-bold text-slate-300">{formatPct(row.acc_comercial)}</td>}
                    
                    <td className="px-6 py-3.5 text-right bg-slate-950/20">
                      {lente === 'sellin' ? (
                        row.fva > 0 ? (
                          <span className="bg-emerald-500/10 text-emerald-400 px-2.5 py-1 rounded font-black text-[10px]">+ {formatPct(row.fva)} FVA</span>
                        ) : row.fva < 0 ? (
                          <span className="bg-rose-500/10 text-rose-400 px-2.5 py-1 rounded font-black text-[10px]">{formatPct(row.fva)} FVA</span>
                        ) : (
                          <span className="text-slate-500 font-bold text-[10px]">Neutro</span>
                        )
                      ) : (
                        row.dias_cobertura > 60 ? (
                          <span className="bg-rose-500/10 text-rose-400 px-2.5 py-1 rounded font-black text-[10px]">Excesso</span>
                        ) : row.dias_cobertura < 15 ? (
                          <span className="bg-rose-600 text-white px-2.5 py-1 rounded font-black text-[10px] animate-pulse">Ruptura</span>
                        ) : (
                          <span className="bg-emerald-500/10 text-emerald-400 px-2.5 py-1 rounded font-black text-[10px]">Saudável</span>
                        )
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}