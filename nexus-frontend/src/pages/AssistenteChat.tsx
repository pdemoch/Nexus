// AssistenteChat.tsx — chat único da Sidebar: "Especialista em Indicadores"
// (portfólio, ciclo ativo) e "Especialista em Demanda" (um SKU ou categoria
// específico). Substitui o chat que existia dentro da tela de KPIs.
//
// Escolha de modo é MANUAL, não automática: roteamento por palavra-chave
// erraria com frequência ("como está o SUCRALOSE?" pode ser pergunta de
// categoria-no-portfólio ou de SKU específico) e responder no modo errado
// é pior do que pedir um clique a mais.
//
// Trocar de modo ou de item (no modo Demanda) reinicia a conversa: o
// histórico enviado ao backend não carrega contexto de modo/escopo por
// mensagem, então misturar contextos confundiria o modelo.

import { useState, useCallback, useEffect, useRef } from 'react';
import axios from 'axios';
import { Sparkles, X, Send, Loader2, Search, Compass, BarChart3 } from 'lucide-react';
import { renderChatMarkdown } from './chatMarkdown';

type Modo = 'indicadores' | 'demanda';
type Msg = { role: 'user' | 'assistant'; content: string };
type ItemBusca = { tipo: 'sku' | 'categoria'; id: string; label: string; categoria: string };

