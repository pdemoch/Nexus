import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState } from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Layers, Lock, Download, AlertTriangle, ShieldCheck, Check, Globe, Package, Users, Search, Filter, TrendingUp, TrendingDown
} from 'lucide-react';
import axios from 'axios';
import { Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ComposedChart } from 'recharts';
import * as XLSX from 'xlsx';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

// Tipagem corrigida para aceitar undefined e fazer fallback para 0
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

export default function GlobalDashboard() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [isLocked, setIsLocked] = useState(false);
  const [lockMessage, setLockMessage] = useState('');
  
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [busca, setBusca] = useState('');
  const [categoriaSelecionada, setCategoriaSelecionada] = useState('TODAS');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/dashboard/global');
      setDadosBrutos(res.data.dados || []);
      setIsLocked(res.data.is_locked);
      setLockMessage(res.data.lock_message);
      setCelulasEditadas({});
      setExpanded({});
    } catch (e) {
      console.error(e);
      setDadosBrutos([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleExportExcel = async () => {
    try {
        const response = await axios.get('/api/v1/dashboard/export', { responseType: 'blob' });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', 'Demanda_Irrestrita_Oficial.xlsx');
        document.body.appendChild(link);
        link.click();
        link.remove();
    } catch (e) { 
        alert("Erro ao exportar. Verifique as permissões."); 
    }
  };

  const handleAprovar = async () => {
    if (isLocked) return; 
    setIsProcessing(true);
    const ajustes = Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
      Object.entries(meses).map(([mes, val]: any) => ({
        nivel: chave.includes('_') ? 'cliente' : 'produto', 
        chave, 
        mes_projetado: mes, 
        novo_volume: val.novo_volume === '' ? 0 : Number(val.novo_volume || 0)
      }))
    );

    try {
      await axios.post('/api/v1/dashboard/aprovar', { ajustes });
      alert("✅ S&OP Global Aprovado! A Demanda Irrestrita foi congelada como Meta Oficial (vol_meta).");
      fetchData(); 
    } catch (e: any) { 
      alert(e.response?.data?.detail || "Erro ao aprovar o ciclo."); 
    } finally { 
      setIsProcessing(false); 
    }
  };

  const categoriasUnicas = useMemo(() => Array.from(new Set(dadosBrutos.map(d => d.categoria).filter(Boolean))).sort(), [dadosBrutos]);
  
  const dadosFiltrados = useMemo(() => dadosBrutos.filter(d => 
    (d.produto + ' ' + d.descricao + ' ' + d.cliente_razaosocial).toLowerCase().includes(busca.toLowerCase()) &&
    (categoriaSelecionada === 'TODAS' || d.categoria === categoriaSelecionada)
  ), [dadosBrutos, busca, categoriaSelecionada]);

  const dadosAgrupados = useMemo(() => {
    if (!dadosFiltrados.length) return [];
    const mapaProdutos = new Map();
    
    dadosFiltrados.forEach(r => {
      if (!mapaProdutos.has(r.produto)) {
        mapaProdutos.set(r.produto, { id: r.produto, chave_matriz: r.produto, nome: r.descricao, produto: r.produto, categoria: r.categoria, tipo: 'produto', meses: new Map(), subRowsMap: new Map() });
      }
      const prod = mapaProdutos.get(r.produto);
      const cKey = `${r.produto}_${r.cliente_razaosocial}`;
      
      if (!prod.subRowsMap.has(cKey)) {
          prod.subRowsMap.set(cKey, { id: cKey, chave_matriz: cKey, nome: r.cliente_razaosocial, tipo: 'cliente', meses: new Map() });
      }
      const cli = prod.subRowsMap.get(cKey);
      
      const mStr = r.mes_projetado;
      if (!prod.meses.has(mStr)) prod.meses.set(mStr, { mes_banco: mStr, vol_ia: 0, vol_td: 0, vol_supply: 0, vol_bu: 0, vol_final: 0, rec_ia: 0, rec_td: 0, rec_sp: 0, rec_bu: 0, rec_final: 0 });
      
      const mProd = prod.meses.get(mStr);
      mProd.vol_ia += (r.vol_ia || 0); mProd.vol_td += (r.vol_td || 0); mProd.vol_supply += (r.vol_supply || 0); mProd.vol_bu += (r.vol_bu || 0); mProd.vol_final += (r.vol_irrestrito || 0);
      mProd.rec_ia += (r.rec_ia || 0); mProd.rec_td += (r.rec_td || 0); mProd.rec_sp += (r.rec_supply || 0); mProd.rec_bu += (r.rec_bu || 0); mProd.rec_final += (r.rec_final || 0);
      
      cli.meses.set(mStr, { mes_banco: mStr, vol_ia: (r.vol_ia || 0), vol_td: (r.vol_td || 0), vol_supply: (r.vol_supply || 0), vol_bu: (r.vol_bu || 0), vol_final: (r.vol_irrestrito || 0), rec_ia: (r.rec_ia || 0), rec_td: (r.rec_td || 0), rec_sp: (r.rec_supply || 0), rec_bu: (r.rec_bu || 0), rec_final: (r.rec_final || 0) });
    });
    
    return Array.from(mapaProdutos.values()).map((p: any) => ({
        ...p, meses: Array.from(p.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
        subRows: Array.from(p.subRowsMap.values()).map((c: any) => ({ ...c, meses: Array.from(c.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)) }))
    }));
  }, [dadosFiltrados]);

  const totaisGerais = useMemo(() => {
    let t_ia = 0, t_td = 0, t_bu = 0, t_sp = 0, t_final = 0;
    let r_ia = 0, r_td = 0, r_bu = 0, r_sp = 0, r_final = 0;
    const porMes: Record<string, any> = {};

    dadosAgrupados.forEach(prod => {
      prod.meses.forEach((m: any) => {
        if (!porMes[m.mes_banco]) porMes[m.mes_banco] = { vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0 };
        
        const edicaoProd = celulasEditadas[prod.chave_matriz]?.[m.mes_banco];
        const isPendente = edicaoProd !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicaoProd.novo_volume : m.vol_final));
        const pmvAprox = m.vol_final > 0 ? (m.rec_final / m.vol_final) : 0;
        const receitaFinal = isPendente ? (volumeFinal * pmvAprox) : m.rec_final;

        t_ia += m.vol_ia; t_td += m.vol_td; t_bu += m.vol_bu; t_sp += m.vol_supply; t_final += volumeFinal;
        r_ia += m.rec_ia; r_td += m.rec_td; r_bu += m.rec_bu; r_sp += m.rec_sp; r_final += receitaFinal;

        porMes[m.mes_banco].vol_ia += m.vol_ia; porMes[m.mes_banco].vol_td += m.vol_td; porMes[m.mes_banco].vol_bu += m.vol_bu; porMes[m.mes_banco].vol_sp += m.vol_supply; porMes[m.mes_banco].vol_final += volumeFinal;
        porMes[m.mes_banco].rec_ia += m.rec_ia; porMes[m.mes_banco].rec_td += m.rec_td; porMes[m.mes_banco].rec_bu += m.rec_bu; porMes[m.mes_banco].rec_sp += m.rec_sp; porMes[m.mes_banco].rec_final += receitaFinal;
      });
    });

    const chartData = Object.keys(porMes).sort().map(mes => {
      const parts = mes.split('-');
      return { name: parts.length === 3 ? `${parts[1]}/${parts[0].slice(2)}` : mes, ...porMes[mes] };
    });

    return { 
      cards: { vol: { ia: t_ia, td: t_td, bu: t_bu, sp: t_sp, final: t_final }, rec: { ia: r_ia, td: r_td, bu: r_bu, sp: r_sp, final: r_final } },
      chartData, porMes
    };
  }, [dadosAgrupados, celulasEditadas]);

  const columns = useMemo(() => {
    if (dadosAgrupados.length === 0) return [];
    const baseCols: any[] = [
      {
        id: 'nome', header: 'SKU / Clientes', accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row; const isProduto = row.original.tipo === 'produto';
          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[350px]">
              {isProduto ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-100 text-slate-500 rounded-lg">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : <div className="w-8"></div>}
              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${isProduto ? 'bg-slate-800 border-slate-700 text-slate-300' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                {isProduto ? <Package className="w-4 h-4" /> : <Users className="w-4 h-4" />}
              </div>
              <div className="flex flex-col">
                 <span className={`text-sm ${isProduto ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[250px]'}`}>{info.getValue()}</span>
                 {isProduto && (
                   <div className="flex items-center gap-2 mt-1">
                     <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{row.original.produto}</span>
                     <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">{row.original.categoria}</span>
                   </div>
                 )}
              </div>
            </div>
          )
        }
      }
    ];

    dadosAgrupados[0].meses.forEach((m: any) => {
      const mesStr = m.mes_banco.split('-').reverse().slice(1).join('/');
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: mesStr, accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_final || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.chave_matriz]?.[m.mes_banco];
          
          const valorReal = edicao !== undefined ? edicao.novo_volume : (dadosMes?.vol_final || 0);
          const valorInteiro = Math.round(Number(valorReal));
          const isGlobalFechado = meta.lockMessage === "Demanda Irrestrita Publicada";

          return (
            <div className="flex flex-col w-36 gap-1.5 relative group">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-indigo-500 font-black uppercase tracking-widest" title="Proposta Comercial Base">BU: {Math.round(dadosMes?.vol_bu || 0)}</span>
                 <span className="text-[9px] text-amber-500 font-black uppercase tracking-widest" title="Capacidade Supply">SP: {Math.round(dadosMes?.vol_supply || 0)}</span>
              </div>
              <input
                type="number" value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0" disabled={meta.isLocked}
                onChange={(e) => meta.updateCell(row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${isGlobalFechado ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : meta.isLocked ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed' : row.tipo === 'produto' ? 'border-dashed border-indigo-200 focus:border-indigo-500 bg-white' : 'bg-slate-50 border-transparent focus:bg-white focus:border-slate-300 text-slate-800'} 
                  ${edicao !== undefined && !meta.isLocked ? 'bg-indigo-600 border-indigo-700 text-white' : ''}`}
              />
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosAgrupados, celulasEditadas]);

  const table = useReactTable({
    data: dadosAgrupados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas, isLocked, lockMessage,
      updateCell: (chave: string, mes: string, val: string) => {
        if (isLocked) return; 
        const v = val === '' ? '' : Math.round(Number(val));
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: Number.isNaN(v as any) && val !== '' ? 0 : v } } }));
      }
    }
  });

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isLocked && !isLoading && (
              <div className={`absolute top-0 left-0 w-full text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10 ${lockMessage === "Demanda Irrestrita Publicada" ? "bg-emerald-500" : "bg-amber-500"}`}>
                  {lockMessage === "Demanda Irrestrita Publicada" ? <ShieldCheck className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />} STATUS: {lockMessage}
              </div>
          )}
          
          <div className={isLocked && !isLoading ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Globe className="w-8 h-8 text-indigo-600" /> S&OP Global Dashboard
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">Aprovação da Demanda Irrestrita</p>
          </div>
          
          <div className={`flex items-center gap-4 ${isLocked && !isLoading ? "pt-4" : ""}`}>
              <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase border border-slate-200"><Download className="w-4 h-4" /> Relatório Oficial</button>
              <button onClick={handleAprovar} disabled={isProcessing || (isLocked && lockMessage !== "Demanda Irrestrita Publicada")} className={`flex items-center gap-2 text-white px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg ${lockMessage === "Demanda Irrestrita Publicada" ? 'bg-emerald-500 shadow-none' : isLocked ? 'bg-slate-300 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-black'}`}>
                  {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : lockMessage === "Demanda Irrestrita Publicada" ? <ShieldCheck className="w-5 h-5" /> : isLocked ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                  {lockMessage === "Demanda Irrestrita Publicada" ? 'Meta Congelada' : isLocked ? 'Aprovação Bloqueada' : 'Aprovar Demanda'}
              </button>
          </div>
        </div>

        {/* CARDS DE CASCATA (VOLUME & RECEITA) */}
        {!isLoading && dadosAgrupados.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
            {[
              { label: 'Sinal IA Base', vol: totaisGerais.cards.vol.ia, rec: totaisGerais.cards.rec.ia, varVol: 0, varRec: 0, color: 'bg-slate-100 text-slate-700' },
              { label: 'Meta Top-Down', vol: totaisGerais.cards.vol.td, rec: totaisGerais.cards.rec.td, varVol: totaisGerais.cards.vol.td, baseVol: totaisGerais.cards.vol.ia, varRec: totaisGerais.cards.rec.td, baseRec: totaisGerais.cards.rec.ia, color: 'bg-blue-50 text-blue-700' },
              { label: 'Proposta Comercial (BU)', vol: totaisGerais.cards.vol.bu, rec: totaisGerais.cards.rec.bu, varVol: totaisGerais.cards.vol.bu, baseVol: totaisGerais.cards.vol.td, varRec: totaisGerais.cards.rec.bu, baseRec: totaisGerais.cards.rec.td, color: 'bg-indigo-50 text-indigo-700' },
              { label: 'Capacidade Fábrica (SP)', vol: totaisGerais.cards.vol.sp, rec: totaisGerais.cards.rec.sp, varVol: totaisGerais.cards.vol.sp, baseVol: totaisGerais.cards.vol.bu, varRec: totaisGerais.cards.rec.sp, baseRec: totaisGerais.cards.rec.bu, color: 'bg-amber-50 text-amber-700' },
              { label: 'Demanda Final', vol: totaisGerais.cards.vol.final, rec: totaisGerais.cards.rec.final, varVol: totaisGerais.cards.vol.final, baseVol: totaisGerais.cards.vol.sp, varRec: totaisGerais.cards.rec.final, baseRec: totaisGerais.cards.rec.sp, color: 'bg-emerald-600 text-white' }
            ].map((card, i) => (
              <div key={i} className={`${card.color} p-5 rounded-2xl shadow-sm border border-slate-100 flex flex-col justify-between`}>
                 <span className="text-[10px] font-black uppercase tracking-widest opacity-80 mb-2">{card.label}</span>
                 <div className="flex flex-col gap-3">
                    <div>
                      <p className="text-xl font-black leading-none">{formatVolume(card.vol || 0)} <span className="text-[10px] font-bold opacity-70">CX</span></p>
                      {i > 0 && <div className="mt-1"><VarBadge atual={card.varVol} anterior={card.baseVol} /></div>}
                    </div>
                    <div className="border-t border-black/10 pt-2">
                      <p className="text-sm font-black leading-none">{formatMoeda(card.rec || 0)}</p>
                      {i > 0 && <div className="mt-1"><VarBadge atual={card.varRec} anterior={card.baseRec} /></div>}
                    </div>
                 </div>
              </div>
            ))}
          </div>
        )}

        {/* 2 GRÁFICOS: VOLUME E FATURAMENTO */}
        {!isLoading && dadosAgrupados.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <div className="bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 min-h-[350px] flex flex-col">
               <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-4">Volume Projetado (CX)</h3>
               <div className="flex-1 w-full"><ResponsiveContainer width="100%" height="100%">
                 <ComposedChart data={totaisGerais.chartData} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                   <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                   <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                   <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                   {/* Tipagem corrigida no formatador do Tooltip */}
                   <Tooltip cursor={{fill: '#f8fafc'}} contentStyle={{borderRadius: '16px', border: '1px solid #e2e8f0', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)'}} />
                   <Legend wrapperStyle={{fontSize: '11px', fontWeight: '900'}} />
                   <Bar dataKey="vol_bu" name="Comercial (BU)" fill="#818cf8" radius={[4, 4, 0, 0]} maxBarSize={30} />
                   <Bar dataKey="vol_sp" name="Supply (SP)" fill="#f59e0b" radius={[4, 4, 0, 0]} maxBarSize={30} />
                   <Line type="monotone" dataKey="vol_final" name="Final" stroke="#0f172a" strokeWidth={3} dot={{r: 4, fill: '#0f172a'}} />
                 </ComposedChart>
               </ResponsiveContainer></div>
            </div>

            <div className="bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 min-h-[350px] flex flex-col">
               <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-4">Faturamento Projetado (R$)</h3>
               <div className="flex-1 w-full"><ResponsiveContainer width="100%" height="100%">
                 <ComposedChart data={totaisGerais.chartData} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                   <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                   <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                   <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} tickLine={false} tickFormatter={(v) => `R$${(v/1000000).toFixed(1)}M`} />
                   {/* Tipagem corrigida no formatador do Tooltip usando 'any' e cast estrito */}
                   <Tooltip cursor={{fill: '#f8fafc'}} contentStyle={{borderRadius: '16px', border: '1px solid #e2e8f0', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)'}} formatter={(value: any) => formatMoeda(Number(value) || 0)} />
                   <Legend wrapperStyle={{fontSize: '11px', fontWeight: '900'}} />
                   <Bar dataKey="rec_bu" name="Comercial (BU)" fill="#a78bfa" radius={[4, 4, 0, 0]} maxBarSize={30} />
                   <Bar dataKey="rec_sp" name="Supply (SP)" fill="#fbbf24" radius={[4, 4, 0, 0]} maxBarSize={30} />
                   <Line type="monotone" dataKey="rec_final" name="Final" stroke="#059669" strokeWidth={3} dot={{r: 4, fill: '#059669'}} />
                 </ComposedChart>
               </ResponsiveContainer></div>
            </div>
          </div>
        )}

        {/* BUSCA E FILTROS */}
        <div className="flex flex-col md:flex-row gap-4 mb-6">
           <div className="flex-1 bg-white p-3 rounded-[24px] shadow-sm border border-slate-100 flex flex-col md:flex-row items-center gap-3">
             <div className="flex-1 flex items-center gap-3 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full">
               <Search className="w-5 h-5 text-slate-400" />
               <input type="text" placeholder="Filtrar por Produto ou Cliente..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full bg-transparent py-3 text-sm font-bold text-slate-800 outline-none placeholder:text-slate-400" />
             </div>
             <div className="flex items-center gap-2 px-4 bg-slate-50 rounded-xl border border-slate-100 w-full md:w-auto">
               <Filter className="w-4 h-4 text-slate-400" />
               <select value={categoriaSelecionada} onChange={e => setCategoriaSelecionada(e.target.value)} className="bg-transparent py-3 text-sm font-bold text-slate-700 outline-none cursor-pointer">
                 <option value="TODAS">TODAS AS CATEGORIAS</option>
                 {categoriasUnicas.map(c => <option key={c as string} value={c as string}>{c as string}</option>)}
               </select>
             </div>
           </div>
        </div>

        {/* TABELA DE APROVAÇÃO */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 z-10"><Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" /><span className="text-slate-400 font-black text-xs tracking-widest uppercase">A compilar S&OP...</span></div>
          ) : dadosAgrupados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400"><Layers className="w-12 h-12 mb-4 opacity-20" /><span className="font-black text-sm tracking-widest uppercase">Nenhum dado</span></div>
          ) : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className="px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest cursor-pointer hover:bg-slate-50" onClick={header.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">{flexRender(header.column.columnDef.header, header.getContext())}
                          <span className="text-slate-300">{{ asc: <ArrowUp className="w-4 h-4 text-slate-500" />, desc: <ArrowDown className="w-4 h-4 text-slate-500" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-30" />}</span>
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-slate-50/50' : row.original.tipo === 'produto' ? 'bg-white hover:bg-slate-50' : 'bg-slate-50/30 hover:bg-slate-100'}`}>
                    {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-8 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-slate-900 text-white">
                <tr>
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'nome') return <td key={header.id} className="px-8 py-5 text-right"><div className="flex flex-col"><span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Receita Final</span><span className="font-bold text-sm text-white">PROJEÇÃO GLOBAL</span></div></td>;
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white">{formatVolume(totaisGerais.porMes[m]?.vol_final || 0)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(totaisGerais.porMes[m]?.rec_final || 0)}</span>
                          </div>
                        </td>
                      );
                    }
                    return <td key={header.id}></td>;
                  })}
                </tr>
              </tfoot>
            </table>
          </div>
          )}
        </div>
      </div>
    </div>
  );
}