import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState } from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Layers, Lock, Download, AlertTriangle, ShieldCheck, Check, Globe, Package, Users, Search, Filter, BarChart3, TrendingUp, TrendingDown, Activity, Hash, X
} from 'lucide-react';
import axios from 'axios';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

// Formatadores com separador de milhares
const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

// Componente de Badge de Variação
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

  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);

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

  // FUNCIONALIDADE RESTAURADA: Exportação para Excel
  const handleExportExcel = async () => {
    try {
        const response = await axios.get('/api/v1/dashboard/export', { responseType: 'blob' });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url; link.setAttribute('download', 'Demanda_Irrestrita_Oficial.xlsx');
        document.body.appendChild(link); link.click(); link.remove();
    } catch (e) { alert("Erro ao exportar."); }
  };

  const handleAprovar = async () => {
    if (isLocked) return; 
    setIsProcessing(true);
    const ajustes = Object.entries(celulasEditadas).flatMap(([chave, meses]: any) => 
      Object.entries(meses).map(([mes, val]: any) => ({
        nivel: chave.split('|').length === 3 ? 'cliente' : 'produto',
        chave: chave.split('|').pop(), 
        mes_projetado: mes, 
        novo_volume: val.novo_volume === '' ? 0 : Number(val.novo_volume || 0)
      }))
    );
    try {
      await axios.post('/api/v1/dashboard/aprovar', { ajustes });
      alert("✅ S&OP Global Aprovado!");
      fetchData(); 
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao aprovar."); } 
    finally { setIsProcessing(false); }
  };

  const dadosAgrupados = useMemo(() => {
    if (!dadosBrutos.length) return [];
    const term = busca.toLowerCase();
    const filtered = dadosBrutos.filter(d => (d.produto + ' ' + d.descricao + ' ' + d.cliente_razaosocial + ' ' + d.categoria).toLowerCase().includes(term));

    const tree = new Map();
    filtered.forEach(r => {
      if (!tree.has(r.categoria)) {
        tree.set(r.categoria, { id: r.categoria, chave_matriz: r.categoria, nome: r.categoria, tipo: 'categoria', meses: new Map(), skus: new Map() });
      }
      const cat = tree.get(r.categoria);
      if (!cat.skus.has(r.produto)) {
        cat.skus.set(r.produto, { id: `${r.categoria}|${r.produto}`, chave_matriz: `${r.categoria}|${r.produto}`, nome: r.descricao, produto: r.produto, tipo: 'produto', meses: new Map(), clientes: new Map() });
      }
      const skuNode = cat.skus.get(r.produto);
      const cKey = `${r.categoria}|${r.produto}|${r.cliente_razaosocial}`;
      if (!skuNode.clientes.has(cKey)) {
        skuNode.clientes.set(cKey, { id: cKey, chave_matriz: cKey, nome: r.cliente_razaosocial, tipo: 'cliente', meses: new Map() });
      }
      const cli = skuNode.clientes.get(cKey);
      const m = r.mes_projetado;

      [cat, skuNode, cli].forEach(node => {
        if (!node.meses.has(m)) node.meses.set(m, { mes_banco: m, vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0 });
        const target = node.meses.get(m);
        target.vol_ia += r.vol_ia; target.vol_td += r.vol_td; target.vol_bu += r.vol_bu; target.vol_sp += r.vol_supply; target.vol_final += r.vol_irrestrito;
        target.rec_ia += r.rec_ia; target.rec_td += r.rec_td; target.rec_bu += r.rec_bu; target.rec_sp += r.rec_supply; target.rec_final += r.rec_final;
      });
    });

    return Array.from(tree.values()).map(cat => ({
      ...cat, meses: Array.from(cat.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
      subRows: Array.from(cat.skus.values()).map((s: any) => ({
        ...s, meses: Array.from(s.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
        subRows: Array.from(s.clientes.values()).map((c: any) => ({
          ...c, meses: Array.from(c.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
        }))
      }))
    }));
  }, [dadosBrutos, busca]);

  const stats = useMemo(() => {
    let t_ia = 0, t_td = 0, t_bu = 0, t_sp = 0, t_final = 0;
    let r_ia = 0, r_td = 0, r_bu = 0, r_sp = 0, r_final = 0;
    const porMes: Record<string, any> = {};

    dadosBrutos.forEach(r => {
      if (!porMes[r.mes_projetado]) porMes[r.mes_projetado] = { vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0 };
      const m = porMes[r.mes_projetado];
      m.vol_ia += r.vol_ia; m.vol_td += r.vol_td; m.vol_bu += r.vol_bu; m.vol_sp += r.vol_supply; m.vol_final += r.vol_irrestrito;
      m.rec_ia += r.rec_ia; m.rec_td += r.rec_td; m.rec_bu += r.rec_bu; m.rec_sp += r.rec_supply; m.rec_final += r.rec_final;
      
      t_ia += r.vol_ia; t_td += r.vol_td; t_bu += r.vol_bu; t_sp += r.vol_supply; t_final += r.vol_irrestrito;
      r_ia += r.rec_ia; r_td += r.rec_td; r_bu += r.rec_bu; r_sp += r.rec_supply; r_final += r.rec_final;
    });

    const chartData = Object.keys(porMes).sort().map(mes => ({
      name: mes.split('-').reverse().slice(1).join('/'),
      IA: porMes[mes].vol_ia, TD: porMes[mes].vol_td, BU: porMes[mes].vol_bu, SP: porMes[mes].vol_sp, Final: porMes[mes].vol_final,
      RIA: porMes[mes].rec_ia, RTD: porMes[mes].rec_td, RBU: porMes[mes].rec_bu, RSP: porMes[mes].rec_sp, RFinal: porMes[mes].rec_final
    }));

    return { porMes, chartData, cards: { vol: { ia: t_ia, td: t_td, bu: t_bu, sp: t_sp, final: t_final }, rec: { ia: r_ia, td: r_td, bu: r_bu, sp: r_sp, final: r_final } } };
  }, [dadosBrutos]);

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const partes = chave.split('|');
        // Se for cliente, a chave é p[2] (razaosocial). Se for SKU, é p[1]. Se for categoria, p[0].
        const queryChave = partes.length === 3 ? `${partes[2]}|${partes[1]}` : (partes.length === 2 ? `|${partes[1]}` : partes[0]);
        const res = await axios.get('/api/v1/consensus/micro/grafico', { params: { chave_matriz: queryChave, tipo_linha: row.original.tipo } });
        setDadosGraficoCache((p: any) => ({ ...p, [chave]: res.data.dados }));
      } catch (e) { console.error(e); } finally { setLoadingGrafico(null); }
    }
  };

  const columns = useMemo(() => {
    if (dadosAgrupados.length === 0) return [];
    const baseCols: any[] = [
      {
        id: 'nome', header: 'Categorias / SKUs / Clientes', accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, nome, produto } = row.original;
          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-1.5 min-w-[380px]">
              {tipo !== 'cliente' ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-100 rounded text-slate-500">
                  {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
              ) : <div className="w-6"></div>}
              
              <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg border transition-colors ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 border-indigo-200 text-indigo-600' : 'hover:bg-slate-100 border-transparent text-slate-400'}`}>
                <Activity className="w-3.5 h-3.5" />
              </button>

              <div className={`w-7 h-7 rounded-full flex items-center justify-center border flex-shrink-0 ${tipo === 'categoria' ? 'bg-slate-900 border-slate-700 text-white' : tipo === 'produto' ? 'bg-indigo-50 border-indigo-100 text-indigo-600' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                {tipo === 'categoria' ? <Layers className="w-3.5 h-3.5" /> : tipo === 'produto' ? <Hash className="w-3.5 h-3.5" /> : <Users className="w-3.5 h-3.5" />}
              </div>

              <div className="flex flex-col">
                <span className={`text-[13px] ${tipo !== 'cliente' ? 'font-black uppercase tracking-tighter' : 'font-bold text-slate-600'}`}>{nome}</span>
                {tipo === 'produto' && <span className="text-[10px] text-slate-400 font-bold">{produto}</span>}
              </div>
            </div>
          )
        }
      }
    ];

    dadosAgrupados[0].meses.forEach((m: any) => {
      const mesStr = m.mes_banco.split('-').reverse().slice(1).join('/');
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: mesStr,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.id]?.[m.mes_banco];
          const valorReal = edicao !== undefined ? edicao.novo_volume : (dadosMes?.vol_final || 0);
          const valorInteiro = Math.round(Number(valorReal));

          if (row.tipo === 'categoria') {
            return <div className="text-center font-black text-slate-900 text-sm">{formatVolume(valorInteiro)}</div>;
          }

          return (
            <div className="flex flex-col w-32 gap-1 relative">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[8px] text-indigo-500 font-black" title="Proposta Comercial">BU: {formatVolume(dadosMes?.vol_bu)}</span>
                 <span className="text-[8px] text-amber-500 font-black" title="Capacidade Supply">SP: {formatVolume(dadosMes?.vol_sp)}</span>
              </div>
              <input
                type="text" value={valorInteiro === 0 ? '' : valorInteiro.toLocaleString('pt-BR')} disabled={meta.isLocked}
                onChange={(e) => meta.updateCell(row.id, m.mes_banco, e.target.value.replace(/\D/g, ''))}
                className={`text-[13px] text-center font-black p-2 rounded-xl outline-none border-2 transition-all w-full
                  ${meta.lockMessage.includes('Publicada') ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : meta.isLocked ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed' : row.tipo === 'produto' ? 'border-dashed border-indigo-200 focus:border-indigo-500 bg-white' : 'bg-slate-50 border-transparent focus:bg-white text-slate-800'} 
                  ${edicao !== undefined && !meta.isLocked ? 'bg-indigo-600 border-indigo-700 text-white' : ''}`}
              />
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [dadosAgrupados, chartExpanded, isLocked, lockMessage]);

  const table = useReactTable({
    data: dadosAgrupados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas, isLocked, lockMessage,
      updateCell: (id: string, mes: string, val: string) => {
        if (isLocked) return; 
        const v = val === '' ? '' : parseInt(val, 10);
        setCelulasEditadas((prev: any) => ({ ...prev, [id]: { ...(prev[id] || {}), [mes]: { novo_volume: isNaN(v as any) ? 0 : v } } }));
      }
    }
  });

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10 relative">
        
        {/* HEADER COM BANNER DE TRAVA */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          {isLocked && !isLoading && (
              <div className={`absolute top-0 left-0 w-full text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10 ${lockMessage.includes('Publicada') ? "bg-emerald-500" : "bg-amber-500"}`}>
                  {lockMessage.includes('Publicada') ? <ShieldCheck className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />} {lockMessage}
              </div>
          )}
          <div className={isLocked && !isLoading ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Globe className="w-8 h-8 text-indigo-600" /> Dashboard Global S&OP
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">Consolidação e Aprovação de Metas</p>
          </div>
          <div className={`flex items-center gap-4 ${isLocked && !isLoading ? "pt-4" : ""}`}>
              <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase border border-slate-200"><Download className="w-4 h-4" /> Exportar Relatório</button>
              <button onClick={handleAprovar} disabled={isProcessing || (isLocked && !lockMessage.includes('Publicada'))} className={`flex items-center gap-2 text-white px-8 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg transition-all ${lockMessage.includes('Publicada') ? 'bg-emerald-500 shadow-none' : isLocked ? 'bg-slate-300 shadow-none' : 'bg-slate-900 hover:bg-black'}`}>
                  {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : lockMessage.includes('Publicada') ? <ShieldCheck className="w-5 h-5" /> : isLocked ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                  {lockMessage.includes('Publicada') ? 'Meta Oficial Salva' : 'Aprovar Plano Final'}
              </button>
          </div>
        </div>

        {/* CARDS DE CASCATA COM VARIAÇÃO % */}
        {!isLoading && dadosAgrupados.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
            {[
              { label: 'Sinal IA', v: stats.cards.vol.ia, r: stats.cards.rec.ia, bV: 0, bR: 0 },
              { label: 'Top-Down', v: stats.cards.vol.td, r: stats.cards.rec.td, bV: stats.cards.vol.ia, bR: stats.cards.rec.ia },
              { label: 'Comercial', v: stats.cards.vol.bu, r: stats.cards.rec.bu, bV: stats.cards.vol.td, bR: stats.cards.rec.td },
              { label: 'Supply', v: stats.cards.vol.sp, r: stats.cards.rec.sp, bV: stats.cards.vol.bu, bR: stats.cards.rec.bu },
              { label: 'Final SOP', v: stats.cards.vol.final, r: stats.cards.rec.final, bV: stats.cards.vol.sp, bR: stats.cards.rec.sp }
            ].map((card, i) => (
              <div key={i} className={`p-5 rounded-[24px] shadow-sm border border-slate-100 flex flex-col justify-between ${i === 4 ? 'bg-indigo-600 text-white' : 'bg-white'}`}>
                 <span className={`text-[10px] font-black uppercase tracking-widest mb-3 ${i === 4 ? 'text-indigo-200' : 'text-slate-400'}`}>{card.label}</span>
                 <div className="space-y-4">
                    <div>
                      <p className="text-2xl font-black leading-none">{formatVolume(card.v)} <span className="text-[10px] opacity-60">CX</span></p>
                      {i > 0 && <VarBadge atual={card.v} anterior={card.bV} />}
                    </div>
                    <div className={`pt-2 border-t ${i === 4 ? 'border-white/10' : 'border-slate-100'}`}>
                      <p className="text-[13px] font-black">{formatMoeda(card.r)}</p>
                      {i > 0 && <VarBadge atual={card.r} anterior={card.bR} />}
                    </div>
                 </div>
              </div>
            ))}
          </div>
        )}

        {/* 2 GRÁFICOS EM BARRAS LADO A LADO */}
        {!isLoading && dadosAgrupados.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            {['Volume Projetado (CX)', 'Receita Projetada (R$)'].map((title, idx) => (
              <div key={idx} className="bg-white rounded-[32px] p-6 shadow-sm border border-slate-100 flex flex-col min-h-[380px]">
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-indigo-500" /> {title}
                </h3>
                <div className="flex-1 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={stats.chartData}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                      <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                      <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => idx === 0 ? `${(v/1000).toFixed(0)}k` : `R$${(v/1000000).toFixed(1)}M`} />
                      <Tooltip formatter={(val: any) => idx === 0 ? formatVolume(val) : formatMoeda(val)} contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)', fontWeight: 900}} />
                      <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: 900}} />
                      <Bar dataKey={idx === 0 ? "IA" : "RIA"} name="IA" fill="#e2e8f0" radius={[4, 4, 0, 0]} />
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
        )}

        {/* PESQUISA */}
        <div className="bg-white p-4 rounded-[24px] shadow-sm border border-slate-100 flex items-center gap-3 mb-6">
           <Search className="w-5 h-5 text-slate-400 ml-2" />
           <input type="text" placeholder="Filtrar por Categoria, Produto ou Cliente..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full bg-transparent py-2 text-sm font-bold text-slate-800 outline-none" />
        </div>

        {/* TABELA DE 3 NÍVEIS COM HISTÓRICO EXPANSÍVEL */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 z-10"><Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" /><span className="text-slate-400 font-black text-xs tracking-widest uppercase">Processando S&OP...</span></div>
          ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm">
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
                    <tr className={`border-b border-slate-50 transition-colors ${row.original.tipo === 'categoria' ? 'bg-slate-50/50' : row.original.tipo === 'produto' ? 'bg-white' : 'bg-slate-50/20'}`}>
                      {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-8 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                    </tr>
                    {chartExpanded === row.original.chave_matriz && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="bg-slate-900 p-8 border-b border-slate-800">
                           <div className="bg-slate-950 rounded-[32px] p-8 shadow-inner border border-slate-800 animate-in fade-in duration-500 h-[350px]">
                              <div className="flex justify-between items-center mb-6">
                                <h3 className="text-sm font-black text-white uppercase tracking-widest flex items-center gap-2"><Activity className="w-4 h-4 text-emerald-400" /> Histórico S&OE: {row.original.nome}</h3>
                                <button onClick={() => setChartExpanded(null)} className="text-slate-500 hover:text-white"><X className="w-5 h-5"/></button>
                              </div>
                              <div className="w-full h-[250px]">
                                 {loadingGrafico === row.original.chave_matriz ? <div className="h-full flex items-center justify-center text-slate-600"><Loader2 className="animate-spin" /></div> : (
                                   <ResponsiveContainer width="100%" height="100%">
                                      <BarChart data={dadosGraficoCache[row.original.chave_matriz] || []}>
                                         <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e293b" />
                                         <XAxis dataKey="name" tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} />
                                         <YAxis tick={{fontSize: 10, fill: '#64748b'}} axisLine={false} />
                                         <Tooltip contentStyle={{backgroundColor: '#0f172a', border: 'none', borderRadius: '12px'}} />
                                         <Bar dataKey="Realizado" name="Faturamento Real" fill="#f8fafc" radius={[4,4,0,0]} />
                                         <Bar dataKey="Consenso" name="Plano S&OP" fill="#10b981" radius={[4,4,0,0]} />
                                      </BarChart>
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
            </table>
          </div>
          )}
        </div>
      </div>
    </div>
  );
}