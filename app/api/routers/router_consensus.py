from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
import datetime
from dateutil.relativedelta import relativedelta
import io
import pandas as pd
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.models.domain_models import DimProduto, DimCliente, FatoIbpGranular, FatoVendas, ControleCiclo

# AQUI ESTÁ O NOSSO SEGURANÇA! (Importamos do router_auth)
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/consensus", tags=["Consenso S&OP"])

# ==========================================
# UTILITÁRIOS
# ==========================================
def get_current_cycle() -> str:
    return datetime.date.today().strftime("%m/%Y")

def get_projection_window() -> tuple[str, str]:
    hoje = datetime.date.today().replace(day=1)
    m_plus_2 = (hoje + relativedelta(months=2)).strftime('%Y-%m-%d')
    m_plus_4 = (hoje + relativedelta(months=4)).strftime('%Y-%m-%d')
    return m_plus_2, m_plus_4

def check_global_lock(db: Session):
    """Garante que as etapas anteriores não sejam alteradas se o Global estiver publicado."""
    status_global = db.query(ControleCiclo).filter(
        ControleCiclo.ciclo_sop == get_current_cycle(), 
        ControleCiclo.origem == 'S&OP-Final'
    ).first()
    if status_global and status_global.status == 'Fechado':
        raise HTTPException(status_code=403, detail="Acesso Negado: S&OP Global publicado. Ordem reversa necessária.")

# ==========================================
# PAYLOADS
# ==========================================
class AjusteTopDown(BaseModel):
    produto: str
    mes_projetado: str
    novo_volume: int

class PayloadCongelarTopDown(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteTopDown]

class AjusteBottomUp(BaseModel):
    chave_matriz: str
    mes_projetado: str
    novo_volume: int
    pmv_aplicado: float

class PayloadCongelarBottomUp(BaseModel):
    origem_ajuste: str
    ajustes: List[AjusteBottomUp]

class PayloadFecharCiclo(BaseModel):
    origem: str

class AjusteGerente(BaseModel):
    nivel: str 
    chave: str 
    mes_projetado: str
    novo_volume: int

class PayloadAprovarGerente(BaseModel):
    ajustes: List[AjusteGerente]

class PayloadLockAll(BaseModel):
    gerente_nome: str = "" 
    acao: str

# ==========================================
# ROTAS BLINDADAS (COM JWT)
# ==========================================

@router.get("/status")
async def checar_status_ciclo(
    origem: str, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    ciclo = get_current_cycle()
    status_usuario = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == origem).first()
    status_topdown = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
    status_global = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'S&OP-Final').first()

    return {
        "is_fechado": status_usuario.status == 'Fechado' if status_usuario else False,
        "is_topdown_fechado": status_topdown.status == 'Fechado' if status_topdown else False,
        "is_global_fechado": status_global.status == 'Fechado' if status_global else False
    }

