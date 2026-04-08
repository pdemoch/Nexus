import React, { useState, useMemo, useEffect } from 'react';
import { Rocket, Target, DollarSign, BarChart3, Save, Layers, Filter, ChevronDown } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import axios from 'axios';

// ==========================================
// COMPONENTE: DROPDOWN COM BUSCA (SEARCHABLE SELECT)
// ==========================================
const SearchableSelect = ({ options, value, onChange, placeholder, icon: Icon }: any) => {
  const [isOpen, setIsOpen] = useState(false);
  const [busca, setBusca] = useState('');

  const selectedOption = options.find((o: any) => o.value === value);
  const displayValue = isOpen ? busca : (selectedOption ? selectedOption.label : '');

  const filtrados = options.filter((o: any) => 
    o.label.toLowerCase().includes(busca.toLowerCase()) || 
    o.value.toLowerCase().includes(busca.toLowerCase())
  );

  return (
    <div className={`relative w-full ${isOpen ? 'z-50' : 'z-10'}`}>
      {isOpen && <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)}></div>}
      
      <div className="relative z-50 flex items-center gap-2 px-4 bg-slate-50 rounded-2xl border border-slate-200 focus-within:border-indigo-500 focus-within:bg-white transition-all">
        {Icon && <Icon className="w-4 h-4 text-slate-400" />}
        <input
          type="text"
          className="w-full bg-transparent py-3 text-sm font-bold text-slate-900 outline-none placeholder:text-slate-400"
          placeholder={placeholder}
          value={displayValue}
          onChange={(e) => { 
            setBusca(e.target.value); 
            setIsOpen(true); 
            if(!isOpen) onChange(''); 
          }}
          onClick={() => setIsOpen(true)}
        />
        <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </div>

      {isOpen && (
        <div className="absolute z-50 w-full mt-2 bg-white border border-slate-100 shadow-2xl rounded-2xl max-h-60 overflow-y-auto py-2">
          {filtrados.length > 0 ? filtrados.map((o: any) => (
            <div
              key={o.value}
              onClick={() => { onChange(o.value); setIsOpen(false); setBusca(''); }}
              className="px-4 py-3 text-sm font-bold text-slate-700 hover:bg-indigo-50 hover:text-indigo-700 cursor-pointer transition-colors border-b border-slate-50 last:border-0"
            >
              {o.label}
            </div>
          )) : (
            <div className="px-4 py-3 text-sm font-medium text-slate-400">Nenhum resultado encontrado...</div>
          )}
        </div>
      )}
    </div>
  );
};

