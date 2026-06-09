import React, { useState, useEffect } from 'react';
import { Lock, Mail, User, ShieldCheck, ChevronRight, Loader2, KeyRound, Briefcase, Factory, Megaphone, Building, Users } from 'lucide-react';
import axios from 'axios';

export default function LoginArena({ onLoginSuccess }: { onLoginSuccess: (userData: any) => void }) {
  const [modo, setModo] = useState<'login' | 'cadastro' | 'primeiro_acesso'>('login');
  const [isLoading, setIsLoading] = useState(false);

  // Estados do Formulário
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [nome, setNome] = useState('');
  const [funcao, setFuncao] = useState('Gerente'); 
  const [nomeGerente, setNomeGerente] = useState(''); 
  const [nomeSupervisor, setNomeSupervisor] = useState(''); // Vinculação para Coordenadores
  const [novaSenha, setNovaSenha] = useState('');
  
  const [listaGerentes, setListaGerentes] = useState<string[]>([]);
  const [listaCoordenadores, setListaCoordenadores] = useState<string[]>([]);

  // Carrega listas dinâmicas do ERP para o Cadastro
  useEffect(() => {
    if (modo === 'cadastro') {
      axios.get('/api/v1/auth/lista-gerentes')
        .then(res => setListaGerentes(res.data.dados))
        .catch(e => console.log("Erro ao carregar gerentes:", e));
      
      axios.get('/api/v1/auth/lista-coordenadores')
        .then(res => setListaCoordenadores(res.data.dados))
        .catch(e => console.log("Erro ao carregar coordenadores:", e));
    }
  }, [modo]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      const res = await axios.post('/api/v1/auth/login', { email, senha });
      const { access_token, usuario } = res.data;
      
      if (usuario.primeiro_acesso) {
        localStorage.setItem('nexus_token_temp', access_token); // Guarda temporário para troca de senha
        setModo('primeiro_acesso');
      } else {
        localStorage.setItem('nexus_token', access_token);
        onLoginSuccess(usuario);
      }
    } catch (error: any) {
      alert(error.response?.data?.detail || "Erro ao realizar login.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleCadastro = async (e: React.FormEvent) => {
    e.preventDefault();
    if (funcao === 'Gerente' && !nomeGerente) return alert("Por favor, selecione a Gerência.");
    if (funcao === 'Coordenador' && !nomeSupervisor) return alert("Por favor, selecione a Coordenação/Equipa.");

    setIsLoading(true);
    try {
      await axios.post('/api/v1/auth/cadastrar', {
        nome,
        email,
        senha_inicial: senha,
        funcao,
        gerente_nome: funcao === 'Gerente' ? nomeGerente : null,
        supervisor_nome: funcao === 'Coordenador' ? nomeSupervisor : null
      });
      alert("✅ Registo enviado! O Administrador irá rever o seu acesso brevemente.");
      setModo('login');
    } catch (error: any) {
      alert(error.response?.data?.detail || "Erro ao realizar o registo.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleTrocaSenha = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      // Usando o e-mail e a nova senha para definir o acesso definitivo
      await axios.post('/api/v1/auth/reset-password-self', { email, nova_senha: novaSenha });
      alert("🔒 Senha atualizada! Por favor, entre agora com as novas credenciais.");
      setSenha(''); setNovaSenha('');
      setModo('login');
    } catch (error: any) {
      alert("Erro ao atualizar a senha.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-slate-50 flex font-sans">
      
      {/* Lado Esquerdo - Decorativo (Layout Linea Mantido) */}
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
          
          {modo === 'login' && (
            <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
              <div className="mb-10">
                <h2 className="text-3xl font-black text-slate-900 tracking-tighter">Bem-vindo ao Nexus</h2>
                <p className="text-slate-500 font-medium mt-2">Insira as suas credenciais de acesso.</p>
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
                
                <button type="submit" disabled={isLoading} className="w-full mt-4 bg-slate-900 hover:bg-slate-800 text-white py-4 rounded-2xl font-black text-sm uppercase tracking-widest transition-all shadow-lg flex justify-center items-center gap-2">
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

          {modo === 'cadastro' && (
            <div className="animate-in fade-in slide-in-from-right-8 duration-500">
              <div className="mb-8">
                <h2 className="text-2xl font-black text-slate-900 tracking-tighter">Registo de Utilizador</h2>
                <p className="text-slate-500 font-medium text-sm mt-1">Defina o seu perfil operacional.</p>
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
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1 block mb-1">E-mail Corporativo</label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input type="email" required value={email} onChange={e => setEmail(e.target.value)} className="w-full bg-white border border-slate-200 py-3 pl-10 pr-4 rounded-xl text-sm font-bold outline-none focus:border-indigo-500" placeholder="nome@linea.com.br" />
                  </div>
                </div>

                <div>
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1 block mb-1">Senha Inicial</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input type="password" required value={senha} onChange={e => setSenha(e.target.value)} className="w-full bg-white border border-slate-200 py-3 pl-10 pr-4 rounded-xl text-sm font-bold outline-none focus:border-indigo-500" placeholder="••••••••" />
                  </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-2">
                  <div onClick={() => setFuncao('Administrador')} className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Administrador' ? 'border-indigo-600 bg-indigo-50' : 'border-slate-200 hover:border-indigo-300'}`}>
                    <ShieldCheck className={`w-5 h-5 ${funcao === 'Administrador' ? 'text-indigo-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Administrador' ? 'text-indigo-900' : 'text-slate-500'}`}>Admin</span>
                  </div>
                  <div onClick={() => setFuncao('Gerente')} className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Gerente' ? 'border-emerald-600 bg-emerald-50' : 'border-slate-200 hover:border-emerald-300'}`}>
                    <Briefcase className={`w-5 h-5 ${funcao === 'Gerente' ? 'text-emerald-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Gerente' ? 'text-emerald-900' : 'text-slate-500'}`}>Gerente</span>
                  </div>
                  {/* Substituição: Coordenador no lugar de Executivo */}
                  <div onClick={() => setFuncao('Coordenador')} className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Coordenador' ? 'border-blue-600 bg-blue-50' : 'border-slate-200 hover:border-blue-300'}`}>
                    <Users className={`w-5 h-5 ${funcao === 'Coordenador' ? 'text-blue-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Coordenador' ? 'text-blue-900' : 'text-slate-500'}`}>Coordenador</span>
                  </div>
                  <div onClick={() => setFuncao('Supply Chain')} className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Supply Chain' ? 'border-amber-600 bg-amber-50' : 'border-slate-200 hover:border-amber-300'}`}>
                    <Factory className={`w-5 h-5 ${funcao === 'Supply Chain' ? 'text-amber-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Supply Chain' ? 'text-amber-900' : 'text-slate-500'}`}>Supply</span>
                  </div>
                  <div onClick={() => setFuncao('Marketing')} className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'Marketing' ? 'border-pink-600 bg-pink-50' : 'border-slate-200 hover:border-pink-300'}`}>
                    <Megaphone className={`w-5 h-5 ${funcao === 'Marketing' ? 'text-pink-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'Marketing' ? 'text-pink-900' : 'text-slate-500'}`}>Marketing</span>
                  </div>
                  <div onClick={() => setFuncao('C-Level')} className={`cursor-pointer border-2 p-3 rounded-xl flex flex-col items-center justify-center gap-1 transition-all ${funcao === 'C-Level' ? 'border-purple-600 bg-purple-50' : 'border-slate-200 hover:border-purple-300'}`}>
                    <Building className={`w-5 h-5 ${funcao === 'C-Level' ? 'text-purple-600' : 'text-slate-400'}`} />
                    <span className={`text-[10px] font-black uppercase tracking-wider ${funcao === 'C-Level' ? 'text-purple-900' : 'text-slate-500'}`}>C-Level</span>
                  </div>
                </div>

                {funcao === 'Gerente' && (
                  <div className="mt-2 animate-in fade-in slide-in-from-top-2">
                    <label className="text-[10px] font-black text-emerald-600 uppercase tracking-widest ml-1 block mb-1">Amarração de Gerente (ERP)</label>
                    <select required value={nomeGerente} onChange={e => setNomeGerente(e.target.value)} className="w-full bg-emerald-50/50 border border-emerald-200 py-3 px-4 rounded-xl text-sm font-bold text-slate-800 outline-none">
                      <option value="">-- SELECIONE A SUA GERÊNCIA --</option>
                      {listaGerentes.map(g => <option key={g} value={g}>{g}</option>)}
                    </select>
                  </div>
                )}

                {funcao === 'Coordenador' && (
                  <div className="mt-2 animate-in fade-in slide-in-from-top-2">
                    <label className="text-[10px] font-black text-blue-600 uppercase tracking-widest ml-1 block mb-1">Equipa / Supervisão (ERP)</label>
                    <select required value={nomeSupervisor} onChange={e => setNomeSupervisor(e.target.value)} className="w-full bg-blue-50/50 border border-blue-200 py-3 px-4 rounded-xl text-sm font-bold text-slate-800 outline-none">
                      <option value="">-- SELECIONE A SUA EQUIPA --</option>
                      {listaCoordenadores.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                )}
                
                <button type="submit" disabled={isLoading} className="w-full mt-4 bg-indigo-600 hover:bg-indigo-700 text-white py-3.5 rounded-xl font-black text-sm uppercase tracking-widest transition-all">
                  {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : "Solicitar Acesso"}
                </button>
              </form>

              <div className="mt-6 text-center">
                <button onClick={() => setModo('login')} className="text-sm font-bold text-slate-400 hover:text-slate-600 transition-colors">Voltar ao Login</button>
              </div>
            </div>
          )}

          {modo === 'primeiro_acesso' && (
            <div className="animate-in fade-in zoom-in duration-500">
              <div className="mb-8 text-center bg-amber-50 border border-amber-200 p-6 rounded-3xl">
                <div className="w-12 h-12 bg-amber-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <KeyRound className="w-6 h-6 text-amber-600" />
                </div>
                <h2 className="text-2xl font-black text-amber-900 tracking-tighter">Primeiro Acesso</h2>
                <p className="text-amber-700 font-medium text-sm mt-2">Defina uma senha definitiva para garantir a segurança da plataforma.</p>
              </div>

              <form onSubmit={handleTrocaSenha} className="flex flex-col gap-5">
                <div>
                  <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 block">Nova Senha Definitiva</label>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
                    <input type="password" required minLength={6} value={novaSenha} onChange={e => setNovaSenha(e.target.value)} className="w-full bg-white border border-slate-200 py-4 pl-12 pr-4 rounded-2xl text-sm font-bold text-slate-900 outline-none focus:border-amber-500 transition-all" placeholder="Digite a nova senha..." />
                  </div>
                </div>
                <button type="submit" disabled={isLoading || novaSenha.length < 6} className="w-full mt-2 bg-amber-500 hover:bg-amber-600 text-white py-4 rounded-2xl font-black text-sm uppercase tracking-widest transition-all shadow-lg shadow-amber-500/20">
                  {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : "Gravar e Entrar"}
                </button>
              </form>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}