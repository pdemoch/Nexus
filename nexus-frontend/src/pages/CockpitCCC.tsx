import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { 
  Activity, Calendar, DollarSign, TrendingDown, TrendingUp, 
  ChevronRight, ChevronDown, Landmark, Receipt, Download, RefreshCw 
} from 'lucide-react';
import { ResponsiveContainer, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, LineChart, Line, Legend } from 'recharts';

// --- HELPERS E FORMATAÇÃO ---
const formatFin = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));

// --- AQUI: Função de Fuso Horário definida no próprio ficheiro ---
const obterDataBrasilia = () => {
  const d = new Date();
  const utc = d.getTime() + (d.getTimezoneOffset() * 60000);
  return new Date(utc + (3600000 * -3));
};

// --- COMPONENTES DA TABELA OLAP (PMR) ---
const RazaoSocialRow = ({ razao, info }: any) => {
  const [open, setOpen] = useState(false);
  const pb = info.val > 0 ? (info.pb / info.val).toFixed(1) : 0;
  const pr = info.val > 0 ? (info.pr / info.val).toFixed(1) : 0;
  const delta = (Number(pr) - Number(pb)).toFixed(1);

  return (
    <React.Fragment>
      <tr className="hover:bg-slate-800/40 cursor-pointer border-b border-slate-800/30 text-xs" onClick={() => setOpen(!open)}>
        <td className="px-4 py-3 pl-12 flex items-center gap-2">
          {open ? <ChevronDown className="w-4 h-4 text-indigo-400" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
          <span className="font-bold text-slate-300">{razao}</span>
        </td>
        <td className="px-4 py-3 text-right text-slate-400">{formatFin(info.val)}</td>
        <td className="px-4 py-3 text-right font-mono text-sky-400">{pb} d</td>
        <td className="px-4 py-3 text-right font-mono text-indigo-400 font-bold">{pr} d</td>
        <td className={`px-4 py-3 text-right font-black ${Number(delta) > 0 ? 'text-rose-400' : 'text-emerald-400'}`}>{Number(delta) > 0 ? '+' : ''}{delta} d</td>
      </tr>
      {open && info.cnpjs.sort((a:any, b:any) => b.delta - a.delta).map((cnpj: any) => (
        <tr key={cnpj.cnpj_loja} className="bg-slate-900/50 hover:bg-slate-800/60 border-b border-slate-800/30 text-xs">
          <td className="px-4 py-2.5 pl-20 font-mono text-slate-500">Filial/Loja: {cnpj.cnpj_loja}</td>
          <td className="px-4 py-2.5 text-right text-slate-500">{formatFin(cnpj.e1_valor)}</td>
          <td className="px-4 py-2.5 text-right font-mono text-sky-500/70">{cnpj.pmr_base} d</td>
          <td className="px-4 py-2.5 text-right font-mono text-indigo-400/80">{cnpj.pmr_real} d</td>
          <td className={`px-4 py-2.5 text-right font-mono ${cnpj.delta > 0 ? 'text-rose-400/80' : 'text-emerald-400/80'}`}>{cnpj.delta > 0 ? '+' : ''}{cnpj.delta} d</td>
        </tr>
      ))}
    </React.Fragment>
  );
};

const RegionalRow = ({ regional, info }: any) => {
  const [open, setOpen] = useState(false);
  const pb = info.val > 0 ? (info.pb / info.val).toFixed(1) : 0;
  const pr = info.val > 0 ? (info.pr / info.val).toFixed(1) : 0;
  const delta = (Number(pr) - Number(pb)).toFixed(1);

  return (
    <React.Fragment>
      <tr className="bg-slate-900 hover:bg-slate-800/80 cursor-pointer border-b border-slate-700 text-sm" onClick={() => setOpen(!open)}>
        <td className="px-4 py-4 flex items-center gap-3 font-black text-white">
          {open ? <ChevronDown className="w-5 h-5 text-indigo-400" /> : <ChevronRight className="w-5 h-5 text-slate-500" />} {regional}
        </td>
        <td className="px-4 py-4 text-right font-bold text-slate-300">{formatFin(info.val)}</td>
        <td className="px-4 py-4 text-right font-mono text-sky-400">{pb} d</td>
        <td className="px-4 py-4 text-right font-mono text-indigo-400">{pr} d</td>
        <td className={`px-4 py-4 text-right font-black ${Number(delta) > 0 ? 'text-rose-500' : 'text-emerald-500'}`}>{Number(delta) > 0 ? '+' : ''}{delta} d</td>
      </tr>
      {open && Object.entries(info.rs).map(([razao, data]: any) => <RazaoSocialRow key={razao} razao={razao} info={data} />)}
    </React.Fragment>
  );
};

// --- COMPONENTES DA TABELA OLAP (PMP) ---
const TipoPMPRow = ({ tipo, info }: any) => {
  const [open, setOpen] = useState(false);
  const pb = info.val > 0 ? (info.pb / info.val).toFixed(1) : 0;
  const pr = info.val > 0 ? (info.pr / info.val).toFixed(1) : 0;
  const delta = (Number(pr) - Number(pb)).toFixed(1);

  return (
    <React.Fragment>
      <tr className="bg-slate-900 hover:bg-slate-800/80 cursor-pointer border-b border-slate-700 text-sm" onClick={() => setOpen(!open)}>
        <td className="px-4 py-4 flex items-center gap-3 font-black text-white">
          {open ? <ChevronDown className="w-5 h-5 text-emerald-400" /> : <ChevronRight className="w-5 h-5 text-slate-500" />} {tipo}
        </td>
        <td className="px-4 py-4 text-right font-bold text-slate-300">{formatFin(info.val)}</td>
        <td className="px-4 py-4 text-right font-mono text-sky-400">{pb} d</td>
        <td className="px-4 py-4 text-right font-mono text-emerald-400">{pr} d</td>
        <td className={`px-4 py-4 text-right font-black ${Number(delta) < 0 ? 'text-amber-500' : 'text-emerald-500'}`}>{Number(delta) > 0 ? '+' : ''}{delta} d</td>
      </tr>
      {open && info.fornecedores.sort((a:any, b:any) => b.e2_valor - a.e2_valor).map((forn: any) => (
        <tr key={forn.a2_nome} className="bg-slate-900/50 hover:bg-slate-800/60 border-b border-slate-800/30 text-xs">
          <td className="px-4 py-3 pl-12 font-bold text-slate-300">{forn.a2_nome}</td>
          <td className="px-4 py-3 text-right text-slate-400">{formatFin(forn.e2_valor)}</td>
          <td className="px-4 py-3 text-right font-mono text-sky-500/70">{forn.pmp_base} d</td>
          <td className="px-4 py-3 text-right font-mono text-emerald-400/80">{forn.pmp_real} d</td>
          <td className={`px-4 py-3 text-right font-mono ${forn.delta < 0 ? 'text-amber-500' : 'text-emerald-500'}`}>{forn.delta > 0 ? '+' : ''}{forn.delta} d</td>
        </tr>
      ))}
    </React.Fragment>
  );
};

export default function CockpitCCC() {
  const [lente, setLente] = useState<'pmr' | 'pmp'>('pmr');
  
  // Datas Default (Últimos 6 meses)
  const dFim = obterDataBrasilia();
  const dInicio = new Date(dFim);
  dInicio.setMonth(dInicio.getMonth() - 6);
  
  const [dataInicio, setDataInicio] = useState(dInicio.toISOString().slice(0, 10));
  const [dataFim, setDataFim] = useState(dFim.toISOString().slice(0, 10));

  const [dados, setDados] = useState<any[]>([]);
  const [tendencia, setTendencia] = useState<any[]>([]);
  const [kpis, setKpis] = useState<any>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchDados = async () => {
      setLoading(true);
      try {
        const res = await axios.get(`/api/v1/ccc/${lente}?data_inicio=${dataInicio}&data_fim=${dataFim}`);
        setDados(res.data.dados || []);
        setTendencia(res.data.tendencia || []);
        setKpis(res.data.kpis || {});
      } catch (e) { console.error(e); }
      setLoading(false);
    };
    fetchDados();
  }, [lente, dataInicio, dataFim]);

  // OLAP Engine Frontend - PMR (Agrupa e Calcula a Árvore dinamicamente)
  const arvorePMR = useMemo(() => {
    if (lente !== 'pmr' || !dados.length) return {};
    const tree: any = {};
    dados.forEach((row: any) => {
      if (!tree[row.regional]) tree[row.regional] = { val: 0, pb: 0, pr: 0, rs: {} };
      tree[row.regional].val += row.e1_valor;
      tree[row.regional].pb += row.peso_base;
      tree[row.regional].pr += row.peso_real;

      if (!tree[row.regional].rs[row.razao_social]) tree[row.regional].rs[row.razao_social] = { val: 0, pb: 0, pr: 0, cnpjs: [] };
      tree[row.regional].rs[row.razao_social].val += row.e1_valor;
      tree[row.regional].rs[row.razao_social].pb += row.peso_base;
      tree[row.regional].rs[row.razao_social].pr += row.peso_real;
      tree[row.regional].rs[row.razao_social].cnpjs.push(row);
    });
    return tree;
  }, [dados, lente]);

  // OLAP Engine Frontend - PMP
  const arvorePMP = useMemo(() => {
    if (lente !== 'pmp' || !dados.length) return {};
    const tree: any = {};
    dados.forEach((row: any) => {
      if (!tree[row.tipo_desc]) tree[row.tipo_desc] = { val: 0, pb: 0, pr: 0, fornecedores: [] };
      tree[row.tipo_desc].val += row.e2_valor;
      tree[row.tipo_desc].pb += row.peso_base;
      tree[row.tipo_desc].pr += row.peso_real;
      tree[row.tipo_desc].fornecedores.push(row);
    });
    return tree;
  }, [dados, lente]);

  return (
    <div className="p-6 bg-slate-950 min-h-screen text-slate-100 font-sans flex-1 w-full transition-all duration-300">
      
      {/* HEADER E FILTROS */}
      <div className="w-full flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 bg-slate-900 p-5 rounded-2xl border border-slate-800 mb-6 shadow-xl">
        <div>
          <h1 className="text-2xl font-black text-white tracking-tight flex items-center gap-3">
            <Landmark className="text-emerald-500 w-7 h-7" /> Engenharia de Caixa (CCC)
          </h1>
          <p className="text-xs text-slate-400 mt-1 font-medium">Auditoria Financeira de Prazos Médios - Base Histórica Real</p>
        </div>

        <div className="flex flex-col md:flex-row gap-4 items-center">
          
          <div className="flex items-center gap-3 bg-slate-950 p-2 rounded-xl border border-slate-800 shadow-inner">
            <Calendar className="w-4 h-4 text-slate-400 ml-2" />
            <input type="date" value={dataInicio} onChange={e => setDataInicio(e.target.value)} className="bg-transparent text-xs text-slate-300 font-mono focus:outline-none" style={{ colorScheme: 'dark' }}/>
            <span className="text-slate-600 text-xs font-black">ATÉ</span>
            <input type="date" value={dataFim} onChange={e => setDataFim(e.target.value)} className="bg-transparent text-xs text-slate-300 font-mono focus:outline-none" style={{ colorScheme: 'dark' }}/>
          </div>

          <div className="flex bg-slate-950 p-1.5 rounded-xl border border-slate-800 shadow-inner">
            <button onClick={() => setLente('pmr')} className={`px-6 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${lente === 'pmr' ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-500 hover:text-slate-300'}`}>
              <Receipt className="w-4 h-4"/> Clientes (PMR)
            </button>
            <button onClick={() => setLente('pmp')} className={`px-6 py-2 text-[10px] font-black uppercase tracking-wider rounded-lg flex items-center gap-2 ${lente === 'pmp' ? 'bg-emerald-600 text-white shadow-lg' : 'text-slate-500 hover:text-slate-300'}`}>
              <DollarSign className="w-4 h-4"/> Fornecedores (PMP)
            </button>
          </div>

        </div>
      </div>

      {loading ? (
        <div className="p-20 flex justify-center items-center gap-3 text-indigo-400 font-bold uppercase text-sm"><RefreshCw className="w-6 h-6 animate-spin" /> Calculando Matrizes Financeiras...</div>
      ) : (
        <>
          {/* KPIS E TENDÊNCIA */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
            
            <div className="flex flex-col gap-4">
              <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 border-l-sky-500 shadow-md">
                <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">Prazo Negociado (Base)</div>
                <div className="text-4xl font-black text-sky-400">{kpis.pmr_base || kpis.pmp_base || 0} <span className="text-xl text-slate-500">Dias</span></div>
              </div>
              <div className={`bg-slate-900 p-6 rounded-2xl border border-slate-800 border-l-4 shadow-md ${lente === 'pmr' ? 'border-l-indigo-500' : 'border-l-emerald-500'}`}>
                <div className="text-xs font-black uppercase text-slate-400 tracking-wider mb-2">Prazo Realizado (Real)</div>
                <div className={`text-4xl font-black ${lente === 'pmr' ? 'text-indigo-400' : 'text-emerald-400'}`}>{kpis.pmr_real || kpis.pmp_real || 0} <span className="text-xl text-slate-500">Dias</span></div>
              </div>
              <div className={`bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-md flex justify-between items-center ${kpis.delta > 0 && lente === 'pmr' ? 'bg-rose-950/20' : ''}`}>
                <div className="flex flex-col">
                  <span className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">{lente === 'pmr' ? 'Giro Retido (Inadimplência)' : 'Diferença Efetiva'}</span>
                  <span className={`text-2xl font-black ${(kpis.delta > 0 && lente === 'pmr') || (kpis.delta < 0 && lente === 'pmp') ? 'text-rose-400' : 'text-emerald-400'}`}>
                    {kpis.delta > 0 ? '+' : ''}{kpis.delta} Dias
                  </span>
                </div>
                {kpis.delta > 0 && lente === 'pmr' ? <TrendingDown className="w-8 h-8 text-rose-500"/> : <TrendingUp className="w-8 h-8 text-emerald-500"/>}
              </div>
            </div>

            <div className="lg:col-span-2 bg-slate-900 p-6 rounded-2xl border border-slate-800 shadow-md">
              <h3 className="text-xs font-black text-white uppercase tracking-widest mb-6 flex items-center gap-2">
                <Activity className="w-4 h-4 text-emerald-400"/> Comportamento Temporal ({lente.toUpperCase()})
              </h3>
              <div className="h-[280px] w-full">
                <ResponsiveContainer>
                  <LineChart data={tendencia} margin={{ top: 10, right: 10, bottom: 0, left: -20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis dataKey="mes" stroke="#64748b" tick={{fontSize: 10}} />
                    <YAxis stroke="#64748b" tick={{fontSize: 10}} unit=" d" />
                    <RechartsTooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155' }} />
                    <Legend verticalAlign="top" height={36} wrapperStyle={{ fontSize: '10px', fontWeight: 'bold' }}/>
                    
                    {lente === 'pmr' ? (
                      <>
                        <Line type="monotone" dataKey="pmr_base" name="PMR Negociado" stroke="#38bdf8" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 4 }} />
                        <Line type="monotone" dataKey="pmr_real" name="PMR Realizado" stroke="#818cf8" strokeWidth={4} dot={{ r: 6 }} />
                      </>
                    ) : (
                      <>
                        <Line type="monotone" dataKey="pmp_base" name="PMP Negociado" stroke="#38bdf8" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 4 }} />
                        <Line type="monotone" dataKey="pmp_real" name="PMP Realizado" stroke="#10b981" strokeWidth={4} dot={{ r: 6 }} />
                      </>
                    )}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

          </div>

          {/* TABELA DE DRILL-DOWN OLAP */}
          <div className="bg-slate-900 rounded-2xl border border-slate-800 shadow-2xl overflow-hidden">
            <table className="w-full text-left whitespace-nowrap">
              <thead className="bg-slate-950">
                <tr className="text-slate-400 border-b border-slate-800 text-[10px] font-black uppercase tracking-widest">
                  <th className="px-6 py-4">{lente === 'pmr' ? 'Nível Hierárquico (Clique para expandir)' : 'Tipo de Despesa'}</th>
                  <th className="px-4 py-4 text-right">Volume Transacionado</th>
                  <th className="px-4 py-4 text-right text-sky-400">Prazo Base</th>
                  <th className="px-4 py-4 text-right font-bold text-white">Prazo Real</th>
                  <th className="px-4 py-4 text-right">Desvio (Delta)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {lente === 'pmr' 
                  ? Object.entries(arvorePMR).map(([reg, info]: any) => <RegionalRow key={reg} regional={reg} info={info} />)
                  : Object.entries(arvorePMP).map(([tipo, info]: any) => <TipoPMPRow key={tipo} tipo={tipo} info={info} />)
                }
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}