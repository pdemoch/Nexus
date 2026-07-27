import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  Activity, TrendingUp, TrendingDown, Target, Bot, HelpCircle,
  ChevronDown, ChevronRight, Loader2, AlertTriangle, Filter, X, LineChart as LineChartIcon
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine
} from 'recharts';

// ============================================================
// HELPERS
// ============================================================
const fmtNum = (v: any, visao: string) => {
  const n = Number(v) || 0;
  if (visao === 'financeiro') return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(n));
  return Math.round(n).toLocaleString('pt-BR');
};
const fmtPct = (v: any) => `${(Number(v) || 0).toFixed(1)}%`;

const corAcuracia = (a: number) => a >= 0.7 ? 'text-emerald-600' : a >= 0.5 ? 'text-amber-600' : 'text-rose-600';

// Ponto que marca mês parcial (aro vazado tracejado).
const PontoParcial = (props: any) => {
  const { cx, cy, payload, stroke } = props;
  if (cx == null || cy == null) return null;
  if (payload?.parcial) return <circle cx={cx} cy={cy} r={5} fill="#fff" stroke={stroke} strokeWidth={2} strokeDasharray="2 2" />;
  return <circle cx={cx} cy={cy} r={4} fill={stroke} />;
};

// ============================================================
// COMPONENTE PRINCIPAL
// ============================================================
export default function AuditoriaArena() {
  const [visao, setVisao] = useState<'caixas' | 'financeiro'>('caixas');
  const [mesesDisp, setMesesDisp] = useState<any[]>([]);
  const [mesesSel, setMesesSel] = useState<string[]>([]);
  const [serie, setSerie] = useState<any[]>([]);
  const [totais, setTotais] = useState<any>({});
  const [loading, setLoading] = useState(false);
  const [dossie, setDossie] = useState<any>(null); // {tipo:'categoria'|'sku', nome, descricao}

  useEffect(() => {
    axios.get('/api/v1/kpis/meses-auditaveis').then(r => {
      // Normalizacao defensiva: aceita objeto {mes, ano, num, parcial} ou string.
      const brutos = r.data.meses || [];
      const ms = brutos.map((m: any) => typeof m === 'string'
        ? { mes: m, ano: m.split('/')[1] || '', num: m.split('/')[0] || m, parcial: false }
        : m);
      setMesesDisp(ms);
      setMesesSel(ms.map((m: any) => m.mes));
    }).catch(() => {});
  }, []);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    p.append('visao', visao);
    mesesSel.forEach(m => p.append('meses', m));
    return p;
  }, [visao, mesesSel]);

  const carregar = useCallback(async () => {
    if (mesesSel.length === 0) { setSerie([]); setTotais({}); return; }
    setLoading(true);
    try {
      const [ev, rs] = await Promise.all([
        axios.get(`/api/v1/kpis/evolucao?${params.toString()}`),
        axios.get(`/api/v1/kpis/resumo?${params.toString()}`),
      ]);
      setSerie(ev.data.serie || []);
      setTotais(rs.data.totais || {});
    } catch { setSerie([]); setTotais({}); }
    finally { setLoading(false); }
  }, [params, mesesSel]);

  useEffect(() => { carregar(); }, [carregar]);

  const temParcial = serie.some(s => s.parcial);

  return (
    <div className="min-h-screen bg-slate-50 p-6 lg:p-8">
      <div className="max-w-[1500px] mx-auto">

        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-black text-slate-900 tracking-tight flex items-center gap-2">
              <Activity className="w-7 h-7 text-indigo-600" /> Desvios · Acurácia do S&OP
            </h1>
            <p className="text-sm text-slate-500 font-medium mt-0.5">
              O plano congelado (2 meses antes) vs o que se realizou dele. IA contra o humano, mês a mês.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <SeletorMeses mesesDisp={mesesDisp} mesesSel={mesesSel} setMesesSel={setMesesSel} />
            <div className="flex items-center bg-white rounded-2xl p-1 shadow-sm border border-slate-100">
              <button onClick={() => setVisao('caixas')} className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${visao === 'caixas' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400'}`}>Caixas</button>
              <button onClick={() => setVisao('financeiro')} className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${visao === 'financeiro' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400'}`}>Financeiro</button>
            </div>
          </div>
        </div>

        {/* CARTÕES RESUMO */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <CardResumo titulo="Acurácia" valor={fmtPct((totais.acuracia || 0) * 100)}
            sub={`erro de ${fmtPct((totais.wmape || 0) * 100)}`} tom={(totais.acuracia || 0) >= 0.7 ? 'bom' : (totais.acuracia || 0) >= 0.5 ? 'medio' : 'ruim'}
            icone={<Target className="w-5 h-5" />} ajuda="Acurácia = 1 − WMAPE. Mede, sobre os totais do plano, quão perto o realizado ficou do previsto." />
          <CardResumo titulo="Tendência (BIAS)" valor={totais.vies_label || '—'}
            sub={`viés de ${fmtPct((totais.bias || 0) * 100)}`} tom={Math.abs(totais.bias || 0) < 0.05 ? 'bom' : 'medio'}
            icone={(totais.bias || 0) > 0 ? <TrendingUp className="w-5 h-5" /> : <TrendingDown className="w-5 h-5" />}
            ajuda="BIAS = (previsto − realizado) / realizado. Positivo = prevê demais (infla estoque). Negativo = prevê de menos (rupturas)." />
          <CardResumo titulo="Valor vs IA (FVA)" valor={(totais.fva || 0) >= 0 ? 'Humano supera' : 'IA supera'}
            sub={`FVA ${fmtPct((totais.fva || 0) * 100)}`} tom={(totais.fva || 0) >= 0 ? 'bom' : 'ruim'}
            icone={<Bot className="w-5 h-5" />} ajuda="FVA = erro da IA − erro do humano. Positivo: o ajuste humano melhorou a previsão da máquina." />
          <CardResumo titulo={visao === 'financeiro' ? 'Realizado (R$)' : 'Realizado (cx)'} valor={fmtNum(totais.realizado, visao)}
            sub={`previsto: ${fmtNum(totais.previsto_final, visao)}`} tom="neutro" icone={<Activity className="w-5 h-5" />}
            ajuda="Realizado dos pares que estavam no plano vs o total previsto. O escopo da auditoria é o plano (fato IBP): vendas sem planejamento não entram nesta conta." />
        </div>

        {temParcial && (
          <div className="flex items-center gap-2 text-[12px] font-bold text-amber-600 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 mb-6">
            <AlertTriangle className="w-4 h-4" /> Meses com aro tracejado ainda estão em andamento — a acurácia é parcial e tende a melhorar até o fechamento.
          </div>
        )}

        {loading ? (
          <div className="p-16 flex items-center justify-center text-slate-400"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Calculando indicadores...</div>
        ) : (
          <>
            {/* OS 4 GRÁFICOS DE LINHA — IA vs HUMANO */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
              <GraficoLinha titulo="WMAPE — erro percentual do plano" subtitulo="Quanto o plano errou vs o que se realizou dele. Menor é melhor."
                serie={serie} chaves={[{ k: 'wmape', nome: 'Humano (S&OP)', cor: '#6366f1' }, { k: 'wmape_ia', nome: 'Nexus Bot (IA)', cor: '#94a3b8' }]}
                ajuda="WMAPE = |Σprevisto − Σrealizado| ÷ Σrealizado, sobre os pares planejados. 20% = o plano errou 20 de cada 100 caixas." />
              <GraficoLinha titulo="Acurácia — o quanto acertou" subtitulo="1 − WMAPE. Maior é melhor."
                serie={serie} chaves={[{ k: 'acuracia', nome: 'Humano (S&OP)', cor: '#059669' }, { k: 'acuracia_ia', nome: 'Nexus Bot (IA)', cor: '#94a3b8' }]}
                ajuda="Acurácia = 1 − WMAPE. De cada 100 caixas do escopo planejado, quantas o plano acertou no total." />
              <GraficoLinha titulo="BIAS — tendência de erro" subtitulo="Acima de zero: prevê demais. Abaixo: prevê de menos."
                serie={serie} chaves={[{ k: 'bias', nome: 'Humano (S&OP)', cor: '#d97706' }, { k: 'bias_ia', nome: 'Nexus Bot (IA)', cor: '#94a3b8' }]}
                ajuda="BIAS = (Σprevisto − Σrealizado) ÷ Σrealizado. Viés sistemático: inflar (positivo) ou subestimar (negativo)." zero />
              <GraficoLinha titulo="FVA — valor que o humano agrega" subtitulo="Acima de zero: o humano bate a IA. Abaixo: a IA seria melhor."
                serie={serie} chaves={[{ k: 'fva', nome: 'FVA (humano − IA)', cor: '#7c3aed' }]}
                ajuda="FVA (Forecast Value Added) = erro da IA − erro do humano. Positivo: o ajuste humano melhorou a previsão pura da máquina." zero />
            </div>

            {/* TABELA DRILL: CATEGORIA -> SKU, com os volumes que comprovam */}
            <TabelaDrill params={params} visao={visao} abrirDossie={setDossie} />

            <ExplicacaoCalculos />
          </>
        )}

        {/* DOSSIÊ (modal com os 4 gráficos do item) */}
        {dossie && <DossieModal item={dossie} visao={visao} mesesSel={mesesSel} fechar={() => setDossie(null)} />}
      </div>
    </div>
  );
}

// ============================================================
// GRÁFICO DE LINHA (IA vs Humano)
// ============================================================
function GraficoLinha({ titulo, subtitulo, serie, chaves, ajuda, zero, compacto }: any) {
  return (
    <div className={compacto ? '' : 'bg-white rounded-[28px] shadow-sm border border-slate-100 p-6'}>
      <h3 className={`font-black text-slate-800 tracking-tight ${compacto ? 'text-xs' : 'text-sm'}`}>{titulo}{ajuda && <Explica titulo={titulo} texto={ajuda} />}</h3>
      {subtitulo && <p className="text-xs text-slate-400 font-medium mb-4">{subtitulo}</p>}
      <div className={compacto ? 'h-44' : 'h-64'}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={serie} margin={{ top: 10, right: 20, left: -10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
            <XAxis dataKey="mes" tick={{ fill: '#64748b', fontSize: 11, fontWeight: 700 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} axisLine={false} tickLine={false} unit="%" />
            <Tooltip contentStyle={{ borderRadius: 12, fontSize: 12, border: '1px solid #e2e8f0' }} formatter={(v: any) => `${v}%`}
              labelFormatter={(l: any) => { const p = serie.find((s: any) => s.mes === l); return p?.parcial ? `${l} (parcial)` : l; }} />
            <Legend wrapperStyle={{ fontSize: 11, fontWeight: 700, paddingTop: 6 }} iconType="circle" />
            {zero && <ReferenceLine y={0} stroke="#cbd5e1" strokeWidth={1.5} />}
            {chaves.map((c: any) => (
              <Line key={c.k} dataKey={c.k} name={c.nome} stroke={c.cor} strokeWidth={c.k.includes('ia') ? 2 : 3}
                strokeDasharray={c.k.includes('ia') ? '5 5' : undefined}
                dot={<PontoParcial stroke={c.cor} />} activeDot={{ r: 5 }} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ============================================================
// TABELA DRILL — Categoria expansível -> SKUs
// ============================================================
function TabelaDrill({ params, visao, abrirDossie }: any) {
  const [dados, setDados] = useState<any[]>([]);
  const [carregando, setCarregando] = useState(false);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());

  useEffect(() => {
    setCarregando(true);
    axios.get(`/api/v1/kpis/tabela?${params.toString()}`)
      .then(r => setDados(r.data.categorias || []))
      .catch(() => setDados([]))
      .finally(() => setCarregando(false));
  }, [params]);

  const toggle = (cat: string) => {
    setAbertas(prev => {
      const n = new Set(prev);
      n.has(cat) ? n.delete(cat) : n.add(cat);
      return n;
    });
  };

  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 mb-6 overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-100">
        <h3 className="text-sm font-black text-slate-800 tracking-tight flex items-center gap-2">
          <Filter className="w-4 h-4 text-indigo-600" /> Os números que comprovam
          <Explica titulo="Tabela de comprovação" texto="Volumes lado a lado — realizado, previsto pelo humano, previsto pela IA — por categoria e SKU, no período selecionado. Clique na categoria para abrir os SKUs; o botão de gráfico abre o dossiê do item." />
        </h3>
      </div>

      {/* Cabeçalho */}
      <div className="grid grid-cols-12 gap-2 px-6 py-2.5 bg-slate-50/60 text-[10px] font-black text-slate-400 uppercase tracking-widest">
        <div className="col-span-4">Categoria / SKU</div>
        <div className="col-span-2 text-right">Realizado</div>
        <div className="col-span-2 text-right">Prev. Humano</div>
        <div className="col-span-2 text-right">Prev. IA</div>
        <div className="col-span-1 text-center">Acurácia</div>
        <div className="col-span-1 text-center">Dossiê</div>
      </div>

      {carregando ? (
        <div className="p-10 flex items-center justify-center text-slate-400"><Loader2 className="w-5 h-5 animate-spin mr-2" /> montando a tabela...</div>
      ) : dados.length === 0 ? (
        <div className="p-10 text-center text-slate-400 text-sm font-medium">Sem dados no período.</div>
      ) : (
        <div className="divide-y divide-slate-50">
          {dados.map((cat: any) => {
            const aberta = abertas.has(cat.nome);
            return (
              <div key={cat.nome}>
                {/* LINHA DA CATEGORIA */}
                <div className="grid grid-cols-12 gap-2 px-6 py-3 items-center hover:bg-slate-50/50 cursor-pointer transition-colors" onClick={() => toggle(cat.nome)}>
                  <div className="col-span-4 flex items-center gap-2 min-w-0">
                    {aberta ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                    <span className="font-black text-slate-800 truncate">{cat.nome}</span>
                    <span className="text-[9px] font-black text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded-md shrink-0">{cat.skus.length} SKU</span>
                  </div>
                  <div className="col-span-2 text-right font-black text-slate-700">{fmtNum(cat.realizado, visao)}</div>
                  <div className="col-span-2 text-right font-bold text-indigo-600">{fmtNum(cat.previsto_final, visao)}</div>
                  <div className="col-span-2 text-right font-bold text-slate-400">{fmtNum(cat.previsto_ia, visao)}</div>
                  <div className={`col-span-1 text-center font-black ${corAcuracia(cat.acuracia)}`}>{fmtPct(cat.acuracia * 100)}</div>
                  <div className="col-span-1 text-center">
                    <button onClick={(e) => { e.stopPropagation(); abrirDossie({ tipo: 'categoria', nome: cat.nome }); }}
                      className="p-1.5 rounded-lg bg-indigo-50 text-indigo-600 hover:bg-indigo-100 transition-colors">
                      <LineChartIcon className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* SKUs DA CATEGORIA */}
                {aberta && cat.skus.map((s: any) => (
                  <div key={s.nome} className="grid grid-cols-12 gap-2 px-6 py-2 items-center bg-slate-50/40 border-t border-slate-50">
                    <div className="col-span-4 min-w-0 pl-7">
                      <div className="font-bold text-slate-700 text-xs truncate">{s.descricao || s.nome}</div>
                      <div className="text-[10px] font-bold text-slate-400">{s.nome}</div>
                    </div>
                    <div className="col-span-2 text-right font-bold text-slate-600 text-xs">{fmtNum(s.realizado, visao)}</div>
                    <div className="col-span-2 text-right font-bold text-indigo-500 text-xs">{fmtNum(s.previsto_final, visao)}</div>
                    <div className="col-span-2 text-right font-bold text-slate-400 text-xs">{fmtNum(s.previsto_ia, visao)}</div>
                    <div className={`col-span-1 text-center font-black text-xs ${corAcuracia(s.acuracia)}`}>{fmtPct(s.acuracia * 100)}</div>
                    <div className="col-span-1 text-center">
                      <button onClick={() => abrirDossie({ tipo: 'sku', nome: s.nome, descricao: s.descricao })}
                        className="p-1.5 rounded-lg bg-slate-100 text-slate-500 hover:bg-indigo-50 hover:text-indigo-600 transition-colors">
                        <LineChartIcon className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ============================================================
// DOSSIÊ — modal com os 4 gráficos do item (categoria ou SKU)
// ============================================================
function DossieModal({ item, visao, mesesSel, fechar }: any) {
  const [serie, setSerie] = useState<any[]>([]);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    const p = new URLSearchParams();
    p.append('visao', visao);
    mesesSel.forEach((m: string) => p.append('meses', m));
    p.append(item.tipo === 'categoria' ? 'categoria' : 'sku', item.nome);
    setCarregando(true);
    axios.get(`/api/v1/kpis/evolucao?${p.toString()}`)
      .then(r => setSerie(r.data.serie || []))
      .catch(() => setSerie([]))
      .finally(() => setCarregando(false));
  }, [item, visao, mesesSel]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm" onClick={fechar} />
      <div className="relative bg-white rounded-[28px] shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-auto">
        <div className="sticky top-0 bg-white border-b border-slate-100 px-6 py-4 flex items-center justify-between rounded-t-[28px]">
          <div>
            <div className="text-[10px] font-black text-indigo-500 uppercase tracking-widest">Dossiê · {item.tipo === 'categoria' ? 'Categoria' : 'SKU'}</div>
            <h3 className="text-lg font-black text-slate-900 tracking-tight">{item.descricao || item.nome}</h3>
            {item.descricao && <div className="text-[11px] font-bold text-slate-400">{item.nome}</div>}
          </div>
          <button onClick={fechar} className="p-2 rounded-xl bg-slate-100 text-slate-500 hover:bg-slate-200"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-6">
          {carregando ? (
            <div className="p-10 flex items-center justify-center text-slate-400"><Loader2 className="w-5 h-5 animate-spin mr-2" /> montando o dossiê...</div>
          ) : serie.length === 0 ? (
            <div className="p-10 text-center text-slate-400 text-sm font-medium">Sem dados planejados para este item no período.</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <GraficoLinha compacto titulo="WMAPE — erro do plano" serie={serie}
                chaves={[{ k: 'wmape', nome: 'Humano', cor: '#6366f1' }, { k: 'wmape_ia', nome: 'Nexus Bot', cor: '#94a3b8' }]} />
              <GraficoLinha compacto titulo="Acurácia" serie={serie}
                chaves={[{ k: 'acuracia', nome: 'Humano', cor: '#059669' }, { k: 'acuracia_ia', nome: 'Nexus Bot', cor: '#94a3b8' }]} />
              <GraficoLinha compacto titulo="BIAS — tendência" serie={serie} zero
                chaves={[{ k: 'bias', nome: 'Humano', cor: '#d97706' }, { k: 'bias_ia', nome: 'Nexus Bot', cor: '#94a3b8' }]} />
              <GraficoLinha compacto titulo="FVA — humano vs IA" serie={serie} zero
                chaves={[{ k: 'fva', nome: 'FVA', cor: '#7c3aed' }]} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SELETOR DE MESES (ano -> meses, seleção múltipla)
// ============================================================
function SeletorMeses({ mesesDisp, mesesSel, setMesesSel }: any) {
  const [aberto, setAberto] = useState(false);
  const porAno = useMemo(() => {
    const g: Record<string, any[]> = {};
    mesesDisp.forEach((m: any) => { (g[m.ano] = g[m.ano] || []).push(m); });
    return g;
  }, [mesesDisp]);
  const toggle = (mes: string) => setMesesSel(mesesSel.includes(mes) ? mesesSel.filter((x: string) => x !== mes) : [...mesesSel, mes]);
  const toggleAno = (ano: string) => {
    const doAno = porAno[ano].map((m: any) => m.mes);
    const todosSel = doAno.every((m: string) => mesesSel.includes(m));
    setMesesSel(todosSel ? mesesSel.filter((m: string) => !doAno.includes(m)) : [...new Set([...mesesSel, ...doAno])]);
  };
  return (
    <div className="relative">
      <button onClick={() => setAberto(!aberto)} className="flex items-center gap-1.5 px-4 py-2.5 rounded-2xl text-xs font-black bg-white text-slate-600 shadow-sm border border-slate-100 hover:bg-slate-50">
        {mesesSel.length === 0 ? 'Selecionar meses' : `${mesesSel.length} mês(es)`} <ChevronDown className="w-3.5 h-3.5" />
      </button>
      {aberto && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setAberto(false)} />
          <div className="absolute right-0 z-50 mt-2 w-56 bg-white rounded-2xl shadow-xl border border-slate-100 p-3 max-h-80 overflow-auto">
            {Object.keys(porAno).sort().map(ano => (
              <div key={ano} className="mb-2">
                <button onClick={() => toggleAno(ano)} className="w-full text-left text-[11px] font-black text-slate-700 uppercase tracking-widest py-1 hover:text-indigo-600">{ano}</button>
                <div className="grid grid-cols-3 gap-1 mt-1">
                  {porAno[ano].sort((a: any, b: any) => a.num.localeCompare(b.num)).map((m: any) => (
                    <button key={m.mes} onClick={() => toggle(m.mes)}
                      className={`text-[11px] font-bold py-1 rounded-lg relative ${mesesSel.includes(m.mes) ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-50 text-slate-400'}`}>
                      {m.num}{m.parcial && <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-400 rounded-full" />}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ============================================================
// AUXILIARES
// ============================================================
function Explica({ titulo, texto }: any) {
  return (
    <span className="group relative inline-flex items-center ml-1 align-middle">
      <HelpCircle className="w-3.5 h-3.5 text-slate-300 hover:text-slate-500 cursor-help" />
      <span className="invisible group-hover:visible absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-3 bg-slate-900 text-white text-[11px] leading-relaxed rounded-xl shadow-xl font-medium">
        <span className="block font-black mb-1 text-indigo-300">{titulo}</span>{texto}
      </span>
    </span>
  );
}

function CardResumo({ titulo, valor, sub, icone, tom, ajuda }: any) {
  const tomCor = tom === 'bom' ? 'text-emerald-600' : tom === 'ruim' ? 'text-rose-600' : tom === 'medio' ? 'text-amber-600' : 'text-slate-700';
  const tomBg = tom === 'bom' ? 'bg-emerald-50' : tom === 'ruim' ? 'bg-rose-50' : tom === 'medio' ? 'bg-amber-50' : 'bg-slate-100';
  return (
    <div className="bg-white rounded-[24px] shadow-sm border border-slate-100 p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{titulo}<Explica titulo={titulo} texto={ajuda} /></span>
        <div className={`p-2 rounded-xl ${tomBg} ${tomCor}`}>{icone}</div>
      </div>
      <div className={`text-2xl font-black tracking-tight ${tomCor}`}>{valor}</div>
      <div className="text-[11px] font-bold text-slate-400 mt-1">{sub}</div>
    </div>
  );
}

function ExplicacaoCalculos() {
  const [aberto, setAberto] = useState(false);
  const metricas = [
    { n: 'WMAPE', f: '|Σprev − Σreal| ÷ Σreal', t: 'Erro percentual sobre os totais do recorte. Método agregado: soma tudo primeiro, depois a razão. É o erro de dimensionamento — quão perto o plano ficou do que se realizou dele.' },
    { n: 'Acurácia', f: '1 − WMAPE', t: 'O complemento do erro. De cada 100 caixas do escopo planejado, quantas o plano acertou no total. É a nota-base do desempenho.' },
    { n: 'BIAS', f: '(Σprev − Σreal) ÷ Σreal', t: 'A tendência sistemática. Positivo: prevê demais (infla estoque, trava capital). Negativo: prevê de menos (rupturas). Perto de zero é saudável.' },
    { n: 'FVA', f: 'WMAPE da IA − WMAPE do humano', t: 'Forecast Value Added. Mede se o ajuste humano melhorou a previsão pura da máquina (Nexus Bot). Positivo: o humano agregou valor. Negativo: a IA sozinha teria acertado mais.' },
  ];
  return (
    <div className="bg-white rounded-[28px] shadow-sm border border-slate-100 overflow-hidden">
      <button onClick={() => setAberto(!aberto)} className="w-full flex items-center justify-between px-6 py-4 hover:bg-slate-50/50">
        <span className="text-sm font-black text-slate-700 flex items-center gap-2"><HelpCircle className="w-4 h-4 text-indigo-600" /> Como os indicadores são calculados</span>
        {aberto ? <ChevronDown className="w-5 h-5 text-slate-400" /> : <ChevronRight className="w-5 h-5 text-slate-400" />}
      </button>
      {aberto && (
        <div className="px-6 pb-6 grid grid-cols-1 md:grid-cols-2 gap-3">
          {metricas.map(m => (
            <div key={m.n} className="bg-slate-50 rounded-2xl p-4 border border-slate-100">
              <div className="flex items-baseline gap-2 mb-1 flex-wrap">
                <span className="font-black text-slate-800">{m.n}</span>
                <code className="text-[11px] font-mono text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">{m.f}</code>
              </div>
              <p className="text-xs text-slate-500 font-medium leading-relaxed">{m.t}</p>
            </div>
          ))}
          <div className="bg-indigo-50 rounded-2xl p-4 border border-indigo-100 md:col-span-2">
            <p className="text-xs text-indigo-900 font-medium leading-relaxed">
              <span className="font-black">O escopo é o plano (fato IBP):</span> a auditoria compara o que foi planejado com o que se realizou daquele plano. Vendas que ocorreram sem nenhum planejamento ficam fora destes indicadores — elas permanecem registradas na base de vendas, mas não entram na conta da acurácia.
              <span className="block mt-1"><span className="font-black">Regra temporal:</span> cada mês é comparado com o plano congelado 2 meses antes (o mês 07 usa o ciclo 05). O mês corrente aparece como parcial até fechar.</span>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}