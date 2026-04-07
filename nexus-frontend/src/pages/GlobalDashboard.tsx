import { useState, useMemo, useEffect, useCallback } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState } from '@tanstack/react-table';
import { Loader2, ChevronDown, ChevronRight, Save, Layers, Package, Users, ArrowUpDown, ArrowUp, ArrowDown, TrendingUp, ChevronLast, Lock, Download, Unlock } from 'lucide-react';
import axios from 'axios';
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

const EditableCell = ({ initialValue, onSave, isChanged, depth, isLocked }: any) => {
  const safeInt = initialValue === 0 ? '' : Math.round(Number(initialValue));
  const [value, setValue] = useState<any>(safeInt);
  
  useEffect(() => { setValue(initialValue === 0 ? '' : Math.round(Number(initialValue))); }, [initialValue]);
  const onBlur = () => { if (value !== (initialValue===0?'':Math.round(Number(initialValue)))) onSave(value === '' ? 0 : value); };

  if (isLocked) {
    return <div className="w-24 text-center text-sm font-black p-3 text-slate-500">{initialValue === 0 ? '-' : Math.round(Number(initialValue)).toLocaleString('pt-BR')}</div>;
  }

  return (
    <input
      type="number" value={value} placeholder="0" disabled={isLocked}
      onChange={e => setValue(e.target.value === '' ? '' : Math.round(Number(e.target.value)))}
      onBlur={onBlur} onKeyDown={e => e.key === 'Enter' && onBlur()}
      className={`w-28 text-center text-sm font-black p-3 rounded-xl border-2 transition-all shadow-sm outline-none
        ${isChanged ? 'bg-emerald-50 border-emerald-500 text-emerald-800' : depth === 1 ? 'bg-white border-slate-100 hover:border-slate-300 focus:border-indigo-500' : 'bg-transparent border-transparent text-slate-500 hover:border-slate-300 focus:bg-white focus:border-indigo-500'}`}
    />
  );
};

