from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    funcao = Column(String(50), nullable=False) 
    
    nome_vendedor = Column(String(100), nullable=True) 
    gerente_nome = Column(String, nullable=True)
    supervisor_nome = Column(String(100), nullable=True)

    aprovado = Column(Boolean, default=False)
    primeiro_acesso = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
    ultima_atividade = Column(DateTime, default=datetime.utcnow)

class DimProduto(Base):
    __tablename__ = 'dim_produtos'
    
    sku = Column(String(50), primary_key=True)
    descricao = Column(String)
    bu = Column(String)
    categoria = Column(String, index=True)
    segmento = Column(String, index=True)
    curva = Column(String)
    modelo_vencedor = Column(String, nullable=True) 
    acuracia_ia = Column(Float, nullable=True)

    vendas = relationship("FatoVendas", back_populates="produto_rel")
    forecasts = relationship("FatoIbpGranular", back_populates="produto_rel")
    estoque = relationship("FatoEstoqueD0", back_populates="produto_rel")
    inbound = relationship("FatoInboundProducao", back_populates="produto_rel")

class DimCliente(Base):
    __tablename__ = 'dim_clientes'
    
    cgc = Column(String(50), primary_key=True)
    cod_cliente = Column(String)
    loja = Column(String)
    razaosocial = Column(String)
    regional = Column(String, index=True)
    bloqueado = Column(String)
    vendedor_nome = Column(String(100), index=True) 
    gerente_nome = Column(String(100), index=True)
    supervisor_nome = Column(String(100), index=True)

    vendas = relationship("FatoVendas", back_populates="cliente_rel")
    forecasts = relationship("FatoIbpGranular", back_populates="cliente_rel")

class FatoVendas(Base):
    __tablename__ = "fato_vendas"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    pedido = Column(String(50), nullable=False, index=True) 
    data_pedido = Column(Date, nullable=False, index=True)
    
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False)
    cgc = Column(String(50), ForeignKey("dim_clientes.cgc"), nullable=False)
    vendedor_nome = Column(String(100), index=True)
    
    qt_pedido = Column(Float, default=0.0)
    vl_pedido = Column(Float, default=0.0)

    qtfatura = Column(Float, default=0.0) 
    qtcorte = Column(Float, default=0.0)  

    __table_args__ = (
        UniqueConstraint('pedido', 'sku', 'cgc', name='uix_vendas_pedido'),
    )

    produto_rel = relationship("DimProduto", back_populates="vendas")
    cliente_rel = relationship("DimCliente", back_populates="vendas")

class FatoEstoqueD0(Base):
    """Guarda a posição consolidada do armazém 05 (API 90)"""
    __tablename__ = "fato_estoque_d0"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False, index=True)
    qtd_dispo = Column(Float, default=0.0)
    data_atualizacao = Column(DateTime, default=datetime.utcnow)

    produto_rel = relationship("DimProduto", back_populates="estoque")

class FatoInboundProducao(Base):
    """Entradas futuras programadas (Input do Supply Chain)"""
    __tablename__ = "fato_inbound_producao"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False, index=True)
    data_entrada = Column(Date, nullable=False, index=True)
    vol_caixas = Column(Integer, nullable=False)
    justificativa = Column(String, nullable=True)
    usuario_nome = Column(String, nullable=True) 
    criado_em = Column(DateTime, default=datetime.utcnow)

    produto_rel = relationship("DimProduto", back_populates="inbound")

class FatoIbpGranular(Base):
    __tablename__ = "fato_ibp_granular"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ciclo_sop = Column(String(10), nullable=False, index=True)
    mes_projetado = Column(Date, nullable=False, index=True)
    
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False)
    cgc = Column(String(50), ForeignKey("dim_clientes.cgc"), nullable=False)
    vendedor_nome = Column(String(100), index=True)

    vol_ia = Column(Integer, default=0)
    vol_topdown = Column(Integer, default=0)
    vol_supply = Column(Integer, default=0) 
    justificativa_supply = Column(String, nullable=True) 
    vol_bottomup = Column(Integer, default=0)
    vol_final = Column(Integer, default=0) 
    vol_meta = Column(Integer, default=0) 
    
    pmv_aplicado = Column(Float, default=0.0)
    
    __table_args__ = (
        UniqueConstraint('ciclo_sop', 'mes_projetado', 'sku', 'cgc', name='uix_forecast_atomico'),
    )

    produto_rel = relationship("DimProduto", back_populates="forecasts")
    cliente_rel = relationship("DimCliente", back_populates="forecasts")

class ControleCiclo(Base):
    __tablename__ = "controle_ciclos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ciclo_sop = Column(String(10), nullable=False, index=True)
    origem = Column(String(100), nullable=False) 
    status = Column(String(50), default='Aberto')
    data_fechamento = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('ciclo_sop', 'origem', name='uix_ciclo_origem'),
    )

class AuditoriaAjuste(Base):
    __tablename__ = "auditoria_ajustes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ciclo_sop = Column(String(10), nullable=False, index=True)
    data_ajuste = Column(DateTime, default=datetime.utcnow)
    origem_ajuste = Column(String(100), nullable=False) # Ex: 'Top-Down', 'Comercial'
    usuario_nome = Column(String(100), nullable=True)   # <-- NOVO: Quem fez a alteração
    
    sku = Column(String(50), nullable=False)
    razaosocial_afetada = Column(String(255), nullable=False)
    mes_projetado = Column(Date, nullable=False)
    
    valor_antigo = Column(Integer, default=0) # <-- NOVO: Valor antes do ajuste
    vol_novo = Column(Integer, nullable=False) # Novo valor gravado

class FatoAcuracia(Base):
    __tablename__ = "fato_acuracia"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ciclo_avaliado = Column(String(10), nullable=False, index=True) 
    mes_referencia = Column(Date, nullable=False, index=True)       
    
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False)
    vendedor_nome = Column(String(100), nullable=False, index=True)

    vol_realizado = Column(Integer, default=0)
    vol_ia = Column(Integer, default=0)       
    vol_consenso = Column(Integer, default=0) 
    
    acuracia_ia = Column(Float, default=0.0)
    acuracia_consenso = Column(Float, default=0.0)