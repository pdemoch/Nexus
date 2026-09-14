import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  ChevronRight, ChevronDown, Save, X, Lock, Unlock,
  Loader2, LineChart as LineIcon, LayoutGrid, ClipboardList, Search, BarChart2,
  ArrowLeft, AlertTriangle,
} from 'lucide-react';
import VisaoGeralMarketing from './VisaoGeralMarketing';
import DossieInferior from './Dossieinferior';

/* =====================================================================
   METAS COMERCIAL
   Hierarquia: Gerente → Coordenador → Executivo → SKU → Cliente/Razão
   Quem preenche: Coordenador (edita no nível SKU, rateio automático
                  para clientes em tempo real; pode também editar cliente
                  individualmente como override).
   Quem trava: Gerente ou Administrador (trancam os coordenadores).
   Dossiê: gaveta lateral, vinculado ao SKU (com filtro por executivo
           no nível SKU, ou por razão social no nível cliente).
   ===================================================================== */

const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
const fmtRs = (n: number) => new Intl.NumberFormat('pt-BR', {
  style: 'currency', currency: 'BRL', maximumFractionDigits: 0,
}).format(Math.round(n || 0));

function CampoNumeroEditavel({
  value,
  format,
  onChange,
  onBlur,
  disabled,
  className,
  title,
}: {
  value: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
  onBlur?: () => void;
  disabled?: boolean;
  className?: string;
  title?: string;
}) {
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState('');

  const valueRounded = Math.round(value || 0);
  const displayValue = focused ? draft : format(valueRounded);

  return (
    <input
      type="text"
      value={displayValue}
      disabled={disabled}
      title={title}
      inputMode="numeric"
      onFocus={(e) => {
        const input = e.currentTarget;
        setFocused(true);
        setDraft(valueRounded === 0 ? '' : String(valueRounded));
        window.setTimeout(() => input.select(), 0);
      }}
      onChange={(e) => {
        const digits = e.target.value.replace(/\D/g, '');
        setDraft(digits);
        onChange(digits ? parseInt(digits, 10) : 0);
      }}
      onBlur={() => {
        setFocused(false);
        setDraft('');
        onBlur?.();
      }}
      className={className}
    />
  );
}

const mesLabel = (iso: string) => {
  const [y, m] = iso.split('-');
  const nomes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  return `${nomes[parseInt(m)-1]}/${y.slice(2)}`;
};

const editKeyOf = (
  gerente: string,
  coordenador: string,
  vendedor: string,
  razao: string,
  sku: string,
  mes: string
) => `${gerente}||${coordenador}||${vendedor}||${razao}||${sku}||${mes}`;

/* Rateio por maior resto — igual ao backend */
function ratearMaiorResto(total: number, pesos: number[]): number[] {
  const soma = pesos.reduce((a, b) => a + b, 0);
  if (soma === 0 || total === 0) {
    // distribuição uniforme quando sem histórico
    const base = Math.floor(total / pesos.length);
    const resto = total - base * pesos.length;
    return pesos.map((_, i) => base + (i < resto ? 1 : 0));
  }
  const exatos = pesos.map(p => (p / soma) * total);
  const inteiros = exatos.map(Math.floor);
  const sobra = total - inteiros.reduce((a, b) => a + b, 0);
  const restos = exatos.map((e, i) => ({ idx: i, r: e - inteiros[i] }))
    .sort((a, b) => b.r - a.r);
  for (let i = 0; i < sobra; i++) inteiros[restos[i].idx]++;
  return inteiros;
}

function ratearVolumesPorValor(
  valorAlvo: number,
  folhas: Array<{ pmv: number; pesoBase: number }>
): number[] {
  const alvo = Math.max(0, Math.round(valorAlvo || 0));
  if (!folhas.length || alvo === 0) return folhas.map(() => 0);

  const pesosValor = folhas.map(f => Math.max(0, f.pesoBase) * f.pmv);
  const somaPesos = pesosValor.reduce((s, p) => s + p, 0);
  const cotasValor = folhas.map((_, i) =>
    somaPesos > 0 ? (alvo * pesosValor[i]) / somaPesos : alvo / folhas.length
  );

  const volumes = cotasValor.map((cota, i) =>
    folhas[i].pmv > 0 ? Math.max(0, Math.floor(cota / folhas[i].pmv)) : 0
  );

  const valorAtual = () =>
    volumes.reduce((s, v, i) => s + v * folhas[i].pmv, 0);
  const erroAtual = () => Math.abs(alvo - valorAtual());

  let erro = erroAtual();
  for (let iter = 0; iter < 10000; iter++) {
    const atual = valorAtual();
    let melhorIdx = -1;
    let melhorDelta = 0;
    let melhorErro = erro;

    folhas.forEach((f, i) => {
      const erroAdd = Math.abs(alvo - (atual + f.pmv));
      if (erroAdd + 0.0001 < melhorErro) {
        melhorIdx = i;
        melhorDelta = 1;
        melhorErro = erroAdd;
      }

      if (volumes[i] > 0) {
        const erroSub = Math.abs(alvo - (atual - f.pmv));
        if (erroSub + 0.0001 < melhorErro) {
          melhorIdx = i;
          melhorDelta = -1;
          melhorErro = erroSub;
        }
      }
    });

    if (melhorIdx < 0) break;
    volumes[melhorIdx] += melhorDelta;
    erro = melhorErro;
  }

  return volumes;
}

const INDENT: Record<string, string> = {
  gerente: 'pl-0', coordenador: 'pl-4', executivo: 'pl-8',
  cliente: 'pl-12', produto: 'pl-16',
};

const pesoRateioBottomUp = (cel: any) => {
  const bottomup = Math.max(0, Number(cel?.bottomup || 0));
  if (bottomup > 0) return bottomup;
  const historico = Math.max(0, Number(cel?.peso_historico || 0));
  if (historico > 0) return historico;
  return Math.max(0, Number(cel?.meta || 0));
};

