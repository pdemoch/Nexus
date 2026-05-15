import sys
import json
from collections import defaultdict
import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from decimal import Decimal

# Importe os seus modelos e a função de DB
# Ajuste os imports abaixo se a estrutura de pastas do seu projeto for diferente
from app.core.database import SessionLocal
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, ControleCiclo

# =====================================================================
# FUNÇÕES MOCKADAS (Para isolar o teste do restante do sistema)
# =====================================================================
def get_current_cycle(db: Session):
    # Forçamos o ciclo que sabemos que tem dados (comprovado pelo seu SQL)
    return '05/2026'

def get_truth_query(db, ciclo, data_inicio, data_fim):
    """Uma versão simplificada e isolada da query base para o teste"""
    return db.query(FatoIbpGranular).filter(
        FatoIbpGranular.ciclo_sop == ciclo,
        FatoIbpGranular.mes_projetado >= data_inicio,
        FatoIbpGranular.mes_projetado <= data_fim
    ).outerjoin(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
     .outerjoin(DimProduto, FatoIbpGranular.sku == DimProduto.sku)

# Custom encoder para lidar com datas e Decimais no JSON dump
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

# =====================================================================
# O TESTE DA LÓGICA DE ÁRVORE
# =====================================================================
def run_test():
    db = SessionLocal()
    try:
        ciclo = get_current_cycle(db)
        
        hoje = datetime.date(2026, 5, 1) # Fixado em Maio de 2026 com base nos seus prints do DB
        data_ini = hoje
        data_fim = hoje + relativedelta(months=4)
        
        meses_lista = []
        curr = data_ini
        while curr <= data_fim:
            meses_lista.append(curr.strftime("%Y-%m-%d"))
            curr += relativedelta(months=1)

        print(f"🔄 Janela de Meses: {meses_lista}")

        query_base = get_truth_query(db, ciclo, data_ini, data_fim)

        print("🔄 Executando Query SQL via SQLAlchemy...")
        
        # Expressões SQL blindadas (Usamos as mesmas do último teste)
        coord_expr = func.coalesce(func.nullif(func.trim(DimCliente.supervisor_nome), ''), func.nullif(func.trim(DimCliente.gerente_nome), ''), 'SEM COORDENADOR')
        vend_expr = func.coalesce(func.nullif(func.trim(DimCliente.vendedor_nome), ''), 'SEM VENDEDOR')
        cli_expr = func.coalesce(func.nullif(func.trim(DimCliente.razaosocial), ''), 'CLIENTE NÃO IDENTIFICADO')

        resultados = query_base.with_entities(
            coord_expr.label('coord_final'),
            vend_expr.label('vend_final'),
            cli_expr.label('cli_final'),
            FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('v_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('v_td'),  
            func.sum(FatoIbpGranular.vol_bottomup).label('v_bu'), 
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv')
        ).group_by(
            coord_expr, vend_expr, cli_expr, FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado
        ).all()

        print(f"✅ Query retornou {len(resultados)} blocos agregados.")
        
        if len(resultados) == 0:
            print("❌ ERRO: A query não retornou nada. Verifique a configuração do banco no teste.")
            return

        print("🔄 Montando a Árvore (Dicionários Manuais)...")
        
        arvore = {}
        def criar_meses():
            return {m: {"vol_ia":0, "vol_td":0, "vol_ajustado":0, "receita":0, "pmv":0.0} for m in meses_lista}

        for r in resultados:
            c = str(r.coord_final)
            v = str(r.vend_final)
            rz = str(r.cli_final)
            sku = str(r.sku).strip() if r.sku else "SEM SKU"
            desc = str(r.descricao).strip() if r.descricao else "PRODUTO SEM CADASTRO"
            ms = str(r.mes_projetado)
            
            # Sanitização forte para evitar type errors na matemática
            v_ia = int(r.v_ia) if r.v_ia is not None else 0
            v_td = int(r.v_td) if r.v_td is not None else 0
            v_bu = int(r.v_bu) if r.v_bu is not None else 0
            pmv_base = float(r.pmv) if r.pmv is not None else 0.0
            rec = v_bu * pmv_base

            if c not in arvore: arvore[c] = {"nome": c, "status": "Aberto", "meses": criar_meses(), "vendedores": {}}
            if v not in arvore[c]["vendedores"]: arvore[c]["vendedores"][v] = {"nome": v, "status": "Aberto", "meses": criar_meses(), "clientes": {}}
            if rz not in arvore[c]["vendedores"][v]["clientes"]: arvore[c]["vendedores"][v]["clientes"][rz] = {"nome": rz, "meses": criar_meses(), "produtos": {}}
            if sku not in arvore[c]["vendedores"][v]["clientes"][rz]["produtos"]: arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku] = {"nome": desc, "sku": sku, "meses": criar_meses()}

            if ms in meses_lista:
                for t in [arvore[c]["meses"][ms], arvore[c]["vendedores"][v]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["meses"][ms], arvore[c]["vendedores"][v]["clientes"][rz]["produtos"][sku]["meses"][ms]]:
                    t["vol_ia"] += v_ia
                    t["vol_td"] += v_td
                    t["vol_ajustado"] += v_bu
                    t["receita"] += rec
                    t["pmv"] = (t["receita"] / t["vol_ajustado"]) if t["vol_ajustado"] > 0 else pmv_base

        print("🔄 Convertendo a Árvore para o formato final (Listas no subRows)...")

        dados_finais = []
        for c_key, c_val in arvore.items():
            sub_vendedores = []
            for v_key, v_val in c_val["vendedores"].items():
                sub_clientes = []
                for cl_key, cl_val in v_val["clientes"].items():
                    sub_produtos = []
                    for p_key, p_val in cl_val["produtos"].items():
                        sub_produtos.append({
                            "id": f"{c_key}|{v_key}|{cl_key}|{p_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}|{p_key}", 
                            "nome": p_val["nome"], "produto": p_key, "tipo": "produto",
                            "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in p_val["meses"].items()]
                        })
                    sub_clientes.append({
                        "id": f"{c_key}|{v_key}|{cl_key}", "chave_matriz": f"{c_key}|{v_key}|{cl_key}", "nome": cl_val["nome"], "tipo": "cliente",
                        "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in cl_val["meses"].items()],
                        "subRows": sorted(sub_produtos, key=lambda x: x["nome"])
                    })
                sub_vendedores.append({
                    "id": f"{c_key}|{v_key}", "chave_matriz": f"{c_key}|{v_key}", "nome": v_val["nome"], "tipo": "vendedor", "status": v_val["status"],
                    "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in v_val["meses"].items()],
                    "subRows": sorted(sub_clientes, key=lambda x: x["nome"])
                })
            dados_finais.append({
                "id": c_key, "chave_matriz": c_key, "nome": c_val["nome"], "tipo": "coordenador", "status": c_val["status"],
                "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%m/%y"), **v} for k, v in c_val["meses"].items()],
                "subRows": sorted(sub_vendedores, key=lambda x: x["nome"])
            })

        print("✅ Objeto final construído.")
        
        # Salva o resultado num ficheiro JSON para podermos analisar a fundo
        output_file = 'resultado_arvore.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(dados_finais, f, ensure_ascii=False, indent=2, cls=CustomJSONEncoder)
        
        print(f"🎯 SUCESSO! O JSON foi guardado em '{output_file}'.")
        print("-> Por favor, verifique este ficheiro para garantir que 'subRows' existe em todos os níveis.")

    except Exception as e:
        print(f"❌ ERRO FATAL: {repr(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    run_test()