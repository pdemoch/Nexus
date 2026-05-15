import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel,
  ColumnDef
} from '@tanstack/react-table';
import { 
    Loader2, ChevronDown, ChevronRight, Layers, Package, Target, 
    ShieldCheck, Check, Search, Download, TrendingUp, TrendingDown,
    BrainCircuit, BarChart3, Activity, Wand2, Filter
} from 'lucide-react';
import axios from 'axios';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import * as XLSX from 'xlsx';

// --- HELPERS ---
const formatMoeda = (valor: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor || 0));
const formatVolume = (val: number) => Math.round(val || 0).toLocaleString('pt-BR');
const calcVar = (atual: number, ant: number) => ant > 0 ? ((atual - ant) / ant) * 100 : 0;

const FvaBadge = ({ atual, anterior, label }: { atual: number, anterior: number, label: string }) => {
    const v = calcVar(atual, anterior);
    if (v === 0 || anterior === 0) return null;
    const isPos = v > 0;
    return (
      <span className={`text-[9px] font-black flex items-center gap-0.5 px-1 rounded ${isPos ? 'text-emerald-600' : 'text-rose-600'}`} title={`Variação de ${label} vs Ciclo Anterior`}>
        {isPos ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />} {Math.abs(v).toFixed(1)}%
      </span>
    );
};

const AiInsightBox = ({ alvo, tipo }: { alvo: string, tipo: string }) => {
  const [insight, setInsight] = useState('');
  const [loading, setLoading] = useState(false);

  const getInsight = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/ai-sql/perguntar', {
        pergunta: `Faça o dossiê executivo 360° para a ${tipo} "${alvo}". Extraia a cascata de volumes, histórico de vendas e pmv_aplicado. Apresente o diagnóstico financeiro (R$) e estratégico utilizando a metodologia dos 4 pilares.`,
        contexto: { tela_ativa: 'Top-Down Arena', ciclo_status: 'Aberto' }
      });
      setInsight(res.data.resposta);
    } catch (e) {
      setInsight('Erro ao gerar insight. Verifique a conexão com o Agente SQL.');
    } finally { setLoading(false); }
  };

  return (
    <div className="bg-indigo-50/50 rounded-3xl p-6 border border-indigo-100 flex flex-col gap-4 h-full shadow-sm">
      <div className="flex items-center gap-3">
         <div className="p-2.5 bg-indigo-100 text-indigo-600 rounded-xl"><Wand2 className="w-5 h-5" /></div>
         <h4 className="text-sm font-black text-slate-800 uppercase tracking-widest">Nexus AI Insight 360°</h4>
      </div>
      {insight ? (
        <div className="text-sm font-medium text-slate-600 leading-relaxed whitespace-pre-wrap overflow-y-auto custom-scrollbar pr-2 max-h-[200px]" dangerouslySetInnerHTML={{ __html: insight.replace(/\*\*(.*?)\*\*/g, '<strong class="font-black text-slate-900">$1</strong>').replace(/\n/g, '<br/>') }} />
      ) : (
        <div className="flex flex-col items-start gap-3 mt-2 h-full justify-center">
           <p className="text-xs text-slate-500 font-medium">Acione o consultor executivo para avaliar a performance e os riscos financeiros desta {tipo.toLowerCase()}.</p>
           <button onClick={getInsight} disabled={loading} className="mt-2 text-xs font-bold bg-indigo-600 text-white px-4 py-3 rounded-xl hover:bg-indigo-700 transition flex items-center gap-2 disabled:opacity-50 w-full justify-center shadow-lg shadow-indigo-900/20">
             {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Gerar Parecer Estratégico'}
           </button>
        </div>
      )}
    </div>
  );
};