/* ── COMPONENTE RAIZ ─────────────────────────────────────────────── */
export default function MetasComercial() {
  const [aba, setAba] = useState<'geral' | 'preenchimento' | 'consolidado'>('geral');
  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <div className="shrink-0 bg-white border-b border-slate-200 px-6 pt-3">
        <div className="flex items-center gap-1">
          <button onClick={() => setAba('geral')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors
              ${aba === 'geral' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <LayoutGrid className="w-4 h-4" /> Visão Geral
          </button>
          <button onClick={() => setAba('preenchimento')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors
              ${aba === 'preenchimento' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <ClipboardList className="w-4 h-4" /> Preenchimento
          </button>
          <button onClick={() => setAba('consolidado')}
            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-black rounded-t-lg border-b-2 transition-colors
              ${aba === 'consolidado' ? 'border-violet-600 text-violet-600' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            <BarChart2 className="w-4 h-4" /> Consolidado
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {aba === 'geral' && <VisaoGeralMarketing prefixoApi="/api/v1/carteira" />}
      {aba === 'preenchimento' && <PreenchimentoMetas />}
      {aba === 'consolidado' && <ConsolidadoMetas />}
      </div>
    </div>
  );
}

/* ── ABA PREENCHIMENTO ───────────────────────────────────────────── */
/* Árvore SKU-first: SKU → Coordenador → Executivo → Razão Social.
   Fluxo do Gerente (2 fases, nesta ordem):
     1) SKU          — edita o total (R$/caixas) de cada SKU da empresa.
     2) COORDENADOR  — drill-down por SKU, distribui % entre coordenadores.
   Fluxo do Coordenador (2 fases, nesta ordem, após o Gerente travar COORDENADOR):
     1) EXECUTIVO    — drill-down por SKU, distribui % entre executivos.
     2) RAZAO_SOCIAL — drill-down SKU+Executivo, distribui % entre clientes.
   Edição de %/R$/volume nunca redistribui os irmãos; a soma só é validada
   contra a tolerância de ±1% no momento de travar a fase (POST /travar-fase).
   ------------------------------------------------------------------- */

type Detalhe422 = { chave: string; volume_atual: number; volume_alvo: number; variacao_pct: number };

function PreenchimentoMetas() {
  const [dados,          setDados]          = useState<any>(null);
  const [loading,        setLoading]        = useState(true);
  const [abertoSku,      setAbertoSku]      = useState<string | null>(null);
  const [abertoExecutivo,setAbertoExecutivo]= useState<string | null>(null);
  const [busca,          setBusca]          = useState('');
  const [travando,       setTravando]       = useState(false);
  const [erroCarregamento, setErroCarregamento] = useState<string | null>(null);
  const [adminAlvo,      setAdminAlvo]      = useState<{ nome: string; nivel: string } | null>(null);
  const [dossieAlvo,     setDossieAlvo]     = useState<{
    sku: string; descricao: string; coordenador?: string; vendedor?: string; razao?: string;
  } | null>(null);
  const [erroTravamento, setErroTravamento] = useState<{ mensagem: string; detalhes: Detalhe422[] } | null>(null);

  const carregar = useCallback(async () => {
    setLoading(true);
    setErroCarregamento(null);
    try {
      const params = adminAlvo
        ? { responsavel: adminAlvo.nome, nivel_responsavel: adminAlvo.nivel }
        : {};
      const r = await axios.get('/api/v1/carteira/tabela', { params });
      if (r.data?.simone_monetario) {
        const monetaria = await axios.get('/api/v1/carteira/tabela-monetaria-simone');
        setDados(monetaria.data);
      } else {
        setDados(r.data);
      }
      setErroTravamento(null);
    } catch (e: any) {
      setErroCarregamento(e?.response?.data?.detail || 'Não foi possível carregar a carteira de metas.');
    } finally { setLoading(false); }
  }, [adminAlvo]);
  useEffect(() => { carregar(); }, [carregar]);

  const meses: string[]     = dados?.meses || [];
  const congeladaEtapa      = Boolean(dados?.etapa_congelada);
  const minhaCongelada      = Boolean(dados?.minha_carteira_congelada);
  const aguardandoUpstream  = Boolean(dados?.aguardando_upstream);
  const souAdmin            = dados?.sou_admin === true;
  const adminOperando       = souAdmin && adminAlvo !== null;
  // Quando Admin opera em nome de alguém, a UI deve refletir a fase/papel da
  // pessoa impersonada (Gerente/Coordenador), não "Administrador".
  const funcao: string      = (souAdmin && adminAlvo) ? adminAlvo.nivel : (dados?.funcao || '');
  const aguardandoGerente   = Boolean(dados?.aguardando_gerente) && funcao === 'Coordenador';
  const bloqueado           = congeladaEtapa || minhaCongelada ||
    (aguardandoUpstream && !souAdmin) || (aguardandoGerente && !souAdmin);
  // Admin operando em nome de alguém não deve ser barrado pelo "aguardando" —
  // ele está justamente destravando/depurando aquela alçada.
  const bloqueadoEdicao     = bloqueado && !(souAdmin && adminOperando);

  const responsaveis = (dados?.responsaveis || []) as Array<{ nome: string; nivel: string }>;

  const minhaFase: { fase_atual: string; sku_travado: boolean; coordenador_travado: boolean; executivo_travado: boolean } | null =
    dados?.minha_fase || null;
  const faseAtual = minhaFase?.fase_atual || (funcao === 'Gerente' ? 'SKU' : funcao === 'Coordenador' ? 'EXECUTIVO' : '');

  // Última fase de cada perfil = "travado definitivamente" quando true.
  // Gerente: coordenador_travado só é setado True por travar_fase_final (trava
  // da fase COORDENADOR) — sinal inequívoco de fim de cascata do Gerente.
  // Coordenador: executivo_travado é setado True tanto ao avançar de EXECUTIVO
  // para RAZAO_SOCIAL quanto ao travar definitivamente RAZAO_SOCIAL — não serve
  // sozinho como sinal de "definitivo". O cadeado real (minha_carteira_congelada,
  // via esta_congelado_para_usuario) é o sinal correto de trancamento final.
  const travadoDefinitivo =
    (funcao === 'Gerente' && Boolean(minhaFase?.coordenador_travado)) ||
    (funcao === 'Coordenador' && minhaCongelada);

  const responsavelBody = adminAlvo ? { responsavel_nome: adminAlvo.nome, responsavel_nivel: adminAlvo.nivel } : {};

  const arvoreFiltrada = useMemo(() => {
    const q = busca.trim().toLowerCase();
    const arvore = (dados?.arvore || []) as any[];
    if (!q) return arvore;
    return arvore.filter((sku: any) =>
      sku.sku.toLowerCase().includes(q) || (sku.descricao || '').toLowerCase().includes(q)
    );
  }, [dados, busca]);

  // Cards de totais por mês: soma direto da raiz (SKU já vem com o total
  // agregado de toda a hierarquia abaixo). "Meta Herdada" = bottomup, o
  // valor original vindo da Demanda Comercial antes de qualquer ajuste.
  const totaisPorMes = useMemo(() => {
    const arvore = (dados?.arvore || []) as any[];
    const acc: Record<string, { meta: number; herdada: number; fatMeta: number; fatHerdada: number }> = {};
    meses.forEach(m => { acc[m] = { meta: 0, herdada: 0, fatMeta: 0, fatHerdada: 0 }; });
    arvore.forEach((sku: any) => {
      meses.forEach(m => {
        const cel = sku.meses?.[m]; if (!cel) return;
        const meta = cel.meta || 0;
        const herdada = cel.bottomup || 0;
        const pmv = cel.pmv || 0;
        acc[m].meta += meta;
        acc[m].herdada += herdada;
        acc[m].fatMeta += meta * pmv;
        acc[m].fatHerdada += herdada * pmv;
      });
    });
    return acc;
  }, [dados, meses]);

  /* ── Ação: travar a fase corrente ─────────────────────────────── */
  const travarFase = async () => {
    if (!confirm('Travar esta fase? A soma dos itens será validada contra a tolerância de ±1%.')) return;
    setTravando(true);
    setErroTravamento(null);
    try {
      await axios.post('/api/v1/carteira/travar-fase', responsavelBody);
      setAbertoSku(null); setAbertoExecutivo(null);
      await carregar();
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail?.detalhes) {
        setErroTravamento({ mensagem: detail.mensagem, detalhes: detail.detalhes });
      } else {
        alert(detail || 'Falha ao travar a fase.');
      }
    } finally { setTravando(false); }
  };

  const reabrirFaseAdmin = async () => {
    if (!adminAlvo || !confirm(`Reabrir a carteira de ${adminAlvo.nome} para a fase inicial?`)) return;
    try {
      await axios.post('/api/v1/carteira/reabrir-fase-meta', {
        nome_alvo: adminAlvo.nome, nivel_alvo: adminAlvo.nivel,
      });
      await carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao reabrir a fase.');
    }
  };

  const congelarEtapa = async () => {
    if (!confirm('Aprovar Metas Comercial? A etapa Irrestrita será liberada.')) return;
    try {
      await axios.post('/api/v1/carteira/aprovar-evidencia', { ajustes: [] });
      await carregar();
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail?.coordenadores_pendentes?.length) {
        alert(`${detail.mensagem}\n${detail.coordenadores_pendentes.join('\n')}`);
      } else {
        alert(detail || 'Falha ao aprovar metas.');
      }
    }
  };

  const reabrirEtapa = async () => {
    if (!confirm('Reabrir Metas Comercial?')) return;
    try {
      await axios.post('/api/v1/carteira/reabrir', {});
      await carregar();
    } catch (e: any) { alert(e?.response?.data?.detail || 'Falha ao reabrir.'); }
  };

  /* ── Gerente, fase SKU: edita o total do SKU direto via /salvar
     (rateio por razão social feito no backend, sem % — não há teto). ── */
  const salvarTotalSku = async (skuNode: any, mes: string, novoVolume: number) => {
    const ajustes: any[] = [];
    const walk = (coord: any) => (coord.subRows || []).forEach((exec: any) =>
      (exec.subRows || []).forEach((raz: any) => {
        ajustes.push({ sku: skuNode.sku, mes_projetado: mes, razao_social: raz.nome, novo_volume: raz.meses?.[mes]?.meta ?? 0 });
      }));
    (skuNode.subRows || []).forEach(walk);
    if (!ajustes.length) return;
    // Rateia o novo total entre as razões sociais existentes, por peso bottomup/histórico.
    const pesos = ajustes.map(a => {
      let peso = 0;
      (skuNode.subRows || []).forEach((coord: any) => (coord.subRows || []).forEach((exec: any) =>
        (exec.subRows || []).forEach((raz: any) => {
          if (raz.nome === a.razao_social) peso = pesoRateioBottomUp(raz.meses?.[mes]);
        })));
      return peso;
    });
    const partes = ratearMaiorResto(Math.max(0, Math.round(novoVolume)), pesos);
    ajustes.forEach((a, i) => { a.novo_volume = partes[i]; });
    await axios.post('/api/v1/carteira/salvar', { ajustes, ...(adminAlvo ? { nome_alvo: adminAlvo.nome, nivel_alvo: adminAlvo.nivel } : {}) });
    await carregar();
  };

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando sua carteira…
    </div>;
  }

  if (erroCarregamento) {
    return (
      <div className="h-full flex items-center justify-center bg-slate-50 px-6">
        <div className="max-w-xl rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-center">
          <div className="text-sm font-black text-rose-700">Falha ao carregar Metas Comercial</div>
          <div className="mt-2 text-xs font-medium text-rose-600">{String(erroCarregamento)}</div>
          <button onClick={carregar} className="mt-4 rounded-lg bg-rose-600 px-4 py-2 text-xs font-black text-white hover:bg-rose-700">
            Tentar novamente
          </button>
        </div>
      </div>
    );
  }

  if (dados?.simone_monetario) {
    return <PreenchimentoMonetarioSimone dados={dados} recarregar={carregar} />;
  }

  const labelFase: Record<string, string> = {
    SKU: 'SKU — total por produto', COORDENADOR: 'Coordenador — distribuição por SKU',
    EXECUTIVO: 'Executivo — distribuição por SKU', RAZAO_SOCIAL: 'Razão Social — distribuição por Executivo+SKU',
  };

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>

      {/* CABEÇALHO STICKY */}
      <div className="shrink-0 bg-white border-b border-slate-200">
        <div className="px-6 pt-4 pb-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-base font-black text-slate-900">Metas Comercial</h1>
            <p className="text-xs font-medium text-slate-400">
              Ciclo {dados?.ciclo} · distribua a meta na sua carteira
              {aguardandoUpstream && <span className="ml-2 text-orange-500 font-bold">· aguardando Demanda Comercial congelar</span>}
              {!aguardandoUpstream && minhaCongelada && <span className="ml-2 text-emerald-600 font-bold">· sua carteira bloqueada</span>}
              {!aguardandoUpstream && congeladaEtapa && <span className="ml-2 text-amber-600 font-bold">· etapa congelada</span>}
            </p>
            {faseAtual && (
              <div className="mt-2 flex items-center gap-2">
                <span className="px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-600 text-[10px] font-black uppercase tracking-wider">
                  Fase atual: {labelFase[faseAtual] || faseAtual}
                </span>
                {travadoDefinitivo && (
                  <span className="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 text-[10px] font-black uppercase tracking-wider flex items-center gap-1">
                    <Lock className="w-3 h-3" /> travado definitivamente
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            {souAdmin && (
              <select
                value={adminAlvo ? `${adminAlvo.nivel}||${adminAlvo.nome}` : ''}
                onChange={e => {
                  const [nivel, ...nome] = e.target.value.split('||');
                  setAdminAlvo(e.target.value ? { nivel, nome: nome.join('||') } : null);
                  setAbertoSku(null); setAbertoExecutivo(null);
                }}
                className="max-w-xs px-3 py-2 rounded-xl border border-violet-200 bg-violet-50 text-xs font-bold text-violet-700 focus:outline-none"
              >
                <option value="">Visão administrativa: toda a carteira</option>
                {responsaveis.map(r => (
                  <option key={`${r.nivel}||${r.nome}`} value={`${r.nivel}||${r.nome}`}>
                    {r.nivel}: {r.nome}
                  </option>
                ))}
              </select>
            )}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              <input type="text" value={busca} onChange={e => setBusca(e.target.value)}
                placeholder="Buscar SKU…"
                className="pl-8 pr-7 py-2 text-xs rounded-xl border border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:outline-none focus:border-indigo-400 w-56" />
              {busca && (
                <button onClick={() => setBusca('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {!travadoDefinitivo && faseAtual && (
              <button onClick={travarFase} disabled={travando || bloqueadoEdicao}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all
                  ${!bloqueadoEdicao ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
                {travando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                Travar fase {labelFase[faseAtual]?.split(' —')[0] || faseAtual}
              </button>
            )}
            {souAdmin && adminOperando && (
              <button onClick={reabrirFaseAdmin}
                className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-black bg-amber-500 text-white hover:bg-amber-600">
                <Unlock className="w-4 h-4" /> Reabrir fase
              </button>
            )}
            {(souAdmin || funcao === 'Gerente') && (
              congeladaEtapa ? (
                <button onClick={reabrirEtapa}
                  className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black bg-amber-500 text-white hover:bg-amber-600">
                  <Unlock className="w-4 h-4" /> Reabrir etapa
                </button>
              ) :
                <button onClick={congelarEtapa} disabled={aguardandoUpstream}
                  className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black
                    ${aguardandoUpstream ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-emerald-600 text-white hover:bg-emerald-700'}`}>
                  <Lock className="w-4 h-4" /> Aprovar Metas
                </button>
            )}
          </div>
        </div>

        {/* Cards de totais por mês: Meta atual vs Meta Herdada (BU original) */}
        {meses.length > 0 && (
          <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: `repeat(${meses.length}, minmax(180px, 1fr))` }}>
            {meses.map(m => {
              const t = totaisPorMes[m] || { meta: 0, herdada: 0, fatMeta: 0, fatHerdada: 0 };
              const dPct = t.herdada > 0 ? ((t.meta - t.herdada) / t.herdada * 100) : null;
              const dCor = dPct == null ? '#94a3b8' : Math.abs(dPct) >= 15 ? '#e11d48' : Math.abs(dPct) >= 5 ? '#d97706' : '#059669';
              return (
                <div key={m} className="bg-slate-50 rounded-xl px-3 py-2 space-y-1.5">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
                  <div>
                    <div className="text-[9px] font-black uppercase text-indigo-500 mb-0.5">Meta (atual)</div>
                    <div className="text-sm font-black text-slate-900">{fmtCx(t.meta)} cx</div>
                    <div className="text-[10px] font-bold text-indigo-600">{fmtRs(t.fatMeta)}</div>
                  </div>
                  <div className="h-px bg-slate-200" />
                  <div>
                    <div className="text-[9px] font-black uppercase text-slate-400 mb-0.5">Meta Herdada</div>
                    <div className="text-sm font-black text-slate-700">{fmtCx(t.herdada)} cx</div>
                    <div className="text-[10px] font-bold text-slate-500">{fmtRs(t.fatHerdada)}</div>
                  </div>
                  {dPct != null && (
                    <div className="flex items-center justify-between rounded-lg px-2 py-1"
                      style={{ background: Math.abs(dPct) >= 5 ? '#fff7ed' : '#f0fdf4' }}>
                      <span className="text-[9px] font-black text-slate-400">Δ vs herdada</span>
                      <span className="text-[11px] font-black" style={{ color: dCor }}>
                        {dPct >= 0 ? '+' : ''}{dPct.toFixed(1)}%
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {aguardandoUpstream && (
          <div className="mx-6 mb-3 flex items-center gap-3 rounded-xl bg-orange-50 border border-orange-200 px-4 py-3">
            <div className="w-2 h-2 rounded-full bg-orange-400 shrink-0 animate-pulse" />
            <div>
              <div className="text-xs font-black text-orange-700">Preenchimento bloqueado</div>
              <div className="text-[11px] font-medium text-orange-600">Demanda Comercial ainda não congelou o plano deste ciclo.</div>
            </div>
          </div>
        )}

        {!aguardandoUpstream && aguardandoGerente && !souAdmin && (
          <div className="mx-6 mb-3 flex items-center gap-3 rounded-xl bg-orange-50 border border-orange-200 px-4 py-3">
            <div className="w-2 h-2 rounded-full bg-orange-400 shrink-0 animate-pulse" />
            <div>
              <div className="text-xs font-black text-orange-700">Aguardando o Gerente</div>
              <div className="text-[11px] font-medium text-orange-600">O Gerente ainda não travou a fase Coordenador neste ciclo. A edição libera automaticamente assim que ele travar.</div>
            </div>
          </div>
        )}

        {erroTravamento && (
          <div className="mx-6 mb-3 rounded-xl bg-rose-50 border border-rose-200 px-4 py-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1">
                <div className="text-xs font-black text-rose-700">{erroTravamento.mensagem}</div>
                <div className="mt-2 grid gap-1">
                  {erroTravamento.detalhes.map((d, i) => (
                    <div key={i} className="grid grid-cols-4 gap-2 text-[11px] font-bold text-rose-600 bg-white/60 rounded-lg px-2 py-1">
                      <span className="truncate">{d.chave}</span>
                      <span className="text-right">atual: {fmtCx(d.volume_atual)}</span>
                      <span className="text-right">alvo: {fmtCx(d.volume_alvo)}</span>
                      <span className="text-right">{(d.variacao_pct * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>
              <button onClick={() => setErroTravamento(null)} className="text-rose-300 hover:text-rose-600 shrink-0">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
        {arvoreFiltrada.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <Search className="w-8 h-8 mb-2 opacity-30" />
            <div className="text-sm font-bold">Nenhum SKU encontrado{busca.trim() ? ` para "${busca}"` : ''}</div>
            {busca && <button onClick={() => setBusca('')} className="mt-3 text-xs font-black text-indigo-500 hover:underline">Limpar busca</button>}
          </div>
        ) : (
          <div className="grid gap-2">
            {arvoreFiltrada.map((sku: any) => (
              <CardSku
                key={sku.sku}
                sku={sku}
                meses={meses}
                funcao={funcao}
                faseAtual={faseAtual}
                bloqueado={bloqueadoEdicao}
                aberto={abertoSku === sku.sku}
                onToggle={() => setAbertoSku(prev => prev === sku.sku ? null : sku.sku)}
                abertoExecutivo={abertoSku === sku.sku ? abertoExecutivo : null}
                setAbertoExecutivo={setAbertoExecutivo}
                salvarTotalSku={salvarTotalSku}
                responsavelBody={responsavelBody}
                dossieAlvo={dossieAlvo}
                setDossieAlvo={setDossieAlvo}
                recarregar={carregar}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PreenchimentoMonetarioSimone({ dados, recarregar }: { dados: any; recarregar: () => void }) {
  const [etapa, setEtapa] = useState<string>(dados.etapa_atual || 'REGIONAL');
  const [regionais, setRegionais] = useState<any[]>(dados.regionais || []);
  const [abertos, setAbertos] = useState<Record<string, boolean>>({});
  const [salvando, setSalvando] = useState(false);

  useEffect(() => {
    setEtapa(dados.etapa_atual || 'REGIONAL');
    setRegionais(dados.regionais || []);
  }, [dados]);

  const alterarRegional = (regional: string, mes: string, valor: number) => {
    setRegionais(prev => prev.map(r => r.regional !== regional ? r : {
      ...r, meses: { ...r.meses, [mes]: Math.max(0, Number.isFinite(valor) ? valor : 0) },
    }));
  };

  const alterarCliente = (regional: string, cgc: string, mes: string, valor: number) => {
    setRegionais(prev => prev.map(r => r.regional !== regional ? r : {
      ...r,
      clientes: r.clientes.map((c: any) => c.cgc !== cgc ? c : {
        ...c, meses: { ...c.meses, [mes]: Math.max(0, Number.isFinite(valor) ? valor : 0) },
      }),
    }));
  };

  const salvar = async (cliente: boolean) => {
    setSalvando(true);
    try {
      const payload = cliente
        ? regionais.flatMap(r => r.clientes.flatMap((c: any) =>
            dados.meses.map((mes: string) => ({
              regional: r.regional, cgc: c.cgc, mes_projetado: mes,
              valor_meta: Number(c.meses?.[mes] || 0),
            }))))
        : regionais.flatMap(r => dados.meses.map((mes: string) => ({
            regional: r.regional, mes_projetado: mes,
            valor_meta: Number(r.meses?.[mes] || 0),
          })));
      await axios.post(cliente
        ? '/api/v1/carteira/salvar-clientes-monetarios-simone'
        : '/api/v1/carteira/salvar-metas-monetarias-simone', payload);
      await recarregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao salvar a distribuição monetária.');
    } finally {
      setSalvando(false);
    }
  };

  const travar = async () => {
    if (!confirm(`Travar a etapa ${etapa.toLowerCase()}? Os valores informados serão mantidos exatamente.`)) return;
    setSalvando(true);
    try {
      if (etapa === 'REGIONAL') await salvar(false);
      if (etapa === 'CLIENTE') await salvar(true);
      await axios.post('/api/v1/carteira/travar-fase-monetaria-simone', { etapa });
      await recarregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao travar a etapa.');
    } finally {
      setSalvando(false);
    }
  };

  const somenteLeitura = etapa === 'CONFIRMACAO';
  return (
    <div className="h-full overflow-y-auto bg-slate-50 px-6 py-4">
      <div className="max-w-6xl mx-auto">
        <div className="bg-white border border-slate-200 rounded-xl px-5 py-4 mb-4">
          <h2 className="text-base font-black text-slate-900">Distribuição monetária — Simone Andrade de Paula</h2>
          <p className="text-xs text-slate-500 mt-1">
            A meta atual é apenas uma sugestão. O valor informado substitui integralmente a meta,
            inclusive quando for R$ 0,00, sem arredondamento ou compensação automática.
          </p>
          <div className="flex items-center justify-between mt-4">
            <div className="flex gap-2 text-[10px] font-black uppercase tracking-wider">
              {['REGIONAL', 'CLIENTE', 'CONFIRMACAO'].map((nome, i) => (
                <span key={nome} className={`px-3 py-1.5 rounded-full ${
                  i <= ['REGIONAL', 'CLIENTE', 'CONFIRMACAO'].indexOf(etapa)
                    ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-400'
                }`}>{nome === 'CONFIRMACAO' ? 'Confirmação' : nome}</span>
              ))}
            </div>
            {!somenteLeitura && (
              <button onClick={travar} disabled={salvando}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-black hover:bg-indigo-700 disabled:opacity-50">
                {salvando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                Salvar e travar etapa
              </button>
            )}
          </div>
        </div>

        <div className="grid gap-3">
          {regionais.map((regional: any) => (
            <div key={regional.regional} className="bg-white border border-slate-200 rounded-xl overflow-hidden">
              <div className="px-4 py-3 flex items-center justify-between border-b border-slate-100">
                <button onClick={() => setAbertos(p => ({ ...p, [regional.regional]: !p[regional.regional] }))}
                  className="flex items-center gap-2 text-left">
                  {abertos[regional.regional] ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                  <span className="text-sm font-black text-slate-800">{regional.regional}</span>
                </button>
                <div className="flex gap-3">
                  {dados.meses.map((mes: string) => (
                    <label key={mes} className="flex flex-col text-[9px] font-black uppercase text-slate-400">
                      {mesLabel(mes)}
                      <input type="number" step="0.01" min="0" disabled={etapa !== 'REGIONAL'}
                        value={Number(regional.meses?.[mes] || 0)}
                        onChange={e => alterarRegional(regional.regional, mes, Number(e.target.value))}
                        className="mt-1 w-28 px-2 py-1.5 rounded-lg border border-slate-200 text-right text-xs font-bold text-slate-700 disabled:bg-slate-50" />
                    </label>
                  ))}
                </div>
              </div>
              {abertos[regional.regional] && (
                <div className="px-4 py-3">
                  {regional.clientes.map((cliente: any) => (
                    <div key={cliente.cgc} className="grid items-center gap-3 py-2 border-b border-slate-50"
                      style={{ gridTemplateColumns: `minmax(240px,1fr) repeat(${dados.meses.length},minmax(110px,auto))` }}>
                      <div>
                        <div className="text-xs font-black text-slate-700">{cliente.cliente}</div>
                        <div className="text-[10px] text-slate-400">{cliente.executivo} · {cliente.cgc}</div>
                      </div>
                      {dados.meses.map((mes: string) => (
                        <input key={mes} type="number" step="0.01" min="0" disabled={etapa !== 'CLIENTE'}
                          value={Number(cliente.meses?.[mes] || 0)}
                          onChange={e => alterarCliente(regional.regional, cliente.cgc, mes, Number(e.target.value))}
                          className="w-full px-2 py-1.5 rounded-lg border border-slate-200 text-right text-xs font-bold text-slate-700 disabled:bg-slate-50" />
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── CARD DE UM SKU (raiz da árvore) ─────────────────────────────── */
function CardSku({
  sku, meses, funcao, faseAtual, bloqueado, aberto, onToggle,
  abertoExecutivo, setAbertoExecutivo, salvarTotalSku, responsavelBody,
  dossieAlvo, setDossieAlvo, recarregar,
}: any) {
  const mesFoco = meses[0];
  const cel = sku.meses?.[mesFoco] || {};
  // Gerente na fase SKU edita o total direto (sem %, sem teto).
  const editaTotalSku = funcao === 'Gerente' && faseAtual === 'SKU';
  // Gerente na fase COORDENADOR faz drill-down por coordenador dentro do SKU.
  const drillCoordenador = funcao === 'Gerente' && faseAtual === 'COORDENADOR';
  // Coordenador nas fases EXECUTIVO/RAZAO_SOCIAL faz drill-down por SKU também.
  const drillExecutivo = funcao === 'Coordenador' && (faseAtual === 'EXECUTIVO' || faseAtual === 'RAZAO_SOCIAL');

  const dossieAberto = dossieAlvo?.sku === sku.sku && !dossieAlvo?.coordenador && !dossieAlvo?.vendedor;

  return (
    <div className="bg-white rounded-xl border border-slate-100 hover:border-slate-200 transition-colors">
      <div className="grid gap-2 px-4 py-3 items-center"
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(150px, 1fr)) 40px` }}>
        <button onClick={onToggle} className="flex items-center gap-2 min-w-0 text-left">
          {aberto ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
          <div className="min-w-0">
            <div className="text-sm font-black text-slate-800 truncate">{sku.descricao}</div>
            <div className="text-[10px] font-bold text-slate-300">{sku.sku}</div>
          </div>
        </button>
        {meses.map((m: string) => {
          const c = sku.meses?.[m] || {};
          return (
            <div key={m} className="text-right">
              {editaTotalSku ? (
                <>
                  <CampoNumeroEditavel
                    value={(c.meta || 0) * (c.pmv || 0)}
                    format={fmtRs}
                    disabled={bloqueado || (c.pmv || 0) <= 0}
                    onChange={valor => salvarTotalSku(sku, m, (c.pmv || 0) > 0 ? valor / c.pmv : 0)}
                    title={(c.pmv || 0) <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                    className={`w-full text-right text-sm font-bold rounded-md px-2 py-1 border transition-colors
                      border-transparent bg-transparent text-indigo-600
                      ${bloqueado || (c.pmv || 0) <= 0 ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
                  />
                  <CampoNumeroEditavel
                    value={c.meta || 0}
                    format={fmtCx}
                    disabled={bloqueado}
                    onChange={valor => salvarTotalSku(sku, m, valor)}
                    className={`w-full text-right text-xs font-bold rounded-md px-2 py-0.5 border transition-colors mt-0.5
                      border-transparent bg-transparent text-slate-500
                      ${bloqueado ? 'cursor-not-allowed opacity-60' : 'hover:border-slate-200 focus:border-indigo-400 focus:bg-white focus:outline-none'}`}
                  />
                </>
              ) : (
                <>
                  <div className="text-sm font-black text-slate-800">{fmtRs((c.meta || 0) * (c.pmv || 0))}</div>
                  <div className="text-xs font-bold text-slate-400">{fmtCx(c.meta || 0)} cx</div>
                </>
              )}
              <div className="text-[9px] font-bold text-slate-300">
                Meta Herdada {fmtCx(c.bottomup || 0)} · {c.variacao_pct == null ? '—' : `${(c.variacao_pct * 100).toFixed(1)}%`}
              </div>
            </div>
          );
        })}
        <button
          onClick={() => setDossieAlvo((prev: any) => dossieAberto ? null : { sku: sku.sku, descricao: sku.descricao })}
          className={`justify-self-center p-1.5 rounded-lg transition-colors
            ${dossieAberto ? 'bg-indigo-100 text-indigo-600' : 'text-slate-300 hover:bg-indigo-50 hover:text-indigo-600'}`}
          title="Ver dossiê do SKU">
          <LineIcon className="w-4 h-4" />
        </button>
      </div>

      {dossieAberto && (
        <div className="mx-4 mb-3">
          <DossieInferior
            prefixoApi="/api/v1/carteira"
            tipo="sku"
            id={sku.sku}
            titulo={sku.descricao}
            subtitulo={sku.sku}
            paramsExtra={{}}
            onFechar={() => setDossieAlvo(null)}
          />
        </div>
      )}

      {aberto && (drillCoordenador || drillExecutivo) && (
        <div className="border-t border-slate-100 px-4 py-3">
          {drillCoordenador && (
            <ListaDistribuicao
              nivel="COORDENADOR"
              itens={(sku.subRows || []).map((c: any) => ({ chave: c.nome, meses: c.meses }))}
              sku={sku.sku} meses={meses} teto={sku.meses}
              bloqueado={bloqueado} responsavelBody={responsavelBody}
              dossieBase={{ sku: sku.sku, descricao: sku.descricao }}
              dossieAlvo={dossieAlvo} setDossieAlvo={setDossieAlvo}
              recarregar={recarregar}
            />
          )}
          {drillExecutivo && (
            <div className="grid gap-2">
              {(sku.subRows || []).map((coord: any) => (
                <div key={coord.nome}>
                  <button
                    onClick={() => setAbertoExecutivo((prev: string | null) => prev === coord.nome ? null : coord.nome)}
                    className="w-full flex items-center gap-2 text-left px-2 py-1.5 rounded-lg hover:bg-slate-50">
                    {abertoExecutivo === coord.nome ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
                    <span className="text-xs font-black text-slate-600">{coord.nome}</span>
                    <span className="text-[9px] font-black uppercase tracking-wider text-slate-300">coordenador</span>
                  </button>
                  {abertoExecutivo === coord.nome && (
                    <div className="ml-4 mt-1">
                      {faseAtual === 'EXECUTIVO' && (
                        <ListaDistribuicao
                          nivel="EXECUTIVO"
                          itens={(coord.subRows || []).map((e: any) => ({ chave: e.nome, meses: e.meses }))}
                          sku={sku.sku} meses={meses} teto={coord.meses}
                          bloqueado={bloqueado} responsavelBody={responsavelBody}
                          dossieBase={{ sku: sku.sku, descricao: sku.descricao, coordenador: coord.nome }}
                          dossieAlvo={dossieAlvo} setDossieAlvo={setDossieAlvo}
                          recarregar={recarregar}
                        />
                      )}
                      {faseAtual === 'RAZAO_SOCIAL' && (
                        <div className="grid gap-2">
                          {(coord.subRows || []).map((exec: any) => (
                            <ExecutivoRazaoSocial
                              key={exec.nome}
                              sku={sku} coordenador={coord} executivo={exec}
                              meses={meses} bloqueado={bloqueado} responsavelBody={responsavelBody}
                              dossieAlvo={dossieAlvo} setDossieAlvo={setDossieAlvo}
                              recarregar={recarregar}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── EXECUTIVO (dentro de Coordenador+SKU), expande razões sociais ──── */
function ExecutivoRazaoSocial({ sku, coordenador, executivo, meses, bloqueado, responsavelBody, dossieAlvo, setDossieAlvo, recarregar }: any) {
  const [aberto, setAberto] = useState(false);
  return (
    <div>
      <button onClick={() => setAberto(p => !p)} className="w-full flex items-center gap-2 text-left px-2 py-1.5 rounded-lg hover:bg-slate-50">
        {aberto ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
        <span className="text-xs font-black text-slate-600">{executivo.nome}</span>
        <span className="text-[9px] font-black uppercase tracking-wider text-slate-300">executivo</span>
      </button>
      {aberto && (
        <div className="ml-4 mt-1">
          <ListaDistribuicao
            nivel="RAZAO_SOCIAL"
            itens={(executivo.subRows || []).map((r: any) => ({ chave: r.nome, meses: r.meses }))}
            sku={sku.sku} meses={meses} teto={executivo.meses}
            executivoPai={executivo.nome}
            bloqueado={bloqueado} responsavelBody={responsavelBody}
            dossieBase={{ sku: sku.sku, descricao: sku.descricao, vendedor: executivo.nome }}
            dossieAlvo={dossieAlvo} setDossieAlvo={setDossieAlvo}
            recarregar={recarregar}
          />
        </div>
      )}
    </div>
  );
}

/* ── LISTA GENÉRICA DE DISTRIBUIÇÃO (Coordenador|Executivo|RazãoSocial) ──
   Cada linha mostra 3 campos sincronizados (%, R$, volume). Editar
   qualquer um recalcula os outros dois localmente; ao sair do campo
   (onBlur) dispara POST /distribuir só daquele item. ------------------- */
function ListaDistribuicao({
  nivel, itens, sku, meses, teto, executivoPai, bloqueado, responsavelBody,
  dossieBase, dossieAlvo, setDossieAlvo, recarregar,
}: {
  nivel: 'COORDENADOR' | 'EXECUTIVO' | 'RAZAO_SOCIAL';
  itens: Array<{ chave: string; meses: Record<string, any> }>;
  sku: string; meses: string[]; teto: Record<string, any>; executivoPai?: string;
  bloqueado: boolean; responsavelBody: any; dossieBase: any;
  dossieAlvo: any; setDossieAlvo: (v: any) => void; recarregar: () => void;
}) {
  const mesFoco = meses[0];
  // draft local: chave -> {percentual, valor, volume} — só para o mês em foco (1 coluna por vez evita explosão de estado)
  const [enviando, setEnviando] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<Record<string, { percentual?: number; valor?: number; volume?: number }>>({});

  const tetoVolume = (m: string) => Math.max(0, Number(teto?.[m]?.meta || 0));
  const pmvMes = (m: string) => Number(teto?.[m]?.pmv || 0);

  const valoresAtuais = (chave: string, m: string) => {
    const item = itens.find(i => i.chave === chave);
    const cel = item?.meses?.[m] || {};
    const d = draft[`${chave}|${m}`] || {};
    const volume = d.volume ?? Math.max(0, Number(cel.meta || 0));
    const pmv = pmvMes(m);
    const valor = d.valor ?? volume * pmv;
    const t = tetoVolume(m);
    const percentual = d.percentual ?? (t > 0 ? volume / t : 0);
    return { volume, valor, percentual, pmv, cel };
  };

  const atualizarDraft = (chave: string, m: string, patch: { percentual?: number; valor?: number; volume?: number }) => {
    setDraft(prev => ({ ...prev, [`${chave}|${m}`]: { ...prev[`${chave}|${m}`], ...patch } }));
  };

  const onEditarPercentual = (chave: string, m: string, pct: number) => {
    const t = tetoVolume(m);
    const pmv = pmvMes(m);
    const novoVolume = Math.max(0, Math.round((pct / 100) * t));
    atualizarDraft(chave, m, { percentual: pct / 100, volume: novoVolume, valor: novoVolume * pmv });
  };
  const onEditarValor = (chave: string, m: string, valor: number) => {
    const pmv = pmvMes(m);
    const t = tetoVolume(m);
    const novoVolume = pmv > 0 ? Math.round(valor / pmv) : 0;
    atualizarDraft(chave, m, { valor, volume: novoVolume, percentual: t > 0 ? novoVolume / t : 0 });
  };
  const onEditarVolume = (chave: string, m: string, volume: number) => {
    const pmv = pmvMes(m);
    const t = tetoVolume(m);
    atualizarDraft(chave, m, { volume, valor: volume * pmv, percentual: t > 0 ? volume / t : 0 });
  };

  const confirmarLinha = async (chave: string, m: string) => {
    const key = `${chave}|${m}`;
    const d = draft[key];
    if (!d) return;
    setEnviando(prev => ({ ...prev, [key]: true }));
    try {
      const item: any = { chave };
      if (d.volume !== undefined) item.novo_volume = Math.round(d.volume);
      else if (d.valor !== undefined) item.novo_valor = d.valor;
      else if (d.percentual !== undefined) item.percentual_volume = d.percentual;
      await axios.post('/api/v1/carteira/distribuir', {
        sku, mes_projetado: m, nivel, itens: [item],
        ...(nivel === 'RAZAO_SOCIAL' ? { executivo_pai: executivoPai } : {}),
        ...responsavelBody,
      });
      setDraft(prev => { const n = { ...prev }; delete n[key]; return n; });
      await recarregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao distribuir.');
    } finally {
      setEnviando(prev => { const n = { ...prev }; delete n[key]; return n; });
    }
  };

  return (
    <div className="grid gap-1">
      <div className="grid gap-2 px-2 py-1 text-[9px] font-black uppercase tracking-wider text-slate-300"
        style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(220px, 1fr)) 32px` }}>
        <div>{nivel === 'COORDENADOR' ? 'Coordenador' : nivel === 'EXECUTIVO' ? 'Executivo' : 'Razão Social'}</div>
        {meses.map(m => <div key={m} className="text-right">{mesLabel(m)} · % / R$ / cx</div>)}
        <div />
      </div>
      {itens.map(item => {
        const dossieAberto =
          (nivel === 'COORDENADOR' && dossieAlvo?.sku === dossieBase.sku && dossieAlvo?.coordenador === item.chave && !dossieAlvo?.vendedor) ||
          (nivel === 'EXECUTIVO' && dossieAlvo?.sku === dossieBase.sku && dossieAlvo?.vendedor === item.chave && !dossieAlvo?.razao) ||
          (nivel === 'RAZAO_SOCIAL' && dossieAlvo?.sku === dossieBase.sku && dossieAlvo?.vendedor === dossieBase.vendedor && dossieAlvo?.razao === item.chave);
        return (
          <div key={item.chave}>
            <div className="grid gap-2 px-2 py-1.5 items-center rounded-lg hover:bg-slate-50"
              style={{ gridTemplateColumns: `1fr repeat(${meses.length}, minmax(220px, 1fr)) 32px` }}>
              <div className="text-xs font-bold text-slate-600 truncate">{item.chave}</div>
              {meses.map(m => {
                const key = `${item.chave}|${m}`;
                const { volume, valor, percentual, pmv, cel } = valoresAtuais(item.chave, m);
                const carregando = !!enviando[key];
                return (
                  <div key={m} className="flex items-center gap-1 justify-end">
                    <CampoNumeroEditavel
                      value={Math.round(percentual * 100)}
                      format={(n: number) => `${n}%`}
                      disabled={bloqueado || carregando}
                      onChange={v => onEditarPercentual(item.chave, m, v)}
                      onBlur={() => confirmarLinha(item.chave, m)}
                      className={`w-14 text-right text-[11px] font-bold rounded-md px-1.5 py-1 border transition-colors
                        ${key in draft ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 bg-white text-slate-600'}
                        ${bloqueado ? 'cursor-not-allowed opacity-60' : 'focus:border-indigo-400 focus:outline-none'}`}
                    />
                    <CampoNumeroEditavel
                      value={valor}
                      format={fmtRs}
                      disabled={bloqueado || carregando || pmv <= 0}
                      onChange={v => onEditarValor(item.chave, m, v)}
                      onBlur={() => confirmarLinha(item.chave, m)}
                      title={pmv <= 0 ? 'Sem PMV: edição monetária bloqueada' : undefined}
                      className={`w-24 text-right text-[11px] font-bold rounded-md px-1.5 py-1 border transition-colors
                        ${key in draft ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 bg-white text-slate-600'}
                        ${bloqueado || pmv <= 0 ? 'cursor-not-allowed opacity-60' : 'focus:border-indigo-400 focus:outline-none'}`}
                    />
                    <CampoNumeroEditavel
                      value={volume}
                      format={fmtCx}
                      disabled={bloqueado || carregando}
                      onChange={v => onEditarVolume(item.chave, m, v)}
                      onBlur={() => confirmarLinha(item.chave, m)}
                      className={`w-16 text-right text-[11px] font-bold rounded-md px-1.5 py-1 border transition-colors
                        ${key in draft ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 bg-white text-slate-600'}
                        ${bloqueado ? 'cursor-not-allowed opacity-60' : 'focus:border-indigo-400 focus:outline-none'}`}
                    />
                    {carregando && <Loader2 className="w-3 h-3 animate-spin text-indigo-400" />}
                    <span className="text-[9px] font-bold text-slate-300 w-14 text-right shrink-0">
                      herdada {fmtCx(cel.bottomup || 0)}{cel.variacao_pct != null ? ` · ${(cel.variacao_pct * 100).toFixed(0)}%` : ''}
                    </span>
                  </div>
                );
              })}
              <button
                onClick={() => setDossieAlvo((prev: any) => dossieAberto ? null : {
                  ...dossieBase,
                  ...(nivel === 'COORDENADOR' ? { coordenador: item.chave } : {}),
                  ...(nivel === 'EXECUTIVO' ? { vendedor: item.chave } : {}),
                  ...(nivel === 'RAZAO_SOCIAL' ? { razao: item.chave } : {}),
                })}
                className={`justify-self-center p-1 rounded-lg transition-colors
                  ${dossieAberto ? 'bg-violet-100 text-violet-600' : 'text-slate-300 hover:bg-violet-50 hover:text-violet-600'}`}
                title="Ver dossiê">
                <LineIcon className="w-3.5 h-3.5" />
              </button>
            </div>
            {dossieAberto && (
              <div className="ml-4 mr-2 mb-2">
                <DossieInferior
                  prefixoApi="/api/v1/carteira"
                  tipo="sku"
                  id={dossieBase.sku}
                  titulo={dossieBase.descricao}
                  subtitulo={`${dossieBase.sku} · ${item.chave}`}
                  paramsExtra={
                    nivel === 'COORDENADOR' ? { coordenador_nome: item.chave } :
                    nivel === 'EXECUTIVO' ? { vendedor_nome: item.chave } :
                    { razao_social: item.chave, vendedor_nome: dossieBase.vendedor }
                  }
                  onFechar={() => setDossieAlvo(null)}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── GAVETA DE DOSSIÊ ────────────────────────────────────────────── */
function GavetaDossie({ alvo, fechar }: {
  alvo: { sku: string; descricao: string; razao?: string; vendedor?: string };
  fechar: () => void;
}) {
  const paramsExtra: Record<string, string> = {};
  if (alvo.razao)    paramsExtra.razao_social   = alvo.razao;
  if (alvo.vendedor) paramsExtra.vendedor_nome  = alvo.vendedor;

  const subtitulo = alvo.razao
    ? `${alvo.sku} · ${alvo.razao}`
    : alvo.vendedor
    ? `${alvo.sku} · executivo: ${alvo.vendedor}`
    : alvo.sku;

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-xl bg-white z-40 shadow-2xl overflow-y-auto">
        <DossieInferior
          prefixoApi="/api/v1/carteira"
          tipo="sku"
          id={alvo.sku}
          titulo={alvo.descricao || alvo.sku}
          subtitulo={subtitulo}
          paramsExtra={Object.keys(paramsExtra).length ? paramsExtra : undefined}
          onFechar={fechar}
        />
      </div>
    </>
  );
}

/* ── PAINEL DE CADEADOS ──────────────────────────────────────────── */
function PainelCadeados({ fechar, recarregar, somenteTravar = false }: { fechar: () => void; recarregar: () => void; somenteTravar?: boolean }) {
  const [d, setD]         = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [em_acao, setEmAcao] = useState<string | null>(null);

  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/carteira/cadeados').then(r => setD(r.data)).finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

  const toggle = async (nome: string, bloqueado: boolean) => {
    const acao = bloqueado ? 'reabrir' : 'bloquear';
    const msg  = bloqueado
      ? `Reabrir a carteira de ${nome}?`
      : `Bloquear a carteira de ${nome}? O coordenador não poderá mais editar.`;
    if (!confirm(msg)) return;
    setEmAcao(nome);
    try {
      if (bloqueado) {
        await axios.post('/api/v1/carteira/reabrir-cadeado', { nome_alvo: nome });
      } else {
        await axios.post('/api/v1/carteira/bloquear', { nome_alvo: nome });
      }
      carregar(); recarregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || `Falha ao ${acao}.`);
    } finally { setEmAcao(null); }
  };

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/20 z-30" onClick={fechar} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-sm bg-white z-40 shadow-2xl flex flex-col">
        <div className="shrink-0 border-b border-slate-100 px-5 py-4 flex items-center justify-between">
          <div>
            <div className="text-sm font-black text-slate-900">Cadeados dos coordenadores</div>
            <div className="text-[10px] font-bold text-slate-400 mt-0.5">
              {somenteTravar ? 'Você pode trancar; somente o Administrador pode reabrir.' : 'Clique no botão para bloquear ou reabrir'}
            </div>
          </div>
          <button onClick={fechar} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-slate-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : (
            <div className="space-y-2">
              {(d?.coordenadores || []).length === 0 && (
                <div className="text-center text-slate-400 text-sm py-8">
                  Nenhum coordenador encontrado.
                </div>
              )}
              {(d?.coordenadores || []).map((c: any) => (
                <div key={c.nome}
                  className={`flex items-center justify-between rounded-xl px-4 py-3 border transition-colors
                    ${c.bloqueado
                      ? 'bg-rose-50 border-rose-200'
                      : 'bg-white border-slate-100 hover:border-slate-200'}`}>
                  <div className="min-w-0">
                    <div className="text-xs font-bold text-slate-700 truncate flex items-center gap-1.5">
                      {c.bloqueado
                        ? <Lock className="w-3 h-3 text-rose-500 shrink-0" />
                        : <Unlock className="w-3 h-3 text-slate-300 shrink-0" />}
                      {c.nome}
                    </div>
                    {c.bloqueado && c.por && (
                      <div className="text-[10px] font-bold text-rose-400 mt-0.5">
                        bloqueado por {c.por}
                      </div>
                    )}
                  </div>
                  {(!somenteTravar || !c.bloqueado) && <button
                    onClick={() => toggle(c.nome, c.bloqueado)}
                    disabled={em_acao === c.nome}
                    className={`ml-3 shrink-0 flex items-center gap-1.5 text-[11px] font-black px-3 py-1.5 rounded-lg transition-colors
                      ${c.bloqueado
                        ? 'bg-white text-emerald-600 border border-emerald-200 hover:bg-emerald-50'
                        : 'bg-rose-600 text-white hover:bg-rose-700'}
                      disabled:opacity-40`}>
                    {em_acao === c.nome
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : c.bloqueado
                        ? <><Unlock className="w-3 h-3" /> Reabrir</>
                        : <><Lock className="w-3 h-3" /> Travar</>}
                  </button>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ── CONSOLIDADO — visão somente leitura: Meta Coordenadores vs Comercial ── */
function ConsolidadoMetas() {
  const [dados,   setDados]   = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [abertas, setAbertas] = useState<Set<string>>(new Set());
  const [aprovando, setAprovando] = useState(false);

  const carregar = () => {
    setLoading(true);
    axios.get('/api/v1/carteira/consolidado')
      .then(r => setDados(r.data))
      .finally(() => setLoading(false));
  };
  useEffect(() => { carregar(); }, []);

  const meses: string[] = dados?.meses || [];
  const mesLabel = (iso: string) => {
    const [y, m] = iso.split('-');
    const n = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
    return `${n[parseInt(m)-1]}/${y.slice(2)}`;
  };
  const fmtCx = (n: number) => new Intl.NumberFormat('pt-BR').format(Math.round(n || 0));
  const fmtRs = (n: number) =>
    new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
      .format(Math.round(n || 0));

  const toggle = (id: string) =>
    setAbertas(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const aprovarMetas = async () => {
    if (!confirm('Aprovar Metas Comercial e liberar Demanda Irrestrita? Será baixado um XLSX de evidência.')) return;
    setAprovando(true);
    try {
      const r = await axios.post('/api/v1/carteira/aprovar-evidencia', { ajustes: [] }, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `metas_aprovadas_${dados?.ciclo?.replace('/', '_') || 'ciclo'}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      carregar();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Falha ao aprovar metas.');
    } finally { setAprovando(false); }
  };

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center text-slate-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Carregando consolidado…
    </div>
  );

  // Totais globais por mês
  const totais = meses.reduce((acc: any, m) => {
    let meta = 0, bu = 0, ia = 0, fatMeta = 0, fatBu = 0;
    (dados?.categorias || []).forEach((cat: any) =>
      cat.skus.forEach((s: any) => {
        const cel = s.meses[m]; if (!cel) return;
        meta   += cel.meta || 0;
        bu     += cel.bu   || 0;
        ia     += cel.ia   || 0;
        fatMeta += (cel.meta || 0) * (cel.pmv || 0);
        fatBu   += (cel.bu   || 0) * (cel.pmv || 0);
      })
    );
    acc[m] = { meta, bu, ia, fatMeta, fatBu };
    return acc;
  }, {});

  const colGrid = `240px repeat(${meses.length}, minmax(200px, 1fr))`;

  return (
    <div className="h-full flex flex-col bg-slate-50" style={{ fontVariantNumeric: 'tabular-nums' }}>
      <div className="flex-1 min-h-0 overflow-y-auto">

        {/* CABEÇALHO */}
        <div className="sticky top-0 z-20 bg-white border-b border-slate-200">
          <div className="px-6 pt-5 pb-3 flex items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-black text-slate-900 tracking-tight">Consolidado de Metas</h1>
              <p className="text-xs font-medium text-slate-400">
                Ciclo {dados?.ciclo} · compara o plano dos coordenadores com a Demanda Comercial
              </p>
              {(dados?.coordenadores_pendentes || []).length > 0 && (
                <div className="mt-2 text-[11px] font-bold text-orange-600">
                  Faltam trancar: {(dados?.coordenadores_pendentes || []).join(', ')}
                </div>
              )}
            </div>
            {dados?.pode_aprovar && (
              <button onClick={aprovarMetas}
                disabled={aprovando || (dados?.coordenadores_pendentes || []).length > 0}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black
                  ${(dados?.coordenadores_pendentes || []).length === 0
                    ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                    : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}>
                {aprovando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                Aprovar Metas
              </button>
            )}
          </div>

          {/* Totais por mês */}
          <div className="px-6 pb-3 grid gap-2" style={{ gridTemplateColumns: colGrid }}>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-300 flex items-end pb-1">Total</div>
            {meses.map(m => {
              const t = totais[m] || {};
              const dPct = t.bu > 0 ? ((t.meta - t.bu) / t.bu * 100) : null;
              const dCor = dPct == null ? '#94a3b8' : Math.abs(dPct) >= 15 ? '#e11d48' : Math.abs(dPct) >= 5 ? '#d97706' : '#059669';
              return (
                <div key={m} className="bg-slate-50 rounded-xl px-3 py-2 space-y-1.5">
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{mesLabel(m)}</div>
                  {/* Coordenadores */}
                  <div>
                    <div className="text-[9px] font-black uppercase text-violet-500 mb-0.5">Coordenadores (meta)</div>
                    <div className="text-sm font-black text-slate-900">{fmtCx(t.meta)} cx</div>
                    <div className="text-[10px] font-bold text-violet-600">{fmtRs(t.fatMeta)}</div>
                  </div>
                  <div className="h-px bg-slate-200" />
                  {/* Comercial */}
                  <div>
                    <div className="text-[9px] font-black uppercase text-indigo-500 mb-0.5">Comercial (Meta Herdada)</div>
                    <div className="text-sm font-black text-slate-700">{fmtCx(t.bu)} cx</div>
                    <div className="text-[10px] font-bold text-indigo-600">{fmtRs(t.fatBu)}</div>
                  </div>
                  {/* Delta */}
                  {dPct != null && (
                    <div className="flex items-center justify-between rounded-lg px-2 py-1"
                      style={{ background: Math.abs(dPct) >= 5 ? '#fff7ed' : '#f0fdf4' }}>
                      <span className="text-[9px] font-black text-slate-400">Δ meta vs herdada</span>
                      <span className="text-[11px] font-black" style={{ color: dCor }}>
                        {dPct >= 0 ? '+' : ''}{dPct.toFixed(1)}%
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Cabeçalho da tabela */}
          <div className="px-6 pb-1">
            <div className="grid gap-2 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400"
              style={{ gridTemplateColumns: colGrid }}>
              <div>Categoria / SKU</div>
              {meses.map(m => (
                <div key={m} className="grid grid-cols-3 gap-1 text-center">
                  <span className="text-violet-400">Meta</span>
                  <span className="text-indigo-400">Herdada</span>
                  <span className="text-slate-400">Δ%</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* CORPO */}
        <div className="px-6 pb-8">
          {(dados?.categorias || []).map((cat: any) => {
            const catAberta = abertas.has(cat.nome);

            // Totais da categoria
            const somaCat = (m: string, campo: 'meta' | 'bu' | 'ia') =>
              cat.skus.reduce((a: number, s: any) => a + (s.meses[m]?.[campo] || 0), 0);

            return (
              <div key={cat.nome} className="mb-1">
                {/* Linha categoria */}
                <button onClick={() => toggle(cat.nome)}
                  className="w-full grid gap-2 px-3 py-3 items-center bg-white rounded-xl border border-slate-100 hover:border-slate-200 transition-colors"
                  style={{ gridTemplateColumns: colGrid }}>
                  <div className="flex items-center gap-2 min-w-0">
                    {catAberta ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
                               : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                    <div className="min-w-0">
                      <div className="font-black text-slate-800 text-sm truncate">{cat.nome}</div>
                      <div className="text-[10px] font-bold text-slate-400">{cat.skus.length} SKUs</div>
                    </div>
                  </div>
                  {meses.map(m => {
                    const meta = somaCat(m, 'meta');
                    const bu   = somaCat(m, 'bu');
                    const d    = bu > 0 ? ((meta - bu) / bu * 100) : null;
                    const cor  = d == null ? '#94a3b8' : Math.abs(d) >= 15 ? '#e11d48' : Math.abs(d) >= 5 ? '#d97706' : '#059669';
                    return (
                      <div key={m} className="grid grid-cols-3 gap-1 items-center">
                        <div className="text-center text-[11px] font-black text-violet-600">{fmtCx(meta)}</div>
                        <div className="text-center text-[11px] font-black text-indigo-500">{fmtCx(bu)}</div>
                        <div className="text-center text-[11px] font-black" style={{ color: cor }}>
                          {d == null ? '—' : `${d >= 0 ? '+' : ''}${d.toFixed(0)}%`}
                        </div>
                      </div>
                    );
                  })}
                </button>

                {/* Linhas SKU */}
                {catAberta && cat.skus.map((s: any) => {
                  const skuKey = `${cat.nome}>${s.sku}`;
                  const skuAberto = abertas.has(skuKey);
                  return (
                    <div key={s.sku}>
                      <button onClick={() => toggle(skuKey)}
                        className="w-full grid gap-2 px-3 py-2 items-center ml-3 mt-0.5 rounded-xl hover:bg-white"
                        style={{ gridTemplateColumns: colGrid }}>
                        <div className="min-w-0 pl-2 flex items-center gap-2">
                          {skuAberto ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                                     : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
                          <div className="min-w-0 text-left">
                            <div className="text-xs font-bold text-slate-700 truncate">{s.descricao}</div>
                            <div className="text-[10px] font-bold text-slate-300">{s.sku}</div>
                          </div>
                        </div>
                        {meses.map(m => {
                          const cel  = s.meses[m];
                          if (!cel) return <div key={m} />;
                          const meta = cel.meta || 0;
                          const bu   = cel.bu   || 0;
                          const d    = cel.delta_pct;
                          const cor  = d == null ? '#94a3b8' : Math.abs(d) >= 15 ? '#e11d48' : Math.abs(d) >= 5 ? '#d97706' : '#059669';
                          const editado = meta !== bu;
                          return (
                            <div key={m} className={`grid grid-cols-3 gap-1 items-center px-1 py-1 rounded-lg ${editado ? 'bg-violet-50' : ''}`}>
                              <div className="text-center">
                                <div className="text-[11px] font-black text-violet-700">{fmtCx(meta)}</div>
                                {cel.pmv > 0 && <div className="text-[9px] text-violet-400">{fmtRs(meta * cel.pmv)}</div>}
                              </div>
                              <div className="text-center">
                                <div className="text-[11px] font-bold text-indigo-500">{fmtCx(bu)}</div>
                                {cel.pmv > 0 && <div className="text-[9px] text-indigo-300">{fmtRs(bu * cel.pmv)}</div>}
                              </div>
                              <div className="text-center">
                                <div className="text-[11px] font-black" style={{ color: cor }}>
                                  {d == null ? '—' : `${d >= 0 ? '+' : ''}${d.toFixed(0)}%`}
                                </div>
                                {d != null && <div className="text-[9px] text-slate-300">{fmtCx(Math.abs(cel.delta_cx))} cx</div>}
                              </div>
                            </div>
                          );
                        })}
                      </button>
                      {skuAberto && (
                        <div className="ml-8 mr-2 mb-2 rounded-xl bg-white border border-slate-100 overflow-hidden">
                          <div className="px-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-400 border-b border-slate-100">
                            Impacto por coordenador neste SKU
                          </div>
                          {(s.impactos_coordenadores || []).map((i: any) => (
                            <div key={`${s.sku}-${i.coordenador}`}
                              className="grid grid-cols-4 gap-2 px-3 py-2 text-[11px] border-b border-slate-50 last:border-0">
                              <div className="font-bold text-slate-700 truncate">{i.coordenador}</div>
                              <div className="text-right text-violet-600 font-bold">{fmtRs(i.meta_rs)}</div>
                              <div className="text-right text-indigo-500 font-bold">{fmtRs(i.bu_rs)}</div>
                              <div className={`text-right font-black ${i.delta_rs > 0 ? 'text-emerald-600' : i.delta_rs < 0 ? 'text-red-600' : 'text-slate-400'}`}>
                                {fmtRs(i.delta_rs)}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
