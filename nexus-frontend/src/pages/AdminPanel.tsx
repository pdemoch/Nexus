import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import { 
  Settings, RefreshCw, Terminal, Play, ShieldAlert, Loader2, Download, DollarSign,
  UserPlus, Check, X, Unlock, Calendar, Users, KeyRound, Database, Shield, History, Search, ArrowRight, TrendingUp, TrendingDown, Trash2, BrainCircuit 
} from 'lucide-react';

export default function AdminPanel() {
  // ESTADOS GERAIS
  const [activeTab, setActiveTab] = useState<'motor' | 'acessos' | 'auditoria'>('motor');
  const [isLoading, setIsLoading] = useState(true);
  const [isExporting, setIsExporting] = useState(false);
  const [isExportingIA, setIsExportingIA] = useState(false);
  
  // ESTADOS DO MOTOR E PIPELINE
  const [logs, setLogs] = useState<string[]>([]);
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [isRecargaTotal, setIsRecargaTotal] = useState(false);
  // Pipeline Financeiro (estado independente do S&OP)
  const [logsFinanceiro, setLogsFinanceiro]             = useState<string[]>([]);
  const [isFinanceiroRunning, setIsFinanceiroRunning]   = useState(false);
  const [isFinanceiroRecarga, setIsFinanceiroRecarga]   = useState(false);
  const terminalScrollRef = useRef<HTMLDivElement>(null);

  // ESTADOS DE ACESSOS E USUÁRIOS
  const [usuariosPendentes, setUsuariosPendentes] = useState<any[]>([]);
  const [usuariosAtivos, setUsuariosAtivos] = useState<any[]>([]);
  
  // ESTADOS DO DESCONGELAMENTO E MÁQUINA DO TEMPO
  const [origemDesbloqueio, setOrigemDesbloqueio] = useState('');
  const [isUnlocking, setIsUnlocking] = useState(false);
  const [cicloAtivo, setCicloAtivo] = useState('');
  const [novoCicloInput, setNovoCicloInput] = useState('');
  const [isChangingCiclo, setIsChangingCiclo] = useState(false);

  // ESTADOS DE AUDITORIA
  const [logsAuditoria, setLogsAuditoria] = useState<any[]>([]);
  const [sessoesAuditoria, setSessoesAuditoria] = useState<any[]>([]);
  const [buscaAuditoria, setBuscaAuditoria] = useState('');

  // Auto-scroll do terminal
  useEffect(() => {
    if (isPipelineRunning && terminalScrollRef.current) {
      terminalScrollRef.current.scrollTop = terminalScrollRef.current.scrollHeight;
    }
  }, [logs, isPipelineRunning]);

  const fetchData = useCallback(async () => {
    try {
      // 1. Pipeline Status — sempre busca, independente do estado local
      try {
        const statusRes = await axios.get('/api/v1/admin/pipeline/status');
        setLogs(statusRes.data.logs || []);
        const running = statusRes.data.is_running;
        setIsPipelineRunning(running);
        if (!running) setIsRecargaTotal(false);
      } catch(e) {}

      // 2. Usuários
      try {
        const [pendentesRes, ativosRes] = await Promise.all([
          axios.get('/api/v1/auth/pendentes'),
          axios.get('/api/v1/auth/ativos')
        ]);
        setUsuariosPendentes(pendentesRes.data.dados || []);
        setUsuariosAtivos(ativosRes.data.dados || []);
      } catch(e) {}

      // 3. Ciclo Ativo
      try {
        const cicloRes = await axios.get('/api/v1/admin/ciclo-ativo');
        setCicloAtivo(cicloRes.data.ciclo_ativo);
        setNovoCicloInput(cicloRes.data.ciclo_ativo);
      } catch(e) {}

      // 4. Logs de Auditoria
      try {
        const auditRes = await axios.get('/api/v1/admin/auditoria/logs');
        setLogsAuditoria(auditRes.data.dados || []);
      } catch (e) {}
      try {
        const sessoesRes = await axios.get('/api/v1/admin/auditoria/sessoes');
        setSessoesAuditoria(sessoesRes.data.dados || []);
      } catch (e) {}

    } catch (e) {
      console.error("Erro no carregamento do Admin Panel:", e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Polling sempre ativo: 3s quando pipeline rodando, 15s em idle
  // Não depende de isPipelineRunning para evitar stale closure no interval
  useEffect(() => {
    fetchData(); // carga inicial
    const interval = setInterval(async () => {
      try {
        const statusRes = await axios.get('/api/v1/admin/pipeline/status');
        const running = statusRes.data.is_running;
        setLogs(statusRes.data.logs || []);
        setIsPipelineRunning(running);
        if (!running) setIsRecargaTotal(false);
      } catch(e) {}
    }, 3000); // 3s fixo — leve o suficiente, garante logs em tempo real

    return () => clearInterval(interval);
  }, [fetchData]);

  // Polling financeiro — mesmo intervalo de 3s
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await axios.get('/api/v1/financeiro/status');
        setLogsFinanceiro(res.data.logs || []);
        setIsFinanceiroRunning(res.data.is_running);
        if (!res.data.is_running) setIsFinanceiroRecarga(false);
      } catch(e) {}
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleFinanceiroAtualizar = async () => {
    if (isFinanceiroRunning) return;
    setIsFinanceiroRunning(true);
    setLogsFinanceiro(['[FINANCEIRO] Iniciando atualizacao dos ultimos 3 meses...']);
    try {
      await axios.post('/api/v1/financeiro/pipeline');
    } catch(e: any) {
      setLogsFinanceiro([e?.response?.data?.detail || 'Erro ao iniciar atualizacao financeira.']);
      setIsFinanceiroRunning(false);
    }
  };

  const handleFinanceiroRecarga = async () => {
    if (isFinanceiroRunning) return;
    const ok = window.confirm(
      'Recarga Total Financeira: extraira todo o historico desde jan/2023.\n' +
      'Operacao longa (pode levar varios minutos). Confirma?'
    );
    if (!ok) return;
    setIsFinanceiroRunning(true);
    setIsFinanceiroRecarga(true);
    setLogsFinanceiro(['[FINANCEIRO] Iniciando recarga total desde jan/2023...']);
    try {
      await axios.post('/api/v1/financeiro/pipeline/recarga');
    } catch(e: any) {
      setLogsFinanceiro([e?.response?.data?.detail || 'Erro ao iniciar recarga financeira.']);
      setIsFinanceiroRunning(false);
      setIsFinanceiroRecarga(false);
    }
  };

  const handleRunPipeline = async () => {
    const msgConfirmacao = `⚠️ EXECUÇÃO DO MOTOR DE DADOS:
    
1. O pipeline atualizará as Vendas Reais do ERP no histórico.
2. Se o calendário real já tiver mudado de mês, um NOVO Ciclo S&OP será gerado automaticamente.

O robô ignorará o ciclo selecionado na 'Máquina do Tempo' para garantir a integridade do banco de dados. Deseja prosseguir?`;

    if (!window.confirm(msgConfirmacao)) return;
    
    setIsPipelineRunning(true);
    setLogs(["[SISTEMA] Conectando ao núcleo de IA e sincronizando ERP..."]);
    
    try {
      await axios.post('/api/v1/admin/pipeline/start');
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao iniciar o pipeline.");
      setIsPipelineRunning(false);
    }
  };

  const handleRecargaTotal = async () => {
    const confirm1 = window.confirm(
`🔴 RECARGA TOTAL DA BASE DE VENDAS

Esta operação vai:
1. APAGAR toda a tabela fato_vendas (TRUNCATE)
2. Recarregar o histórico completo desde jan/2023
3. Remover permanentemente os filtros de Operação 51, Fifeiro e EIC

⏱ Tempo estimado: 30–60 minutos (3+ anos de dados dia a dia).

Tem certeza que deseja continuar?`
    );
    if (!confirm1) return;

    const confirm2 = window.confirm(
`⚠️ CONFIRMAÇÃO FINAL

Esta é uma operação IRREVERSÍVEL e DESTRUTIVA.
Toda a fato_vendas será apagada antes da reinserção.

Se o pipeline falhar no meio, a base ficará vazia até uma nova recarga.

Digite OK para confiruar.

Confirma a RECARGA TOTAL?`
    );
    if (!confirm2) return;

    setIsPipelineRunning(true);
    setIsRecargaTotal(true);
    setLogs(["[RECARGA TOTAL] 🔴 Iniciando recarga histórica completa desde jan/2023..."]);

    try {
      await axios.post('/api/v1/admin/pipeline/recarga-total');
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao iniciar a recarga total.");
      setIsPipelineRunning(false);
      setIsRecargaTotal(false);
    }
  };

  const handleAprovarUsuario = async (id: number) => {
    if (!window.confirm("Aprovar o acesso deste utilizador ao Nexus?")) return;
    try {
      await axios.post(`/api/v1/auth/aprovar/${id}`);
      fetchData();
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao aprovar utilizador."); }
  };

  const handleRejeitarUsuario = async (id: number) => {
    if (!window.confirm("Tem a certeza que deseja REJEITAR e excluir este cadastro?")) return;
    try {
      await axios.delete(`/api/v1/auth/rejeitar/${id}`);
      fetchData();
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao rejeitar utilizador."); }
  };

  const handleResetSenha = async (id: number, nome: string) => {
    if (!window.confirm(`⚠️ ATENÇÃO: Tem a certeza que deseja resetar a senha de ${nome}? \n\nA senha voltará para o padrão "Linea@123" e o utilizador será obrigado a trocá-la no próximo acesso.`)) return;
    try {
      await axios.post(`/api/v1/auth/reset-password/${id}`);
      alert(`✅ Senha de ${nome} resetada para "Linea@123" com sucesso!`);
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao resetar a senha."); }
  };

  const handleDeleteUser = async (id: number, nome: string) => {
    if (!window.confirm(`⚠️ EXCLUSÃO PERMANENTE: Deseja realmente remover o utilizador "${nome}" do Nexus? \n\nEsta ação não pode ser desfeita.`)) return;
    try {
      await axios.delete(`/api/v1/admin/delete-user/${id}`);
      alert(`✅ Utilizador ${nome} removido com sucesso.`);
      fetchData();
    } catch (e: any) { alert(e.response?.data?.detail || "Erro ao excluir o utilizador."); }
  };

  const handleExportarBase = async () => {
    setIsExporting(true);
    try {
      const response = await axios.get('/api/v1/admin/exportar-base', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `SOP_Nexus_Base_Granular_${cicloAtivo.replace('/','_')}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (e: any) {
      alert("Erro ao exportar a base de dados. Verifique se existem dados no ciclo atual.");
    } finally { setIsExporting(false); }
  };


  const handleExportarDatasetIA = async () => {
    setIsExportingIA(true);
    try {
      const response = await axios.get('/api/v1/admin/exportar-dataset-ia', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `Nexus_Dataset_IA_${new Date().toISOString().slice(0,10)}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (e: any) {
      // Mostra o erro real do backend — o blob precisa ser lido como texto
      let detalhe = e?.message || "erro desconhecido";
      try {
        if (e?.response?.data instanceof Blob) {
          const txt = await e.response.data.text();
          try { detalhe = JSON.parse(txt).detail || txt; } catch { detalhe = txt; }
        } else if (e?.response?.data?.detail) {
          detalhe = e.response.data.detail;
        }
      } catch {}
      console.error("Dataset IA:", detalhe);
      alert("Erro ao gerar o dataset de IA:\n\n" + detalhe);
    } finally { setIsExportingIA(false); }
  };

  const handleDescongelar = async () => {
    if (!origemDesbloqueio) return alert("Selecione a origem que deseja descongelar.");
    if (!window.confirm(`Deseja realmente forçar a reabertura da tela para: ${origemDesbloqueio} no ciclo ${cicloAtivo}?`)) return;

    setIsUnlocking(true);
    try {
      await axios.post('/api/v1/admin/reabrir-ciclo', null, { params: { origem: origemDesbloqueio } });
      alert(`✅ Tela destrancada com sucesso para: ${origemDesbloqueio}`);
      setOrigemDesbloqueio('');
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao destrancar a tela.");
    } finally { setIsUnlocking(false); }
  };

  const handleChangeCiclo = async () => {
    if (!novoCicloInput) return;
    if (!window.confirm(`MÁQUINA DO TEMPO: Deseja alterar o relógio global do sistema para o ciclo ${novoCicloInput}? \n\nIsto irá mudar a visão de todos os utilizadores na plataforma imediatamente.`)) return;
    
    setIsChangingCiclo(true);
    try {
      await axios.post('/api/v1/admin/ciclo-ativo', { novo_ciclo: novoCicloInput });
      alert(`✅ Relógio do sistema alterado para ${novoCicloInput} com sucesso! A página será recarregada.`);
      window.location.reload(); 
    } catch (e: any) {
      alert(e.response?.data?.detail || "Erro ao alterar o ciclo.");
      setIsChangingCiclo(false);
    }
  };

  const auditoriaFiltrada = useMemo(() => {
    if (!buscaAuditoria) return logsAuditoria;
    const t = buscaAuditoria.toLowerCase();
    return logsAuditoria.filter(log => 
      log.usuario.toLowerCase().includes(t) ||
      log.origem.toLowerCase().includes(t) ||
      log.cliente.toLowerCase().includes(t) ||
      log.sku.toLowerCase().includes(t)
    );
  }, [logsAuditoria, buscaAuditoria]);

  const sessoesFiltradas = useMemo(() => {
    if (!buscaAuditoria) return sessoesAuditoria;
    const termo = buscaAuditoria.toLowerCase();
    return sessoesAuditoria.filter(sessao =>
      [sessao.usuario, sessao.email, sessao.funcao, sessao.status]
        .filter(Boolean).some(valor => String(valor).toLowerCase().includes(termo))
    );
  }, [sessoesAuditoria, buscaAuditoria]);

  const formatarDuracao = (segundos: number) => {
    const total = Math.max(0, Number(segundos) || 0);
    const horas = Math.floor(total / 3600);
    const minutos = Math.floor((total % 3600) / 60);
    const segundosRestantes = total % 60;
    return horas
      ? `${horas}h ${String(minutos).padStart(2, '0')}min`
      : `${minutos}min ${String(segundosRestantes).padStart(2, '0')}s`;
  };

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
          
          <div className="flex flex-wrap items-center gap-3">
          <button 
            onClick={handleExportarDatasetIA} 
            disabled={isExportingIA}
            title="Histórico completo por SKU, gabarito de acurácia IA vs Humano e perfil de cada item — formato pronto para modelagem preditiva."
            className="flex items-center gap-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-sm disabled:opacity-50"
          >
            {isExportingIA ? <Loader2 className="w-5 h-5 animate-spin" /> : <BrainCircuit className="w-5 h-5" />} 
            {isExportingIA ? 'A Gerar...' : 'Dataset p/ IA'}
          </button>
          <button 
            onClick={handleExportarBase} 
            disabled={isExporting}
            className="flex items-center gap-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 px-6 py-3.5 rounded-2xl text-sm font-black tracking-widest uppercase transition-all shadow-sm disabled:opacity-50"
          >
            {isExporting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />} 
            {isExporting ? 'A Gerar...' : 'Exportar Base S&OP'}
          </button>
          </div>
        </div>

        {/* NAVEGAÇÃO DE ABAS */}
        <div className="flex gap-2 mb-6 bg-slate-200/50 p-1.5 rounded-[20px] w-fit">
           <button 
             onClick={() => setActiveTab('motor')} 
             className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-sm font-black uppercase tracking-widest transition-all ${activeTab === 'motor' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-200/50'}`}
           >
             <Database className="w-4 h-4" /> Motor S&OP
           </button>
           <button 
             onClick={() => setActiveTab('acessos')} 
             className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-sm font-black uppercase tracking-widest transition-all ${activeTab === 'acessos' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-200/50'}`}
           >
             <Shield className="w-4 h-4" /> Gestão de Acessos
           </button>
           <button 
             onClick={() => setActiveTab('auditoria')} 
             className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-sm font-black uppercase tracking-widest transition-all ${activeTab === 'auditoria' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-200/50'}`}
           >
             <History className="w-4 h-4" /> Trilha de Auditoria
           </button>
        </div>

        {/* CONTEÚDO DA ABA: MOTOR S&OP */}
        {activeTab === 'motor' && (
            <div className="flex flex-col lg:flex-row gap-6 items-stretch animate-in fade-in duration-300">
                <div className="w-full lg:w-1/3 flex flex-col gap-6">
                    <div className="bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col relative overflow-hidden">
                        <div className="absolute top-0 left-0 w-full h-1.5 bg-indigo-500"></div>
                        <div className="flex items-center gap-3 mb-4 pb-4 border-b border-slate-100 flex-shrink-0">
                            <Calendar className="w-5 h-5 text-indigo-500" />
                            <h2 className="text-sm font-black text-slate-700 uppercase tracking-widest">Controlo de Ciclo (Tempo)</h2>
                        </div>
                        <div className="flex flex-col gap-4">
                            <p className="text-xs font-medium text-slate-500 leading-relaxed">
                            O sistema inteiro (IA, Relatórios e Travas) está focado no ciclo abaixo. Altere o valor para viajar para um mês passado.
                            </p>
                            <div className="flex items-center gap-3 mt-2">
                            <input 
                                type="text" placeholder="MM/YYYY" value={novoCicloInput} onChange={e => setNovoCicloInput(e.target.value)}
                                className="flex-1 bg-slate-50 border-2 border-slate-200 text-slate-800 text-lg text-center rounded-xl p-3 font-black outline-none focus:border-indigo-500 transition-all"
                            />
                            <button 
                                onClick={handleChangeCiclo} disabled={isChangingCiclo || novoCicloInput === cicloAtivo}
                                className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-3.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all shadow-md shadow-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {isChangingCiclo ? <Loader2 className="w-5 h-5 animate-spin" /> : 'Ativar'}
                            </button>
                            </div>
                        </div>
                    </div>

                    <div className="bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col">
                        <div className="flex items-center gap-3 mb-6 pb-4 border-b border-slate-100 flex-shrink-0">
                            <Unlock className="w-5 h-5 text-amber-500" />
                            <h2 className="text-sm font-black text-slate-700 uppercase tracking-widest">Descongelar Telas</h2>
                        </div>
                        <div className="flex flex-col gap-4">
                            <p className="text-xs font-medium text-slate-500 leading-relaxed">
                            Force a reabertura de uma etapa do S&OP que já foi assinada e congelada <strong className="text-slate-800">no ciclo ativo ({cicloAtivo})</strong>.
                            </p>
                            <select 
                                value={origemDesbloqueio} 
                                onChange={(e) => setOrigemDesbloqueio(e.target.value)} 
                                className="bg-slate-50 border border-slate-200 text-slate-700 text-sm rounded-xl focus:ring-amber-500 focus:border-amber-500 block w-full p-3 font-bold outline-none cursor-pointer"
                            >
                                <option value="">Selecione uma etapa...</option>
                                <option value="TopDown">Top-Down (Marketing)</option>
                                <option value="BottomUP">Bottom-Up (Gerência Comercial)</option>
                                <option value="Metas">Metas / Consenso (Vendas)</option>
                                <option value="Supply">Supply (Fábrica)</option>
                                <option value="Final">S&OP Global (Final)</option>
                            </select>
                            <button onClick={handleDescongelar} disabled={isUnlocking || !origemDesbloqueio} className="mt-2 w-full flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-600 text-white py-3.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all shadow-md shadow-amber-500/20 disabled:opacity-50">
                                {isUnlocking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlock className="w-4 h-4" />} Forçar Reabertura
                            </button>
                        </div>
                    </div>
                </div>

                <div className="w-full lg:w-2/3 bg-slate-950 rounded-[32px] p-2 shadow-2xl border border-slate-800 flex flex-col relative min-h-[500px]">
                    <div className="bg-slate-900 rounded-[24px] p-6 border border-slate-800 m-2 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 flex-shrink-0">
                        <div>
                            <h2 className="text-sm font-black text-white uppercase tracking-widest flex items-center gap-2">
                                <RefreshCw className={`w-4 h-4 text-emerald-400 ${isPipelineRunning ? 'animate-spin' : ''}`} /> Motor de Engenharia S&OP
                            </h2>
                            <p className="text-xs font-medium text-slate-400 mt-1">Carga de Vendas Delta e Processamento de Redes Neurais S&OP.</p>
                        </div>
                        <div className="flex items-center gap-3 flex-wrap">
                            {/* RECARGA TOTAL — botão destrutivo */}
                            <button
                                onClick={handleRecargaTotal}
                                disabled={isPipelineRunning}
                                title="Apaga toda a fato_vendas e recarrega o histórico completo desde jan/2023 sem filtros de canal. Operação longa e irreversível."
                                className="flex items-center gap-2 bg-rose-600 hover:bg-rose-500 text-white px-5 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-[0_0_20px_rgba(225,29,72,0.25)] disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap border border-rose-500"
                            >
                                <ShieldAlert className="w-4 h-4" />
                                Recarga Total
                            </button>
                            {/* RUN PIPELINE — operação normal */}
                            <button onClick={handleRunPipeline} disabled={isPipelineRunning} className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-white px-6 py-3 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-[0_0_20px_rgba(16,185,129,0.3)] disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap">
                                {isPipelineRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                                {isPipelineRunning ? 'Processando...' : 'Run Pipeline'}
                            </button>
                        </div>
                    </div>

                    <div className="flex-1 bg-slate-950 p-6 flex flex-col overflow-hidden relative">
                        <div className="flex items-center gap-2 mb-4 pb-4 border-b border-slate-800 flex-shrink-0">
                            <Terminal className="w-5 h-5 text-slate-500" />
                            <span className="text-xs font-black text-slate-500 uppercase tracking-widest">NEXUS SERVER // CONSOLE OUTPUT</span>
                            {isPipelineRunning && !isRecargaTotal && (
                                <span className="ml-auto flex items-center gap-2 text-[10px] font-black text-emerald-400 uppercase tracking-widest animate-pulse">
                                    <div className="w-2 h-2 bg-emerald-400 rounded-full"></div> RUNNING
                                </span>
                            )}
                            {isPipelineRunning && isRecargaTotal && (
                                <span className="ml-auto flex items-center gap-2 text-[10px] font-black text-rose-400 uppercase tracking-widest animate-pulse">
                                    <div className="w-2 h-2 bg-rose-400 rounded-full animate-ping"></div> RECARGA TOTAL EM CURSO
                                </span>
                            )}
                        </div>
                        {/* Banner de aviso durante recarga total */}
                        {isRecargaTotal && (
                            <div className="mb-4 flex items-center gap-3 bg-rose-950/60 border border-rose-800 rounded-xl px-4 py-3 flex-shrink-0">
                                <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0" />
                                <p className="text-[11px] font-bold text-rose-300 leading-snug">
                                    Recarga total em andamento — toda a fato_vendas foi apagada e está sendo reinserida desde jan/2023. Não feche esta janela.
                                </p>
                            </div>
                        )}
                        <div ref={terminalScrollRef} className="flex-1 overflow-y-auto font-mono text-xs md:text-sm text-emerald-400/90 pr-2 space-y-1 custom-scrollbar">
                            {logs.length === 0 ? (
                                <div className="h-full flex items-center justify-center text-slate-800 font-bold uppercase tracking-widest">Aguardando Execução...</div>
                            ) : (
                                logs.map((log, i) => (
                                    <div key={i} className={`${log.includes('ERRO') || log.includes('❌') ? 'text-rose-400' : log.includes('✅') ? 'text-cyan-400' : log.includes('🔴') || log.includes('RECARGA TOTAL') || log.includes('TRUNCATE') ? 'text-rose-300' : 'text-emerald-400/80'} py-0.5 border-l-2 border-slate-800 pl-3 break-words`}>
                                    {log}
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>
            </div>
        )}

        {/* PIPELINE FINANCEIRO - mesmo padrao visual do Pipeline S&OP */}
        {activeTab === 'motor' && (
            <div className="bg-slate-950 rounded-[32px] border border-slate-800 flex flex-col overflow-hidden mt-6" style={{ minHeight: 260 }}>
                <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between flex-shrink-0 flex-wrap gap-3">
                    <div className="flex items-center gap-3">
                        <DollarSign className={`w-4 h-4 text-violet-400 ${isFinanceiroRunning ? 'animate-pulse' : ''}`} />
                        <span className="text-xs font-black text-slate-400 uppercase tracking-widest">Dados Financeiros - Gobi API</span>
                    </div>
                    <div className="flex gap-2">
                        {/* Recarga Total Financeira */}
                        <button
                            onClick={handleFinanceiroRecarga}
                            disabled={isFinanceiroRunning || isPipelineRunning}
                            title="Extrai historico completo desde jan/2023. Operacao longa."
                            className="flex items-center gap-2 bg-rose-600 hover:bg-rose-500 text-white px-4 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-[0_0_16px_rgba(225,29,72,0.2)] disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap border border-rose-500"
                        >
                            <ShieldAlert className="w-3.5 h-3.5" />
                            Recarga 2023+
                        </button>
                        {/* Atualizacao rolling 3 meses */}
                        <button
                            onClick={handleFinanceiroAtualizar}
                            disabled={isFinanceiroRunning || isPipelineRunning}
                            className="flex items-center gap-2 bg-violet-600 hover:bg-violet-500 text-white px-5 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all shadow-[0_0_16px_rgba(124,58,237,0.25)] disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                        >
                            {isFinanceiroRunning
                                ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Processando...</>
                                : <><RefreshCw className="w-3.5 h-3.5" /> Atualizar (3 meses)</>
                            }
                        </button>
                    </div>
                </div>

                {isFinanceiroRecarga && (
                    <div className="mx-6 mt-4 flex items-center gap-3 bg-rose-950/60 border border-rose-800 rounded-xl px-4 py-3 flex-shrink-0">
                        <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0" />
                        <p className="text-[11px] font-bold text-rose-300 leading-snug">
                            Recarga financeira em andamento — extraindo historico completo desde jan/2023. Nao feche esta janela.
                        </p>
                    </div>
                )}

                <div className="flex-1 overflow-y-auto font-mono text-xs text-violet-400/90 p-6 space-y-1 custom-scrollbar">
                    {logsFinanceiro.length === 0 ? (
                        <div className="h-full flex items-center justify-center text-slate-800 font-bold uppercase tracking-widest">Aguardando Execucao...</div>
                    ) : (
                        logsFinanceiro.map((log, i) => (
                            <div key={i} className={`${log.includes('ERRO') ? 'text-rose-400' : log.includes('OK') || log.includes('concluida') ? 'text-cyan-400' : log.includes('RECARGA') ? 'text-rose-300' : 'text-violet-400/80'} py-0.5 border-l-2 border-slate-800 pl-3 break-words`}>
                                {log}
                            </div>
                        ))
                    )}
                </div>
            </div>
        )}

        {/* CONTEÚDO DA ABA: GESTÃO DE ACESSOS */}
        {activeTab === 'acessos' && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 animate-in fade-in duration-300">
                <div className="bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col min-h-[500px]">
                    <div className="flex items-center gap-3 mb-6 pb-4 border-b border-slate-100 flex-shrink-0">
                        <UserPlus className="w-5 h-5 text-blue-500" />
                        <h2 className="text-sm font-black text-slate-700 uppercase tracking-widest">Aprovações Pendentes</h2>
                        <div className="ml-auto bg-blue-100 text-blue-700 font-black text-xs px-2 py-1 rounded-lg">{usuariosPendentes.length}</div>
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
                                        {user.supervisor_nome && <span className="text-[10px] font-bold text-slate-600 truncate">Equipa: {user.supervisor_nome}</span>}
                                        {user.gerente_nome && <span className="text-[10px] font-bold text-slate-600 truncate">Gerente: {user.gerente_nome}</span>}
                                    </div>
                                    <div className="flex items-center gap-2 mt-1">
                                        <button onClick={() => handleRejeitarUsuario(user.id)} className="flex-1 flex items-center justify-center gap-1 bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors"><X className="w-3 h-3" /> Rejeitar</button>
                                        <button onClick={() => handleAprovarUsuario(user.id)} className="flex-1 flex items-center justify-center gap-1 bg-emerald-500 hover:bg-emerald-600 text-white py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors shadow-md shadow-emerald-500/20"><Check className="w-3 h-3" /> Aprovar</button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>

                <div className="bg-white rounded-[32px] p-6 lg:p-8 shadow-sm border border-slate-100 flex flex-col min-h-[500px]">
                    <div className="flex items-center gap-3 mb-6 pb-4 border-b border-slate-100 flex-shrink-0">
                        <Users className="w-5 h-5 text-indigo-500" />
                        <h2 className="text-sm font-black text-slate-700 uppercase tracking-widest">Utilizadores Ativos</h2>
                        <div className="ml-auto bg-indigo-100 text-indigo-700 font-black text-xs px-2 py-1 rounded-lg">{usuariosAtivos.length}</div>
                    </div>
                    <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-4">
                        {usuariosAtivos.length === 0 ? (
                            <div className="h-full flex flex-col items-center justify-center text-slate-400 gap-3 opacity-60">
                                <ShieldAlert className="w-10 h-10" />
                                <span className="text-xs font-black uppercase tracking-widest text-center">Nenhum utilizador<br/>ativo no sistema</span>
                            </div>
                        ) : (
                            usuariosAtivos.map((user) => (
                                <div key={user.id} className="bg-slate-50 border border-slate-200 rounded-2xl p-4 flex flex-col gap-3">
                                    <div>
                                        <h3 className="font-black text-sm text-slate-800">{user.nome}</h3>
                                        <p className="text-xs font-bold text-slate-500">{user.email}</p>
                                    </div>
                                    <div className="bg-white p-2 rounded-xl border border-slate-100 flex flex-col gap-1">
                                        <span className="text-[10px] font-black text-indigo-500 uppercase tracking-widest">Perfil: {user.funcao}</span>
                                        {user.supervisor_nome && <span className="text-[10px] font-bold text-slate-600 truncate">Equipa: {user.supervisor_nome}</span>}
                                        {user.gerente_nome && <span className="text-[10px] font-bold text-slate-600 truncate">Gerente: {user.gerente_nome}</span>}
                                    </div>
                                    <div className="flex items-center gap-2 mt-1">
                                        <button onClick={() => handleResetSenha(user.id, user.nome)} className="flex-1 flex items-center justify-center gap-2 bg-white border border-slate-200 hover:border-amber-300 hover:bg-amber-50 text-slate-600 hover:text-amber-700 py-2.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all">
                                            <KeyRound className="w-4 h-4" /> Resetar Senha
                                        </button>
                                        <button onClick={() => handleDeleteUser(user.id, user.nome)} className="flex-1 flex items-center justify-center gap-2 bg-white border border-rose-200 hover:border-rose-300 hover:bg-rose-50 text-slate-600 hover:text-rose-700 py-2.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all">
                                            <Trash2 className="w-4 h-4" /> Excluir
                                        </button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>
        )}

        {/* CONTEÚDO DA ABA: TRILHA DE AUDITORIA */}
        {activeTab === 'auditoria' && (
            <div className="bg-white rounded-[32px] p-6 lg:p-10 shadow-sm border border-slate-100 animate-in fade-in duration-300 min-h-[600px] flex flex-col">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8 pb-6 border-b border-slate-100 flex-shrink-0">
                    <div className="flex items-center gap-4">
                        <div className="p-3 bg-slate-100 rounded-2xl">
                            <History className="w-6 h-6 text-slate-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-black text-slate-800 tracking-tighter uppercase">Trilha de Auditoria</h2>
                            <p className="text-xs font-bold text-slate-400">Histórico de alterações e movimentos manuais no sistema.</p>
                        </div>
                    </div>

                    <div className="relative w-full md:w-96">
                        <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                        <input 
                            type="text" 
                            placeholder="Buscar por usuário, cliente ou produto..." 
                            value={buscaAuditoria}
                            onChange={(e) => setBuscaAuditoria(e.target.value)}
                            className="w-full bg-slate-50 border-2 border-slate-100 rounded-2xl py-3 pl-12 pr-4 text-sm font-bold text-slate-700 outline-none focus:border-indigo-500 transition-all"
                        />
                    </div>
                </div>

                <div className="flex-1 overflow-x-auto custom-scrollbar">
                    <table className="w-full border-collapse">
                        <thead>
                            <tr className="text-left">
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Data / Hora</th>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Usuário</th>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Movimentação</th>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Detalhes do Item</th>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Ajuste (CX)</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-50">
                            {auditoriaFiltrada.length === 0 ? (
                                <tr>
                                    <td colSpan={5} className="py-20 text-center">
                                        <div className="flex flex-col items-center gap-3 text-slate-300">
                                            <Search className="w-12 h-12" />
                                            <span className="text-xs font-black uppercase tracking-widest">Nenhum registo encontrado</span>
                                        </div>
                                    </td>
                                </tr>
                            ) : (
                                auditoriaFiltrada.map((log) => {
                                    const aumentou = log.para > log.de;
                                    const diminuiu = log.para < log.de;
                                    return (
                                        <tr key={log.id} className="hover:bg-slate-50/50 transition-colors">
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <div className="flex flex-col">
                                                    <span className="text-sm font-black text-slate-700">{log.data.split(' ')[0]}</span>
                                                    <span className="text-[10px] font-bold text-slate-400">{log.data.split(' ')[1]}</span>
                                                </div>
                                            </td>
                                            <td className="px-6 py-4">
                                                <span className="text-sm font-black text-indigo-600">{log.usuario}</span>
                                            </td>
                                            <td className="px-6 py-4">
                                                <div className="flex items-center gap-2">
                                                    <span className="px-2 py-1 bg-slate-100 rounded-lg text-[10px] font-black text-slate-600 uppercase tracking-tight">{log.origem}</span>
                                                </div>
                                            </td>
                                            <td className="px-6 py-4 max-w-xs">
                                                <div className="flex flex-col">
                                                    <span className="text-sm font-black text-slate-800 truncate">{log.cliente}</span>
                                                    <span className="text-[10px] font-bold text-slate-400 truncate">{log.sku}</span>
                                                    <span className="w-fit mt-1 px-2 py-0.5 bg-slate-100 rounded-md text-[9px] font-black text-slate-500 uppercase">{log.mes_ref}</span>
                                                </div>
                                            </td>
                                            <td className="px-6 py-4">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-sm font-bold text-slate-400">{log.de.toLocaleString('pt-BR')}</span>
                                                    <ArrowRight className="w-4 h-4 text-slate-300" />
                                                    <span className={`text-sm font-black flex items-center gap-1 ${aumentou ? 'text-emerald-600' : diminuiu ? 'text-rose-600' : 'text-slate-600'}`}>
                                                        {log.para.toLocaleString('pt-BR')}
                                                        {aumentou && <TrendingUp className="w-4 h-4" />}
                                                        {diminuiu && <TrendingDown className="w-4 h-4" />}
                                                    </span>
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                </div>

                <div className="mt-10 pt-8 border-t border-slate-100">
                    <div className="flex items-center justify-between mb-4">
                        <div>
                            <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest">Uso e sessões</h3>
                            <p className="text-xs font-bold text-slate-400 mt-1">Tempo conectado e atividade medida pelos pulsos de sessão.</p>
                        </div>
                        <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                            {sessoesFiltradas.length} sessões
                        </span>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full border-collapse">
                            <thead>
                                <tr className="text-left">
                                    <th className="px-4 py-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">Usuário</th>
                                    <th className="px-4 py-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">Início</th>
                                    <th className="px-4 py-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">Último pulso</th>
                                    <th className="px-4 py-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">Duração</th>
                                    <th className="px-4 py-3 text-[10px] font-black text-slate-400 uppercase tracking-widest">Uso</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-50">
                                {sessoesFiltradas.length === 0 ? (
                                    <tr><td colSpan={5} className="py-8 text-center text-xs font-bold text-slate-300">Nenhuma sessão registrada.</td></tr>
                                ) : sessoesFiltradas.map(sessao => (
                                    <tr key={sessao.id} className="hover:bg-slate-50/50">
                                        <td className="px-4 py-3">
                                            <div className="text-sm font-black text-slate-700">{sessao.usuario}</div>
                                            <div className="text-[10px] font-bold text-slate-400">{sessao.email} · {sessao.funcao}</div>
                                        </td>
                                        <td className="px-4 py-3 text-xs font-bold text-slate-500 whitespace-nowrap">{sessao.inicio}</td>
                                        <td className="px-4 py-3 text-xs font-bold text-slate-500 whitespace-nowrap">{sessao.ultimo_sinal}</td>
                                        <td className="px-4 py-3 text-xs font-black text-slate-700 whitespace-nowrap">{formatarDuracao(sessao.duracao_segundos)}</td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-2">
                                                <span className={`w-2 h-2 rounded-full ${sessao.status === 'online' ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                                                <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">{sessao.status}</span>
                                                <span className="text-[10px] font-bold text-slate-400">({sessao.heartbeats} pulsos)</span>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>
        )}

      </div>
    </div>
  );
}