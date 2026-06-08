import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, 
  getSortedRowModel, SortingState, ColumnDef 
} from '@tanstack/react-table';
import { 
  Loader2, ChevronDown, ChevronRight, Layers, Lock, Download, AlertTriangle, 
  ShieldCheck, Check, Globe, Package, Users, Search, BarChart3, TrendingUp, 
  TrendingDown, Bot, Wand2, Target
} from 'lucide-react';
import axios from 'axios';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const formatMoeda = (valor: any) => {
  const num = Number(valor);
  if (isNaN(num)) return 'R$ 0';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(num));
};

const formatVolume = (val: any) => {
  const num = Number(val);
  if (isNaN(num)) return '0';
  return Math.round(num).toLocaleString('pt-BR');
};

const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

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

const getPmvSeguro = (m: any) => {
  if (!m) return 0;
  if (m.vol_final > 0 && m.rec_final > 0) return m.rec_final / m.vol_final;
  if (m.vol_sp > 0 && m.rec_sp > 0) return m.rec_sp / m.vol_sp;
  if (m.vol_bu > 0 && m.rec_bu > 0) return m.rec_bu / m.vol_bu;
  if (m.vol_td > 0 && m.rec_td > 0) return m.rec_td / m.vol_td;
  if (m.vol_ia > 0 && m.rec_ia > 0) return m.rec_ia / m.vol_ia;
  return 0;
};

const tooltipSorterRow = (item: any) => {
  const order: Record<string, number> = { 'Histórico Real': 1, 'Proposta Mês Passado': 2, 'Sinal IA': 3, 'Restrição Supply': 4, 'Global S&OP': 5, 'Orçamento 2026': 0 };
  return order[item.name] || 99;
};

const SmartInput = ({ value, onChange, disabled }: { value: number, onChange: (val: number) => void, disabled: boolean }) => {
  const [localVal, setLocalVal] = useState((value !== undefined && value !== null) ? formatVolume(value) : '0');
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => { 
    if (!isFocused) setLocalVal((value !== undefined && value !== null) ? formatVolume(value) : '0'); 
  }, [value, isFocused]);

  const handleFocus = () => { setIsFocused(true); setLocalVal(localVal.replace(/\./g, '')); };
  const handleBlur = () => { 
      setIsFocused(false); 
      const num = parseInt(localVal.replace(/\D/g, ''), 10) || 0; 
      setLocalVal(formatVolume(num)); 
      onChange(num); 
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };

  return (
    <input
      type="text" value={localVal} disabled={disabled} onFocus={handleFocus} onBlur={handleBlur} onKeyDown={handleKeyDown} onChange={(e) => setLocalVal(e.target.value)}
      className={`w-full bg-transparent border-none text-center focus:outline-none focus:ring-2 focus:ring-indigo-500 rounded-lg py-1
        ${disabled ? 'text-slate-900 font-black cursor-not-allowed' : 'text-indigo-700 font-black bg-indigo-50 shadow-inner'}`}
    />
  );
};

