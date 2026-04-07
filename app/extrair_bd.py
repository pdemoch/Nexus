import pandas as pd
from app.core.database import engine

def exportar_para_excel():
    print("⏳ A conectar à base de dados Nexus...")
    ficheiro_saida = "Nexus_Base_Completa.xlsx"
    
    # Usamos o Pandas com o SQLAlchemy (engine) para ler as tabelas e converter em Excel
    with pd.ExcelWriter(ficheiro_saida, engine="openpyxl") as writer:
        print("Extraindo Dimensão de Produtos...")
        pd.read_sql_table("dim_produtos", engine).to_excel(writer, index=False, sheet_name="Produtos")
        
        print("Extraindo Dimensão de Clientes...")
        pd.read_sql_table("dim_clientes", engine).to_excel(writer, index=False, sheet_name="Clientes")
        
        print("Extraindo Fato S&OP (IA e Consenso)...")
        pd.read_sql_table("fato_ibp_granular", engine).to_excel(writer, index=False, sheet_name="Previsoes_SOP")
        
        # A tabela de Vendas pode ser pesada (445 mil linhas), se o Excel ficar lento, avise-me!
        print("Extraindo Histórico de Vendas (Isto pode demorar uns segundos)...")
        pd.read_sql_table("fato_vendas", engine).to_excel(writer, index=False, sheet_name="Historico_Vendas")

    print(f"✅ [SUCESSO] Base de dados exportada para o ficheiro: {ficheiro_saida}")

if __name__ == "__main__":
    exportar_para_excel()