export default function AssistenteChat({ isCollapsed }: { isCollapsed: boolean }) {
  const [aberto, setAberto]         = useState(false);
  const [modo, setModo]             = useState<Modo>('indicadores');
  const [escopo, setEscopo]         = useState<ItemBusca | null>(null);
  const [buscaTexto, setBuscaTexto] = useState('');
  const [buscaResultados, setBuscaResultados] = useState<ItemBusca[]>([]);
  const [buscando, setBuscando]     = useState(false);
  const [chat, setChat]             = useState<Msg[]>([]);
  const [pergunta, setPergunta]     = useState('');
  const [respondendo, setRespondendo] = useState(false);

  const debounceRef = useRef<any>(null);
  const scrollRef    = useRef<HTMLDivElement>(null);

  const trocarModo = useCallback((m: Modo) => {
    setModo(m);
    setEscopo(null);
    setChat([]);
    setBuscaTexto('');
    setBuscaResultados([]);
  }, []);

  const escolherEscopo = useCallback((item: ItemBusca) => {
    setEscopo(item);
    setChat([]);
    setBuscaTexto('');
    setBuscaResultados([]);
  }, []);

  const trocarEscopo = useCallback(() => {
    setEscopo(null);
    setChat([]);
  }, []);

  // Busca com debounce de 300ms — só no modo Demanda, antes de escolher item
  useEffect(() => {
    if (modo !== 'demanda' || escopo || buscaTexto.trim().length < 2) {
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
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [buscaTexto, modo, escopo]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [chat, respondendo]);

  const enviar = useCallback(async () => {
    const p = pergunta.trim();
    if (!p || respondendo) return;
    if (modo === 'demanda' && !escopo) return;

    const novoHistorico = [...chat, { role: 'user' as const, content: p }];
    setChat(novoHistorico);
    setPergunta('');
    setRespondendo(true);
    try {
      const { data } = await axios.post('/api/v1/assistente/chat', {
        pergunta: p,
        modo,
        escopo_tipo: modo === 'demanda' ? escopo?.tipo : undefined,
        escopo_id:   modo === 'demanda' ? escopo?.id   : undefined,
        historico: chat.slice(-6),
      });
      setChat([...novoHistorico, { role: 'assistant', content: data.resposta }]);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || 'Erro ao consultar o assistente. Tente novamente.';
      setChat([...novoHistorico, { role: 'assistant', content: msg }]);
    } finally {
      setRespondendo(false);
    }
  }, [pergunta, respondendo, modo, escopo, chat]);

  const podeEnviar = pergunta.trim().length > 0 && !respondendo && (modo === 'indicadores' || !!escopo);

  return (
    <>
      {/* BOTÃO FLUTUANTE — mesmo padrão visual dos itens de nav da Sidebar,
          com gradiente próprio para se diferenciar dos itens de navegação */}
      <button
        onClick={() => setAberto(v => !v)}
        title={isCollapsed ? 'Assistente Nexus' : ''}
        className={`w-full flex items-center ${isCollapsed ? 'justify-center px-0' : 'justify-start px-4'} gap-3 py-3.5 rounded-2xl text-sm font-bold transition-all
          bg-gradient-to-r from-violet-600 to-indigo-600 text-white shadow-lg shadow-violet-600/30
          hover:from-violet-500 hover:to-indigo-500`}
      >
        <Sparkles className="w-5 h-5 shrink-0" />
        {!isCollapsed && <span className="whitespace-nowrap truncate">Assistente</span>}
      </button>

      {/* PAINEL FLUTUANTE — sobrevive à troca de tela, fixo no viewport */}
      {aberto && (
        <div
          className="fixed bottom-6 right-6 z-[9999] w-[420px] max-w-[calc(100vw-48px)]
                     bg-white rounded-2xl shadow-2xl border border-slate-200 flex flex-col overflow-hidden"
          style={{ height: 560 }}
        >
          {/* CABEÇALHO */}
          <div className="px-4 py-3 bg-gradient-to-r from-violet-600 to-indigo-600 text-white
                          flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4" />
              <span className="text-sm font-black">Assistente Nexus</span>
            </div>
            <button onClick={() => setAberto(false)} className="p-1 rounded-lg hover:bg-white/20">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* SELETOR DE MODO + BUSCA */}
          <div className="px-3 pt-3 pb-2 shrink-0 border-b border-slate-100">
            <div className="flex gap-1.5">
              <button
                onClick={() => trocarModo('indicadores')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl text-[11px] font-bold transition-all
                  ${modo === 'indicadores' ? 'bg-indigo-600 text-white shadow' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}
              >
                <BarChart3 className="w-3.5 h-3.5" /> Indicadores
              </button>
              <button
                onClick={() => trocarModo('demanda')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl text-[11px] font-bold transition-all
                  ${modo === 'demanda' ? 'bg-violet-600 text-white shadow' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}
              >
                <Compass className="w-3.5 h-3.5" /> Demanda
              </button>
            </div>

            {/* Busca — só aparece no modo Demanda, antes de escolher o item */}
            {modo === 'demanda' && !escopo && (
              <div className="relative mt-2">
                <div className="relative">
                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input
                    value={buscaTexto}
                    onChange={e => setBuscaTexto(e.target.value)}
                    placeholder="Buscar SKU ou categoria…"
                    className="w-full pl-8 pr-8 py-2 text-xs rounded-xl border border-slate-200
                               focus:outline-none focus:ring-2 focus:ring-violet-300"
                  />
                  {buscando && (
                    <Loader2 className="w-3.5 h-3.5 absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 animate-spin" />
                  )}
                </div>
                {buscaResultados.length > 0 && (
                  <div className="absolute z-10 mt-1 w-full bg-white rounded-xl border border-slate-200 shadow-lg
                                  max-h-52 overflow-y-auto">
                    {buscaResultados.map(item => (
                      <button
                        key={`${item.tipo}-${item.id}`}
                        onClick={() => escolherEscopo(item)}
                        className="w-full text-left px-3 py-2 text-xs hover:bg-violet-50 border-b border-slate-50 last:border-0"
                      >
                        <span className={`inline-block text-[9px] font-black uppercase tracking-wide mr-1.5 px-1.5 py-0.5 rounded
                          ${item.tipo === 'categoria' ? 'bg-violet-100 text-violet-600' : 'bg-slate-100 text-slate-500'}`}>
                          {item.tipo === 'categoria' ? 'Categoria' : 'SKU'}
                        </span>
                        <span className="font-bold text-slate-700">{item.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Chip do item escolhido */}
            {modo === 'demanda' && escopo && (
              <div className="mt-2 flex items-center justify-between gap-2 bg-violet-50 border border-violet-100
                              rounded-xl px-3 py-2">
                <div className="min-w-0">
                  <div className="text-[9px] font-black uppercase tracking-wide text-violet-500">
                    {escopo.tipo === 'categoria' ? 'Categoria' : 'SKU'}
                  </div>
                  <div className="text-xs font-bold text-slate-700 truncate">{escopo.label}</div>
                </div>
                <button onClick={trocarEscopo}
                  className="shrink-0 text-[10px] font-bold text-violet-500 hover:text-violet-700">
                  trocar
                </button>
              </div>
            )}
          </div>

          {/* MENSAGENS */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3">
            {chat.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center px-4">
                <Sparkles className="w-8 h-8 text-violet-300 mb-2" />
                <div className="text-xs font-bold text-slate-400">
                  {modo === 'indicadores'
                    ? 'Pergunte sobre WMAPE, BIAS, fill rate ou impacto financeiro do portfólio.'
                    : escopo
                      ? `Pergunte sobre ${escopo.label}.`
                      : 'Busque um SKU ou categoria acima para começar.'}
                </div>
              </div>
            )}
            {chat.map((m, i) => (
              <div key={i} className={`mb-3 flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[88%] px-3 py-2 rounded-xl text-[12.5px] leading-relaxed
                  ${m.role === 'user'
                    ? 'bg-indigo-50 text-indigo-800 border border-indigo-100'
                    : 'bg-slate-50 text-slate-700 border border-slate-100'}`}>
                  {m.role === 'assistant' ? renderChatMarkdown(m.content) : m.content}
                </div>
              </div>
            ))}
            {respondendo && (
              <div className="flex justify-start mb-3">
                <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-100 flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-400" />
                  <span className="text-[11px] text-slate-400 font-bold">Pensando…</span>
                </div>
              </div>
            )}
          </div>

          {/* INPUT */}
          <div className="p-3 border-t border-slate-100 shrink-0 flex items-center gap-2">
            <input
              value={pergunta}
              onChange={e => setPergunta(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && podeEnviar) enviar(); }}
              placeholder={modo === 'demanda' && !escopo ? 'Selecione um item acima primeiro…' : 'Pergunte alguma coisa…'}
              disabled={modo === 'demanda' && !escopo}
              className="flex-1 px-3 py-2.5 text-xs rounded-xl border border-slate-200
                         focus:outline-none focus:ring-2 focus:ring-violet-300 disabled:bg-slate-50 disabled:text-slate-400"
            />
            <button onClick={enviar} disabled={!podeEnviar}
              className={`shrink-0 p-2.5 rounded-xl transition-all
                ${podeEnviar ? 'bg-violet-600 hover:bg-violet-500 text-white' : 'bg-slate-100 text-slate-300'}`}>
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}