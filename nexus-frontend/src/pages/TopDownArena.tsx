import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
    LineChart,
    Line,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend,
    ResponsiveContainer
} from 'recharts';
import { BarChart2, Save, Lock, CheckCircle } from 'lucide-react'; 

interface MesData {
    mes_banco: string;
    mes_str: string;
    vol_topdown: number;
    pmv: number;
    rec_topdown: number;
    rec_orcada: number;
    variacao: number;
}

interface SkuData {
    sku: string;
    descricao: string;
    meses: MesData[];
    grafico: {
        labels: string[];
        realizado: (number | null)[];
        ia: (number | null)[];
        lag1: (number | null)[];
        topdown: (number | null)[];
    };
}

const formatCurrency = (val: number) => {
    return new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(val || 0);
};

export default function TopDownArena() {
    const [dados, setDados] = useState<SkuData[]>([]);
    const [ciclo, setCiclo] = useState<string>('');
    const [statusEtapa, setStatusEtapa] = useState<string>('ABERTO');
    const [expandedRows, setExpandedRows] = useState<{ [key: string]: boolean }>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [pendingChanges, setPendingChanges] = useState<{ [key: string]: { sku: string, mes_banco: string, novo_vol: number } }>({});

    useEffect(() => {
        carregarDados();
    }, []);

    const carregarDados = async () => {
        try {
            const response = await axios.get('/api/v1/topdown/dados');
            setDados(response.data.dados || []);
            setCiclo(response.data.ciclo_ativo || '');
            setStatusEtapa(response.data.status_etapa || 'ABERTO');
        } catch (err) {
            console.error("Erro ao carregar dados:", err);
        } finally {
            setLoading(false);
        }
    };

    const toggleGraph = (sku: string) => {
        setExpandedRows(prev => ({ ...prev, [sku]: !prev[sku] }));
    };

    const handleInputChange = (sku: string, mes_banco: string, valorStr: string) => {
        const novoVol = parseFloat(valorStr);
        const volumeFinal = isNaN(novoVol) ? 0 : novoVol;

        const uniqueKey = `${sku}-${mes_banco}`;
        setPendingChanges(prev => ({
            ...prev,
            [uniqueKey]: { sku, mes_banco, novo_vol: volumeFinal }
        }));

        setDados(prevDados => prevDados.map(item => {
            if (item.sku === sku) {
                const mesesAtualizados = (item.meses || []).map(m => {
                    if (m.mes_banco === mes_banco) {
                        const novaRec = volumeFinal * m.pmv;
                        const novaVar = m.rec_orcada > 0 ? (novaRec - m.rec_orcada) / m.rec_orcada : 0;
                        return { ...m, vol_topdown: volumeFinal, rec_topdown: novaRec, variacao: novaVar };
                    }
                    return m;
                });

                const mesIndex = (item.meses || []).findIndex(m => m.mes_banco === mes_banco);
                let novosPontosTopDown = [...(item.grafico?.topdown || [])];
                
                if (mesIndex !== -1 && item.grafico?.labels) {
                    const labelAlvo = item.meses[mesIndex].mes_str;
                    const labelGraphIndex = item.grafico.labels.findIndex(l => l?.toLowerCase() === labelAlvo?.toLowerCase());
                    
                    if (labelGraphIndex !== -1) {
                        while (novosPontosTopDown.length <= labelGraphIndex) {
                            novosPontosTopDown.push(null);
                        }
                        novosPontosTopDown[labelGraphIndex] = volumeFinal;
                    }
                }

                return {
                    ...item,
                    meses: mesesAtualizados,
                    grafico: {
                        ...item.grafico,
                        topdown: novosPontosTopDown
                    }
                };
            }
            return item;
        }));
    };

    const handleSalvarRascunho = async () => {
        const listaAlteracoes = Object.values(pendingChanges);
        if (listaAlteracoes.length === 0) return alert("Nenhuma alteração pendente para salvar.");

        setSaving(true);
        try {
            await axios.post('/api/v1/topdown/salvar', { alteracoes: listaAlteracoes });
            setPendingChanges({}); 
            alert("Rascunho salvo e distribuído proporcionalmente entre os CNPJs!");
            carregarDados();
        } catch (err) {
            console.error(err);
            alert("Erro ao salvar rascunho.");
        } finally {
            setSaving(false);
        }
    };

    const handleCongelarEtapa = async () => {
        if (!window.confirm("Atenção: O congelamento irá propagar os volumes Top-Down para toda a malha operacional. Confirmar fecho da etapa?")) return;
        
        setSaving(true);
        try {
            await axios.post('/api/v1/topdown/congelar');
            setStatusEtapa('CONGELADO');
            alert("Etapa Top-Down trancada com sucesso!");
            carregarDados();
        } catch (err) {
            console.error(err);
            alert("Erro ao congelar etapa.");
        } finally {
            setSaving(false);
        }
    };

    const getRechartsData = (grafico: any) => {
        if (!grafico || !grafico.labels) return [];
        return grafico.labels.map((label: string, idx: number) => ({
            name: label,
            realizado: grafico.realizado?.[idx] ?? null,
            ia: grafico.ia?.[idx] ?? null,
            lag1: grafico.lag1?.[idx] ?? null,
            topdown: grafico.topdown?.[idx] ?? null,
        }));
    };

    if (loading) return <div className="text-white p-8">A Carregar Arena Top-Down...</div>;

    const hasPendingChanges = Object.keys(pendingChanges).length > 0;

    return (
        <div className="p-6 bg-slate-900 min-h-screen">
            
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 bg-slate-800 p-4 rounded-lg border border-slate-700 gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-white tracking-tight">Arena Top-Down</h1>
                    <p className="text-slate-400 text-xs mt-0.5">Ciclo Ativo S&OP: {ciclo} | Visão Direta por SKU</p>
                </div>
                
                <div className="flex items-center gap-3">
                    {statusEtapa === 'CONGELADO' ? (
                        <div className="flex items-center bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-bold px-3 py-2 rounded-lg">
                            <CheckCircle className="mr-2 w-4 h-4" /> ETAPA CONGELADA NA MALHA
                        </div>
                    ) : (
                        <>
                            <button
                                onClick={handleSalvarRascunho}
                                disabled={saving || !hasPendingChanges}
                                className={`flex items-center text-sm font-semibold px-4 py-2 rounded-lg transition-colors border ${
                                    hasPendingChanges 
                                    ? "bg-slate-700 hover:bg-slate-600 border-slate-500 text-cyan-400" 
                                    : "bg-slate-800 border-slate-700 text-slate-500 opacity-50 cursor-not-allowed"
                                }`}
                            >
                                <Save className="mr-2 w-4 h-4" /> {saving ? "A Guardar..." : "Salvar Rascunho"}
                            </button>
                            <button
                                onClick={handleCongelarEtapa}
                                disabled={saving}
                                className="flex items-center bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-lg shadow-cyan-600/20 disabled:opacity-50"
                            >
                                <Lock className="mr-2 w-4 h-4" /> Congelar Arena
                            </button>
                        </>
                    )}
                </div>
            </div>

            <div className="space-y-4">
                {dados.map(item => (
                    <div key={item.sku} className="bg-slate-800 rounded-lg shadow border border-slate-700 overflow-hidden">
                        
                        <div className="flex flex-col xl:flex-row">
                            
                            <div className="w-full xl:w-80 p-4 border-b xl:border-b-0 xl:border-r border-slate-700 flex flex-col justify-center shrink-0">
                                <h3 className="text-sm font-bold text-white tracking-tight leading-tight">{item.descricao}</h3>
                                <span className="text-xs text-slate-400 mt-1 font-mono">SKU: {item.sku}</span>
                                <button 
                                    onClick={() => toggleGraph(item.sku)} 
                                    className="mt-4 text-xs flex items-center w-max px-2 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-cyan-400 transition-colors border border-slate-600"
                                >
                                    <BarChart2 className="mr-2 w-4 h-4"/> {expandedRows[item.sku] ? "Ocultar Curva" : "Visualizar Gráfico"}
                                </button>
                            </div>
                            
                            <div className="flex flex-1 overflow-x-auto">
                                {(item.meses || []).map(mes => (
                                    <div key={mes.mes_banco} className="w-64 p-4 border-r border-slate-700 shrink-0 bg-slate-800/40">
                                        
                                        <div className="text-xs font-bold text-slate-400 tracking-wider uppercase mb-3 border-b border-slate-700/60 pb-1.5">
                                            {mes.mes_str}
                                        </div>
                                        
                                        <div className="flex items-center justify-between mb-4">
                                            <span className="text-xs font-semibold text-cyan-400">Volume (Cx)</span>
                                            <input 
                                                type="number"
                                                disabled={statusEtapa === 'CONGELADO'}
                                                className="bg-slate-900 border border-slate-700 focus:border-cyan-500 rounded px-2.5 py-1 text-sm text-white w-28 text-right focus:outline-none focus:ring-1 focus:ring-cyan-500 font-mono disabled:opacity-50 disabled:cursor-not-allowed"
                                                value={mes.vol_topdown.toString()} 
                                                onFocus={(e) => e.target.select()} 
                                                onChange={(e) => handleInputChange(item.sku, mes.mes_banco, e.target.value)}
                                            />
                                        </div>
                                        
                                        <div className="space-y-2 pt-1">
                                            <div className="flex justify-between text-xs">
                                                <span className="text-slate-400">Faturamento</span>
                                                <span className="text-slate-100 font-medium font-mono">R$ {formatCurrency(mes.rec_topdown)}</span>
                                            </div>
                                            <div className="flex justify-between text-xs">
                                                <span className="text-slate-500">Orçado (Sku)</span>
                                                <span className="text-slate-400 font-mono">R$ {formatCurrency(mes.rec_orcada)}</span>
                                            </div>
                                            <div className="flex justify-between text-xs pt-2 border-t border-slate-700/60 mt-1">
                                                <span className="text-slate-400 font-medium">Variação</span>
                                                <span className={`font-mono font-bold ${mes.variacao >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                                    {mes.variacao > 0 ? '+' : ''}{(mes.variacao * 100).toFixed(1)}%
                                                </span>
                                            </div>
                                        </div>

                                    </div>
                                ))}
                            </div>

                        </div>

                        {expandedRows[item.sku] && (
                            <div className="p-6 bg-slate-950 border-t border-slate-700">
                                <div className="h-64 w-full">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <LineChart data={getRechartsData(item.grafico)} margin={{ top: 5, right: 30, bottom: 5, left: 0 }}>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                                            <XAxis dataKey="name" stroke="#64748b" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={false} />
                                            <YAxis stroke="#64748b" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={false} tickFormatter={(val) => new Intl.NumberFormat('pt-BR', { notation: "compact" }).format(val)} />
                                            <Tooltip 
                                                contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px' }}
                                                itemStyle={{ fontSize: '13px', fontWeight: '500' }}
                                                labelStyle={{ color: '#94a3b8', marginBottom: '8px', fontSize: '12px', fontWeight: 'bold' }}
                                            />
                                            <Legend wrapperStyle={{ paddingTop: '20px', fontSize: '12px' }} iconType="circle" />
                                            <Line type="monotone" dataKey="realizado" name="Realizado (Cx)" stroke="#94a3b8" strokeWidth={2} dot={{ r: 4, fill: '#94a3b8', strokeWidth: 0 }} connectNulls />
                                            <Line type="monotone" dataKey="ia" name="Projeção IA" stroke="#a855f7" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls />
                                            <Line type="monotone" dataKey="lag1" name="Lag 1 (ant.)" stroke="#f59e0b" strokeWidth={2} strokeDasharray="3 3" dot={false} connectNulls />
                                            <Line type="monotone" dataKey="topdown" name="Top-Down Simulado" stroke="#22d3ee" strokeWidth={3} dot={{ r: 5, fill: '#22d3ee', strokeWidth: 0 }} activeDot={{ r: 7 }} connectNulls />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </div>
                            </div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}