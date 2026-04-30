import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Loader2, Package, DollarSign, Activity, AlertTriangle, ShieldCheck, TrendingUp, TrendingDown, Crosshair } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');

export default function AuditoriaArena() {
  const [cicloSelecionado, setCicloSelecionado] = useState('04/2026');
  const [visaoFinanceira, setVisaoFinanceira] = useState(false);
  
  const [isLoading, setIsLoading] = useState(true);
  const [dadosMacro, setDadosMacro] = useState<any>({});
  const [dadosCurva, setDadosCurva] = useState<any[]>([]);
  const [dadosOfensores, setDadosOfensores] = useState<any[]>([]);

  // Dispara as 3 chamadas para o FastAPI (Backend Gordo) simultaneamente
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [resMacro, resCurva, resOfensores] = await Promise.all([
        axios.get('/api/v1/kpis/macro', { params: { ciclo: cicloSelecionado } }),
        axios.get('/api/v1/kpis/curva', { params: { ciclo: cicloSelecionado } }),
        axios.get('/api/v1/kpis/ofensores', { params: { ciclo: cicloSelecionado, visao: visaoFinanceira ? 'financeiro' : 'caixas' } })
      ]);
      
      setDadosMacro(resMacro.data);
      setDadosCurva(resCurva.data.dados);
      setDadosOfensores(resOfensores.data.ofensores);
    } catch (error) {
      console.error("Erro ao buscar KPIs:", error);
    } finally {
      setIsLoading(false);
    }
  }, [cicloSelecionado, visaoFinanceira]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">
        
        {/* CABEÇALHO E FILTROS */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Crosshair className="w-8 h-8 text-indigo-600" /> Auditoria de IA & S&OP
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Forecast Value Added (FVA) e Assertividade
            </p>
          </div>

          <div className="flex items-center gap-6">
            <div className="flex flex-col">
              <label className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-1">Ciclo S&OP</label>
              <select 
                value={cicloSelecionado} 
                onChange={e => setCicloSelecionado(e.target.value)} 
                className="bg-slate-50 border border-slate-200 text-sm font-black px-4 py-2.5 rounded-xl outline-none text-slate-700 cursor-pointer transition-colors focus:border-indigo-500"
              >
                <option value="04/2026">Abril / 2026</option>
                <option value="05/2026">Maio / 2026</option>
              </select>
            </div>

            <div className="flex flex-col">
              <label className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-1">Visão de Erro</label>
              <div className="flex bg-slate-100 rounded-xl p-1">
                <button 
                  onClick={() => setVisaoFinanceira(false)}
                  className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-black uppercase tracking-widest transition-all ${!visaoFinanceira ? 'bg-white shadow text-indigo-600' : 'text-slate-400 hover:text-slate-600'}`}
                >
                  <Package className="w-4 h-4" /> Caixas
                </button>
                <button 
                  onClick={() => setVisaoFinanceira(true)}
                  className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-black uppercase tracking-widest transition-all ${visaoFinanceira ? 'bg-white shadow text-emerald-600' : 'text-slate-400 hover:text-slate-600'}`}
                >
                  <DollarSign className="w-4 h-4" /> Capital
                </button>
              </div>
            </div>
          </div>
        </div>

        {isLoading ? (
          <div className="flex flex-col items-center justify-center h-64 bg-white rounded-[32px] shadow-sm border border-slate-100">
            <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
            <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Processando Motor de Auditoria...</span>
          </div>
        ) : (
          <>
            {/* CARDS SUPERIORES: A BATALHA (FVA) */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
              {/* Card IA */}
              <div className="bg-white p-6 rounded-[24px] shadow-sm border border-slate-100 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1.5 h-full bg-slate-300"></div>
                <h3 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4">Acurácia Motor de IA</h3>
                <div className="flex items-end justify-between">
                  <span className="text-4xl font-black text-slate-900">{dadosMacro?.acuracia_ia || 0}%</span>
                  <span className="text-xs font-bold text-slate-400 flex items-center bg-slate-100 px-2 py-1 rounded">Baseline Neutra</span>
                </div>
              </div>

              {/* Card Bottom-up (Vendas) */}
              <div className="bg-white p-6 rounded-[24px] shadow-sm border border-slate-100 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1.5 h-full bg-indigo-400"></div>
                <h3 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4">Acurácia Vendas (Bottom-Up)</h3>
                <div className="flex items-end justify-between">
                  <span className="text-4xl font-black text-slate-900">{dadosMacro?.acuracia_bu || 0}%</span>
                  <div className={`flex items-center gap-1 text-xs font-black px-2 py-1 rounded ${(dadosMacro?.fva_bu || 0) >= 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
                    {(dadosMacro?.fva_bu || 0) >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                    FVA: {dadosMacro?.fva_bu > 0 ? '+' : ''}{dadosMacro?.fva_bu || 0}%
                  </div>
                </div>
              </div>

              {/* Card Consenso S&OP */}
              <div className="bg-white p-6 rounded-[24px] shadow-sm border border-slate-100 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1.5 h-full bg-emerald-500"></div>
                <h3 className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4">Acurácia Consenso (Final)</h3>
                <div className="flex items-end justify-between">
                  <span className="text-4xl font-black text-slate-900">{dadosMacro?.acuracia_final || 0}%</span>
                  <div className={`flex items-center gap-1 text-xs font-black px-2 py-1 rounded ${(dadosMacro?.fva_final || 0) >= 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
                    {(dadosMacro?.fva_final || 0) >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                    FVA: {dadosMacro?.fva_final > 0 ? '+' : ''}{dadosMacro?.fva_final || 0}%
                  </div>
                </div>
              </div>
            </div>

            {/* ÁREA PRINCIPAL: GRÁFICO E TABELA */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              
              {/* GRÁFICO DE HORIZONTE */}
              <div className="lg:col-span-2 bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 flex flex-col min-h-[400px]">
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2">
                  <Activity className="w-4 h-4 text-indigo-500" /> Curva de Previsão vs Realizado
                </h3>
                <div className="flex-1 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={dadosCurva} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                      <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                      <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                      <Tooltip 
                        contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)', fontWeight: 900}} 
                        formatter={(val: any) => formatVolume(val)}
                      />
                      <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: 900}} />
                      
                      <Line type="monotone" dataKey="Realizado" name="Histórico Real" stroke="#94a3b8" strokeWidth={4} dot={{r: 5}} connectNulls />
                      <Line type="monotone" dataKey="IA" name="Motor IA" stroke="#cbd5e1" strokeWidth={3} strokeDasharray="5 5" dot={false} connectNulls />
                      <Line type="monotone" dataKey="Comercial" name="Proposta Vendas" stroke="#818cf8" strokeWidth={3} dot={{r: 4}} connectNulls />
                      <Line type="monotone" dataKey="Final" name="Consenso Oficial" stroke="#10b981" strokeWidth={4} dot={{r: 5}} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* RANKING TOP 10 OFENSORES */}
              <div className="bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 flex flex-col min-h-[400px]">
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-rose-500" /> Top 10 Ofensores
                </h3>
                
                <div className="overflow-x-auto custom-scrollbar pr-2 flex-1">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-slate-100">
                        <th className="pb-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">SKU / Responsável</th>
                        <th className="pb-3 text-[10px] font-black text-slate-400 uppercase tracking-widest text-right">
                          Erro Absoluto {visaoFinanceira ? '(R$)' : '(CX)'}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                      {dadosOfensores.length === 0 ? (
                        <tr>
                          <td colSpan={2} className="py-8 text-center text-xs font-bold text-slate-400">
                            Nenhum ofensor encontrado neste ciclo.
                          </td>
                        </tr>
                      ) : (
                        dadosOfensores.map((item, idx) => (
                          <tr key={idx} className="hover:bg-slate-50/50 transition-colors">
                            <td className="py-3">
                              <div className="flex flex-col">
                                <span className="font-bold text-sm text-slate-800">{item.sku}</span>
                                <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{item.responsavel || 'Não Atribuído'}</span>
                              </div>
                            </td>
                            <td className="py-3 text-right">
                              <span className="font-black text-rose-500">
                                {visaoFinanceira ? formatMoeda(item.erro) : formatVolume(item.erro)}
                              </span>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

            </div>
          </>
        )}
      </div>
    </div>
  );
}