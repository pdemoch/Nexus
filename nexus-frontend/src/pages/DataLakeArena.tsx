import { useState, useEffect } from 'react';
import axios from 'axios';
import { Database, FileType, RefreshCw, HardDrive, AlertTriangle, ShieldCheck } from 'lucide-react';

interface ArquivoLake {
    nome: string;
    tamanho_mb: number;
    ultima_atualizacao: string;
}

export default function DataLakeArena() {
    const [arquivos, setArquivos] = useState<ArquivoLake[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [erro, setErro] = useState('');

    const buscarArquivosDataLake = async () => {
        setIsLoading(true);
        setErro('');
        try {
            const response = await axios.get('/api/v1/datalake/files');
            if (response.data && response.data.erro) {
                setErro(response.data.erro);
            } else {
                setArquivos(response.data || []);
            }
        } catch (error) {
            console.error("Erro ao ler repositório S3", error);
            setErro("Não foi possível conectar ao servidor para ler o Data Lake.");
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        buscarArquivosDataLake();
    }, []);

    return (
        <div className="min-h-screen bg-slate-950 p-8 text-slate-100 selection:bg-indigo-500/30">
            <div className="max-w-6xl mx-auto">
                
                {/* TOPO DA TELA */}
                <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4 mb-8 border-b border-slate-800 pb-6">
                    <div>
                        <div className="flex items-center gap-3">
                            <span className="bg-indigo-500/10 text-indigo-400 p-2 rounded-xl border border-indigo-500/20">
                                <Database className="w-6 h-6" />
                            </span>
                            <h1 className="text-3xl font-black tracking-tight text-white">
                                AWS Data Lake <span className="text-indigo-500 text-lg font-bold font-mono">Layer Bronze</span>
                            </h1>
                        </div>
                        <p className="text-slate-400 mt-2 text-sm max-w-2xl">
                            Auditoria analítica dos repositórios colunares <code className="text-emerald-400 font-mono text-xs bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800">.parquet</code> compactados via <span className="text-slate-200 font-semibold">Snappy Engine</span> e gerados a partir da API da MTRIX.
                        </p>
                    </div>
                    
                    <button 
                        onClick={buscarArquivosDataLake}
                        disabled={isLoading}
                        className="w-full md:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-800 text-white px-5 py-2.5 rounded-xl flex items-center justify-center gap-2 font-black transition-all shadow-[0_4px_20px_rgba(79,70,229,0.2)] active:scale-95"
                    >
                        <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
                        Sincronizar Storage
                    </button>
                </div>

                {/* ALERTA DE ERRO */}
                {erro && (
                    <div className="mb-6 bg-rose-500/10 border border-rose-500/20 text-rose-400 p-4 rounded-xl flex items-center gap-3 text-sm">
                        <AlertTriangle className="w-5 h-5 shrink-0" />
                        <span><strong>Erro de Infraestrutura:</strong> {erro}</span>
                    </div>
                )}

                {/* INFOCARDS RÁPIDOS */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
                    <div className="bg-slate-900 border border-slate-800 p-5 rounded-2xl flex items-center gap-4">
                        <div className="p-3 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            <ShieldCheck className="w-6 h-6" />
                        </div>
                        <div>
                            <span className="text-xs font-bold text-slate-500 uppercase tracking-widest block">Segurança Integrada</span>
                            <span className="text-sm font-black text-slate-200 block mt-0.5">IAM Instance Role Ativa (Zero-Keys)</span>
                        </div>
                    </div>
                    <div className="bg-slate-900 border border-slate-800 p-5 rounded-2xl flex items-center gap-4">
                        <div className="p-3 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                            <Database className="w-6 h-6" />
                        </div>
                        <div>
                            <span className="text-xs font-bold text-slate-500 uppercase tracking-widest block">Bucket Alvo S3</span>
                            <span className="text-sm font-black font-mono text-slate-200 block mt-0.5">nexus-datalake-linea-prd</span>
                        </div>
                    </div>
                </div>

                {/* TABELA DE ARQUIVOS */}
                <div className="bg-slate-900 border border-slate-800/80 rounded-2xl overflow-hidden shadow-2xl relative">
                    <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500"></div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-slate-300 border-collapse">
                            <thead className="bg-slate-950/40 border-b border-slate-800 text-xs uppercase tracking-wider text-slate-400 font-bold">
                                <tr>
                                    <th className="px-6 py-4 font-black">Estrutura Parquet</th>
                                    <th className="px-6 py-4 font-black">Última Atualização M-2</th>
                                    <th className="px-6 py-4 font-black">Tamanho em Nuvem</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-800/40">
                                {isLoading ? (
                                    <tr>
                                        <td colSpan={3} className="p-12 text-center">
                                            <RefreshCw className="w-8 h-8 animate-spin text-indigo-500 mx-auto mb-2" />
                                            <span className="text-sm text-slate-500 font-bold font-mono">Varrendo blocos colunares do Amazon S3...</span>
                                        </td>
                                    </tr>
                                ) : arquivos.length === 0 ? (
                                    <tr>
                                        <td colSpan={3} className="p-12 text-center text-slate-500 font-medium">
                                            Nenhum dado estruturado encontrado no prefixo informado. Execute o Pipeline do S&OP para popular a camada Bronze.
                                        </td>
                                    </tr>
                                ) : (
                                    arquivos.map((arq) => (
                                        <tr key={arq.nome} className="hover:bg-slate-800/20 transition-all group">
                                            <td className="px-6 py-4 flex items-center gap-3 font-semibold text-white">
                                                <FileType className="w-5 h-5 text-emerald-400 group-hover:scale-110 transition-transform" />
                                                <span className="font-mono tracking-tight text-sm">{arq.nome}</span>
                                            </td>
                                            <td className="px-6 py-4 text-sm text-slate-400 font-medium">{arq.ultima_atualizacao}</td>
                                            <td className="px-6 py-4">
                                                <span className="bg-slate-950 text-slate-400 px-3 py-1 rounded-lg text-xs font-black flex items-center gap-1.5 w-max border border-slate-800 group-hover:border-indigo-500/30 group-hover:text-indigo-400 transition-colors">
                                                    <HardDrive className="w-3.5 h-3.5" />
                                                    {arq.tamanho_mb.toLocaleString('pt-BR')} MB
                                                </span>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>
        </div>
    );
}