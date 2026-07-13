import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  useReactTable, getCoreRowModel, flexRender, getExpandedRowModel,
  getSortedRowModel, SortingState, ColumnDef
} from '@tanstack/react-table';
import {
  Loader2, ChevronDown, ChevronRight, Lock, Search, Factory, Package,
  TrendingUp, TrendingDown, Layers, AlertTriangle, Save, ShieldCheck,
  MessageSquare, Unlock
} from 'lucide-react';
import axios from 'axios';

const fmtVol = (v: any) => (Math.round(Number(v) || 0)).toLocaleString('pt-BR');
const fmtMoeda = (v: any) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(Number(v) || 0));
// PMV e preco unitario: precisa de centavos.
const fmtMoedaPreciso = (v: any) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v) || 0);

const MOTIVOS = [
  { valor: 'capacidade_producao', label: 'Capacidade de Produção' },
  { valor: 'ruptura_mp', label: 'Ruptura de Matéria-Prima' },
  { valor: 'restricao_linha', label: 'Restrição de Linha' },
  { valor: 'estrategia_estoque', label: 'Estratégia de Estoque' },
  { valor: 'sazonalidade', label: 'Sazonalidade' },
  { valor: 'outros', label: 'Outros' },
];

interface MesNode {
  mes_banco: string; mes_str: string;
  vol_meta: number; vol_supply: number;
  fat_meta: number; fat_supply: number;
  gap: number; motivo: string | null; justificativa: string | null;
}
interface Node {
  tipo: 'categoria' | 'produto';
  nome: string; produto?: string; chave: string;
  meses: MesNode[]; subRows?: Node[];
}

// Input de volume simples (edita caixas do SKU).
const VolInput = ({ valor, disabled, onCommit }: { valor: number; disabled: boolean; onCommit: (v: number) => void }) => {
  const [local, setLocal] = useState(fmtVol(valor));
  const [foco, setFoco] = useState(false);
  useEffect(() => { if (!foco) setLocal(fmtVol(valor)); }, [valor, foco]);
  return (
    <input
      type="text" value={local} disabled={disabled}
      onFocus={() => { setFoco(true); setLocal(local.replace(/\./g, '')); }}
      onBlur={() => { setFoco(false); const n = parseInt(local.replace(/\D/g, ''), 10) || 0; setLocal(fmtVol(n)); if (n !== Math.round(valor)) onCommit(n); }}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
      onChange={(e) => setLocal(e.target.value)}
      className={`w-28 text-center rounded-lg py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${disabled ? 'text-slate-900 font-black bg-transparent cursor-not-allowed' : 'text-indigo-700 font-black bg-indigo-50 shadow-inner'}`}
    />
  );
};

