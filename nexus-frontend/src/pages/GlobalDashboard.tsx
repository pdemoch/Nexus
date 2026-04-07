import { useState, useMemo, useEffect, useCallback } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState } from '@tanstack/react-table';
import { Loader2, ChevronDown, ChevronRight, Save, Layers, Package, Users, ArrowUpDown, ArrowUp, ArrowDown, TrendingUp, ChevronLast, Lock, Download, Unlock, BarChart2 } from 'lucide-react';
import axios from 'axios';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');
const calcVar = (atual: number, base: number) => base > 0 ? ((atual - base) / base) * 100 : 0;

const EditableCell = ({ initialValue, onSave, isChanged, isLocked }: any) => {
  const safeInt = initialValue === 0 ? '' : Math.round(Number(initialValue));
  const [value, setValue] = useState<any>(safeInt);
  
  useEffect(() => { setValue(initialValue === 0 ? '' : Math.round(Number(initialValue))); }, [initialValue]);
  const onBlur = () => { if (value !== (initialValue===0?'':Math.round(Number(initialValue)))) onSave(value === '' ? 0 : value); };

  if (isLocked) {
    return <div className="w-full text-center text-sm font-black p-2 text-slate-600 bg-slate-50 border border-slate-200 rounded-lg">{initialValue === 0 ? '-' : Math.round(Number(initialValue)).toLocaleString('pt-BR')}</div>;
  }

  return (
    <input
      type="number" value={value} placeholder="0" disabled={isLocked}
      onChange={e => setValue(e.target.value === '' ? '' : Math.round(Number(e.target.value)))}
      onBlur={onBlur} onKeyDown={e => e.key === 'Enter' && onBlur()}
      className={`w-full text-center text-sm font-black p-2 rounded-lg border-2 transition-all shadow-sm outline-none
        ${isChanged ? 'bg-emerald-50 border-emerald-500 text-emerald-800' : 'bg-white border-slate-200 hover:border-slate-300 focus:border-indigo-500 text-slate-800'}`}
    />
  );
};

