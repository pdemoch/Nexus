import datetime
from typing import List, Optional
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.api.routers.router_auth import get_current_user
from app.models.domain_models import FatoIbpGranular

# Funções utilitárias mantidas do ecossistema S&OP
from app.api.routers.shared_ibp import (
    get_current_cycle,
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
    """Garante que apenas perfis macro acedem a esta tela."""
    if usuario.get('funcao') not in ['Administrador', 'Diretoria', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria e Administradores.")
    return usuario

@router.get("/status")
def obter_status_topdown(ciclo: Optional[str] = None, db: Session = Depends(get_db)):
    """Informa o React se os botões devem nascer bloqueados ou abertos."""
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
    """Gera a árvore de SKUs e a Timeline para os Gráficos."""
    nome_usuario = usuario.get('nome') or usuario.get('username') or "Usuário"
    
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)
            
        print(f"\n🧭 [TOP-DOWN] Utilizador Executivo '{nome_usuario}' acedeu ao ciclo {ciclo}.")
        
        ciclo_date = datetime.datetime.strptime(ciclo, "%m/%Y")
        hoje = datetime.date.today()
        
        # 1. ESTADO DA TRAVA
        query_trava = text("SELECT status FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'Top-Down Arena'")
        resultado_trava = db.execute(query_trava, {"ciclo": ciclo}).fetchone()
        ciclo_fechado = (resultado_trava is not None and resultado_trava[0] == 'Fechado')

        # 2. HISTÓRICO PARA A TIMELINE DO GRÁFICO
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

        # 3. EXTRAÇÃO DA MATRIX DE FUTURO (AGORA COM DIM_PRODUTOS NO PLURAL E DESCRIÇÃO)
        query_matriz = text("""
            SELECT 
                f.sku,
                p.descricao,
                p.categoria,
                p.segmento,
                f.mes_projetado,
                SUM(COALESCE(f.vol_topdown, 0)) as vol_td,
                SUM(COALESCE(f.vol_ia, 0)) as vol_ia,
                AVG(COALESCE(f.pmv_aplicado, 0)) as pmv_medio,
                SUM(COALESCE(f.vol_meta, 0)) as vol_anterior
            FROM fato_ibp_granular f
            LEFT JOIN dim_produtos p ON f.sku = p.sku
            WHERE f.ciclo_sop = :ciclo
            GROUP BY f.sku, p.descricao, p.categoria, p.segmento, f.mes_projetado
        """)

        dados_banco = db.execute(query_matriz, {"ciclo": ciclo}).fetchall()
        
        # Estrutura preparada para receber a descrição do SKU
        hierarquia = defaultdict(lambda: {"segmentos": defaultdict(lambda: {"skus": defaultdict(lambda: {"descricao": "", "meses": {}})})})
        meses_dinamicos = set()

        for row in dados_banco:
            sku = row[0]
            descricao = row[1] or "Sem Descrição"
            cat = row[2] or "SEM CATEGORIA"
            seg = row[3] or "SEM SEGMENTO"
            mes_date = row[4]
            
            if isinstance(mes_date, str):
                mes_date = datetime.datetime.strptime(mes_date, "%Y-%m-%d").date()
            
            mes_str = mes_date.strftime("%Y-%m")
            meses_dinamicos.add(mes_str)

            vol_td = float(row[5] or 0)
            vol_ia = float(row[6] or 0)
            pmv = float(row[7] or 0)
            vol_ant = float(row[8] or 0)

            # Acumuladores do Gráfico
            calendario[mes_str]["IA"] += vol_ia
            calendario[mes_str]["TopDown"] += vol_td
            calendario[mes_str]["CicloAnterior"] += vol_ant

            # Variáveis da Tabela
            fat_td = vol_td * pmv
            orc_fake = vol_ia * pmv * 1.05 

            # Injetando dados na árvore
            hierarquia[cat]["segmentos"][seg]["skus"][sku]["descricao"] = descricao
            hierarquia[cat]["segmentos"][seg]["skus"][sku]["meses"][mes_str] = {
                "vol": vol_td,
                "fat": fat_td,
                "ia": vol_ia,
                "pmv": pmv,
                "orc": orc_fake
            }

        # 4. FORMATAÇÃO DA RESPOSTA
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

        dados_arvore = []
        for cat_nome, cat_data in hierarquia.items():
            segmentos_list = []
            for seg_nome, seg_data in cat_data["segmentos"].items():
                # Formatação final dos SKUs com a Descrição incluída
                skus_list = [
                    {"sku": k, "descricao": v["descricao"], "meses": v["meses"]} 
                    for k, v in seg_data["skus"].items()
                ]
                segmentos_list.append({"segmento": seg_nome, "skus": skus_list})
            dados_arvore.append({"categoria": cat_nome, "segmentos": segmentos_list})

        return {
            "isLocked": ciclo_fechado,
            "mesesColunas": sorted(list(meses_dinamicos)),
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
    nome_usuario = usuario.get('nome') or usuario.get('username') or "Usuário"
    print(f"\n💾 [TOP-DOWN] {nome_usuario} submeteu {len(payload.ajustes)} edições.")
    
    try:
        if not ciclo:
            ciclo = get_current_cycle(db)

        # 1. Trava de Segurança
        query_trava = text("SELECT status FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'Top-Down Arena'")
        trava = db.execute(query_trava, {"ciclo": ciclo}).fetchone()
        if trava and trava[0] == 'Fechado':
            raise HTTPException(status_code=400, detail="Este ciclo já se encontra encerrado.")

        # =====================================================================
        # ⚙️ 2. MOTOR DE RATEIO INTELIGENTE (Histórico -> NPI -> Maior Resto)
        # =====================================================================
        print("   -> Executando Motor de Rateio...")
        updates_para_banco = []

        for aj in payload.ajustes:
            mes_str_banco = f"{aj.mes_projetado}-01"
            novo_volume_macro = aj.novo_volume
            sku = aj.sku
            
            query_linhas = text("""
                WITH Historico AS (
                    SELECT cgc, SUM(COALESCE(qt_pedido, 0)) as vol_hist
                    FROM fato_vendas
                    WHERE sku = :s 
                      AND data_pedido >= CURRENT_DATE - INTERVAL '6 months'
                    GROUP BY cgc
                )
                SELECT 
                    f.id, 
                    COALESCE(h.vol_hist, 0) as vol_hist,
                    COALESCE(f.vol_ia, 0) as vol_ia
                FROM fato_ibp_granular f
                LEFT JOIN Historico h ON f.cgc = h.cgc
                WHERE f.ciclo_sop = :c 
                  AND f.sku = :s 
                  AND f.mes_projetado = :m
            """)
            
            linhas_sku = db.execute(query_linhas, {"c": ciclo, "s": sku, "m": mes_str_banco}).fetchall()
            
            if not linhas_sku:
                continue 
            
            total_hist = sum(r[1] for r in linhas_sku)
            total_ia = sum(r[2] for r in linhas_sku)
            
            soma_alocada = 0
            fracoes = []

            # TIER 1: Histórico
            if total_hist > 0:
                for r in linhas_sku:
                    peso = r[1] / total_hist
                    cota = peso * novo_volume_macro
                    cota_inteira = int(cota)
                    soma_alocada += cota_inteira
                    fracoes.append({"id": r[0], "vol": cota_inteira, "resto": cota - cota_inteira})
                    
            # TIER 2: IA (Lançamentos / NPI)
            elif total_ia > 0:
                for r in linhas_sku:
                    peso = r[2] / total_ia
                    cota = peso * novo_volume_macro
                    cota_inteira = int(cota)
                    soma_alocada += cota_inteira
                    fracoes.append({"id": r[0], "vol": cota_inteira, "resto": cota - cota_inteira})
                    
            # TIER 3: Divisão Igualitária (Fallback)
            else:
                qtd_clientes = len(linhas_sku)
                cota = novo_volume_macro / qtd_clientes
                cota_inteira = int(cota)
                for r in linhas_sku:
                    soma_alocada += cota_inteira
                    fracoes.append({"id": r[0], "vol": cota_inteira, "resto": cota - cota_inteira})

            # TIER 4: Maior Resto (Para não perder 1 caixa no arredondamento)
            faltam = novo_volume_macro - soma_alocada
            fracoes.sort(key=lambda x: x["resto"], reverse=True) 

            for i in range(faltam):
                if i < len(fracoes):
                    fracoes[i]["vol"] += 1 

            for f in fracoes:
                updates_para_banco.append({
                    "id": f["id"],
                    "vol_topdown": f["vol"]
                })

        # =====================================================================
        # 3. GRAVAÇÃO ATÓMICA
        # =====================================================================
        if updates_para_banco:
            db.bulk_update_mappings(FatoIbpGranular, updates_para_banco)
            print(f"   -> {len(updates_para_banco)} CNPJs rateados e atualizados.")

        # 4. FINALIZAÇÃO / CASCATA
        if payload.finalizar_etapa:
            print("🔒 [TOP-DOWN] Selando a etapa...")
            
            db.execute(text("DELETE FROM controle_ciclos WHERE ciclo_sop = :ciclo AND origem = 'Top-Down Arena'"), {"ciclo": ciclo})
            
            db.execute(text("""
                INSERT INTO controle_ciclos (ciclo_sop, origem, status, data_fechamento) 
                VALUES (:ciclo, 'Top-Down Arena', 'Fechado', CURRENT_TIMESTAMP)
            """), {"ciclo": ciclo})
            
            db.execute(text("""
                UPDATE fato_ibp_granular 
                SET vol_bottomup = vol_topdown, 
                    vol_meta = vol_topdown,
                    vol_final = vol_topdown
                WHERE ciclo_sop = :ciclo
            """), {"ciclo": ciclo})
            
            registrar_log_auditoria(db, usuario.get('id', 1), "Finalizar Etapa", f"Ciclo {ciclo} trancado. Rateio OK.")
            
        db.commit()
        return {"msg": "Salvo com sucesso!"}
        
    except Exception as e:
        db.rollback()
        print(f"❌ ERRO AO GRAVAR MATRIZ: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))