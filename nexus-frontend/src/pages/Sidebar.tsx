import React from 'react';
import { BarChart3, Users, LayoutDashboard, Settings, Rocket, LogOut, UserCircle, ShieldAlert } from 'lucide-react';

export default function Sidebar({ currentRoute, setCurrentRoute, user, onLogout }: any) {
  
  // Definição das permissões de cada tela
  const allNavItems = [
    { id: 'dashboard', label: 'S&OP Global', icon: LayoutDashboard, roles: ['Administrador', 'Gerente'] },
    { id: 'topdown', label: 'Top-Down (Diretoria)', icon: BarChart3, roles: ['Administrador', 'Gerente'] },
    { id: 'npd', label: 'Inovações (NPD)', icon: Rocket, roles: ['Administrador', 'Gerente'] },
    { id: 'consenso', label: 'Bottom-Up (Vendas)', icon: Users, roles: ['Administrador', 'Gerente', 'Executivo'] },
    { id: 'gerenciamento', label: 'Gerenciamento', icon: ShieldAlert, roles: ['Administrador', 'Gerente'] },
    { id: 'admin', label: 'Control Tower', icon: Settings, roles: ['Administrador'] },
  ];

  // Filtra o menu com base no cargo do utilizador logado
  const allowedNavItems = allNavItems.filter(item => item.roles.includes(user?.funcao));

  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col h-full shadow-2xl relative z-50">
      
      {/* PERFIL DO USUÁRIO LOGADO */}
      <div className="px-6 pb-6 mb-4 border-b border-slate-800">
        <div className="flex items-center gap-3 bg-slate-800 p-3 rounded-2xl border border-slate-700">
          <UserCircle className="w-8 h-8 text-indigo-400 flex-shrink-0" />
          <div className="flex flex-col overflow-hidden">
            <span className="text-sm font-bold text-white truncate">{user?.nome}</span>
            <span className="text-[10px] font-black uppercase tracking-widest text-emerald-400 truncate">{user?.funcao}</span>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-4 space-y-2 overflow-y-auto custom-scrollbar">
        {allowedNavItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => setCurrentRoute(item.id)}
              className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-bold transition-all
                ${currentRoute === item.id
                  ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/30'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                }`}
            >
              <Icon className="w-5 h-5" />
              {item.label}
            </button>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-800">
        <button
          onClick={onLogout}
          className="w-full flex items-center gap-3 px-4 py-3 rounded-2xl text-sm font-bold text-rose-400 hover:bg-rose-500/10 transition-all"
        >
          <LogOut className="w-5 h-5" />
          Sair do Sistema
        </button>
      </div>
    </aside>
  );
}