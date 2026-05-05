from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from dateutil.relativedelta import relativedelta
from app.core.database import get_db
from app.models.domain_models import DimProduto, FatoIbpGranular
from app.api.routers.router_auth import get_current_user

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
            db.commit()

        clientes_espelho = db.query(FatoIbpGranular.cgc, FatoIbpGranular.vendedor_nome, func.sum(FatoIbpGranular.vol_ia).label('peso_hist'))\
            .filter(FatoIbpGranular.sku == payload.sku_espelho)\
            .group_by(FatoIbpGranular.cgc, FatoIbpGranular.vendedor_nome).all()
            
        if not clientes_espelho:
            raise HTTPException(status_code=400, detail="O SKU Espelho selecionado não possui clientes ativos para clonagem.")

        total_peso_espelho = sum([c.peso_hist for c in clientes_espelho])
        hoje = date.today()
        mes_base_dinamico = hoje.replace(day=1) + relativedelta(months=2) 
        
        novas_linhas = []
        for i, proj in enumerate(payload.projecao):
            mes_alvo = mes_base_dinamico + relativedelta(months=i)
            volume_mes_nacional = proj.volume
            
            for cliente in clientes_espelho:
                peso_cliente = cliente.peso_hist / total_peso_espelho if total_peso_espelho > 0 else 1 / len(clientes_espelho)
                volume_cliente = int(round(volume_mes_nacional * peso_cliente))
                if volume_cliente == 0: continue
                    
                nova_fato = FatoIbpGranular(
                    ciclo_sop=date.today().strftime("%m/%Y"), # CORREÇÃO DO ERRO AQUI!
                    mes_projetado=mes_alvo,
                    sku=payload.codigo_lancamento,
                    cgc=cliente.cgc,
                    vendedor_nome=cliente.vendedor_nome,
                    vol_ia=volume_cliente, 
                    vol_topdown=volume_cliente, # Adequado à nova regra de Cascata
                    vol_bottomup=volume_cliente, # Adequado à nova regra de Cascata
                    vol_final=volume_cliente,    # Adequado à nova regra de Cascata
                    pmv_aplicado=payload.pmv
                )
                novas_linhas.append(nova_fato)

        db.bulk_save_objects(novas_linhas)
        db.commit()
        return {"status": "success", "message": "NPD injetado com sucesso na janela de S&OP."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))