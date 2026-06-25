import datetime
import math
from typing import List, Optional
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular, FatoVendas, DimCliente
from app.api.routers.router_auth import get_current_user

# Preservação integral das amarrações do ecossistema de S&OP
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_previous_cycle,
    get_truth_query,
    check_global_lock,
    parse_date_safe,
    registrar_log_auditoria
)

router = APIRouter(prefix="/api/v1/consensus/macro", tags=["Consenso Top-Down"])

class AjusteTopDown(BaseModel):
    sku: str
    mes_projetado: str
    novo_volume: int

class PayloadAprovarTopDown(BaseModel):
    ajustes: List[AjusteTopDown]
    finalizar_etapa: bool = False

def require_admin(usuario: dict = Depends(get_current_user)):
    # Governação de acesso: Diretoria, Administradores e Marketing possuem passe-livre macro
    if usuario['funcao'] not in ['Administrador', 'Diretoria', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria e Administradores.")
    return usuario

@router.get("/status")
def obter_status_topdown(ciclo: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Rota auxiliar para o Frontend descobrir o estado da tela ao carregar.
    Resolve o erro 404 Not Found.
    """
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)
            
        query = text("SELECT status FROM controle_ciclos WHERE ciclo_sop = :c AND origem = 'Top-Down Arena'")
        trava = db.execute(query, {"c": ciclo}).fetchone()
        
        return {
            "ciclo": ciclo,
            "isLocked": (trava is not None and trava[0] == 'Fechado')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("")
def obter_visao_topdown(
    ciclo: Optional[str] = None, 
    db: Session = Depends(get_db),
    usuario: dict = Depends(require_admin)
):
    """
    Retorna a matriz macro completa para a Diretoria (Top-Down Arena).
    """
    # CORREÇÃO: Usar .get() para evitar o erro KeyError: 'nome'
    nome_usuario = usuario.get('nome') or usuario.get('username') or "Usuário"
    
    try:
        # Se o frontend não enviar o ciclo, o backend descobre sozinho
        if not ciclo:
            ciclo = get_current_cycle(db)
            
        print(f"\n🧭 [TOP-DOWN] Utilizador Executivo '{nome_usuario}' acedeu ao ciclo {ciclo}.")
        
        ciclo_date = datetime.datetime.strptime(ciclo, "%m/%Y")
        hoje = datetime.date.today()
        inicio_projeto = ciclo_date
        
        # 1. LEITURA REAL DA TRAVA (TABELA CONTROLE_CICLOS)
        query_trava = text("""
            SELECT status FROM controle_ciclos 
            WHERE ciclo_sop = :ciclo AND origem = 'Top-Down Arena'
        """)
        resultado_trava = db.execute(query_trava, {"ciclo": ciclo}).fetchone()
        ciclo_fechado = (resultado_trava is not None and resultado_trava[0] == 'Fechado')
        print(f"   -> Estado de travamento verificado no banco: {'FECHADO 🔒' if ciclo_fechado else 'ABERTO 🔓'}")

        # Definição dos 4 meses de projeção tática do ciclo
        meses_interesse = [
            (ciclo_date + relativedelta(months=1)).strftime("%Y-%m-%d"),
            (ciclo_date + relativedelta(months=2)).strftime("%Y-%m-%d"),
            (ciclo_date + relativedelta(months=3)).strftime("%Y-%m-%d"),
            (ciclo_date + relativedelta(months=4)).strftime("%Y-%m-%d")
        ]

        # =========================================================================
        # 📊 CONSTRUÇÃO DA TIMELINE COMPLEXA (GRÁFICOS SUPERIORES)
        # =========================================================================
        # Busca o histórico real de faturamento para os meses anteriores (Realizado)
        query_hist = text("""
            SELECT TO_CHAR(data_pedido, 'YYYY-MM') as mes, SUM(COALESCE(qt_pedido, 0)) as qtd
            FROM fato_vendas
            WHERE data_pedido >= CURRENT_DATE - INTERVAL '6 months' AND data_pedido < :limite
            GROUP BY TO_CHAR(data_pedido, 'YYYY-MM')
        """)
        dados_historicos = db.execute(query_hist, {"limite": ciclo_date.date()}).fetchall()
        
        calendario = defaultdict(lambda: {"Realizado": 0.0, "IA": 0.0, "TopDown": 0.0, "CicloAnterior": 0.0})
        for row in dados_historicos:
            calendario[row[0]]["Realizado"] = float(row[1])

        # 2. QUERY MATRIX COM JOIN RELACIONAL BLINDADO
        query_matriz = text("""
            SELECT 
                f.sku,
                p.categoria,
                p.segmento,
                f.mes_projetado,
                SUM(COALESCE(f.vol_topdown, 0)) as vol_td,
                SUM(COALESCE(f.vol_ia, 0)) as vol_ia,
                AVG(COALESCE(f.pmv_aplicado, 0)) as pmv_medio,
                SUM(COALESCE(f.vol_meta, 0)) as vol_anterior
            FROM fato_ibp_granular f
            LEFT JOIN dim_produto p ON f.sku = p.sku
            LEFT JOIN dim_cliente c ON f.cgc = c.cgc
            WHERE f.ciclo_sop = :ciclo
            GROUP BY f.sku, p.categoria, p.segmento, f.mes_projetado
        """)

        dados_banco = db.execute(query_matriz, {"ciclo": ciclo}).fetchall()
        print(f"   -> {len(dados_banco)} registos relacionais extraídos para processamento.")

        hierarquia = defaultdict(lambda: {"segmentos": defaultdict(lambda: {"skus": defaultdict(dict)})})
        meses_dinamicos = set()

        # Alimenta a matriz e acumula os valores projetados na timeline do gráfico
        for row in dados_banco:
            sku = row[0]
            cat = row[1] or "SEM CATEGORIA"
            seg = row[2] or "SEM SEGMENTO"
            mes_date = row[3]
            
            if isinstance(mes_date, str):
                mes_date = datetime.datetime.strptime(mes_date, "%Y-%m-%d").date()
            
            mes_str = f"{mes_date.year}-{mes_date.month:02d}"
            meses_dinamicos.add(mes_str)

            vol_td = float(row[4])
            vol_ia = float(row[5])
            pmv = float(row[6])
            vol_ant = float(row[7])

            # Preenchimento da estrutura do gráfico superior
            calendario[mes_str]["IA"] += vol_ia
            calendario[mes_str]["TopDown"] += vol_td
            calendario[mes_str]["CicloAnterior"] += vol_ant

            # Montagem estruturada do grid expansível
            fat_td = vol_td * pmv
            orc_fake = vol_ia * pmv * 1.05 

            hierarquia[cat]["segmentos"][seg]["skus"][sku][mes_str] = {
                "vol": vol_td,
                "fat": fat_td,
                "ia": vol_ia,
                "pmv": pmv,
                "orc": orc_fake
            }

        # Formatação final do array 'timeline' com meses traduzidos (Jan/y, Fev/y...)
        meses_pt = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        timeline_final = []
        
        for ms, v in sorted(calendario.items()):
            m_dt = datetime.datetime.strptime(ms, '%Y-%m').date()
            nome_formatado = f"{meses_pt[m_dt.month - 1]}/{m_dt.strftime('%y')}"
            
            timeline_final.append({
                "name": nome_formatado,
                "data_iso": f"{ms}-01",
                "Realizado": None if m_dt >= hoje else round(v["Realizado"]),
                "IA": round(v["IA"]) if m_dt >= hoje.replace(day=1) else None,
                "Top-Down": round(v["TopDown"]) if m_dt >= hoje.replace(day=1) else None,
                "CicloAnterior": round(v["CicloAnterior"]) if m_dt >= hoje.replace(day=1) else None
            })

        # Estruturação final dos nós (Nodes) para o TreeGrid do React
        dados_arvore = []
        for cat_nome, cat_data in hierarquia.items():
            segmentos_list = []
            for seg_nome, seg_data in cat_data["segmentos"].items():
                skus_list = []
                for sku_nome, sku_meses in seg_data["skus"].items():
                    skus_list.append({
                        "sku": sku_nome,
                        "meses": sku_meses
                    })
                segmentos_list.append({
                    "segmento": seg_nome,
                    "skus": skus_list
                })
            dados_arvore.append({
                "categoria": cat_nome,
                "segmentos": segmentos_list
            })

        meses_colunas_ordenados = sorted(list(meses_dinamicos))
        
        return {
            "isLocked": ciclo_fechado,
            "mesesColunas": meses_colunas_ordenados,
            "timeline": timeline_final,
            "dados": dados_arvore
        }

    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO BACKEND TOPDOWN: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/salvar")
def salvar_ajustes_topdown(
    payload: PayloadAprovarTopDown,
    ciclo: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario: dict = Depends(require_admin)
):
    """
    Grava de forma massiva as decisões da Diretoria e realiza 
    a passagem de bastão atómica para as Gerências.
    """
    print(f"\n💾 [TOP-DOWN] {usuario['nome']} submeteu {len(payload.ajustes)} alterações.")
    
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)

        # 1. Trava de Segurança contra Sobrescrita de Ciclo Trancado
        query_trava = text("SELECT status FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'Top-Down Arena'")
        trava = db.execute(query_trava, {"ciclo": ciclo}).fetchone()
        if trava and trava[0] == 'Fechado':
            raise HTTPException(status_code=400, detail="Este ciclo já se encontra encerrado e auditado.")

        # 2. Gravação das Metas (vol_topdown)
        query_update = text("""
            UPDATE fato_ibp_granular 
            SET vol_topdown = :novo_vol 
            WHERE ciclo_sop = :ciclo 
              AND sku = :sku 
              AND mes_projetado = :mes_proj
        """)
        
        for aj in payload.ajustes:
            mes_str_banco = f"{aj.mes_projetado}-01"
            db.execute(query_update, {
                "novo_vol": aj.novo_volume,
                "ciclo": ciclo,
                "sku": aj.sku,
                "mes_proj": mes_str_banco
            })
            
        # 3. FINALIZAÇÃO DA ETAPA E CRIAÇÃO DO CADEADO (CONTROLE_CICLOS)
        if payload.finalizar_etapa:
            print("🔒 [TOP-DOWN] Comando de finalização detetado. Selando a etapa...")
            
            # Limpa qualquer resíduo e cria a trava definitiva de fechamento
            db.execute(text("DELETE FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'Top-Down Arena'"), {"ciclo": ciclo})
            
            db.execute(text("""
                INSERT INTO controle_ciclos (ciclo_sop, origem, status, data_fechamento) 
                VALUES (:ciclo, 'Top-Down Arena', 'Fechado', CURRENT_TIMESTAMP)
            """), {"ciclo": ciclo})
            
            # =========================================================================
            # 🔥 REGRA DE TRANSIÇÃO (CASCATA COMPLETA COM VOL_FINAL)
            # Copia o vol_topdown para vol_bottomup, vol_meta e vol_final ao mesmo tempo
            # =========================================================================
            db.execute(text("""
                UPDATE fato_ibp_granular 
                SET vol_bottomup = vol_topdown, 
                    vol_meta = vol_topdown,
                    vol_final = vol_topdown
                WHERE ciclo_sop = :ciclo
            """), {"ciclo": ciclo})
            
            # Registro de Auditoria Nativo do Sistema para Compliance
            registrar_log_auditoria(
                db, 
                usuario_id=usuario.get('id', 1), 
                acao="Finalizar Etapa", 
                detalhe=f"Diretoria trancou o ciclo {ciclo}. vol_topdown replicado para vol_bottomup, vol_meta e vol_final."
            )
            
        db.commit()
        return {"msg": "Decisões estratégicas salvas com sucesso!"}
        
    except Exception as e:
        db.rollback()
        print(f"❌ ERRO OPERACIONAL AO GRAVAR MATRIZ: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))