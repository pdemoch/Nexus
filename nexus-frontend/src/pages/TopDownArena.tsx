import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Lock, Unlock, Download,
  Loader2, LineChart as LineIcon,
} from 'lucide-react';
import DossieInferior from './DossieInferior';

/* =====================================================================
   DEMANDA MARKETING (Top-Down) — mesa de trabalho do planejador
   Partido: tabela densa + coluna editável indigo + semáforo por linha +
   totalizadores vivos + gaveta lateral de dossiê (o signature).
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

export default function DemandaMarketing() {
  const [dados, setDados] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, number>>({}); // `${sku}|${mes}` -> valor
  const [dossie, setDossie] = useState<{ sku: string; descricao: string } | null>(null);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/topdown/tabela');
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
    const vol: Record<string, number> = {}; const fat: Record<string, number> = {};
    meses.forEach((m) => { vol[m] = 0; fat[m] = 0; });
    dados?.categorias?.forEach((cat: any) =>
      cat.segmentos.forEach((seg: any) =>
        seg.skus.forEach((s: any) =>
          meses.forEach((m) => {
            const cel = s.meses[m];
            if (!cel) return;
            const v = valorCelula(s.sku, m, cel.topdown);
            vol[m] += v;
            fat[m] += v * (cel.pmv || 0);
          })
        )
      )
    );
    return { vol, fat };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [sku, mes] = k.split('|');
        return { sku, mes_projetado: mes, novo_volume: v };
      });
      await axios.post('/api/v1/topdown/salvar', { ajustes });
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
    <div className="h-screen flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      {/* AREA DA TABELA — rolável, encolhe quando o dossiê abre */}
      <div className="flex-1 min-h-0 overflow-y-auto">
      {/* ---------- CABEÇALHO + TOTALIZADORES VIVOS ---------- */}
      <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
        <div className="px-6 pt-5 pb-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-black text-slate-900 tracking-tight">Demanda Marketing</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · defina o volume-alvo por SKU
              {congelada && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={salvar}
              disabled={!temEdicoes || salvando || congelada}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !congelada ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}
            >
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            <a
              href="/api/v1/topdown/exportar"
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
            >
              <Download className="w-4 h-4" /> CSV
            </a>
          </div>
        </div>

        {/* faixa de totalizadores por mês */}
        <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr))` }}>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total do plano</div>
          {meses.map((m) => (
            <div key={m} className="bg-slate-50 rounded-lg px-3 py-2">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
              <div className="text-sm font-black text-slate-900">{fmtCx(totaisVivos.vol[m])} <span className="text-[10px] font-bold text-slate-400">cx</span></div>
              <div className="text-[11px] font-bold text-emerald-600">{fmtRs(totaisVivos.fat[m])}</div>
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
        {dados?.categorias?.map((cat: any) => {
          const catAberta = abertas.has(cat.nome);
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
                  return <div key={m} className="text-right text-sm font-bold text-slate-500">{fmtCx(soma)}</div>;
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
                      const d = divergenciaIA(cel.ia, valorCelula(s.sku, m, cel.topdown));
                      return d === 'alta' ? 'alta' : (d === 'media' && acc !== 'alta' ? 'media' : acc);
                    }, null);
                    const barra = div === 'alta' ? 'bg-rose-400' : div === 'media' ? 'bg-amber-400' : 'bg-transparent';
                    return (
                      <div key={s.sku}
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
                          const val = valorCelula(s.sku, m, cel.topdown);
                          const editado = `${s.sku}|${m}` in edits;
                          return (
                            <div key={m} className="text-right">
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
                          onClick={() => setDossie({ sku: s.sku, descricao: s.descricao })}
                          className={`justify-self-center p-1.5 rounded-lg transition-colors ${dossie?.sku === s.sku ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
                          title="Ver dossiê do SKU"
                        >
                          <LineIcon className="w-4 h-4" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {/* fim do corpo da tabela */}
      </div>
      {/* ---------- DOSSIÊ INFERIOR (split-screen) ---------- */}
      {dossie && (
        <DossieInferior
          prefixoApi="/api/v1/topdown"
          tipo="sku"
          id={dossie.sku}
          titulo={dossie.descricao}
          onFechar={() => setDossie(null)}
        />
      )}
    </div>
  );
}