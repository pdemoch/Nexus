import { BarChart3, Users, LayoutDashboard, Settings, Rocket, LogOut, UserCircle, ShieldAlert, Factory, Activity, Crosshair } from 'lucide-react';

export default function Sidebar({ currentRoute, setCurrentRoute, user, onLogout }: any) {
  
  // Definição das permissões de cada ecrã 
  const allNavItems = [
    { id: 'dashboard', label: 'Plano de Demanda', icon: LayoutDashboard, roles: ['Administrador', 'Gerente', 'Supply', 'Marketing', 'C-Level', 'Diretoria'] },
    { id: 'soe-radar', label: 'S&OE', icon: Activity, roles: ['Administrador', 'Gerente', 'C-Level', 'Diretoria'] },
    { id: 'inbound', label: 'Mapa Produtivo', icon: Factory, roles: ['Administrador', 'Supply'] },
    { id: 'auditoria', label: 'KPIs', icon: Crosshair, roles: ['Administrador', 'Gerente', 'C-Level', 'Diretoria'] },
    { id: 'topdown', label: 'Plano Irrestrito', icon: BarChart3, roles: ['Administrador', 'Marketing', 'Gerente', 'Diretoria'] },
    { id: 'npd', label: 'Inovações', icon: Rocket, roles: ['Administrador', 'Marketing'] },
    { id: 'supply', label: 'Supply Review', icon: Factory, roles: ['Administrador', 'Supply'] },
    { id: 'consenso', label: 'Metas da Equipe', icon: Users, roles: ['Administrador', 'Gerente', 'Diretoria'] },
    // AQUI ESTÁ A CHAVE DE ACESSO DA EQUIPE COMERCIAL:
    { id: 'gerenciamento', label: 'Gestão de Carteiras', icon: ShieldAlert, roles: ['Administrador', 'Diretoria', 'Gerente', 'Coordenador', 'Vendedor'] },
  ];

  // Filtro Inteligente: Verifica se a função do banco de dados (ex: 'Gerente Comercial') CONTÉM a palavra chave ('Gerente')
  const userRole = user?.funcao || '';
  const allowedNavItems = allNavItems.filter(item => 
    item.roles.some(role => userRole.toLowerCase().includes(role.toLowerCase()))
  );

  return (
    <div className="w-72 bg-slate-900 text-white flex flex-col h-full border-r border-slate-800 shadow-2xl relative z-20">
      
      {/* LOGO */}
      <div className="p-8 flex items-center gap-4">
        <div className="w-12 h-12 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-2xl flex items-center justify-center shadow-lg shadow-indigo-500/30">
          <Activity className="w-6 h-6 text-white" />
        </div>
        <div className="flex flex-col">
          <span className="text-2xl font-black tracking-tighter text-white">NEXUS</span>
          <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-widest">IBP Platform</span>
        </div>
      </div>

      {/* USER PROFILE */}
      <div className="px-6 mb-8">
        <div className="bg-slate-800/50 p-4 rounded-2xl border border-slate-700/50 flex items-center gap-3 backdrop-blur-sm">
          <div className="w-10 h-10 bg-slate-700 rounded-full flex items-center justify-center shrink-0">
            <UserCircle className="w-6 h-6 text-slate-300" />
          </div>
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
          className="w-full flex items-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-bold text-rose-400 hover:bg-rose-500/10 hover:text-rose-300 transition-colors"
        >
          <LogOut className="w-5 h-5" />
          Terminar Sessão
        </button>
      </div>
    </div>
  );
}