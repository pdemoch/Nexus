import React, { useState, useMemo, useEffect, useCallback, Fragment } from 'react';
import axios from 'axios';
import { 
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel, ColumnDef 
} from '@tanstack/react-table';
import { 
  ChevronRight, ChevronDown, Package, Store, Users, 
  Target, Save, Shield, ShieldAlert, ShieldCheck, Activity, Wand2, TrendingUp, TrendingDown
} from 'lucide-react';

const formatVolume = (val: any) => {
  const num = Number(val);
  if (isNaN(num)) return '0';
  return new Intl.NumberFormat('pt-BR').format(Math.round(num));
};

const formatMoeda = (val: any) => {
  const num = Number(val);
  if (isNaN(num)) return 'R$ 0,00';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(num));
};

const calcVar = (atual: number, anterior: number) => anterior > 0 ? ((atual - anterior) / anterior) * 100 : 0;

const VarBadge = ({ atual = 0, anterior = 0, dark = false }: { atual?: number, anterior?: number, dark?: boolean }) => {
  const v = calcVar(atual, anterior);
  if (v === 0 || anterior === 0) return null;
  const isPos = v >= 0;
  
  if (dark) {
    return (
      <span className={`text-[9px] font-black flex items-center gap-0.5 px-1 py-0.5 rounded ${isPos ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
        {isPos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />} {Math.abs(v).toFixed(1)}%
      </span>
    );
  }
  
  return (
    <span className={`text-[9px] font-black flex items-center gap-0.5 px-1.5 py-0.5 rounded ${isPos ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
      {isPos ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />} {Math.abs(v).toFixed(1)}%
    </span>
  );
};

const SmartCurrencyInput = ({ value, onChange, disabled }: { value: number, onChange: (val: number) => void, disabled: boolean }) => {
  const [localVal, setLocalVal] = useState(value !== undefined ? formatMoeda(value).replace('R$', '').trim() : '0');
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    if (!isFocused) setLocalVal(value !== undefined ? formatMoeda(value).replace('R$', '').trim() : '0');
  }, [value, isFocused]);

  const handleFocus = () => { setIsFocused(true); setLocalVal(localVal.replace(/\./g, '')); };
  const handleBlur = () => { 
      setIsFocused(false); 
      const num = parseInt(localVal.replace(/\D/g, ''), 10) || 0;
      setLocalVal(formatMoeda(num).replace('R$', '').trim());
      onChange(num);
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); };

  return (
    <div className="relative w-full flex items-center">
      <span className="absolute left-3 text-slate-400 font-black text-xs">R$</span>
      <input
        type="text" value={localVal} disabled={disabled} onFocus={handleFocus} onBlur={handleBlur} onKeyDown={handleKeyDown} onChange={(e) => setLocalVal(e.target.value)}
        className={`w-full pl-8 pr-2 py-2.5 rounded-xl border-2 text-center outline-none text-sm font-black transition-all
          ${disabled ? 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed' : 'bg-white border-blue-200 text-blue-700 focus:border-blue-500 focus:bg-blue-50 shadow-sm'}`}
      />
    </div>
  );
};