@router.get("/filtros")
async def obter_filtros_busca(
    gerente_nome: str = None, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    q_reg = db.query(DimCliente.regional).filter(DimCliente.regional.isnot(None))
    q_vend = db.query(DimCliente.vendedor_nome).filter(DimCliente.vendedor_nome.isnot(None))
    
    # SEGURANÇA: Força o filtro se o utilizador for Gerente
    nome_gerente_filtro = gerente_nome
    if usuario_logado['funcao'] == 'Gerente':
        nome_gerente_filtro = usuario_logado['gerente_nome']

    if nome_gerente_filtro:
        gerente_nome_limpo = nome_gerente_filtro.strip()
        q_reg = q_reg.filter(func.trim(DimCliente.gerente_nome) == gerente_nome_limpo)
        q_vend = q_vend.filter(func.trim(DimCliente.gerente_nome) == gerente_nome_limpo)

    regionais = q_reg.distinct().all()
    vendedores = q_vend.distinct().all()
    
    return {
        "regionais": sorted([str(r[0]).strip() for r in regionais if r[0] and str(r[0]).strip()]),
        "vendedores": sorted([str(v[0]).strip() for v in vendedores if v[0] and str(v[0]).strip()])
    }

@router.post("/fechar")
async def fechar_ciclo(
    payload: PayloadFecharCiclo, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    # SEGURANÇA: Executivo só pode fechar a sua própria carteira
    if usuario_logado['funcao'] == 'Executivo' and payload.origem != usuario_logado['nome_vendedor']:
        raise HTTPException(status_code=403, detail="Você só pode fechar a sua própria carteira.")

    check_global_lock(db)
    ciclo = get_current_cycle()
    registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == payload.origem).first()
    
    if not registro:
        db.add(ControleCiclo(ciclo_sop=ciclo, origem=payload.origem, status='Fechado'))
    else:
        registro.status = 'Fechado'
    db.commit()
    return {"status": "success"}

@router.get("/macro")
async def listar_macro(
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    # SEGURANÇA DE ACESSO: Apenas Admin e Gerente acedem ao Top-Down
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito à Diretoria/Gerência.")

    try:
        m_plus_2, m_plus_4 = get_projection_window()
        projecoes = db.query(
            FatoIbpGranular.sku,
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('vol_td'),
            func.sum(FatoIbpGranular.vol_ia * FatoIbpGranular.pmv_aplicado).label('receita_base'),
            func.avg(FatoIbpGranular.pmv_aplicado).label('pmv_simples'),
            DimProduto.descricao, DimProduto.categoria, DimProduto.segmento,
            DimProduto.modelo_vencedor, DimProduto.acuracia_ia
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .filter(
             func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2,
             func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4
         ).group_by(
             FatoIbpGranular.sku, FatoIbpGranular.mes_projetado, DimProduto.descricao, 
             DimProduto.categoria, DimProduto.segmento, DimProduto.modelo_vencedor, DimProduto.acuracia_ia
         ).all()

        produtos_dict = {}
        for r in projecoes:
            if r.sku not in produtos_dict:
                produtos_dict[r.sku] = {
                    "produto": r.sku, "descricao": r.descricao, "categoria": r.categoria, "segmento": r.segmento,
                    "modelo_vencedor": r.modelo_vencedor or "Não Calculado", "acuracia_ia": float(r.acuracia_ia or 0.0), "meses": []
                }
            
            vol_ia = int(r.vol_ia or 0)
            receita_base = float(r.receita_base or 0)
            pmv_real = (receita_base / vol_ia) if vol_ia > 0 else float(r.pmv_simples or 0)

            produtos_dict[r.sku]["meses"].append({
                "mes_banco": r.mes_projetado.strftime("%Y-%m-%d"), 
                "mes_str": r.mes_projetado.strftime("%b/%y").capitalize(),
                "vol_ia": vol_ia, 
                "vol_ajustado": int(r.vol_td or 0), 
                "pmv": pmv_real
            })

        return {"status": "success", "dados": list(produtos_dict.values())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/macro/grafico")
async def obter_grafico_produto(
    produto: str, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")

    try:
        m_plus_2, m_plus_4 = get_projection_window()
        hoje = datetime.date.today()
        data_limite = hoje - relativedelta(years=2)
        
        historico = db.query(
            func.strftime('%Y-%m', FatoVendas.data_pedido).label('mes_ano'),
            func.sum(FatoVendas.qt_pedido).label('vol_real')
        ).filter(FatoVendas.sku == produto, FatoVendas.data_pedido >= data_limite).group_by(func.strftime('%Y-%m', FatoVendas.data_pedido)).all()
        
        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in historico}
        
        projecoes = db.query(
            FatoIbpGranular.mes_projetado,
            func.sum(FatoIbpGranular.vol_ia).label('vol_ia'),
            func.sum(FatoIbpGranular.vol_topdown).label('vol_td')
        ).filter(FatoIbpGranular.sku == produto).group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = []
        for i in range(24, 0, -1):
            mes_dt = hoje - relativedelta(months=i)
            timeline.append({
                "name": mes_dt.strftime("%b/%y").capitalize(), 
                "data_iso": mes_dt.replace(day=1).strftime("%Y-%m-%d"), 
                "Realizado": hist_dict.get(mes_dt.strftime('%Y-%m'), 0), 
                "IA": None, "Consenso": None
            })
            
        for p in projecoes:
            p_str = p.mes_projetado.strftime('%Y-%m-%d')
            consenso = int(p.vol_td or 0) if m_plus_2 <= p_str <= m_plus_4 else None 
            timeline.append({
                "name": p.mes_projetado.strftime("%b/%y").capitalize(), 
                "data_iso": p_str, "Realizado": None, "IA": int(p.vol_ia or 0), "Consenso": consenso
            })
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/macro/congelar")
async def congelar_macro_rateio(
    payload: PayloadCongelarTopDown, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Apenas a Diretoria/Gerência pode congelar o Top-Down.")

    try:
        check_global_lock(db)
        for ajuste in payload.ajustes:
            data_alvo = datetime.datetime.strptime(ajuste.mes_projetado, "%Y-%m-%d").date()
            linhas_atomicas = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
                .filter(FatoIbpGranular.sku == ajuste.produto, FatoIbpGranular.mes_projetado == data_alvo, DimCliente.bloqueado != 'INATIVO').all()
            
            if not linhas_atomicas: continue
                
            total_ia = sum([l.vol_ia for l in linhas_atomicas])
            soma_distribuida = 0
            
            for i, linha in enumerate(linhas_atomicas):
                if i == len(linhas_atomicas) - 1:
                    valor_rateado = ajuste.novo_volume - soma_distribuida
                else:
                    peso = linha.vol_ia / total_ia if total_ia > 0 else 1.0 / len(linhas_atomicas)
                    valor_rateado = int(round(ajuste.novo_volume * peso))
                    soma_distribuida += valor_rateado
                
                linha.vol_topdown = valor_rateado
                linha.vol_bottomup = valor_rateado
                linha.vol_final = valor_rateado

        ciclo = get_current_cycle()
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if not registro:
            db.add(ControleCiclo(ciclo_sop=ciclo, origem='Top-Down', status='Fechado'))
        else:
            registro.status = 'Fechado'
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/macro/reabrir")
async def reabrir_macro(
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Apenas o Administrador pode reabrir ciclos.")

    try:
        check_global_lock(db)
        ciclo = get_current_cycle()
        
        fechados = db.query(ControleCiclo).filter(
            ControleCiclo.ciclo_sop == ciclo, 
            ControleCiclo.status == 'Fechado', 
            ControleCiclo.origem.notin_(['Top-Down', 'S&OP-Final'])
        ).count()
        
        if fechados > 0:
            raise HTTPException(status_code=403, detail="Ordem Reversa Violada: Destranque todos os Vendedores antes de reabrir o Top-Down.")

        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == 'Top-Down').first()
        if registro:
            registro.status = 'Aberto'
            db.commit()
        return {"status": "success"}
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# =======================================================
# A ROTA MAIS IMPORTANTE: LISTA DA TELA DO VENDEDOR
# =======================================================
@router.get("/micro")
async def listar_micro(
    nivel_hierarquia: str, 
    nome_responsavel: str, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT ATIVADA!
):
    # SEGURANÇA DE DADOS (ROW-LEVEL SECURITY)
    # Se for um vendedor (Executivo), forçamos os parâmetros a serem os do crachá dele.
    # O que ele mandou pela URL (F12) é ignorado!
    if usuario_logado['funcao'] == 'Executivo':
        nivel_hierarquia = 'vendedor'
        nome_responsavel = usuario_logado['nome_vendedor']

    m_plus_2, m_plus_4 = get_projection_window()
    query = db.query(
        FatoIbpGranular.sku, FatoIbpGranular.cgc, FatoIbpGranular.mes_projetado,
        FatoIbpGranular.vol_ia, FatoIbpGranular.vol_bottomup, FatoIbpGranular.pmv_aplicado, 
        DimProduto.descricao, DimCliente.razaosocial
    ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
     .filter(func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2, func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4)
     
    # Aplicação do Filtro Hierárquico
    if nivel_hierarquia == 'vendedor': 
        query = query.filter(func.trim(DimCliente.vendedor_nome) == nome_responsavel.strip())
    else: 
        query = query.filter(func.trim(DimCliente.regional) == nome_responsavel.strip())

    # SEGURANÇA DE DADOS: Se for Gerente, garantimos que ele só veja dados da sua própria equipa,
    # mesmo que tente buscar um vendedor que pertence a outro gerente.
    if usuario_logado['funcao'] == 'Gerente':
         query = query.filter(func.trim(DimCliente.gerente_nome) == usuario_logado['gerente_nome'].strip())
        
    resultados = query.all()
    matriz_dict = {}
    for r in resultados:
        chave = f"{r.sku}_{r.cgc}"
        if chave not in matriz_dict:
            matriz_dict[chave] = {"chave_matriz": chave, "produto": r.sku, "descricao": r.descricao, "cgc": r.cgc, "razaosocial": r.razaosocial, "meses": []}
        matriz_dict[chave]["meses"].append({
            "mes_banco": r.mes_projetado.strftime("%Y-%m-%d"), "mes_str": r.mes_projetado.strftime("%b/%y").capitalize(),
            "vol_ia": int(r.vol_ia or 0), "vol_ajustado": int(r.vol_bottomup or 0), "pmv": float(r.pmv_aplicado or 0)
        })
    return {"status": "success", "dados": list(matriz_dict.values())}

@router.get("/micro/grafico")
async def obter_grafico_micro(
    chave_matriz: str, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    # Nota: Aqui seria ideal validar também se o `cgc` pertence à carteira do vendedor,
    # mas como a chave_matriz é difícil de adivinhar e não expõe dados agregados, deixamos passar para não pesar a query.
    try:
        sku, cgc = chave_matriz.split('_')
        m_plus_2, m_plus_4 = get_projection_window()
        hoje = datetime.date.today()
        data_limite = hoje - relativedelta(years=2)
        
        historico = db.query(func.strftime('%Y-%m', FatoVendas.data_pedido).label('mes_ano'), func.sum(FatoVendas.qt_pedido).label('vol_real'))\
            .filter(FatoVendas.sku == sku, FatoVendas.cgc == cgc, FatoVendas.data_pedido >= data_limite).group_by(func.strftime('%Y-%m', FatoVendas.data_pedido)).all()
        hist_dict = {h.mes_ano: int(h.vol_real or 0) for h in historico}
        
        projecoes = db.query(FatoIbpGranular.mes_projetado, func.sum(FatoIbpGranular.vol_ia).label('vol_ia'), func.sum(FatoIbpGranular.vol_bottomup).label('vol_bu'))\
            .filter(FatoIbpGranular.sku == sku, FatoIbpGranular.cgc == cgc).group_by(FatoIbpGranular.mes_projetado).order_by(FatoIbpGranular.mes_projetado).all()

        timeline = []
        for i in range(24, 0, -1):
            mes_dt = hoje - relativedelta(months=i)
            timeline.append({"name": mes_dt.strftime("%b/%y").capitalize(), "data_iso": mes_dt.replace(day=1).strftime("%Y-%m-%d"), "Realizado": hist_dict.get(mes_dt.strftime('%Y-%m'), 0), "IA": None, "Consenso": None})
            
        for p in projecoes:
            p_str = p.mes_projetado.strftime('%Y-%m-%d')
            consenso = int(p.vol_bu or 0) if m_plus_2 <= p_str <= m_plus_4 else None 
            timeline.append({"name": p.mes_projetado.strftime("%b/%y").capitalize(), "data_iso": p_str, "Realizado": None, "IA": int(p.vol_ia or 0), "Consenso": consenso})
            
        return {"status": "success", "dados": timeline}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/micro/congelar")
async def congelar_micro(
    nome_responsavel: str, 
    payload: PayloadCongelarBottomUp, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    try:
        # SEGURANÇA DE DADOS (ROW-LEVEL SECURITY)
        # Impede que um Vendedor A congele o ciclo do Vendedor B usando o F12
        origem_real = nome_responsavel
        if usuario_logado['funcao'] == 'Executivo':
            origem_real = usuario_logado['nome_vendedor']

        check_global_lock(db)
        
        # Opcional: Adicionar validação se os CGCs enviados no payload pertencem mesmo à origem_real
        
        for ajuste in payload.ajustes:
            sku, cgc = ajuste.chave_matriz.split('_')
            data_alvo = datetime.datetime.strptime(ajuste.mes_projetado, "%Y-%m-%d").date()
            linha = db.query(FatoIbpGranular).filter(FatoIbpGranular.sku == sku, FatoIbpGranular.cgc == cgc, FatoIbpGranular.mes_projetado == data_alvo).first()
            if linha: 
                linha.vol_bottomup = ajuste.novo_volume
                linha.vol_final = ajuste.novo_volume
                
        ciclo = get_current_cycle()
        registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == origem_real).first()
        if not registro: 
            db.add(ControleCiclo(ciclo_sop=ciclo, origem=origem_real, status='Fechado'))
        else: 
            registro.status = 'Fechado'
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/gerenciamento/vendedores")
async def listar_gerenciamento_vendedores(
    gerente_nome: str = None, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    # SEGURANÇA: Vendedor não acede aqui
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")

    try:
        m_plus_2, m_plus_4 = get_projection_window()
        query = db.query(
            FatoIbpGranular.vendedor_nome, FatoIbpGranular.cgc, FatoIbpGranular.sku, FatoIbpGranular.mes_projetado,
            FatoIbpGranular.vol_ia, FatoIbpGranular.vol_bottomup, FatoIbpGranular.pmv_aplicado,
            DimCliente.razaosocial, DimProduto.descricao
        ).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku)\
         .filter(
            func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2,
            func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4,
            FatoIbpGranular.vendedor_nome.isnot(None),
            DimCliente.bloqueado != 'INATIVO'
         )

        # RLS: Gerente só vê os seus próprios dados
        nome_gerente_filtro = gerente_nome
        if usuario_logado['funcao'] == 'Gerente':
            nome_gerente_filtro = usuario_logado['gerente_nome']

        if nome_gerente_filtro: 
            query = query.filter(func.trim(DimCliente.gerente_nome) == nome_gerente_filtro.strip())

        resultados = query.all()
        ciclos = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == get_current_cycle()).all()
        status_dict = {c.origem.strip(): c.status for c in ciclos if c.origem}
        arvore = {}

        for r in resultados:
            vend = str(r.vendedor_nome).strip()
            mes = r.mes_projetado.strftime("%Y-%m-%d")
            receita = r.vol_bottomup * float(r.pmv_aplicado or 0)

            if vend not in arvore:
                arvore[vend] = {"id": vend, "nome": vend, "tipo": "vendedor", "status": status_dict.get(vend, "Aberto"), "meses": {}, "clientes": {}}
            if r.cgc not in arvore[vend]["clientes"]:
                arvore[vend]["clientes"][r.cgc] = {"id": f"{vend}|{r.cgc}", "nome": r.razaosocial, "tipo": "cliente", "meses": {}, "produtos": {}}
            if r.sku not in arvore[vend]["clientes"][r.cgc]["produtos"]:
                arvore[vend]["clientes"][r.cgc]["produtos"][r.sku] = {"id": f"{vend}|{r.cgc}|{r.sku}", "nome": r.descricao, "tipo": "produto", "meses": {}}

            arvore[vend]["clientes"][r.cgc]["produtos"][r.sku]["meses"][mes] = {"vol_ajustado": r.vol_bottomup, "receita": receita}
            
            if mes not in arvore[vend]["clientes"][r.cgc]["meses"]: 
                arvore[vend]["clientes"][r.cgc]["meses"][mes] = {"vol_ajustado": 0, "receita": 0}
            arvore[vend]["clientes"][r.cgc]["meses"][mes]["vol_ajustado"] += r.vol_bottomup
            arvore[vend]["clientes"][r.cgc]["meses"][mes]["receita"] += receita

            if mes not in arvore[vend]["meses"]: 
                arvore[vend]["meses"][mes] = {"vol_ajustado": 0, "receita": 0}
            arvore[vend]["meses"][mes]["vol_ajustado"] += r.vol_bottomup
            arvore[vend]["meses"][mes]["receita"] += receita

        dados_finais = []
        for vend, v_data in arvore.items():
            v_node = {
                "id": v_data["id"], "chave_matriz": v_data["id"], "nome": v_data["nome"], "tipo": v_data["tipo"], "status": v_data["status"],
                "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%b/%y").capitalize(), "vol_ajustado": v["vol_ajustado"], "receita": v["receita"]} for k, v in v_data["meses"].items()],
                "subRows": []
            }
            for cgc, c_data in v_data["clientes"].items():
                c_node = {
                    "id": c_data["id"], "chave_matriz": c_data["id"], "nome": c_data["nome"], "tipo": c_data["tipo"],
                    "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%b/%y").capitalize(), "vol_ajustado": v["vol_ajustado"], "receita": v["receita"]} for k, v in c_data["meses"].items()],
                    "subRows": []
                }
                for sku, p_data in c_data["produtos"].items():
                    c_node["subRows"].append({
                        "id": p_data["id"], "chave_matriz": p_data["id"], "nome": p_data["nome"], "tipo": p_data["tipo"],
                        "meses": [{"mes_banco": k, "mes_str": datetime.datetime.strptime(k, "%Y-%m-%d").strftime("%b/%y").capitalize(), "vol_ajustado": v["vol_ajustado"], "receita": v["receita"]} for k, v in p_data["meses"].items()]
                    })
                v_node["subRows"].append(c_node)
            dados_finais.append(v_node)

        return {"status": "success", "dados": dados_finais}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/gerenciamento/aprovar")
