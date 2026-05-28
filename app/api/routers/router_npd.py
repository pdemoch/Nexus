import datetime
from typing import List
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular
from app.api.routers.router_auth import get_current_user
from app.api.routers.shared_ibp import get_current_cycle

router = APIRouter(prefix="/api/v1/npd", tags=["New Product Development"])

class ProjecaoMes(BaseModel):
    mes: str
    percentual: int
    volume: int
    receita: float

class PayloadNPD(BaseModel):
    codigo_lancamento: str
    nome_lancamento: str
    sku_espelho: str
    categoria: str
    segmento: str
    pmv: float
    baseline: int
    projecao: List[ProjecaoMes]

@router.get("/espelhos")
async def obter_listas_npd(db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] not in ['Administrador', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito à Diretoria/Gerência.")
        
    produtos = db.query(DimProduto.sku, DimProduto.descricao).filter(DimProduto.descricao.isnot(None)).distinct().all()
    categorias = db.query(DimProduto.categoria).filter(DimProduto.categoria.isnot(None)).distinct().all()
    segmentos = db.query(DimProduto.segmento).filter(DimProduto.segmento.isnot(None)).distinct().all()
    
    return {
        "status": "success",
        "espelhos": [{"produto": p[0], "descricao": p[1]} for p in produtos],
        "categorias": sorted([c[0] for c in categorias if c[0]]),
        "segmentos": sorted([s[0] for s in segmentos if s[0]])
    }

@router.post("/injetar")
async def injetar_lancamento(payload: PayloadNPD, db: Session = Depends(get_db), usuario_logado: dict = Depends(get_current_user)):
    if usuario_logado['funcao'] not in ['Administrador', 'Marketing']:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
        
    try:
        # 1. TRATA A DIMENSÃO DO PRODUTO (Garante que o cadastro existe)
        produto_existente = db.query(DimProduto).filter(DimProduto.sku == payload.codigo_lancamento).first()
        if not produto_existente:
            novo_produto = DimProduto(
                sku=payload.codigo_lancamento,
                descricao=payload.nome_lancamento,
                categoria=payload.categoria,
                segmento=payload.segmento,
                curva="LANÇAMENTO"
            )
            db.add(novo_produto)
            db.flush() 

        ciclo_oficial = get_current_cycle(db)

        # 2. LIMPEZA PRÉVIA TÁTICA (O FIM DO ERRO DE CHAVE DUPLICADA)
        # Se você re-injetar o SKU, o sistema limpa a projeção anterior deste ciclo e aplica a nova por cima.
        db.query(FatoIbpGranular).filter(
            FatoIbpGranular.ciclo_sop == ciclo_oficial,
            FatoIbpGranular.sku == payload.codigo_lancamento
        ).delete()
        db.flush()

        # 3. BLINDAGEM DO AGRUPAMENTO (RATEIO EXATO POR CNPJ)
        clientes_espelho = db.query(
            FatoIbpGranular.cgc, 
            func.max(FatoIbpGranular.vendedor_nome).label('vendedor_nome'), 
            func.sum(func.coalesce(FatoIbpGranular.vol_ia, 0)).label('peso_hist')
        ).filter(FatoIbpGranular.sku == payload.sku_espelho)\
         .group_by(FatoIbpGranular.cgc).all()
            
        if not clientes_espelho:
            raise HTTPException(status_code=400, detail="O SKU Espelho selecionado não possui clientes com histórico no banco.")

        total_peso_espelho = sum([float(c.peso_hist or 0) for c in clientes_espelho])
        
        hoje = datetime.date.today()
        mes_base_dinamico = hoje.replace(day=1) + relativedelta(months=2) 
        
        novas_linhas = []
        for i, proj in enumerate(payload.projecao):
            mes_alvo = mes_base_dinamico + relativedelta(months=i)
            volume_mes_nacional = proj.volume
            
            for cliente in clientes_espelho:
                peso_atual = float(cliente.peso_hist or 0)
                peso_cliente = (peso_atual / total_peso_espelho) if total_peso_espelho > 0 else (1.0 / len(clientes_espelho))
                
                volume_cliente = int(round(volume_mes_nacional * peso_cliente))
                if volume_cliente == 0: continue
                    
                # Insere o dado em TODAS as camadas simultaneamente (IA, Comercial e Supply) 
                # para que o NPD flua na árvore como um produto consolidado.+
                nova_fato = FatoIbpGranular(
                    ciclo_sop=ciclo_oficial, 
                    mes_projetado=mes_alvo,
                    sku=payload.codigo_lancamento,
                    cgc=cliente.cgc,
                    vendedor_nome=cliente.vendedor_nome,
                    vol_ia=volume_cliente, 
                    vol_topdown=volume_cliente,
                    vol_bottomup=volume_cliente,
                    vol_supply=volume_cliente,
                    vol_meta=volume_cliente,
                    vol_final=volume_cliente,
                    pmv_aplicado=payload.pmv
                )
                novas_linhas.append(nova_fato)

        if not novas_linhas:
            raise HTTPException(status_code=400, detail="O volume é tão baixo que não gerou caixas inteiras para ratear.")

        db.bulk_save_objects(novas_linhas)
        db.commit()
        
        return {"status": "success", "message": "NPD injetado com sucesso na janela de S&OP."}
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro Crítico NPD: {repr(e)}")