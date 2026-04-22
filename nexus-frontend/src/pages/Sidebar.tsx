import { BarChart3, Users, LayoutDashboard, Settings, Rocket, LogOut, UserCircle, ShieldAlert, Factory, Activity } from 'lucide-react';

export default function Sidebar({ currentRoute, setCurrentRoute, user, onLogout }: any) {
  
  // Definição das permissões de cada ecrã (Ordem Lógica do S&OP)
  const allNavItems = [
    // ATUALIZADO: Supply Chain adicionado ao Dashboard Global
    { id: 'dashboard', label: 'S&OP Global', icon: LayoutDashboard, roles: ['Administrador', 'Gerente', 'Supply Chain'] },
    
    // NOVAS ROTAS DO MÓDULO S&OE
    { id: 'soe-radar', label: 'Radar S&OE', icon: Activity, roles: ['Administrador', 'Gerente', 'Supply Chain', 'Executivo'] },
    { id: 'inbound', label: 'Inbound Arena', icon: Factory, roles: ['Administrador', 'Supply Chain'] },

    { id: 'topdown', label: 'Top-Down (Diretoria)', icon: BarChart3, roles: ['Administrador', 'Gerente'] },
    { id: 'npd', label: 'Inovações (MKT)', icon: Rocket, roles: ['Administrador', 'Gerente'] },
    { id: 'supply', label: 'Supply Review', icon: Factory, roles: ['Administrador', 'Supply Chain'] }, // Mantido
    { id: 'consenso', label: 'Bottom-Up (Comercial)', icon: Users, roles: ['Administrador', 'Gerente', 'Executivo'] },
    { id: 'gerenciamento', label: 'Gerenciamento', icon: ShieldAlert, roles: ['Administrador', 'Gerente'] },
    { id: 'admin', label: 'Control Tower', icon: Settings, roles: ['Administrador'] },
  ];

  // Filtra o menu com base no cargo do utilizador logado
  const allowedNavItems = allNavItems.filter(item => item.roles.includes(user?.funcao));

  return (
    <aside className="w-64 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col h-screen relative z-50">
      
      {/* CABEÇALHO DA SIDEBAR */}
      <div className="h-20 flex items-center justify-center border-b border-slate-800 px-6 shrink-0">
        <div className="flex items-center gap-3">
          <div className="bg-indigo-500 p-2 rounded-xl shadow-lg shadow-indigo-500/30">
            <Rocket className="w-6 h-6 text-white" />
          </div>
          <span className="text-2xl font-black text-white tracking-tighter">NEXUS<span className="text-indigo-400">.</span></span>
        </div>
      </div>

      {/* PERFIL DO UTILIZADOR */}
      <div className="p-6 border-b border-slate-800 mb-4 shrink-0">
        <div className="flex items-center gap-3 bg-slate-800/50 p-3 rounded-2xl border border-slate-700/50">
          <UserCircle className="w-10 h-10 text-indigo-400 flex-shrink-0" />
          <div className="flex flex-col overflow-hidden">
            <span className="text-sm font-bold text-white truncate">{user?.nome}</span>
            <span className="text-[10px] font-black uppercase tracking-widest text-emerald-400 truncate">{user?.funcao}</span>
          </div>
        </div>
      </div>

      {/* NAVEGAÇÃO */}
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

      {/* LOGOUT */}
      <div className="p-4 border-t border-slate-800 shrink-0">
        <button
          onClick={onLogout}
          className="w-full flex items-center gap-3 px-4 py-3 text-sm font-bold text-rose-400 hover:bg-rose-500/10 hover:text-rose-300 rounded-xl transition-colors"
        >
          <LogOut className="w-5 h-5" />
          Sair do Sistema
        </button>
      </div>
    </aside>
  );
}