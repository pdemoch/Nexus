import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  useReactTable, 
  getCoreRowModel, 
  getExpandedRowModel,
  flexRender, 
  createColumnHelper
} from '@tanstack/react-table';

// --- INTERFACES DE TIPAGEM ---
interface RadarItem {
  sku: string;
  descricao: string;
  curva: string;
  pmv: number;
  metas: { vol_sop: number; vol_meta: number; val_meta: number };
  execucao: { pedidos_qtd: number; faturado_qtd: number; faturado_val: number; corte_qtd: number; corte_val: number; pacing: number };
  logistica: { estoque_d0: number; backlog_total: number; saldo_projetado: number; fat_em_risco: number };
}

interface OportunidadeItem {
  cliente: string;
  regional: string;
  meta_vol: number;
  valor_estimado: number;
  dias_sem_compra: number;
  status: string;
}

const columnHelper = createColumnHelper<RadarItem>();

export default function SoeDashboard() {
  const [radarData, setRadarData] = useState<RadarItem[]>([]);
  const [oportunidades, setOportunidades] = useState<OportunidadeItem[]>([]);
  const [loading, setLoading] = useState(true);

  // --- BUSCA DE DADOS ---
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        // Busca o Radar e as Oportunidades simultaneamente
        const [resRadar, resOportunidades] = await Promise.all([
          axios.get('https://api.lineanexus.com.br/api/v1/soe/radar'),
          axios.get('https://api.lineanexus.com.br/api/v1/soe/oportunidades')
        ]);
        
        setRadarData(resRadar.data.dados || []);
        setOportunidades(resOportunidades.data || []);
      } catch (error) {
        console.error("Erro ao carregar War Room S&OE:", error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  // --- COLUNAS DO RADAR (VISÃO PRINCIPAL) ---
  const radarColumns = [
    columnHelper.display({
      id: 'expander',
      header: () => null,
      cell: ({ row }) => (
        <button 
          onClick={row.getToggleExpandedHandler()} 
          className="text-blue-600 font-bold px-2 cursor-pointer"
        >
          {row.getIsExpanded() ? '▼' : '▶'}
        </button>
      ),
    }),
    columnHelper.accessor('sku', { header: 'SKU' }),
    columnHelper.accessor('descricao', { header: 'Descrição' }),
    columnHelper.accessor('curva', { header: 'Curva' }),
    columnHelper.accessor('metas.vol_meta', { header: 'Meta (Cx)', cell: info => info.getValue().toLocaleString() }),
    columnHelper.accessor('execucao.faturado_qtd', { header: 'Faturado (Cx)', cell: info => info.getValue().toLocaleString() }),
    columnHelper.accessor('logistica.estoque_d0', { header: 'Estoque D0', cell: info => info.getValue().toLocaleString() }),
    columnHelper.accessor('logistica.saldo_projetado', { 
      header: 'Saldo Proj.', 
      cell: info => {
        const val = info.getValue();
        return <span className={val < 0 ? 'text-red-600 font-bold' : 'text-green-600'}>{val.toLocaleString()}</span>;
      } 
    }),
    columnHelper.accessor('execucao.pacing', { 
      header: 'Pacing %', 
      cell: info => `${info.getValue().toFixed(1)}%` 
    }),
  ];

  const table = useReactTable({
    data: radarData,
    columns: radarColumns,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  if (loading) return <div className="p-10 text-center text-xl">Iniciando War Room S&OE...</div>;

  return (
    <div className="p-6 space-y-8 bg-gray-50 min-h-screen">
      <h1 className="text-3xl font-bold text-gray-800">War Room S&OE</h1>

      {/* --- BLOCO 1: RADAR S&OE (TABELA EXPANSÍVEL) --- */}
      <div className="bg-white p-4 rounded shadow">
        <h2 className="text-xl font-semibold mb-4 text-blue-800">Radar de Saúde dos SKUs</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-100">
              {table.getHeaderGroups().map(headerGroup => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map(header => (
                    <th key={header.id} className="px-4 py-2 text-left font-medium text-gray-600">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody className="divide-y divide-gray-200">
              {table.getRowModel().rows.map(row => (
                <React.Fragment key={row.id}>
                  {/* Linha Principal */}
                  <tr className="hover:bg-gray-50">
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-4 py-2">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                  
                  {/* DRILL-DOWN (LINHA EXPANDIDA) */}
                  {row.getIsExpanded() && (
                    <tr className="bg-blue-50">
                      <td colSpan={row.getVisibleCells().length} className="px-8 py-4">
                        <div className="grid grid-cols-3 gap-4">
                          <div className="bg-white p-3 shadow-sm border-l-4 border-blue-500">
                            <h4 className="font-bold text-gray-700">Comercial (R$)</h4>
                            <p>💰 Meta Mês: {(row.original.metas.val_meta).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'})}</p>
                            <p>✅ Faturado: {(row.original.execucao.faturado_val).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'})}</p>
                            <p className="text-red-500">❌ Risco Real: {(row.original.logistica.fat_em_risco).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'})}</p>
                          </div>
                          <div className="bg-white p-3 shadow-sm border-l-4 border-yellow-500">
                            <h4 className="font-bold text-gray-700">Funil de Pedidos (Cx)</h4>
                            <p>📥 Inseridos: {row.original.execucao.pedidos_qtd}</p>
                            <p className="text-red-500">✂️ Cortes: {row.original.execucao.corte_qtd}</p>
                            <p className="text-orange-500">⏳ Backlog: {row.original.logistica.backlog_total}</p>
                          </div>
                          <div className="bg-white p-3 shadow-sm border-l-4 border-green-500">
                            <h4 className="font-bold text-gray-700">Parâmetros</h4>
                            <p>PMV Atual: R$ {row.original.pmv.toFixed(2)}</p>
                            <p>Vol. S&OP: {row.original.metas.vol_sop} Cx</p>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* --- BLOCO 2: OPORTUNIDADES E CHURN (TABELA COMERCIAL) --- */}
      <div className="bg-white p-4 rounded shadow border-t-4 border-orange-500">
        <h2 className="text-xl font-semibold mb-4 text-orange-700">Oportunidades e Risco de Churn (S/ Compra no Mês)</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-orange-50">
              <tr>
                <th className="px-4 py-2 text-left">Cliente</th>
                <th className="px-4 py-2 text-left">Regional</th>
                <th className="px-4 py-2 text-right">Meta (Cx)</th>
                <th className="px-4 py-2 text-right">Perda Fin. (R$)</th>
                <th className="px-4 py-2 text-center">Dias S/ Compra</th>
                <th className="px-4 py-2 text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {oportunidades.map((op, idx) => (
                <tr key={idx} className="hover:bg-orange-50">
                  <td className="px-4 py-2 font-medium text-gray-700">{op.cliente}</td>
                  <td className="px-4 py-2 text-gray-500">{op.regional}</td>
                  <td className="px-4 py-2 text-right">{op.meta_vol.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right font-semibold text-red-600">
                    {op.valor_estimado.toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'})}
                  </td>
                  <td className="px-4 py-2 text-center">
                    <span className="bg-gray-200 px-2 py-1 rounded-full text-xs font-bold">{op.dias_sem_compra}</span>
                  </td>
                  <td className="px-4 py-2 text-center">
                    <span className={`px-2 py-1 rounded text-xs font-bold ${op.status === 'Crítico' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'}`}>
                      {op.status}
                    </span>
                  </td>
                </tr>
              ))}
              {oportunidades.length === 0 && (
                <tr><td colSpan={6} className="text-center py-4 text-gray-500">Nenhum risco detectado. Todos os clientes com meta já compraram!</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

    </div>
  );
}