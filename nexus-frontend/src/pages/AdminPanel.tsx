import { useState, useRef, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Settings, RefreshCw, Terminal, Play, ShieldAlert, Loader2, Download, UserPlus, Check, X, Unlock } from 'lucide-react';

export default function AdminPanel() {
  const [logs, setLogs] = useState<string[]>([]);
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isExporting, setIsExporting] = useState(false);
  
  const [usuariosPendentes, setUsuariosPendentes] = useState<any[]>([]);
  const [vendedores, setVendedores] = useState<string[]>([]);
  
  // ESTADOS DO DESCONGELAMENTO
  const [origemDesbloqueio, setOrigemDesbloqueio] = useState('');
  const [isUnlocking, setIsUnlocking] = useState(false);

  const terminalScrollRef = useRef<HTMLDivElement>(null);

  // EFEITO: Rola a barra do terminal automaticamente se o pipeline estiver a rodar
  useEffect(() => {
    if (isPipelineRunning && terminalScrollRef.current) {
      terminalScrollRef.current.scrollTop = terminalScrollRef.current.scrollHeight;
    }
  }, [logs, isPipelineRunning]);

  const fetchData = useCallback(async () => {
    try {
      // 1. Busca o status do Pipeline ML
      try {
        const statusRes = await axios.get('/api/v1/admin/pipeline/status');
        setLogs(statusRes.data.logs || []);
        setIsPipelineRunning(statusRes.data.is_running);
      } catch(e) { console.error("Erro ao buscar status do pipeline"); }

      // 2. Busca utilizadores pendentes de aprovação
      try {
        const pendentesRes = await axios.get('/api/v1/auth/pendentes');
        setUsuariosPendentes(pendentesRes.data.dados || []);
      } catch(e) { console.error("Erro ao buscar pendentes"); }

      // 3. Busca a lista de vendedores para a caixa de seleção de destrancamento
      try {
        const filtrosRes = await axios.get('/api/v1/consensus/gerenciamento/filtros');
        setVendedores(filtrosRes.data.vendedores || []);
      } catch(e) { console.error("Erro ao buscar vendedores"); }

    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Faz polling do status a cada 3 segundos APENAS se o pipeline estiver a rodar
  useEffect(() => {
    fetchData();
    let interval: ReturnType<typeof setInterval>;
    if (isPipelineRunning) {
      interval = setInterval(fetchData, 3000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [fetchData, isPipelineRunning]);

  const handleRunPipeline = async () => {
    if (!window.confirm("⚠️ ATENÇÃO: Iniciar a Engenharia de Dados irá bloquear todo o sistema para os utilizadores. Deseja continuar?")) return;
    
    setIsPipelineRunning(true);
    setLogs(["[SISTEMA] Iniciando requisição para o núcleo de IA..."]);
    
    try {
      await axios.post('/api/v1/admin/pipeline/start');
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao iniciar o pipeline.");
      setIsPipelineRunning(false);
    }
  };

  const handleAprovarUsuario = async (id: number) => {
    if (!window.confirm("Aprovar o acesso deste utilizador ao Nexus?")) return;
    try {
      await axios.post(`/api/v1/auth/aprovar/${id}`);
      fetchData();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao aprovar utilizador.");
    }
  };

  const handleRejeitarUsuario = async (id: number) => {
    if (!window.confirm("Tem a certeza que deseja REJEITAR e excluir este cadastro?")) return;
    try {
      await axios.delete(`/api/v1/auth/rejeitar/${id}`);
      fetchData();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao rejeitar utilizador.");
    }
  };

  const handleExportarBase = async () => {
    setIsExporting(true);
    try {
      const response = await axios.get('/api/v1/admin/exportar-base', {
        responseType: 'blob', // Importante para o download de ficheiros
      });
      
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `SOP_Nexus_Base_Granular_${new Date().toISOString().split('T')[0]}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (e) {
      alert("Erro ao exportar a base de dados. Verifique se existem dados no ciclo atual.");
    } finally {
      setIsExporting(false);
    }
  };

  const handleDescongelar = async () => {
    if (!origemDesbloqueio) return alert("Selecione a origem que deseja descongelar.");
    if (!window.confirm(`Deseja realmente forçar a reabertura da tela para: ${origemDesbloqueio}?`)) return;

    setIsUnlocking(true);
    try {
      await axios.post('/api/v1/admin/reabrir-ciclo', null, { 
        params: { origem: origemDesbloqueio } 
      });
      alert(`✅ Tela destrancada com sucesso para: ${origemDesbloqueio}`);
      setOrigemDesbloqueio('');
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao destrancar a tela.");
    } finally {
      setIsUnlocking(false);
    }
  };

  const isGlobalSelected = ['Top-Down', 'Supply Review', 'S&OP-Final'].includes(origemDesbloqueio);
  const isVendedorSelected = !isGlobalSelected && origemDesbloqueio !== '';

  if (isLoading) {
    return (
      <div className="h-screen w-full bg-[#f8fafc] flex flex-col items-center justify-center font-sans">
        <Loader2 className="w-8 h-8 animate-spin text-slate-500 mb-4" />
        <span className="text-slate-400 font-black text-xs tracking-widest uppercase">Carregando Control Tower...</span>
      </div>
    );
  }

  return (
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20">
      <div className="max-w-[1600px] mx-auto p-6 lg:p-12 relative flex flex-col">
        
        {/* CABEÇALHO */}
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-slate-100 flex-shrink-0">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Settings className="w-8 h-8 text-slate-800" /> CONTROL TOWER <span className="text-slate-300 font-medium ml-2">Administração</span>
            </h1>
            <p className="text-slate-500 font-medium text-sm mt-1 ml-11">Gestão de utilizadores, travas de segurança e motor de IA.</p>
          </div>
          
          <button 
            onClick={handleExportarBase} 
            disabled={isExporting}
            className="flex items-center gap-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-sm disabled:opacity-50"
          >
            {isExporting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />} 
            {isExporting ? 'A Gerar...' : 'Exportar Base S&OP'}
          </button>
        </div>

        {/* MUDANÇA: Layout solto com items-stretch em vez de forçar altura min-h-0 */}
        <div className="flex flex-col lg:flex-row gap-6 items-stretch">
          
          {/* COLUNA ESQUERDA: GESTÃO DE UTILIZADORES E TRAVAS */}
          <div className="w-full lg:w-1/3 flex flex-col gap-6">
             
             {/* CARD 1: APROVAÇÕES PENDENTES (Altura Fixa Garantida) */}
             <div className="bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col h-[400px]">
                 <div className="flex items-center gap-3 mb-6 pb-4 border-b border-slate-100 flex-shrink-0">
                   <UserPlus className="w-5 h-5 text-indigo-500" />
                   <h2 className="text-sm font-black text-slate-700 uppercase tracking-widest">Aprovações Pendentes</h2>
                   <div className="ml-auto bg-indigo-100 text-indigo-700 font-black text-xs px-2 py-1 rounded-lg">{usuariosPendentes.length}</div>
                 </div>

                 <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-4">
                    {usuariosPendentes.length === 0 ? (
                      <div className="h-full flex flex-col items-center justify-center text-slate-400 gap-3 opacity-60">
                         <ShieldAlert className="w-10 h-10" />
                         <span className="text-xs font-black uppercase tracking-widest text-center">Nenhum acesso<br/>pendente</span>
                      </div>
                    ) : (
                      usuariosPendentes.map((user) => (
                        <div key={user.id} className="bg-slate-50 border border-slate-200 rounded-2xl p-4 flex flex-col gap-3">
                           <div>
                             <h3 className="font-black text-sm text-slate-800">{user.nome}</h3>
                             <p className="text-xs font-bold text-slate-500">{user.email}</p>
                           </div>
                           
                           <div className="bg-white p-2 rounded-xl border border-slate-100 flex flex-col gap-1">
                              <span className="text-[10px] font-black text-indigo-500 uppercase tracking-widest">Perfil: {user.funcao}</span>
                              {user.nome_vendedor && <span className="text-[10px] font-bold text-slate-600 truncate">Vendedor: {user.nome_vendedor}</span>}
                              {user.gerente_nome && <span className="text-[10px] font-bold text-slate-600 truncate">Gerente: {user.gerente_nome}</span>}
                           </div>

                           <div className="flex items-center gap-2 mt-1">
                              <button onClick={() => handleRejeitarUsuario(user.id)} className="flex-1 flex items-center justify-center gap-1 bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors">
                                <X className="w-3 h-3" /> Rejeitar
                              </button>
                              <button onClick={() => handleAprovarUsuario(user.id)} className="flex-1 flex items-center justify-center gap-1 bg-emerald-500 hover:bg-emerald-600 text-white py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors shadow-md shadow-emerald-500/20">
                                <Check className="w-3 h-3" /> Aprovar
                              </button>
                           </div>
                        </div>
                      ))
                    )}
                 </div>
             </div>

             {/* CARD 2: DESCONGELAMENTO DE TELAS */}
             <div className="bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col">
               <div className="flex items-center gap-3 mb-6 pb-4 border-b border-slate-100 flex-shrink-0">
                 <Unlock className="w-5 h-5 text-amber-500" />
                 <h2 className="text-sm font-black text-slate-700 uppercase tracking-widest">Descongelar Telas</h2>
               </div>

               <div className="flex flex-col gap-4">
                 <p className="text-xs font-medium text-slate-500 leading-relaxed">
                   Force a reabertura de uma etapa do S&OP que já foi assinada e congelada.
                 </p>

                 <select
                   value={isGlobalSelected ? origemDesbloqueio : ''}
                   onChange={(e) => setOrigemDesbloqueio(e.target.value)}
                   className="bg-slate-50 border border-slate-200 text-slate-700 text-sm rounded-xl focus:ring-amber-500 focus:border-amber-500 block w-full p-3 font-bold outline-none cursor-pointer"
                 >
                   <option value="">Selecione um nível global...</option>
                   <option value="Top-Down">Visão Gerencial (Top-Down)</option>
                   <option value="Supply Review">Fábrica (Supply Review)</option>
                   <option value="S&OP-Final">S&OP Global (Dashboard Final)</option>
                 </select>

                 <select
                   value={isVendedorSelected ? origemDesbloqueio : ''}
                   onChange={(e) => setOrigemDesbloqueio(e.target.value)}
                   className="bg-slate-50 border border-slate-200 text-slate-700 text-sm rounded-xl focus:ring-amber-500 focus:border-amber-500 block w-full p-3 font-bold outline-none cursor-pointer"
                 >
                   <option value="">Ou selecione a carteira de um Vendedor...</option>
                   {vendedores.map(v => (
                     <option key={v} value={v}>{v}</option>
                   ))}
                 </select>

                 <button
                   onClick={handleDescongelar}
                   disabled={isUnlocking || !origemDesbloqueio}
                   className="mt-2 w-full flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-600 text-white py-3.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all shadow-md shadow-amber-500/20 disabled:opacity-50"
                 >
                   {isUnlocking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlock className="w-4 h-4" />}
                   Forçar Reabertura
                 </button>
               </div>
             </div>

          </div>

          {/* COLUNA DIREITA: MOTOR DE IA & TERMINAL */}
          <div className="w-full lg:w-2/3 bg-slate-950 rounded-[32px] p-2 shadow-2xl border border-slate-800 flex flex-col relative min-h-[500px]">
            
            {/* PAINEL DE CONTROLE DO MOTOR */}
            <div className="bg-slate-900 rounded-[24px] p-6 border border-slate-800 m-2 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 flex-shrink-0">
               <div>
                  <h2 className="text-sm font-black text-white uppercase tracking-widest flex items-center gap-2">
                    <RefreshCw className={`w-4 h-4 text-emerald-400 ${isPipelineRunning ? 'animate-spin' : ''}`} />
                    Motor de Engenharia S&OP
                  </h2>
                  <p className="text-xs font-medium text-slate-400 mt-1">Aciona a pipeline de Machine Learning e recria a matriz estatística global.</p>
               </div>
               <button 
                 onClick={handleRunPipeline}
                 disabled={isPipelineRunning}
                 className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-white px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-[0_0_20px_rgba(16,185,129,0.3)] disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
               >
                 {isPipelineRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                 {isPipelineRunning ? 'Processando...' : 'Run Pipeline'}
               </button>
            </div>

            {/* TERMINAL DE LOGS */}
            <div className="flex-1 bg-slate-950 p-6 flex flex-col overflow-hidden relative">
              <div className="flex items-center gap-2 mb-4 pb-4 border-b border-slate-800 flex-shrink-0">
                 <Terminal className="w-5 h-5 text-slate-500" />
                 <span className="text-xs font-black text-slate-500 uppercase tracking-widest">NEXUS SERVER // CONSOLE OUTPUT</span>
                 {isPipelineRunning && <span className="ml-auto flex items-center gap-2 text-[10px] font-black text-emerald-400 uppercase tracking-widest animate-pulse"><div className="w-2 h-2 bg-emerald-400 rounded-full"></div> RUNNING</span>}
              </div>

              <div ref={terminalScrollRef} className="flex-1 overflow-y-auto font-mono text-xs md:text-sm text-emerald-400/90 pr-2 space-y-1 custom-scrollbar">
                {logs.length === 0 ? (
                   <div className="h-full flex items-center justify-center text-slate-800 font-bold uppercase tracking-widest">Aguardando Execução...</div>
                ) : (
                  logs.map((log, i) => (
                    <div key={i} className={`${log.includes('ERRO') || log.includes('❌') ? 'text-rose-400' : log.includes('✅') ? 'text-cyan-400' : 'text-emerald-400/80'} py-0.5 border-l-2 border-slate-800 pl-3 break-words`}>
                      {log}
                    </div>
                  ))
                )}
              </div>
            </div>

          </div>

        </div>
      </div>
    </div>
  );
}