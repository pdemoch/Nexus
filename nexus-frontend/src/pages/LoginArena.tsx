import React, { useState, useEffect } from 'react';
import { Lock, Mail, User, ShieldCheck, ChevronRight, Loader2, KeyRound, Briefcase, Factory } from 'lucide-react';
import axios from 'axios';

export default function LoginArena({ onLoginSuccess }: { onLoginSuccess: (userData: any) => void }) {
  const [modo, setModo] = useState<'login' | 'cadastro' | 'primeiro_acesso'>('login');
  const [isLoading, setIsLoading] = useState(false);

  // Form states
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [nome, setNome] = useState('');
  const [funcao, setFuncao] = useState('Gerente'); 
  const [nomeVendedor, setNomeVendedor] = useState('');
  const [nomeGerente, setNomeGerente] = useState(''); 
  const [novaSenha, setNovaSenha] = useState('');
  
  const [listaVendedores, setListaVendedores] = useState<string[]>([]);
  const [listaGerentes, setListaGerentes] = useState<string[]>([]);

  // Busca lista de vendedores e gerentes se for para o modo cadastro
  useEffect(() => {
    if (modo === 'cadastro') {
      axios.get('/api/v1/auth/lista-vendedores')
        .then(res => setListaVendedores(res.data.dados))
        .catch(e => console.log(e));
        
      axios.get('/api/v1/auth/lista-gerentes')
        .then(res => setListaGerentes(res.data.dados))
        .catch(e => console.log("Erro ao buscar gerentes:", e));
    }
  }, [modo]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      const res = await axios.post('/api/v1/auth/login', { email, senha });
      
      const usuario = res.data.usuario;
      const token = res.data.access_token;
      
      if (usuario.primeiro_acesso) {
        setModo('primeiro_acesso');
      } else {
        localStorage.setItem('nexus_token', token); // Grava o crachá
        onLoginSuccess(usuario);
      }
    } catch (error: any) {
      alert(error.response?.data?.detail || "Erro ao conectar com o servidor.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleCadastro = async (e: React.FormEvent) => {
    e.preventDefault();
    if (funcao === 'Executivo' && !nomeVendedor) return alert("Selecione o Vendedor para amarrar à conta do Executivo.");
    if (funcao === 'Gerente' && !nomeGerente) return alert("Selecione o nome do Gerente para amarrar à conta.");
    
    setIsLoading(true);
    try {
      await axios.post('/api/v1/auth/cadastrar', {
        nome, 
        email, 
        senha_inicial: senha, 
        funcao, 
        nome_vendedor: funcao === 'Executivo' ? nomeVendedor : null,
        gerente_nome: funcao === 'Gerente' ? nomeGerente : null
      });
      alert("✅ Usuário cadastrado com sucesso! A senha digitada é a senha inicial.");
      setModo('login');
    } catch (error: any) {
      alert(error.response?.data?.detail || "Erro ao cadastrar.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleTrocaSenha = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      await axios.post('/api/v1/auth/alterar-senha', { email, nova_senha: novaSenha });
      alert("🔒 Senha atualizada! Faça o login novamente com a sua nova senha.");
      setSenha(''); setNovaSenha('');
      setModo('login');
    } catch (error: any) {
      alert("Erro ao trocar senha.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-slate-50 flex font-sans">
      
      {/* Lado Esquerdo - Decorativo */}
      <div className="hidden lg:flex w-1/2 bg-slate-900 relative overflow-hidden flex-col justify-between p-12">
        <div className="absolute inset-0 bg-gradient-to-br from-indigo-900/50 to-slate-900 z-0"></div>
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-indigo-500 rounded-full blur-[120px] opacity-20"></div>
        
        <div className="relative z-10">
          <img src="https://www.lineaalimentos.com.br/media/wysiwyg/icones/logo-linea-headline.png" alt="Linea" className="h-12 brightness-0 invert" />
        </div>
        
        <div className="relative z-10 mb-20">
          <h1 className="text-5xl font-black text-white tracking-tighter mb-6 leading-tight">
            NEXUS<br/><span className="text-indigo-400">IBP</span>
          </h1>
          <p className="text-slate-400 text-lg max-w-md font-medium">
            Plataforma unificada de Planejamento de Demanda (S&OP), impulsionada por Inteligência Artificial.
          </p>
        </div>
      </div>

      {/* Lado Direito - Formulários */}
      <div className="w-full lg:w-1/2 flex items-center justify-center p-8 relative">
        <div className="w-full max-w-md">
          
          {/* MODO LOGIN */}
          {modo === 'login' && (
            <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
              <div className="mb-10 text-center lg:text-left">
                <h2 className="text-3xl font-black text-slate-900 tracking-tighter">Bem-vindo de volta</h2>
                <p className="text-slate-500 font-medium mt-2">Insira as suas credenciais para aceder ao sistema.</p>
              </div>

              <form onSubmit={handleLogin} className="flex flex-col gap-5">
                <div>
                  <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 block">E-mail Corporativo</label>
                  <div className="relative">
                    <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
                    <input type="email" required value={email} onChange={e => setEmail(e.target.value)} className="w-full bg-white border border-slate-200 py-4 pl-12 pr-4 rounded-2xl text-sm font-bold text-slate-900 outline-none focus:border-indigo-500 focus:ring-4 focus:ring-indigo-500/10 transition-all" placeholder="nome@linea.com.br" />
                  </div>
                </div>
                <div>
                  <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 block">Senha de Acesso</label>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
                    <input type="password" required value={senha} onChange={e => setSenha(e.target.value)} className="w-full bg-white border border-slate-200 py-4 pl-12 pr-4 rounded-2xl text-sm font-bold text-slate-900 outline-none focus:border-indigo-500 focus:ring-4 focus:ring-indigo-500/10 transition-all" placeholder="••••••••" />
                  </div>
                </div>
                
                <button type="submit" disabled={isLoading} className="w-full mt-4 bg-slate-900 hover:bg-slate-800 text-white py-4 rounded-2xl font-black text-sm uppercase tracking-widest transition-all shadow-lg shadow-slate-900/20 flex justify-center items-center gap-2">
                  {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <>Entrar no Sistema <ChevronRight className="w-4 h-4" /></>}
                </button>
              </form>

              <div className="mt-8 text-center">
                <button onClick={() => setModo('cadastro')} className="text-sm font-bold text-indigo-600 hover:text-indigo-800 transition-colors">
                  Novo utilizador? Cadastre-se aqui.
                </button>
              </div>
            </div>
          )}

          {/* MODO CADASTRO */}
          {modo === 'cadastro' && (
            <div className="animate-in fade-in slide-in-from-right-8 duration-500">
              <div className="mb-8">
                <h2 className="text-2xl font-black text-slate-900 tracking-tighter">Registo de Utilizador</h2>
                <p className="text-slate-500 font-medium text-sm mt-1">Defina o nível de acesso e o perfil operacional.</p>
              </div>

              <form onSubmit={handleCadastro} className="flex flex-col gap-4">
                <div>
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1 block mb-1">Nome Completo</label>
                  <div className="relative">
                    <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input type="text" required value={nome} onChange={e => setNome(e.target.value)} className="w-full bg-white border border-slate-200 py-3 pl-10 pr-4 rounded-xl text-sm font-bold outline-none focus:border-indigo-500" placeholder="Ex: João Silva" />
                  </div>
                </div>
                
                <div>
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1 block mb-1">E-mail</label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input type="email" required value={email} onChange={e => setEmail(e.target.value)} className="w-full bg-white border border-slate-200 py-3 pl-10 pr-4 rounded-xl text-sm font-bold outline-none focus:border-indigo-500" placeholder="nome@linea.com.br" />
                  </div>
                </div>

                <div>
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1 block mb-1">Senha Inicial (Provisória)</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input type="password" required value={senha} onChange={e => setSenha(e.target.value)} className="w-full bg-white border border-slate-200 py-3 pl-10 pr-4 rounded-xl text-sm font-bold outline-none focus:border-indigo-500" placeholder="••••••••" />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 mt-2">
                  <div 
                    onClick={() => setFuncao('Administrador')}
                    className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Administrador' ? 'border-indigo-600 bg-indigo-50' : 'border-slate-200 hover:border-indigo-300'}`}
                  >
                    <ShieldCheck className={`w-5 h-5 ${funcao === 'Administrador' ? 'text-indigo-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Administrador' ? 'text-indigo-900' : 'text-slate-500'}`}>Admin</span>
                  </div>
                  <div 
                    onClick={() => setFuncao('Gerente')}
                    className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Gerente' ? 'border-emerald-600 bg-emerald-50' : 'border-slate-200 hover:border-emerald-300'}`}
                  >
                    <Briefcase className={`w-5 h-5 ${funcao === 'Gerente' ? 'text-emerald-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Gerente' ? 'text-emerald-900' : 'text-slate-500'}`}>Gerente</span>
                  </div>
                  <div 
                    onClick={() => setFuncao('Supply Chain')}
                    className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Supply Chain' ? 'border-amber-600 bg-amber-50' : 'border-slate-200 hover:border-amber-300'}`}
                  >
                    <Factory className={`w-5 h-5 ${funcao === 'Supply Chain' ? 'text-amber-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Supply Chain' ? 'text-amber-900' : 'text-slate-500'}`}>Supply Chain</span>
                  </div>
                  <div 
                    onClick={() => setFuncao('Executivo')}
                    className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Executivo' ? 'border-blue-600 bg-blue-50' : 'border-slate-200 hover:border-blue-300'}`}
                  >
                    <User className={`w-5 h-5 ${funcao === 'Executivo' ? 'text-blue-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Executivo' ? 'text-blue-900' : 'text-slate-500'}`}>Executivo (Vendas)</span>
                  </div>
                </div>

                {/* CAMPO CONDICIONAL PARA GERENTE */}
                {funcao === 'Gerente' && (
                  <div className="mt-2 animate-in fade-in slide-in-from-top-2">
                    <label className="text-[10px] font-black text-emerald-600 uppercase tracking-widest ml-1 block mb-1">Amarração de Gerente (Obrigatório)</label>
                    <select required value={nomeGerente} onChange={e => setNomeGerente(e.target.value)} className="w-full bg-emerald-50/50 border border-emerald-200 py-3 px-4 rounded-xl text-sm font-bold text-slate-800 outline-none focus:border-emerald-500 cursor-pointer">
                      <option value="">-- SELECIONE A GERÊNCIA NO ERP --</option>
                      {listaGerentes.map(g => <option key={g} value={g}>{g}</option>)}
                    </select>
                  </div>
                )}

                {/* CAMPO CONDICIONAL PARA EXECUTIVO */}
                {funcao === 'Executivo' && (
                  <div className="mt-2 animate-in fade-in slide-in-from-top-2">
                    <label className="text-[10px] font-black text-blue-600 uppercase tracking-widest ml-1 block mb-1">Amarração de Vendedor (Obrigatório)</label>
                    <select required value={nomeVendedor} onChange={e => setNomeVendedor(e.target.value)} className="w-full bg-blue-50/50 border border-blue-200 py-3 px-4 rounded-xl text-sm font-bold text-slate-800 outline-none focus:border-blue-500 cursor-pointer">
                      <option value="">-- SELECIONE O VENDEDOR NO ERP --</option>
                      {listaVendedores.map(v => <option key={v} value={v}>{v}</option>)}
                    </select>
                  </div>
                )}
                
                <button type="submit" disabled={isLoading} className="w-full mt-4 bg-indigo-600 hover:bg-indigo-700 text-white py-3.5 rounded-xl font-black text-sm uppercase tracking-widest transition-all flex justify-center items-center">
                  {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : "Criar Utilizador"}
                </button>
              </form>

              <div className="mt-6 text-center">
                <button onClick={() => setModo('login')} className="text-sm font-bold text-slate-400 hover:text-slate-600 transition-colors">
                  Voltar para o Login
                </button>
              </div>
            </div>
          )}

          {/* MODO PRIMEIRO ACESSO */}
          {modo === 'primeiro_acesso' && (
            <div className="animate-in fade-in zoom-in duration-500">
              <div className="mb-8 text-center bg-amber-50 border border-amber-200 p-6 rounded-3xl">
                <div className="w-12 h-12 bg-amber-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <KeyRound className="w-6 h-6 text-amber-600" />
                </div>
                <h2 className="text-2xl font-black text-amber-900 tracking-tighter">Primeiro Acesso</h2>
                <p className="text-amber-700 font-medium text-sm mt-2">Por questões de segurança corporativa, é necessário definir uma senha pessoal antes de aceder ao sistema.</p>
              </div>

              <form onSubmit={handleTrocaSenha} className="flex flex-col gap-5">
                <div>
                  <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 block">Nova Senha Definitiva</label>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
                    <input type="password" required minLength={6} value={novaSenha} onChange={e => setNovaSenha(e.target.value)} className="w-full bg-white border border-slate-200 py-4 pl-12 pr-4 rounded-2xl text-sm font-bold text-slate-900 outline-none focus:border-amber-500 transition-all" placeholder="Digite a nova senha..." />
                  </div>
                </div>
                
                <button type="submit" disabled={isLoading || novaSenha.length < 6} className="w-full mt-2 bg-amber-500 hover:bg-amber-600 text-white py-4 rounded-2xl font-black text-sm uppercase tracking-widest transition-all shadow-lg shadow-amber-500/20 flex justify-center items-center">
                  {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : "Gravar Senha e Entrar"}
                </button>
              </form>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}