export default function GlobalDashboard() {
  const [clientesArray, setClientesArray] = useState<any[]>([]);
  const [mesesUnicos, setMesesUnicos] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [isLocked, setIsLocked] = useState(false); 
  const [canReopen, setCanReopen] = useState(false);
  const [lockMessage, setLockMessage] = useState('Publicar Demanda Oficial');
  
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});

  const fetchData = useCallback(async () => {
    try {
      const res = await axios.get(`http://localhost:8000/api/v1/dashboard/global?t=${new Date().getTime()}`);
      setIsLocked(res.data.is_locked); 
      setLockMessage(res.data.lock_message || 'Publicar Demanda Oficial');
      setCanReopen(res.data.can_reopen || false);
      
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
      const val = editadas[`cliente|${node.id}|${mes}`] ?? node.meses[mes]?.vol_irrestrito ?? 0;
      const volInt = Math.round(Number(val));
      return { 
        vol: volInt, fat: volInt * (node.meses[mes]?.pmv || 0), 
        ia: Math.round(Number(node.meses[mes]?.vol_ia || 0)), 
        td: Math.round(Number(node.meses[mes]?.vol_td || 0)), 
        bu: Math.round(Number(node.meses[mes]?.vol_bu || 0)) 
      };
    }
    const somaFilhos = node.children.reduce((acc: any, c: any) => {
      const r = getMetricasNode(c, mes, editadas);
      return { vol: acc.vol + r.vol, fat: acc.fat + r.fat, ia: acc.ia + r.ia, td: acc.td + r.td, bu: acc.bu + r.bu };
    }, { vol: 0, fat: 0, ia: 0, td: 0, bu: 0 });

    if (node.tipo === 'produto') {
      const valEdit = editadas[`produto|${node.chave_produto}|${mes}`];
      if (valEdit !== undefined) {
        const novoV = Math.round(Number(valEdit));
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

  const handleReopen = async () => {
    if (!window.confirm("Ordem Reversa: Tem certeza que deseja reabrir o ciclo S&OP Final? Isso permitirá destravar os Vendedores no Gerenciamento.")) return;
    try {
      await axios.post('http://localhost:8000/api/v1/dashboard/reabrir');
      alert("✅ S&OP Global Reaberto!");
      fetchData();
    } catch (e: any) {
      alert("Erro ao reabrir: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleManualExport = () => {
    window.open('http://localhost:8000/api/v1/dashboard/export', '_blank');
  };

  const stats = useMemo(() => {
    const dataMes: any[] = mesesUnicos.map(m => {
      let mTot = { ia: 0, td: 0, bu: 0, final: 0, fat: 0 };
      arvoreDados.forEach(cat => {
        const r = getMetricasNode(cat, m, celulasEditadas);
        mTot.ia += r.ia; mTot.td += r.td; mTot.bu += r.bu; mTot.final += r.vol; mTot.fat += r.fat;
      });
      return { mesLabel: m.split('-').reverse().slice(1).join('/'), ...mTot };
    });
    const glob = dataMes.reduce((acc, curr) => ({ ia: acc.ia + curr.ia, td: acc.td + curr.td, bu: acc.bu + curr.bu, final: acc.final + curr.final, fat: acc.fat + curr.fat }), { ia: 0, td: 0, bu: 0, final: 0, fat: 0 });
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
          <button onClick={() => column.toggleSorting()} className="flex items-center justify-center gap-2 w-full text-center bg-slate-900 text-white py-2 px-4 rounded-xl text-[11px] font-black tracking-widest hover:bg-slate-800 transition-colors">
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

          if (isCategoria) return <div className="text-center font-black text-slate-900 py-3 border-b-4 border-indigo-100 bg-slate-50/50 rounded-t-xl">{formatVolume(res.vol)}</div>;

          return (
            <div className="flex flex-col items-center justify-center gap-1 group">
              <EditableCell initialValue={res.vol} isChanged={isChanged} depth={row.depth} isLocked={isLocked} onSave={(newVal: number) => setCelulasEditadas((p: any) => ({ ...p, [editKey]: newVal }))} />
              
              <div className="flex gap-2 text-[9px] font-black tracking-widest uppercase opacity-0 group-hover:opacity-100 transition-opacity mt-1">
                 <span className="text-slate-400" title="Proposta Bottom-Up">BU: {formatVolume(res.bu)}</span>
              </div>

              {isProduto && !isLocked && (
                <button onClick={() => handleDistribuirVolume(row.original, m)} className="p-1 hover:bg-indigo-100 text-indigo-600 rounded-lg transition-colors mt-1"><ChevronLast className="w-4 h-4 rotate-90" /></button>
              )}
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
            {isLocked && canReopen && (
              <button onClick={handleReopen} className="flex items-center gap-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 px-6 py-5 rounded-[24px] text-xs font-black tracking-widest uppercase transition-all shadow-sm">
                  <Unlock className="w-5 h-5" /> Reabrir Global
              </button>
            )}

            <button 
              onClick={handleManualExport}
              className="flex items-center gap-2 px-6 py-5 rounded-[24px] text-xs font-black transition-all border-2 border-slate-200 text-slate-600 hover:bg-white hover:border-slate-300 shadow-sm tracking-widest uppercase"
            >
              <Download className="w-5 h-5" /> Exportar
            </button>

            <button 
              onClick={handleSave} 
              disabled={isLocked || (Object.keys(celulasEditadas).length === 0 && !isLocked)} 
              className={`group flex items-center gap-4 px-12 py-5 rounded-[24px] text-sm font-black transition-all shadow-xl tracking-tighter uppercase 
                ${isLocked ? 'bg-emerald-50 border-2 border-emerald-200 text-emerald-600 shadow-none' 
                : Object.keys(celulasEditadas).length > 0 ? 'bg-slate-900 hover:bg-black text-white hover:scale-105' 
                : 'bg-slate-200 text-slate-400 cursor-not-allowed'}`}
            >
              {isProcessing ? <Loader2 className="animate-spin" /> : isLocked ? <Lock className="w-5 h-5" /> : <Save className="w-5 h-5" />}
              {isProcessing ? 'A Gravar...' : lockMessage}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-8 mb-12">
          {[
            { label: 'IA Base', val: stats.glob.ia, color: 'text-slate-400', border: 'border-slate-200' },
            { label: 'Diretoria (Top-Down)', val: stats.glob.td, color: 'text-blue-600', border: 'border-blue-200' },
            { label: 'Vendas (Bottom-Up)', val: stats.glob.bu, color: 'text-amber-500', border: 'border-amber-200' },
            { label: 'Oficial S&OP Final', val: stats.glob.final, color: 'text-emerald-600', border: 'border-emerald-500', sub: formatMoeda(stats.glob.fat), highlight: true }
          ].map((c, i) => (
            <div key={i} className={`bg-white p-10 rounded-[48px] shadow-sm border-2 ${c.border} transition-all ${c.highlight ? 'ring-4 ring-emerald-500/10' : ''}`}>
              <p className={`text-[11px] font-black uppercase tracking-widest mb-3 ${c.color}`}>{c.label}</p>
              <h3 className="text-4xl font-black text-slate-900">{formatVolume(c.val)} <span className="text-xs text-slate-400 font-bold">CX</span></h3>
              {c.sub && <div className="mt-5 flex items-center gap-2 text-emerald-700 font-black text-sm bg-emerald-50 px-4 py-2 rounded-2xl w-fit"><TrendingUp className="w-4 h-4" /> {c.sub}</div>}
            </div>
          ))}
        </div>

        <div className="bg-white p-10 lg:p-16 rounded-[64px] shadow-sm border border-slate-100 mb-12 h-[600px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={stats.dataMes} margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
              <XAxis dataKey="mesLabel" tick={{fontSize: 14, fontWeight: 900, fill: '#64748b'}} axisLine={false} tickLine={false} />
              <YAxis yAxisId="left" tickFormatter={v => formatVolume(v)} tick={{fontSize: 12, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
              <YAxis yAxisId="right" orientation="right" tickFormatter={v => `R$${(v/1e6).toFixed(1)}M`} tick={{fontSize: 12, fontWeight: 900, fill: '#0f172a'}} axisLine={false} tickLine={false} />
              <RechartsTooltip formatter={(v: any, n: any) => [n === 'fat' ? formatMoeda(v) : formatVolume(v), n === 'fat' ? 'RECEITA' : n.toUpperCase()]} contentStyle={{borderRadius: '32px', border: 'none', boxShadow: '0 40px 80px rgba(0,0,0,0.15)', padding: '20px'}} />
              <Legend verticalAlign="top" height={60} iconType="circle" />
              <Bar yAxisId="left" dataKey="td" name="Diretoria (TD)" fill="#3b82f6" radius={[12, 12, 0, 0]} barSize={40} />
              <Bar yAxisId="left" dataKey="bu" name="Vendas (BU)" fill="#f59e0b" radius={[12, 12, 0, 0]} barSize={40} />
              <Bar yAxisId="left" dataKey="final" name="S&OP Final" fill="#10b981" radius={[12, 12, 0, 0]} barSize={40} />
              <Line yAxisId="right" type="monotone" dataKey="fat" name="Receita" stroke="#0f172a" strokeWidth={6} dot={{r: 8, fill: '#0f172a', strokeWidth: 4, stroke: '#fff'}} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-[64px] shadow-2xl border border-slate-100 overflow-hidden">
          <div className="p-10 bg-slate-50 border-b border-slate-100 flex flex-col md:flex-row justify-between items-center gap-4 text-center md:text-left">
            <h3 className="font-black text-slate-900 uppercase tracking-widest text-sm flex items-center gap-3"><Package className="w-5 h-5 text-indigo-600"/> Matriz de Decisão S&OP Final</h3>
            {isLocked && <div className="bg-emerald-100 text-emerald-700 px-4 py-2 rounded-xl text-xs font-black tracking-widest flex items-center gap-2"><Lock className="w-4 h-4"/> BLOQUEADO: {lockMessage}</div>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead className="bg-white border-b-2 border-slate-100">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (
                      <th key={h.id} className="px-10 py-8 text-left text-[11px] font-black text-slate-400 uppercase tracking-widest">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className={`group transition-all ${row.depth === 0 ? 'bg-slate-50/70' : 'hover:bg-indigo-50/30'}`}>
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-10 py-5 border-b border-slate-50">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
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