export default function GlobalDashboard() {
  const [clientesArray, setClientesArray] = useState<any[]>([]);
  const [mesesUnicos, setMesesUnicos] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [isLocked, setIsLocked] = useState(false); 
  const [lockMessage, setLockMessage] = useState('Publicar Demanda Irrestrita');
  
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});

  const fetchData = useCallback(async () => {
    try {
      const res = await axios.get(`http://localhost:8000/api/v1/dashboard/global?t=${new Date().getTime()}`);
      setIsLocked(res.data.is_locked); 
      setLockMessage(res.data.lock_message || 'Publicar Demanda Irrestrita');
      
      const clientesMap = new Map();
      const mesesSet = new Set<string>();
      
      res.data.dados.forEach((d: any) => {
        mesesSet.add(d.mes_projetado);
        if (!clientesMap.has(d.chave_matriz)) {
          clientesMap.set(d.chave_matriz, { 
            id: d.chave_matriz, categoria: d.categoria || 'SEM CATEGORIA', 
            produto: d.produto, descricao: d.descricao, 
            cliente: d.cliente_razaosocial, meses: {} 
          });
        }
        clientesMap.get(d.chave_matriz).meses[d.mes_projetado] = { 
          vol_ia: d.vol_ia, vol_td: d.vol_td, vol_bu: d.vol_bu, vol_irrestrito: d.vol_irrestrito, pmv: d.pmv 
        };
      });
      setMesesUnicos(Array.from(mesesSet).sort());
      setClientesArray(Array.from(clientesMap.values()));
    } catch (e) { console.error(e); }
    finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const arvoreDados = useMemo(() => {
    const catMap = new Map();
    clientesArray.forEach(cli => {
      if(!catMap.has(cli.categoria)) catMap.set(cli.categoria, { tipo: 'categoria', id: cli.categoria, nome_exibicao: cli.categoria, childrenMap: new Map() });
      const catNode = catMap.get(cli.categoria);
      if(!catNode.childrenMap.has(cli.produto)) catNode.childrenMap.set(cli.produto, { tipo: 'produto', id: cli.produto, chave_produto: cli.produto, nome_exibicao: cli.descricao, children: [] });
      catNode.childrenMap.get(cli.produto).children.push({ ...cli, tipo: 'cliente', nome_exibicao: cli.cliente });
    });
    return Array.from(catMap.values()).map(cat => ({ ...cat, children: Array.from(cat.childrenMap.values()) }));
  }, [clientesArray]);

  const getMetricasNode = useCallback((node: any, mes: string, editadas: any): any => {
    if (node.tipo === 'cliente') {
      const pmv = node.meses[mes]?.pmv || 0;
      const valFinal = editadas[`cliente|${node.id}|${mes}`] ?? node.meses[mes]?.vol_irrestrito ?? 0;
      
      const vol = Math.round(Number(valFinal));
      const ia = Math.round(Number(node.meses[mes]?.vol_ia || 0));
      const td = Math.round(Number(node.meses[mes]?.vol_td || 0));
      const bu = Math.round(Number(node.meses[mes]?.vol_bu || 0));

      return { 
        vol, fat: vol * pmv, 
        ia, fat_ia: ia * pmv, 
        td, fat_td: td * pmv, 
        bu, fat_bu: bu * pmv 
      };
    }
    const somaFilhos = node.children.reduce((acc: any, c: any) => {
      const r = getMetricasNode(c, mes, editadas);
      return { 
        vol: acc.vol + r.vol, fat: acc.fat + r.fat, 
        ia: acc.ia + r.ia, fat_ia: acc.fat_ia + r.fat_ia, 
        td: acc.td + r.td, fat_td: acc.fat_td + r.fat_td, 
        bu: acc.bu + r.bu, fat_bu: acc.fat_bu + r.fat_bu 
      };
    }, { vol: 0, fat: 0, ia: 0, fat_ia: 0, td: 0, fat_td: 0, bu: 0, fat_bu: 0 });

    if (node.tipo === 'produto') {
      const editKey = `produto|${node.chave_produto}|${mes}`;
      if (editadas[editKey] !== undefined) {
        const novoV = Math.round(Number(editadas[editKey]));
        return { ...somaFilhos, vol: novoV, fat: somaFilhos.vol > 0 ? (novoV / somaFilhos.vol) * somaFilhos.fat : 0 };
      }
    }
    return somaFilhos;
  }, []);

  const handleDistribuirVolume = (produtoNode: any, mes: string) => {
    if (isLocked) return;
    const editKeyProd = `produto|${produtoNode.chave_produto}|${mes}`;
    const novoVolumeTotal = celulasEditadas[editKeyProd];
    if (novoVolumeTotal === undefined) return alert("Digite um valor no produto primeiro para distribuir.");
    
    const metricasOriginais = produtoNode.children.map((cli: any) => ({ id: cli.id, base: Math.round(Number(cli.meses[mes]?.vol_irrestrito || 0)) }));
    const totalBase = metricasOriginais.reduce((acc: number, curr: any) => acc + curr.base, 0);
    
    const novasEdicoes: any = { ...celulasEditadas };
    metricasOriginais.forEach((cli: any) => {
      const peso = totalBase > 0 ? (cli.base / totalBase) : (1 / metricasOriginais.length);
      novasEdicoes[`cliente|${cli.id}|${mes}`] = Math.round(novoVolumeTotal * peso);
    });
    delete novasEdicoes[editKeyProd];
    setCelulasEditadas(novasEdicoes);
  };

  const handleSave = async () => {
    if (isLocked) return;
    if (!window.confirm("Atenção: A Publicação bloqueará a tela para todos os utilizadores. Deseja aprovar este plano oficial e reescrever a base atómica na Coluna Final?")) return;
    setIsProcessing(true);
    try {
      const ajustes = Object.entries(celulasEditadas).map(([k, v]) => ({ nivel: k.split('|')[0], chave: k.split('|')[1], mes_projetado: k.split('|')[2], novo_volume: v === '' ? 0 : v }));
      await axios.post('http://localhost:8000/api/v1/dashboard/aprovar', { ajustes });
      setCelulasEditadas({});
      await fetchData(); 
      window.open('http://localhost:8000/api/v1/dashboard/export', '_blank');
    } catch (e: any) { 
      alert("Erro ao salvar: " + (e.response?.data?.detail || e.message)); 
    } finally { 
      setIsProcessing(false); 
    }
  };

  const handleManualExport = () => {
    window.open('http://localhost:8000/api/v1/dashboard/export', '_blank');
  };

  const stats = useMemo(() => {
    const dataMes: any[] = mesesUnicos.map(m => {
      let mTot = { ia: 0, fat_ia: 0, td: 0, fat_td: 0, bu: 0, fat_bu: 0, final: 0, fat: 0 };
      arvoreDados.forEach(cat => {
        const r = getMetricasNode(cat, m, celulasEditadas);
        mTot.ia += r.ia; mTot.fat_ia += r.fat_ia;
        mTot.td += r.td; mTot.fat_td += r.fat_td;
        mTot.bu += r.bu; mTot.fat_bu += r.fat_bu;
        mTot.final += r.vol; mTot.fat += r.fat;
      });
      return { mesLabel: m.split('-').reverse().slice(1).join('/'), ...mTot };
    });
    
    const glob = dataMes.reduce((acc, curr) => ({ 
      ia: acc.ia + curr.ia, fat_ia: acc.fat_ia + curr.fat_ia, 
      td: acc.td + curr.td, fat_td: acc.fat_td + curr.fat_td, 
      bu: acc.bu + curr.bu, fat_bu: acc.fat_bu + curr.fat_bu, 
      final: acc.final + curr.final, fat: acc.fat + curr.fat 
    }), { ia: 0, fat_ia: 0, td: 0, fat_td: 0, bu: 0, fat_bu: 0, final: 0, fat: 0 });
    
    return { dataMes, glob };
  }, [arvoreDados, mesesUnicos, celulasEditadas, getMetricasNode]);

  const columns = useMemo(() => {
    const cols: any[] = [
      {
        id: 'hierarquia', accessorKey: 'nome_exibicao',
        header: ({ column }: any) => (
          <button className="flex items-center gap-2 hover:text-slate-900 transition font-black" onClick={() => column.toggleSorting()}>
            NÍVEIS DE DECISÃO {column.getIsSorted() ? (column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3"/> : <ArrowDown className="w-3 h-3"/>) : <ArrowUpDown className="w-3 h-3 text-slate-300"/>}
          </button>
        ),
        cell: ({ row }: any) => (
          <div className="flex items-center gap-2" style={{ paddingLeft: `${row.depth * 2.5}rem` }}>
            {row.getCanExpand() && (
              <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-200 rounded-lg">
                {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
              </button>
            )}
            <div className="flex items-center gap-2 truncate">
              {row.depth === 0 ? <Layers className="w-5 h-5 text-indigo-600"/> : row.depth === 1 ? <Package className="w-4 h-4 text-amber-500"/> : <Users className="w-3 h-3 text-slate-400"/>}
              <span className={`uppercase truncate ${row.depth === 0 ? 'font-black text-slate-900 text-sm tracking-tight' : row.depth === 1 ? 'font-bold text-slate-700 text-xs' : 'text-slate-500 text-[11px]'}`}>
                {row.original.nome_exibicao}
              </span>
            </div>
          </div>
        )
      }
    ];

    mesesUnicos.forEach(m => {
      cols.push({
        id: `mes_${m}`, accessorFn: (row: any) => getMetricasNode(row, m, celulasEditadas).vol,
        header: ({ column }: any) => (
          <button 
            onClick={() => column.toggleSorting()} 
            className="flex items-center justify-center gap-2 bg-slate-900 text-white py-2 px-4 rounded-xl text-[11px] font-black tracking-widest w-[160px] hover:bg-slate-800 transition-colors"
          >
            {m.split('-').reverse().slice(1).join('/')}
            {column.getIsSorted() ? (column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3 text-emerald-400"/> : <ArrowDown className="w-3 h-3 text-rose-400"/>) : <ArrowUpDown className="w-3 h-3 opacity-30"/>}
          </button>
        ),
        cell: ({ row }: any) => {
          const res = getMetricasNode(row.original, m, celulasEditadas);
          const isCategoria = row.original.tipo === 'categoria';
          const isProduto = row.original.tipo === 'produto';
          const isCliente = row.original.tipo === 'cliente';

          const editKey = isCliente ? `cliente|${row.original.id}|${m}` : isProduto ? `produto|${row.original.chave_produto}|${m}` : '';
          const isChanged = celulasEditadas[editKey] !== undefined;

          if (isCategoria) {
            return (
              <div className="flex flex-col items-center py-2 min-w-[160px] bg-slate-50/50 rounded-xl border-b-4 border-indigo-100">
                <span className="font-black text-slate-900">{formatVolume(res.vol)} <span className="text-[9px] text-slate-400">CX</span></span>
                <span className="text-[10px] font-bold text-emerald-600">{formatMoeda(res.fat)}</span>
              </div>
            );
          }

          return (
            <div className="flex flex-col items-center justify-center gap-1.5 min-w-[160px] bg-white p-2 rounded-xl border border-slate-100 shadow-sm hover:shadow-md transition-all group">
              <div className="w-full flex items-center justify-between gap-2">
                <EditableCell initialValue={res.vol} isChanged={isChanged} depth={row.depth} isLocked={isLocked} onSave={(newVal: number) => setCelulasEditadas((p: any) => ({ ...p, [editKey]: newVal }))} />
                {isProduto && !isLocked && (
                  <button onClick={() => handleDistribuirVolume(row.original, m)} className="p-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-600 rounded-lg transition-colors" title="Ratear Volume">
                    <ChevronLast className="w-4 h-4 rotate-90" />
                  </button>
                )}
              </div>
              <span className="text-[10px] font-black text-emerald-600">{formatMoeda(res.fat)}</span>
              
              {/* VISÃO COMPARATIVA DE CENÁRIOS TRAVADOS */}
              <div className="grid grid-cols-3 w-full border-t border-slate-100 pt-1 mt-1">
                 <div className="flex flex-col items-center justify-center border-r border-slate-100 px-1" title="Sinal IA">
                    <span className="text-[7px] font-black text-slate-400 uppercase tracking-tighter">IA</span>
                    <span className="text-[9px] font-bold text-slate-600">{formatVolume(res.ia)}</span>
                 </div>
                 <div className="flex flex-col items-center justify-center border-r border-slate-100 px-1" title="Gerencial">
                    <span className="text-[7px] font-black text-blue-400 uppercase tracking-tighter">GER</span>
                    <span className="text-[9px] font-bold text-blue-700">{formatVolume(res.td)}</span>
                 </div>
                 <div className="flex flex-col items-center justify-center px-1" title="Comercial">
                    <span className="text-[7px] font-black text-amber-400 uppercase tracking-tighter">COM</span>
                    <span className="text-[9px] font-bold text-amber-600">{formatVolume(res.bu)}</span>
                 </div>
              </div>
            </div>
          );
        }
      });
    });
    return cols;
  }, [mesesUnicos, celulasEditadas, getMetricasNode, isLocked]);

  const table = useReactTable({
    data: arvoreDados, columns, state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: (r: any) => r.children,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel()
  });

  if (isLoading) {
    return (
      <div className="h-screen w-full bg-slate-50 flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Iniciando S&OP Oficial...</span>
      </div>
    );
  }

  return (
    <div className="w-full bg-slate-50 font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12">
        <div className="sticky top-0 z-40 bg-slate-50/90 backdrop-blur-xl py-6 mb-8 border-b border-slate-200 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6">
          <div>
            <h1 className="text-5xl font-black text-slate-900 tracking-tighter flex items-center gap-4">
              <Layers className="w-12 h-12 text-indigo-600" /> DASHBOARD GLOBAL
            </h1>
            <p className="text-slate-400 font-bold text-xs uppercase tracking-widest mt-2">Torre de Controle de Demanda Irrestrita</p>
          </div>
          
          <div className="flex items-center gap-4">
            <button 
              onClick={handleManualExport}
              className="flex items-center gap-2 px-6 py-5 rounded-[24px] text-xs font-black transition-all border-2 border-slate-200 text-slate-600 hover:bg-white hover:border-slate-300 shadow-sm tracking-widest uppercase"
            >
              <Download className="w-5 h-5" /> Exportar
            </button>

            {/* BOTÃO PUBLICAR SEMPRE ATIVO */}
            <button 
              onClick={handleSave} 
              disabled={isLocked} 
              className={`group flex items-center gap-4 px-12 py-5 rounded-[24px] text-sm font-black transition-all shadow-xl tracking-tighter uppercase 
                ${isLocked ? 'bg-emerald-50 border-2 border-emerald-200 text-emerald-600 shadow-none' 
                : 'bg-slate-900 hover:bg-black text-white hover:scale-105'}`}
            >
              {isProcessing ? <Loader2 className="animate-spin" /> : isLocked ? <Lock className="w-5 h-5" /> : <Save className="w-5 h-5" />}
              {isProcessing ? 'A Gravar...' : lockMessage}
            </button>
          </div>
        </div>

        {/* CARDS DE KPI (COM VARIAÇÕES DUPLAS: VOLUME E FATURAMENTO) */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-6 mb-10">
          {[
            { label: 'IA Base', vol: stats.glob.ia, fat: stats.glob.fat_ia, varVol: 0, varFat: 0, color: 'text-slate-500', border: 'border-slate-200' },
            { label: 'Gerencial', vol: stats.glob.td, fat: stats.glob.fat_td, varVol: calcVar(stats.glob.td, stats.glob.ia), varFat: calcVar(stats.glob.fat_td, stats.glob.fat_ia), color: 'text-blue-600', border: 'border-blue-200' },
            { label: 'Comercial', vol: stats.glob.bu, fat: stats.glob.fat_bu, varVol: calcVar(stats.glob.bu, stats.glob.td), varFat: calcVar(stats.glob.fat_bu, stats.glob.fat_td), color: 'text-amber-500', border: 'border-amber-200' },
            { label: 'Demanda Irrestrita', vol: stats.glob.final, fat: stats.glob.fat, varVol: calcVar(stats.glob.final, stats.glob.bu), varFat: calcVar(stats.glob.fat, stats.glob.fat_bu), color: 'text-emerald-600', border: 'border-emerald-500', highlight: true }
          ].map((c, i) => (
            <div key={i} className={`bg-white p-8 rounded-[40px] shadow-sm border-2 ${c.border} flex flex-col justify-between transition-all ${c.highlight ? 'ring-4 ring-emerald-500/10' : ''}`}>
              <div className="flex justify-between items-start mb-4">
                 <p className={`text-[10px] font-black uppercase tracking-widest ${c.color}`}>{c.label}</p>
                 {i > 0 && (
                   <div className="flex gap-2">
                     <div className={`px-2 py-1 rounded-md text-[9px] font-black flex items-center gap-1 ${c.varVol > 0 ? 'bg-emerald-50 text-emerald-600' : c.varVol < 0 ? 'bg-rose-50 text-rose-600' : 'bg-slate-50 text-slate-500'}`} title="Variação de Volume (vs. anterior)">
                       <Package className="w-3 h-3"/> {c.varVol > 0 ? '+' : ''}{c.varVol.toFixed(1)}%
                     </div>
                     <div className={`px-2 py-1 rounded-md text-[9px] font-black flex items-center gap-1 ${c.varFat > 0 ? 'bg-emerald-50 text-emerald-600' : c.varFat < 0 ? 'bg-rose-50 text-rose-600' : 'bg-slate-50 text-slate-500'}`} title="Variação de Faturamento (vs. anterior)">
                       <TrendingUp className={`w-3 h-3 ${c.varFat < 0 ? 'rotate-180' : ''}`}/> {c.varFat > 0 ? '+' : ''}{c.varFat.toFixed(1)}%
                     </div>
                   </div>
                 )}
              </div>
              <div>
                 <h3 className="text-3xl font-black text-slate-900 mb-1">{formatVolume(c.vol)} <span className="text-[10px] text-slate-400 font-bold uppercase">Caixas</span></h3>
                 <div className={`text-sm font-black flex items-center gap-1.5 ${c.highlight ? 'text-emerald-600' : 'text-slate-500'}`}>
                    R$ {formatMoeda(c.fat).replace('R$', '').trim()} Previsto
                 </div>
              </div>
            </div>
          ))}
        </div>

        {/* GRÁFICOS: APENAS BARRAS NA ORDEM (IA, GER, COM, IRR) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-10">
          <div className="bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 h-[400px] flex flex-col">
            <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2"><TrendingUp className="w-4 h-4 text-emerald-500"/> Faturamento (R$) por Cenário</h3>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={stats.dataMes} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="mesLabel" tick={{fontSize: 11, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={v => `R$${(v/1e6).toFixed(1)}M`} tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                <RechartsTooltip formatter={(v: any) => formatMoeda(v)} contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 20px 40px rgba(0,0,0,0.1)'}} />
                <Legend iconType="circle" wrapperStyle={{fontSize: '10px', fontWeight: 'bold'}}/>
                <Bar dataKey="fat_ia" name="IA Base" fill="#cbd5e1" radius={[4, 4, 0, 0]} barSize={15} />
                <Bar dataKey="fat_td" name="Gerencial" fill="#3b82f6" radius={[4, 4, 0, 0]} barSize={15} />
                <Bar dataKey="fat_bu" name="Comercial" fill="#f59e0b" radius={[4, 4, 0, 0]} barSize={15} />
                <Bar dataKey="fat" name="Irrestrita" fill="#10b981" radius={[4, 4, 0, 0]} barSize={15} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          
          <div className="bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 h-[400px] flex flex-col">
            <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2"><BarChart2 className="w-4 h-4 text-indigo-500"/> Volumes (Cx) Consolidados</h3>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={stats.dataMes} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="mesLabel" tick={{fontSize: 11, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={v => formatVolume(v)} tick={{fontSize: 10, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                <RechartsTooltip formatter={(v: any) => formatVolume(v)} contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 20px 40px rgba(0,0,0,0.1)'}} />
                <Legend iconType="circle" wrapperStyle={{fontSize: '10px', fontWeight: 'bold'}}/>
                <Bar dataKey="ia" name="IA Base" fill="#cbd5e1" radius={[4, 4, 0, 0]} barSize={15} />
                <Bar dataKey="td" name="Gerencial" fill="#3b82f6" radius={[4, 4, 0, 0]} barSize={15} />
                <Bar dataKey="bu" name="Comercial" fill="#f59e0b" radius={[4, 4, 0, 0]} barSize={15} />
                <Bar dataKey="final" name="Irrestrita" fill="#10b981" radius={[4, 4, 0, 0]} barSize={15} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* TABELA DE MATRIZ DE CENÁRIOS */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden">
          <div className="p-8 bg-slate-50 border-b border-slate-100 flex justify-between items-center">
            <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3"><Package className="w-5 h-5 text-indigo-600"/> Matriz de Cenários (Volume & Faturamento)</h3>
            {isLocked && <div className="bg-emerald-100 text-emerald-700 px-4 py-2 rounded-xl text-xs font-black tracking-widest flex items-center gap-2"><Lock className="w-4 h-4"/> BLOQUEADO: {lockMessage}</div>}
          </div>
          <div className="overflow-x-auto pb-4">
            <table className="w-full border-collapse">
              <thead className="bg-white border-b-2 border-slate-100">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (
                      <th key={h.id} className="px-6 py-6 text-left align-bottom">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className={`group transition-all ${row.depth === 0 ? 'bg-slate-50/70' : 'hover:bg-indigo-50/10'}`}>
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-6 py-4 border-b border-slate-50">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-slate-900 text-white">
                <tr>
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'hierarquia') return (
                      <td key={header.id} className="px-6 py-6 text-right">
                        <div className="flex flex-col">
                          <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total Oficial</span>
                          <span className="font-bold text-sm text-white">IRRESTRITA</span>
                        </div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      const tot = stats.dataMes.find(d => d.mesLabel === m.split('-').reverse().slice(1).join('/'));
                      return (
                        <td key={header.id} className="px-6 py-6">
                           <div className="flex flex-col items-center min-w-[160px]">
                              <span className="font-black text-white">{formatVolume(tot?.final || 0)} <span className="text-[10px] text-slate-400">CX</span></span>
                              <span className="text-sm font-black text-emerald-400 mt-1">{formatMoeda(tot?.fat || 0)}</span>
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
        </div>
      </div>
    </div>
  );
}