// ==========================================
// TELA PRINCIPAL: NPD ARENA
// ==========================================
export default function NPDArena() {
  const [codigoNPD, setCodigoNPD] = useState('');
  const [nomeNPD, setNomeNPD] = useState('');
  const [skuEspelho, setSkuEspelho] = useState('');
  const [categoria, setCategoria] = useState('');
  const [segmento, setSegmento] = useState('');
  const [pmv, setPmv] = useState<number | ''>('');
  const [baseline, setBaseline] = useState<number | ''>('');
  
  const [listaEspelhos, setListaEspelhos] = useState<any[]>([]);
  const [listaCategorias, setListaCategorias] = useState<string[]>([]);
  const [listaSegmentos, setListaSegmentos] = useState<string[]>([]);

  const mesesDinamicos = useMemo(() => {
    const nomesMeses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
    const getMesProj = (addOffset: number) => {
      const d = new Date();
      d.setMonth(d.getMonth() + addOffset);
      return `${nomesMeses[d.getMonth()]}/${d.getFullYear().toString().slice(2)}`;
    };
    return [getMesProj(2), getMesProj(3), getMesProj(4)];
  }, []);

  const [rampUp, setRampUp] = useState<number[]>([20, 50, 100]); 

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await axios.get('/api/v1/npd/espelhos');
        setListaEspelhos(res.data.espelhos || []);
        setListaCategorias(res.data.categorias || []);
        setListaSegmentos(res.data.segmentos || []);
      } catch (e) { console.error("Erro ao carregar listas do NPD", e); }
    };
    fetchData();
  }, []);

  const handleRampUpChange = (index: number, valor: string) => {
    const novoValor = valor === '' ? 0 : Math.min(200, Math.max(0, parseInt(valor) || 0));
    const novaCurva = [...rampUp];
    novaCurva[index] = novoValor;
    setRampUp(novaCurva);
  };

  const formatarMoeda = (valor: number) => 
    new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(Math.round(valor));

  const projecao = useMemo(() => {
    const base = Number(baseline) || 0;
    const preco = Number(pmv) || 0;
    
    return mesesDinamicos.map((mes, i) => {
      const volumeCalculado = Math.round(base * (rampUp[i] / 100));
      return { mes, percentual: rampUp[i], volume: volumeCalculado, receita: volumeCalculado * preco };
    });
  }, [baseline, pmv, rampUp, mesesDinamicos]);

  const handleSalvarNPD = async () => {
    if (!codigoNPD || !nomeNPD || !skuEspelho || !categoria || !segmento || !baseline || !pmv) {
        return alert("Preencha todos os campos obrigatórios.");
    }
    if (!window.confirm(`Injetar o lançamento [${codigoNPD}] - ${nomeNPD} na janela de S&OP (${mesesDinamicos.join(', ')})?`)) return;
    
    try {
      const payload = {
        codigo_lancamento: codigoNPD,
        nome_lancamento: nomeNPD,
        sku_espelho: skuEspelho,
        categoria: categoria,
        segmento: segmento,
        pmv: Number(pmv),
        baseline: Number(baseline),
        projecao: projecao
      };

      await axios.post('/api/v1/npd/injetar', payload);
      
      alert("🚀 Lançamento injetado com sucesso no S&OP Global!");
      
      setCodigoNPD(''); setNomeNPD(''); setSkuEspelho(''); setCategoria(''); setSegmento(''); setPmv(''); setBaseline(''); 
      setRampUp([20, 50, 100]);
      
    } catch (error: any) {
      alert(error.response?.data?.detail || "Erro inesperado ao injetar lançamento.");
    }
  };

  const opcoesEspelhos = listaEspelhos.map(p => ({ value: p.produto, label: `${p.produto} - ${p.descricao}` }));
  const opcoesCategorias = listaCategorias.map(c => ({ value: c, label: c }));
  const opcoesSegmentos = listaSegmentos.map(s => ({ value: s, label: s }));

  return (
    // CORREÇÃO DO SCROLL: Removido 'overflow-hidden' e adicionado 'overflow-y-auto'
    <div className="w-full bg-[#f8fafc] font-sans min-h-screen pb-20 overflow-y-auto custom-scrollbar">
      <div className="flex-1 flex flex-col max-w-[1600px] mx-auto p-6 lg:p-12 relative">
        
        <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 bg-white p-6 rounded-[32px] shadow-sm border border-gray-100">
          <div>
            <h1 className="text-2xl font-black text-slate-900 flex items-center gap-3 tracking-tighter">
              <Rocket className="w-8 h-8 text-indigo-600" /> ARENA DE INOVAÇÃO (NPD)
            </h1>
            <p className="text-slate-500 font-medium text-sm mt-1 ml-11">Planejamento e injeção de lançamentos na janela S&OP ativa.</p>
          </div>
          <button 
            onClick={handleSalvarNPD}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-8 py-4 rounded-2xl text-sm font-black tracking-widest uppercase shadow-lg shadow-indigo-600/30 transition-all hover:scale-105"
          >
            <Save className="w-5 h-5" />
            Injetar no Consenso
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 flex-1">
          <div className="lg:col-span-5 bg-white p-8 rounded-[40px] shadow-sm border border-gray-100 flex flex-col gap-6">
            <h2 className="text-sm font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 pb-4">1. Parâmetros do Lançamento</h2>
            
            <div className="grid grid-cols-3 gap-4">
              <div className="col-span-1">
                <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 block">Código (SKU)</label>
                <input 
                  type="text" placeholder="Ex: 12345" value={codigoNPD} onChange={e => setCodigoNPD(e.target.value.trim().toUpperCase())}
                  className="w-full bg-slate-50 border border-slate-200 p-3 rounded-2xl text-sm font-bold text-slate-900 outline-none focus:border-indigo-500 focus:bg-white transition-all"
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 block">Nome do Produto</label>
                <input 
                  type="text" placeholder="Ex: NOVO CHOCOLATE 90G" value={nomeNPD} onChange={e => setNomeNPD(e.target.value.toUpperCase())}
                  className="w-full bg-slate-50 border border-slate-200 p-3 rounded-2xl text-sm font-bold text-slate-900 outline-none focus:border-indigo-500 focus:bg-white transition-all"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 flex items-center gap-1">Categoria</label>
                <SearchableSelect options={opcoesCategorias} value={categoria} onChange={setCategoria} placeholder="Digitar..." icon={Filter} />
              </div>
              <div>
                <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 flex items-center gap-1">Segmento</label>
                <SearchableSelect options={opcoesSegmentos} value={segmento} onChange={setSegmento} placeholder="Digitar..." icon={Filter} />
              </div>
            </div>

            <div>
              <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 flex items-center gap-1">SKU Espelho (Cópia de Distribuição)</label>
              <SearchableSelect options={opcoesEspelhos} value={skuEspelho} onChange={setSkuEspelho} placeholder="Buscar SKU veterano..." icon={Layers} />
            </div>

            <div className="grid grid-cols-2 gap-4 mt-2">
              <div>
                <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 flex items-center gap-1"><DollarSign className="w-3 h-3"/> PMV Esperado</label>
                <input 
                  type="number" placeholder="R$ 0,00" value={pmv} onChange={e => setPmv(e.target.value === '' ? '' : Number(e.target.value))}
                  className="w-full bg-emerald-50 border border-emerald-200 p-4 rounded-2xl text-sm font-black text-emerald-900 outline-none focus:border-emerald-500 transition-all text-center"
                />
              </div>
              <div>
                <label className="text-xs font-black text-slate-700 uppercase tracking-widest ml-1 mb-2 flex items-center gap-1"><Target className="w-3 h-3"/> Vol. Nacional (100%)</label>
                <input 
                  type="number" placeholder="Caixas / Unid." value={baseline} onChange={e => setBaseline(e.target.value === '' ? '' : Number(e.target.value))}
                  className="w-full bg-blue-50 border border-blue-200 p-4 rounded-2xl text-sm font-black text-blue-900 outline-none focus:border-blue-500 transition-all text-center"
                />
              </div>
            </div>
            
            <div className="mt-auto bg-slate-900 rounded-3xl p-6 text-white shadow-xl relative overflow-hidden">
               <div className="absolute -right-4 -bottom-4 opacity-10"><BarChart3 className="w-32 h-32"/></div>
               <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">Receita Alvo (em 100%)</p>
               <p className="text-3xl font-black">{formatarMoeda((Number(baseline)||0) * (Number(pmv)||0))}</p>
            </div>
          </div>

          <div className="lg:col-span-7 flex flex-col gap-6">
            <div className="bg-white p-8 rounded-[40px] shadow-sm border border-gray-100">
              <h2 className="text-sm font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 pb-4 mb-6">2. Ramp-Up (% de Aceleração S&OP)</h2>
              
              <div className="grid grid-cols-3 gap-6">
                {mesesDinamicos.map((mes, idx) => (
                  <div key={idx} className="flex flex-col items-center">
                    <span className="text-xs font-black text-slate-800 mb-3 uppercase tracking-widest">{mes}</span>
                    <div className="relative w-full">
                      <input 
                        type="number" 
                        value={rampUp[idx] === 0 ? '' : rampUp[idx]} 
                        onChange={(e) => handleRampUpChange(idx, e.target.value)}
                        className={`w-full p-4 text-center text-2xl font-black rounded-2xl border-2 outline-none transition-all
                          ${rampUp[idx] >= 100 ? 'bg-indigo-50 border-indigo-200 text-indigo-700' : 'bg-slate-50 border-slate-200 text-slate-700 focus:border-indigo-500 focus:bg-white'}`}
                      />
                      <span className="absolute right-4 top-1/2 -translate-y-1/2 text-sm font-black opacity-40">%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white p-8 rounded-[40px] shadow-sm border border-gray-100 flex-1 flex flex-col">
              <h2 className="text-sm font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 pb-4 mb-6">3. Projeção Resultante</h2>
              
              <div className="grid grid-cols-3 gap-4 mb-8">
                 {projecao.map((p, idx) => (
                   <div key={idx} className="bg-slate-50 rounded-2xl p-5 text-center border border-slate-100">
                     <p className="text-[10px] font-bold text-slate-400 uppercase mb-2">Volume {p.mes}</p>
                     <p className="text-2xl font-black text-slate-800 leading-none">{p.volume.toLocaleString('pt-BR')} <span className="text-[10px] opacity-50">CX</span></p>
                     <div className="h-px w-full bg-slate-200 my-3"></div>
                     <p className="text-xs font-black text-emerald-600 truncate">{formatarMoeda(p.receita)}</p>
                   </div>
                 ))}
              </div>

              <div className="flex-1 min-h-[200px] w-full -ml-4">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={projecao}>
                    <defs>
                      <linearGradient id="colorVolume" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#4f46e5" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                    <XAxis dataKey="mes" tick={{fontSize: 10, fontWeight: 900}} axisLine={false} tickLine={false} />
                    <YAxis tick={{fontSize: 10}} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{borderRadius: '20px', border: 'none', boxShadow: '0 10px 30px rgba(0,0,0,0.1)'}} />
                    <Area type="monotone" dataKey="volume" name="Volume" stroke="#4f46e5" strokeWidth={4} fillOpacity={1} fill="url(#colorVolume)" dot={{r: 6, fill: '#4f46e5', strokeWidth: 2, stroke: '#fff'}} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}