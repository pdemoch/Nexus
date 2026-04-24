import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, getSortedRowModel, SortingState
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown, 
  Layers, Lock, Download, AlertTriangle, ShieldCheck, Check, Activity, Globe, Package, Users
} from 'lucide-react';
import axios from 'axios';

const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));
const formatVolume = (val: number) => Math.round(val).toLocaleString('pt-BR');

export default function GlobalDashboard() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  
  // =========================================================================
  // STATUS DO CICLO (BLINDAGEM EM CASCATA)
  // =========================================================================
  const [isLocked, setIsLocked] = useState(false);
  const [lockMessage, setLockMessage] = useState('');
  
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);

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
        alert("Erro ao exportar. Verifique se tem permissões e se há dados.");
    }
  };

  const handleAprovar = async () => {
    if (isLocked) return; 
    setIsProcessing(true);
    const ajustes: any[] = [];
    
    Object.entries(celulasEditadas).forEach(([chave, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        ajustes.push({ 
          nivel: chave.includes('_') ? 'cliente' : 'produto', 
          chave: chave, 
          mes_projetado: mes, 
          novo_volume: val.novo_volume === '' ? 0 : val.novo_volume
        });
      });
    });

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

  // Agrupamento de dados para a Tabela (Hierarquia: Produto -> Cliente)
  const dadosAgrupados = useMemo(() => {
    if (!dadosBrutos || dadosBrutos.length === 0) return [];
    
    const mapaProdutos = new Map();
    
    dadosBrutos.forEach(r => {
      const pKey = r.produto;
      if (!mapaProdutos.has(pKey)) {
        mapaProdutos.set(pKey, {
            id: pKey, chave_matriz: pKey, nome: r.descricao, produto: r.produto, categoria: r.categoria, tipo: 'produto',
            meses: new Map(), subRowsMap: new Map()
        });
      }
      
      const prod = mapaProdutos.get(pKey);
      const cKey = `${r.produto}_${r.cliente_razaosocial}`;
      
      if (!prod.subRowsMap.has(cKey)) {
          prod.subRowsMap.set(cKey, {
              id: cKey, chave_matriz: cKey, nome: r.cliente_razaosocial, tipo: 'cliente', meses: new Map()
          });
      }
      const cli = prod.subRowsMap.get(cKey);
      
      const mStr = r.mes_projetado;
      
      // Agrega no Produto
      if (!prod.meses.has(mStr)) {
          prod.meses.set(mStr, { mes_banco: mStr, vol_ia: 0, vol_td: 0, vol_supply: 0, vol_bu: 0, vol_final: 0, receita: 0 });
      }
      const mProd = prod.meses.get(mStr);
      mProd.vol_ia += r.vol_ia; mProd.vol_td += r.vol_td; mProd.vol_supply += r.vol_supply; 
      mProd.vol_bu += r.vol_bu; mProd.vol_final += r.vol_irrestrito; mProd.receita += r.rec_final;
      
      // Atribui no Cliente
      cli.meses.set(mStr, {
          mes_banco: mStr, vol_ia: r.vol_ia, vol_td: r.vol_td, vol_supply: r.vol_supply, 
          vol_bu: r.vol_bu, vol_final: r.vol_irrestrito, receita: r.rec_final
      });
    });
    
    return Array.from(mapaProdutos.values()).map((p: any) => ({
        ...p,
        meses: Array.from(p.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
        subRows: Array.from(p.subRowsMap.values()).map((c: any) => ({
            ...c, meses: Array.from(c.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
        }))
    }));
  }, [dadosBrutos]);

  const totaisGerais = useMemo(() => {
    const totais: Record<string, {vol: number, fat: number}> = {};
    dadosAgrupados.forEach(prod => {
      prod.meses.forEach((m: any) => {
        if (!totais[m.mes_banco]) totais[m.mes_banco] = {vol: 0, fat: 0};
        
        const edicaoProd = celulasEditadas[prod.chave_matriz]?.[m.mes_banco];
        const isPendente = edicaoProd !== undefined;
        const volumeFinal = Math.round(Number(isPendente ? edicaoProd.novo_volume : m.vol_final));
        
        totais[m.mes_banco].vol += volumeFinal;
        totais[m.mes_banco].fat += m.receita; // Faturamento mantido base S&OP para simplificação visual
      });
    });
    return totais;
  }, [dadosAgrupados, celulasEditadas]);

  const columns = useMemo(() => {
    if (dadosAgrupados.length === 0) return [];
    
    const baseCols: any[] = [
      {
        id: 'nome', header: 'SKU / Clientes',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const isProduto = row.original.tipo === 'produto';

          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[350px]">
              {isProduto ? (
                <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-100 text-slate-500 rounded-lg transition-colors">
                  {row.getIsExpanded() ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : <div className="w-8"></div>}
              
              <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 ${isProduto ? 'bg-slate-800 border-slate-700 text-slate-300' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                {isProduto ? <Package className="w-4 h-4" /> : <Users className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col">
                 <span className={`text-sm ${isProduto ? 'font-black text-slate-800 uppercase tracking-tighter' : 'font-bold text-slate-600 truncate max-w-[250px]'}`}>
                   {info.getValue()}
                 </span>
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

    const mesesExemplo = dadosAgrupados[0].meses;
    mesesExemplo.forEach((m: any) => {
      const mesStr = m.mes_banco.split('-').reverse().slice(1).join('/'); // yyyy-mm-dd -> mm/yyyy
      
      baseCols.push({
        id: `mes_${m.mes_banco}`, header: mesStr,
        accessorFn: (row: any) => row.meses.find((rm: any) => rm.mes_banco === m.mes_banco)?.vol_final || 0,
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === m.mes_banco);
          const meta = info.table.options.meta as any;
          const edicao = meta.celulasEditadas[row.chave_matriz]?.[m.mes_banco];
          
          const isPendente = edicao !== undefined;
          const valorReal = isPendente ? edicao.novo_volume : (dadosMes?.vol_final || 0);
          const valorInteiro = Math.round(Number(valorReal));
          
          const isProduto = row.tipo === 'produto';
          const bloqueado = meta.isLocked;
          
          const isGlobalFechado = meta.lockMessage === "Demanda Irrestrita Publicada";

          return (
            <div className="flex flex-col w-36 gap-1.5 relative group">
              <div className="flex justify-between items-center px-1">
                 <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest" title="Proposta Comercial Base">BU: {Math.round(dadosMes?.vol_bu || 0)}</span>
                 <span className="text-[9px] text-amber-500 font-black uppercase tracking-widest" title="Capacidade Supply">SP: {Math.round(dadosMes?.vol_supply || 0)}</span>
              </div>
              
              <input
                type="number" value={valorInteiro === 0 ? '' : valorInteiro} placeholder="0" disabled={bloqueado}
                onChange={(e) => meta.updateCell(row.chave_matriz, m.mes_banco, e.target.value)}
                className={`text-sm text-center font-black p-2.5 rounded-xl outline-none border-2 transition-all w-full
                  ${isGlobalFechado ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 
                    bloqueado ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed opacity-80' : 
                    isProduto ? 'border-dashed border-indigo-200 focus:border-indigo-500 focus:bg-indigo-50 text-indigo-900 bg-white' : 'border-solid bg-slate-50 border-transparent focus:bg-white focus:border-slate-300 text-slate-800'} 
                  ${isPendente && !bloqueado ? 'bg-indigo-600 border-indigo-700 text-white shadow-md' : ''}`}
                title={isGlobalFechado ? "Meta Oficial de Execução Congelada" : bloqueado ? "Aprovação pendente em fases anteriores" : "Ajuste Fino da Diretoria"}
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
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(), getSortedRowModel: getSortedRowModel(),
    meta: {
      celulasEditadas,
      isLocked,
      lockMessage,
      updateCell: (chave: string, mes: string, val: string) => {
        if (isLocked) return; 
        const v = val === '' ? '' : Math.round(Number(val));
        const finalV = Number.isNaN(v as any) && val !== '' ? 0 : v;
        setCelulasEditadas((prev: any) => ({ ...prev, [chave]: { ...(prev[chave] || {}), [mes]: { novo_volume: finalV } } }));
      }
    }
  });

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {/* CABEÇALHO DO PAINEL EXECUTIVO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 relative overflow-hidden">
          
          {/* BANNER DE AVISO (A MÁGICA DA CASCATA ACONTECE AQUI) */}
          {isLocked && !isLoading && (
              <div className={`absolute top-0 left-0 w-full text-white text-[10px] font-black py-1.5 flex justify-center items-center gap-2 tracking-widest uppercase shadow-sm z-10 
                ${lockMessage === "Demanda Irrestrita Publicada" ? "bg-emerald-500" : "bg-amber-500"}`}>
                  {lockMessage === "Demanda Irrestrita Publicada" ? <ShieldCheck className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />} 
                  STATUS: {lockMessage}
              </div>
          )}
          
          <div className={isLocked && !isLoading ? "pt-4" : ""}>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Globe className="w-8 h-8 text-indigo-600" /> S&OP Global
            </h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">
              Visão Executiva & Aprovação de Demanda Irrestrita
            </p>
          </div>
          
          <div className={`flex items-center gap-4 ${isLocked && !isLoading ? "pt-4" : ""}`}>
              <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase transition-all border border-slate-200">
                  <Download className="w-4 h-4" /> Relatório Oficial
              </button>

              <button 
                 onClick={handleAprovar} 
                 disabled={isProcessing || (isLocked && lockMessage !== "Demanda Irrestrita Publicada")} 
                 className={`flex items-center gap-2 text-white px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg 
                   ${lockMessage === "Demanda Irrestrita Publicada" ? 'bg-emerald-500 cursor-default shadow-none' : 
                     isLocked ? 'bg-slate-300 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-black shadow-slate-900/30'}`}
              >
                  {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : 
                   lockMessage === "Demanda Irrestrita Publicada" ? <ShieldCheck className="w-5 h-5" /> : 
                   isLocked ? <Lock className="w-5 h-5" /> : <Check className="w-5 h-5" />}
                  
                  {lockMessage === "Demanda Irrestrita Publicada" ? 'Meta Congelada' : 
                   isLocked ? 'Aprovação Bloqueada' : 'Aprovar e Publicar Demanda'}
              </button>
          </div>
        </div>

        {/* TABELA DE APROVAÇÃO (SÓ RENDERIZA SE HOUVER DADOS) */}
        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/80 backdrop-blur-sm z-10">
                 <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-4" />
                 <span className="text-slate-400 font-black text-xs tracking-widest uppercase">A compilar Visão Global S&OP...</span>
             </div>
          ) : dadosAgrupados.length === 0 ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                 <Layers className="w-12 h-12 mb-4 opacity-20" />
                 <span className="font-black text-sm tracking-widest uppercase">O motor de dados está vazio</span>
             </div>
          ) : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-white border-b-2 border-slate-100 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th key={header.id} className={`px-8 py-6 text-[10px] font-black text-slate-400 uppercase tracking-widest ${header.column.getCanSort() ? 'cursor-pointer hover:bg-slate-50 transition-colors' : ''}`} onClick={header.column.getToggleSortingHandler()}>
                        <div className="flex items-center gap-2">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getCanSort() && (
                            <span className="text-slate-300">
                              {{ asc: <ArrowUp className="w-4 h-4 text-slate-500" />, desc: <ArrowDown className="w-4 h-4 text-slate-500" /> }[header.column.getIsSorted() as string] ?? <ArrowUpDown className="w-4 h-4 opacity-30" />}
                            </span>
                          )}
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-slate-50/50' : row.original.tipo === 'produto' ? 'bg-white hover:bg-slate-50' : 'bg-slate-50/30 hover:bg-slate-100'}`}>
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-8 py-3">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              
              <tfoot className="bg-slate-900 text-white">
                <tr>
                  {table.getHeaderGroups()[0].headers.map(header => {
                    if (header.id === 'nome') return (
                      <td key={header.id} className="px-8 py-5 text-right">
                        <div className="flex flex-col"><span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Receita Final</span><span className="font-bold text-sm text-white">PROJEÇÃO GLOBAL</span></div>
                      </td>
                    );
                    if (header.id.startsWith('mes_')) {
                      const m = header.id.replace('mes_', '');
                      return (
                        <td key={header.id} className="px-8 py-5">
                          <div className="flex flex-col items-center justify-center min-w-[100px]">
                            <span className="font-black text-white">{formatVolume(totaisGerais[m]?.vol || 0)} <span className="text-[9px] text-slate-400">CX</span></span>
                            <span className="font-black text-emerald-400 text-[13px] tracking-tight mt-0.5">{formatMoeda(totaisGerais[m]?.fat || 0)}</span>
                          </div>
                        </td>
                      );
                    }
                    return <td key={header.id} className="px-8 py-5"></td>;
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