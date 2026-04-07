import { useState, useRef, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Settings, RefreshCw, Terminal, Play, Unlock, ShieldAlert, Loader2, Download, UserPlus, Check, X } from 'lucide-react';

export default function AdminPanel() {
  const [vendedorNome, setVendedorNome] = useState('');
  const [logs, setLogs] = useState<string[]>([]);
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isExporting, setIsExporting] = useState(false);
  
  const [usuariosPendentes, setUsuariosPendentes] = useState<any[]>([]);

  const terminalScrollRef = useRef<HTMLDivElement>(null);

  // EFEITO: Rola a barra do terminal automaticamente se o pipeline estiver a rodar
  useEffect(() => {
    if (isPipelineRunning && terminalScrollRef.current) {
      terminalScrollRef.current.scrollTop = terminalScrollRef.current.scrollHeight;
    }
  }, [logs, isPipelineRunning]);

  const fetchData = useCallback(async () => {
    try {
      let statusRes = { data: { logs: [], is_running: false } };
      try {
        statusRes = await axios.get('http://localhost:8000/api/v1/admin/pipeline/status');
      } catch(e) {}

      let pendentesRes = { data: { dados: [] } };
      try {
        pendentesRes = await axios.get('http://localhost:8000/api/v1/auth/pendentes');
      } catch(e) {}
      
      setLogs(statusRes.data.logs);
      
      // Se a resposta da API disser que está rodando, garantimos que o estado atualize. 
      // Se disser que não, e nós já tínhamos ativado no clique, ele desliga.
      setIsPipelineRunning(statusRes.data.is_running);
      
      setUsuariosPendentes(pendentesRes.data.dados);

    } catch (e) {
      console.error("Erro ao comunicar com o servidor", e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // EFEITO: POLLING CONDICIONAL
  useEffect(() => {
    fetchData(); // Carrega os dados 1x quando a tela abre

    let interval: ReturnType<typeof setInterval>;

    // SÓ fica "pingando" o servidor a cada 2 segundos se o pipeline estiver rodando
    if (isPipelineRunning) {
      interval = setInterval(() => { 
        fetchData(); 
      }, 2000); 
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [fetchData, isPipelineRunning]);

  // ==========================================
  // FUNÇÕES DE GOVERNANÇA (ACESSOS)
  // ==========================================
  const handleAprovarUsuario = async (id: string, nome: string) => {
    if (!window.confirm(`Deseja aprovar o acesso de ${nome}?`)) return;
    try {
      await axios.post(`http://localhost:8000/api/v1/auth/aprovar/${id}`);
      fetchData();
    } catch (e) { alert("Erro ao aprovar usuário."); }
  };

  const handleRejeitarUsuario = async (id: string, nome: string) => {
    if (!window.confirm(`Deseja REJEITAR e DELETAR o pedido de ${nome}? Esta ação não pode ser desfeita.`)) return;
    try {
      await axios.delete(`http://localhost:8000/api/v1/auth/rejeitar/${id}`);
      fetchData();
    } catch (e) { alert("Erro ao rejeitar usuário."); }
  };

  // ==========================================
  // FUNÇÕES DE GOVERNANÇA (DESTRANCAR TELAS)
  // ==========================================
  const handleReabrirTopDown = async () => {
    if (!window.confirm("Isso permitirá que a Diretoria altere os volumes novamente. Continuar?")) return;
    try {
      await axios.post('http://localhost:8000/api/v1/admin/reabrir-ciclo', null, { params: { origem: 'Top-Down' } });
      alert("✅ Ciclo Top-Down reaberto!");
    } catch (e: any) { alert("Erro: " + (e.response?.data?.detail || e.message)); }
  };

  const handleReabrirVendedor = async () => {
    if (!vendedorNome) return alert("Digite o nome do vendedor.");
    if (!window.confirm(`Reabrir o ciclo para ${vendedorNome}?`)) return;
    try {
      await axios.post('http://localhost:8000/api/v1/admin/reabrir-ciclo', null, { params: { origem: vendedorNome } });
      alert("✅ Ciclo Bottom-Up reaberto!");
      setVendedorNome('');
    } catch (e: any) { alert("Erro: " + (e.response?.data?.detail || e.message)); }
  };

  const handleReabrirGlobal = async () => {
    if (!window.confirm("🚨 Isso destrancará o Dashboard Global. Continuar?")) return;
    try {
      await axios.post('http://localhost:8000/api/v1/admin/reabrir-ciclo', null, { params: { origem: 'S&OP-Final' } });
      alert("✅ Dashboard Global (Consenso Final) destrancado com sucesso!");
    } catch (e: any) { alert("Erro: " + (e.response?.data?.detail || e.message)); }
  };

  // ==========================================
  // FUNÇÃO DO PIPELINE
  // ==========================================
  const rodarPipeline = async () => {
    if (!window.confirm("🚨 A plataforma ficará travada para todos os usuários. Tem certeza?")) return;
    try {
      setIsPipelineRunning(true); // Acorda o "ping" de 2 em 2 segundos
      await axios.post('http://localhost:8000/api/v1/admin/pipeline/start');
      fetchData(); 
    } catch (e: any) { 
      setIsPipelineRunning(false);
      alert("Erro ao iniciar pipeline."); 
    }
  };

  // ==========================================
  // EXPORTAÇÃO EXCEL
  // ==========================================
  const handleExportBottomUp = async () => {
    setIsExporting(true);
    try {
      const res = await axios.get('http://localhost:8000/api/v1/consensus/export/bottom-up', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      const dataAtual = new Date().toISOString().split('T')[0];
      link.setAttribute('download', `SOP_Nexus_BottomUp_Granular_${dataAtual}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.parentNode?.removeChild(link);
    } catch (e) {
      alert("Erro ao exportar o Excel do Bottom-Up.");
    } finally {
      setIsExporting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="h-screen w-full bg-slate-50 flex flex-col items-center justify-center font-sans">
        <div className="relative flex items-center justify-center">
          <div className="absolute w-40 h-40 bg-indigo-500/20 blur-3xl rounded-full animate-pulse"></div>
          <img src="https://www.lineaalimentos.com.br/media/wysiwyg/icones/logo-linea-headline.png" alt="Linea" className="h-16 relative z-10 animate-pulse drop-shadow-xl" />
        </div>
        <div className="mt-8 flex items-center gap-3 text-slate-400 font-black text-xs tracking-widest uppercase">
          <Loader2 className="w-4 h-4 animate-spin text-indigo-500" /> 
          Iniciando Control Tower...
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full bg-[#f8fafc] p-8 font-sans overflow-hidden min-h-screen">
      
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
        <div className="flex items-center gap-4">
          <div className="bg-slate-900 p-3 rounded-2xl shadow-lg"><Settings className="w-8 h-8 text-white" /></div>
          <div>
            <h1 className="text-3xl font-black text-slate-900 tracking-tighter">CONTROL TOWER</h1>
            <p className="text-slate-500 font-medium">Gestão de Acessos, Ciclos e Engenharia de Dados</p>
          </div>
        </div>

        <button 
          onClick={handleExportBottomUp}
          disabled={isExporting}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-lg shadow-emerald-600/30 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isExporting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />}
          Exportar Base Granular
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 flex-1 min-h-0 overflow-y-auto pr-2 pb-10 custom-scrollbar">
        
        {/* ========================================== */}
        {/* LADO ESQUERDO: GOVERNANÇA E CADEADOS */}
        {/* ========================================== */}
        <div className="flex flex-col gap-6">
          
          <div className={`bg-white p-6 rounded-[32px] shadow-sm border relative overflow-hidden transition-all ${usuariosPendentes.length > 0 ? 'border-amber-200' : 'border-gray-100'}`}>
            {usuariosPendentes.length > 0 && <div className="absolute top-0 left-0 w-1 h-full bg-amber-400"></div>}
            
            <h2 className="text-lg font-black text-slate-800 mb-1 flex items-center gap-2 uppercase tracking-widest">
              <UserPlus className={`w-5 h-5 ${usuariosPendentes.length > 0 ? 'text-amber-500' : 'text-slate-400'}`}/> 
              Solicitações de Acesso
            </h2>
            
            {usuariosPendentes.length === 0 ? (
              <p className="text-sm text-slate-400 mt-4 font-medium italic">Nenhum cadastro pendente no momento.</p>
            ) : (
              <div className="mt-6 flex flex-col gap-3">
                {usuariosPendentes.map((user) => (
                  <div key={user.id} className="bg-slate-50 border border-slate-200 p-4 rounded-2xl flex flex-col gap-3">
                    <div>
                      <h4 className="font-bold text-slate-800 text-sm leading-tight">{user.nome}</h4>
                      <p className="text-xs text-slate-500">{user.email}</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <span className="bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest">{user.funcao}</span>
                        {user.nome_vendedor && <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest truncate max-w-[150px]">{user.nome_vendedor}</span>}
                      </div>
                    </div>
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => handleAprovarUsuario(user.id, user.nome)} className="flex-1 bg-emerald-500 hover:bg-emerald-600 text-white py-2 rounded-xl text-xs font-black uppercase tracking-widest flex items-center justify-center gap-1 transition-all"><Check className="w-4 h-4"/> Aprovar</button>
                      <button onClick={() => handleRejeitarUsuario(user.id, user.nome)} className="flex-1 bg-rose-100 hover:bg-rose-200 text-rose-700 py-2 rounded-xl text-xs font-black uppercase tracking-widest flex items-center justify-center gap-1 transition-all"><X className="w-4 h-4"/> Rejeitar</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
            <h2 className="text-lg font-black text-slate-800 mb-4 flex items-center gap-2 uppercase tracking-widest"><Unlock className="w-5 h-5 text-emerald-500"/> Reabrir Ciclo Executivo</h2>
            <p className="text-sm text-slate-500 mb-6">Destranca a visão Top-Down para novas alterações e rateios da Diretoria.</p>
            <button disabled={isPipelineRunning} onClick={handleReabrirTopDown} className="w-full bg-emerald-50 hover:bg-emerald-100 text-emerald-700 py-3.5 rounded-2xl font-black text-sm uppercase tracking-widest transition-colors border border-emerald-200 disabled:opacity-50">
              Destrancar Top-Down
            </button>
          </div>

          <div className="bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
            <h2 className="text-lg font-black text-slate-800 mb-4 flex items-center gap-2 uppercase tracking-widest"><Unlock className="w-5 h-5 text-blue-500"/> Reabrir Consenso Vendedor</h2>
            <p className="text-sm text-slate-500 mb-4">Destranca a visão Bottom-Up de um vendedor específico.</p>
            <input type="text" placeholder="Ex: SIMONE ANDRADE DE PAULA" value={vendedorNome} onChange={e => setVendedorNome(e.target.value.toUpperCase())} disabled={isPipelineRunning} className="w-full bg-gray-50 border border-gray-200 text-sm font-bold p-3.5 rounded-2xl mb-4 outline-none focus:border-blue-400 focus:bg-white transition-all disabled:opacity-50" />
            <button onClick={handleReabrirVendedor} disabled={isPipelineRunning || !vendedorNome} className="w-full bg-blue-50 hover:bg-blue-100 text-blue-700 py-3.5 rounded-2xl font-black text-sm uppercase tracking-widest transition-colors border border-blue-200 disabled:opacity-50">
              Destrancar Vendedor
            </button>
          </div>

          <div className="bg-white p-6 rounded-[32px] shadow-sm border border-rose-100 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-1 h-full bg-rose-500"></div>
            <h2 className="text-lg font-black text-slate-800 mb-4 flex items-center gap-2 uppercase tracking-widest"><ShieldAlert className="w-5 h-5 text-rose-500"/> Reabrir Consenso Final</h2>
            <p className="text-sm text-slate-500 mb-6">Destranca o Dashboard Global (S&OP) para edições da Demanda Oficial.</p>
            <button onClick={handleReabrirGlobal} disabled={isPipelineRunning} className="w-full bg-rose-50 hover:bg-rose-100 text-rose-700 py-3.5 rounded-2xl font-black text-sm uppercase tracking-widest transition-colors border border-rose-200 disabled:opacity-50">
              Destrancar Dashboard Global
            </button>
          </div>

        </div>

        {/* ========================================== */}
        {/* LADO DIREITO: PIPELINE E LOGS */}
        {/* ========================================== */}
        <div className="lg:col-span-2 flex flex-col gap-6 h-[800px]">
          
          <div className="bg-white p-6 rounded-[32px] shadow-sm border border-gray-100 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
             <div>
                <h2 className="text-lg font-black text-slate-800 mb-1 flex items-center gap-2 uppercase tracking-widest"><ShieldAlert className="w-5 h-5 text-rose-500"/> Nexus Pipeline (ETL & IA)</h2>
                <p className="text-sm text-slate-500">Recarrega os dados do ERP, processa a STG e roda o treinamento do Forecast.</p>
             </div>
             <button onClick={rodarPipeline} disabled={isPipelineRunning} className={`flex items-center gap-2 px-8 py-4 rounded-2xl font-black text-sm uppercase tracking-widest transition-all shadow-lg whitespace-nowrap ${isPipelineRunning ? 'bg-slate-200 text-slate-400 cursor-not-allowed shadow-none' : 'bg-slate-900 hover:bg-slate-800 text-white shadow-slate-900/30'}`}>
                {isPipelineRunning ? <RefreshCw className="w-5 h-5 animate-spin" /> : <Play className="w-5 h-5" />}
                {isPipelineRunning ? 'Processando...' : 'RODAR PIPELINE GERAL'}
             </button>
          </div>

          <div className="flex-1 bg-slate-950 rounded-[32px] p-6 shadow-2xl border border-slate-800 flex flex-col overflow-hidden relative min-h-0">
            <div className="flex items-center gap-2 mb-4 pb-4 border-b border-slate-800 flex-shrink-0">
               <Terminal className="w-5 h-5 text-slate-400" />
               <span className="text-xs font-black text-slate-400 uppercase tracking-widest">NEXUS SERVER // CONSOLE OUTPUT</span>
               {isPipelineRunning && <span className="ml-auto flex items-center gap-2 text-xs font-black text-emerald-400 uppercase tracking-widest animate-pulse"><div className="w-2 h-2 bg-emerald-400 rounded-full"></div> PLATAFORMA BLOQUEADA (RUNNING)</span>}
            </div>

            <div ref={terminalScrollRef} className="flex-1 overflow-y-auto font-mono text-xs md:text-sm text-emerald-400/90 pr-2 space-y-1 custom-scrollbar">
              {logs.length === 0 ? (
                 <div className="h-full flex items-center justify-center text-slate-700 font-bold uppercase tracking-widest">Aguardando Execução...</div>
              ) : (
                logs.map((log, i) => (
                  <div key={i} className={`${log.includes('ERRO') || log.includes('❌') ? 'text-rose-400 font-bold' : log.includes('✅') ? 'text-blue-400 font-bold' : ''}`}>
                    {log}
                  </div>
                ))
              )}
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}