export default function ConsensoArena({ usuarioSessao }: any) {
  const [dadosBase, setDadosBase] = useState<any[]>([]);
  const [isFechado, setIsFechado] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [celulasEditadas, setCelulasEditadas] = useState<any>({});
  const [expanded, setExpanded] = useState({});

  const colunasData = dadosBase.length > 0 ? dadosBase[0].meses : [];

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/micro');
      setDadosBase(res.data.dados || []);
      setIsFechado(res.data.is_fechado);
      setCelulasEditadas({});
    } catch (e) { console.error(e); } 
    finally { setIsLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Extração Dinâmica (Puxa os Volumes Editados em Caixas)
  const getDynamicVol = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const ed = celulasEditadas[row.chave_matriz]?.[mesBanco];
      if (ed !== undefined) return ed.novo_volume; // Caixas Inteiras
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.vol_sim || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicVol(child, mesBanco), 0);
  }, [celulasEditadas]);

  // Extração de Receita (Caixas Inteiras x PMV Exato = Faturamento Real)
  const getDynamicRec = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const vol = getDynamicVol(row, mesBanco);
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return vol * (m?.pmv || 0);
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getDynamicRec(child, mesBanco), 0);
  }, [getDynamicVol]);

  const getStaticMeta = useCallback((row: any, mesBanco: string): number => {
    if (row.tipo === 'produto') {
      const m = row.meses?.find((x: any) => x.mes_banco === mesBanco);
      return m?.rec_meta || 0;
    }
    return (row.subRows || []).reduce((acc: number, child: any) => acc + getStaticMeta(child, mesBanco), 0);
  }, []);

  // O MOTOR DE RATEIO FINANCEIRO (Digita R$, Rateia por Histórico de Faturamento R$, Converte em Caixas)
  const handleEditCell = (chaveStr: string, mesBanco: string, novoValorRS: number) => {
    if (isFechado) return;

    setCelulasEditadas((currentEdits: any) => {
      const nextEdits = { ...currentEdits };

      const findNode = (nodes: any[]): any => {
        for (const n of nodes) {
          if (n.chave_matriz === chaveStr) return n;
          if (n.subRows) {
            const found = findNode(n.subRows);
            if (found) return found;
          }
        }
        return null;
      };

      const targetNode = findNode(dadosBase);
      if (!targetNode) return nextEdits;

      const leaves: any[] = [];
      const getLeaves = (n: any) => {
        if (n.tipo === 'produto') leaves.push(n);
        else if (n.subRows) n.subRows.forEach(getLeaves);
      };
      getLeaves(targetNode);

      // Soma o Faturamento Histórico como Peso
      const totalPesoFat = leaves.reduce((sum, leaf) => sum + (leaf.peso_fat || 1), 0);

      leaves.forEach(leaf => {
          const weight = leaf.peso_fat || 1;
          const target_RS = totalPesoFat > 0 ? (weight / totalPesoFat) * novoValorRS : (1 / leaves.length) * novoValorRS;
          
          const pmv = leaf.meses.find((m: any) => m.mes_banco === mesBanco)?.pmv || 1;
          
          // Converte o dinheiro alocado para caixas físicas inteiras
          const caixasFisicas = Math.round(target_RS / pmv);

          if (!nextEdits[leaf.chave_matriz]) nextEdits[leaf.chave_matriz] = {};
          nextEdits[leaf.chave_matriz][mesBanco] = { novo_volume: Math.max(0, caixasFisicas) };
      });

      return nextEdits;
    });
  };

  // VALIDADOR DA REGRA DE OURO (99% a 101%)
  const isSaveBlocked = useMemo(() => {
    if (dadosBase.length === 0) return true;
    
    // Varre todos os meses visíveis. Se QUALQUER mês estiver fora do limite de 99-101%, bloqueia.
    for (const m of colunasData) {
      let totalMetaRS = 0;
      let totalSimRS = 0;
      dadosBase.forEach(rootNode => {
        totalMetaRS += getStaticMeta(rootNode, m.mes_banco);
        totalSimRS += getDynamicRec(rootNode, m.mes_banco);
      });
      
      if (totalMetaRS > 0) {
        const percent = (totalSimRS / totalMetaRS) * 100;
        if (percent < 99 || percent > 101) return true; // Falhou a regra de tolerância!
      }
    }
    return false;
  }, [dadosBase, colunasData, celulasEditadas, getStaticMeta, getDynamicRec]);

  const columns = useMemo<ColumnDef<any>[]>(() => {
    const cols: ColumnDef<any>[] = [
      {
        accessorKey: 'nome',
        header: 'Equipa e Carteira',
        cell: ({ row, getValue }) => {
          const depth = row.depth;
          const isExpanded = row.getIsExpanded();
          const hasChildren = row.getCanExpand();
          const { tipo } = row.original;

          return (
            <div style={{ paddingLeft: `${depth * 1.5}rem` }} className="flex items-center gap-3 py-3">
              {hasChildren ? (
                <button onClick={() => row.toggleExpanded()} className="p-1 hover:bg-slate-200 text-slate-500 rounded transition-colors">
                  {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
              ) : <div className="w-7" />}

              <div className={`w-8 h-8 rounded-full flex items-center justify-center border shrink-0 ${tipo === 'gerente' || tipo === 'coordenador' ? 'bg-slate-900 text-white border-slate-800' : tipo === 'vendedor' ? 'bg-blue-600 text-white border-blue-700' : tipo === 'cliente' ? 'bg-indigo-50 border-indigo-200 text-indigo-600' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                {tipo === 'vendedor' || tipo === 'coordenador' || tipo === 'gerente' ? <Users className="w-4 h-4" /> : tipo === 'cliente' ? <Store className="w-4 h-4" /> : <Package className="w-4 h-4" />}
              </div>
              
              <div className="flex flex-col overflow-hidden">
                <span className={`text-sm pr-4 truncate max-w-[300px] ${tipo !== 'produto' ? 'font-black text-slate-800 uppercase' : 'font-semibold text-slate-600'}`}>
                  {getValue() as string}
                </span>
                {tipo === 'produto' && (
                  <span className="text-[10px] text-emerald-600 font-black mt-1 tracking-widest uppercase bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100 self-start">
                    PMV: {formatMoeda(row.original.meses[0]?.pmv || 0)}
                  </span>
                )}
              </div>
            </div>
          );
        },
      }
    ];

    colunasData.forEach((mes: any) => {
      cols.push({
        id: mes.mes_banco,
        header: mes.mes_str,
        cell: ({ row }) => {
          const mBanco = mes.mes_banco;
          const { tipo } = row.original;
          
          const metaRS = getStaticMeta(row.original, mBanco);
          const simRS = getDynamicRec(row.original, mBanco);
          const simCX = getDynamicVol(row.original, mBanco);
          
          const percent = metaRS > 0 ? (simRS / metaRS) * 100 : 0;
          const isValid = percent >= 99 && percent <= 101;

          return (
            <div className="flex flex-col items-center justify-center p-2 min-w-[150px]">
              
              {/* O TETO (META DO NÓ) */}
              <div className="flex items-center gap-1 text-[10px] font-black text-slate-400 mb-1.5 uppercase tracking-widest">
                 <Target className="w-3 h-3 text-slate-300" /> Meta: {formatMoeda(metaRS)}
              </div>
              
              {/* O INPUT DE DINHEIRO */}
              <SmartCurrencyInput value={simRS} onChange={(val) => handleEditCell(row.original.chave_matriz, mBanco, val)} disabled={isFechado} />

              {/* AS CAIXAS REAIS E O SEMÁFORO */}
              <div className="flex flex-col items-center mt-2 w-full">
                 <span className="text-[11px] font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded tracking-widest">
                    {formatVolume(simCX)} CX
                 </span>
                 
                 {/* O Semáforo de Tolerância */}
                 <div className={`mt-1.5 w-full h-1 rounded-full ${isValid ? 'bg-emerald-400' : 'bg-rose-500'}`} title={isValid ? 'Dentro da Margem de Tolerância' : 'Fora da Tolerância de 99%-101%'} />
              </div>
            </div>
          );
        },
      });
    });

    return cols;
  }, [colunasData, celulasEditadas, isFechado, getDynamicRec, getStaticMeta, getDynamicVol]);

  const table = useReactTable({
    data: dadosBase,
    columns,
    state: { expanded },
    onExpandedChange: setExpanded,
    getSubRows: row => row.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  const handleSalvar = async () => {
    if (isSaveBlocked) return alert("Erro de Tolerância! Ajuste a distribuição financeira da equipa. O simulado deve ficar entre 99% e 101% do Teto/Meta herdado.");
    if (Object.keys(celulasEditadas).length === 0) return alert("Nenhuma alteração para salvar.");
    
    // Transforma os ajustes (que estão em CAIXAS, por causa do SKU) no payload
    const leafEdits = Object.entries(celulasEditadas).filter(([chave]) => chave.split('|').length === 2); // {cli}|{sku}
    const ajustes = leafEdits.flatMap(([chave, meses]: any) => Object.entries(meses).map(([mes_projetado, val]: any) => ({ chave, mes_projetado, novo_volume: val.novo_volume })));
    
    try {
      await axios.post(`/api/v1/consensus/micro/salvar`, { origem_ajuste: 'Carteira', ajustes });
      alert("Valores distribuídos e salvos com sucesso!");
      fetchData();
    } catch (e: any) { alert("Erro ao salvar."); }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-8 pb-32 font-sans">
      <div className="max-w-[1500px] mx-auto mb-8 flex flex-col gap-6">
        
        <div className="flex items-center justify-between mb-4 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100">
            <div>
              <h1 className="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
                  <Activity className="w-8 h-8 text-blue-600" /> Gestão de <span className="text-blue-600">Carteiras e Equipa</span>
              </h1>
              <p className="text-slate-500 mt-1 font-bold text-sm tracking-widest uppercase">Distribuição Financeira Tática M-2</p>
            </div>

            <div className="flex items-center gap-4">
              <button onClick={handleSalvar} disabled={isFechado || isSaveBlocked} className={`px-6 py-3 font-black tracking-widest uppercase rounded-xl shadow-lg transition-all flex items-center gap-2 text-white ${isFechado || isSaveBlocked ? 'bg-slate-300 shadow-none cursor-not-allowed text-slate-500' : 'bg-blue-600 hover:bg-blue-500 shadow-blue-900/20'}`}>
                  {isSaveBlocked ? <ShieldAlert className="w-5 h-5" /> : <Save className="w-5 h-5" />}
                  {isSaveBlocked ? 'Ajuste a Tolerância' : 'Salvar Distribuição'}
              </button>
            </div>
        </div>

        {isSaveBlocked && dadosBase.length > 0 && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-5 rounded-2xl flex items-center gap-4 shadow-sm animate-in fade-in slide-in-from-top-4">
                <ShieldAlert className="w-6 h-6 text-rose-500" />
                <div>
                  <h3 className="font-black uppercase tracking-widest text-xs">Aviso de Regra de Ouro (99% - 101%)</h3>
                  <p className="text-sm font-medium mt-1">O Faturamento simulado pelas caixas inteiras não corresponde à sua Meta imposta. Ajuste a distribuição até que os indicadores fiquem verdes para libertar a gravação.</p>
                </div>
            </div>
        )}

        {isFechado && !isLoading && (
            <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 p-5 rounded-2xl flex items-center gap-4 shadow-sm">
                <ShieldCheck className="w-6 h-6 text-emerald-500" />
                <div><h3 className="font-bold uppercase text-xs">Etapa Concluída</h3><p className="text-sm">A distribuição das metas da sua equipa foi trancada e oficializada no ciclo atual.</p></div>
            </div>
        )}

        <div className="bg-white rounded-3xl shadow-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse whitespace-nowrap">
              <thead>
                {table.getHeaderGroups().map(headerGroup => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header, idx) => (
                      <th key={header.id} className={`bg-slate-900 py-4 font-black uppercase tracking-widest text-[11px] text-white border-b border-slate-800 ${idx === 0 ? 'px-6 border-r border-slate-800/50' : 'text-center border-l border-slate-800/50'}`}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              
              <tbody>
                {isLoading ? (
                  <tr><td colSpan={columns.length} className="p-12 text-center text-slate-400 font-bold">Modelando Árvore Financeira da Equipa...</td></tr>
                ) : (
                  table.getRowModel().rows.map(row => (
                    <React.Fragment key={row.id}>
                      <tr className={`border-b border-slate-100 transition-colors ${row.getIsExpanded() ? 'bg-blue-50/20' : row.depth === 0 ? 'bg-slate-50' : 'bg-white hover:bg-slate-50'}`}>
                        {row.getVisibleCells().map((cell, idx) => (
                          <td key={cell.id} className={`align-middle ${idx === 0 ? 'border-r border-slate-100' : 'border-l border-slate-100'}`}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                    </React.Fragment>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}