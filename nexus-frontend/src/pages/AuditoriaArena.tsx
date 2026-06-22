import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { 
  ShieldCheck, Layers, ShoppingCart, TrendingUp, TrendingDown, 
  Target, Activity, Search, RefreshCw, BarChart2
} from 'lucide-react';
import { 
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, 
  Tooltip, ResponsiveContainer, ZAxis, LineChart, Line, Legend, ReferenceLine
} from 'recharts';

const formatVol = (val: number) => new Intl.NumberFormat('pt-BR').format(Math.round(val || 0));
const formatPct = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1 }).format(val || 0);

export default function AuditoriaArena() {
  const [lente, setLente] = useState<'sellin' | 'sellout'>('sellin');
  const [mesesDisponiveis] = useState(['07/2026', '08/2026', '09/2026', '10/2026']);
  const [mesesSelecionados, setMesesSelecionados] = useState<string[]>(['07/2026']);
  
  // Estados para Filtros Hierárquicos alimentados pelo Backend
  const [categoriaSel, setCategoriaSel] = useState('Todas');
  const [segmentoSel, setSegmentoSel] = useState('Todos');
  const [buscaSku, setBuscaSku] = useState('');
  
  const [listaCategorias, setListaCategorias] = useState<string[]>([]);
  const [listaSegmentos, setListaSegmentos] = useState<string[]>([]);

  // Estados de Dados da API
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

  // CONEXÃO AXIOS COM O ROUTER REFORMULADO
  const carregarDadosAuditoria = async () => {
    setCarregando(true);
    try {
      const params = new URLSearchParams();
      params.append('lente', lente);
      params.append('categoria', categoriaSel);
      params.append('segmento', segmentoSel);
      mesesSelecionados.forEach(m => params.append('meses_horizonte', m));

      const res = await axios.get(`/api/v1/kpis/auditoria-dinamica?${params.toString()}`);
      
      const { kpis_globais, cronologia, tabela_skus, filtros_cascata } = res.data;
      
      setKpisGlobais(kpis_globais || {});
      setCronologia(cronologia || []);
      setSkusTabela(tabela_skus || []);
      
      if (filtros_cascata) {
        setListaCategorias(filtros_cascata.categorias || []);
        setListaSegmentos(filtros_cascata.segmentos || []);
      }
    } catch (err) {
      console.error("Erro na comunicação com a API de KPIs Nexus:", err);
    } finally {
      setCarregando(false);
    }
  };

  // Dispara recarga sempre que o usuário interagir com qualquer interruptor ou filtro
  useEffect(() => {
    carregarDadosAuditoria();
  }, [lente, categoriaSel, segmentoSel, mesesSelecionados]);

  // Filtro de busca textual local por SKU (não exige hit no banco)
  const skusFiltradosLocais = skusTabela.filter(row => 
    row.descricao?.toLowerCase().includes(buscaSku.toLowerCase()) || 
    row.sku?.toLowerCase().includes(buscaSku.toLowerCase())
  );

  return (
    <div className="p-6 bg-slate-950 min-h-screen text-slate-100 font-sans">
      
      {/* HEADER DE CONTROLO DE S&OP */}
      <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 bg-slate-900 p-5 rounded-2xl border border-slate-800 mb-6 shadow-xl">
        <div>
          <h1 className="text-2xl font-black text-white tracking-tight flex items-center gap-3">
            <ShieldCheck className="text-indigo-500 w-7 h-7" /> Arena de Auditoria e Aderência S&OP
          </h1>
          <p className="text-xs text-slate-400 mt-1 font-medium">Conexão em tempo real com o PostgreSQL • Análise de Frozen Horizonte <span className="text-indigo-400 font-bold">Lag 2 (M-2)</span></p>
        </div>

        <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner">
          <button onClick={() => { setCategoriaSel('Todas'); setSegmentoSel('Todos'); setLente('sellin'); }} className={`px-5 py-2.5 text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'sellin' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}>
            <Layers className="w-4 h-4" /> Fábrica (Sell-In)
          </button>
          <button onClick={() => { setCategoriaSel('Todas'); setSegmentoSel('Todos'); setLente('sellout'); }} className={`px-5 py-2.5 text-xs font-black uppercase tracking-wider rounded-lg transition-all flex items-center gap-2 ${lente === 'sellout' ? 'bg-teal-600 text-white shadow-md' : 'text-slate-500 hover:text-slate-300'}`}>
            <ShoppingCart className="w-4 h-4" /> Canal MTRIX (Sell-Out)
          </button>
        </div>
      </div>

      {/* SELETOR DE HORIZONTES TEMPORAIS ESPECÍFICOS */}
      <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 mb-6 flex flex-wrap items-center gap-3 shadow-md">
        <span className="text-xs font-black uppercase tracking-widest text-slate-500 flex items-center gap-1.5">
          <BarChart2 className="w-4 h-4 text-indigo-400" /> Horizontes Avaliados:
        </span>
        {mesesDisponiveis.map((mes, idx) => (
          <button
            key={mes}
            onClick={() => toggleMes(mes)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all border ${mesesSelecionados.includes(mes) ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/80 shadow-inner' : 'bg-slate-950 text-slate-400 border-slate-800 hover:bg-slate-800'}`}
          >
            {mes} {idx === 0 ? '(M0)' : `(M${idx})`}
          </button>
        ))}
      </div>

      {/* FILTROS HIERÁRQUICOS DINÂMICOS EM CASCATA */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Filtrar Categoria</label>
          <select value={categoriaSel} onChange={(e) => setCategoriaSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500">
            <option value="Todas">Todas as Categorias</option>
            {listaCategorias.map(cat => <option key={cat} value={cat}>{cat}</option>)}
          </select>
        </div>

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Filtrar Segmento</label>
          <select value={segmentoSel} onChange={(e) => setSegmentoSel(e.target.value)} className="w-full bg-slate-950 border border-slate-800 text-sm p-2 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500">
            <option value="Todos">Todos os Segmentos</option>
            {listaSegmentos.map(seg => <option key={seg} value={seg}>{seg}</option>)}
          </select>
        </div>

        <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 shadow-md">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 block">Pesquisa Local por SKU</label>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
            <input type="text" value={buscaSku} onChange={(e) => setBuscaSku(e.target.value)} placeholder="Nome ou SKU..." className="w-full bg-slate-950 border border-slate-800 text-sm py-2 pl-9 pr-3 rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500" />
          </div>
        </div>
      </div>

      {/* KPI INDICATORS DE EXIBIÇÃO CLÍNICA */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-indigo-500 shadow-md">
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">Acurácia IA (Lag 2)</div>
          <div className="text-3xl font-black text-white">{formatPct(kpisGlobais.acc_ia)}</div>
        </div>
        
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 border-l-4 border-l-sky-500 shadow-md">
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">{lente === 'sellin' ? 'Acurácia Humano' : 'Acurácia Canal'}</div>
          <div className="text-3xl font-black text-white">{formatPct(kpisGlobais.acc_comercial)}</div>
        </div>

        <div className={`bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md border-l-4 ${kpisGlobais.fva >= 0 ? 'border-l-emerald-500' : 'border-l-rose-500'}`}>
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2 flex justify-between">
            FVA (Valor Adicionado)
            {kpisGlobais.fva >= 0 ? <TrendingUp className="w-4 h-4 text-emerald-500"/> : <TrendingDown className="w-4 h-4 text-rose-500"/>}
          </div>
          <div className={`text-3xl font-black ${kpisGlobais.fva >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {lente === 'sellin' ? (kpisGlobais.fva > 0 ? `+${formatPct(kpisGlobais.fva)}` : formatPct(kpisGlobais.fva)) : 'Nulo (Canal)'}
          </div>
        </div>

        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md border-l-4 border-l-amber-500">
          <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">{lente === 'sellin' ? 'BIAS Global (Viés)' : 'Cobertura Média'}</div>
          <div className="text-3xl font-black text-slate-200">
            {lente === 'sellin' ? formatPct(kpisGlobais.bias_global) : `${kpisGlobais.cobertura_media_canal || 0} Dias`}
          </div>
        </div>
      </div>

      {/* BLOCO DE VISUALIZAÇÕES GRÁFICAS */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        
        {/* GRÁFICO DE OFENSORES S&OP */}
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
          <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
            <Target className="w-4 h-4 text-rose-500" /> Matriz de Ofensores (Impacto vs Erro)
          </h3>
          <div className="h-[280px] w-full">
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" dataKey="vol_real" name="Volume" stroke="#64748b" tickFormatter={(v) => `${v/1000}k`} />
                <YAxis type="number" dataKey="acc_ia" name="Acurácia" stroke="#64748b" tickFormatter={(v) => `${(v*100).toFixed(0)}%`} domain={[0, 1]} />
                <ZAxis type="number" range={[60, 300]} />
                <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155' }} />
                <Scatter name="SKUs" data={skusFiltradosLocais} fill="#6366f1" fillOpacity={0.6} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* GRÁFICO DE BIAS CONTINUO (TRACKING SIGNAL) */}
        <div className="bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md">
          <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
            <Activity className="w-4 h-4 text-sky-400" /> Tracking Signal (Tendência de Viés)
          </h3>
          <div className="h-[280px] w-full">
            <ResponsiveContainer>
              <LineChart data={cronologia} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
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

      {/* TABELA DINÂMICA INTEGRADA */}
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
                  <th className="px-4 py-4 text-right">Realizado</th>
                  <th className="px-4 py-4 text-right">
                    <span className="text-indigo-400">IA</span> (Frozen M-2)
                  </th>
                  <th className="px-4 py-4 text-right">
                    {lente === 'sellin' ? <span className="text-sky-400">Humano</span> : <span className="text-amber-400">Estoque</span>} {lente === 'sellin' ? '(Frozen M-2)' : 'Canal'}
                  </th>
                  {lente === 'sellout' && <th className="px-4 py-4 text-right">Giro (Cobertura)</th>}
                  <th className="px-4 py-4 text-right border-l border-slate-800">Acurácia IA</th>
                  {lente === 'sellin' && <th className="px-4 py-4 text-right">Acurácia Humano</th>}
                  <th className="px-6 py-4 text-right bg-slate-950/40">{lente === 'sellin' ? 'FVA (Valor Acc)' : 'Status'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-xs">
                {skusFiltradosLocais.map((row) => (
                  <tr key={row.sku} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-6 py-3.5">
                      <div className="flex flex-col">
                        <span className="font-bold text-slate-200 tracking-tight">{row.descricao}</span>
                        <span className="text-[10px] text-slate-500 font-mono mt-0.5">{row.sku}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-right font-black text-white">{formatVol(row.vol_real)}</td>
                    <td className="px-4 py-3.5 text-right font-semibold text-indigo-400 bg-indigo-950/20">{formatVol(row.vol_ia_congelado)}</td>
                    
                    {lente === 'sellin' ? (
                      <td className="px-4 py-3.5 text-right font-semibold text-sky-400 bg-sky-950/10">{formatVol(row.vol_comercial_congelado)}</td>
                    ) : (
                      <td className="px-4 py-3.5 text-right font-bold text-amber-400">{formatVol(row.estoque_canal)}</td>
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
                          <span className="bg-emerald-500/10 text-emerald-400 px-2.5 py-1 rounded font-black tracking-wider uppercase text-[10px]">+ {formatPct(row.fva)}</span>
                        ) : row.fva < 0 ? (
                          <span className="bg-rose-500/10 text-rose-400 px-2.5 py-1 rounded font-black tracking-wider uppercase text-[10px]">{formatPct(row.fva)}</span>
                        ) : (
                          <span className="text-slate-500 font-bold uppercase text-[10px]">Neutro</span>
                        )
                      ) : (
                        row.dias_cobertura > 60 ? (
                          <span className="bg-rose-500/10 text-rose-400 px-2.5 py-1 rounded font-black tracking-wider uppercase text-[10px]">Excesso</span>
                        ) : row.dias_cobertura < 15 ? (
                          <span className="bg-rose-600 text-white px-2.5 py-1 rounded font-black tracking-wider uppercase text-[10px] animate-pulse">Ruptura</span>
                        ) : (
                          <span className="bg-emerald-500/10 text-emerald-400 px-2.5 py-1 rounded font-black tracking-wider uppercase text-[10px]">Saudável</span>
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