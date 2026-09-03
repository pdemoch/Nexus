import { useState, useEffect } from 'react';
import axios from 'axios';
import { Cpu, Lock, Loader2 } from 'lucide-react';

import Sidebar from './pages/Sidebar';
import AdminPanel from './pages/AdminPanel';
import LoginArena from './pages/LoginArena';
// Telas de consenso (mantidos os NOMES de arquivo antigos; conteúdo reconstruído).
import TopDownArena from './pages/TopDownArena';            // -> Demanda Marketing
import GerenciamentoArena from './pages/GerenciamentoArena'; // -> Demanda Comercial
import IrrestritaArena from './pages/Irrestritaarena';       // -> Demanda Irrestrita
import ConsensoArena from './pages/ConsensoArena';           // -> Metas Comercial
import SupplyReviewArena from './pages/SupplyReviewArena';   // -> Supply Review
import GlobalDashboard from './pages/GlobalDashboard';       // -> Demanda Final
import NPDArena from './pages/NPDArena';
import AuditoriaArena from './pages/AuditoriaArena';         // KPIs
import FinanceiroArena from './pages/FinanceiroArena'; 

export default function App() {
  const [user, setUser] = useState<any>(null);
  const [currentRoute, setCurrentRoute] = useState('admin');

  const [isSystemLocked, setIsSystemLocked] = useState(false);
  const [latestLog, setLatestLog] = useState('');

  // MOTOR DE HEARTBEAT: mantém a sessão e mede o tempo de uso no servidor.
  useEffect(() => {
    if (!user) return;
    const enviarPulso = async () => {
      try { await axios.post('/api/v1/auth/heartbeat'); } catch (error) {}
    };
    enviarPulso();
    const intervalId = setInterval(enviarPulso, 60000);
    return () => clearInterval(intervalId);
  }, [user]);

  // RADAR DO SISTEMA — bloqueia a UI enquanto o pipeline/IA roda
  useEffect(() => {
    if (!user || user.funcao !== 'Administrador') return;
    const checkSystemStatus = async () => {
      try {
        const res = await axios.get('/api/v1/admin/pipeline/status');
        setIsSystemLocked(res.data.is_running);
        if (res.data.is_running && res.data.logs && res.data.logs.length > 0) {
          setLatestLog(res.data.logs[res.data.logs.length - 1]);
        }
      } catch (error) { console.error("Falha ao verificar status do sistema."); }
    };
    checkSystemStatus();
    const intervalId = setInterval(checkSystemStatus, 3000);
    return () => clearInterval(intervalId);
  }, [user]);

  // GESTÃO DE SESSÃO
  useEffect(() => {
    const token = localStorage.getItem('nexus_token');
    const savedUser = localStorage.getItem('nexus_user');
    if (token && savedUser) {
      const parsedUser = JSON.parse(savedUser);
      setUser(parsedUser);
      // Rota inicial por cargo
      if (parsedUser.funcao === 'Coordenador') setCurrentRoute('consenso');
      else setCurrentRoute('dashboard');
    }
  }, []);

  useEffect(() => {
    const handleSessionExpired = () => {
      setUser(null);
      setCurrentRoute('login');
    };
    window.addEventListener('nexus:session-expired', handleSessionExpired);
    return () => window.removeEventListener('nexus:session-expired', handleSessionExpired);
  }, []);

  const handleLoginSuccess = (userData: any) => {
    setUser(userData);
    localStorage.setItem('nexus_user', JSON.stringify(userData));
    if (userData.funcao === 'Coordenador') setCurrentRoute('consenso');
    else setCurrentRoute('dashboard');
  };

  const handleLogout = () => {
    axios.post('/api/v1/auth/logout').catch(() => undefined);
    localStorage.removeItem('nexus_token');
    localStorage.removeItem('nexus_user');
    setUser(null);
  };

  if (!user) return <LoginArena onLoginSuccess={handleLoginSuccess} />;

  const renderContent = () => {
    switch (currentRoute) {
      case 'dashboard': return <GlobalDashboard />;
      case 'topdown': return <TopDownArena />;
      case 'gerenciamento': return <GerenciamentoArena />;
      case 'irrestrita': return <IrrestritaArena />;
      case 'consenso': return <ConsensoArena />;
      case 'supply': return <SupplyReviewArena />;
      case 'npd': return <NPDArena />;
      case 'auditoria': return <AuditoriaArena />;
      case 'financeiro': return <FinanceiroArena />;
      case 'admin': return <AdminPanel />;
      default: return <AdminPanel />;
    }
  };

  return (
    <div className="flex h-screen w-full bg-[#f8fafc] font-sans overflow-hidden">

      {/* TELA DE BLOQUEIO GLOBAL — enquanto o pipeline/IA está em execução */}
      {isSystemLocked && user?.funcao !== 'Administrador' && (
        <div className="fixed inset-0 z-[9999] bg-slate-950/90 backdrop-blur-xl flex flex-col items-center justify-center p-6 animate-in fade-in duration-500">
            <div className="max-w-2xl w-full flex flex-col items-center text-center">
                <div className="relative mb-8">
                    <div className="absolute inset-0 bg-emerald-500 blur-[100px] opacity-20 rounded-full animate-pulse"></div>
                    <div className="bg-slate-900 p-6 rounded-full border border-slate-800 relative z-10 shadow-[0_0_50px_rgba(16,185,129,0.1)]">
                       <Cpu className="w-16 h-16 text-emerald-400" />
                    </div>
                    <div className="absolute -bottom-2 -right-2 bg-rose-500 p-2 rounded-full border-4 border-slate-950 z-20">
                       <Lock className="w-5 h-5 text-white" />
                    </div>
                </div>
                <h1 className="text-3xl md:text-5xl font-black text-white tracking-tighter mb-4">
                    PROTOCOLO DE <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-cyan-400">ENGENHARIA ATIVO</span>
                </h1>
                <p className="text-slate-400 text-base md:text-lg mb-10 font-medium max-w-xl">
                    O sistema encontra-se bloqueado. O Motor de Engenharia de Dados e o Treinamento de Redes Neurais (IA) estão em execução neste exato momento.
                </p>
                <div className="bg-slate-900 border border-slate-800 p-6 rounded-[24px] w-full flex flex-col items-start text-left shadow-[0_0_50px_rgba(0,0,0,0.5)] relative overflow-hidden">
                    <div className="absolute top-0 left-0 w-1.5 h-full bg-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.8)]"></div>
                    <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-3 flex items-center gap-2">
                       <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500" />
                       Executando Pipeline
                    </span>
                    <code className="text-emerald-400 font-mono text-xs md:text-sm w-full truncate border-l border-slate-800 pl-3 py-1">
                        {latestLog || "Sincronizando com o núcleo do servidor..."}
                    </code>
                </div>
            </div>
        </div>
      )}

      <Sidebar
        currentRoute={currentRoute}
        setCurrentRoute={setCurrentRoute}
        user={user}
        onLogout={handleLogout}
      />

      <main className="flex-1 min-w-0 h-full overflow-y-auto relative z-10 bg-[#f8fafc]">
        {renderContent()}
      </main>

    </div>
  );
}