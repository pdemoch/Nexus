import React, { useState } from 'react';
import { 
  BarChart3, Users, LayoutDashboard, Settings, Rocket, LogOut, 
  UserCircle, ShieldAlert, Factory, Activity, Crosshair, Database, 
  ChevronLeft, ChevronRight, Landmark, Zap,
  Coins
} from 'lucide-react';
import AssistenteChat from './AssistenteChat';

export default function Sidebar({ currentRoute, setCurrentRoute, user, onLogout }: any) {
  // Estado para controlar se a sidebar está recolhida (w-20) ou expandida (w-64)
  const [isCollapsed, setIsCollapsed] = useState(false);

  const allNavItems = [
    { id: 'dashboard', label: 'Demanda Final', icon: LayoutDashboard, roles: ['Administrador', 'Gerente', 'Supply Chain', 'Marketing', 'C-Level'] },
    { id: 'auditoria', label: 'KPIs', icon: Crosshair, roles: ['Administrador', 'C-Level', 'Gerente', 'Coordenador', 'Marketing'] },
    { id: 'topdown', label: 'Demanda Marketing', icon: BarChart3, roles: ['Administrador', 'Marketing'] },
    { id: 'gerenciamento', label: 'Demanda Comercial', icon: ShieldAlert, roles: ['Administrador', 'Gerente'] },
    { id: 'irrestrita', label: 'Demanda Irrestrita', icon: Zap, roles: ['Administrador'] },
    { id: 'consenso', label: 'Metas Comercial', icon: Users, roles: ['Administrador', 'Gerente', 'Coordenador'] },
    { id: 'npd', label: 'Inovações', icon: Rocket, roles: ['Administrador', 'Marketing'] },
    { id: 'supply', label: 'Supply Review', icon: Factory, roles: ['Administrador', 'Supply Chain'] },
    { id: 'admin', label: 'Painel Admin', icon: Settings, roles: ['Administrador'] },
    { id: 'financeiro', label: 'Painel Financeiro', icon: Coins, roles: ['Administrador', 'C-Level'] },
  ];

  // Filtra os itens baseado no papel do usuário
  const allowedNavItems = allNavItems.filter(item => item.roles.includes(user?.funcao || ''));

  return (
    <div className={`relative flex flex-col bg-slate-900 border-r border-slate-800 transition-all duration-300 ease-in-out z-40 h-screen ${isCollapsed ? 'w-20' : 'w-64'}`}>
      
      {/* BOTÃO DE RECOLHER/EXPANDIR FLUTUANTE NA BORDA */}
      <button 
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="absolute -right-3 top-10 bg-indigo-600 hover:bg-indigo-500 text-white rounded-full p-1 shadow-lg z-50 border-2 border-slate-900 transition-transform"
        title={isCollapsed ? "Expandir Menu" : "Recolher Menu"}
      >
        {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
      </button>

      {/* CABEÇALHO / PERFIL */}
      <div className={`p-6 shrink-0 mt-4 transition-all duration-300 ${isCollapsed ? 'px-4' : 'px-6'}`}>
        <div className={`flex items-center ${isCollapsed ? 'justify-center' : 'gap-3'}`}>
          <div className="w-10 h-10 rounded-full bg-indigo-500/20 border border-indigo-500/50 flex items-center justify-center shrink-0">
            <UserCircle className="w-6 h-6 text-indigo-400" />
          </div>
          {!isCollapsed && (
            <div className="flex flex-col overflow-hidden whitespace-nowrap opacity-100 transition-opacity duration-300">
              <span className="text-sm font-bold text-white truncate">{user?.nome}</span>
              <span className="text-[10px] font-black uppercase tracking-widest text-emerald-400 truncate">{user?.funcao}</span>
            </div>
          )}
        </div>
      </div>

      {/* NAVEGAÇÃO */}
      <nav className="flex-1 px-3 space-y-2 overflow-y-auto custom-scrollbar overflow-x-hidden">
        {allowedNavItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => setCurrentRoute(item.id)}
              title={isCollapsed ? item.label : ""} // Mostra o nome ao passar o mouse se estiver recolhido
              className={`w-full flex items-center ${isCollapsed ? 'justify-center px-0' : 'justify-start px-4'} gap-3 py-3.5 rounded-2xl text-sm font-bold transition-all
                ${currentRoute === item.id
                  ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/30'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                }`}
            >
              <Icon className="w-5 h-5 shrink-0" />
              {!isCollapsed && <span className="whitespace-nowrap truncate">{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {/* ASSISTENTE — chat único (Indicadores + Demanda), ícone diferenciado */}
      {['Administrador', 'C-Level', 'Gerente'].includes(user?.funcao || '') && (
        <div className="px-3 pb-2 shrink-0">
          <AssistenteChat isCollapsed={isCollapsed} />
        </div>
      )}

      {/* LOGOUT. */}
      <div className="p-4 border-t border-slate-800 shrink-0">
        <button
          onClick={onLogout}
          title={isCollapsed ? "Sair do Sistema" : ""}
          className={`w-full flex items-center ${isCollapsed ? 'justify-center px-0' : 'justify-start px-4'} gap-3 py-3.5 rounded-2xl text-sm font-bold text-rose-400 hover:bg-rose-500/10 hover:text-rose-300 transition-all`}
        >
          <LogOut className="w-5 h-5 shrink-0" />
          {!isCollapsed && <span className="whitespace-nowrap">Sair do Sistema</span>}
        </button>
      </div>
    </div>
  );
}