const AiInsightBox = ({ alvo, tipo }: { alvo: string, tipo: string }) => {
  const [insight, setInsight] = useState('');
  const [loading, setLoading] = useState(false);

  const getInsight = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/ai-sql/perguntar', {
        pergunta: `Faça o dossiê executivo 360° para ${tipo} "${alvo}". Extraia a cascata de volumes, histórico de vendas e calcule as oportunidades. Avalie as restrições de Supply e o desvio da meta do Orçamento.`,
        contexto: { tela_ativa: `Painel Global S&OP`, ciclo_status: 'Analítico/Consolidado' }
      });
      setInsight(res.data.resposta);
    } catch (e) { setInsight('Erro ao gerar insight.'); } finally { setLoading(false); }
  };

  return (
    <div className="bg-indigo-50/50 rounded-[24px] p-6 border border-indigo-100 flex flex-col gap-4 h-full shadow-sm">
      <div className="flex items-center gap-3">
         <div className="p-2.5 bg-indigo-100 text-indigo-600 rounded-xl"><Wand2 className="w-5 h-5" /></div>
         <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest">Nexus AI Insight</h4>
      </div>
      {insight ? (
        <div className="text-sm font-medium text-slate-600 leading-relaxed whitespace-pre-wrap overflow-y-auto custom-scrollbar pr-2" dangerouslySetInnerHTML={{ __html: insight.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-slate-900">$1</strong>').replace(/\n/g, '<br/>') }} />
      ) : (
        <div className="flex flex-col items-start gap-3 mt-2">
           <p className="text-xs text-slate-500 font-medium">Acione o consultor executivo para avaliar o histórico, gargalos de Supply e alinhamento com o Orçamento.</p>
           <button onClick={getInsight} disabled={loading} className="mt-2 text-xs font-bold bg-indigo-600 text-white px-4 py-3 rounded-xl hover:bg-indigo-700 transition flex items-center gap-2 disabled:opacity-50 w-full justify-center shadow-lg shadow-indigo-900/20">
             {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Gerar Parecer Estratégico'}
           </button>
        </div>
      )}
    </div>
  );
};

export default function GlobalDashboard() {
  const [dadosBrutos, setDadosBrutos] = useState<any[]>([]);
  const [orcamentoBase, setOrcamentoBase] = useState<any>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [lockMessage, setLockMessage] = useState('');
  
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);
  const [busca, setBusca] = useState('');
  const [chartExpanded, setChartExpanded] = useState<string | null>(null);
  const [dadosGraficoCache, setDadosGraficoCache] = useState<any>({});
  const [loadingGrafico, setLoadingGrafico] = useState<string | null>(null);
  
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});

  const handleEditCell = (sku: string, mesBanco: string, novoValor: number) => {
    setCelulasEditadas((prev: any) => ({ ...prev, [sku]: { ...(prev[sku] || {}), [mesBanco]: novoValor } }));
  };

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/dashboard/global');
      setDadosBrutos(res.data.dados || []);
      setOrcamentoBase(res.data.orcamento || {});
      setIsLocked(res.data.is_locked);
      setLockMessage(res.data.lock_message);
      setExpanded({}); setChartExpanded(null); setCelulasEditadas({});
    } catch (e) { console.error(e); } finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const mesesDisponiveis = useMemo(() => {
    return Array.from(new Set(dadosBrutos.map(d => d.mes_projetado))).sort() as string[];
  }, [dadosBrutos]);

  const { arvoreDados, skuReferencias } = useMemo(() => {
    if (!dadosBrutos.length) return { arvoreDados: [], skuReferencias: { totaisBU: new Map(), basesSupply: new Map(), basesFinal: new Map() } };
    
    const term = busca.toLowerCase();
    const tree = new Map();
    const totaisBU = new Map();
    const basesSupply = new Map();
    const basesFinal = new Map();
    
    const filtered = dadosBrutos.filter(d => (d.sku + ' ' + d.descricao + ' ' + d.razaosocial + ' ' + d.categoria).toLowerCase().includes(term));
    
    filtered.forEach(r => {
      const mes = r.mes_projetado;
      const razaoLimpa = r.razaosocial || 'CLIENTE NÃO IDENTIFICADO';
      
      if (!totaisBU.has(r.sku)) totaisBU.set(r.sku, {});
      if (!basesSupply.has(r.sku)) basesSupply.set(r.sku, {});
      if (!basesFinal.has(r.sku)) basesFinal.set(r.sku, {});
      
      totaisBU.get(r.sku)[mes] = (totaisBU.get(r.sku)[mes] || 0) + (r.vol_bottomup || 0);
      basesSupply.get(r.sku)[mes] = (basesSupply.get(r.sku)[mes] || 0) + (r.vol_supply || 0);
      basesFinal.get(r.sku)[mes] = (basesFinal.get(r.sku)[mes] || 0) + (r.vol_final || 0);

      if (!tree.has(r.categoria)) {
        tree.set(r.categoria, { id: r.categoria, chave_matriz: r.categoria, nome: r.categoria, tipo: 'categoria', meses: new Map(), filhos: new Map() });
      }
      const cat = tree.get(r.categoria);
      
      if (!cat.filhos.has(r.sku)) {
        cat.filhos.set(r.sku, { id: `${r.categoria}|${r.sku}`, chave_pai: r.categoria, chave_matriz: `${r.categoria}|${r.sku}`, nome: r.descricao, produto: r.sku, tipo: 'produto', meses: new Map(), filhos: new Map() });
      }
      const skuNode = cat.filhos.get(r.sku);

      const clienteKey = `${r.sku}|${razaoLimpa}`;
      if (!skuNode.filhos.has(clienteKey)) {
        skuNode.filhos.set(clienteKey, { id: clienteKey, chave_pai: r.sku, chave_matriz: razaoLimpa, nome: razaoLimpa, tipo: 'cliente', meses: new Map(), filhos: null });
      }
      const cliNode = skuNode.filhos.get(clienteKey);

      [cat, skuNode, cliNode].forEach(node => {
        if (!node.meses.has(mes)) {
            node.meses.set(mes, { 
              mes_banco: mes, vol_ia:0, vol_td:0, vol_bu:0, vol_sp:0, vol_final:0, 
              rec_ia:0, rec_td:0, rec_bu:0, rec_sp:0, rec_final:0, rec_orc:0
            });
        }
        const mNode = node.meses.get(mes);
        mNode.vol_ia += (r.vol_ia || 0);
        mNode.vol_td += (r.vol_topdown || 0);
        mNode.vol_bu += (r.vol_bottomup || 0);
        mNode.vol_sp += (r.vol_supply || 0);
        mNode.vol_final += (r.vol_final || 0);
        
        mNode.rec_ia += (r.rec_ia || 0);
        mNode.rec_td += (r.rec_td || 0);
        mNode.rec_bu += (r.rec_bu || 0);
        mNode.rec_sp += (r.rec_supply || 0);
        mNode.rec_final += (r.rec_final || 0);
      });
    });

    // Injeção do Orçamento Financeiro pela raiz dos SKUs
    Array.from(tree.values()).forEach((cat: any) => {
       Array.from(cat.filhos.values()).forEach((sku: any) => {
          sku.meses.forEach((m: any) => {
             const orcSkuMes = orcamentoBase[sku.produto]?.[m.mes_banco] || 0;
             m.rec_orc = orcSkuMes;
             cat.meses.get(m.mes_banco).rec_orc += orcSkuMes;
          });
       });
    });

    const dataArray = Array.from(tree.values()).map((cat: any) => ({
      ...cat,
      meses: Array.from(cat.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
      subRows: Array.from(cat.filhos.values()).map((sku: any) => ({
        ...sku,
        meses: Array.from(sku.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco)),
        subRows: Array.from(sku.filhos.values()).map((cli: any) => ({
           ...cli,
           meses: Array.from(cli.meses.values()).sort((a: any, b: any) => a.mes_banco.localeCompare(b.mes_banco))
        }))
      }))
    }));

    return { arvoreDados: dataArray, skuReferencias: { totaisBU, basesSupply, basesFinal } };
  }, [dadosBrutos, busca, orcamentoBase]);

  const getDynamicRowVol = useCallback((rowOriginal: any, mesBanco: string): number => {
      if (rowOriginal.tipo === 'produto') {
          const editado = celulasEditadas[rowOriginal.produto]?.[mesBanco];
          const mesData = rowOriginal.meses.find((m:any) => m.mes_banco === mesBanco);
          const dbValue = mesData?.vol_final;
          const baseFinal = (dbValue !== undefined && dbValue !== null) ? dbValue : (mesData?.vol_sp || 0);
          return editado !== undefined ? Number(editado) : baseFinal;
      }
      if (rowOriginal.tipo === 'cliente') {
          const skuId = rowOriginal.chave_pai;
          const editadoSku = celulasEditadas[skuId]?.[mesBanco];
          const mesCliente = rowOriginal.meses.find((m:any) => m.mes_banco === mesBanco);
          const totalBU = skuReferencias.totaisBU.get(skuId)?.[mesBanco] || 1;
          const baseSupply = skuReferencias.basesSupply.get(skuId)?.[mesBanco] || 0;
          const baseFinal = skuReferencias.basesFinal.get(skuId)?.[mesBanco] > 0 ? skuReferencias.basesFinal.get(skuId)[mesBanco] : baseSupply;
          const volAtualSku = editadoSku !== undefined ? Number(editadoSku) : baseFinal;
          const peso = (mesCliente?.vol_bu || 0) / totalBU;
          return Math.round(volAtualSku * peso);
      }
      if (rowOriginal.tipo === 'categoria') {
          return rowOriginal.subRows.reduce((acc: number, child: any) => acc + getDynamicRowVol(child, mesBanco), 0);
      }
      return 0;
  }, [celulasEditadas, skuReferencias]);

  const stats = useMemo(() => {
    let t_ia = 0, t_td = 0, t_bu = 0, t_sp = 0, t_final = 0, r_ia = 0, r_td = 0, r_bu = 0, r_sp = 0, r_final = 0, r_orc = 0;
    
    arvoreDados.forEach((cat: any) => {
      cat.meses.forEach((m: any) => { 
          t_ia += (m.vol_ia || 0); t_td += (m.vol_td || 0); t_bu += (m.vol_bu || 0); t_sp += (m.vol_sp || 0); 
          r_orc += (m.rec_orc || 0);
          const pmvSeguro = getPmvSeguro(m);
          r_ia += (m.vol_ia || 0) * pmvSeguro; 
          r_td += (m.vol_td || 0) * pmvSeguro; 
          r_bu += (m.vol_bu || 0) * pmvSeguro; 
          r_sp += (m.vol_sp || 0) * pmvSeguro; 
      });
      cat.subRows.forEach((sku: any) => {
        sku.meses.forEach((m: any) => {
            const liveVol = getDynamicRowVol(sku, m.mes_banco);
            const pmv = getPmvSeguro(m);
            t_final += liveVol; r_final += (liveVol * pmv);
        });
      });
    });

    return [
      { id: 'ia', label: 'Baseline IA', v: t_ia, r: r_ia, bV: 0, orc: r_orc, icon: <Bot className="w-5 h-5 opacity-70" /> },
      { id: 'td', label: 'Meta Global', v: t_td, r: r_td, bV: t_ia, orc: r_orc, icon: <Globe className="w-5 h-5 opacity-70" /> },
      { id: 'bu', label: 'Proposta Comercial', v: t_bu, r: r_bu, bV: t_td, orc: r_orc, icon: <TrendingUp className="w-5 h-5 opacity-70" /> },
      { id: 'sp', label: 'Restrição Supply', v: t_sp, r: r_sp, bV: t_bu, orc: r_orc, icon: <AlertTriangle className="w-5 h-5 opacity-70" /> },
      { id: 'final', label: 'Plano S&OP Consolidado', v: t_final, r: r_final, bV: t_sp, orc: r_orc, icon: <Check className="w-5 h-5 opacity-70" /> }
    ];
  }, [arvoreDados, getDynamicRowVol]);

  const chartDataBar = useMemo(() => {
    if (!mesesDisponiveis.length) return [];
    return mesesDisponiveis.map((mesBanco) => {
      let ia = 0, topdown = 0, comercial = 0, supply = 0, final = 0, orc = 0;
      let r_ia = 0, r_td = 0, r_com = 0, r_sup = 0, r_fin = 0;

      arvoreDados.forEach((cat: any) => {
        const catMes = cat.meses.find((rm: any) => rm.mes_banco === mesBanco);
        if(catMes) orc += catMes.rec_orc || 0;

        cat.subRows.forEach((sku: any) => {
          const m = sku.meses.find((rm: any) => rm.mes_banco === mesBanco);
          if (m) {
            const liveVol = getDynamicRowVol(sku, mesBanco);
            const pmv = getPmvSeguro(m);
            
            ia += m.vol_ia || 0; topdown += m.vol_td || 0; comercial += m.vol_bu || 0; supply += m.vol_sp || 0;
            final += liveVol;

            r_ia += (m.vol_ia || 0) * pmv; r_td += (m.vol_td || 0) * pmv; r_com += (m.vol_bu || 0) * pmv; r_sup += (m.vol_sp || 0) * pmv;
            r_fin += liveVol * pmv;
          }
        });
      });

      const name = mesBanco.split('-').reverse().slice(1).join('/');
      return { name, mesBanco, IA: ia, TopDown: topdown, Comercial: comercial, Supply: supply, Final: final, RevIA: r_ia, RevTopDown: r_td, RevComercial: r_com, RevSupply: r_sup, RevFinal: r_fin, RevOrcamento: orc };
    });
  }, [arvoreDados, mesesDisponiveis, getDynamicRowVol]);

  const handleAprovar = async () => {
    if (!window.confirm("Confirmar a publicação do plano oficial? Isto irá congelar as metas e aplicar o rateio aos clientes.")) return;
    setIsProcessing(true);
    try {
      const ajustesFormatados = Object.entries(celulasEditadas).flatMap(([sku, meses]: any) => 
         Object.entries(meses).map(([mes_projetado, novo_volume]: any) => ({ nivel: 'sku', chave: sku, mes_projetado, novo_volume }))
      );
      await axios.post('/api/v1/dashboard/aprovar', { ajustes: ajustesFormatados });
      alert("✅ S&OP Global Aprovado e Rateio Aplicado!");
      fetchData(); 
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao aprovar."); } 
    finally { setIsProcessing(false); }
  };

  const handleExportExcel = async () => {
    try {
        const response = await axios.get('/api/v1/dashboard/export', { responseType: 'blob' });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a'); link.href = url; link.setAttribute('download', 'Plano_Oficial_Nexus.xlsx');
        document.body.appendChild(link); link.click(); link.remove();
    } catch (e) { alert("Erro ao exportar."); }
  };

  const toggleChart = async (row: any) => {
    const chave = row.original.chave_matriz;
    if (chartExpanded === chave) { setChartExpanded(null); return; }
    setChartExpanded(chave);
    if (!dadosGraficoCache[chave]) {
      setLoadingGrafico(chave);
      try {
        const res = await axios.get('/api/v1/dashboard/grafico', { params: { chave_matriz: chave, nivel_hierarquia: row.original.tipo } });
        setDadosGraficoCache((p: any) => ({ ...p, [chave]: res.data.dados || [] }));
      } catch (e) { } finally { setLoadingGrafico(null); }
    }
  };

  const columns = useMemo<ColumnDef<any>[]>(() => {
    if (!arvoreDados.length) return [];
    
    const baseCols: ColumnDef<any>[] = [{
        id: 'nome',
        header: 'Hierarquia (Categoria > SKU > Cliente)',
        accessorFn: (row: any) => row.nome,
        cell: (info: any) => {
          const row = info.row;
          const { tipo, nome, produto } = row.original;
          return (
            <div style={{ paddingLeft: `${row.depth * 28}px` }} className="flex items-center gap-3 py-2">
               {row.getCanExpand() ? (
                 <button onClick={row.getToggleExpandedHandler()} className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-500 transition-colors">
                   {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                 </button>
               ) : <div className="w-7" />}
               
               <button onClick={() => toggleChart(row)} className={`p-1.5 rounded-lg border transition-all shadow-sm ${chartExpanded === row.original.chave_matriz ? 'bg-indigo-100 text-indigo-600 border-indigo-200 shadow-indigo-100' : 'bg-white hover:bg-slate-50 text-slate-400 border-slate-200'}`}>
                 <BarChart3 className="w-3.5 h-3.5" />
               </button>
               
               <div className={`w-8 h-8 flex items-center justify-center rounded-xl shadow-sm border ${tipo === 'categoria' ? 'bg-slate-900 text-white border-slate-800' : tipo === 'cliente' ? 'bg-indigo-600 text-white border-indigo-700' : 'bg-white text-slate-500 border-slate-200'}`}>
                  {tipo === 'categoria' ? <Layers className="w-3.5 h-3.5" /> : tipo === 'cliente' ? <Users className="w-3.5 h-3.5" /> : <Package className="w-3.5 h-3.5" />}
               </div>
               
               <div className="flex flex-col overflow-hidden">
                  <span className={`text-[13px] truncate ${tipo !== 'produto' ? 'font-black uppercase tracking-tighter' : 'font-bold text-slate-600'}`} title={nome}>{nome}</span>
                  {tipo === 'produto' && <span className="text-[10px] text-slate-400 font-black tracking-widest">{produto}</span>}
               </div>
            </div>
          )
        }
    }];

    mesesDisponiveis.forEach((mBanco: any) => {
      const mesStr = mBanco.split('-').reverse().slice(1).join('/');
      baseCols.push({
        id: `mes_${mBanco}`,
        header: mesStr,
        accessorFn: (row: any) => getDynamicRowVol(row, mBanco),
        cell: (info: any) => {
          const row = info.row.original;
          const dadosMes = row.meses.find((rm: any) => rm.mes_banco === mBanco);
          const valorInteiro = getDynamicRowVol(row, mBanco);
          const pmv = getPmvSeguro(dadosMes);
          const receitaExibida = valorInteiro * pmv;

          return (
            <div className="flex flex-col items-center justify-center min-w-[140px]">
              <div className="w-24 mb-1">
                 {row.tipo === 'produto' ? (
                    <SmartInput value={valorInteiro} disabled={isLocked} onChange={(val) => handleEditCell(row.produto, mBanco, val)} />
                 ) : (
                    <div className={`font-black text-sm text-center ${row.tipo === 'categoria' ? 'text-slate-900' : 'text-indigo-600'}`}>
                       {formatVolume(valorInteiro)}
                    </div>
                 )}
              </div>
              
              {row.tipo !== 'cliente' && (
                 <div className="flex items-center gap-1.5 mb-1.5">
                    <span className="text-[9px] font-bold bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded shadow-sm" title="Sinal IA">IA: {formatVolume(dadosMes?.vol_ia)}</span>
                    <span className="text-[9px] font-bold bg-indigo-50 text-indigo-500 px-1.5 py-0.5 rounded shadow-sm" title="Meta Top-Down">TD: {formatVolume(dadosMes?.vol_td)}</span>
                 </div>
              )}

              <div className="text-[10px] font-bold text-emerald-600">{formatMoeda(receitaExibida)}</div>
            </div>
          );
        }
      });
    });
    return baseCols;
  }, [arvoreDados, chartExpanded, mesesDisponiveis, isLocked, getDynamicRowVol]);

  const table = useReactTable({
    data: arvoreDados,
    columns,
    state: { expanded, sorting },
    onExpandedChange: setExpanded,
    onSortingChange: setSorting,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (isLoading) return <div className="min-h-screen bg-[#f8fafc] flex flex-col items-center justify-center"><Loader2 className="w-12 h-12 text-indigo-600 animate-spin" /></div>;

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20 relative">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">
        
        {/* --- HEADER --- */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 relative overflow-hidden">
           {isLocked && (
             <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase shadow-sm z-10 bg-emerald-500">
                <ShieldCheck className="w-4 h-4" /> {lockMessage}
             </div>
           )}
           <div className={isLocked ? "pt-6" : ""}>
             <div className="flex items-center gap-4">
                <div className="p-3 bg-indigo-50 rounded-2xl"><Globe className="w-8 h-8 text-indigo-600" /></div>
                <div>
                   <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">Executive S&OP Panel</h1>
                   <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mt-1">Matriz Hierárquica Top-Down de Consenso</p>
                </div>
             </div>
           </div>
           
           <div className={`flex items-center gap-4 ${isLocked ? "pt-6" : ""}`}>
             <button onClick={handleExportExcel} className="flex items-center gap-2 bg-slate-50 hover:bg-slate-100 text-slate-700 font-bold py-3 px-5 rounded-2xl transition-all shadow-sm border border-slate-200">
               <Download className="w-4 h-4" /> Exportar Oficial
             </button>
             <button onClick={handleAprovar} disabled={isLocked || isProcessing} className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white font-bold py-3 px-8 rounded-2xl shadow-lg shadow-indigo-600/30 transition-all">
               {isProcessing ? <Loader2 className="w-4 h-4 animate-spin" /> : (isLocked ? <Lock className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />)}
               {isLocked ? 'Publicado' : 'Publicar Mestre'}
             </button>
           </div>
        </div>

        {/* --- CARDS EXECUTIVOS ALINHADOS --- */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 mb-10">
          {stats.map((card, i) => (
             <div key={card.id} className={`p-6 rounded-[32px] shadow-sm relative overflow-hidden transition-all duration-300 hover:-translate-y-1 hover:shadow-md flex flex-col justify-between min-h-[190px] ${i === 4 ? 'bg-slate-900 text-white' : 'bg-white border border-slate-100'}`}>
                {i === 4 && <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-indigo-500/20 to-transparent rounded-full -mr-10 -mt-10 blur-xl" />}
                
                {/* CABEÇALHO DO CARD */}
                <div className="flex justify-between items-start z-10">
                   <h3 className={`text-[11px] font-black uppercase tracking-widest leading-tight ${i === 4 ? 'text-indigo-300' : 'text-slate-400'}`}>{card.label}</h3>
                   <div className={`p-2 rounded-xl ${i === 4 ? 'bg-slate-800' : 'bg-slate-50'}`}>{card.icon}</div>
                </div>
                
                {/* CORPO DO CARD (Fixo na Base) */}
                <div className="flex flex-col z-10 mt-auto pt-4">
                   <div className="flex items-end gap-2 mb-1">
                      <p className="text-2xl font-black leading-none tracking-tighter">{formatVolume(card.v)} <span className="text-[10px] opacity-60">CX</span></p>
                      {i > 0 && <VarBadge atual={card.v} anterior={card.bV} />}
                   </div>
                   
                   <div className={`pt-3 border-t flex flex-col gap-2.5 ${i === 4 ? 'border-white/10' : 'border-slate-100'}`}>
                      <p className={`text-[13px] font-black ${i === 4 ? 'text-emerald-400' : 'text-slate-800'}`}>{formatMoeda(card.r)}</p>
                      
                      {/* LINHA DE COMPARAÇÃO DE ORÇAMENTO */}
                      <div className={`flex items-center justify-between p-1.5 rounded-lg border ${i === 4 ? 'bg-slate-800/50 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                         <div className="flex flex-col">
                            <span className={`text-[8px] font-black uppercase tracking-widest ${i === 4 ? 'text-slate-400' : 'text-slate-400'}`}>Orçamento 2026</span>
                            <span className={`text-[10px] font-black ${i === 4 ? 'text-slate-300' : 'text-slate-600'}`}>{formatMoeda(card.orc)}</span>
                         </div>
                         <VarBadge atual={card.r} anterior={card.orc} />
                      </div>
                   </div>
                </div>
             </div>
          ))}
        </div>

        {/* --- GRÁFICOS --- */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8 mt-6">
          <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100">
            <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-4 flex items-center gap-2"><Package className="w-5 h-5 text-indigo-500" /> Evolução de Volume S&OP</h3>
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartDataBar} margin={{ top: 10, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis dataKey="name" tick={{fill: '#64748b', fontSize: 12, fontWeight: 'bold'}} axisLine={false} tickLine={false} />
                  <YAxis tickFormatter={(v) => formatVolume(v)} tick={{fill: '#64748b', fontSize: 12}} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{fill: '#f8fafc'}} formatter={(value: any, name: any) => [formatVolume(value), name]} contentStyle={{borderRadius: '12px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)'}} />
                  <Legend iconType="circle" wrapperStyle={{ paddingTop: '20px', fontSize: '12px', fontWeight: 'bold' }} />
                  <Bar dataKey="IA" name="Baseline IA" fill="#94a3b8" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="Comercial" name="Comercial" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="Supply" name="Supply" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="Final" name="Consenso Atual" fill="#10b981" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100">
            <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-4 flex items-center gap-2"><Target className="w-5 h-5 text-emerald-500" /> Projeção Financeira vs Orçamento</h3>
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartDataBar} margin={{ top: 10, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis dataKey="name" tick={{fill: '#64748b', fontSize: 12, fontWeight: 'bold'}} axisLine={false} tickLine={false} />
                  <YAxis tickFormatter={(v) => `R$ ${(Number(v)/1000000).toFixed(1)}M`} tick={{fill: '#64748b', fontSize: 12}} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{fill: '#f8fafc'}} formatter={(value: any, name: any) => [formatMoeda(value), name]} contentStyle={{borderRadius: '12px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)'}} />
                  <Legend iconType="circle" wrapperStyle={{ paddingTop: '20px', fontSize: '12px', fontWeight: 'bold' }} />
                  <Bar dataKey="RevIA" name="Receita IA" fill="#94a3b8" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="RevComercial" name="Receita Comercial" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="RevFinal" name="Receita Atual" fill="#10b981" radius={[4, 4, 0, 0]} />
                  <Line type="monotone" dataKey="RevOrcamento" name="Meta Orçamento" stroke="#f43f5e" strokeWidth={3} strokeDasharray="5 5" dot={{r: 4, strokeWidth: 2}} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        <div className="mb-6 relative">
          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input type="text" placeholder="Localizar Categoria, SKU ou Cliente..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full lg:w-1/3 pl-12 pr-4 py-4 rounded-[20px] bg-white border border-slate-200 text-sm font-bold text-slate-700 shadow-sm focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 transition-all" />
        </div>

        {/* --- TABELA PRINCIPAL --- */}
        <div className="bg-white rounded-[40px] shadow-sm border border-slate-100 overflow-hidden relative">
           <div className="overflow-x-auto relative max-h-[800px] custom-scrollbar">
             <table className="w-full text-left border-collapse relative">
               <thead className="sticky top-0 z-20 shadow-sm">
                 {table.getHeaderGroups().map(hg => (
                   <tr key={hg.id}>
                     {hg.headers.map((h, idx) => (
                       <th key={h.id} className={`bg-slate-900 p-6 ${idx === 0 ? 'min-w-[450px]' : 'border-l border-slate-800/50'}`}>
                          <div className={`flex flex-col ${idx === 0 ? 'items-start' : 'items-center justify-center'}`}>
                             <span className="text-white font-black text-sm tracking-widest">{flexRender(h.column.columnDef.header, h.getContext())}</span>
                             {idx > 0 && <span className="text-indigo-400 text-[10px] font-bold tracking-widest uppercase mt-1">S&OP Forecast</span>}
                          </div>
                       </th>
                     ))}
                   </tr>
                 ))}
               </thead>
               <tbody>
                 {table.getRowModel().rows.map(row => (
                   <Fragment key={row.id}>
                     <tr className={`border-b border-slate-50 transition-colors ${row.depth === 0 ? 'bg-white hover:bg-slate-50' : row.depth === 1 ? 'bg-slate-50/50 hover:bg-slate-100/50' : 'bg-slate-100/30 hover:bg-slate-200/30'}`}>
                       {row.getVisibleCells().map((cell, idx) => (
                         <td key={cell.id} className={`p-4 align-middle ${idx > 0 ? 'border-l border-slate-100' : ''}`}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                         </td>
                       ))}
                     </tr>
                     {/* --- DOSSIÊ CATEGORIA / GRÁFICO (AGORA COM ORÇAMENTO) --- */}
                     {chartExpanded === row.original.chave_matriz && (
                       <tr>
                         <td colSpan={mesesDisponiveis.length + 1} className="p-0 border-b-4 border-indigo-500">
                            <div className="w-full bg-slate-900 p-8 grid grid-cols-1 lg:grid-cols-4 gap-8">
                               <div className="lg:col-span-3 flex flex-col gap-6">
                                  <div className="h-[300px] w-full">
                                     {loadingGrafico === row.original.chave_matriz ? <div className="h-full flex flex-col items-center justify-center text-slate-400 gap-4"><Loader2 className="animate-spin w-8 h-8" /></div> : (
                                       <ResponsiveContainer width="100%" height="100%">
                                          <LineChart 
                                             data={(dadosGraficoCache[row.original.chave_matriz] || []).map((pt: any) => {
                                                const mesProj = mesesDisponiveis.find((m: string) => m === pt.data_iso);
                                                if (mesProj) return { ...pt, Final: getDynamicRowVol(row.original, mesProj) };
                                                return pt;
                                             })} 
                                             margin={{ top: 10, right: 30, left: 10, bottom: 0 }}
                                          >
                                             <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" opacity={0.1} />
                                             <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} dy={10} />
                                             <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(Number(v)/1000).toFixed(0)}k`} />
                                             <Tooltip contentStyle={{backgroundColor: '#fff', border: 'none', borderRadius: '20px', boxShadow: '0 20px 40px rgba(0,0,0,0.1)', fontWeight: 900}} formatter={(value: any, name: any) => [formatVolume(value), name]} itemSorter={tooltipSorterRow} />
                                             <Legend iconType="circle" wrapperStyle={{paddingTop: '20px', fontSize: '11px', fontWeight: 'bold', color: '#fff'}} />
                                             <Line type="monotone" dataKey="Realizado" stroke="#94a3b8" strokeWidth={3} dot={{r:4, strokeWidth:2}} connectNulls={false} />
                                             <Line type="monotone" dataKey="IA" name="Sinal IA" stroke="#64748b" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls={false} />
                                             <Line type="monotone" dataKey="CicloAnterior" name="Proposta Mês Passado" stroke="#a855f7" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                                             <Line type="monotone" dataKey="Supply" name="Restrição Supply" stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 4" dot={false} connectNulls={false} />
                                             <Line type="monotone" dataKey="Final" name="Global S&OP" stroke="#10b981" strokeWidth={4} dot={{r:5, fill:'#10b981', stroke:'#fff', strokeWidth:2}} connectNulls={false} />                                            
                                          </LineChart>
                                       </ResponsiveContainer>
                                     )}
                                  </div>
                               </div>
                               <div className="col-span-1">
                                  <AiInsightBox alvo={row.original.nome} tipo={row.original.tipo} />
                               </div>
                            </div>
                         </td>
                       </tr>
                     )}
                   </Fragment>
                 ))}
               </tbody>
               
               {/* --- RODAPÉ TOTALIZADOR --- */}
               {arvoreDados.length > 0 && (
                 <tfoot className="bg-slate-900 sticky bottom-0 z-20 shadow-[0_-10px_40px_rgba(0,0,0,0.1)] border-t-4 border-indigo-500">
                   <tr>
                     <td className="px-6 py-5">
                       <div className="flex flex-col">
                         <span className="font-black uppercase tracking-widest text-[10px] text-slate-400">Total Consolidação</span>
                         <span className="font-bold text-sm text-white">SUMÁRIO GERENCIAL</span>
                       </div>
                     </td>
                     {mesesDisponiveis.map((mBanco, idx) => {
                       const mesData = chartDataBar.find(c => c.mesBanco === mBanco);
                       return (
                         <td key={idx} className="px-6 py-5 border-l border-slate-800/50 text-center">
                           <div className="flex flex-col items-center">
                             <span className="font-black text-white text-base">
                               {formatVolume(mesData?.Final || 0)} <span className="text-[10px] text-slate-400 font-medium ml-1">CX</span>
                             </span>
                             <span className="font-bold text-emerald-400 text-xs tracking-tight mt-1 bg-emerald-400/10 px-2 py-0.5 rounded">
                               {formatMoeda(mesData?.RevFinal || 0)}
                             </span>
                             {/* GAP ORÇAMENTO RODAPÉ */}
                             <div className="flex items-center justify-center gap-1 mt-1.5 pt-1.5 border-t border-slate-800 w-full">
                               <span className="text-[9px] text-slate-500 font-bold tracking-widest uppercase">Orç: {formatMoeda(mesData?.RevOrcamento || 0)}</span>
                             </div>
                           </div>
                         </td>
                       )
                     })}
                   </tr>
                 </tfoot>
               )}
             </table>
           </div>
        </div>
      </div>
    </div>
  );
}