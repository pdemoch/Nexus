import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  TrendingUp, TrendingDown, Target, Activity, CheckCircle, AlertTriangle, Cpu, Users, Loader2
} from 'lucide-react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
} from 'recharts';

const formatVolume = (val: number) => new Intl.NumberFormat('pt-BR').format(Math.round(val || 0));
const formatPct = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1 }).format(val || 0);

// Componente visual para diagnosticar o comportamento da previsão
const BiasBadge = ({ bias }: { bias: number }) => {
  if (bias > 0.05) return <span className="flex items-center gap-1 bg-rose-100 text-rose-700 px-2 py-1 rounded text-[10px] font-black uppercase tracking-widest"><TrendingUp className="w-3 h-3"/> Superestimado</span>;
  if (bias < -0.05) return <span className="flex items-center gap-1 bg-amber-100 text-amber-700 px-2 py-1 rounded text-[10px] font-black uppercase tracking-widest"><TrendingDown className="w-3 h-3"/> Subestimado</span>;
  return <span className="flex items-center gap-1 bg-emerald-100 text-emerald-700 px-2 py-1 rounded text-[10px] font-black uppercase tracking-widest"><CheckCircle className="w-3 h-3"/> Preciso</span>;
};

export default function AuditoriaArena() {
  const [dados, setDados] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    axios.get('/api/v1/kpis/auditoria')
      .then(res => setDados(res.data.dados))
      .catch(console.error)
      .finally(() => setIsLoading(false));
  }, []);

  if (isLoading) return (
    <div className="h-screen flex flex-col items-center justify-center bg-slate-50">
      <Loader2 className="w-10 h-10 animate-spin text-indigo-600 mb-4" />
      <p className="text-slate-400 font-black text-xs tracking-widest uppercase">Consolidando Rolling Forecast...</p>
    </div>
  );

  if (!dados || Object.keys(dados.macro).length === 0) return (
    <div className="h-screen flex flex-col items-center justify-center bg-slate-50">
      <AlertTriangle className="w-10 h-10 text-amber-500 mb-4" />
      <p className="text-slate-400 font-black text-xs tracking-widest uppercase">Sem dados de auditoria disponíveis.</p>
    </div>
  );

  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32">
      <div className="max-w-[1600px] mx-auto">
        
        {/* CABEÇALHO */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
              <Target className="w-8 h-8 text-indigo-600" /> Auditoria de Demanda
            </h1>
            <p className="text-slate-500 mt-1 font-medium">Acurácia Contínua e Rolling Forecast</p>
          </div>
        </div>

        {/* CARDS DE PERFORMANCE MACRO */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
          
          {/* Card IA */}
          <div className="bg-white p-8 rounded-[32px] shadow-sm border border-slate-100 flex flex-col justify-between transition-shadow hover:shadow-md">
             <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3 text-slate-400">
                   <div className="p-3 bg-slate-100 rounded-xl"><Cpu className="w-6 h-6 text-slate-600" /></div>
                   <span className="font-black uppercase tracking-widest text-sm">Sinal IA</span>
                </div>
                <div className="text-right">
                   <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Acurácia (1-WMAPE)</p>
                   <p className="text-3xl font-black text-slate-800">{formatPct(dados.macro.acc_ia)}</p>
                </div>
             </div>
             <div className="grid grid-cols-2 gap-4 border-t border-slate-100 pt-6">
                <div>
                   <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Volume Projetado</p>
                   <p className="text-xl font-black text-slate-800">{formatVolume(dados.macro.vol_ia)} <span className="text-xs opacity-50">CX</span></p>
                </div>
                <div>
                   <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Comportamento (BIAS)</p>
                   <div className="flex items-center gap-2 mt-1">
                     <span className="text-xl font-black text-slate-800">{formatPct(dados.macro.bias_ia)}</span>
                     <BiasBadge bias={dados.macro.bias_ia} />
                   </div>
                </div>
             </div>
          </div>

          {/* Card Comercial */}
          <div className="bg-indigo-600 p-8 rounded-[32px] shadow-lg shadow-indigo-900/20 flex flex-col justify-between text-white transition-transform hover:-translate-y-1">
             <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3 text-indigo-200">
                   <div className="p-3 bg-indigo-500/50 rounded-xl"><Users className="w-6 h-6 text-white" /></div>
                   <span className="font-black uppercase tracking-widest text-sm">Consenso S&OP</span>
                </div>
                <div className="text-right">
                   <p className="text-[10px] font-bold text-indigo-300 uppercase tracking-widest">Acurácia (1-WMAPE)</p>
                   <p className="text-3xl font-black">{formatPct(dados.macro.acc_comercial)}</p>
                </div>
             </div>
             <div className="grid grid-cols-2 gap-4 border-t border-indigo-500/30 pt-6">
                <div>
                   <p className="text-[10px] font-bold text-indigo-300 uppercase tracking-widest mb-1">Volume Fechado</p>
                   <p className="text-xl font-black">{formatVolume(dados.macro.vol_comercial)} <span className="text-xs opacity-50">CX</span></p>
                </div>
                <div>
                   <p className="text-[10px] font-bold text-indigo-300 uppercase tracking-widest mb-1">Comportamento (BIAS)</p>
                   <div className="flex items-center gap-2 mt-1">
                     <span className="text-xl font-black">{formatPct(dados.macro.bias_comercial)}</span>
                     <BiasBadge bias={dados.macro.bias_comercial} />
                   </div>
                </div>
             </div>
          </div>

        </div>

        {/* GRÁFICO DE SOBREPOSIÇÃO (ROLLING FORECAST) */}
        <div className="bg-white p-8 rounded-[32px] shadow-sm border border-slate-100 mb-8">
           <div className="flex items-center justify-between mb-8">
             <div className="flex items-center gap-2">
               <Activity className="w-5 h-5 text-indigo-500" />
               <h3 className="text-lg font-black text-slate-800 uppercase tracking-tighter">Evolução de Assertividade: Projeção vs Real</h3>
             </div>
           </div>
           
           <div className="h-[400px] w-full">
             <ResponsiveContainer width="100%" height="100%">
               <LineChart data={dados.grafico} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                 <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                 <XAxis dataKey="name" tick={{fontSize: 12, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} dy={10} />
                 <YAxis tick={{fontSize: 12, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => formatVolume(v)} />
                 
                 {/* Tipagem corrigida no formatter (name: any) e conversão para String */}
                 <Tooltip 
                   contentStyle={{backgroundColor: '#0f172a', border: 'none', borderRadius: '16px', color: '#fff', boxShadow: '0 10px 30px rgba(0,0,0,0.2)'}}
                   formatter={(val: any, name: any) => [formatVolume(val), String(name).replace('_', ' ')]}
                 />
                 <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '12px', fontWeight: 900}} />
                 
                 <Line type="monotone" dataKey="Realizado" name="Venda Realizada" stroke="#0f172a" strokeWidth={4} dot={{r: 4}} connectNulls={false} />
                 <Line type="monotone" dataKey="Projecao_IA" name="Projeção IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls={false} />
                 <Line type="monotone" dataKey="Proposta_Comercial" name="Consenso S&OP" stroke="#6366f1" strokeWidth={3} dot={{r: 5, strokeWidth: 2}} connectNulls={false} />
               </LineChart>
             </ResponsiveContainer>
           </div>
        </div>

        {/* MATRIZ DE OFENSORES (DIAGNÓSTICO GRANULAR) */}
        <div className="bg-white rounded-[32px] shadow-sm border border-slate-100 overflow-hidden">
           <div className="p-8 border-b border-slate-100 flex items-center gap-2">
             <AlertTriangle className="w-5 h-5 text-amber-500" />
             <h3 className="text-lg font-black text-slate-800 uppercase tracking-tighter">Matriz de Ofensores (Top 50 SKUs)</h3>
           </div>
           <div className="overflow-x-auto custom-scrollbar">
             <table className="w-full text-left border-collapse">
               <thead>
                 <tr className="bg-slate-50/50">
                   <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">SKU / Descrição</th>
                   <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 text-right">Venda Real</th>
                   <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 text-right bg-slate-50">Acurácia IA</th>
                   <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 text-right bg-slate-50">BIAS IA</th>
                   <th className="px-6 py-4 text-[10px] font-black text-indigo-400 uppercase tracking-widest border-b border-slate-100 text-right bg-indigo-50/30">Acurácia S&OP</th>
                   <th className="px-6 py-4 text-[10px] font-black text-indigo-400 uppercase tracking-widest border-b border-slate-100 text-right bg-indigo-50/30">BIAS S&OP</th>
                 </tr>
               </thead>
               <tbody>
                 {dados.tabela.map((row: any, idx: number) => (
                   <tr key={idx} className="border-b border-slate-50 hover:bg-slate-50/80 transition-colors">
                     <td className="px-6 py-4">
                       <div className="flex flex-col">
                         <span className="text-sm font-black text-slate-800 tracking-tight">{row.descricao || 'N/A'}</span>
                         <span className="text-[10px] text-slate-400 font-bold uppercase tracking-widest">{row.sku}</span>
                       </div>
                     </td>
                     <td className="px-6 py-4 text-right font-black text-slate-800">{formatVolume(row.vol_real)}</td>
                     
                     {/* Colunas IA */}
                     <td className="px-6 py-4 text-right font-bold text-slate-500 bg-slate-50/50">{formatPct(row.acc_ia)}</td>
                     <td className="px-6 py-4 text-right bg-slate-50/50">
                        <div className="flex justify-end"><BiasBadge bias={row.bias_ia} /></div>
                     </td>

                     {/* Colunas S&OP */}
                     <td className={`px-6 py-4 text-right font-bold ${row.acc_comercial > row.acc_ia ? 'text-emerald-600' : 'text-rose-600'} bg-indigo-50/10`}>
                       {formatPct(row.acc_comercial)}
                     </td>
                     <td className="px-6 py-4 text-right bg-indigo-50/10">
                        <div className="flex justify-end"><BiasBadge bias={row.bias_comercial} /></div>
                     </td>
                   </tr>
                 ))}
               </tbody>
             </table>
           </div>
        </div>

      </div>
    </div>
  );
}