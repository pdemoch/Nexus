import { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend, Label,
} from 'recharts';
import { ChevronDown, ChevronRight, Loader2, TrendingUp, TrendingDown, AlertTriangle,
         Sparkles, FileDown, Send, RefreshCw } from 'lucide-react';

// ─── Paleta ───────────────────────────────────────────────────────────────────
const COR = { humano: '#2563eb', ia: '#10b981', over: '#e11d48', under: '#2563eb', neutro: '#94a3b8' };

// ─── Formatadores ─────────────────────────────────────────────────────────────
const NOMES_MES = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const lMes  = (m: string) => { const [a,mm] = m.split('-'); return `${NOMES_MES[+mm-1]}/${a.slice(2)}`; };
const pct   = (v: any, d = 1) => v == null ? '—' : `${Number(v).toFixed(d).replace('.',',')}%`;
const sinal  = (v: any) => { if (v==null) return '—'; const n=Number(v); return `${n>0?'+':''}${n.toFixed(1).replace('.',',')}%`; };
const num   = (v: any)  => v == null ? '—' : Math.round(Number(v)).toLocaleString('pt-BR');
const corWmape = (v: number|null) => v==null?COR.neutro:v<=20?'#059669':v<=35?'#d97706':'#e11d48';
const corBias  = (v: number|null) => {
  if (v==null) return COR.neutro;
  if (v >  10) return COR.over;
  if (v < -10) return COR.under;
  return '#059669';
};

// ─── Tooltip customizado ──────────────────────────────────────────────────────
const TT = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background:'#fff', border:'1px solid #e2e8f0', borderRadius:10, padding:'10px 14px', fontSize:12, boxShadow:'0 4px 12px rgba(0,0,0,.08)' }}>
      <div style={{ fontWeight:700, marginBottom:6, color:'#0f172a' }}>{lMes(label)}</div>
      {payload.map((p: any) => p.value != null && (
        <div key={p.name} style={{ color:p.color, display:'flex', justifyContent:'space-between', gap:16, marginBottom:2 }}>
          <span>{p.name}</span>
          <span style={{ fontWeight:700 }}>{pct(p.value)}</span>
        </div>
      ))}
    </div>
  );
};