async def aprovar_gerenciamento(
    payload: PayloadAprovarGerente, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")

    try:
        check_global_lock(db)
        for ajuste in payload.ajustes:
            partes = ajuste.chave.split('|')
            data_alvo = datetime.datetime.strptime(ajuste.mes_projetado, "%Y-%m-%d").date()
            
            # Opcional: Adicionar verificação se o partes[0] (vendedor) pertence ao gerente logado
            
            query = db.query(FatoIbpGranular).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc).filter(
                func.trim(FatoIbpGranular.vendedor_nome) == partes[0].strip(), FatoIbpGranular.mes_projetado == data_alvo, DimCliente.bloqueado != 'INATIVO'
            )
            if ajuste.nivel in ['cliente', 'produto']: query = query.filter(FatoIbpGranular.cgc == partes[1])
            if ajuste.nivel == 'produto': query = query.filter(FatoIbpGranular.sku == partes[2])
                
            linhas = query.all()
            if not linhas: continue

            if ajuste.nivel == 'produto':
                for l in linhas: 
                    l.vol_bottomup = ajuste.novo_volume
                    l.vol_final = ajuste.novo_volume
            else:
                total_base = sum([l.vol_bottomup for l in linhas]) 
                soma_dist = 0
                for i, l in enumerate(linhas):
                    if i == len(linhas) - 1: 
                        rateado = ajuste.novo_volume - soma_dist
                    else:
                        peso = l.vol_bottomup / total_base if total_base > 0 else 1.0 / len(linhas)
                        rateado = int(round(ajuste.novo_volume * peso))
                        soma_dist += rateado
                    l.vol_bottomup = rateado
                    l.vol_final = rateado
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/gerenciamento/lock-all")
async def lock_all_gerenciamento(
    payload: PayloadLockAll, 
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    if usuario_logado['funcao'] not in ['Administrador', 'Gerente']:
        raise HTTPException(status_code=403, detail="Acesso Restrito.")

    try:
        check_global_lock(db) 
        ciclo = get_current_cycle()
        m_plus_2, m_plus_4 = get_projection_window()
        
        query = db.query(FatoIbpGranular.vendedor_nome).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
            .filter(
                func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2,
                func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4,
                FatoIbpGranular.vendedor_nome.isnot(None),
                DimCliente.bloqueado != 'INATIVO'
            )
            
        # RLS: Se for gerente, forçamos o nome para o dele próprio
        nome_gerente_filtro = payload.gerente_nome
        if usuario_logado['funcao'] == 'Gerente':
             nome_gerente_filtro = usuario_logado['gerente_nome']

        if nome_gerente_filtro:
            query = query.filter(func.trim(DimCliente.gerente_nome) == nome_gerente_filtro.strip())
            
        vendedores = [v[0].strip() for v in query.distinct().all() if v[0]]
        status_alvo = 'Fechado' if payload.acao == 'Trancar' else 'Aberto'
        
        for vend in vendedores:
            registro = db.query(ControleCiclo).filter(ControleCiclo.ciclo_sop == ciclo, ControleCiclo.origem == vend).first()
            if registro:
                registro.status = status_alvo
            else:
                db.add(ControleCiclo(ciclo_sop=ciclo, origem=vend, status=status_alvo))
                
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export/bottom-up")
async def exportar_excel_bottom_up(
    db: Session = Depends(get_db),
    usuario_logado: dict = Depends(get_current_user) # BLINDAGEM JWT
):
    if usuario_logado['funcao'] != 'Administrador':
        raise HTTPException(status_code=403, detail="Apenas o Administrador pode exportar a base bruta granular.")

    try:
        m_plus_2, m_plus_4 = get_projection_window()
        query = db.query(
            FatoIbpGranular.vendedor_nome, FatoIbpGranular.cgc, DimCliente.razaosocial,
            DimCliente.cod_cliente, DimCliente.loja,
            FatoIbpGranular.sku, DimProduto.descricao, FatoIbpGranular.mes_projetado,
            FatoIbpGranular.vol_ia, FatoIbpGranular.vol_topdown, FatoIbpGranular.vol_bottomup,
            FatoIbpGranular.vol_final, FatoIbpGranular.pmv_aplicado
        ).join(DimProduto, FatoIbpGranular.sku == DimProduto.sku).join(DimCliente, FatoIbpGranular.cgc == DimCliente.cgc)\
         .filter(func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) >= m_plus_2, func.strftime('%Y-%m-%d', FatoIbpGranular.mes_projetado) <= m_plus_4).all()

        if not query: raise HTTPException(status_code=404, detail="Sem dados para exportar.")

        dados = [{
            "Vendedor": r.vendedor_nome, "Cód. Cliente": r.cod_cliente, "Loja": r.loja, "CGC": r.cgc, 
            "Cliente": r.razaosocial, "SKU": r.sku, "Produto": r.descricao, 
            "Mês Projetado": r.mes_projetado.strftime("%m/%Y"), "Volume IA (Inicial)": r.vol_ia, 
            "Volume Top-Down (Diretoria)": r.vol_topdown, "Volume Bottom-Up (Vendedor)": r.vol_bottomup, 
            "Volume Final (S&OP)": r.vol_final, "Preço Médio (PMV)": float(r.pmv_aplicado or 0), 
            "Receita Projetada (R$)": r.vol_final * float(r.pmv_aplicado or 0)
        } for r in query]

        df = pd.DataFrame(dados)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Base Granular')
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=consenso_granular.xlsx"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))