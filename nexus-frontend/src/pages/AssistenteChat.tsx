import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { BarChart3, CalendarDays, Coins, Compass, Loader2, Search, Send, Sparkles, X } from 'lucide-react';
import { renderChatMarkdown } from './chatMarkdown';

type Area = 'indicadores' | 'demanda' | 'financeiro';
type Msg = { role: 'user' | 'assistant'; content: string };
type ItemBusca = { tipo: 'sku' | 'categoria'; id: string; label: string; categoria: string };

const hoje = new Date();
const fimPadrao = hoje.toISOString().slice(0, 10);
const inicioPadrao = new Date(hoje.getFullYear(), hoje.getMonth() - 3, hoje.getDate()).toISOString().slice(0, 10);

export default function AssistenteChat({ isCollapsed }: { isCollapsed: boolean }) {
  const [aberto, setAberto] = useState(false);
  const [escopo, setEscopo] = useState<ItemBusca | null>(null);
  const [buscaTexto, setBuscaTexto] = useState('');
  const [buscaResultados, setBuscaResultados] = useState<ItemBusca[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [dataIni, setDataIni] = useState(inicioPadrao);
  const [dataFim, setDataFim] = useState(fimPadrao);
  const [chat, setChat] = useState<Msg[]>([]);
  const [pergunta, setPergunta] = useState('');
  const [respondendo, setRespondendo] = useState(false);
  const [areaIdentificada, setAreaIdentificada] = useState<Area | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (escopo || buscaTexto.trim().length < 2) {
      setBuscaResultados([]);
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setBuscando(true);
      axios.get(`/api/v1/assistente/buscar?q=${encodeURIComponent(buscaTexto.trim())}`)
        .then(r => setBuscaResultados(r.data || []))
        .catch(() => setBuscaResultados([]))
        .finally(() => setBuscando(false));
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [buscaTexto, escopo]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [chat, respondendo]);

  const escolherEscopo = useCallback((item: ItemBusca) => {
    setEscopo(item);
    setBuscaTexto('');
    setBuscaResultados([]);
    setChat([]);
    setAreaIdentificada('demanda');
  }, []);

  const limparEscopo = useCallback(() => {
    setEscopo(null);
    setChat([]);
    setAreaIdentificada(null);
  }, []);

  const enviar = useCallback(async () => {
    const texto = pergunta.trim();
    if (!texto || respondendo || !dataIni || !dataFim) return;
    if (dataIni > dataFim) {
      setChat([...chat, { role: 'assistant', content: 'A **data inicial** deve ser anterior à **data final**.' }]);
      return;
    }

    const novoHistorico = [...chat, { role: 'user' as const, content: texto }];
    setChat(novoHistorico);
    setPergunta('');
    setRespondendo(true);
    try {
      const { data } = await axios.post('/api/v1/assistente/chat', {
        pergunta: texto,
        modo: 'auto',
        escopo_tipo: escopo?.tipo,
        escopo_id: escopo?.id,
        data_ini: dataIni,
        data_fim: dataFim,
        historico: chat.slice(-6),
      });
      setAreaIdentificada(data.area || null);
      setChat([...novoHistorico, { role: 'assistant', content: data.resposta }]);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || 'Erro ao consultar o Nexus Bot. Tente novamente.';
      setChat([...novoHistorico, { role: 'assistant', content: msg }]);
    } finally {
      setRespondendo(false);
    }
  }, [chat, dataFim, dataIni, escopo, pergunta, respondendo]);

  const podeEnviar = pergunta.trim().length > 0 && !respondendo && !!dataIni && !!dataFim;

  return (
    <>
      <button
        onClick={() => setAberto(v => !v)}
        title={isCollapsed ? 'Nexus Bot' : ''}
        className={`w-full flex items-center ${isCollapsed ? 'justify-center px-0' : 'justify-start px-4'} gap-3 py-3.5 rounded-2xl text-sm font-bold transition-all bg-gradient-to-r from-violet-600 to-indigo-600 text-white shadow-lg shadow-violet-600/30 hover:from-violet-500 hover:to-indigo-500`}
      >
        <Sparkles className="w-5 h-5 shrink-0" />
        {!isCollapsed && <span className="whitespace-nowrap truncate">Nexus Bot</span>}
      </button>

      {aberto && (
        <div className="fixed inset-4 z-[9999] mx-auto flex max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl md:inset-y-8">
          <div className="flex items-center justify-between bg-gradient-to-r from-violet-600 to-indigo-600 px-5 py-4 text-white shrink-0">
            <div className="flex items-center gap-2">
              <Sparkles className="w-5 h-5" />
              <div>
                <div className="text-base font-black">Nexus Bot</div>
                <div className="text-[11px] text-violet-100">Demanda, indicadores e financeiro em um único lugar</div>
              </div>
            </div>
            <button onClick={() => setAberto(false)} className="rounded-lg p-1.5 hover:bg-white/20" title="Fechar">
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="grid shrink-0 gap-3 border-b border-slate-100 bg-slate-50 px-5 py-4 md:grid-cols-[1fr_1fr_2fr]">
            <label className="text-[11px] font-bold text-slate-600">
              <span className="mb-1 flex items-center gap-1"><CalendarDays className="w-3.5 h-3.5" /> Período inicial</span>
              <input type="date" value={dataIni} onChange={e => setDataIni(e.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs outline-none focus:ring-2 focus:ring-violet-300" />
            </label>
            <label className="text-[11px] font-bold text-slate-600">
              <span className="mb-1 flex items-center gap-1"><CalendarDays className="w-3.5 h-3.5" /> Período final</span>
              <input type="date" value={dataFim} onChange={e => setDataFim(e.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs outline-none focus:ring-2 focus:ring-violet-300" />
            </label>
            <div className="relative text-[11px] font-bold text-slate-600">
              <span className="mb-1 flex items-center gap-1"><Compass className="w-3.5 h-3.5" /> Escopo da análise (opcional)</span>
              {escopo ? (
                <div className="flex h-[34px] items-center justify-between gap-2 rounded-lg border border-violet-200 bg-violet-50 px-2.5">
                  <span className="truncate text-xs text-violet-800">{escopo.label}</span>
                  <button onClick={limparEscopo} className="text-violet-600 hover:text-violet-800">remover</button>
                </div>
              ) : (
                <>
                  <Search className="absolute bottom-2.5 left-2.5 w-3.5 h-3.5 text-slate-400" />
                  <input value={buscaTexto} onChange={e => setBuscaTexto(e.target.value)}
                    placeholder="SKU ou categoria, para perguntas de demanda"
                    className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-8 pr-7 text-xs font-normal outline-none focus:ring-2 focus:ring-violet-300" />
                  {buscando && <Loader2 className="absolute bottom-2.5 right-2.5 w-3.5 h-3.5 animate-spin text-slate-400" />}
                  {buscaResultados.length > 0 && (
                    <div className="absolute z-10 mt-1 max-h-52 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
                      {buscaResultados.map(item => (
                        <button key={`${item.tipo}-${item.id}`} onClick={() => escolherEscopo(item)}
                          className="w-full border-b border-slate-50 px-3 py-2 text-left text-xs hover:bg-violet-50 last:border-0">
                          <span className="mr-1.5 rounded bg-violet-100 px-1.5 py-0.5 text-[9px] font-black uppercase text-violet-600">{item.tipo}</span>
                          <span className="font-bold text-slate-700">{item.label}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5">
            {chat.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <Sparkles className="mb-3 h-10 w-10 text-violet-300" />
                <div className="max-w-md text-sm font-bold text-slate-500">Faça a pergunta. O Nexus Bot identifica se deve consultar Demanda, Indicadores ou Financeiro.</div>
                <div className="mt-3 flex flex-wrap justify-center gap-2 text-[11px] text-slate-500">
                  <span className="rounded-full bg-indigo-50 px-2.5 py-1"><BarChart3 className="mr-1 inline w-3 h-3" /> WMAPE e BIAS</span>
                  <span className="rounded-full bg-violet-50 px-2.5 py-1"><Compass className="mr-1 inline w-3 h-3" /> Plano e demanda por SKU</span>
                  <span className="rounded-full bg-emerald-50 px-2.5 py-1"><Coins className="mr-1 inline w-3 h-3" /> PMR, PMP e carteira</span>
                </div>
              </div>
            )}
            {chat.map((m, i) => (
              <div key={i} className={`mb-3 flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[88%] rounded-xl border px-4 py-3 text-[13px] leading-relaxed ${m.role === 'user' ? 'border-indigo-100 bg-indigo-50 text-indigo-800' : 'border-slate-100 bg-slate-50 text-slate-700'}`}>
                  {m.role === 'assistant' ? renderChatMarkdown(m.content) : m.content}
                </div>
              </div>
            ))}
            {respondendo && <div className="flex items-center gap-2 text-xs font-bold text-slate-400"><Loader2 className="h-4 w-4 animate-spin text-violet-400" /> Consultando a base correta...</div>}
          </div>

          <div className="shrink-0 border-t border-slate-100 p-4">
            {areaIdentificada && <div className="mb-2 text-[10px] font-bold text-slate-400">Última consulta: {areaIdentificada === 'financeiro' ? 'Financeiro' : areaIdentificada === 'demanda' ? 'Demanda' : 'Indicadores'}</div>}
            <div className="flex gap-2">
              <input value={pergunta} onChange={e => setPergunta(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && podeEnviar) enviar(); }}
                placeholder="Ex.: Qual é o PMR dos clientes ativos? Como está a previsão de SHAKE?"
                className="min-w-0 flex-1 rounded-xl border border-slate-200 px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-violet-300" />
              <button onClick={enviar} disabled={!podeEnviar}
                className="rounded-xl bg-violet-600 p-3 text-white transition hover:bg-violet-500 disabled:bg-slate-200">
                <Send className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
