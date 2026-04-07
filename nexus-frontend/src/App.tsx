import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Cpu, Lock, Loader2 } from 'lucide-react';

import Sidebar from './pages/Sidebar';
import AdminPanel from './pages/AdminPanel';
import LoginArena from './pages/LoginArena'; 
import TopDownArena from './pages/TopDownArena';
import ConsensoArena from './pages/ConsensoArena';
import GlobalDashboard from './pages/GlobalDashboard';
import NPDArena from './pages/NPDArena';
import GerenciamentoArena from './pages/GerenciamentoArena';

export default function App() {
  const [user, setUser] = useState<any>(null);
  const [currentRoute, setCurrentRoute] = useState('admin');
  
  // ==========================================
  // ESTADOS DO ESCUDO GLOBAL (LOCK SCREEN)
  // ==========================================
  const [isSystemLocked, setIsSystemLocked] = useState(false);
  const [latestLog, setLatestLog] = useState('');

  // ==========================================
  // RADAR DO SISTEMA (POLLING GLOBAL)
  // ==========================================
  useEffect(() => {
    if (!user) return; // Só ativa o radar se alguém estiver logado

    const checkSystemStatus = async () => {
      try {
        const res = await axios.get('http://localhost:8000/api/v1/admin/pipeline/status');
        setIsSystemLocked(res.data.is_running);
        
        // Pega a última linha do log para mostrar no escudo de bloqueio
        if (res.data.is_running && res.data.logs && res.data.logs.length > 0) {
          setLatestLog(res.data.logs[res.data.logs.length - 1]);
        }
      } catch (e) {
        console.error("Erro ao checar status global do Nexus.");
      }
    };

    checkSystemStatus();
    // Verifica o status do servidor a cada 3 segundos
    const interval = setInterval(checkSystemStatus, 3000);
    return () => clearInterval(interval);
  }, [user]);

  const handleLoginSuccess = (userData: any) => {
    setUser(userData);
    // Roteamento inteligente baseado na função
    if (userData.funcao === 'Executivo') {
      setCurrentRoute('consenso');
    } else if (userData.funcao === 'Gerente') {
      setCurrentRoute('gerenciamento'); // Gerente vai direto para a sua central
    } else {
      setCurrentRoute('admin'); // Admin vai para a Control Tower
    }
  };

  const handleLogout = () => {
    setUser(null);
    setCurrentRoute('admin');
  };

  if (!user) {
    return <LoginArena onLoginSuccess={handleLoginSuccess} />;
  }

  const renderRoute = () => {
    if (user.funcao !== 'Administrador' && currentRoute === 'admin') {
       return (
        <div className="h-full flex items-center justify-center bg-slate-50 text-rose-400 font-bold tracking-widest uppercase">
          Acesso Negado à Control Tower.
        </div>
      );
    }

    switch (currentRoute) {
      case 'admin': return <AdminPanel />;
      case 'topdown': return <TopDownArena />;
      case 'consenso': return <ConsensoArena usuarioSessao={user} />; 
      case 'dashboard': return <GlobalDashboard />;
      case 'npd': return <NPDArena />;
      case 'gerenciamento': return <GerenciamentoArena usuarioSessao={user} />; // <--- SESSÃO PASSADA PARA A TELA DE GERENCIAMENTO!
      default: return (
        <div className="h-full flex flex-col items-center justify-center bg-slate-50 text-slate-400 font-bold tracking-widest uppercase gap-4">
          <h2>Módulo em Construção</h2>
        </div>
      );
    }
  };

  // Regra de Ouro: O bloqueio engole a tela de todos, EXCETO se for o Admin olhando a Control Tower
  const isControlTower = currentRoute === 'admin';
  const showGlobalLock = isSystemLocked && !isControlTower;

  return (
    <div className="flex h-screen bg-[#f8fafc] overflow-hidden relative">
      
      {/* ========================================== */}
      {/* TELA DE BLOQUEIO FUTURISTA (OVERLAY) */}
      {/* ========================================== */}
      {showGlobalLock && (
        <div className="absolute inset-0 z-[9999] bg-slate-950/95 backdrop-blur-2xl flex flex-col items-center justify-center text-white overflow-hidden animate-in fade-in duration-500">
            
            {/* Brilho de Fundo (Reactor) */}
            <div className="absolute w-[600px] h-[600px] bg-indigo-600/20 rounded-full blur-[120px] animate-pulse"></div>
            
            <div className="relative z-10 flex flex-col items-center max-w-2xl text-center px-6">
                
                {/* Ícone Rotativo Cibernético */}
                <div className="relative flex items-center justify-center w-40 h-40 mb-8">
                    {/* Anel Externo Lento */}
                    <div className="absolute inset-0 border-[3px] border-indigo-500/20 rounded-full animate-[spin_6s_linear_infinite]"></div>
                    {/* Anel Interno Rápido com falha (tracejado) */}
                    <div className="absolute inset-3 border-[3px] border-t-indigo-400 border-r-transparent border-b-indigo-400 border-l-transparent rounded-full animate-[spin_2s_linear_infinite]"></div>
                    {/* Pulso Central */}
                    <div className="absolute inset-8 bg-indigo-500/20 rounded-full animate-ping opacity-50"></div>
                    <Cpu className="w-14 h-14 text-indigo-400" />
                </div>

                <h1 className="text-3xl md:text-5xl font-black tracking-tighter mb-4 flex items-center justify-center gap-4 uppercase text-transparent bg-clip-text bg-gradient-to-r from-white to-slate-400">
                    <Lock className="w-8 h-8 md:w-10 md:h-10 text-rose-500 flex-shrink-0" />
                    NEXUS EM MANUTENÇÃO
                </h1>
                
                <p className="text-slate-400 text-base md:text-lg mb-10 font-medium max-w-xl">
                    O sistema encontra-se bloqueado. O Motor de Engenharia de Dados e o Treinamento de Redes Neurais (IA) estão em execução neste exato momento.
                </p>

                {/* Console Minimalista do Escudo */}
                <div className="bg-slate-900 border border-slate-800 p-6 rounded-[24px] w-full flex flex-col items-start text-left shadow-[0_0_50px_rgba(0,0,0,0.5)] relative overflow-hidden">
                    <div className="absolute top-0 left-0 w-1.5 h-full bg-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.8)]"></div>
                    <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-3 flex items-center gap-2">
                       <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500" />
                       Interceção de Logs / Tempo Real
                    </span>
                    <code className="text-emerald-400 font-mono text-xs md:text-sm w-full truncate border-l border-slate-800 pl-3 py-1">
                        {latestLog || "Sincronizando com o núcleo do servidor..."}
                    </code>
                </div>
            </div>
        </div>
      )}

      {/* RENDERIZAÇÃO NORMAL DO SISTEMA */}
      <Sidebar
        currentRoute={currentRoute}
        setCurrentRoute={setCurrentRoute}
        user={user}
        onLogout={handleLogout}
      />
      <main className="flex-1 overflow-auto bg-[#f8fafc]">
        {renderRoute()}
      </main>
    </div>
  );
}