import { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import { Layers, Factory, CalendarPlus, Loader2, Trash2, CalendarDays, Package, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { useReactTable, getCoreRowModel, flexRender, getSortedRowModel, SortingState } from '@tanstack/react-table';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));

export default function InboundArena() {
  const [inbounds, setInbounds] = useState<any[]>([]);
  const [skus, setSkus] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([]);

  // Estado do Formulário
  const [formSku, setFormSku] = useState('');
  const [formData, setFormData] = useState('');
  const [formVolume, setFormVolume] = useState('');
  const [formJust, setFormJust] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      // Busca as programações futuras
      const resInbound = await axios.get('/api/v1/soe/inbound');
      setInbounds(resInbound.data || []);

      // Busca a lista de SKUs para o select do formulário (usando a rota do S&OP para facilitar)
      const resSkus = await axios.get('/api/v1/consensus/macro/detalhado');
      // Extrai SKUs únicos do portfólio
      const skusUnicos = Array.from(new Map(resSkus.data.dados.map((item: any) => [item.produto, item])).values());
      setSkus(skusUnicos);

    } catch (e) {
      console.error("Erro ao carregar dados", e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formSku || !formData || !formVolume) return alert("Preencha os campos obrigatórios: SKU, Data e Volume.");

    setIsSaving(true);
    try {
      await axios.post('/api/v1/soe/inbound', {
        sku: formSku,
        data_entrada: formData,
        vol_caixas: parseInt(formVolume, 10),
        justificativa: formJust || ""
      });
      
      // Limpa o form
      setFormSku(''); setFormData(''); setFormVolume(''); setFormJust('');
      await fetchData(); // Recarrega a tabela

    } catch (error: any) {
      alert(error.response?.data?.detail || "Erro ao salvar programação.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Deseja realmente cancelar esta programação de fábrica? Isso pode gerar ruptura na visão Comercial.")) return;
    try {
      await axios.delete(`/api/v1/soe/inbound/${id}`);
      await fetchData();
    } catch (error) {
      alert("Erro ao excluir.");
    }
  };

  const totalProgramado = useMemo(() => inbounds.reduce((acc, curr) => acc + curr.vol_caixas, 0), [inbounds]);

  const columns = useMemo(() => [
    {
      accessorKey: 'data_entrada',
      header: 'Data de Entrada',
      cell: (info: any) => {
        const d = new Date(info.getValue());
        // Ajuste de fuso para não dar o dia anterior
        d.setMinutes(d.getMinutes() + d.getTimezoneOffset());
        return <span className="font-black text-slate-800">{d.toLocaleDateString('pt-BR')}</span>;
      }
    },
    {
      accessorKey: 'sku',
      header: 'SKU (Código)',
      cell: (info: any) => <span className="text-[10px] bg-slate-100 px-2 py-1 rounded-md font-black text-slate-500 uppercase tracking-widest">{info.getValue()}</span>
    },
    {
      accessorKey: 'vol_caixas',
      header: 'Volume (Cx)',
      cell: (info: any) => <span className="font-black text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-xl border border-emerald-100">{info.getValue().toLocaleString('pt-BR')}</span>
    },
    {
      accessorKey: 'usuario_nome',
      header: 'Planejador',
      cell: (info: any) => <span className="text-xs font-bold text-slate-400 uppercase tracking-widest">{info.getValue() || 'Sistema'}</span>
    },
    {
      id: 'acoes',
      header: '',
      cell: (info: any) => (
        <button onClick={() => handleDelete(info.row.original.id)} className="p-2 bg-rose-50 hover:bg-rose-100 text-rose-500 rounded-lg transition-colors ml-auto flex">
           <Trash2 className="w-4 h-4" />
        </button>
      )
    }
  ], []);

  const table = useReactTable({
    data: inbounds,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel()
  });

  if (isLoading) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-fuchsia-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Iniciando Inbound Arena...</span>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative flex flex-col h-full">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 flex-shrink-0">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Factory className="w-8 h-8 text-fuchsia-600" /> INBOUND ARENA <span className="text-slate-300 font-medium ml-2">S&OE</span>
            </h1>
            <p className="text-slate-500 font-medium text-sm mt-1 ml-11">Planejamento e input de entradas futuras no Armazém 05.</p>
          </div>
          
          <div className="flex items-center gap-4 bg-fuchsia-50 border border-fuchsia-100 px-6 py-3.5 rounded-2xl">
            <Package className="w-5 h-5 text-fuchsia-500" />
            <div className="flex flex-col">
              <span className="text-[9px] font-black uppercase tracking-widest text-fuchsia-500 leading-none mb-0.5">Total Programado (Futuro)</span>
              <span className="text-sm font-black text-fuchsia-700 leading-none">{totalProgramado.toLocaleString('pt-BR')} <span className="text-[9px]">CX</span></span>
            </div>
          </div>
        </div>

        <div className="flex flex-col lg:flex-row gap-6 items-start">
          
          {/* COLUNA ESQUERDA: FORMULÁRIO DE INPUT */}
          <div className="w-full lg:w-1/3 bg-white rounded-[32px] p-8 shadow-sm border border-slate-100 flex flex-col gap-6 sticky top-6">
             <div className="flex items-center gap-3 border-b border-slate-100 pb-4">
               <CalendarPlus className="w-6 h-6 text-fuchsia-500" />
               <h2 className="text-base font-black text-slate-800 uppercase tracking-widest">Nova Programação</h2>
             </div>

             <form onSubmit={handleSave} className="flex flex-col gap-5">
               <div className="flex flex-col gap-2">
                 <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">SKU (Produto)</label>
                 <select 
                   value={formSku} onChange={e => setFormSku(e.target.value)} required
                   className="w-full bg-slate-50 border-2 border-slate-100 p-3.5 rounded-2xl text-sm font-bold text-slate-700 outline-none focus:border-fuchsia-500 focus:bg-white transition-all"
                 >
                   <option value="">Selecione o SKU...</option>
                   {skus.map(s => (
                     <option key={s.produto} value={s.produto}>{s.produto} - {s.descricao}</option>
                   ))}
                 </select>
               </div>

               <div className="flex flex-col gap-2">
                 <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Data de Entrada (Disponível Arm 05)</label>
                 <input 
                   type="date" value={formData} onChange={e => setFormData(e.target.value)} required
                   className="w-full bg-slate-50 border-2 border-slate-100 p-3.5 rounded-2xl text-sm font-bold text-slate-700 outline-none focus:border-fuchsia-500 focus:bg-white transition-all"
                 />
               </div>

               <div className="flex flex-col gap-2">
                 <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Volume (Caixas)</label>
                 <input 
                   type="number" value={formVolume} onChange={e => setFormVolume(e.target.value)} required placeholder="Ex: 5000" min="1"
                   className="w-full bg-slate-50 border-2 border-slate-100 p-3.5 rounded-2xl text-sm font-black text-slate-700 outline-none focus:border-fuchsia-500 focus:bg-white transition-all"
                 />
               </div>

               <div className="flex flex-col gap-2">
                 <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Observação / Status (Opcional)</label>
                 <input 
                   type="text" value={formJust} onChange={e => setFormJust(e.target.value)} placeholder="Ex: Atraso de Insumo, Manutenção..."
                   className="w-full bg-slate-50 border-2 border-slate-100 p-3.5 rounded-2xl text-sm font-bold text-slate-700 outline-none focus:border-fuchsia-500 focus:bg-white transition-all"
                 />
               </div>

               <button 
                 type="submit" disabled={isSaving}
                 className="mt-4 flex items-center justify-center gap-2 bg-slate-900 hover:bg-black text-white py-4 rounded-2xl text-sm font-black uppercase tracking-widest transition-all shadow-xl shadow-slate-900/20 disabled:opacity-50"
               >
                 {isSaving ? <Loader2 className="w-5 h-5 animate-spin" /> : <CheckCircle2 className="w-5 h-5 text-emerald-400" />}
                 {isSaving ? 'A Gravar...' : 'Confirmar Inbound'}
               </button>
             </form>
          </div>

          {/* COLUNA DIREITA: GRID DE PROGRAMAÇÕES */}
          <div className="w-full lg:w-2/3 bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col min-h-[500px]">
             <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-100">
               <div className="flex items-center gap-3">
                 <CalendarDays className="w-6 h-6 text-slate-400" />
                 <h2 className="text-base font-black text-slate-800 uppercase tracking-widest">Raio-X do Futuro</h2>
               </div>
               {inbounds.length === 0 && <span className="text-[10px] font-black bg-amber-50 text-amber-600 px-3 py-1 rounded-full uppercase tracking-widest flex items-center gap-1"><AlertTriangle className="w-3 h-3"/> Sem programação</span>}
             </div>

             <div className="flex-1 overflow-x-auto">
               <table className="w-full text-left">
                 <thead>
                   {table.getHeaderGroups().map(hg => (
                     <tr key={hg.id}>
                       {hg.headers.map(h => (
                         <th key={h.id} className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
                           {flexRender(h.column.columnDef.header, h.getContext())}
                         </th>
                       ))}
                     </tr>
                   ))}
                 </thead>
                 <tbody>
                   {table.getRowModel().rows.length === 0 ? (
                     <tr>
                       <td colSpan={5} className="py-20 text-center">
                         <div className="flex flex-col items-center gap-3 opacity-50">
                           <Layers className="w-12 h-12 text-slate-300" />
                           <p className="text-sm font-bold text-slate-400 uppercase tracking-widest">Nenhuma entrada programada na fábrica.</p>
                         </div>
                       </td>
                     </tr>
                   ) : (
                     table.getRowModel().rows.map(row => (
                       <tr key={row.id} className="group hover:bg-slate-50/50 transition-colors border-b border-slate-50">
                         {row.getVisibleCells().map(cell => (
                           <td key={cell.id} className="px-4 py-4">
                             {flexRender(cell.column.columnDef.cell, cell.getContext())}
                           </td>
                         ))}
                       </tr>
                     ))
                   )}
                 </tbody>
               </table>
             </div>
          </div>

        </div>
      </div>
    </div>
  );
}