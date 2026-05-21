import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import engine
from app.models.domain_models import FatoIbpGranular
from app.ml.forecaster import NexusForecaster

def executar_patch_ia():
    print("🚀 [PATCH] Iniciando atualização isolada do vol_ia para o ciclo 05/2026 (Estratégia B)...")
    
    # 1. Gerar Nova Previsão (Com Rolling Origin e Pesos Táticos)
    print("🧠 [PATCH] 1. Rodando a Nova Arena (Aguarde o processamento dos Modelos Especialistas)...")
    forecaster = NexusForecaster()
    df_nova_ia = forecaster.executar_arena().to_pandas()
    
    # 2. Puxar Histórico de Vendas Real (Recálculo Zero Histórico)
    print("📊 [PATCH] 2. Mapeando histórico puro de faturamento para rateio...")
    query_hist = text("""
        SELECT sku, cgc, SUM(qt_pedido) as hist_total
        FROM fato_vendas
        WHERE data_pedido >= (CURRENT_DATE - INTERVAL '12 months')
        GROUP BY sku, cgc;
    """)
    df_hist = pd.read_sql(query_hist, engine)
    
    mapa_hist = {}
    for _, row in df_hist.iterrows():
        sku = str(row['sku'])
        cgc = str(row['cgc'])
        if sku not in mapa_hist:
            mapa_hist[sku] = {}
        mapa_hist[sku][cgc] = float(row['hist_total'])

    # 3. Preparar a Atualização Cirúrgica
    print("🔄 [PATCH] 3. Lendo as linhas exatas do ciclo 05/2026 na tabela de IBP...")
    with Session(engine) as session:
        linhas_banco = session.query(
            FatoIbpGranular.id,
            FatoIbpGranular.sku,
            FatoIbpGranular.mes_projetado,
            FatoIbpGranular.cgc
        ).filter(FatoIbpGranular.ciclo_sop == '05/2026').all()
        
        # Agrupar linhas do banco convertendo a data para YYYY-MM para match perfeito
        agrupamento_linhas = {}
        for linha in linhas_banco:
            mes_str = linha.mes_projetado.strftime('%Y-%m')
            chave = (str(linha.sku), mes_str)
            if chave not in agrupamento_linhas:
                agrupamento_linhas[chave] = []
            agrupamento_linhas[chave].append(linha)

        updates_para_banco = []
        
        print("🧮 [PATCH] 4. Aplicando Matemática de Rateio sobre o Novo Volume Global...")
        for _, prev in df_nova_ia.iterrows():
            sku_ia = str(prev['produto'])
            # Converter a data da IA também para YYYY-MM
            mes_ia_str = pd.to_datetime(prev['mes_projetado']).strftime('%Y-%m')
            vol_global_ia = float(prev['vol_ia_global'])
            
            chave = (sku_ia, mes_ia_str)
            if chave not in agrupamento_linhas:
                continue 
            
            linhas_clientes = agrupamento_linhas[chave]
            
            # Descobrir o peso total DESTE grupo específico de clientes
            peso_total_grupo = sum(mapa_hist.get(sku_ia, {}).get(str(linha.cgc), 0.0) for linha in linhas_clientes)
            
            # Ratear o novo volume
            for linha in linhas_clientes:
                cgc = str(linha.cgc)
                hist_cliente = mapa_hist.get(sku_ia, {}).get(cgc, 0.0)
                
                if peso_total_grupo > 0:
                    fatia = hist_cliente / peso_total_grupo
                else:
                    fatia = 1.0 / len(linhas_clientes)
                    
                novo_vol_cliente = int(round(vol_global_ia * fatia))
                
                updates_para_banco.append({
                    "id": linha.id,
                    "vol_ia": novo_vol_cliente
                })
        
        print(f"💾 [PATCH] 5. Salvando {len(updates_para_banco)} registros atualizados no banco...")
        session.bulk_update_mappings(FatoIbpGranular, updates_para_banco)
        session.commit()
        
        print("✅ [PATCH] Operação concluída com sucesso! Teste A/B paralelo pronto para análise nas telas do Supply e S&OP.")

if __name__ == "__main__":
    executar_patch_ia()