export default function SupplyReviewArena(_props: { usuarioSessao?: any } = {}) {
  const [dados, setDados] = useState<Node[]>([]);
  const [meses, setMeses] = useState<{ mes_banco: string; mes_str: string }[]>([]);
  const [ciclo, setCiclo] = useState('');
  const [metasCongelado, setMetasCongelado] = useState(false);
  const [supplyCongelado, setSupplyCongelado] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busca, setBusca] = useState('');
  const [expanded, setExpanded] = useState({});
  const [sorting, setSorting] = useState<SortingState>([]);

  // Edições: { [sku]: { [mes]: { novo_volume, motivo, justificativa } } }
  const [edicoes, setEdicoes] = useState<{ [sku: string]: { [mes: string]: { novo_volume: number; motivo?: string; justificativa?: string } } }>({});
  // Justificativa aberta (qual SKU×mes está com o painel de motivo aberto)
  const [justAberta, setJustAberta] = useState<string | null>(null);

  const isLocked = supplyCongelado;

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get('/api/v1/consensus/supply');
      setDados(res.data.dados || []);
      setMeses(res.data.meses || []);
      setCiclo(res.data.ciclo_ativo || '');
      setMetasCongelado(res.data.metas_congelado || false);
      setSupplyCongelado(res.data.supply_congelado || false);
      setEdicoes({});
      setJustAberta(null);
    } catch (e) {
      console.error('Erro ao carregar Supply:', e);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  const temPend = Object.keys(edicoes).length > 0;

  // volume vivo de um SKU num mês (edição ou banco)
  const volVivoSku = useCallback((sku: string, mes: string, doBanco: number) => {
    const e = edicoes[sku]?.[mes];
    return e?.novo_volume !== undefined ? e.novo_volume : doBanco;
  }, [edicoes]);

  // volume supply vivo de um nó (SKU direto, ou soma dos filhos p/ categoria)
  const volNode = useCallback((node: Node, mes: string): number => {
    if (node.tipo === 'produto') {
      const m = node.meses.find(x => x.mes_banco === mes);
      return volVivoSku(node.produto!, mes, m?.vol_supply || 0);
    }
    return (node.subRows || []).reduce((acc, f) => {
      const m = f.meses.find(x => x.mes_banco === mes);
      return acc + volVivoSku(f.produto!, mes, m?.vol_supply || 0);
    }, 0);
  }, [volVivoSku]);

  const metaNode = useCallback((node: Node, mes: string): number => {
    if (node.tipo === 'produto') return node.meses.find(x => x.mes_banco === mes)?.vol_meta || 0;
    return (node.subRows || []).reduce((acc, f) => acc + (f.meses.find(x => x.mes_banco === mes)?.vol_meta || 0), 0);
  }, []);

  const pmvMedioSku = useCallback((node: Node, mes: string): number => {
    const m = node.meses.find(x => x.mes_banco === mes);
    if (!m || m.vol_supply <= 0) return m && m.vol_meta > 0 ? m.fat_meta / m.vol_meta : 0;
    return m.fat_supply / m.vol_supply;
  }, []);

  // Editar o volume de um SKU num mês (Supply define entrega da fábrica).
  const editarSku = (sku: string, mes: string, novoVol: number) => {
    setEdicoes(prev => {
      const nx = { ...prev };
      if (!nx[sku]) nx[sku] = {};
      nx[sku][mes] = { ...(nx[sku][mes] || {}), novo_volume: novoVol };
      return nx;
    });
  };
  const editarJustificativa = (sku: string, mes: string, campo: 'motivo' | 'justificativa', valor: string, volAtual: number) => {
    setEdicoes(prev => {
      const nx = { ...prev };
      if (!nx[sku]) nx[sku] = {};
      const atual = nx[sku][mes] || { novo_volume: volAtual };
      nx[sku][mes] = { ...atual, [campo]: valor };
      return nx;
    });
  };

  const arvore = useMemo(() => {
    const t = busca.toLowerCase();
    if (!t) return dados;
    return dados.map(cat => {
      const filhos = (cat.subRows || []).filter(s => (s.nome + ' ' + (s.produto || '')).toLowerCase().includes(t));
      if (cat.nome.toLowerCase().includes(t)) return cat;
      if (filhos.length) return { ...cat, subRows: filhos };
      return null;
    }).filter(Boolean) as Node[];
  }, [dados, busca]);

  // Totais de comando
  const kpis = useMemo(() => {
    let vMeta = 0, vSup = 0, fMeta = 0, fSup = 0;
    meses.forEach(mc => {
      dados.forEach(cat => (cat.subRows || []).forEach(sku => {
        const m = sku.meses.find(x => x.mes_banco === mc.mes_banco);
        vMeta += m?.vol_meta || 0;
        const vs = volVivoSku(sku.produto!, mc.mes_banco, m?.vol_supply || 0);
        vSup += vs;
        fMeta += m?.fat_meta || 0;
        fSup += vs * pmvMedioSku(sku, mc.mes_banco);
      }));
    });
    return { vMeta, vSup, fMeta, fSup, gap: vSup - vMeta, gapPct: vMeta > 0 ? ((vSup - vMeta) / vMeta) * 100 : 0 };
  }, [dados, meses, volVivoSku, pmvMedioSku]);

  const montarAjustes = () => {
    const out: any[] = [];
    Object.entries(edicoes).forEach(([sku, porMes]) => {
      Object.entries(porMes).forEach(([mes, e]) => {
        out.push({ produto: sku, mes_projetado: mes, novo_volume: e.novo_volume, motivo: e.motivo || null, justificativa: e.justificativa || null });
      });
    });
    return out;
  };

  const salvar = async () => {
    if (!temPend) return alert('Nenhuma alteração pendente.');
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/supply/salvar', { ajustes: montarAjustes() });
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao salvar.');
    } finally { setSaving(false); }
  };

  const congelar = async () => {
    const msg = `CONGELAR A ETAPA DE SUPPLY\n\n` +
      `Entrega da fábrica: ${fmtVol(kpis.vSup)} cx (${fmtMoeda(kpis.fSup)})\n` +
      `Gap vs Consenso: ${kpis.gapPct >= 0 ? '+' : ''}${kpis.gapPct.toFixed(1)}%\n\n` +
      `Isto passa o bastão ao Plano Final. Confirmar?`;
    if (!window.confirm(msg)) return;
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/supply/congelar', { ajustes: montarAjustes() });
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao congelar.');
    } finally { setSaving(false); }
  };

  const reabrir = async () => {
    if (!window.confirm('Reabrir a etapa de Supply para edição?')) return;
    setSaving(true);
    try {
      await axios.post('/api/v1/consensus/supply/reabrir');
      await carregar();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Erro ao reabrir.');
    } finally { setSaving(false); }
  };

  const columns = useMemo<ColumnDef<any>[]>(() => {
    if (!dados.length) return [];
    const cols: ColumnDef<any>[] = [{
      id: 'nome', header: 'Categoria > SKU',
      accessorFn: (r: any) => r.nome,
      cell: (info: any) => {
        const row = info.row;
        const { tipo, nome, produto } = row.original;
        return (
          <div style={{ paddingLeft: `${row.depth * 24}px` }} className="flex items-center gap-2.5 py-2 min-w-[280px]">
            {row.getCanExpand() ? (
              <button onClick={row.getToggleExpandedHandler()} className="p-1 hover:bg-slate-200 rounded-lg text-slate-500">
                {row.getIsExpanded() ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
            ) : <div className="w-6" />}
            <div className={`w-7 h-7 flex items-center justify-center rounded-lg border ${tipo === 'categoria' ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
              {tipo === 'categoria' ? <Layers className="w-3.5 h-3.5 text-white" /> : <Package className="w-3.5 h-3.5 text-slate-400" />}
            </div>
            <div className="flex flex-col overflow-hidden">
              <span className={`text-[12px] truncate ${tipo === 'produto' ? 'font-bold text-slate-600' : 'font-black text-slate-800'}`} title={nome}>{nome}</span>
              {produto && <span className="text-[9px] text-slate-400 font-black tracking-widest">{produto}</span>}
            </div>
          </div>
        );
      }
    }];

    meses.forEach(mc => {
      cols.push({
        id: `mes_${mc.mes_banco}`, header: mc.mes_str,
        accessorFn: (r: any) => volNode(r, mc.mes_banco),
        cell: (info: any) => {
          const node: Node = info.row.original;
          const supply = volNode(node, mc.mes_banco);
          const meta = metaNode(node, mc.mes_banco);
          const gap = supply - meta;
          const gapPct = meta > 0 ? (gap / meta) * 100 : 0;
          const ehSku = node.tipo === 'produto';
          const chaveJust = `${node.produto}|${mc.mes_banco}`;
          const edKey = ehSku ? edicoes[node.produto!]?.[mc.mes_banco] : undefined;
          const mesNode = ehSku ? node.meses.find(x => x.mes_banco === mc.mes_banco) : undefined;
          const motivoAtual = edKey?.motivo ?? mesNode?.motivo ?? '';
          const justAtual = edKey?.justificativa ?? mesNode?.justificativa ?? '';
          const temDivergencia = Math.abs(gap) > 0;

          // Faturamento da entrega + auditoria do calculo (volume x PMV).
          const pmvMes = pmvMedioSku(node, mc.mes_banco);
          const fatSupply = supply * pmvMes;
          const tipCalculo = `${fmtVol(supply)} cx x ${fmtMoedaPreciso(pmvMes)} = ${fmtMoeda(fatSupply)}`
            + `\nMeta do Consenso: ${fmtVol(meta)} cx`;

          return (
            <div className="flex flex-col items-center min-w-[160px] gap-1">
              {ehSku ? (
                <VolInput valor={supply} disabled={isLocked} onCommit={(v) => editarSku(node.produto!, mc.mes_banco, v)} />
              ) : (
                <span className="text-sm font-black text-slate-800 py-1.5">{fmtVol(supply)}</span>
              )}
              {/* Faturamento da entrega (auditavel no tooltip) */}
              <span className="text-[10px] font-black text-emerald-600 cursor-help" title={tipCalculo}>
                {fmtMoeda(fatSupply)}
              </span>
              {/* gap vs Consenso */}
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-bold text-slate-400">Meta: {fmtVol(meta)}</span>
                {temDivergencia && (
                  <span className={`text-[9px] font-black flex items-center gap-0.5 px-1.5 py-0.5 rounded ${gap >= 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
                    {gap >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}{Math.abs(gapPct).toFixed(0)}%
                  </span>
                )}
              </div>
              {/* botão de justificativa quando SKU diverge */}
              {ehSku && temDivergencia && !isLocked && (
                <button onClick={() => setJustAberta(justAberta === chaveJust ? null : chaveJust)}
                  className={`flex items-center gap-1 text-[9px] font-black px-2 py-1 rounded-lg border transition-all ${motivoAtual ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-rose-50 text-rose-600 border-rose-200'}`}>
                  <MessageSquare className="w-3 h-3" /> {motivoAtual ? 'Justificado' : 'Justificar'}
                </button>
              )}
              {ehSku && temDivergencia && isLocked && motivoAtual && (
                <span className="text-[9px] text-slate-400 font-bold" title={justAtual}>
                  {MOTIVOS.find(m => m.valor === motivoAtual)?.label || motivoAtual}
                </span>
              )}
              {/* painel de justificativa */}
              {ehSku && justAberta === chaveJust && (
                <div className="absolute z-20 mt-16 bg-white rounded-2xl shadow-xl border border-slate-200 p-4 w-64">
                  <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">Justificativa do ajuste</p>
                  <select value={motivoAtual} onChange={(e) => editarJustificativa(node.produto!, mc.mes_banco, 'motivo', e.target.value, supply)}
                    className="w-full mb-2 text-xs rounded-lg border border-slate-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500">
                    <option value="">Selecione o motivo...</option>
                    {MOTIVOS.map(m => <option key={m.valor} value={m.valor}>{m.label}</option>)}
                  </select>
                  <textarea value={justAtual} onChange={(e) => editarJustificativa(node.produto!, mc.mes_banco, 'justificativa', e.target.value, supply)}
                    placeholder="Detalhe (opcional)" rows={3}
                    className="w-full text-xs rounded-lg border border-slate-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none" />
                  <button onClick={() => setJustAberta(null)} className="mt-2 w-full text-[10px] font-black text-white bg-indigo-600 rounded-lg py-1.5 hover:bg-indigo-700">OK</button>
                </div>
              )}
            </div>
          );
        }
      });
    });
    return cols;
  }, [dados, meses, isLocked, edicoes, justAberta, volNode, metaNode, pmvMedioSku]);

  const table = useReactTable({
    data: arvore, columns,
    state: { expanded, sorting },
    onExpandedChange: setExpanded, onSortingChange: setSorting,
    getSubRows: (r: any) => r.subRows,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) {
    return <div className="min-h-screen bg-[#f8fafc] flex items-center justify-center"><Loader2 className="w-12 h-12 text-indigo-600 animate-spin" /></div>;
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-10">

        {/* HEADER */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 bg-white p-8 rounded-[40px] shadow-sm border border-slate-100 relative overflow-hidden">
          {supplyCongelado && (
            <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase z-10 bg-emerald-500">
              <ShieldCheck className="w-4 h-4" /> SUPPLY CONGELADO · BASTÃO NO PLANO FINAL
            </div>
          )}
          {!supplyCongelado && !metasCongelado && (
            <div className="absolute top-0 left-0 w-full text-white text-[10px] font-black py-2 flex justify-center items-center gap-3 tracking-[0.3em] uppercase z-10 bg-amber-500">
              <AlertTriangle className="w-4 h-4" /> O CONSENSO (METAS) AINDA NÃO FOI PUBLICADO · EDIÇÃO BLOQUEADA
            </div>
          )}
          <div className={supplyCongelado || !metasCongelado ? 'pt-6' : ''}>
            <div className="flex items-center gap-4">
              <div className="p-3 bg-indigo-50 rounded-2xl"><Factory className="w-8 h-8 text-indigo-600" /></div>
              <div>
                <h1 className="text-3xl font-black text-slate-900 tracking-tighter uppercase">Supply Review</h1>
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mt-1">Ciclo {ciclo} · Fábrica · Meta vs Capacidade</p>
              </div>
            </div>
          </div>
          <div className={`flex items-center gap-3 ${supplyCongelado || !metasCongelado ? 'pt-6' : ''}`}>
            {supplyCongelado ? (
              <button onClick={reabrir} disabled={saving} className="flex items-center gap-2 bg-amber-50 hover:bg-amber-100 text-amber-700 border border-amber-200 font-bold py-3 px-5 rounded-2xl transition-all">
                <Unlock className="w-4 h-4" /> Reabrir Etapa
              </button>
            ) : (
              <>
                <button onClick={salvar} disabled={saving || !temPend}
                  className={`flex items-center gap-2 font-bold py-3 px-5 rounded-2xl transition-all shadow-sm border ${temPend ? 'bg-slate-50 hover:bg-slate-100 text-slate-700 border-slate-200' : 'bg-slate-50 text-slate-300 border-slate-100 cursor-not-allowed'}`}>
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Salvar
                </button>
                <button onClick={congelar} disabled={saving}
                  className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white font-bold py-3 px-6 rounded-2xl shadow-lg shadow-indigo-600/30 transition-all">
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />} Congelar Supply
                </button>
              </>
            )}
          </div>
        </div>

        {/* CARDS */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="p-6 rounded-[28px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[150px]">
            <div className="flex justify-between items-start">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Consenso Pediu</h3>
              <Package className="w-4 h-4 text-slate-400" />
            </div>
            <div className="mt-auto pt-3">
              <p className="text-xl font-black tracking-tighter text-slate-700">{fmtVol(kpis.vMeta)} <span className="text-[9px] opacity-60">CX</span></p>
              <p className="text-[11px] font-bold text-slate-400 mt-1">{fmtMoeda(kpis.fMeta)}</p>
            </div>
          </div>
          <div className="p-6 rounded-[28px] shadow-sm bg-white border-2 border-indigo-200 flex flex-col justify-between min-h-[150px]">
            <div className="flex justify-between items-start">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-indigo-500">Fábrica Entrega</h3>
              <Factory className="w-4 h-4 text-indigo-500" />
            </div>
            <div className="mt-auto pt-3">
              <p className="text-xl font-black tracking-tighter text-indigo-700">{fmtVol(kpis.vSup)} <span className="text-[9px] opacity-60">CX</span></p>
              <p className="text-[11px] font-bold text-slate-400 mt-1">{fmtMoeda(kpis.fSup)}</p>
            </div>
          </div>
          <div className="p-6 rounded-[28px] shadow-sm bg-white border border-slate-100 flex flex-col justify-between min-h-[150px]">
            <div className="flex justify-between items-start">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Faturamento Entrega</h3>
              <TrendingUp className="w-4 h-4 text-emerald-500" />
            </div>
            <div className="mt-auto pt-3">
              <p className="text-xl font-black tracking-tighter text-emerald-600">{fmtMoeda(kpis.fSup)}</p>
            </div>
          </div>
          <div className="p-6 rounded-[28px] shadow-sm bg-slate-900 text-white flex flex-col justify-between min-h-[150px] relative overflow-hidden">
            <div className="absolute top-0 right-0 w-28 h-28 bg-gradient-to-bl from-indigo-500/20 to-transparent rounded-full -mr-8 -mt-8 blur-xl" />
            <div className="flex justify-between items-start z-10">
              <h3 className="text-[10px] font-black uppercase tracking-widest text-indigo-300">Gap vs Consenso</h3>
              {kpis.gap >= 0 ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
            </div>
            <div className="mt-auto pt-3 z-10">
              <p className={`text-2xl font-black tracking-tighter ${kpis.gap >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {kpis.gap >= 0 ? '+' : ''}{kpis.gapPct.toFixed(1)}%
              </p>
              <p className="text-[9px] font-bold text-slate-400 mt-2 uppercase tracking-widest">{kpis.gap >= 0 ? '+' : ''}{fmtVol(kpis.gap)} cx vs comercial</p>
            </div>
          </div>
        </div>

        {/* BUSCA */}
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar categoria ou SKU..."
              className="w-full bg-white border border-slate-200 rounded-2xl pl-11 pr-4 py-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 shadow-sm" />
          </div>
          {temPend && (
            <span className="flex items-center gap-1.5 text-[11px] font-black text-amber-600 bg-amber-50 border border-amber-200 px-3 py-2 rounded-xl">
              <AlertTriangle className="w-3.5 h-3.5" /> Alterações não salvas
            </span>
          )}
        </div>

        {/* TABELA */}
        <div className="bg-white rounded-[32px] shadow-sm border border-slate-100 overflow-visible">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id} className="border-b border-slate-100">
                    {hg.headers.map(header => (
                      <th key={header.id} onClick={header.column.getToggleSortingHandler()}
                        className="text-left px-4 py-4 text-[11px] font-black text-slate-400 uppercase tracking-widest cursor-pointer select-none hover:text-slate-600 whitespace-nowrap">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {{ asc: ' ↑', desc: ' ↓' }[header.column.getIsSorted() as string] ?? ''}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr key={row.id} className={`border-b border-slate-50 transition-colors ${row.original.tipo === 'categoria' ? 'bg-slate-50/50' : 'hover:bg-indigo-50/30'}`}>
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-4 relative">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-[10px] text-slate-400 font-medium mt-4">
          O Supply define a entrega da fábrica por SKU. Restrições ou aumentos vs o Consenso exigem justificativa. O ajuste desce aos clientes pela proporção da meta comercial, preservando as prioridades do consenso. Congelar passa o bastão ao Plano Final.
        </p>

      </div>
    </div>
  );
}