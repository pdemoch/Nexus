import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Lock, Unlock, Download,
  Loader2, LineChart as LineIcon, LayoutGrid, ClipboardList, Search, X,
} from 'lucide-react';
import DossieInferior from './Dossieinferior';
import VisaoGeralMarketing from './VisaoGeralMarketing';

/* =====================================================================
   DEMANDA MARKETING (Top-Down) — duas abas:
   1) Visão Geral: quem cresce/cai (volume, PMV) e quem mais acerta.
   2) Preenchimento: a mesa de trabalho SKU a SKU, com dossiê split-screen.
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`;
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

// Semáforo: a IA discorda muito do TopDown? (divergência relativa)
function divergenciaIA(ia: number, td: number): 'alta' | 'media' | null {
  if (!ia || !td) return null;
  const d = Math.abs(td - ia) / Math.max(ia, td);
  if (d >= 0.4) return 'alta';
  if (d >= 0.2) return 'media';
  return null;
}

export default function DemandaGerenciamento() {
  const [aba, setAba] = useState<'geral' | 'preenchimento'>('geral');
  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <div className="shrink-0 bg-white border-b border-slate-200 px-6 pt-3">
        <div className="flex items-center gap-1">
          <button onClick={() => setAba('geral')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors ${aba === 'geral' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <LayoutGrid className="w-4 h-4" /> Visão Geral
          </button>
          <button onClick={() => setAba('preenchimento')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors ${aba === 'preenchimento' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <ClipboardList className="w-4 h-4" /> Preenchimento
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {aba === 'geral' ? (
          <VisaoGeralMarketing prefixoApi="/api/v1/gerenciamento" />
        ) : (
          <PreenchimentoBottomUp />
        )}
      </div>
    </div>
  );
}

function PreenchimentoBottomUp() {
  const [dados, setDados] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, number>>({}); // `${sku}|${mes}` -> valor
  const [dossiesAbertos, setDossiesAbertos] = useState<Set<string>>(new Set()); // múltiplos SKUs
  const [busca, setBusca] = useState(''); // filtro de busca (categoria, SKU, descrição)

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/gerenciamento/tabela');
      setDados(r.data);
      setEdits({});
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[] = dados?.meses || [];
  const congelada: boolean = dados?.congelada;

  // valor corrente de uma célula (edição local sobrepõe o banco)
  const valorCelula = (sku: string, mes: string, original: number) => {
    const k = `${sku}|${mes}`;
    return k in edits ? edits[k] : original;
  };

  const setCelula = (sku: string, mes: string, v: number) => {
    setEdits((e) => ({ ...e, [`${sku}|${mes}`]: Math.max(0, Math.round(v || 0)) }));
  };

  // Totalizadores VIVOS — recalculam com as edições locais
  const totaisVivos = useMemo(() => {
    const vol: Record<string, number> = {};
    const fat: Record<string, number> = {};
    const orc: Record<string, number> = {};
    meses.forEach((m) => { vol[m] = 0; fat[m] = 0; orc[m] = 0; });
    dados?.categorias?.forEach((cat: any) =>
      cat.segmentos.forEach((seg: any) =>
        seg.skus.forEach((s: any) =>
          meses.forEach((m) => {
            const cel = s.meses[m];
            if (!cel) return;
            const v = valorCelula(s.sku, m, cel.bottomup);
            vol[m] += v;
            fat[m] += v * (cel.pmv || 0);
            orc[m] += cel.orcamento || 0;
          })
        )
      )
    );
    return { vol, fat, orc };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  // Filtra categorias/segmentos/SKUs pela busca.
  // Se busca não-vazia: filtra SKUs cujo sku/descricao/categoria bate, e
  // mantém só as categorias/segmentos que têm pelo menos 1 SKU com match.
  // Categorias com match abrem automaticamente.
  const categoriasFiltradas = useMemo(() => {
    const q = busca.trim().toLowerCase();
    if (!q) return dados?.categorias || [];
    return (dados?.categorias || [])
      .map((cat: any) => {
        const catMatch = cat.nome.toLowerCase().includes(q);
        const segsFiltrados = cat.segmentos.map((seg: any) => {
          const skusFiltrados = seg.skus.filter((s: any) =>
            catMatch ||
            s.sku.toLowerCase().includes(q) ||
            s.descricao.toLowerCase().includes(q)
          );
          return skusFiltrados.length > 0 ? { ...seg, skus: skusFiltrados } : null;
        }).filter(Boolean);
        return segsFiltrados.length > 0 ? { ...cat, segmentos: segsFiltrados } : null;
      })
      .filter(Boolean);
  }, [dados, busca]);

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [sku, mes] = k.split('|');
        return { sku, mes_projetado: mes, novo_volume: v };
      });
      await axios.post('/api/v1/gerenciamento/salvar', { ajustes });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally {
      setSalvando(false);
    }
  };

  const toggle = (nome: string) =>
    setAbertas((p) => { const n = new Set(p); n.has(nome) ? n.delete(nome) : n.add(nome); return n; });

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando o plano…
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      {/* AREA DA TABELA — rolável, encolhe quando o dossiê abre */}
      <div className="flex-1 min-h-0 overflow-y-auto">
      {/* ---------- CABEÇALHO + TOTALIZADORES VIVOS ---------- */}
      <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
        <div className="px-6 pt-5 pb-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-black text-slate-900 tracking-tight">Demanda Marketing</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · defina o Vol. BottomUp por SKU
              {congelada && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* Campo de busca por categoria, SKU ou descrição */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              <input
                type="text"
                value={busca}
                onChange={e => setBusca(e.target.value)}
                placeholder="Buscar categoria, SKU ou produto…"
                className="pl-8 pr-7 py-2 text-xs rounded-xl border border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:outline-none focus:border-indigo-400 w-64"
              />
              {busca && (
                <button onClick={() => setBusca('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            <button
              onClick={salvar}
              disabled={!temEdicoes || salvando || congelada}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !congelada ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}
            >
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            <button
              onClick={async () => {
                if (temEdicoes) await salvar();
                try {
                  const resp = await axios.get('/api/v1/gerenciamento/exportar', { responseType: 'blob' });
                  const url = window.URL.createObjectURL(new Blob([resp.data]));
                  const a = document.createElement('a');
                  a.href = url; a.download = 'bottomup_preenchimento.xlsx';
                  document.body.appendChild(a); a.click(); document.body.removeChild(a);
                  window.URL.revokeObjectURL(url);
                } catch { alert('Erro ao gerar o Excel. Tente novamente.'); }
              }}
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
            >
              <Download className="w-4 h-4" /> Excel
            </button>
          </div>
        </div>

        {/* faixa de totalizadores por mês */}
        <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr))` }}>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total do plano</div>
          {meses.map((m) => (
            <div key={m} className="bg-slate-50 rounded-lg px-3 py-2">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
              {totaisVivos.orc[m] > 0 && (
                <div className="text-[9px] font-bold text-amber-500">orç {fmtRs(totaisVivos.orc[m])}</div>
              )}
              <div className="text-[11px] font-bold text-indigo-500">{fmtRs(totaisVivos.fat[m])}</div>
              <div className="text-sm font-black text-slate-900">{fmtCx(totaisVivos.vol[m])} <span className="text-[10px] font-bold text-slate-400">cx</span></div>
            </div>
          ))}
        </div>
      </div>

      {/* ---------- CABEÇALHO DA TABELA ---------- */}
      <div className="px-6 pt-4">
        <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
             style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
          <div>Categoria / SKU</div>
          {meses.map((m) => <div key={m} className="text-right">{mesLabel(m)}</div>)}
          <div />
        </div>
      </div>

      {/* ---------- CORPO ---------- */}
      <div className="px-6 pb-6">
        {categoriasFiltradas.map((cat: any) => {
          // Com busca ativa, abre automaticamente todas as categorias que têm match
          const catAberta = busca.trim() ? true : abertas.has(cat.nome);
          return (
            <div key={cat.nome} className="mb-1">
              <button
                onClick={() => toggle(cat.nome)}
                className="w-full grid gap-2 px-3 py-2.5 items-center bg-white rounded-lg border border-slate-100 hover:border-slate-200 transition-colors"
                style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  {catAberta ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                  <span className="font-black text-slate-800 text-sm truncate">{cat.nome}</span>
                </div>
                {meses.map((m) => {
                  const soma = cat.segmentos.reduce((acc: number, seg: any) =>
                    acc + seg.skus.reduce((a: number, s: any) => a + valorCelula(s.sku, m, s.meses[m]?.topdown || 0), 0), 0);
                  const fatCat = cat.segmentos.reduce((acc: number, seg: any) =>
                    acc + seg.skus.reduce((a: number, s: any) => {
                      const cel = s.meses[m]; if (!cel) return a;
                      return a + valorCelula(s.sku, m, cel.bottomup) * (cel.pmv || 0);
                    }, 0), 0);
                  const orcCat = cat.segmentos.reduce((acc: number, seg: any) =>
                    acc + seg.skus.reduce((a: number, s: any) => a + (s.meses[m]?.orcamento || 0), 0), 0);
                  return (
                    <div key={m} className="text-right">
                      <div className="text-[10px] font-bold text-indigo-400">{fmtRs(fatCat)}</div>
                      <div className="text-sm font-bold text-slate-500">{fmtCx(soma)} cx</div>
                      {orcCat > 0 && <div className="text-[10px] font-bold text-amber-500">orç {fmtRs(orcCat)}</div>}
                    </div>
                  );
                })}
                <div />
              </button>

              {catAberta && cat.segmentos.map((seg: any) => (
                <div key={seg.nome} className="ml-4 mt-1">
                  <div className="px-3 py-1.5 text-[11px] font-black uppercase tracking-wider text-slate-400">{seg.nome}</div>
                  {seg.skus.map((s: any) => {
                    // semáforo: maior divergência entre os meses
                    const div = meses.reduce<'alta' | 'media' | null>((acc, m) => {
                      const cel = s.meses[m]; if (!cel) return acc;
                      const d = divergenciaIA(cel.ia, valorCelula(s.sku, m, cel.bottomup));
                      return d === 'alta' ? 'alta' : (d === 'media' && acc !== 'alta' ? 'media' : acc);
                    }, null);
                    const barra = div === 'alta' ? 'bg-rose-400' : div === 'media' ? 'bg-amber-400' : 'bg-transparent';
                    const aberto = dossiesAbertos.has(s.sku);
                    return (
                      <React.Fragment key={s.sku}>
                      <div
                        className="grid gap-2 px-3 py-1.5 items-center hover:bg-white rounded-lg group relative"
                        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
                        <div className={`absolute left-0 top-1 bottom-1 w-0.5 rounded ${barra}`} />
                        <div className="min-w-0 pl-2">
                          <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                          <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                        </div>
                        {meses.map((m) => {
                          const cel = s.meses[m];
                          if (!cel) return <div key={m} />;
                          const val = valorCelula(s.sku, m, cel.bottomup);
                          const editado = `${s.sku}|${m}` in edits;
                          const fatPrev = val && cel.pmv ? val * cel.pmv : null;
                          const orcPrev = cel.orcamento ?? null;
                          return (
                            <div key={m} className="text-right">
                              {/* orçamento previsto — referência da empresa */}
                              {orcPrev != null && orcPrev > 0 && (
                                <div className="text-[9px] font-bold text-amber-500 pr-2">orç {fmtRs(orcPrev)}</div>
                              )}
                              {/* valor pedido previsto — dinâmico: volume × PMV */}
                              <div className="text-[9px] font-bold text-indigo-400 pr-2 mb-0.5">
                                {fatPrev != null ? fmtRs(fatPrev) : '—'}
                              </div>
                              <input
                                type="number"
                                value={val}
                                disabled={congelada}
                                onChange={(e) => setCelula(s.sku, m, parseInt(e.target.value))}
                                className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                                  ${editado ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                                  ${congelada ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
                              />
                              <div className="text-[9px] font-bold text-slate-300 pr-2">IA {fmtCx(cel.ia)}</div>
                            </div>
                          );
                        })}
                        <button
                          onClick={async () => {
                            // Salva edições pendentes antes de abrir o dossiê,
                            // para que a linha "Meta" reflita o vol_topdown atual.
                            if (temEdicoes) await salvar();
                            setDossiesAbertos((prev) => {
                              const novo = new Set(prev);
                              novo.has(s.sku) ? novo.delete(s.sku) : novo.add(s.sku);
                              return novo;
                            });
                          }}
                          className={`justify-self-center p-1.5 rounded-lg transition-colors ${aberto ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
                          title="Ver dossiê do SKU"
                        >
                          <LineIcon className="w-4 h-4" />
                        </button>
                      </div>
                      {aberto && (
                        <DossieInferior
                          prefixoApi="/api/v1/gerenciamento"
                          tipo="sku"
                          id={s.sku}
                          titulo={s.descricao}
                          onFechar={() => setDossiesAbertos((prev) => {
                            const novo = new Set(prev);
                            novo.delete(s.sku);
                            return novo;
                          })}
                        />
                      )}
                      </React.Fragment>
                    );
                  })}
                </div>
              ))}
            </div>
          );
        })}
        {categoriasFiltradas.length === 0 && busca.trim() && (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <Search className="w-8 h-8 mb-2 opacity-30" />
            <div className="text-sm font-bold">Nenhum resultado para "{busca}"</div>
            <div className="text-xs mt-1">Tente buscar por categoria, código SKU ou nome do produto.</div>
            <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">
              Limpar busca
            </button>
          </div>
        )}
      </div>
      {/* fim do corpo da tabela */}
      </div>
    </div>
  );
}