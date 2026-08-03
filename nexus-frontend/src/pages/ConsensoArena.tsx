import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, Download, X, Lock, Unlock, ShieldCheck,
  TrendingUp, TrendingDown, Minus, Bot, AlertTriangle, Loader2, LineChart as LineIcon,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';

/* =====================================================================
   METAS COMERCIAL — a cascata gerente->coordenador->vendedor->razão->SKU.
   Mesmo padrão visual, com duas particularidades:
   • Árvore de 5 níveis (RLS: cada um vê só a sua carteira).
   • Cadeado por responsável: "bloquear minha carteira" + painel de quem
     já bloqueou. Congelar a ETAPA continua sendo só do Admin.
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n || 0));
const fmtPct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`;
const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
  return `${nomes[parseInt(m) - 1]}/${y.slice(2)}`;
};

const INDENT: Record<string, string> = {
  gerente: 'pl-0', coordenador: 'pl-4', vendedor: 'pl-8', cliente: 'pl-12', produto: 'pl-16',
};

export default function MetasComercial() {
  const [dados, setDados] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, number>>({}); // `${razao}||${sku}||${mes}` -> vol
  const [dossieSku, setDossieSku] = useState<string | null>(null);
  const [painelCadeados, setPainelCadeados] = useState(false);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/v1/metas/tabela');
      setDados(r.data); setEdits({});
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[] = dados?.meses || [];
  const congeladaEtapa: boolean = dados?.etapa_congelada;
  const minhaCongelada: boolean = dados?.minha_carteira_congelada;
  const bloqueado = congeladaEtapa || minhaCongelada;

  const keyOf = (razao: string, sku: string, mes: string) => `${razao}||${sku}||${mes}`;
  const valorCelula = (razao: string, sku: string, mes: string, original: number) => {
    const k = keyOf(razao, sku, mes);
    return k in edits ? edits[k] : original;
  };
  const setCelula = (razao: string, sku: string, mes: string, v: number) => {
    setEdits((e) => ({ ...e, [keyOf(razao, sku, mes)]: Math.max(0, Math.round(v || 0)) }));
  };

  const totaisVivos = useMemo(() => {
    const vol: Record<string, number> = {}; const fat: Record<string, number> = {};
    meses.forEach((m) => { vol[m] = 0; fat[m] = 0; });
    const walk = (node: any, razaoCtx: string | null) => {
      if (node.tipo === 'produto') {
        meses.forEach((m) => {
          const cel = node.meses[m]; if (!cel) return;
          const v = valorCelula(razaoCtx || '', node.sku, m, cel.meta);
          vol[m] += v; fat[m] += v * (cel.pmv || 0);
        });
        return;
      }
      const novoCtx = node.tipo === 'cliente' ? node.nome : razaoCtx;
      (node.subRows || []).forEach((f: any) => walk(f, novoCtx));
    };
    (dados?.arvore || []).forEach((g: any) => walk(g, null));
    return { vol, fat };
  }, [dados, edits, meses]);

  const temEdicoes = Object.keys(edits).length > 0;

  const salvar = async () => {
    if (!temEdicoes) return;
    setSalvando(true);
    try {
      const ajustes = Object.entries(edits).map(([k, v]) => {
        const [razao_social, sku, mes] = k.split('||');
        return { razao_social, sku, mes_projetado: mes, novo_volume: v };
      });
      await axios.post('/api/v1/metas/salvar', { ajustes });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar.');
    } finally { setSalvando(false); }
  };

  const bloquearMinha = async () => {
    if (!confirm('Bloquear sua carteira? Após bloquear, você não poderá mais editar suas metas neste ciclo.')) return;
    try {
      await axios.post('/api/v1/metas/bloquear', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao bloquear.'); }
  };

  const toggle = (id: string) =>
    setAbertas((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando sua carteira…</div>;
  }

  return (
    <div className="min-h-screen bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      {/* CABEÇALHO */}
      <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
        <div className="px-6 pt-5 pb-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-black text-slate-900 tracking-tight">Metas Comercial</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · distribua a meta na sua carteira
              {minhaCongelada && <span className="ml-2 text-emerald-600 font-bold">· sua carteira bloqueada</span>}
              {congeladaEtapa && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setPainelCadeados(true)}
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
              <ShieldCheck className="w-4 h-4" /> Cadeados
            </button>
            <button onClick={salvar} disabled={!temEdicoes || salvando || bloqueado}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                ${temEdicoes && !bloqueado ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
              {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Salvar{temEdicoes ? ` (${Object.keys(edits).length})` : ''}
            </button>
            {!minhaCongelada && !dados?.sou_admin && (
              <button onClick={bloquearMinha}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-emerald-600 text-white hover:bg-emerald-700">
                <Lock className="w-4 h-4" /> Bloquear minha carteira
              </button>
            )}
            <a href="/api/v1/metas/exportar"
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-white border border-slate-200 text-slate-600 hover:bg-slate-50">
              <Download className="w-4 h-4" /> CSV
            </a>
          </div>
        </div>
        <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr))` }}>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total da carteira</div>
          {meses.map((m) => (
            <div key={m} className="bg-slate-50 rounded-lg px-3 py-2">
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
              <div className="text-sm font-black text-slate-900">{fmtCx(totaisVivos.vol[m])} <span className="text-[10px] font-bold text-slate-400">cx</span></div>
              <div className="text-[11px] font-bold text-emerald-600">{fmtRs(totaisVivos.fat[m])}</div>
            </div>
          ))}
        </div>
      </div>

      {/* CABEÇALHO TABELA */}
      <div className="px-6 pt-4">
        <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
             style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
          <div>Hierarquia / Cliente / SKU</div>
          {meses.map((m) => <div key={m} className="text-right">{mesLabel(m)}</div>)}
          <div />
        </div>
      </div>

      {/* ÁRVORE RECURSIVA */}
      <div className="px-6 pb-24">
        {(dados?.arvore || []).map((g: any) => (
          <NoArvore key={g.nome} node={g} razaoCtx={null} nivel={0}
            meses={meses} abertas={abertas} toggle={toggle}
            valorCelula={valorCelula} setCelula={setCelula} bloqueado={bloqueado}
            edits={edits} setDossieSku={setDossieSku} idPath={g.nome} />
        ))}
      </div>

      {dossieSku && <GavetaDossie sku={dossieSku} fechar={() => setDossieSku(null)} />}
      {painelCadeados && <PainelCadeados fechar={() => setPainelCadeados(false)} recarregar={carregar} />}
    </div>
  );
}

/* Nó recursivo da árvore de 5 níveis */
function NoArvore({ node, razaoCtx, nivel, meses, abertas, toggle, valorCelula, setCelula, bloqueado, edits, setDossieSku, idPath }: any) {
  if (node.tipo === 'produto') {
    const razao = razaoCtx || '';
    return (
      <div className="grid gap-2 px-3 py-1.5 items-center hover:bg-white rounded-lg group"
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
        <div className={`min-w-0 ${INDENT.produto}`}>
          <div className="text-xs font-bold text-slate-700 truncate">{node.descricao}</div>
          <div className="text-[10px] font-bold text-slate-300">{node.sku}</div>
        </div>
        {meses.map((m: string) => {
          const cel = node.meses[m];
          if (!cel) return <div key={m} />;
          const val = valorCelula(razao, node.sku, m, cel.meta);
          const editado = `${razao}||${node.sku}||${m}` in edits;
          return (
            <div key={m} className="text-right">
              <input type="number" value={val} disabled={bloqueado}
                onChange={(e) => setCelula(razao, node.sku, m, parseInt(e.target.value))}
                className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                  ${editado ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-transparent bg-transparent text-slate-700'}
                  ${bloqueado ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`} />
              <div className="text-[9px] font-bold text-slate-300 pr-2">BU {fmtCx(cel.bottomup)}</div>
            </div>
          );
        })}
        <button onClick={() => setDossieSku(node.sku)}
          className="justify-self-center p-1.5 rounded-lg text-slate-300 hover:bg-indigo-50 hover:text-indigo-600 transition-colors"
          title="Ver dossiê do SKU">
          <LineIcon className="w-4 h-4" />
        </button>
      </div>
    );
  }

  const aberta = abertas.has(idPath);
  const novoCtx = node.tipo === 'cliente' ? node.nome : razaoCtx;
  const somaMes = (m: string): number => {
    let s = 0;
    const walk = (n: any, rz: string | null) => {
      if (n.tipo === 'produto') { s += valorCelula(rz || '', n.sku, m, n.meses[m]?.meta || 0); return; }
      const c = n.tipo === 'cliente' ? n.nome : rz;
      (n.subRows || []).forEach((f: any) => walk(f, c));
    };
    walk(node, razaoCtx);
    return s;
  };

  return (
    <div className="mb-0.5">
      <button onClick={() => toggle(idPath)}
        className={`w-full grid gap-2 px-3 py-2 items-center rounded-lg transition-colors ${nivel === 0 ? 'bg-white border border-slate-100 hover:border-slate-200' : 'hover:bg-white'}`}
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(120px, 1fr)) 40px` }}>
        <div className={`flex items-center gap-1.5 min-w-0 ${INDENT[node.tipo]}`}>
          {aberta ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
          <span className={`truncate ${nivel === 0 ? 'font-black text-slate-800 text-sm' : node.tipo === 'cliente' ? 'font-bold text-slate-600 text-xs' : 'font-bold text-slate-500 text-xs'}`}>
            {node.nome}
          </span>
          <span className="text-[9px] font-black uppercase tracking-wider text-slate-300 ml-1">{node.tipo}</span>
        </div>
        {meses.map((m: string) => <div key={m} className="text-right text-xs font-bold text-slate-400">{fmtCx(somaMes(m))}</div>)}
        <div />
      </button>
      {aberta && (node.subRows || []).map((f: any, i: number) => (
        <NoArvore key={(f.nome || f.sku) + i} node={f} razaoCtx={novoCtx} nivel={nivel + 1}
          meses={meses} abertas={abertas} toggle={toggle}
          valorCelula={valorCelula} setCelula={setCelula} bloqueado={bloqueado}
          edits={edits} setDossieSku={setDossieSku} idPath={`${idPath}>${f.nome || f.sku}`} />
      ))}
    </div>
  );
}

/* Painel de cadeados por responsável */
function PainelCadeados({ fechar, recarregar }: { fechar: () => void; recarregar: () => void }) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/metas/cadeados').then((r) => setD(r.data)).finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

  const reabrir = async (nome: string) => {
    if (!confirm(`Reabrir a carteira de ${nome}?`)) return;
    try {
      await axios.post('/api/v1/metas/reabrir-cadeado', { nome_alvo: nome });
      carregar(); recarregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao reabrir.'); }
  };

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-sm bg-white z-40 shadow-2xl overflow-y-auto">
        <div className="sticky top-0 bg-white border-b border-slate-100 px-5 py-4 flex items-center justify-between">
          <div className="text-sm font-black text-slate-900">Cadeados da equipe</div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100"><X className="w-4 h-4" /></button>
        </div>
        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400"><Loader2 className="w-5 h-5 animate-spin" /></div>
        ) : (
          <div className="p-5">
            {(d?.cadeados || []).length === 0 && (
              <div className="text-center text-slate-400 text-sm py-8">Nenhuma carteira bloqueada ainda.</div>
            )}
            <div className="space-y-2">
              {(d?.cadeados || []).map((c: any) => (
                <div key={c.nome} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="text-xs font-bold text-slate-700 truncate flex items-center gap-1.5">
                      <Lock className="w-3 h-3 text-emerald-600" /> {c.nome}
                    </div>
                    <div className="text-[10px] font-bold text-slate-400">{c.nivel} · por {c.por}</div>
                  </div>
                  {(d?.sou_admin || c.nome === d?.meu_nome) && (
                    <button onClick={() => reabrir(c.nome)}
                      className="flex items-center gap-1 text-[11px] font-bold text-slate-500 hover:text-rose-600 px-2 py-1 rounded-lg hover:bg-white">
                      <Unlock className="w-3 h-3" /> Reabrir
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/* Gaveta de dossiê — mesmo padrão das outras telas */
function GavetaDossie({ sku, fechar }: { sku: string; fechar: () => void }) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    axios.get(`/api/v1/metas/dossie?sku=${encodeURIComponent(sku)}`)
      .then((r) => setD(r.data)).finally(() => setLoading(false));
  }, [sku]);
  const tendIcon = (t: string) =>
    t === 'CRESCIMENTO' ? <TrendingUp className="w-4 h-4 text-emerald-600" /> :
    t === 'DECLINIO' ? <TrendingDown className="w-4 h-4 text-rose-600" /> :
    <Minus className="w-4 h-4 text-slate-400" />;
  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-white z-40 shadow-2xl overflow-y-auto" style={{ fontVariantNumeric: 'tabular-nums' }}>
        <div className="sticky top-0 bg-white border-b border-slate-100 px-5 py-4 flex items-start justify-between">
          <div className="min-w-0">
            <div className="text-[10px] font-black uppercase tracking-widest text-indigo-500">Dossiê do SKU</div>
            <div className="text-sm font-black text-slate-900 truncate">{d?.descricao || sku}</div>
            <div className="text-[10px] font-bold text-slate-300">{sku}</div>
          </div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100"><X className="w-4 h-4" /></button>
        </div>
        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400"><Loader2 className="w-5 h-5 animate-spin" /></div>
        ) : (
          <div className="p-5 space-y-6">
                        {d?.fva && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Quem acertou mais · último mês fechado</div>
                  {d.fva.baixo_volume && (
                    <span className="text-[9px] font-black uppercase tracking-wider text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">baixo volume</span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: 'Humano', val: d.fva.aderencia_humano, vol: d.fva.humano_cx, key: 'HUMANO' },
                    { label: 'IA', val: d.fva.aderencia_ia, vol: d.fva.ia_cx, key: 'IA' },
                    { label: 'Ano passado', val: d.fva.aderencia_naive, vol: d.fva.naive_cx, key: 'ANO PASSADO' },
                  ].map((x) => {
                    const venceu = d.fva.vencedor === x.key;
                    return (
                      <div key={x.key} className={`rounded-xl px-3 py-2 border ${venceu ? 'border-emerald-300 bg-emerald-50' : 'border-slate-100 bg-slate-50'}`}>
                        <div className="text-[10px] font-bold text-slate-400">{x.label}</div>
                        <div className={`text-lg font-black ${venceu ? 'text-emerald-700' : 'text-slate-500'}`}>{x.val != null ? fmtPct(x.val) : '—'}</div>
                        <div className="text-[9px] font-bold text-slate-400">previu {x.vol != null ? fmtCx(x.vol) : '—'} cx</div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-1 text-[10px] font-bold text-slate-400 text-right">vendeu {fmtCx(d.fva.vendido_cx)} cx no mês</div>
                {d.fva.insight_fva && (
                  <div className="mt-2 flex items-start gap-2 text-[11px] font-bold text-slate-600 bg-slate-50 rounded-lg px-3 py-2">
                    <Bot className="w-4 h-4 shrink-0 mt-0.5 text-indigo-500" />
                    {d.fva.insight_fva}
                  </div>
                )}
              </div>
            )}
            <div>
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-2">Histórico · 24 meses</div>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={d?.grafico || []} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="mes" tick={{ fontSize: 9, fill: '#94a3b8' }} interval={3} />
                    <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} />
                    <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #e2e8f0' }} />
                    <Line type="monotone" dataKey="vendido" stroke="#4f46e5" strokeWidth={2} dot={false} name="Vendido" />
                    <Line type="monotone" dataKey="faturado" stroke="#94a3b8" strokeWidth={1.5} dot={false} name="Faturado" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
            {d?.plurianual && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Trajetória plurianual</div>
                  <div className="flex items-center gap-3 text-[10px] font-bold">
                    <span className="flex items-center gap-1 text-slate-500">Vol {tendIcon(d.plurianual.tendencia_volume)}</span>
                    <span className="flex items-center gap-1 text-slate-500">PMV {tendIcon(d.plurianual.tendencia_pmv)}</span>
                  </div>
                </div>
                {d.plurianual.alerta_historico && (
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-amber-600 bg-amber-50 rounded-lg px-3 py-1.5">
                    <AlertTriangle className="w-3.5 h-3.5" /> {d.plurianual.alerta_historico}
                  </div>
                )}
                <div className="space-y-1">
                  {(d.plurianual.anos || []).map((a: any, i: number) => {
                    const ehRecente = i >= (d.plurianual.anos.length - 3);
                    return (
                      <div key={a.ano} className={`grid grid-cols-4 gap-2 px-3 py-1.5 rounded-lg text-xs ${ehRecente ? 'bg-slate-50 font-bold' : 'opacity-50'}`}>
                        <div className="text-slate-500">{a.ano}</div>
                        <div className="text-right text-slate-700">{fmtCx(a.vendido_cx)} cx</div>
                        <div className="text-right text-slate-700">R$ {a.pmv.toFixed(2)}</div>
                        <div className="text-right text-slate-400">{a.razoes_sociais} cli</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}