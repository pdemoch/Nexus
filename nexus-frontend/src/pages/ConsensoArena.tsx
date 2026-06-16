import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getExpandedRowModel } from '@tanstack/react-table';
import { 
  Check, TrendingUp, Filter, Loader2, ChevronDown, ChevronRight, Lock, LockOpen, Search, X, Store, Package, Users, Download, Activity, Target, ShieldCheck, PieChart, LayoutList, Save
} from 'lucide-react';
import axios from 'axios';

const formatMoeda = (valor: number | string | undefined | null) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Number(valor) || 0);
const formatVolume = (val: number | string | undefined | null) => Math.round(Number(val) || 0).toLocaleString('pt-BR');

// COMPONENTE DUAL INPUT (% e R$)
const DualInput = ({ baseRS, currentRS, onChange, disabled }: any) => {
  const [valRS, setValRS] = useState(formatMoeda(currentRS).replace('R$', '').trim());
  const [valPerc, setValPerc] = useState(baseRS > 0 ? ((currentRS / baseRS) * 100).toFixed(1) : '0');
  const [isFocused, setIsFocused] = useState<'RS'|'PERC'|null>(null);

  useEffect(() => {
    if (!isFocused) {
      setValRS(formatMoeda(currentRS).replace('R$', '').trim());
      setValPerc(baseRS > 0 ? ((currentRS / baseRS) * 100).toFixed(1) : '0');
    }
  }, [currentRS, baseRS, isFocused]);

  const handleBlurRS = () => {
    setIsFocused(null);
    const num = parseInt(valRS.replace(/\D/g, ''), 10) || 0;
    onChange(num);
  };

  const handleBlurPerc = () => {
    setIsFocused(null);
    const perc = parseFloat(valPerc.replace(',', '.')) || 0;
    const num = (perc / 100) * baseRS;
    onChange(num);
  };

  return (
    <div className="flex items-center gap-1 w-full bg-slate-50 p-1 rounded-xl border border-slate-200 shadow-inner">
      <div className="relative flex-1 flex items-center">
        <span className="absolute left-2 text-[9px] font-black text-slate-400">%</span>
        <input 
          type="text" value={valPerc} disabled={disabled}
          onFocus={() => setIsFocused('PERC')} onBlur={handleBlurPerc} onChange={e => setValPerc(e.target.value)}
          className={`w-full pl-6 pr-1 py-1.5 rounded-lg text-center text-xs font-black outline-none transition-all ${disabled ? 'bg-transparent text-slate-400 cursor-not-allowed' : 'bg-white text-indigo-700 shadow-sm focus:ring-1 ring-indigo-400'}`}
        />
      </div>
      <div className="relative flex-1 flex items-center">
        <span className="absolute left-2 text-[9px] font-black text-slate-400">R$</span>
        <input 
          type="text" value={valRS} disabled={disabled}
          onFocus={() => {setIsFocused('RS'); setValRS(valRS.replace(/\./g, ''));}} onBlur={handleBlurRS} onChange={e => setValRS(e.target.value)}
          className={`w-full pl-6 pr-1 py-1.5 rounded-lg text-center text-xs font-black outline-none transition-all ${disabled ? 'bg-transparent text-slate-400 cursor-not-allowed' : 'bg-white text-emerald-700 shadow-sm focus:ring-1 ring-emerald-400'}`}
        />
      </div>
    </div>
  );
};