// ─── Rótulo de dado na linha ─────────────────────────────────────────────────
const RoituloLinha = (cor: string, total: number) => (props: any) => {
  const { x, y, value } = props;
  if (value == null || total > 20) return null; // omite se muitos pontos
  const salto = total > 12 ? 2 : 1;
  if (props.index % salto !== 0) return null;
  return (
    <text x={x} y={y - 9} fill={cor} fontSize={9} textAnchor="middle" fontWeight={700}>
      {Number(value).toFixed(1).replace('.',',')}
    </text>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// SELETOR DE DATAS EM ÁRVORE
// ═══════════════════════════════════════════════════════════════════════════
function SeletorDatas({ calendario, mesesSel, setMesesSel }: any) {
  const [abertos, setAbertos] = useState<Set<string>>(new Set(['2026']));
  const [open, setOpen] = useState(false);

  const toggleAno = (ano: string) =>
    setAbertos(p => { const n = new Set(p); n.has(ano)?n.delete(ano):n.add(ano); return n; });

  const toggleMes = (m: string) =>
    setMesesSel((p: string[]) => p.includes(m) ? p.filter(x=>x!==m) : [...p, m]);

  const toggleAnoTudo = (ano: string, meses: string[]) => {
    const todos = meses.map(m => `${ano}-${m}`);
    const tudo  = todos.every(m => mesesSel.includes(m));
    setMesesSel((p: string[]) => tudo ? p.filter(m=>!todos.includes(m)) : [...new Set([...p,...todos])]);
  };

  const todos = Object.entries(calendario as Record<string,string[]>)
    .sort(([a],[b])=>+a - +b).flatMap(([ano,ms])=>ms.map(m=>`${ano}-${m}`));

  return (
    <div style={{ position:'relative' }}>
      <button onClick={()=>setOpen(o=>!o)} style={{
        display:'flex', alignItems:'center', gap:8, border:'1px solid #d1d5db',
        borderRadius:10, padding:'8px 14px', background:'#fff', cursor:'pointer',
        fontSize:13, fontWeight:600, color:'#374151',
      }}>
        📅 {mesesSel.length===0?'Período':`${mesesSel.length} mes${mesesSel.length>1?'es':''}`}
        <ChevronDown style={{ width:14, color:'#9ca3af' }} />
      </button>

      {open && (
        <div style={{
          position:'absolute', top:46, left:0, zIndex:200, background:'#fff',
          border:'1px solid #e2e8f0', borderRadius:12, boxShadow:'0 8px 24px rgba(0,0,0,.12)',
          padding:12, minWidth:290, maxHeight:430, overflowY:'auto',
        }}>
          <div style={{ display:'flex', gap:10, marginBottom:8, paddingBottom:8, borderBottom:'1px solid #f1f5f9', fontSize:11 }}>
            <button onClick={()=>setMesesSel(todos)} style={{ color:'#2563eb', fontWeight:700, background:'none', border:'none', cursor:'pointer' }}>Todos</button>
            <button onClick={()=>setMesesSel([])}  style={{ color:'#64748b', background:'none', border:'none', cursor:'pointer' }}>Limpar</button>
            <button onClick={()=>setMesesSel(todos.slice(-12))} style={{ color:'#7c3aed', fontWeight:700, background:'none', border:'none', cursor:'pointer' }}>Últ. 12</button>
            <button onClick={()=>setMesesSel(todos.slice(-6))}  style={{ color:'#7c3aed', background:'none', border:'none', cursor:'pointer' }}>Últ. 6</button>
          </div>

          {Object.keys(calendario as Record<string,string[]>).sort((a,b)=>+b - +a).map(ano => {
            const meses: string[] = (calendario as any)[ano];
            const chaves = meses.map(m=>`${ano}-${m}`);
            const qtd    = chaves.filter(m=>mesesSel.includes(m)).length;
            const ab     = abertos.has(ano);
            return (
              <div key={ano}>
                <div style={{ display:'flex', alignItems:'center', gap:6, padding:'5px 4px', cursor:'pointer', borderRadius:8 }}>
                  <button onClick={()=>toggleAno(ano)} style={{ background:'none', border:'none', cursor:'pointer', padding:0, color:'#64748b' }}>
                    {ab?<ChevronDown style={{width:13}}/>:<ChevronRight style={{width:13}}/>}
                  </button>
                  <input type="checkbox" checked={qtd===chaves.length}
                    ref={el=>{ if(el) el.indeterminate=qtd>0&&qtd<chaves.length; }}
                    onChange={()=>toggleAnoTudo(ano,meses)} style={{ accentColor:'#2563eb' }} />
                  <span style={{ fontWeight:700, fontSize:13, color:'#0f172a' }}>{ano}</span>
                  <span style={{ fontSize:10, color:'#94a3b8', marginLeft:'auto' }}>{qtd}/{chaves.length}</span>
                </div>
                {ab && (
                  <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:4, padding:'4px 4px 8px 28px' }}>
                    {meses.map(m => {
                      const chave=`${ano}-${m}`; const sel=mesesSel.includes(chave);
                      return (
                        <label key={m} style={{ display:'flex', alignItems:'center', gap:3, fontSize:11, cursor:'pointer',
                          color:sel?'#2563eb':'#374151', fontWeight:sel?700:400,
                          padding:'2px 4px', borderRadius:5, background:sel?'#eff6ff':'transparent' }}>
                          <input type="checkbox" checked={sel} onChange={()=>toggleMes(chave)} style={{ accentColor:'#2563eb', width:11, height:11 }} />
                          {NOMES_MES[+m-1]}
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
// GRÁFICO COM RÓTULOS E LINHAS SUAVIZADAS
// ═══════════════════════════════════════════════════════════════════════════
function Grafico({ dados, chaveH, chaveIA, titulo, refZero=false, legendaRefs, yDomain }: any) {
  const temIA = dados.some((d: any) => d[chaveIA]!=null);
  const n     = dados.length;

  return (
    <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', padding:'16px 18px' }}>
      <div style={{ fontWeight:700, fontSize:13, color:'#0f172a', marginBottom:12 }}>{titulo}</div>

      <ResponsiveContainer width="100%" height={210}>
        <LineChart data={dados} margin={{ top:14, right:16, bottom:0, left:-8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis dataKey="mes" tick={{ fontSize:10, fill:'#94a3b8' }}
            tickFormatter={lMes} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize:10, fill:'#94a3b8' }} tickFormatter={v=>`${v}%`}
            domain={yDomain || ['auto','auto']} />
          <Tooltip content={<TT />} />

          {refZero && <ReferenceLine y={0} stroke="#cbd5e1" strokeWidth={1.5} />}

          {/* Linhas de referência com explicações embutidas */}
          {(legendaRefs||[]).map(({ y, cor, pos='insideTopRight' }: any) => (
            <ReferenceLine key={y} y={y} stroke={cor} strokeDasharray="5 3" strokeWidth={1.5}>
              <Label value={`${y}%`} position={pos} fill={cor} fontSize={9} fontWeight={700} />
            </ReferenceLine>
          ))}

          {chaveH && (
            <Line type="monotone" dataKey={chaveH} name="Humano" stroke={COR.humano} strokeWidth={2.5}
              dot={{ r:3, fill:COR.humano, strokeWidth:2, stroke:'#fff' }}
              activeDot={{ r:5 }} connectNulls={false}
              label={chaveH ? RoituloLinha(COR.humano, n) : false} />
          )}
          {temIA && chaveIA && (
            <Line type="monotone" dataKey={chaveIA} name="IA (Nexus)" stroke={COR.ia} strokeWidth={2.5}
              dot={{ r:5, fill:COR.ia, strokeWidth:2, stroke:'#fff' }}
              activeDot={{ r:6 }} connectNulls={false}
              label={RoituloLinha(COR.ia, n)} />
          )}
          <Legend iconType="line" wrapperStyle={{ fontSize:11, paddingTop:6 }} />
        </LineChart>
      </ResponsiveContainer>

      {/* Legenda das linhas de referência */}
      {legendaRefs && (
        <div style={{ display:'flex', flexWrap:'wrap', gap:16, marginTop:10, paddingTop:8, borderTop:'1px solid #f8fafc' }}>
          {legendaRefs.map(({ y, cor, label }: any) => (
            <div key={y} style={{ display:'flex', alignItems:'center', gap:6, fontSize:10, color:'#64748b' }}>
              <svg width={20} height={3}><line x1={0} y1={1.5} x2={20} y2={1.5} stroke={cor} strokeWidth={1.5} strokeDasharray="4 2"/></svg>
              <span style={{ color:cor, fontWeight:700 }}>{y}%</span>
              <span>{label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// CARD KPI
// ═══════════════════════════════════════════════════════════════════════════
function Card({ label, valor, cor, sub }: any) {
  return (
    <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', padding:'14px 18px', flex:1, minWidth:130 }}>
      <div style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.06em', color:'#94a3b8', marginBottom:8 }}>{label}</div>
      <div style={{ fontSize:24, fontWeight:900, color:cor||'#4f46e5', lineHeight:1 }}>{valor==null?'—':pct(valor)}</div>
      {sub && <div style={{ fontSize:11, color:'#94a3b8', marginTop:6 }}>{sub}</div>}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// TABELAS DE BIAS
// ═══════════════════════════════════════════════════════════════════════════
const styleTab = (ativo: boolean): React.CSSProperties => ({
  padding:'10px 18px', border:'none', background:'none', cursor:'pointer',
  fontSize:13, fontWeight:ativo?700:400,
  color:ativo?'#0f172a':'#64748b',
  borderBottom: ativo?'2px solid #2563eb':'2px solid transparent',
  marginBottom:-1,
});
const Th = ({ children, right }: any) => (
  <th style={{ padding:'8px 10px', textAlign:right?'right':'left', fontSize:10, fontWeight:800,
    textTransform:'uppercase', letterSpacing:'.05em', color:'#94a3b8', borderBottom:'2px solid #f1f5f9',
    whiteSpace:'nowrap' }}>
    {children}
  </th>
);
const Td = ({ children, right, bold, cor }: any) => (
  <td style={{ padding:'8px 10px', textAlign:right?'right':'left', fontWeight:bold?700:400,
    color:cor||'#374151', fontSize:13, fontVariantNumeric:'tabular-nums' }}>
    {children}
  </td>
);

function TabelaBias({ itens, modo }: { itens: any[], modo: 'super'|'sub' }) {
  const filtrados = itens
    .filter(i => modo==='super' ? (i.bias_h||0) > 5 : (i.bias_h||0) < -5)
    .sort((a,b) => modo==='super'
      ? (b.vol_previsto-b.vol_real) - (a.vol_previsto-a.vol_real)
      : (a.vol_real-a.vol_previsto) - (b.vol_real-b.vol_previsto));

  if (!filtrados.length)
    return <div style={{ padding:32, textAlign:'center', color:'#94a3b8', fontSize:13 }}>Nenhum SKU nesta categoria.</div>;

  const corD   = modo==='super' ? '#e11d48' : '#2563eb';
  const label  = modo==='super' ? 'Excesso (cx)' : 'Falta (cx)';
  const ícone  = modo==='super' ? '▲' : '▼';

  return (
    <div style={{ overflowX:'auto' }}>
      <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
        <thead>
          <tr>
            <Th>SKU</Th>
            <Th>Descrição</Th>
            <Th>Categoria</Th>
            <Th right>Real (cx)</Th>
            <Th right>Previsto (cx)</Th>
            <Th right>{label}</Th>
            <Th right>BIAS</Th>
            <Th right>Frequência</Th>
          </tr>
        </thead>
        <tbody>
          {filtrados.map((it, idx) => {
            const delta = Math.round(it.vol_previsto - it.vol_real);
            const abs   = Math.abs(delta);
            const freqMeses = Math.round(((it.persistencia||0) / 100) * (it.meses||1));
            const freq  = `${freqMeses}/${it.meses||'—'} meses`;
            return (
              <tr key={it.sku} style={{ background: idx%2?'#f9fafb':'#fff', borderBottom:'1px solid #f1f5f9' }}>
                <Td><code style={{ fontSize:11, color:'#64748b' }}>{it.sku}</code></Td>
                <Td><span title={it.descricao} style={{ display:'block', maxWidth:220, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{it.descricao}</span></Td>
                <Td><span style={{ fontSize:11, color:'#64748b' }}>{it.categoria}</span></Td>
                <Td right>{num(it.vol_real)}</Td>
                <Td right>{num(it.vol_previsto)}</Td>
                <Td right bold cor={corD}>{ícone} {num(abs)}</Td>
                <Td right bold cor={corD}>{sinal(it.bias_h)}</Td>
                <Td right>
                  <span style={{ fontSize:12, background:(it.persistencia||0)>=70?'#fef2f2':'#f0f9ff',
                    color:(it.persistencia||0)>=70?'#e11d48':'#0284c7',
                    borderRadius:5, padding:'2px 6px', fontWeight:700 }}>
                    {freq}
                  </span>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TabelaWmape({ itens }: { itens: any[] }) {
  const sorted = [...itens].sort((a,b) => (b.wmape_h||0)-(a.wmape_h||0));
  if (!sorted.length) return <div style={{ padding:32, textAlign:'center', color:'#94a3b8', fontSize:13 }}>Sem dados.</div>;

  return (
    <div style={{ overflowX:'auto' }}>
      <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
        <thead>
          <tr>
            <Th>SKU</Th>
            <Th>Descrição</Th>
            <Th>Categoria</Th>
            <Th right>Real (cx)</Th>
            <Th right>Previsto (cx)</Th>
            <Th right>Δ (cx)</Th>
            <Th right>WMAPE</Th>
            <Th right>BIAS</Th>
            <Th right>Meses</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((it, idx) => {
            const delta = Math.round(it.vol_previsto - it.vol_real);
            const wc = corWmape(it.wmape_h);
            const bc = corBias(it.bias_h);
            return (
              <tr key={it.sku} style={{ background:idx%2?'#f9fafb':'#fff', borderBottom:'1px solid #f1f5f9' }}>
                <Td><code style={{ fontSize:11, color:'#64748b' }}>{it.sku}</code></Td>
                <Td><span title={it.descricao} style={{ display:'block', maxWidth:220, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{it.descricao}</span></Td>
                <Td><span style={{ fontSize:11, color:'#64748b' }}>{it.categoria}</span></Td>
                <Td right>{num(it.vol_real)}</Td>
                <Td right>{num(it.vol_previsto)}</Td>
                <Td right bold cor={delta>0?'#e11d48':delta<0?'#2563eb':'#64748b'}>
                  {delta>0?'▲':delta<0?'▼':''} {num(Math.abs(delta))}
                </Td>
                <Td right bold cor={wc}>{pct(it.wmape_h)}</Td>
                <Td right bold cor={bc}>{sinal(it.bias_h)}</Td>
                <Td right><span style={{ color:'#94a3b8' }}>{it.meses}</span></Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// TELA PRINCIPAL
// ═══════════════════════════════════════════════════════════════════════════
export default function AuditoriaArena() {
  const [opts, setOpts]           = useState<any>({ categorias:[], skus:[], calendario:{} });
  const [mesesSel, setMesesSel]   = useState<string[]>([]);
  const [categoria, setCategoria] = useState('');
  const [sku, setSku]             = useState('');
  const [evolucao, setEvolucao]   = useState<any>(null);
  const [diag, setDiag]           = useState<any[]>([]);
  const [loading, setLoading]     = useState(false);
  const [erro, setErro]           = useState('');
  const [abaTabela, setAbaTabela] = useState<'wmape'|'super'|'sub'>('wmape');
  const [abaKpi, setAbaKpi]       = useState<'acuracia'|'fillrate'|'agente'>('acuracia');
  const [unidade, setUnidade]     = useState<'cx'|'rs'>('cx');
  // Agente
  const [relatorio, setRelatorio]   = useState<string>('');
  const [gerandoRel, setGerandoRel] = useState(false);
  const [baixandoPdf, setBaixandoPdf] = useState(false);
  const [erroAgente, setErroAgente] = useState('');
  const [chat, setChat] = useState<{role:'user'|'assistant', content:string}[]>([]);
  const [pergunta, setPergunta] = useState('');
  const [respondendo, setRespondendo] = useState(false);
  const [fillRate, setFillRate]    = useState<any>(null);
  const [fillDiag, setFillDiag]    = useState<{cat: any[], sku: any[]}>({cat:[], sku:[]});

  useEffect(() => {
    axios.get('/api/v1/kpis/filtros').then(r => {
      setOpts(r.data);
      const cal: Record<string,string[]> = r.data.calendario || {};
      const todos = Object.entries(cal).sort(([a],[b])=>+a - +b)
        .flatMap(([ano,ms])=>(ms as string[]).map(m=>`${ano}-${m}`));
      setMesesSel(todos.slice(-12));
    }).catch(()=>{});
  }, []);

  const qs = useMemo(()=>{
    const p = new URLSearchParams();
    mesesSel.forEach(m=>p.append('meses',m));
    if (categoria) p.set('categoria',categoria);
    if (sku) p.set('sku',sku);
    p.set('base', 'pedido');
    p.set('unidade', unidade);
    return p.toString();
  }, [mesesSel, categoria, sku, unidade]);

  const carregar = useCallback(async () => {
    if (!mesesSel.length) { setEvolucao(null); setDiag([]); return; }
    setLoading(true); setErro('');
    try {
      const [ev, dg, fr, frCat, frSku] = await Promise.all([
        axios.get(`/api/v1/kpis/evolucao?${qs}`),
        axios.get(`/api/v1/kpis/diagnostico?${qs}&nivel=sku`),
        axios.get(`/api/v1/kpis/fill-rate?${qs}&nivel=evolucao`),
        axios.get(`/api/v1/kpis/fill-rate?${qs}&nivel=categoria`),
        axios.get(`/api/v1/kpis/fill-rate?${qs}&nivel=sku`),
      ]);
      setEvolucao(ev.data);
      setDiag(dg.data?.itens || []);
      setFillRate(fr.data);
      setFillDiag({ cat: frCat.data?.itens || [], sku: frSku.data?.itens || [] });
    } catch(e: any) {
      setErro(e?.response?.data?.detail || 'Erro ao carregar.');
      setEvolucao(null); setDiag([]);
    } finally { setLoading(false); }
  }, [qs]);

  useEffect(()=>{ carregar(); }, [carregar]);

  const serie  = evolucao?.serie  || [];
  const resumo = evolucao?.resumo || {};
  const temIA  = serie.some((d: any)=>d.wmape_ia!=null);
  const skusFilt = opts.skus.filter((s: any)=>!categoria||s.categoria===categoria);
  const ctx = sku?`SKU ${sku}`:categoria?`Categoria: ${categoria}`:'Portfólio completo';


  // ─── Agente ────────────────────────────────────────────────────────────
  const gerarRelatorio = useCallback(async () => {
    if (!mesesSel.length) { setErroAgente('Selecione ao menos um mês.'); return; }
    setGerandoRel(true); setErroAgente(''); setRelatorio('');
    try {
      const r = await axios.get(`/api/v1/kpis/agente/relatorio?${qs}`);
      setRelatorio(r.data.relatorio || '');
      setChat([]);
    } catch (e: any) {
      setErroAgente(e?.response?.data?.detail || 'Falha ao gerar o relatório.');
    } finally { setGerandoRel(false); }
  }, [qs, mesesSel]);

  const baixarPdf = useCallback(async () => {
    if (!relatorio) return;
    setBaixandoPdf(true);
    try {
      const r = await axios.post('/api/v1/kpis/agente/pdf',
        { relatorio, meses: mesesSel, base: 'pedido', unidade: 'cx' },
        { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([r.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `relatorio_sop_${mesesSel[0]}_${mesesSel[mesesSel.length-1]}.pdf`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch {
      setErroAgente('Falha ao gerar o PDF.');
    } finally { setBaixandoPdf(false); }
  }, [relatorio, mesesSel]);

  const enviarPergunta = useCallback(async () => {
    const p = pergunta.trim();
    if (!p || respondendo) return;
    setPergunta('');
    const novoChat = [...chat, { role: 'user' as const, content: p }];
    setChat(novoChat);
    setRespondendo(true);
    try {
      const r = await axios.post('/api/v1/kpis/agente/chat', {
        pergunta: p, meses: mesesSel, base: 'pedido', unidade: 'cx',
        historico: chat.slice(-6),
      });
      setChat([...novoChat, { role: 'assistant' as const, content: r.data.resposta }]);
    } catch (e: any) {
      setChat([...novoChat, { role: 'assistant' as const,
        content: e?.response?.data?.detail || 'Não consegui responder agora.' }]);
    } finally { setRespondendo(false); }
  }, [pergunta, chat, mesesSel, respondendo]);

  const nSuper = diag.filter(i=>(i.bias_h||0)>5).length;
  const nSub   = diag.filter(i=>(i.bias_h||0)<-5).length;

  return (
    <div style={{ minHeight:'100vh', background:'#f8fafc', padding:'24px 28px', fontFamily:'system-ui,sans-serif' }}>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}} .spin{animation:spin 1s linear infinite}`}</style>
      <div style={{ maxWidth:1440, margin:'0 auto' }}>

        {/* CABEÇALHO */}
        <div style={{ marginBottom:18 }}>
          <h1 style={{ fontSize:20, fontWeight:900, color:'#0f172a', margin:0 }}>KPIs S&OP</h1>
          <p style={{ fontSize:12, color:'#64748b', margin:'4px 0 0' }}>
            Previsão vs vendido em caixas · Meses fechados · {ctx}
          </p>
        </div>

        {/* ABAS KPI + TOGGLE BASE */}
        <div style={{ display:'flex', gap:4, marginBottom:16, borderBottom:'2px solid #f1f5f9', alignItems:'center' }}>
          {([['acuracia','📊 Acurácia'], ['fillrate','📦 Fill Rate'], ['agente','✨ Relatório IA']] as const).map(([id, label]) => (
            <button key={id} onClick={() => setAbaKpi(id)}
              style={{ padding:'8px 20px', fontSize:13, fontWeight:800, border:'none', background:'none',
                cursor:'pointer', borderBottom: abaKpi===id ? '2px solid #2563eb' : '2px solid transparent',
                color: abaKpi===id ? '#2563eb' : '#94a3b8', marginBottom:-2 }}>
              {label}
            </button>
          ))}

          {/* Toggle — só aparece no Fill Rate, só Caixas / Reais */}
          {abaKpi === 'fillrate' && (
            <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:10, paddingBottom:6 }}>
              <div style={{ display:'flex', background:'#f1f5f9', borderRadius:8, padding:2 }}>
                {([['cx','Caixas'], ['rs','Reais']] as const).map(([id, label]) => (
                  <button key={id} onClick={() => setUnidade(id)}
                    style={{ padding:'5px 12px', fontSize:11, fontWeight:800, border:'none', borderRadius:6,
                      cursor:'pointer', background: unidade===id ? '#fff' : 'transparent',
                      color: unidade===id ? '#2563eb' : '#64748b',
                      boxShadow: unidade===id ? '0 1px 3px rgba(0,0,0,.08)' : 'none' }}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* FILTROS */}
        <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', padding:'14px 18px',
          marginBottom:16, display:'flex', flexWrap:'wrap', gap:12, alignItems:'flex-end' }}>
          <SeletorDatas calendario={opts.calendario} mesesSel={mesesSel} setMesesSel={setMesesSel} />

          <label style={{ display:'flex', flexDirection:'column', gap:4 }}>
            <span style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.05em', color:'#94a3b8' }}>Categoria</span>
            <select value={categoria} onChange={e=>{ setCategoria(e.target.value); setSku(''); }}
              style={{ border:'1px solid #d1d5db', borderRadius:10, padding:'8px 12px', fontSize:13, background:'#fff', minWidth:160 }}>
              <option value="">Todas</option>
              {opts.categorias.map((c: string)=><option key={c} value={c}>{c}</option>)}
            </select>
          </label>

          <label style={{ display:'flex', flexDirection:'column', gap:4 }}>
            <span style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.05em', color:'#94a3b8' }}>SKU</span>
            <select value={sku} onChange={e=>setSku(e.target.value)}
              style={{ border:'1px solid #d1d5db', borderRadius:10, padding:'8px 12px', fontSize:13, background:'#fff', minWidth:260 }}>
              <option value="">Todos</option>
              {skusFilt.map((s: any)=>(
                <option key={s.sku} value={s.sku}>{s.sku} — {s.descricao}</option>
              ))}
            </select>
          </label>

          {loading && <Loader2 style={{ width:16, color:'#94a3b8', alignSelf:'center' }} className="animate-spin"/>}
          {erro && <span style={{ color:'#e11d48', fontSize:12, alignSelf:'center' }}>{erro}</span>}
        </div>

        {/* ── ABA ACURÁCIA ── */}
        {abaKpi === 'acuracia' && <>

        {/* CARDS */}
        {resumo.wmape_h != null && (
          <div style={{ display:'flex', gap:12, marginBottom:16, flexWrap:'wrap' }}>
            <Card label="WMAPE Humano" valor={resumo.wmape_h} cor={corWmape(resumo.wmape_h)}
              sub={`Acurácia ${pct(100-resumo.wmape_h)}`} />
            <Card label="Bias Humano" valor={resumo.bias_h} cor={corBias(resumo.bias_h)}
              sub={(resumo.bias_h||0)>5?'▲ Superestimando':(resumo.bias_h||0)<-5?'▼ Subestimando':'✓ Equilibrado'} />
            {temIA && (
              <>
                <Card label="WMAPE IA" valor={resumo.wmape_ia} cor={corWmape(resumo.wmape_ia)}
                  sub={`${resumo.meses_com_ia||0} meses`} />
                <Card label="FVA" valor={resumo.fva}
                  cor={(resumo.fva||0)<0?'#059669':(resumo.fva||0)>0?'#e11d48':'#94a3b8'}
                  sub={(resumo.fva||0)<0?'IA mais precisa':(resumo.fva||0)>0?'Humano mais preciso':'—'} />
              </>
            )}
            <div style={{ background:'#f0f9ff', border:'1px solid #bae6fd', borderRadius:14,
              padding:'14px 18px', flex:1, minWidth:160 }}>
              <div style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.06em', color:'#0284c7', marginBottom:6 }}>Período</div>
              <div style={{ fontSize:13, fontWeight:700 }}>{mesesSel.length} meses</div>
              {mesesSel.length>0 && (
                <div style={{ fontSize:11, color:'#64748b', marginTop:3 }}>
                  {lMes(mesesSel[0])} → {lMes(mesesSel[mesesSel.length-1])}
                </div>
              )}
              {!temIA && <div style={{ fontSize:10, color:'#d97706', marginTop:6 }}>⚠ IA disponível a partir de Jun/26</div>}
            </div>
          </div>
        )}

        {/* GRÁFICOS */}
        {serie.length > 0 ? (
          <div style={{ display:'flex', flexDirection:'column', gap:14, marginBottom:20 }}>

            <Grafico dados={serie} chaveH="wmape_h" chaveIA="wmape_ia" titulo="WMAPE (%)"
              legendaRefs={[
                { y:20, cor:'#059669', label:'Meta — abaixo de 20% a acurácia é adequada para o plano tático' },
                { y:35, cor:'#e11d48', label:'Crítico — acima de 35% o plano não tem base confiável de previsão' },
              ]} />

            <Grafico dados={serie} chaveH="bias_h" chaveIA="bias_ia" titulo="BIAS (%)"
              refZero
              legendaRefs={[
                { y: 10, cor:'#e11d48', label:'Limite de atenção positivo — acima de +10% há superestimativa sistemática, risco de sobra de estoque' },
                { y:-10, cor:'#2563eb', label:'Limite de atenção negativo — abaixo de -10% há subestimativa sistemática, risco de ruptura' },
              ]} />

            {temIA && (
              <Grafico dados={serie} chaveH={undefined} chaveIA="fva" titulo="FVA — Forecast Value Added (%)"
                refZero
                legendaRefs={[
                  { y:0, cor:'#94a3b8', label:'Zero — FVA negativo significa que a IA foi mais precisa que o ajuste humano; positivo, que o humano melhorou o plano' },
                ]} />
            )}
          </div>
        ) : !loading && (
          <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9',
            padding:48, textAlign:'center', color:'#94a3b8', fontSize:13, marginBottom:20 }}>
            {mesesSel.length===0?'Selecione pelo menos um mês.':'Sem dados para os filtros selecionados.'}
          </div>
        )}

        {/* TABELAS */}
        {diag.length > 0 && (
          <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', overflow:'hidden' }}>
            <div style={{ display:'flex', borderBottom:'1px solid #f1f5f9', padding:'0 18px', background:'#fafafa' }}>
              <button style={styleTab(abaTabela==='wmape')} onClick={()=>setAbaTabela('wmape')}>
                Ranking WMAPE ({diag.length})
              </button>
              <button style={styleTab(abaTabela==='super')} onClick={()=>setAbaTabela('super')}>
                <TrendingUp style={{ width:13, display:'inline', marginRight:4, color:abaTabela==='super'?'#e11d48':'#94a3b8' }}/>
                Superestimando ({nSuper})
              </button>
              <button style={styleTab(abaTabela==='sub')} onClick={()=>setAbaTabela('sub')}>
                <TrendingDown style={{ width:13, display:'inline', marginRight:4, color:abaTabela==='sub'?'#2563eb':'#94a3b8' }}/>
                Subestimando ({nSub})
              </button>
            </div>

            {/* Legenda da aba */}
            <div style={{ padding:'10px 18px', background:'#fafafa', borderBottom:'1px solid #f1f5f9', fontSize:11, color:'#64748b' }}>
              {abaTabela==='wmape' && '↑ Ordenado do maior erro para o menor. Δ = Previsto − Real: ▲ excesso de caixas planejadas, ▼ falta de caixas planejadas.'}
              {abaTabela==='super' && '↑ Ordenado por excesso absoluto em caixas. Frequência em vermelho quando o erro ocorre em ≥ 70% dos meses — indica padrão sistemático, não acaso.'}
              {abaTabela==='sub'   && '↑ Ordenado por falta absoluta em caixas. Frequência em vermelho quando o erro ocorre em ≥ 70% dos meses — risco de ruptura recorrente.'}
            </div>

            <div style={{ padding:'0 0 4px' }}>
              {abaTabela==='wmape' && <TabelaWmape itens={diag} />}
              {abaTabela==='super' && <TabelaBias  itens={diag} modo="super" />}
              {abaTabela==='sub'   && <TabelaBias  itens={diag} modo="sub" />}
            </div>
          </div>
        )}

        {/* Rodapé metodológico */}
        <div style={{ fontSize:10, color:'#94a3b8', lineHeight:1.8, padding:'12px 4px' }}>
          <b>WMAPE</b> = SUM(|prev−real|)/SUM(real): ponderado por volume, adequado para portfólio e categoria. &nbsp;
          <b>BIAS</b> = (SUM(prev)−SUM(real))/SUM(real): positivo = superestimou, negativo = subestimou. &nbsp;
          <b>Frequência</b> = meses onde o erro foi na mesma direção do BIAS médio (≥ 70% = padrão sistemático). &nbsp;
          <b>FVA</b> = WMAPE_IA − WMAPE_Humano (disponível somente para ciclos Nexus, M+2 congelado, a partir de jun/26). &nbsp;
          Meta humana jan/23–mai/26 via arquivo histórico · jun/26+ via vol_final do ciclo M+2 · portfólio ativo, a partir da primeira venda de cada SKU.
        </div>
        </>}

        {/* ── ABA FILL RATE ── */}
        {abaKpi === 'fillrate' && (
          <div>
            {/* Cards de resumo */}
            {fillRate?.resumo && (
              <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:12, marginBottom:16 }}>
                {[
                  { label:'Pedido',
                    val: unidade==='rs'
                      ? 'R$ ' + (fillRate.resumo.pedido_total||0).toLocaleString('pt-BR')
                      : (fillRate.resumo.pedido_total||0).toLocaleString('pt-BR') + ' cx',
                    cor:'#0f172a' },
                  { label:'Faturado',
                    val: unidade==='rs'
                      ? 'R$ ' + (fillRate.resumo.faturado_total||0).toLocaleString('pt-BR')
                      : (fillRate.resumo.faturado_total||0).toLocaleString('pt-BR') + ' cx',
                    cor:'#059669' },
                  { label:'Corte',
                    val: unidade==='rs'
                      ? 'R$ ' + (fillRate.resumo.corte_total||0).toLocaleString('pt-BR')
                      : (fillRate.resumo.corte_total||0).toLocaleString('pt-BR') + ' cx',
                    cor:'#e11d48' },
                  { label:'Atendimento',
                    val: fillRate.resumo.fill_rate != null ? `${Number(fillRate.resumo.fill_rate).toFixed(1).replace('.',',')}%` : '—',
                    cor: (fillRate.resumo.fill_rate ?? 0) >= 95 ? '#059669' : (fillRate.resumo.fill_rate ?? 0) >= 85 ? '#d97706' : '#e11d48' },
                ].map(c => (
                  <div key={c.label} style={{ background:'#fff', borderRadius:12, border:'1px solid #f1f5f9', padding:'16px 20px' }}>
                    <div style={{ fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:'.05em', color:'#94a3b8', marginBottom:6 }}>{c.label}</div>
                    <div style={{ fontSize:22, fontWeight:900, color:c.cor }}>{c.val}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Gráfico evolução mensal */}
            {(fillRate?.serie || []).length > 0 && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', padding:'20px', marginBottom:16 }}>
                <div style={{ fontSize:13, fontWeight:800, color:'#0f172a', marginBottom:16 }}>Atendimento mês a mês</div>
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={fillRate.serie} margin={{ top:10, right:20, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                    <XAxis dataKey="mes" tickFormatter={lMes} tick={{ fontSize:10, fontWeight:700 }} axisLine={false} tickLine={false} />
                    <YAxis domain={[70,100]} tick={{ fontSize:10 }} axisLine={false} tickLine={false} tickFormatter={v=>`${v}%`} />
                    <Tooltip content={<TT />} />
                    <ReferenceLine y={95} stroke="#059669" strokeDasharray="4 2" strokeWidth={1}>
                      <Label value="Meta 95%" position="right" fontSize={9} fill="#059669" />
                    </ReferenceLine>
                    <ReferenceLine y={85} stroke="#e11d48" strokeDasharray="4 2" strokeWidth={1}>
                      <Label value="Crítico 85%" position="right" fontSize={9} fill="#e11d48" />
                    </ReferenceLine>
                    <Line dataKey="atendimento" name="Atendimento" stroke="#2563eb" strokeWidth={2.5} dot={{ r:4 }}
                      label={RoituloLinha('#2563eb', fillRate.serie.length)} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Tabela por categoria */}
            {fillDiag.cat.length > 0 && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', overflow:'hidden', marginBottom:16 }}>
                <div style={{ padding:'14px 18px', fontWeight:800, fontSize:13, borderBottom:'1px solid #f1f5f9', background:'#fafafa' }}>
                  Atendimento por categoria — ordenado pelo maior corte
                </div>
                <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                  <thead>
                    <tr style={{ background:'#f8fafc' }}>
                      {['Categoria',
                        unidade==='rs' ? 'Pedido (R$)' : 'Pedido (cx)',
                        unidade==='rs' ? 'Faturado (R$)' : 'Faturado (cx)',
                        unidade==='rs' ? 'Corte (R$)' : 'Corte (cx)',
                        'Atendimento','Status'].map(h => (
                        <th key={h} style={{ padding:'8px 14px', textAlign: h==='Categoria' ? 'left' : 'right',
                          fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.05em', color:'#94a3b8' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {fillDiag.cat.map((r: any, i: number) => {
                      const cor = r.classe==='Restricao' ? '#e11d48' : r.classe==='Atencao' ? '#d97706' : '#059669';
                      const pedVal  = unidade==='rs' ? 'R$ '+(r.vl_pedido||0).toLocaleString('pt-BR')   : (r.pedido||0).toLocaleString('pt-BR');
                      const fatVal  = unidade==='rs' ? 'R$ '+(r.vl_entregue||0).toLocaleString('pt-BR') : (r.entregue||0).toLocaleString('pt-BR');
                      const corVal  = unidade==='rs' ? 'R$ '+(r.vl_corte||0).toLocaleString('pt-BR')    : (r.corte||0).toLocaleString('pt-BR');
                      return (
                        <tr key={r.categoria} style={{ background: i%2 ? '#f9fafb' : '#fff', borderBottom:'1px solid #f1f5f9' }}>
                          <td style={{ padding:'8px 14px', fontWeight:700, color:'#334155' }}>{r.categoria}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', color:'#475569' }}>{pedVal}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', color:'#059669', fontWeight:700 }}>{fatVal}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', color:'#e11d48', fontWeight:700 }}>{corVal}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', fontWeight:900, color:cor }}>
                            {r.atendimento != null ? `${r.atendimento.toFixed(1).replace('.',',')}%` : '—'}
                          </td>
                          <td style={{ padding:'8px 14px', textAlign:'right' }}>
                            <span style={{ fontSize:10, fontWeight:800, padding:'2px 8px', borderRadius:6, background: cor+'18', color:cor }}>{r.classe}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Tabela por SKU */}
            {fillDiag.sku.length > 0 && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', overflow:'hidden' }}>
                <div style={{ padding:'14px 18px', fontWeight:800, fontSize:13, borderBottom:'1px solid #f1f5f9', background:'#fafafa' }}>
                  SKUs com corte, ordenados pelo maior volume cortado
                </div>
                <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                  <thead>
                    <tr style={{ background:'#f8fafc' }}>
                      {['SKU','Descrição','Categoria',
                        unidade==='rs' ? 'Pedido (R$)' : 'Pedido (cx)',
                        unidade==='rs' ? 'Faturado (R$)' : 'Faturado (cx)',
                        unidade==='rs' ? 'Corte (R$)' : 'Corte (cx)',
                        'Atendimento','Status'].map(h => (
                        <th key={h} style={{ padding:'8px 14px', textAlign: ['SKU','Descrição','Categoria'].includes(h) ? 'left' : 'right',
                          fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.05em', color:'#94a3b8' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {fillDiag.sku.map((r: any, i: number) => {
                      const cor = r.classe==='Restricao' ? '#e11d48' : r.classe==='Atencao' ? '#d97706' : '#059669';
                      const pedVal = unidade==='rs' ? 'R$ '+(r.vl_pedido||0).toLocaleString('pt-BR')   : (r.pedido||0).toLocaleString('pt-BR');
                      const fatVal = unidade==='rs' ? 'R$ '+(r.vl_entregue||0).toLocaleString('pt-BR') : (r.entregue||0).toLocaleString('pt-BR');
                      const corVal = unidade==='rs' ? 'R$ '+(r.vl_corte||0).toLocaleString('pt-BR')    : (r.corte||0).toLocaleString('pt-BR');
                      return (
                        <tr key={r.sku} style={{ background: i%2 ? '#f9fafb' : '#fff', borderBottom:'1px solid #f1f5f9' }}>
                          <td style={{ padding:'8px 14px', fontWeight:700, color:'#64748b', fontSize:10 }}>{r.sku}</td>
                          <td style={{ padding:'8px 14px', fontWeight:700, color:'#334155', maxWidth:200, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{r.descricao}</td>
                          <td style={{ padding:'8px 14px', color:'#64748b' }}>{r.categoria}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', color:'#475569' }}>{pedVal}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', color:'#059669', fontWeight:700 }}>{fatVal}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', color:'#e11d48', fontWeight:700 }}>{corVal}</td>
                          <td style={{ padding:'8px 14px', textAlign:'right', fontWeight:900, color:cor }}>
                            {r.atendimento != null ? `${r.atendimento.toFixed(1).replace('.',',')}%` : '—'}
                          </td>
                          <td style={{ padding:'8px 14px', textAlign:'right' }}>
                            <span style={{ fontSize:10, fontWeight:800, padding:'2px 8px', borderRadius:6, background: cor+'18', color:cor }}>{r.classe}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div style={{ fontSize:10, color:'#94a3b8', lineHeight:1.8, padding:'12px 4px' }}>
              <b>Atendimento</b> = volume entregue / volume pedido. &nbsp;
              <b>Corte</b> = volume pedido não entregue, medido diretamente no registro de corte do pedido. &nbsp;
              Mede execução de suprimento, não acurácia de previsão. &nbsp;
              Situação: <b style={{color:'#059669'}}>Adequado</b> ≥ 95% · <b style={{color:'#d97706'}}>Atenção</b> 85–95% · <b style={{color:'#e11d48'}}>Restrição</b> &lt; 85%.
            </div>
          </div>
        )}

        {/* ── ABA AGENTE IA ── */}
        {abaKpi === 'agente' && (
          <div>
            {/* Barra de ação */}
            <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9',
              padding:'18px 22px', marginBottom:16, display:'flex', alignItems:'center',
              justifyContent:'space-between', gap:16, flexWrap:'wrap' }}>
              <div style={{ minWidth:0 }}>
                <div style={{ fontSize:14, fontWeight:900, color:'#0f172a', display:'flex', alignItems:'center', gap:8 }}>
                  <Sparkles style={{ width:16, color:'#7c3aed' }} />
                  Relatório analítico
                </div>
                <div style={{ fontSize:11, color:'#64748b', marginTop:3 }}>
                  {mesesSel.length} {mesesSel.length === 1 ? 'mês' : 'meses'} · vendido · caixas · {ctx}
                </div>
              </div>
              <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
                <button onClick={gerarRelatorio} disabled={gerandoRel || !mesesSel.length}
                  style={{ display:'flex', alignItems:'center', gap:7, padding:'9px 18px',
                    borderRadius:10, border:'none', fontSize:12, fontWeight:800, cursor: gerandoRel ? 'wait' : 'pointer',
                    background: gerandoRel ? '#e2e8f0' : '#7c3aed', color: gerandoRel ? '#94a3b8' : '#fff' }}>
                  {gerandoRel
                    ? <><Loader2 style={{ width:14 }} className="spin" /> Analisando…</>
                    : <>{relatorio ? <RefreshCw style={{ width:14 }} /> : <Sparkles style={{ width:14 }} />}
                       {relatorio ? 'Regerar' : 'Gerar relatório'}</>}
                </button>

                <button onClick={baixarPdf} disabled={!relatorio || baixandoPdf}
                  style={{ display:'flex', alignItems:'center', gap:7, padding:'9px 16px',
                    borderRadius:10, fontSize:12, fontWeight:800,
                    cursor: (!relatorio || baixandoPdf) ? 'not-allowed' : 'pointer',
                    border:'1px solid #e2e8f0',
                    background:'#fff', color: relatorio ? '#334155' : '#cbd5e1' }}>
                  {baixandoPdf ? <Loader2 style={{ width:14 }} className="spin" /> : <FileDown style={{ width:14 }} />}
                  PDF
                </button>
              </div>
            </div>

            {erroAgente && (
              <div style={{ background:'#fef2f2', border:'1px solid #fecaca', borderRadius:12,
                padding:'12px 16px', marginBottom:16, fontSize:12, color:'#991b1b', fontWeight:600 }}>
                {erroAgente}
              </div>
            )}

            {/* Relatório */}
            {gerandoRel && !relatorio && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9',
                padding:'48px 24px', textAlign:'center', marginBottom:16 }}>
                <Loader2 style={{ width:26, color:'#7c3aed', margin:'0 auto 12px' }} className="spin" />
                <div style={{ fontSize:13, fontWeight:800, color:'#334155' }}>Analisando os indicadores…</div>
                <div style={{ fontSize:11, color:'#94a3b8', marginTop:5 }}>
                  Cruzando WMAPE, BIAS, fill rate e evolução YoY. Leva alguns segundos.
                </div>
              </div>
            )}

            {relatorio && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9',
                padding:'26px 32px', marginBottom:16 }}>
                {relatorio.split('\n').filter(l => l.trim()).map((linha, i) => {
                  const t = linha.trim().replace(/\*\*/g, '');
                  const ehTitulo = (/^\d+[.)]/.test(t) && t.length < 70)
                                || (t === t.toUpperCase() && t.length < 70 && t.length > 3);
                  return ehTitulo ? (
                    <div key={i} style={{ fontSize:12, fontWeight:900, color:'#7c3aed',
                      textTransform:'uppercase', letterSpacing:'.04em', marginTop: i ? 18 : 0, marginBottom:7 }}>
                      {t.replace(/^#+\s*/, '')}
                    </div>
                  ) : (
                    <p key={i} style={{ fontSize:13, lineHeight:1.72, color:'#334155',
                      margin:'0 0 9px', textAlign:'justify' }}>
                      {t.replace(/^[#\-\u2022]\s*/, '')}
                    </p>
                  );
                })}
              </div>
            )}

            {/* Chat */}
            <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', overflow:'hidden' }}>
                <div style={{ padding:'14px 20px', borderBottom:'1px solid #f1f5f9', background:'#fafafa' }}>
                  <div style={{ fontSize:12, fontWeight:900, color:'#0f172a' }}>Perguntas sobre os indicadores</div>
                  <div style={{ fontSize:10.5, color:'#94a3b8', marginTop:2 }}>
                    O agente responde apenas com base nos dados do recorte selecionado.
                  </div>
                </div>

                <div style={{ maxHeight:400, overflowY:'auto', padding: chat.length ? '16px 20px' : 0 }}>
                  {chat.map((m, i) => (
                    <div key={i} style={{ marginBottom:14, display:'flex',
                      justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
                      <div style={{ maxWidth:'82%', padding:'10px 14px', borderRadius:12, fontSize:12.5, lineHeight:1.6,
                        background: m.role === 'user' ? '#eef2ff' : '#f8fafc',
                        color: m.role === 'user' ? '#3730a3' : '#334155',
                        border: `1px solid ${m.role === 'user' ? '#e0e7ff' : '#f1f5f9'}`,
                        whiteSpace:'pre-wrap' }}>
                        {m.content}
                      </div>
                    </div>
                  ))}
                  {respondendo && (
                    <div style={{ display:'flex', gap:7, alignItems:'center', color:'#94a3b8', fontSize:12, padding:'4px 2px' }}>
                      <Loader2 style={{ width:13 }} className="spin" /> Consultando os dados…
                    </div>
                  )}
                </div>

                {/* Sugestões rápidas */}
                {!chat.length && (
                  <div style={{ padding:'14px 20px', display:'flex', gap:7, flexWrap:'wrap' }}>
                    {[
                      'Qual categoria mais piorou vs ano passado?',
                      'Onde o corte é falha de planejamento e não de operação?',
                      'A IA está melhor que o humano? Em quais categorias?',
                      'Quais SKUs têm viés sistemático?',
                    ].map(s => (
                      <button key={s} onClick={() => setPergunta(s)}
                        style={{ fontSize:11, padding:'6px 12px', borderRadius:16, cursor:'pointer',
                          border:'1px solid #e2e8f0', background:'#fff', color:'#64748b', fontWeight:600 }}>
                        {s}
                      </button>
                    ))}
                  </div>
                )}

                <div style={{ display:'flex', gap:8, padding:'14px 20px', borderTop:'1px solid #f1f5f9' }}>
                  <input value={pergunta} onChange={e => setPergunta(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') enviarPergunta(); }}
                    placeholder="Pergunte sobre WMAPE, BIAS, fill rate, categorias, SKUs…"
                    style={{ flex:1, border:'1px solid #e2e8f0', borderRadius:10, padding:'10px 14px',
                      fontSize:12.5, outline:'none' }} />
                  <button onClick={enviarPergunta} disabled={!pergunta.trim() || respondendo}
                    style={{ display:'flex', alignItems:'center', gap:6, padding:'10px 18px', borderRadius:10,
                      border:'none', fontSize:12, fontWeight:800,
                      cursor: (!pergunta.trim() || respondendo) ? 'not-allowed' : 'pointer',
                      background: (!pergunta.trim() || respondendo) ? '#e2e8f0' : '#2563eb',
                      color: (!pergunta.trim() || respondendo) ? '#94a3b8' : '#fff' }}>
                    <Send style={{ width:13 }} /> Enviar
                  </button>
                </div>
            </div>

            {!relatorio && !gerandoRel && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px dashed #e2e8f0',
                padding:'52px 24px', textAlign:'center' }}>
                <Sparkles style={{ width:30, color:'#c4b5fd', margin:'0 auto 14px' }} />
                <div style={{ fontSize:14, fontWeight:800, color:'#334155', marginBottom:6 }}>
                  Análise completa dos indicadores
                </div>
                <div style={{ fontSize:12, color:'#94a3b8', maxWidth:520, margin:'0 auto', lineHeight:1.7 }}>
                  O agente cruza WMAPE, BIAS, fill rate e evolução YoY de todas as categorias e SKUs,
                  identifica padrões sistemáticos e separa falhas de planejamento de falhas de operação.
                  Todo número citado é rastreável às tabelas do anexo.
                </div>
              </div>
            )}

            <div style={{ fontSize:10, color:'#94a3b8', lineHeight:1.8, padding:'14px 4px' }}>
              <b>Escopo:</b> SKUs ativos do portfólio · sem filtro de cliente · erro absoluto computado em (sku, mês) antes de agregar. &nbsp;
              <b>Meta humana:</b> arquivo Excel jan/23–mai/26 · vol_final do ciclo M-2 a partir de jun/26. &nbsp;
              <b>IA:</b> vol_ia do ciclo M-2, disponível apenas a partir de jun/26. &nbsp;
              <b>R$:</b> valorizado pelo PMV do pedido (vl_pedido/qt_pedido), isolando erro de volume de desconto de faturamento.
            </div>
          </div>
        )}

      </div>
    </div>
  );
}