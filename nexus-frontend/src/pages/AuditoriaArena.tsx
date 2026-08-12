import { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend
} from 'recharts';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';

const COR = { humano: '#2563eb', ia: '#10b981', ref: '#f59e0b' };
const NOMES_MES = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const labelMes = (m: string) => {
  const [a, mm] = m.split('-');
  return `${NOMES_MES[parseInt(mm) - 1]}/${a.slice(2)}`;
};
const pct = (v: any, dec = 1) =>
  v == null ? '—' : `${Number(v).toFixed(dec).replace('.', ',')}%`;
const corWmape = (v: number | null) =>
  v == null ? '#94a3b8' : v <= 20 ? '#059669' : v <= 35 ? '#d97706' : '#e11d48';

// ─── Tooltip ─────────────────────────────────────────────────────────────────
const TT = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 10, padding: '10px 14px', fontSize: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{labelMes(label)}</div>
      {payload.map((p: any) => p.value != null && (
        <div key={p.name} style={{ color: p.color, display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
          <span>{p.name}</span>
          <span style={{ fontWeight: 700 }}>{pct(p.value)}</span>
        </div>
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// SELETOR DE DATAS EM ÁRVORE
// ═══════════════════════════════════════════════════════════════════════════
function SeletorDatas({ calendario, mesesSel, setMesesSel }: any) {
  const [abertos, setAbertos] = useState<Set<string>>(new Set(['2026']));
  const [open, setOpen] = useState(false);

  const toggleAno = (ano: string) =>
    setAbertos(p => { const n = new Set(p); n.has(ano) ? n.delete(ano) : n.add(ano); return n; });

  const toggleMes = (m: string) =>
    setMesesSel((p: string[]) => p.includes(m) ? p.filter(x => x !== m) : [...p, m]);

  const toggleAnoTudo = (ano: string, meses: string[]) => {
    const todos = meses.map(m => `${ano}-${m}`);
    const temTodos = todos.every(m => mesesSel.includes(m));
    setMesesSel((p: string[]) =>
      temTodos ? p.filter(m => !todos.includes(m)) : [...new Set([...p, ...todos])]);
  };

  const anos = Object.keys(calendario).sort((a, b) => Number(b) - Number(a));

  return (
    <div style={{ position: 'relative' }}>
      <button onClick={() => setOpen(o => !o)} style={{
        display: 'flex', alignItems: 'center', gap: 8, border: '1px solid #d1d5db',
        borderRadius: 10, padding: '8px 14px', background: '#fff', cursor: 'pointer',
        fontSize: 13, fontWeight: 600, color: '#374151',
      }}>
        📅 {mesesSel.length === 0 ? 'Período' : `${mesesSel.length} mês${mesesSel.length > 1 ? 'es' : ''}`}
        <ChevronDown style={{ width: 14, color: '#9ca3af' }} />
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 46, left: 0, zIndex: 200,
          background: '#fff', border: '1px solid #e2e8f0', borderRadius: 12,
          boxShadow: '0 8px 24px rgba(0,0,0,.12)', padding: 12,
          minWidth: 280, maxHeight: 420, overflowY: 'auto',
        }}>
          <div style={{ display: 'flex', gap: 12, marginBottom: 8, paddingBottom: 8, borderBottom: '1px solid #f1f5f9' }}>
            <button onClick={() => setMesesSel(
              Object.entries(calendario).flatMap(([a, ms]: any) => ms.map((m: string) => `${a}-${m}`))
            )} style={{ fontSize: 11, color: '#2563eb', fontWeight: 700, background: 'none', border: 'none', cursor: 'pointer' }}>
              Todos
            </button>
            <button onClick={() => setMesesSel([])}
              style={{ fontSize: 11, color: '#64748b', background: 'none', border: 'none', cursor: 'pointer' }}>
              Limpar
            </button>
            <button onClick={() => {
              const todos = Object.entries(calendario)
                .flatMap(([a, ms]: any) => ms.map((m: string) => `${a}-${m}`));
              setMesesSel(todos.slice(-12));
            }} style={{ fontSize: 11, color: '#7c3aed', fontWeight: 700, background: 'none', border: 'none', cursor: 'pointer' }}>
              Últimos 12
            </button>
          </div>

          {anos.map(ano => {
            const meses: string[] = calendario[ano];
            const todos = meses.map(m => `${ano}-${m}`);
            const qtd = todos.filter(m => mesesSel.includes(m)).length;
            const ab = abertos.has(ano);

            return (
              <div key={ano} style={{ marginBottom: 2 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 4px',
                  cursor: 'pointer', borderRadius: 8,
                  background: ab ? '#f8fafc' : 'transparent' }}>
                  <button onClick={() => toggleAno(ano)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: '#64748b' }}>
                    {ab ? <ChevronDown style={{ width: 13 }} /> : <ChevronRight style={{ width: 13 }} />}
                  </button>
                  <input type="checkbox" checked={qtd === todos.length}
                    ref={el => { if (el) el.indeterminate = qtd > 0 && qtd < todos.length; }}
                    onChange={() => toggleAnoTudo(ano, meses)}
                    style={{ accentColor: '#2563eb' }} />
                  <span style={{ fontWeight: 700, fontSize: 13, color: '#0f172a' }}>{ano}</span>
                  <span style={{ fontSize: 10, color: '#94a3b8', marginLeft: 'auto' }}>
                    {qtd}/{todos.length}
                  </span>
                </div>

                {ab && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
                    gap: 4, padding: '4px 4px 8px 28px' }}>
                    {meses.map(m => {
                      const chave = `${ano}-${m}`;
                      const sel = mesesSel.includes(chave);
                      return (
                        <label key={m} style={{
                          display: 'flex', alignItems: 'center', gap: 3,
                          fontSize: 11, cursor: 'pointer',
                          color: sel ? '#2563eb' : '#374151', fontWeight: sel ? 700 : 400,
                          padding: '2px 4px', borderRadius: 5,
                          background: sel ? '#eff6ff' : 'transparent',
                        }}>
                          <input type="checkbox" checked={sel} onChange={() => toggleMes(chave)}
                            style={{ accentColor: '#2563eb', width: 11, height: 11 }} />
                          {NOMES_MES[parseInt(m) - 1]}
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// GRÁFICO DE LINHA
// ═══════════════════════════════════════════════════════════════════════════
function Grafico({ dados, chaveH, chaveIA, titulo, descricao, refZero = false, refBands }: any) {
  const temIA = dados.some((d: any) => d[chaveIA] != null);
  return (
    <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: '16px 18px' }}>
      <div style={{ marginBottom: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: '#0f172a' }}>{titulo}</span>
        <span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 8 }}>{descricao}</span>
      </div>
      <ResponsiveContainer width="100%" height={195}>
        <LineChart data={dados} margin={{ top: 4, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis dataKey="mes" tick={{ fontSize: 10, fill: '#94a3b8' }}
            tickFormatter={labelMes} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `${v}%`} />
          <Tooltip content={<TT />} />
          {refZero && <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={1.5} />}
          {(refBands || []).map(({ y, cor, label: lb }: any) => (
            <ReferenceLine key={y} y={y} stroke={cor || COR.ref} strokeDasharray="4 2"
              label={{ value: lb || `${y}%`, fill: cor || COR.ref, fontSize: 9, position: 'right' }} />
          ))}
          {chaveH && (
            <Line dataKey={chaveH} name="Humano" stroke={COR.humano} strokeWidth={2.5}
              dot={{ r: 2.5, fill: COR.humano }} activeDot={{ r: 5 }} connectNulls={false} />
          )}
          {temIA && chaveIA && (
            <Line dataKey={chaveIA} name="IA (Nexus)" stroke={COR.ia} strokeWidth={2.5}
              dot={{ r: 5, fill: COR.ia, strokeWidth: 2, stroke: '#fff' }}
              activeDot={{ r: 6 }} connectNulls={false} />
          )}
          <Legend iconType="line" wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// CARD NUMÉRICO
// ═══════════════════════════════════════════════════════════════════════════
function Card({ label, valor, cor, sub }: any) {
  return (
    <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: '14px 18px', flex: 1, minWidth: 140 }}>
      <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.06em', color: '#94a3b8', marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 900, color: cor || '#4f46e5', lineHeight: 1 }}>
        {valor == null ? '—' : pct(valor)}
      </div>
      {sub && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// TELA
// ═══════════════════════════════════════════════════════════════════════════
export default function AuditoriaArena() {
  const [opts, setOpts]           = useState<any>({ categorias: [], skus: [], calendario: {} });
  const [mesesSel, setMesesSel]   = useState<string[]>([]);
  const [categoria, setCategoria] = useState('');
  const [sku, setSku]             = useState('');
  const [dados, setDados]         = useState<any>(null);
  const [loading, setLoading]     = useState(false);
  const [erro, setErro]           = useState('');

  useEffect(() => {
    axios.get('/api/v1/kpis/filtros').then(r => {
      setOpts(r.data);
      const cal: Record<string, string[]> = r.data.calendario || {};
      const todos = Object.entries(cal)
        .sort(([a], [b]) => Number(a) - Number(b))
        .flatMap(([ano, ms]) => (ms as string[]).map(m => `${ano}-${m}`));
      setMesesSel(todos.slice(-12));
    }).catch(() => {});
  }, []);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    mesesSel.forEach(m => p.append('meses', m));
    if (categoria) p.set('categoria', categoria);
    if (sku) p.set('sku', sku);
    return p.toString();
  }, [mesesSel, categoria, sku]);

  const carregar = useCallback(async () => {
    if (!mesesSel.length) { setDados(null); return; }
    setLoading(true); setErro('');
    try {
      const r = await axios.get(`/api/v1/kpis/evolucao?${qs}`);
      setDados(r.data);
    } catch (e: any) {
      setErro(e?.response?.data?.detail || 'Erro ao carregar.');
      setDados(null);
    } finally { setLoading(false); }
  }, [qs]);

  useEffect(() => { carregar(); }, [carregar]);

  const serie  = dados?.serie  || [];
  const resumo = dados?.resumo || {};
  const temIA  = serie.some((d: any) => d.wmape_ia != null);
  const skusFilt = opts.skus.filter((s: any) => !categoria || s.categoria === categoria);

  const ctx = sku ? `SKU ${sku}` : categoria ? `Categoria: ${categoria}` : 'Portfólio completo';

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', padding: '24px 28px' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto' }}>

        {/* CABEÇALHO */}
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ fontSize: 20, fontWeight: 900, color: '#0f172a', margin: 0 }}>
            Acurácia do S&OP
          </h1>
          <p style={{ fontSize: 12, color: '#64748b', margin: '4px 0 0' }}>
            Previsão vs realizado em caixas · Meses fechados · {ctx}
          </p>
        </div>

        {/* FILTROS */}
        <div style={{
          background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9',
          padding: '14px 18px', marginBottom: 18,
          display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end',
        }}>
          <SeletorDatas calendario={opts.calendario} mesesSel={mesesSel} setMesesSel={setMesesSel} />

          {[
            { label: 'Categoria', val: categoria, set: (v: string) => { setCategoria(v); setSku(''); },
              opts: opts.categorias.map((c: string) => ({ v: c, l: c })) },
          ].map(({ label, val, set, opts: op }) => (
            <label key={label} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.05em', color: '#94a3b8' }}>
                {label}
              </span>
              <select value={val} onChange={e => set(e.target.value)}
                style={{ border: '1px solid #d1d5db', borderRadius: 10, padding: '8px 12px', fontSize: 13, background: '#fff', minWidth: 150 }}>
                <option value="">Todas</option>
                {op.map(({ v, l }: any) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          ))}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.05em', color: '#94a3b8' }}>
              SKU
            </span>
            <select value={sku} onChange={e => setSku(e.target.value)}
              style={{ border: '1px solid #d1d5db', borderRadius: 10, padding: '8px 12px', fontSize: 13, background: '#fff', minWidth: 260 }}>
              <option value="">Todos</option>
              {skusFilt.map((s: any) => (
                <option key={s.sku} value={s.sku}>{s.sku} — {s.descricao}</option>
              ))}
            </select>
          </label>

          {loading && <Loader2 style={{ width: 16, color: '#94a3b8', alignSelf: 'center' }} className="animate-spin" />}
          {erro && <span style={{ color: '#e11d48', fontSize: 12, alignSelf: 'center' }}>{erro}</span>}
        </div>

        {/* CARDS */}
        {resumo.wmape_h != null && (
          <div style={{ display: 'flex', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
            <Card label="WMAPE Humano" valor={resumo.wmape_h} cor={corWmape(resumo.wmape_h)}
              sub={`Acurácia ${pct(100 - resumo.wmape_h)}`} />
            <Card label="MAPE Humano" valor={resumo.mape_h} cor={corWmape(resumo.mape_h)}
              sub="Média por SKU" />
            <Card label="Bias Humano" valor={resumo.bias_h}
              cor={Math.abs(resumo.bias_h || 0) < 5 ? '#059669' : (resumo.bias_h || 0) > 0 ? '#e11d48' : '#2563eb'}
              sub={(resumo.bias_h || 0) > 5 ? '▲ Superestimando' : (resumo.bias_h || 0) < -5 ? '▼ Subestimando' : '✓ Equilibrado'} />
            {temIA && (
              <>
                <Card label="WMAPE IA" valor={resumo.wmape_ia} cor={corWmape(resumo.wmape_ia)}
                  sub={`${resumo.meses_com_ia || 0} meses`} />
                <Card label="FVA" valor={resumo.fva}
                  cor={(resumo.fva || 0) < 0 ? '#059669' : (resumo.fva || 0) > 0 ? '#e11d48' : '#94a3b8'}
                  sub={(resumo.fva || 0) < 0 ? 'IA mais precisa' : (resumo.fva || 0) > 0 ? 'Humano mais preciso' : '—'} />
              </>
            )}
            <div style={{
              background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: 14,
              padding: '14px 18px', flex: 1, minWidth: 160,
            }}>
              <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.06em', color: '#0284c7', marginBottom: 6 }}>
                Período
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#0f172a' }}>
                {mesesSel.length} meses
              </div>
              {mesesSel.length > 0 && (
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 3 }}>
                  {labelMes(mesesSel[0])} → {labelMes(mesesSel[mesesSel.length - 1])}
                </div>
              )}
              {!temIA && (
                <div style={{ fontSize: 10, color: '#d97706', marginTop: 6 }}>
                  IA disponível a partir de Jun/26
                </div>
              )}
            </div>
          </div>
        )}

        {/* GRÁFICOS */}
        {serie.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Grafico dados={serie} chaveH="wmape_h" chaveIA="wmape_ia"
              titulo="WMAPE (%)" descricao="Ponderado por volume — quanto do volume total foi mal previsto"
              refBands={[{ y: 20, cor: '#10b981', label: 'Meta 20%' }, { y: 35, cor: '#e11d48', label: 'Crítico 35%' }]} />

            <Grafico dados={serie} chaveH="mape_h" chaveIA="mape_ia"
              titulo="MAPE (%)" descricao="Média por SKU — cada produto com peso igual, exclui real = 0"
              refBands={[{ y: 20, cor: '#10b981', label: '20%' }]} />

            <Grafico dados={serie} chaveH="bias_h" chaveIA="bias_ia"
              titulo="BIAS (%)" descricao="Positivo = plano acima do vendido · Negativo = plano abaixo"
              refZero
              refBands={[{ y: 10, cor: '#d97706', label: '+10%' }, { y: -10, cor: '#2563eb', label: '-10%' }]} />

            {temIA && (
              <Grafico dados={serie} chaveH={undefined} chaveIA="fva"
                titulo="FVA — Forecast Value Added (%)"
                descricao="Negativo = IA foi mais precisa que o humano · Positivo = humano foi mais preciso"
                refZero />
            )}

            <div style={{ fontSize: 11, color: '#94a3b8', lineHeight: 1.8, padding: '2px 4px' }}>
              <b>WMAPE</b> = SUM(|previsão−real|) / SUM(real): ponderado por volume, o indicador principal para portfólio.&nbsp;
              <b>MAPE</b> = Média de |previsão−real| / real por SKU: cada item com peso igual, mostra spread de erros, exclui meses com real = 0.&nbsp;
              <b>BIAS</b> = (SUM(previsão) − SUM(real)) / SUM(real): direção sistemática, ±10% é a zona de atenção.&nbsp;
              <b>FVA</b> = WMAPE_IA − WMAPE_Humano: disponível somente para ciclos Nexus (M+2 congelado, a partir de jun/26).&nbsp;
              Meta humana jan/23–mai/26 via arquivo histórico, jun/26+ via vol_final do ciclo M+2.&nbsp;
              Portfólio ativo, a partir da primeira venda de cada SKU.
            </div>
          </div>
        ) : !loading && (
          <div style={{
            background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9',
            padding: 48, textAlign: 'center', color: '#94a3b8', fontSize: 13,
          }}>
            {mesesSel.length === 0
              ? 'Selecione pelo menos um mês no seletor de período.'
              : 'Nenhum dado encontrado para os filtros selecionados.'}
          </div>
        )}
      </div>
    </div>
  );
}