export default function ConsensoArena({ usuarioSessao }: { usuarioSessao?: any }) {
  const role = (usuarioSessao?.funcao || '').toLowerCase();
  const isGerenteOrAdmin = role.includes('admin') || role.includes('diretoria') || role.includes('gerente');

  const [viewMode, setViewMode] = useState<'carteira' | 'portfolio'>('carteira');
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [lockedNodes, setLockedNodes] = useState<Set<string>>(new Set());
  const [isFechado, setIsFechado] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  
  const [opcoesBusca, setOpcoesBusca] = useState<{gerentes: string[], coordenadores: string[], vendedores: string[]}>({gerentes: [], coordenadores: [], vendedores: []});
  const [nomeResponsavel, setNomeResponsavel] = useState('');

  const colunasData = dadosBrutos?.length > 0 ? (dadosBrutos[0]?.subRows[0]?.subRows[0]?.subRows[0]?.subRows[0]?.meses || []) : [];

  useEffect(() => {
    axios.get('/api/v1/consensus/micro/filtros').then(res => {
        const opcoes = res.data || {gerentes: [], coordenadores: [], vendedores: []};
        setOpcoesBusca(opcoes);
        if (isGerenteOrAdmin && opcoes.gerentes?.length > 0) {
            setNomeResponsavel(opcoes.gerentes[0]);
        } else if (!isGerenteOrAdmin && opcoes.coordenadores?.length > 0) {
            setNomeResponsavel(opcoes.coordenadores[0]);
        }
    }).catch(console.error);
  }, [isGerenteOrAdmin]);

  const fetchData = useCallback(async () => {
    if (isGerenteOrAdmin && !nomeResponsavel) return; 
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/micro', { params: { nome_responsavel: nomeResponsavel }});
      setDadosBrutos(res.data?.dados || []);
      setIsFechado(res.data?.is_fechado || false);
      setCelulasEditadas({}); setLockedNodes(new Set()); setExpanded({}); setViewMode('carteira');
    } catch (e) { console.error(e); } finally { setIsLoading(false); }
  }, [nomeResponsavel, isGerenteOrAdmin]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const getSimulations = useCallback((node: any, mes: string) => {
    if (node.tipo === 'produto' || node.tipo === 'produto_macro') {
      const edit = celulasEditadas[node.chave_matriz]?.[mes];
      const m = (node.meses || []).find((x: any) => x.mes_banco === mes);
      const cx = edit !== undefined ? edit.novo_volume : (m?.vol_meta || 0);
      return { cx, rs: cx * (m?.pmv || 0), base_rs: m?.rec_base || 0 };
    }
    const children = (node.subRows || []).map((c: any) => getSimulations(c, mes));
    return {
      cx: children.reduce((s: number, c: any) => s + c.cx, 0),
      rs: children.reduce((s: number, c: any) => s + c.rs, 0),
      base_rs: children.reduce((s: number, c: any) => s + c.base_rs, 0)
    };
  }, [celulasEditadas]);

  const handleEditCascade = (chaveNode: string, mes: string, novoValorRS: number) => {
    if (isFechado) return;
    setCelulasEditadas((prev: any) => {
      const next = { ...prev };
      const findNode = (nodes: any[]): any => {
        for (const n of nodes) {
          if (n.chave_matriz === chaveNode) return n;
          if (n.subRows) { const f = findNode(n.subRows); if (f) return f; }
        }
        return null;
      };
      const target = findNode(dadosBrutos);
      if (!target) return next;

      const leaves: any[] = [];
      const getLeaves = (n: any) => { if (n.tipo === 'produto') leaves.push(n); else (n.subRows || []).forEach(getLeaves); };
      getLeaves(target);

      const totalBase = leaves.reduce((s, l) => s + ((l.meses||[]).find((m:any)=>m.mes_banco===mes)?.rec_base || 0), 0);
      const fractions = leaves.map(l => {
          const m = (l.meses||[]).find((x:any)=>x.mes_banco===mes);
          const weight = m?.rec_base || 0;
          const targetRS = totalBase > 0 ? (weight / totalBase) * novoValorRS : (1/leaves.length) * novoValorRS;
          const pmv = m?.pmv || 1;
          const exactCX = targetRS / pmv;
          const intCX = Math.floor(exactCX);
          return { leaf: l, intCX, pmv, rem: (exactCX - intCX) * pmv };
      });

      let currentRS = fractions.reduce((s, i) => s + (i.intCX * i.pmv), 0);
      fractions.sort((a, b) => b.rem - a.rem);
      for (let i = 0; i < fractions.length; i++) {
          if (currentRS + fractions[i].pmv <= novoValorRS + (fractions[i].pmv / 2)) {
              fractions[i].intCX++; currentRS += fractions[i].pmv;
          }
      }

      fractions.forEach(i => {
          if (!next[i.leaf.chave_matriz]) next[i.leaf.chave_matriz] = {};
          next[i.leaf.chave_matriz][mes] = { novo_volume: i.intCX };
      });
      return next;
    });
  };

  const toggleLock = (chave: string, isValid: boolean) => {
    if (!isValid) return alert("Erro de Tolerância! Ajuste entre 99% e 101% da meta base antes de travar.");
    setLockedNodes(prev => {
        const next = new Set(prev);
        next.has(chave) ? next.delete(chave) : next.add(chave);
        return next;
    });
  };

  // MÁGICA: Reconstrução Dinâmica e Segura da Visão Portfólio (Evita Erro map/find)
  const dadosPortfolio = useMemo(() => {
    if (viewMode !== 'portfolio') return [];
    const folhas: any[] = [];
    const extract = (nodes: any[]) => nodes.forEach(n => { if (n.tipo === 'produto') folhas.push(n); else extract(n.subRows || []); });
    extract(dadosBrutos);

    const mapa: any = {};
    folhas.forEach(f => {
        const cat = f.categoria || 'Geral';
        const seg = f.segmento || 'Sem Segmento';
        if (!mapa[cat]) mapa[cat] = { tipo: 'categoria', nome: cat, subRows: {}, chave_matriz: `CAT|${cat}` };
        if (!mapa[cat].subRows[seg]) mapa[cat].subRows[seg] = { tipo: 'segmento', nome: seg, subRows: {}, chave_matriz: `SEG|${cat}|${seg}` };

        let skuNode = mapa[cat].subRows[seg].subRows[f.produto];
        if (!skuNode) {
            skuNode = { tipo: 'produto_macro', nome: f.nome, produto: f.produto, chave_matriz: `MACRO|${f.produto}`, meses: colunasData.map((m:any) => ({ mes_banco: m.mes_banco, mes_str: m.mes_str, vol_meta: 0, vol_sim: 0 })) };
            mapa[cat].subRows[seg].subRows[f.produto] = skuNode;
        }
        colunasData.forEach((m:any) => {
            const mIdx = skuNode.meses.findIndex((x:any) => x.mes_banco === m.mes_banco);
            const orig = (f.meses || []).find((x:any) => x.mes_banco === m.mes_banco);
            if (mIdx !== -1 && orig) {
                skuNode.meses[mIdx].vol_meta += orig.vol_base;
                skuNode.meses[mIdx].vol_sim += getSimulations(f, m.mes_banco).cx;
            }
        });
    });

    // Agora consolida as sublinhas e cria um array de "meses" seguro para as categorias e segmentos
    return Object.values(mapa).map((cat: any) => {
        const catMesesMapa: any = {};
        cat.subRows = Object.values(cat.subRows).map((seg: any) => {
            const segMesesMapa: any = {};
            seg.subRows = Object.values(seg.subRows);
            seg.subRows.forEach((sku: any) => {
                sku.meses.forEach((m: any) => {
                    if(!segMesesMapa[m.mes_banco]) segMesesMapa[m.mes_banco] = { mes_banco: m.mes_banco, vol_meta: 0, vol_sim: 0 };
                    segMesesMapa[m.mes_banco].vol_meta += m.vol_meta;
                    segMesesMapa[m.mes_banco].vol_sim += m.vol_sim;
                });
            });
            seg.meses = Object.values(segMesesMapa);
            seg.meses.forEach((m: any) => {
                if(!catMesesMapa[m.mes_banco]) catMesesMapa[m.mes_banco] = { mes_banco: m.mes_banco, vol_meta: 0, vol_sim: 0 };
                catMesesMapa[m.mes_banco].vol_meta += m.vol_meta;
                catMesesMapa[m.mes_banco].vol_sim += m.vol_sim;
            });
            return seg;
        });
        cat.meses = Object.values(catMesesMapa);
        return cat;
    });
  }, [dadosBrutos, viewMode, colunasData, getSimulations]);

  const columns = useMemo(() => {
    if (dadosBrutos.length === 0) return [];
    const baseCols: any[] = [{
      id: 'nome', header: viewMode === 'carteira' ? 'Árvore Comercial (Cascata)' : 'Mix de Portfólio (Leitura)',
      cell: (info: any) => {
        const r = info.row; const t = r.original.tipo;
        const icon = t === 'gerente' ? <Target className="w-5 h-5 text-purple-600"/> : t === 'coordenador' ? <Users className="w-4 h-4 text-indigo-600"/> : t === 'vendedor' ? <Users className="w-4 h-4 text-blue-500"/> : t === 'cliente' ? <Store className="w-4 h-4 text-slate-500"/> : <Package className="w-4 h-4 text-slate-400"/>;
        return (
          <div style={{ paddingLeft: `${r.depth * 2}rem` }} className="flex items-center gap-3 py-2">
            {r.getCanExpand() ? (<button onClick={r.getToggleExpandedHandler()} className="p-1.5 text-slate-400 hover:bg-slate-100 rounded-lg">{r.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}</button>) : <div className="w-7"/>}
            <div className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center border shadow-sm shrink-0">{icon}</div>
            <span className={`text-sm ${t==='gerente'?'font-black uppercase': t==='coordenador'?'font-bold':'font-medium text-slate-600'} truncate max-w-[200px]`}>{info.getValue()}</span>
          </div>
        );
      }
    }];

    colunasData.forEach((m: any) => {
      baseCols.push({
        id: `m_${m.mes_banco}`, header: m.mes_str,
        cell: (info: any) => {
          const row = info.row.original; const mStr = m.mes_banco;
          if (viewMode === 'portfolio') {
              const d = (row.meses || []).find((x:any)=>x.mes_banco===mStr);
              return <div className="text-right"><div className="font-black text-slate-800">{formatVolume(d?.vol_sim)} cx</div><div className="text-[10px] text-slate-400">Meta: {formatVolume(d?.vol_meta)} cx</div></div>;
          }

          const { cx, rs, base_rs } = getSimulations(row, mStr);
          const percent = base_rs > 0 ? (rs / base_rs) * 100 : 100;
          const isOk = percent >= 99 && percent <= 101;
          const isLocked = lockedNodes.has(row.chave_matriz);

          // PROTEÇÃO RÍGIDA: Apenas Coordenadores e Vendedores ganham Inputs
          const isEditableType = row.tipo === 'coordenador' || row.tipo === 'vendedor';
          let canEdit = false;
          if (!isFechado && isEditableType) {
             if (row.tipo === 'coordenador' && !isLocked) canEdit = true;
             if (row.tipo === 'vendedor') {
                 const paiChave = row.chave_matriz.split('|').slice(0, 3).join('|'); 
                 if (lockedNodes.has(paiChave) && !isLocked) canEdit = true;
             }
          }

          return (
            <div className="flex flex-col items-center w-36">
                <div className="flex w-full justify-between items-center mb-1 px-1">
                    <span className="text-[9px] text-slate-400 font-bold uppercase tracking-widest">Base: {formatMoeda(base_rs)}</span>
                    {isEditableType && (
                        <button onClick={()=>toggleLock(row.chave_matriz, isOk)} className={`p-1 rounded ${isLocked ? 'bg-rose-100 text-rose-600' : isOk ? 'bg-emerald-100 text-emerald-600 hover:bg-emerald-200' : 'bg-slate-100 text-slate-300 cursor-not-allowed'}`}>
                            {isLocked ? <Lock className="w-3 h-3"/> : <LockOpen className="w-3 h-3"/>}
                        </button>
                    )}
                </div>
                
                {isEditableType ? (
                    <DualInput baseRS={base_rs} currentRS={rs} disabled={!canEdit} onChange={(val: number) => handleEditCascade(row.chave_matriz, mStr, val)} />
                ) : (
                    <div className="w-full text-center py-2 bg-slate-50 rounded-xl border font-black text-slate-700 text-sm shadow-inner">{formatMoeda(rs)}</div>
                )}
                
                <div className="mt-1.5 w-full flex flex-col items-center">
                    <span className="text-[10px] font-black text-slate-500">{formatVolume(cx)} CX</span>
                    <div className={`mt-1 h-1 w-[80%] rounded-full ${isOk ? 'bg-emerald-400' : 'bg-amber-400'}`} />
                </div>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [viewMode, colunasData, getSimulations, lockedNodes, isFechado]);

  const table = useReactTable({ data: viewMode === 'carteira' ? dadosBrutos : dadosPortfolio, columns, state: { expanded }, onExpandedChange: setExpanded, getSubRows: r => r.subRows, getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel() });

  const handleSalvar = async (finalizar: boolean) => {
    setIsProcessing(true);
    const leafEdits = Object.entries(celulasEditadas).filter(([chave]) => chave.split('|').length === 6); 
    const ajustes = leafEdits.flatMap(([chave, meses]: any) => Object.entries(meses).map(([m, v]: any) => ({ chave, mes_projetado: m, novo_volume: v.novo_volume })));
    
    try {
      await axios.post(`/api/v1/consensus/micro/salvar`, { origem_ajuste: 'Metas_Equipe', finalizar_etapa: finalizar, ajustes });
      alert(finalizar ? "✅ Metas publicadas com sucesso!" : "💾 Rascunho gravado.");
      fetchData(); 
    } catch (e: any) { alert("Erro ao salvar."); }
    finally { setIsProcessing(false); }
  };

  const handleExportCSV = () => window.open('/api/v1/consensus/micro/exportar', '_blank');

  if (dadosBrutos.length === 0 && !isLoading) {
      return (
          <div className="h-screen w-full flex flex-col justify-center items-center bg-[#f8fafc]">
              <Target className="w-12 h-12 text-slate-300 mb-4" />
              <h2 className="text-xl font-black text-slate-500">Nenhum dado encontrado para sua equipe.</h2>
          </div>
      );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        {isFechado && !isLoading && (
            <div className="w-full bg-emerald-500 text-white text-[11px] font-black py-2 flex justify-center items-center gap-2 tracking-widest uppercase rounded-t-3xl shadow-sm"><ShieldCheck className="w-4 h-4" /> METAS PUBLICADAS PARA SUPPLY (FECHADO)</div>
        )}

        <div className={`flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 bg-white p-6 shadow-sm border border-slate-100 ${isFechado ? 'rounded-b-[32px]' : 'rounded-[32px]'}`}>
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter"><Target className="w-8 h-8 text-purple-600" /> Metas da Equipe</h1>
            <p className="text-sm font-bold text-slate-400 mt-1 uppercase tracking-widest pl-11">S&OP Gated Waterfall (Distribuição Financeira Top-Down)</p>
          </div>
          <div className="flex items-center gap-3">
             <button onClick={handleExportCSV} className="flex items-center gap-2 text-indigo-600 bg-indigo-50 hover:bg-indigo-100 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase transition-all"><Download className="w-4 h-4" /> Exportar CSV</button>
             {!isFechado && <button onClick={() => handleSalvar(false)} disabled={isProcessing} className="flex items-center gap-2 text-slate-600 bg-slate-100 hover:bg-slate-200 px-5 py-3 rounded-2xl text-xs font-black tracking-widest uppercase transition-all"><Save className="w-4 h-4" /> Rascunho</button>}
             {!isFechado && <button onClick={() => handleSalvar(true)} disabled={isProcessing} className="flex items-center gap-2 text-white bg-slate-900 hover:bg-black px-6 py-3 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg">{isProcessing ? <Loader2 className="w-4 h-4 animate-spin"/> : <Check className="w-4 h-4" />} Publicar (Supply)</button>}
          </div>
        </div>

        <div className="flex flex-col md:flex-row gap-4 mb-6">
            <div className="flex bg-slate-200 p-1.5 rounded-[20px] shadow-inner w-full md:w-auto">
               <button onClick={() => setViewMode('carteira')} className={`flex-1 md:flex-none flex items-center gap-2 px-6 py-2.5 rounded-[16px] text-xs font-black uppercase tracking-widest transition-all ${viewMode === 'carteira' ? 'bg-white text-purple-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}><Users className="w-4 h-4"/> 1. Edição em Cascata</button>
               <button onClick={() => setViewMode('portfolio')} className={`flex-1 md:flex-none flex items-center gap-2 px-6 py-2.5 rounded-[16px] text-xs font-black uppercase tracking-widest transition-all ${viewMode === 'portfolio' ? 'bg-white text-purple-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}><LayoutList className="w-4 h-4"/> 2. Impacto Portfólio</button>
            </div>
            {viewMode === 'carteira' && isGerenteOrAdmin && (
                <div className="flex items-center gap-4 bg-white p-2 rounded-[20px] shadow-sm border border-slate-100 px-4">
                    <select value={nomeResponsavel} onChange={e => setNomeResponsavel(e.target.value)} className="bg-transparent text-sm font-bold text-slate-700 outline-none cursor-pointer">
                        {opcoesBusca?.gerentes.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                    </select>
                </div>
            )}
        </div>

        <div className="bg-white rounded-[40px] shadow-2xl border border-slate-100 overflow-hidden relative min-h-[400px]">
          {isLoading ? <div className="h-64 flex justify-center items-center"><Loader2 className="w-8 h-8 animate-spin text-purple-600"/></div> : (
          <div className="overflow-x-auto pb-4">
            <table className="w-full text-left border-collapse">
              <thead className="bg-slate-50 border-b-2 border-slate-200 shadow-sm">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (
                      <th key={h.id} className="px-6 py-5 text-[10px] font-black text-slate-500 uppercase tracking-widest">{flexRender(h.column.columnDef.header, h.getContext())}</th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className={`border-b border-slate-50 transition-colors ${row.getIsExpanded() ? 'bg-purple-50/20' : row.depth === 0 ? 'bg-slate-50/50' : 'hover:bg-slate-50'}`}>
                    {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-6 py-3">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                  </tr>
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