// =========================================================
// DOSSIÊ DA CATEGORIA (BLOCO SUPERIOR E INFERIOR)
// =========================================================
const CategoriaDossier = ({ data }: { data: any }) => {
    const chartData = data.meses.map((m: any) => ({
        name: m.mes.split('-')[1] + '/' + m.mes.split('-')[0],
        "Ciclo Anterior": m.vol_ciclo_anterior, "Nova Projeção": m.vol_td
    }));
    const receitaTotal = data.meses.reduce((acc: number, m: any) => acc + m.rec_td, 0);
    const volumeTotal = data.meses.reduce((acc: number, m: any) => acc + m.vol_td, 0);

    return (
        <div className="p-8 bg-slate-50/80 border-y border-slate-200">
            <div className="flex flex-col gap-6">
                
                {/* ANDAR SUPERIOR */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100 flex flex-col justify-center">
                        <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2"><Target className="w-4 h-4"/> Saúde da Categoria</h4>
                        <p className="text-3xl font-black text-slate-900 mb-1">{formatMoeda(receitaTotal)}</p>
                        <p className="text-sm font-bold text-slate-500 mb-6">Receita Projetada (M2-M4)</p>
                        <div className="p-4 bg-indigo-50 text-indigo-800 rounded-2xl">
                            <p className="text-[11px] font-black uppercase mb-1">Volume Projetado</p>
                            <p className="text-xl font-black">{formatVolume(volumeTotal)} CX</p>
                        </div>
                    </div>
                    <div><AiInsightBox alvo={data.nome} tipo="Categoria" /></div>
                </div>

                {/* ANDAR INFERIOR (GRÁFICO EXPANDIDO 100%) */}
                <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100">
                    <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2"><BarChart3 className="w-4 h-4"/> Ponte de Ciclo (FVA Macro)</h4>
                    <div className="h-[250px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                                <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                                <Tooltip cursor={{fill: '#f8fafc'}} contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                <Legend iconType="circle" wrapperStyle={{fontSize: '11px', fontWeight: 900}} />
                                <Bar dataKey="Ciclo Anterior" fill="#cbd5e1" radius={[4, 4, 0, 0]} />
                                <Bar dataKey="Nova Projeção" fill="#6366f1" radius={[4, 4, 0, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>

            </div>
        </div>
    );
};

// =========================================================
// DOSSIÊ DO SKU (BLOCO SUPERIOR E INFERIOR)
// =========================================================
const SkuDossier = ({ data, celulasEditadas }: { data: any, celulasEditadas: any }) => {
    const [chartDataOrigem, setChartDataOrigem] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        axios.get('/api/v1/consensus/macro/grafico', { params: { produto: data.sku } })
            .then(res => { setChartDataOrigem(res.data.dados || []); setLoading(false); })
            .catch(() => setLoading(false));
    }, [data.sku]);

    const chartDataDinamico = useMemo(() => {
        if (!chartDataOrigem.length) return [];
        return chartDataOrigem.map(point => {
            const mesBanco = point.name + '-01'; 
            const edicao = celulasEditadas[data.sku]?.[mesBanco];
            if (edicao && edicao.novo_volume !== '') {
                return { ...point, Comercial: parseInt(edicao.novo_volume), Final: parseInt(edicao.novo_volume) };
            }
            return point;
        });
    }, [chartDataOrigem, celulasEditadas, data.sku]);

    return (
        <div className="p-8 bg-slate-50/80 border-y border-slate-200">
            <div className="flex flex-col gap-6">
                
                {/* ANDAR SUPERIOR */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100 flex flex-col gap-4">
                        <h4 className="text-xs font-black text-indigo-400 uppercase tracking-widest flex items-center gap-2"><BrainCircuit className="w-4 h-4"/> Engine Estatística</h4>
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase">Modelo Vencedor</p>
                            <p className="text-lg font-black text-slate-800">{data.modelo_vencedor}</p>
                        </div>
                        <div className="mt-2">
                            <p className="text-[10px] font-bold text-slate-400 uppercase">Acurácia IA Histórica</p>
                            <div className="flex items-end gap-2">
                                <p className="text-3xl font-black text-emerald-500">{data.acuracia_ia.toFixed(1)}%</p>
                                <span className="text-[10px] font-bold text-emerald-600 mb-1 bg-emerald-50 px-2 py-0.5 rounded">Alta Confiança</span>
                            </div>
                        </div>
                    </div>
                    <div><AiInsightBox alvo={data.descricao} tipo="SKU" /></div>
                </div>

                {/* ANDAR INFERIOR (GRÁFICO EXPANDIDO 100%) */}
                <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100">
                    <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2"><Activity className="w-4 h-4"/> S&OE Timeline Contínua (24 Meses)</h4>
                    {/* A altura do gráfico subiu de 220px para 320px para melhor análise do histórico longo */}
                    <div className="h-[320px] w-full">
                        {loading ? <div className="h-full flex items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-slate-300" /></div> : (
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={chartDataDinamico} margin={{ top: 5, right: 20, left: 0, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                                    <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} />
                                    <YAxis tick={{fontSize: 10, fontWeight: 900, fill: '#94a3b8'}} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                                    <Tooltip contentStyle={{borderRadius: '16px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                                    <Legend iconType="circle" wrapperStyle={{fontSize: '11px', fontWeight: 900}} />
                                    <Line type="monotone" dataKey="Realizado" stroke="#0f172a" strokeWidth={3} dot={{r: 4, fill: '#0f172a'}} connectNulls />
                                    <Line type="monotone" dataKey="IA" stroke="#94a3b8" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls />
                                    <Line type="monotone" dataKey="Comercial" stroke="#6366f1" strokeWidth={2} dot={{r: 3}} connectNulls />
                                    <Line type="monotone" dataKey="Final" stroke="#10b981" strokeWidth={4} dot={{r: 5, fill: '#10b981'}} connectNulls />
                                </LineChart>
                            </ResponsiveContainer>
                        )}
                    </div>
                </div>

            </div>
        </div>
    );
};

// =====================================================================
// MAIN COMPONENT: TOP-DOWN ARENA
// =====================================================================
export default function TopDownArena() {
  const [hierarquiaBruta, setHierarquiaBruta] = useState<any[]>([]);
  const [mesesJanela, setMesesJanela] = useState<string[]>([]);
  const [cicloAtivo, setCicloAtivo] = useState('');
  const [isCicloFechado, setIsCicloFechado] = useState(false);
  
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});
  const [busca, setBusca] = useState('');
  const [categoriaSelecionada, setCategoriaSelecionada] = useState('TODAS');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [res, statusRes] = await Promise.all([
        axios.get('/api/v1/consensus/macro'),
        axios.get('/api/v1/consensus/macro/status')
      ]);
      setHierarquiaBruta(res.data.hierarquia || []);
      setMesesJanela(res.data.meses_janela || []);
      setCicloAtivo(res.data.ciclo_ativo || '');
      setIsCicloFechado(statusRes.data.is_topdown_fechado);
      setExpanded({});
    } catch (e) { console.error(e); } 
    finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const categoriasUnicas = useMemo(() => Array.from(new Set(hierarquiaBruta.map(c => c.nome))).sort(), [hierarquiaBruta]);

  const dadosFormatados = useMemo(() => {
    if (!hierarquiaBruta.length) return [];
    
    return hierarquiaBruta
      .filter(cat => categoriaSelecionada === 'TODAS' || cat.nome === categoriaSelecionada)
      .map(cat => {
        const skusFiltrados = cat.skus.filter((s:any) => (s.sku + ' ' + s.descricao).toLowerCase().includes(busca.toLowerCase()));
        if (skusFiltrados.length === 0) return null;

        const mesesCategoria = mesesJanela.map(mesStr => {
            const totaisM = { mes: mesStr, vol_ia: 0, vol_td: 0, vol_final: 0, vol_ciclo_anterior: 0, rec_td: 0, rec_ciclo_anterior: 0, pmv_medio: 0 };
            skusFiltrados.forEach((s:any) => {
                const mk = s.meses.find((x:any) => x.mes === mesStr);
                const edicao = celulasEditadas[s.sku]?.[mesStr];
                const isEdited = edicao !== undefined && edicao.novo_volume !== '';
                const volAtual = isEdited ? parseInt(edicao.novo_volume) : (mk ? mk.vol_td : 0);

                if (mk) {
                    totaisM.pmv_medio = mk.pmv_medio;
                    totaisM.vol_ia += mk.vol_ia;
                    totaisM.vol_td += volAtual; 
                    totaisM.vol_final += mk.vol_final;
                    totaisM.vol_ciclo_anterior += mk.vol_ciclo_anterior;
                    totaisM.rec_td += (volAtual * mk.pmv_medio); 
                    totaisM.rec_ciclo_anterior += (mk.vol_ciclo_anterior * mk.pmv_medio);
                }
            });
            return totaisM;
        });

        return { ...cat, isCategory: true, meses: mesesCategoria, subRows: skusFiltrados.map((s:any) => ({ ...s, isSku: true })) };
    }).filter(Boolean);
  }, [hierarquiaBruta, mesesJanela, busca, categoriaSelecionada, celulasEditadas]);

  // TOTALIZADORES GERAIS (BARRA FIXA INFERIOR)
  const totaisGerais = useMemo(() => {
    const totais: Record<string, { vol: number, rec: number }> = {};
    mesesJanela.forEach(m => totais[m] = { vol: 0, rec: 0 });
    
    dadosFormatados.forEach(cat => {
        mesesJanela.forEach(mesStr => {
           const m = cat.meses.find((x:any) => x.mes === mesStr);
           if(m) {
               totais[mesStr].vol += m.vol_td;
               totais[mesStr].rec += m.rec_td;
           }
        });
    });
    return totais;
  }, [dadosFormatados, mesesJanela]);

  const onCellChange = (sku: string, mesStr: string, val: string) => {
    setCelulasEditadas((prev: any) => ({ ...prev, [sku]: { ...(prev[sku] || {}), [mesStr]: { novo_volume: val } } }));
  };

  const handleExportExcel = (isSnapshot = false) => {
    if (dadosFormatados.length === 0) return alert("Não há dados na tela para exportar.");
    const dadosExcel: any[] = [];
    dadosFormatados.forEach(cat => {
        cat.subRows.forEach((row: any) => {
            const linha: any = { "CATEGORIA": cat.nome, "CÓDIGO SKU": row.sku, "DESCRIÇÃO": row.descricao, "MODELO IA": row.modelo_vencedor, "ACURÁCIA IA (%)": row.acuracia_ia };
            mesesJanela.forEach(mesStr => {
                const m = row.meses.find((x:any) => x.mes === mesStr);
                const edicao = celulasEditadas[row.sku]?.[mesStr];
                const volFinal = Math.round(Number(edicao !== undefined && edicao.novo_volume !== '' ? edicao.novo_volume : (m ? m.vol_td : 0)));
                linha[`${mesStr} (Base IA)`] = Math.round(Number(m ? m.vol_ia : 0));
                linha[`${mesStr} (Ciclo Anterior)`] = Math.round(Number(m ? m.vol_ciclo_anterior : 0));
                linha[`${mesStr} (Top-Down)`] = volFinal;
            });
            dadosExcel.push(linha);
        });
    });
    const worksheet = XLSX.utils.json_to_sheet(dadosExcel);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Top_Down_Consenso");
    XLSX.writeFile(workbook, `SOP_TopDown_${isSnapshot ? 'CONGELADO' : 'DRAFT'}_${new Date().toISOString().split('T')[0]}.xlsx`);
  };

  const handleGravar = async () => {
    const ajustes: any[] = [];
    Object.entries(celulasEditadas).forEach(([sku, meses]: any) => {
      Object.entries(meses).forEach(([mes, val]: any) => {
        if (val.novo_volume !== '') ajustes.push({ sku: sku, mes: mes, novo_volume: parseInt(val.novo_volume) });
      });
    });

    if (ajustes.length === 0) return alert("Nenhuma alteração detectada.");
    setIsProcessing(true);
    try {
      await axios.post('/api/v1/consensus/save', { ajustes });
      alert("✅ Edições gravadas e rateadas com sucesso!");
      setCelulasEditadas({});
      fetchData(); 
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao gravar."); } 
    finally { setIsProcessing(false); }
  };

  const columns = useMemo<ColumnDef<any>[]>(() => {
    const baseCols: ColumnDef<any>[] = [
      {
        id: 'nome', header: 'Hierarquia S&OP / Dossiê', accessorFn: (row: any) => row.nome || row.descricao,
        cell: ({ row }: any) => {
          const isCat = row.original.isCategory;
          return (
            <div style={{ paddingLeft: `${row.depth * 2}rem` }} className="flex items-center gap-3 py-2 min-w-[300px]">
              <button onClick={(e) => { e.stopPropagation(); row.toggleExpanded(); }} className={`p-1.5 rounded-lg border transition-all ${row.getIsExpanded() ? 'bg-slate-900 border-slate-900 text-white shadow-lg' : 'hover:bg-slate-100 border-transparent text-slate-400'}`}>
                {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center border flex-shrink-0 ${isCat ? 'bg-slate-900 border-slate-700 text-white shadow-md' : 'bg-indigo-50 border-indigo-100 text-indigo-600'}`}>
                {isCat ? <Layers className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              <div className="flex flex-col overflow-hidden">
                <span className={`text-[13px] truncate ${isCat ? 'font-black uppercase tracking-widest' : 'font-bold text-slate-700'}`}>{isCat ? row.original.nome : row.original.descricao}</span>
                {!isCat && <span className="text-[10px] text-slate-400 font-black tracking-widest">{row.original.sku}</span>}
              </div>
            </div>
          )
        }
      }
    ];

    mesesJanela.forEach(mesStr => {
        baseCols.push({
            id: `mes_${mesStr}`, header: mesStr.split('-').reverse().slice(1).join('/'),
            cell: ({ row }: any) => {
                const isCat = row.original.isCategory;
                const mData = row.original.meses.find((m:any) => m.mes === mesStr);
                const volProj = mData ? mData.vol_td : 0;
                const volAntigo = mData ? mData.vol_ciclo_anterior : 0;
                
                if (isCat) {
                    const recProj = mData ? mData.rec_td : 0;
                    const recAntiga = mData ? mData.rec_ciclo_anterior : 0;
                    return (
                        <div className="flex flex-col items-center justify-center min-w-[140px] gap-1">
                            <div className="w-full flex justify-between items-center bg-slate-50 px-2 py-1 rounded">
                                <span className="font-black text-slate-900 text-xs">{formatVolume(volProj)} cx</span>
                                <FvaBadge atual={volProj} anterior={volAntigo} label="Volume" />
                            </div>
                            <div className="w-full flex justify-between items-center bg-slate-50 px-2 py-1 rounded">
                                <span className="font-black text-emerald-600 text-[10px]">{formatMoeda(recProj)}</span>
                                <FvaBadge atual={recProj} anterior={recAntiga} label="Receita" />
                            </div>
                        </div>
                    );
                }

                const edicao = celulasEditadas[row.original.sku]?.[mesStr];
                const isEdited = edicao !== undefined && edicao.novo_volume !== '';
                const displayVal = isEdited ? edicao.novo_volume : volProj;
                const pmv = mData?.pmv_medio || 0;
                const recProjetada = (Number(displayVal) || 0) * pmv;

                return (
                    <div className="flex flex-col items-center justify-center min-w-[140px] gap-0.5">
                        <input
                            type="number" disabled={isCicloFechado} value={displayVal} onChange={(e) => onCellChange(row.original.sku, mesStr, e.target.value)}
                            className={`w-24 text-center font-black rounded-lg py-1.5 transition-all ${isCicloFechado ? 'bg-slate-50 text-slate-400 border-transparent cursor-not-allowed' : isEdited ? 'bg-indigo-50 border-2 border-indigo-400 text-indigo-700 shadow-inner outline-none' : 'bg-white border border-slate-200 text-slate-700 hover:border-indigo-300 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none'}`}
                        />
                        <div className="w-full flex justify-between items-center mt-1 px-3">
                            <div className="flex flex-col items-start leading-none">
                                {isEdited ? (
                                    <span className="text-[9px] font-bold text-indigo-500 uppercase animate-pulse">Pendente</span>
                                ) : (
                                    <span className="text-[10px] font-bold text-emerald-600">{formatMoeda(recProjetada)}</span>
                                )}
                            </div>
                            <FvaBadge atual={Number(displayVal)} anterior={volAntigo} label="Volume" />
                        </div>
                    </div>
                );
            }
        });
    });
    return baseCols;
  }, [mesesJanela, celulasEditadas, isCicloFechado]);

  const table = useReactTable({
    data: dadosFormatados, columns, state: { expanded },
    onExpandedChange: setExpanded, getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(), getExpandedRowModel: getExpandedRowModel(),
  });

  if (isLoading) return (
    <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
      <Loader2 className="w-12 h-12 animate-spin text-indigo-600" />
      <span className="text-slate-400 font-black text-[10px] tracking-[0.2em] uppercase mt-6 animate-pulse">Recalculando Rateios...</span>
    </div>
  );

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">
        
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 relative">
          {isCicloFechado && (
              <div className="absolute top-0 left-0 w-full bg-emerald-500 text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase rounded-t-[40px]"><ShieldCheck className="w-4 h-4"/> Metas da Diretoria Congeladas</div>
          )}
          <div className={isCicloFechado ? "pt-6" : ""}>
            <div className="flex items-center gap-4">
              <div className="p-3 bg-indigo-50 rounded-2xl"><Target className="w-8 h-8 text-indigo-600" /></div>
              <div>
                <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">Marketing & Category</h1>
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mt-1">Consenso Top-Down - Ciclo {cicloAtivo}</p>
              </div>
            </div>
          </div>
          <div className={`flex items-center gap-4 ${isCicloFechado ? "pt-6" : ""}`}>
             <button onClick={() => handleExportExcel(isCicloFechado)} className="flex items-center gap-2 bg-slate-50 hover:bg-slate-100 text-slate-700 px-6 py-4 rounded-2xl text-[10px] font-black tracking-widest uppercase border border-slate-200 transition-all shadow-sm"><Download className="w-4 h-4" /> Exportar</button>
             <button disabled={isCicloFechado || isProcessing} onClick={handleGravar} className={`flex items-center gap-3 text-white px-10 py-4 rounded-2xl text-xs font-black tracking-widest uppercase shadow-xl transition-all ${isCicloFechado ? 'bg-slate-300 shadow-none' : 'bg-indigo-600 hover:bg-indigo-700 shadow-indigo-200'}`}>
                {isProcessing ? <Loader2 className="w-5 h-5 animate-spin" /> : <Check className="w-5 h-5" />} Gravar e Ratear
             </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div className="relative md:col-span-2">
                <Search className="absolute left-6 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-300" />
                <input type="text" placeholder="Procurar por SKU ou Descrição..." value={busca} onChange={e => setBusca(e.target.value)} className="w-full h-full min-h-[60px] bg-white border border-slate-100 rounded-[24px] pl-16 pr-6 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-indigo-500/20 shadow-sm" />
            </div>
            <div className="relative">
                <Filter className="absolute left-6 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-300" />
                <select value={categoriaSelecionada} onChange={(e) => setCategoriaSelecionada(e.target.value)} className="w-full h-full min-h-[60px] bg-white border border-slate-100 rounded-[24px] pl-16 pr-10 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-indigo-500/20 shadow-sm appearance-none cursor-pointer">
                    <option value="TODAS">TODAS AS CATEGORIAS</option>
                    {categoriasUnicas.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <ChevronDown className="absolute right-6 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
            </div>
        </div>

        <div className="bg-white rounded-[48px] shadow-2xl border border-slate-100 overflow-hidden">
          <div className="overflow-x-auto custom-scrollbar relative">
            <table className="w-full text-left border-collapse">
              <thead className="bg-slate-50/50 border-b-2 border-slate-100 sticky top-0 z-10">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (<th key={h.id} className="px-10 py-8 text-[10px] font-black text-slate-400 uppercase tracking-widest">{flexRender(h.column.columnDef.header, h.getContext())}</th>))}
                  </tr>
                ))}
              </thead>
              <tbody className="mb-20">
                {table.getRowModel().rows.map(row => (
                  <Fragment key={row.id}>
                    <tr className={`border-b border-slate-50 transition-all ${row.depth === 0 ? 'bg-white hover:bg-slate-50' : 'bg-slate-50/30 hover:bg-slate-100/50'}`}>
                      {row.getVisibleCells().map(cell => (<td key={cell.id} className="px-10 py-4">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>))}
                    </tr>
                    {row.getIsExpanded() && (
                      <tr>
                        <td colSpan={table.getAllColumns().length} className="p-0">
                          {row.original.isCategory ? <CategoriaDossier data={row.original} /> : <SkuDossier data={row.original} celulasEditadas={celulasEditadas} />}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
              <tfoot className="bg-slate-900 text-white shadow-inner sticky bottom-0 z-20">
                 <tr>
                    <td className="px-10 py-5 font-black text-xs tracking-widest uppercase rounded-bl-[48px] border-t border-slate-700">
                        Totalizadores Dinâmicos
                    </td>
                    {mesesJanela.map(mesStr => (
                        <td key={mesStr} className="px-10 py-5 border-t border-slate-700 text-center">
                            <div className="flex flex-col items-center justify-center">
                                <span className="text-xl font-black">{formatVolume(totaisGerais[mesStr]?.vol)} CX</span>
                                <span className="text-xs text-emerald-400 font-bold tracking-widest">{formatMoeda(totaisGerais[mesStr]?.rec)}</span>
                            </div>
                        </td>
                    ))}
                 </tr>
              </tfoot>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}