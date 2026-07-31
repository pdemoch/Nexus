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
from app.api.routers.shared_ibp import (
    get_current_cycle,
    get_projection_window,
    ratear_maior_resto,
    check_imutabilidade_mes,
)

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
    """
    Injeta um lancamento (NPD) na janela do ciclo, rateado por CNPJ conforme o
    SKU espelho, gravando as 6 camadas de uma vez (o produto novo nasce
    consolidado, igual ao nascimento de ciclo pelo distributor).

    Correcoes:
      - Rateio canonico (ratear_maior_resto): a soma dos CNPJs e EXATAMENTE o
        volume nacional digitado. Antes 'int(round())' + 'if 0: continue'
        perdia clientes de peso baixo e a soma nao fechava o volume do mes.
      - Zero e gravado explicitamente (nao pula), preservando a linha do cliente.
      - Meses vem do RELOGIO DO CICLO (get_projection_window), nao de
        datetime.today(). Antes o mes-base dependia do dia real de execucao, e
        o lancamento podia cair em meses diferentes dos que a etapa planeja.
      - Imutabilidade: nao injeta em mes ja realizado.
    """
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

        # Meses-alvo pelo relogio do ciclo (M2, M3, M4 do ciclo ativo), nao pelo
        # dia real. get_projection_window devolve (data_ini_M2, data_fim_M4).
        data_ini_m2, _data_fim_m4 = get_projection_window(db)

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

        pesos = [max(0.0, float(c.peso_hist or 0)) for c in clientes_espelho]

        novas_linhas = []
        for i, proj in enumerate(payload.projecao):
            mes_alvo = data_ini_m2 + relativedelta(months=i)

            # Nao injeta em mes ja realizado.
            check_imutabilidade_mes(mes_alvo, payload.codigo_lancamento, contexto="NPD")

            volume_mes_nacional = int(proj.volume)

            # Rateio canonico: a soma por CNPJ e EXATAMENTE o volume nacional.
            partes = ratear_maior_resto(volume_mes_nacional, pesos)

            for cliente, volume_cliente in zip(clientes_espelho, partes):
                # Grava zero explicito tambem: mantem a linha do cliente no plano,
                # coerente com o restante do sistema (zerar e zerar, nao pular).
                nova_fato = FatoIbpGranular(
                    ciclo_sop=ciclo_oficial, 
                    mes_projetado=mes_alvo,
                    sku=payload.codigo_lancamento,
                    cgc=cliente.cgc,
                    vendedor_nome=cliente.vendedor_nome,
                    vol_ia=int(volume_cliente), 
                    vol_topdown=int(volume_cliente),
                    vol_bottomup=int(volume_cliente),
                    vol_supply=int(volume_cliente),
                    vol_meta=int(volume_cliente),
                    vol_final=int(volume_cliente),
                    pmv_aplicado=payload.pmv
                )
                novas_linhas.append(nova_fato)

        if not novas_linhas:
            raise HTTPException(status_code=400, detail="Nenhum cliente no espelho para ratear o lançamento.")

        db.bulk_save_objects(novas_linhas)
        db.commit()
        
        return {"status": "success", "message": "NPD injetado com sucesso na janela de S&OP."}
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro Crítico NPD: {repr(e)}")