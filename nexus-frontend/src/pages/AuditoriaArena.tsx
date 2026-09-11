import { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  LineChart, Line, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend, Label,
} from 'recharts';
import { ChevronDown, ChevronRight, Loader2, TrendingUp, TrendingDown,
         Sparkles, FileDown, RefreshCw, Info } from 'lucide-react';

// ─── Paleta ───────────────────────────────────────────────────────────────────
const COR = { humano: '#2563eb', ia: '#10b981', over: '#e11d48', under: '#2563eb', neutro: '#94a3b8' };

// ─── Formatadores ─────────────────────────────────────────────────────────────
const NOMES_MES = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const lMes  = (m: string) => { const [a,mm] = m.split('-'); return `${NOMES_MES[+mm-1]}/${a.slice(2)}`; };
const pct   = (v: any, d = 1) => v == null ? '—' : `${Number(v).toFixed(d).replace('.',',')}%`;
const sinal  = (v: any) => { if (v==null) return '—'; const n=Number(v); return `${n>0?'+':''}${n.toFixed(1).replace('.',',')}%`; };
const num   = (v: any)  => v == null ? '—' : Math.round(Number(v)).toLocaleString('pt-BR');
const corWmape = (v: number|null) => v==null?COR.neutro:v<=20?'#059669':v<=35?'#d97706':'#e11d48';
const corClasse = (c?: string) => {
  switch (c) {
    case 'Sob controle':            return '#059669';
    case 'Superestimando':          return '#e11d48';
    case 'Subestimando':            return '#2563eb';
    case 'Errático':                return '#d97706';
    case 'Sem cobertura de plano':  return '#7c3aed';
    case 'Sem Meta':                return '#94a3b8';
    default:                        return '#64748b';
  }
};
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
  if (value == null || total > 20) return null;
  const salto = total > 12 ? 2 : 1;
  if (props.index % salto !== 0) return null;
  return (
    <text x={x} y={y - 9} fill={cor} fontSize={9} textAnchor="middle" fontWeight={700}>
      {Number(value).toFixed(1).replace('.',',')}
    </text>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// SELETOR DE DATAS
// ═══════════════════════════════════════════════════════════════════════════
function SeletorDatas({ calendario, mesesSel, setMesesSel }: any) {
  const [open, setOpen]       = useState(false);
  const [abertos, setAbertos] = useState<Set<string>>(new Set(Object.keys(calendario || {})));

  useEffect(() => {
    if (Object.keys(calendario || {}).length) setAbertos(new Set(Object.keys(calendario)));
  }, [calendario]);

  const toggleAno = (ano: string) => {
    setAbertos(prev => {
      const next = new Set(prev);
      if (next.has(ano)) next.delete(ano); else next.add(ano);
      return next;
    });
  };

  const toggleMes = (chave: string) => {
    setMesesSel((prev: string[]) =>
      prev.includes(chave) ? prev.filter(m => m !== chave) : [...prev, chave].sort()
    );
  };

  const toggleAnoTudo = (ano: string, meses: string[]) => {
    const chaves = meses.map(m => `${ano}-${m}`);
    const todasSel = chaves.every(c => mesesSel.includes(c));
    if (todasSel) setMesesSel((prev: string[]) => prev.filter(m => !chaves.includes(m)));
    else setMesesSel((prev: string[]) => Array.from(new Set([...prev, ...chaves])).sort());
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
function Grafico({ dados, chaveH, chaveIA, titulo, refZero=false, legendaRefs, yDomain, corLinha }: any) {
  const temIA = chaveIA && dados.some((d: any) => d[chaveIA]!=null);
  const n     = dados.length;
  const corPadrao = corLinha || COR.humano;

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

          {(legendaRefs||[]).map(({ y, cor, pos='insideTopRight', label }: any) => (
            <ReferenceLine key={y} y={y} stroke={cor} strokeDasharray="5 3" strokeWidth={1.5}>
              <Label value={`${y}% ${label ? `(${label.split(' — ')[0]})` : ''}`} position={pos} fill={cor} fontSize={9} fontWeight={700} />
            </ReferenceLine>
          ))}

          {chaveH && (
            <Line type="monotone" dataKey={chaveH} name="Real / Humano" stroke={corPadrao} strokeWidth={2.5}
              dot={{ r:3, fill:corPadrao, strokeWidth:2, stroke:'#fff' }}
              activeDot={{ r:5 }} connectNulls={false}
              label={RoituloLinha(corPadrao, n)} />
          )}
          {temIA && (
            <Line type="monotone" dataKey={chaveIA} name="IA (Nexus)" stroke={COR.ia} strokeWidth={2.5}
              dot={{ r:5, fill:COR.ia, strokeWidth:2, stroke:'#fff' }}
              activeDot={{ r:6 }} connectNulls={false}
              label={RoituloLinha(COR.ia, n)} />
          )}
          <Legend iconType="line" wrapperStyle={{ fontSize:11, paddingTop:6 }} />
        </LineChart>
      </ResponsiveContainer>

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
function Card({ label, valor, cor, sub, ehSinal = false }: any) {
  const valStr = ehSinal ? sinal(valor) : (valor == null ? '—' : pct(valor));
  return (
    <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', padding:'14px 18px', flex:1, minWidth:130 }}>
      <div style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.06em', color:'#94a3b8', marginBottom:8 }}>{label}</div>
      <div style={{ fontSize:22, fontWeight:900, color:cor||'#4f46e5', lineHeight:1 }}>{valStr}</div>
      {sub && <div style={{ fontSize:11, color:'#94a3b8', marginTop:6 }}>{sub}</div>}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// TABELAS ESTILIZADAS
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
            <Th right>WMAPE</Th>
            <Th right>Frequência</Th>
            <Th>Classificação</Th>
          </tr>
        </thead>
        <tbody>
          {filtrados.map((it, idx) => {
            const delta = Math.round(it.vol_previsto - it.vol_real);
            const abs   = Math.abs(delta);
            const freqMeses = Math.round(((it.persistencia||0) / 100) * (it.meses||1));
            const freq  = `${freqMeses}/${it.meses||'—'} meses`;
            const corCl = corClasse(it.classe);
            return (
              <tr key={it.sku} style={{ background: idx%2?'#f9fafb':'#fff', borderBottom:'1px solid #f1f5f9' }}>
                <Td><code style={{ fontSize:11, color:'#64748b' }}>{it.sku}</code></Td>
                <Td><span title={it.descricao} style={{ display:'block', maxWidth:220, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{it.descricao}</span></Td>
                <Td><span style={{ fontSize:11, color:'#64748b' }}>{it.categoria}</span></Td>
                <Td right>{num(it.vol_real)}</Td>
                <Td right>{num(it.vol_previsto)}</Td>
                <Td right bold cor={corD}>{ícone} {num(abs)}</Td>
                <Td right bold cor={corD}>{sinal(it.bias_h)}</Td>
                <Td right bold cor={corWmape(it.wmape_h)}>{pct(it.wmape_h)}</Td>
                <Td right>
                  <span style={{ fontSize:12, background:(it.persistencia||0)>=70?'#fef2f2':'#f0f9ff',
                    color:(it.persistencia||0)>=70?'#e11d48':'#0284c7',
                    borderRadius:5, padding:'2px 6px', fontWeight:700 }}>
                    {freq}
                  </span>
                </Td>
                <Td>
                  <span style={{ fontSize:10, fontWeight:800, padding:'2px 8px', borderRadius:6, background:corCl+'18', color:corCl, whiteSpace:'nowrap' }}>
                    {it.classe || '—'}
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
            <Th>Classificação</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((it, idx) => {
            const delta = Math.round(it.vol_previsto - it.vol_real);
            const wc = corWmape(it.wmape_h);
            const bc = corBias(it.bias_h);
            const corCl = corClasse(it.classe);
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
                <Td>
                  <span style={{ fontSize:10, fontWeight:800, padding:'2px 8px', borderRadius:6, background:corCl+'18', color:corCl, whiteSpace:'nowrap' }}>
                    {it.classe || '—'}
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

function TabelaRankingFillRate({ itens, unidade }: { itens: any[]; unidade: 'cx' | 'rs' }) {
  const sorted = [...itens].sort((a, b) => (b.corte || 0) - (a.corte || 0));
  if (!sorted.length) return <div style={{ padding: 32, textAlign: 'center', color: '#94a3b8', fontSize: 13 }}>Sem dados de atendimento.</div>;

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr>
            <Th>SKU</Th>
            <Th>Descrição</Th>
            <Th>Categoria</Th>
            <Th right>{unidade === 'rs' ? 'Pedido (R$)' : 'Pedido (cx)'}</Th>
            <Th right>{unidade === 'rs' ? 'Faturado (R$)' : 'Faturado (cx)'}</Th>
            <Th right>{unidade === 'rs' ? 'Corte (R$)' : 'Corte (cx)'}</Th>
            <Th right>Atendimento (%)</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((it, idx) => {
            const cor = it.classe === 'Restricao' ? '#e11d48' : it.classe === 'Atencao' ? '#d97706' : '#059669';
            const pedVal = unidade === 'rs' ? 'R$ ' + (it.pedido || 0).toLocaleString('pt-BR') : (it.pedido || 0).toLocaleString('pt-BR');
            const fatVal = unidade === 'rs' ? 'R$ ' + (it.entregue || 0).toLocaleString('pt-BR') : (it.entregue || 0).toLocaleString('pt-BR');
            const corVal = unidade === 'rs' ? 'R$ ' + (it.corte || 0).toLocaleString('pt-BR') : (it.corte || 0).toLocaleString('pt-BR');
            return (
              <tr key={it.sku} style={{ background: idx % 2 ? '#f9fafb' : '#fff', borderBottom: '1px solid #f1f5f9' }}>
                <Td><code style={{ fontSize: 11, color: '#64748b' }}>{it.sku}</code></Td>
                <Td><span title={it.descricao} style={{ display: 'block', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.descricao}</span></Td>
                <Td><span style={{ fontSize: 11, color: '#64748b' }}>{it.categoria}</span></Td>
                <Td right>{pedVal}</Td>
                <Td right cor="#059669" bold>{fatVal}</Td>
                <Td right cor="#e11d48" bold>▲ {corVal}</Td>
                <Td right bold cor={cor}>{it.atendimento != null ? `${it.atendimento.toFixed(1).replace('.', ',')}%` : '—'}</Td>
                <Td>
                  <span style={{ fontSize: 10, fontWeight: 800, padding: '2px 8px', borderRadius: 6, background: cor + '18', color: cor, whiteSpace: 'nowrap' }}>
                    {it.classe || '—'}
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

// ═══════════════════════════════════════════════════════════════════════════
// TABELA CONSOLIDADA 3 INDICADORES (ESTILO EXECUTIVE DARK)
// ═══════════════════════════════════════════════════════════════════════════
function TabelaConsolidada3Indicadores({
  diagCat, fillCat, diagSku, fillSku, categoriaFiltro
}: {
  diagCat: any[];
  fillCat: any[];
  diagSku: any[];
  fillSku: any[];
  categoriaFiltro: string;
}) {
  const [visao, setVisao] = useState<'auto' | 'skus' | 'categorias'>('auto');
  const mostrarSkus = visao === 'skus' || (visao === 'auto' && Boolean(categoriaFiltro));

  if (mostrarSkus) {
    const fillMap = new Map(fillSku.map((item: any) => [item.sku, item]));
    const diagMap = new Map(diagSku.map((item: any) => [item.sku, item]));
    const skusUnicos = Array.from(new Set([...fillMap.keys(), ...diagMap.keys()]));

    let linhas = skusUnicos.map((sKey) => {
      const fItem = fillMap.get(sKey);
      const dItem = diagMap.get(sKey);
      return {
        sku: sKey,
        descricao: dItem?.descricao || fItem?.descricao || sKey,
        categoria: dItem?.categoria || fItem?.categoria || 'SEM CATEGORIA',
        wmape: dItem?.wmape_h ?? null,
        bias: dItem?.bias_h ?? null,
        fillRate: fItem?.atendimento ?? null,
      };
    });

    if (categoriaFiltro) {
      linhas = linhas.filter((l) => l.categoria === categoriaFiltro);
    }

    linhas.sort((a, b) => (b.wmape ?? -1) - (a.wmape ?? -1));

    return (
      <div style={{ background: '#0a0f1d', borderRadius: 12, padding: 18, border: '1px solid #1e293b' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 900, color: '#f8fafc' }}>
              Consolidado dos 3 Indicadores por SKU {categoriaFiltro ? `— Categoria: ${categoriaFiltro}` : '— Todos os SKUs'}
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>
              Exibindo {linhas.length} produtos · WMAPE e BIAS (portfólio ativo) + Fill Rate (total vendas)
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={() => setVisao('categorias')}
              style={{
                fontSize: 11, padding: '5px 12px', borderRadius: 6, border: '1px solid #334155', cursor: 'pointer',
                background: !mostrarSkus ? '#2563eb' : '#1e293b', color: '#fff', fontWeight: 700
              }}
            >
              Ver por Categorias
            </button>
            <button
              onClick={() => setVisao('skus')}
              style={{
                fontSize: 11, padding: '5px 12px', borderRadius: 6, border: '1px solid #334155', cursor: 'pointer',
                background: mostrarSkus ? '#2563eb' : '#1e293b', color: '#fff', fontWeight: 700
              }}
            >
              Ver por SKUs
            </button>
          </div>
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, color: '#e2e8f0' }}>
            <thead>
              <tr style={{ background: '#0f172a', borderBottom: '2px solid #1e293b' }}>
                <th style={{ padding: '10px 14px', textAlign: 'left', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>Descrição</th>
                <th style={{ padding: '10px 14px', textAlign: 'right', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>WMAPE (%)</th>
                <th style={{ padding: '10px 14px', textAlign: 'right', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>BIAS (%)</th>
                <th style={{ padding: '10px 14px', textAlign: 'right', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>Fill Rate (%)</th>
              </tr>
            </thead>
            <tbody>
              {linhas.map((row, idx) => (
                <tr key={row.sku} style={{ background: idx % 2 === 0 ? '#0b1329' : '#030712', borderBottom: '1px solid #1e293b' }}>
                  <td style={{ padding: '9px 14px', fontWeight: 700, color: '#f1f5f9' }}>
                    {row.descricao}
                  </td>
                  <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 800, color: row.wmape != null && row.wmape <= 20 ? '#34d399' : row.wmape != null && row.wmape <= 35 ? '#fbbf24' : '#f87171' }}>
                    {row.wmape != null ? row.wmape.toFixed(2) : '—'}
                  </td>
                  <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 800, color: row.bias != null && Math.abs(row.bias) <= 10 ? '#34d399' : row.bias != null && row.bias > 0 ? '#f87171' : '#60a5fa' }}>
                    {row.bias != null ? (row.bias > 0 ? `+${row.bias.toFixed(2)}` : row.bias.toFixed(2)) : '—'}
                  </td>
                  <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 800, color: row.fillRate != null && row.fillRate >= 95 ? '#34d399' : row.fillRate != null && row.fillRate >= 85 ? '#fbbf24' : '#f87171' }}>
                    {row.fillRate != null ? row.fillRate.toFixed(1) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // Visão por Categorias
  const fillMap = new Map(fillCat.map((item: any) => [item.categoria, item]));
  const diagMap = new Map(diagCat.map((item: any) => [item.categoria, item]));
  const catsUnicas = Array.from(new Set([...fillMap.keys(), ...diagMap.keys()]));

  const linhasCat = catsUnicas.map((cKey) => {
    const fItem = fillMap.get(cKey);
    const dItem = diagMap.get(cKey);
    return {
      categoria: cKey,
      wmape: dItem?.wmape_h ?? null,
      bias: dItem?.bias_h ?? null,
      fillRate: fItem?.atendimento ?? null,
    };
  });

  linhasCat.sort((a, b) => (b.wmape ?? -1) - (a.wmape ?? -1));

  return (
    <div style={{ background: '#0a0f1d', borderRadius: 12, padding: 18, border: '1px solid #1e293b' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 900, color: '#f8fafc' }}>
            Consolidado dos 3 Indicadores por Categoria
          </div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>
            Exibindo {linhasCat.length} categorias consolidadas · Selecione uma categoria no filtro acima para ver os SKUs
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            onClick={() => setVisao('categorias')}
            style={{
              fontSize: 11, padding: '5px 12px', borderRadius: 6, border: '1px solid #334155', cursor: 'pointer',
              background: '#2563eb', color: '#fff', fontWeight: 700
            }}
          >
            Ver por Categorias
          </button>
          <button
            onClick={() => setVisao('skus')}
            style={{
              fontSize: 11, padding: '5px 12px', borderRadius: 6, border: '1px solid #334155', cursor: 'pointer',
              background: '#1e293b', color: '#fff', fontWeight: 700
            }}
          >
            Ver por SKUs
          </button>
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, color: '#e2e8f0' }}>
          <thead>
            <tr style={{ background: '#0f172a', borderBottom: '2px solid #1e293b' }}>
              <th style={{ padding: '10px 14px', textAlign: 'left', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>Categoria</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>WMAPE (%)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>BIAS (%)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', color: '#a5b4fc', fontWeight: 800, fontSize: 11, letterSpacing: '.03em' }}>Fill Rate (%)</th>
            </tr>
          </thead>
          <tbody>
            {linhasCat.map((row, idx) => (
              <tr key={row.categoria} style={{ background: idx % 2 === 0 ? '#0b1329' : '#030712', borderBottom: '1px solid #1e293b' }}>
                <td style={{ padding: '9px 14px', fontWeight: 700, color: '#f1f5f9' }}>
                  {row.categoria}
                </td>
                <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 800, color: row.wmape != null && row.wmape <= 20 ? '#34d399' : row.wmape != null && row.wmape <= 35 ? '#fbbf24' : '#f87171' }}>
                  {row.wmape != null ? row.wmape.toFixed(2) : '—'}
                </td>
                <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 800, color: row.bias != null && Math.abs(row.bias) <= 10 ? '#34d399' : row.bias != null && row.bias > 0 ? '#f87171' : '#60a5fa' }}>
                  {row.bias != null ? (row.bias > 0 ? `+${row.bias.toFixed(2)}` : row.bias.toFixed(2)) : '—'}
                </td>
                <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 800, color: row.fillRate != null && row.fillRate >= 95 ? '#34d399' : row.fillRate != null && row.fillRate >= 85 ? '#fbbf24' : '#f87171' }}>
                  {row.fillRate != null ? row.fillRate.toFixed(1) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// MATRIZ MÊS A MÊS (WMAPE, BIAS, FILL RATE)
// ═══════════════════════════════════════════════════════════════════════════
function MatrizCategoriaMes({ matriz, metrica = 'wmape' }: { matriz: any; metrica?: 'wmape'|'bias'|'fill_rate' }) {
  const categorias: string[] = matriz?.categorias || [];
  const meses: string[]      = matriz?.meses || [];
  const dados: Record<string, Record<string, number|null>> = matriz?.[metrica] || {};
  const nomes: Record<string, string> = matriz?.nomes || {};
  const rotulo = matriz?.dimensao === 'sku' ? 'Produto' : 'Categoria';

  if (!categorias.length || !meses.length)
    return <div style={{ padding:32, textAlign:'center', color:'#94a3b8', fontSize:13 }}>Sem dados.</div>;

  const corCelula = (v: number|null) => {
    if (v == null) return { bg:'#f8fafc', fg:'#cbd5e1' };

    if (metrica === 'fill_rate') {
      if (v >= 95) return { bg: '#dcfce7', fg: '#065f46' };
      if (v >= 85) return { bg: '#fef9c3', fg: '#854d0e' };
      return { bg: '#fecaca', fg: '#991b1b' };
    }

    if (metrica === 'bias') {
      const abs = Math.abs(v);
      if (abs <= 10) return { bg:'#dcfce7', fg:'#065f46' };
      if (v > 0) return abs <= 35
        ? { bg:'#ffedd5', fg:'#9a3412' }
        : { bg:'#fecaca', fg:'#991b1b' };
      return abs <= 35
        ? { bg:'#dbeafe', fg:'#1e40af' }
        : { bg:'#bfdbfe', fg:'#1e3a8a' };
    }

    // WMAPE
    if (v <= 20) return { bg:'#dcfce7', fg:'#065f46' };
    if (v <= 35) return { bg:'#fef9c3', fg:'#854d0e' };
    if (v <= 60) return { bg:'#fed7aa', fg:'#9a3412' };
    return { bg:'#fecaca', fg:'#991b1b' };
  };

  return (
    <div style={{ overflowX:'auto' }}>
      <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
        <thead>
          <tr>
            <th style={{ padding:'10px 14px', textAlign:'left', fontSize:11, fontWeight:800,
              textTransform:'uppercase', letterSpacing:'.04em', background:'#1e3a5f', color:'#fff',
              position:'sticky', left:0, zIndex:1 }}>{rotulo}</th>
            {meses.map(m => (
              <th key={m} style={{ padding:'10px 12px', textAlign:'center', fontSize:11, fontWeight:800,
                background:'#1e3a5f', color:'#fff', whiteSpace:'nowrap' }}>{lMes(m)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {categorias.map((cat, i) => (
            <tr key={cat} style={{ background: i%2 ? '#f8fafc' : '#fff' }}>
              <td style={{ padding:'7px 14px', fontWeight:700, color:'#334155', whiteSpace:'nowrap',
                position:'sticky', left:0, background: i%2 ? '#f8fafc' : '#fff', borderRight:'1px solid #e2e8f0' }}>
                <span title={nomes[cat] || cat}>{nomes[cat] || cat}</span>
              </td>
              {meses.map(m => {
                const v = dados[cat]?.[m];
                const { bg, fg } = corCelula(v ?? null);
                return (
                  <td key={m} style={{ padding:'7px 10px', textAlign:'center', fontWeight:700,
                    background:bg, color:fg }}>
                    {v == null ? '—' : (metrica === 'fill_rate' ? `${v.toFixed(1)}%` : `${v.toFixed(0)}%`)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// ABA COMPARATIVO YOY
// ═══════════════════════════════════════════════════════════════════════════
function AbaComparativoYoY({ qs, ctx }: { qs: string; ctx: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState('');

  useEffect(() => {
    setLoading(true); setErro('');
    axios.get(`/api/v1/kpis/comparativo-yoy?${qs}`)
      .then(r => setData(r.data))
      .catch(e => setErro(e?.response?.data?.detail || 'Erro ao carregar comparativo YoY.'))
      .finally(() => setLoading(false));
  }, [qs]);

  if (loading) {
    return (
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: 48, textAlign: 'center' }}>
        <Loader2 style={{ width: 24, color: '#2563eb', margin: '0 auto 12px' }} className="spin" />
        <div style={{ fontSize: 13, fontWeight: 700, color: '#334155' }}>Calculando comparativo YoY…</div>
      </div>
    );
  }

  if (erro) {
    return (
      <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 14, padding: 20, color: '#991b1b', fontSize: 13 }}>
        {erro}
      </div>
    );
  }

  const dados = data?.dados || [];
  const rotulo = data?.periodo_rotulo || 'Período Selecionado';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 14, padding: '16px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 900, color: '#1e3a8a', display: 'flex', alignItems: 'center', gap: 8 }}>
            <TrendingUp style={{ width: 18, color: '#2563eb' }} />
            Comparativo Ano a Ano (YoY) — {rotulo}
          </div>
          <div style={{ fontSize: 12, color: '#3b82f6', marginTop: 4 }}>
            Comparação do mesmo recorte de meses ({rotulo}) ao longo dos anos para o escopo: <b>{ctx}</b>.
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
        {dados.map((item: any) => (
          <div key={item.ano} style={{ background: '#fff', borderRadius: 14, border: '1px solid #e2e8f0', padding: '16px 18px', boxShadow: '0 2px 4px rgba(0,0,0,.02)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, paddingBottom: 6, borderBottom: '1px solid #f1f5f9' }}>
              <span style={{ fontSize: 16, fontWeight: 900, color: '#0f172a' }}>{item.ano}</span>
              <span style={{ fontSize: 10, fontWeight: 800, color: '#64748b', background: '#f1f5f9', padding: '2px 8px', borderRadius: 6 }}>{rotulo}</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12.5 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>WMAPE:</span>
                <span style={{ fontWeight: 800, color: corWmape(item.wmape) }}>{pct(item.wmape)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>BIAS:</span>
                <span style={{ fontWeight: 800, color: corBias(item.bias) }}>{sinal(item.bias)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Fill Rate:</span>
                <span style={{ fontWeight: 800, color: item.fill_rate >= 95 ? '#059669' : '#e11d48' }}>{pct(item.fill_rate)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', paddingTop: 6, borderTop: '1px dashed #f1f5f9', fontSize: 11 }}>
                <span style={{ color: '#94a3b8' }}>Volume Venda:</span>
                <span style={{ fontWeight: 700, color: '#334155' }}>{num(item.pedido)} cx</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: 14 }}>
        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: 18 }}>
          <div style={{ fontWeight: 800, fontSize: 13, color: '#0f172a', marginBottom: 14 }}>
            WMAPE YoY (%) — Quanto menor, mais preciso
          </div>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={dados} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
              <XAxis dataKey="ano" tick={{ fontSize: 11, fontWeight: 700 }} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={v => `${v}%`} />
              <Tooltip formatter={(v: any) => [`${v}%`, 'WMAPE']} />
              <ReferenceLine y={20} stroke="#059669" strokeDasharray="4 2">
                <Label value="Meta 20%" position="top" fill="#059669" fontSize={9} />
              </ReferenceLine>
              <Bar dataKey="wmape" name="WMAPE (%)" radius={[6, 6, 0, 0]}>
                {dados.map((entry: any) => (
                  <Cell key={entry.ano} fill={corWmape(entry.wmape)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', padding: 18 }}>
          <div style={{ fontWeight: 800, fontSize: 13, color: '#0f172a', marginBottom: 14 }}>
            Fill Rate YoY (%) — Quanto maior, melhor a execução
          </div>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={dados} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
              <XAxis dataKey="ano" tick={{ fontSize: 11, fontWeight: 700 }} />
              <YAxis domain={[70, 100]} tick={{ fontSize: 10 }} tickFormatter={v => `${v}%`} />
              <Tooltip formatter={(v: any) => [`${v}%`, 'Fill Rate']} />
              <ReferenceLine y={95} stroke="#059669" strokeDasharray="4 2">
                <Label value="Meta 95%" position="top" fill="#059669" fontSize={9} />
              </ReferenceLine>
              <Bar dataKey="fill_rate" name="Fill Rate (%)" radius={[6, 6, 0, 0]}>
                {dados.map((entry: any) => (
                  <Cell key={entry.ano} fill={entry.fill_rate >= 95 ? '#059669' : entry.fill_rate >= 85 ? '#d97706' : '#e11d48'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', overflow: 'hidden' }}>
        <div style={{ padding: '14px 18px', fontWeight: 800, fontSize: 13, borderBottom: '1px solid #f1f5f9', background: '#fafafa' }}>
          Tabela Comparativa Ano a Ano ({rotulo})
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ background: '#f8fafc' }}>
              <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>Ano</th>
              <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>Período</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>Pedido (cx)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>Faturado (cx)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>Corte (cx)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>WMAPE (%)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>BIAS (%)</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#94a3b8' }}>Fill Rate (%)</th>
            </tr>
          </thead>
          <tbody>
            {dados.map((row: any, i: number) => (
              <tr key={row.ano} style={{ background: i % 2 ? '#f9fafb' : '#fff', borderBottom: '1px solid #f1f5f9' }}>
                <td style={{ padding: '10px 14px', fontWeight: 900, color: '#0f172a' }}>{row.ano}</td>
                <td style={{ padding: '10px 14px', color: '#64748b' }}>{rotulo}</td>
                <td style={{ padding: '10px 14px', textAlign: 'right', color: '#334155', fontWeight: 600 }}>{num(row.pedido)}</td>
                <td style={{ padding: '10px 14px', textAlign: 'right', color: '#059669', fontWeight: 700 }}>{num(row.entregue)}</td>
                <td style={{ padding: '10px 14px', textAlign: 'right', color: '#e11d48', fontWeight: 700 }}>{num(row.corte)}</td>
                <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 800, color: corWmape(row.wmape) }}>{pct(row.wmape)}</td>
                <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 800, color: corBias(row.bias) }}>{sinal(row.bias)}</td>
                <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 800, color: row.fill_rate >= 95 ? '#059669' : '#e11d48' }}>{pct(row.fill_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
  const [diagCat, setDiagCat]     = useState<any[]>([]);
  const [matrizCat, setMatrizCat] = useState<any>(null);
  const [loading, setLoading]     = useState(false);
  const [erro, setErro]           = useState('');
  
  // NAVEGAÇÃO
  const [abaKpi, setAbaKpi]       = useState<'acuracia'|'tabelas'|'comparativo'|'agente'>('acuracia');
  const [abaTabela, setAbaTabela] = useState<'wmape'|'super'|'sub'|'ranking_fillrate'>('wmape');
  const [subAbaTabelas, setSubAbaTabelas] = useState<'consolidado'|'wmape'|'bias'|'fillrate'>('consolidado');
  const [unidade, setUnidade]     = useState<'cx'|'rs'>('cx');
  
  // AGENTE
  const [relatorio, setRelatorio]   = useState<string>('');
  const [gerandoRel, setGerandoRel] = useState(false);
  const [baixandoPdf, setBaixandoPdf] = useState(false);
  const [erroAgente, setErroAgente] = useState('');
  
  // FILL RATE
  const [fillRate, setFillRate]    = useState<any>(null);
  const [fillDiag, setFillDiag]    = useState<{cat: any[], sku: any[]}>({cat:[], sku:[]});
  const [baixando, setBaixando]    = useState(false);

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
      const [ev, dg, dgCat, mzCat, fr, frCat, frSku] = await Promise.all([
        axios.get(`/api/v1/kpis/evolucao?${qs}`),
        axios.get(`/api/v1/kpis/diagnostico?${qs}&nivel=sku`),
        axios.get(`/api/v1/kpis/diagnostico?${qs}&nivel=categoria`),
        axios.get(`/api/v1/kpis/matriz-categoria?${qs}`),
        axios.get(`/api/v1/kpis/fill-rate?${qs}&nivel=evolucao`),
        axios.get(`/api/v1/kpis/fill-rate?${qs}&nivel=categoria`),
        axios.get(`/api/v1/kpis/fill-rate?${qs}&nivel=sku`),
      ]);
      setEvolucao(ev.data);
      setDiag(dg.data?.itens || []);
      setDiagCat(dgCat.data?.itens || []);
      setMatrizCat(mzCat.data || null);
      setFillRate(fr.data);
      setFillDiag({ cat: frCat.data?.itens || [], sku: frSku.data?.itens || [] });
    } catch(e: any) {
      setErro(e?.response?.data?.detail || 'Erro ao carregar.');
      setEvolucao(null); setDiag([]); setMatrizCat(null);
    } finally { setLoading(false); }
  }, [qs]);

  useEffect(()=>{ carregar(); }, [carregar]);

  const exportarExcel = async () => {
    if (!mesesSel.length) return;
    setBaixando(true);
    try {
      const p = new URLSearchParams();
      mesesSel.forEach(m=>p.append('meses',m));
      if (categoria) p.set('categoria',categoria);
      const r = await axios.get(`/api/v1/kpis/exportar?${p.toString()}`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `KPIs_FillRate_WMAPE_${mesesSel[0]}_${mesesSel[mesesSel.length-1]}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {}
    finally { setBaixando(false); }
  };

  const serie  = evolucao?.serie  || [];
  const resumo = evolucao?.resumo || {};
  const temIA  = serie.some((d: any)=>d.wmape_ia!=null);
  const skusFilt = opts.skus.filter((s: any)=>!categoria||s.categoria===categoria);
  const ctx = sku?`SKU ${sku}`:categoria?`Categoria: ${categoria}`:'Portfólio completo';

  // Combinando série de WMAPE/BIAS com a série de Fill Rate
  const serieCombinada = useMemo(() => {
    if (!serie.length) return [];
    const fillMap = new Map((fillRate?.serie || []).map((f: any) => [f.mes, f.atendimento]));
    return serie.map((item: any) => ({
      ...item,
      fill_rate: fillMap.get(item.mes) ?? null,
    }));
  }, [serie, fillRate]);

  // Agente
  const gerarRelatorio = useCallback(async () => {
    if (!mesesSel.length) { setErroAgente('Selecione ao menos um mês.'); return; }
    setGerandoRel(true); setErroAgente(''); setRelatorio('');
    try {
      const r = await axios.get(`/api/v1/kpis/agente/relatorio?${qs}`);
      setRelatorio(r.data.relatorio || '');
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

        {/* ABAS KPI PRINCIPAIS */}
        <div style={{ display:'flex', gap:4, marginBottom:16, borderBottom:'2px solid #f1f5f9', alignItems:'center' }}>
          {([
            ['acuracia','📊 KPIs (Acurácia)'],
            ['tabelas','📋 Tabelas'],
            ['comparativo','📈 Comparativo (YoY)'],
            ['agente','✨ Relatório IA']
          ] as const).map(([id, label]) => (
            <button key={id} onClick={() => setAbaKpi(id as any)}
              style={{ padding:'8px 20px', fontSize:13, fontWeight:800, border:'none', background:'none',
                cursor:'pointer', borderBottom: abaKpi===id ? '2px solid #2563eb' : '2px solid transparent',
                color: abaKpi===id ? '#2563eb' : '#94a3b8', marginBottom:-2 }}>
              {label}
            </button>
          ))}

          {/* Toggle de Unidade (Caixas / Reais) */}
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

          <button onClick={exportarExcel} disabled={baixando || !mesesSel.length}
            style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:6, padding:'8px 14px',
              fontSize:12, fontWeight:800, border:'1px solid #d1d5db', borderRadius:10, background:'#fff',
              cursor: baixando ? 'default' : 'pointer', color:'#334155', alignSelf:'flex-end' }}>
            {baixando ? <Loader2 style={{ width:14 }} className="spin"/> : '⬇'} Exportar Excel
          </button>

          {loading && <Loader2 style={{ width:16, color:'#94a3b8', alignSelf:'center' }} className="spin"/>}
          {erro && <span style={{ color:'#e11d48', fontSize:12, alignSelf:'center' }}>{erro}</span>}
        </div>

        {/* ── ABA 1: KPIs (ACURÁCIA) ── */}
        {abaKpi === 'acuracia' && <>

          {/* BANNER INFORMATIVO */}
          <div style={{
            background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 12,
            padding: '10px 16px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10,
            fontSize: 12, color: '#1e40af', fontWeight: 600
          }}>
            <Info style={{ width: 18, color: '#2563eb', flexShrink: 0 }} />
            <span>
              <b>Nota de metodologia:</b> O <b>Fill Rate</b> considera a totalidade das vendas (incluindo produtos históricos/desativados). <b>WMAPE</b>, <b>BIAS</b> e <b>FVA</b> consideram exclusivamente o portfólio ativo.
            </span>
          </div>

          {/* CARDS UNIFICADOS (ACURÁCIA + FILL RATE) */}
          <div style={{ display:'flex', gap:12, marginBottom:16, flexWrap:'wrap' }}>
            {resumo.wmape_h != null && (
              <>
                <Card label="WMAPE Humano" valor={resumo.wmape_h} cor={corWmape(resumo.wmape_h)}
                  sub={`Acurácia ${pct(100-resumo.wmape_h)}`} />
                <Card label="Bias Humano" valor={resumo.bias_h} cor={corBias(resumo.bias_h)} ehSinal
                  sub={(resumo.bias_h||0)>5?'▲ Superestimando':(resumo.bias_h||0)<-5?'▼ Subestimando':'✓ Equilibrado'} />
              </>
            )}

            {/* CARD FILL RATE */}
            {fillRate?.resumo?.atendimento != null && (
              <Card label="Fill Rate (Atendimento)" valor={fillRate.resumo.atendimento}
                cor={(fillRate.resumo.atendimento ?? 0) >= 95 ? '#059669' : (fillRate.resumo.atendimento ?? 0) >= 85 ? '#d97706' : '#e11d48'}
                sub={`${num(fillRate.resumo.entregue)} de ${num(fillRate.resumo.pedido)} ${unidade==='rs'?'R$':'cx'}`} />
            )}

            {/* CARD CORTE TOTAL */}
            {fillRate?.resumo?.corte != null && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', padding:'14px 18px', flex:1, minWidth:130 }}>
                <div style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.06em', color:'#94a3b8', marginBottom:8 }}>Corte Total</div>
                <div style={{ fontSize:22, fontWeight:900, color:'#e11d48', lineHeight:1 }}>
                  {unidade==='rs' ? 'R$ ' + num(fillRate.resumo.corte) : num(fillRate.resumo.corte) + ' cx'}
                </div>
                <div style={{ fontSize:11, color:'#94a3b8', marginTop:6 }}>Volume não atendido</div>
              </div>
            )}

            {temIA && (
              <>
                <Card label="WMAPE IA" valor={resumo.wmape_ia} cor={corWmape(resumo.wmape_ia)}
                  sub={`${resumo.meses_com_ia||0} meses`} />
                <Card label="FVA" valor={resumo.fva} ehSinal
                  cor={(resumo.fva||0)<0?'#059669':(resumo.fva||0)>0?'#e11d48':'#94a3b8'}
                  sub={(resumo.fva||0)<0?'IA mais precisa':(resumo.fva||0)>0?'Humano mais preciso':'—'} />
              </>
            )}

            <div style={{ background:'#f0f9ff', border:'1px solid #bae6fd', borderRadius:14,
              padding:'14px 18px', flex:1, minWidth:150 }}>
              <div style={{ fontSize:10, fontWeight:800, textTransform:'uppercase', letterSpacing:'.06em', color:'#0284c7', marginBottom:6 }}>Período</div>
              <div style={{ fontSize:13, fontWeight:700 }}>{mesesSel.length} meses</div>
              {mesesSel.length>0 && (
                <div style={{ fontSize:11, color:'#64748b', marginTop:3 }}>
                  {lMes(mesesSel[0])} → {lMes(mesesSel[mesesSel.length-1])}
                </div>
              )}
            </div>
          </div>

          {/* GRÁFICOS (WMAPE, FILL RATE, BIAS, FVA) */}
          {serieCombinada.length > 0 ? (
            <div style={{ display:'flex', flexDirection:'column', gap:14, marginBottom:20 }}>

              {/* 1. GRÁFICO WMAPE */}
              <Grafico dados={serieCombinada} chaveH="wmape_h" chaveIA="wmape_ia" titulo="WMAPE (%) — Acurácia da Previsão"
                legendaRefs={[
                  { y:20, cor:'#059669', label:'Meta — abaixo de 20% a acurácia é adequada para o plano tático' },
                  { y:35, cor:'#e11d48', label:'Crítico — acima de 35% o plano não tem base confiável de previsão' },
                ]} />

              {/* 2. GRÁFICO FILL RATE (REPOSICIONADO ABAIXO DO WMAPE) */}
              <Grafico dados={serieCombinada} chaveH="fill_rate" chaveIA={undefined} titulo="Fill Rate (%) — Atendimento mês a mês"
                corLinha="#059669"
                legendaRefs={[
                  { y:95, cor:'#059669', label:'Meta — 95% de atendimento da demanda' },
                  { y:85, cor:'#e11d48', label:'Crítico — abaixo de 85% nível de serviço comprometido' },
                ]} />

              {/* 3. GRÁFICO BIAS */}
              <Grafico dados={serieCombinada} chaveH="bias_h" chaveIA="bias_ia" titulo="BIAS (%) — Viés de Tendência"
                refZero
                legendaRefs={[
                  { y: 10, cor:'#e11d48', label:'Limite de atenção positivo — acima de +10% há superestimativa sistemática' },
                  { y:-10, cor:'#2563eb', label:'Limite de atenção negativo — abaixo de -10% há subestimativa sistemática' },
                ]} />

              {/* 4. GRÁFICO FVA */}
              {temIA && (
                <Grafico dados={serieCombinada} chaveH={undefined} chaveIA="fva" titulo="FVA — Forecast Value Added (%)"
                  refZero
                  legendaRefs={[
                    { y:0, cor:'#94a3b8', label:'Zero — FVA negativo significa que a IA foi mais precisa que o humano' },
                  ]} />
              )}
            </div>
          ) : !loading && (
            <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9',
              padding:48, textAlign:'center', color:'#94a3b8', fontSize:13, marginBottom:20 }}>
              {mesesSel.length===0?'Selecione pelo menos um mês.':'Sem dados para os filtros selecionados.'}
            </div>
          )}

          {/* TABELA DE RANKINGS (WMAPE, SUPERESTIMANDO, SUBESTIMANDO, RANKING FILL RATE) */}
          {(diag.length > 0 || fillDiag.sku.length > 0) && (
            <div style={{ background:'#fff', borderRadius:14, border:'1px solid #f1f5f9', overflow:'hidden' }}>
              <div style={{ display:'flex', borderBottom:'1px solid #f1f5f9', padding:'0 18px', background:'#fafafa', flexWrap:'wrap' }}>
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
                <button style={styleTab(abaTabela==='ranking_fillrate')} onClick={()=>setAbaTabela('ranking_fillrate')}>
                  📦 Ranking Fill Rate ({fillDiag.sku.length})
                </button>
              </div>

              {/* Legenda da aba */}
              <div style={{ padding:'10px 18px', background:'#fafafa', borderBottom:'1px solid #f1f5f9', fontSize:11, color:'#64748b' }}>
                {abaTabela==='wmape' && '↑ Ordenado do maior erro WMAPE para o menor (ponderado por volume).'}
                {abaTabela==='super' && '↑ Ordenado por maior excesso absoluto de caixas. Frequência em vermelho = erro em ≥ 70% dos meses.'}
                {abaTabela==='sub'   && '↑ Ordenado por maior falta absoluta de caixas. Frequência em vermelho = risco de ruptura recorrente.'}
                {abaTabela==='ranking_fillrate' && '↑ Ordenado pelo maior volume cortado (ruptura de atendimento no ERP).'}
              </div>

              <div style={{ padding:'0 0 4px' }}>
                {abaTabela==='wmape' && <TabelaWmape itens={diag} />}
                {abaTabela==='super' && <TabelaBias  itens={diag} modo="super" />}
                {abaTabela==='sub'   && <TabelaBias  itens={diag} modo="sub" />}
                {abaTabela==='ranking_fillrate' && <TabelaRankingFillRate itens={fillDiag.sku} unidade={unidade} />}
              </div>
            </div>
          )}

          {/* Rodapé metodológico */}
          <div style={{ fontSize:10, color:'#94a3b8', lineHeight:1.8, padding:'12px 4px' }}>
            <b>WMAPE</b> = SUM(|prev−real|)/SUM(real): ponderado por volume. &nbsp;
            <b>BIAS</b> = (SUM(prev)−SUM(real))/SUM(real): positivo = superestimou, negativo = subestimou. &nbsp;
            <b>Fill Rate</b> = SUM(faturado)/SUM(pedido): considera a totalidade das vendas na base. &nbsp;
            Portfólio ativo para acurácia · Meses fechados.
          </div>
        </>}

        {/* ── ABA 2: TABELAS ── */}
        {abaKpi === 'tabelas' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            
            {/* SUB-NAVEGAÇÃO DAS TABELAS */}
            <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e2e8f0', padding: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {[
                ['consolidado', '📊 Consolidado (3 Indicadores)'],
                ['wmape', '🎯 WMAPE Mês a Mês'],
                ['bias', '⚖️ BIAS Mês a Mês'],
                ['fillrate', '📦 Fill Rate Mês a Mês'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setSubAbaTabelas(id as any)}
                  style={{
                    padding: '8px 16px', fontSize: 12.5, fontWeight: 800, border: 'none', borderRadius: 8,
                    cursor: 'pointer',
                    background: subAbaTabelas === id ? '#2563eb' : 'transparent',
                    color: subAbaTabelas === id ? '#fff' : '#64748b',
                  }}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* CONTEÚDO DA SUB-ABA */}
            {subAbaTabelas === 'consolidado' && (
              <TabelaConsolidada3Indicadores
                diagCat={diagCat}
                fillCat={fillDiag.cat}
                diagSku={diag}
                fillSku={fillDiag.sku}
                categoriaFiltro={categoria}
              />
            )}

            {subAbaTabelas === 'wmape' && (
              <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', overflow: 'hidden' }}>
                <div style={{ padding: '14px 18px', fontWeight: 800, fontSize: 13, borderBottom: '1px solid #f1f5f9', background: '#fafafa' }}>
                  WMAPE por {matrizCat?.dimensao === 'sku' ? 'produto' : 'categoria'} — mês a mês (%)
                </div>
                <MatrizCategoriaMes matriz={matrizCat} metrica="wmape" />
              </div>
            )}

            {subAbaTabelas === 'bias' && (
              <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', overflow: 'hidden' }}>
                <div style={{ padding: '14px 18px', fontWeight: 800, fontSize: 13, borderBottom: '1px solid #f1f5f9', background: '#fafafa' }}>
                  BIAS por {matrizCat?.dimensao === 'sku' ? 'produto' : 'categoria'} — mês a mês (%)
                </div>
                <MatrizCategoriaMes matriz={matrizCat} metrica="bias" />
              </div>
            )}

            {subAbaTabelas === 'fillrate' && (
              <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #f1f5f9', overflow: 'hidden' }}>
                <div style={{ padding: '14px 18px', fontWeight: 800, fontSize: 13, borderBottom: '1px solid #f1f5f9', background: '#fafafa' }}>
                  Fill Rate por {matrizCat?.dimensao === 'sku' ? 'produto' : 'categoria'} — mês a mês (%)
                </div>
                <MatrizCategoriaMes matriz={matrizCat} metrica="fill_rate" />
              </div>
            )}

            <div style={{ fontSize: 10, color: '#94a3b8', padding: '0 4px' }}>
              WMAPE: verde ≤ 20% · amarelo ≤ 35% · vermelho &gt; 35%. BIAS: verde entre -10% e +10% · azul = subestimativa · vermelho = superestimativa. Fill Rate: verde ≥ 95% · amarelo ≥ 85% · vermelho &lt; 85%.
            </div>
          </div>
        )}

        {/* ── ABA 3: COMPARATIVO YOY ── */}
        {abaKpi === 'comparativo' && (
          <AbaComparativoYoY qs={qs} ctx={ctx} />
        )}

        {/* ── ABA 4: RELATÓRIO IA ── */}
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

            <div style={{ background:'#faf5ff', borderRadius:14, border:'1px dashed #d8b4fe',
              padding:'20px 24px', display:'flex', alignItems:'center', gap:14 }}>
              <Sparkles style={{ width:22, color:'#7c3aed', flexShrink:0 }} />
              <div>
                <div style={{ fontSize:12.5, fontWeight:900, color:'#334155' }}>
                  Perguntas sobre os indicadores no Nexus Bot
                </div>
                <div style={{ fontSize:11.5, color:'#7c3aed', marginTop:3 }}>
                  Abra o Nexus Bot no canto inferior direito e selecione o modo "Indicadores".
                </div>
              </div>
            </div>

            {!relatorio && !gerandoRel && (
              <div style={{ background:'#fff', borderRadius:14, border:'1px dashed #e2e8f0',
                padding:'52px 24px', textAlign:'center', marginTop: 16 }}>
                <Sparkles style={{ width:30, color:'#c4b5fd', margin:'0 auto 14px' }} />
                <div style={{ fontSize:14, fontWeight:800, color:'#334155', marginBottom:6 }}>
                  Análise completa dos indicadores
                </div>
                <div style={{ fontSize:12, color:'#94a3b8', maxWidth:520, margin:'0 auto', lineHeight:1.7 }}>
                  O agente cruza WMAPE, BIAS, fill rate e evolução YoY de todas as categorias e SKUs,
                  identifica padrões sistemáticos e separa falhas de planejamento de falhas de operação.
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
