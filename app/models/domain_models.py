from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint
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
    aprovado = Column(Boolean, default=False)
    primeiro_acesso = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

class DimProduto(Base):
    __tablename__ = 'dim_produtos'
    
    sku = Column(String, primary_key=True)
    descricao = Column(String)
    bu = Column(String)
    categoria = Column(String)
    segmento = Column(String)
    curva = Column(String)
    modelo_vencedor = Column(String, nullable=True) 
    acuracia_ia = Column(Float, nullable=True)

    vendas = relationship("FatoVendas", back_populates="produto_rel")
    forecasts = relationship("FatoIbpGranular", back_populates="produto_rel")

class DimCliente(Base):
    __tablename__ = 'dim_clientes'
    
    cgc = Column(String, primary_key=True)
    cod_cliente = Column(String)
    loja = Column(String)
    razaosocial = Column(String)
    regional = Column(String)
    bloqueado = Column(String)
    vendedor_nome = Column(String) 
    gerente_nome = Column(String, nullable=True)

    vendas = relationship("FatoVendas", back_populates="cliente_rel")
    forecasts = relationship("FatoIbpGranular", back_populates="cliente_rel")

class FatoVendas(Base):
    __tablename__ = "fato_vendas"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    data_pedido = Column(Date, nullable=False, index=True)
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False)
    cgc = Column(String(50), ForeignKey("dim_clientes.cgc"), nullable=False)
    vendedor_nome = Column(String(100), index=True)
    
    qt_pedido = Column(Float, default=0.0)
    vl_pedido = Column(Float, default=0.0)

    produto_rel = relationship("DimProduto", back_populates="vendas")
    cliente_rel = relationship("DimCliente", back_populates="vendas")

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
    vol_bottomup = Column(Integer, default=0)
    vol_final = Column(Integer, default=0) 
    
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
    origem_ajuste = Column(String(100), nullable=False) 
    
    sku = Column(String(50), nullable=False)
    razaosocial_afetada = Column(String(255), nullable=False)
    mes_projetado = Column(Date, nullable=False)
    
    vol_novo = Column(Integer, nullable=False)

class FatoAcuracia(Base):
    __tablename__ = "fato_acuracia"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ciclo_avaliado = Column(String(10), nullable=False) 
    mes_referencia = Column(Date, nullable=False)       
    sku = Column(String(50), ForeignKey("dim_produtos.sku"), nullable=False)
    vendedor_nome = Column(String(100), nullable=False)

    vol_realizado = Column(Integer, default=0)
    
    acuracia_ia = Column(Float, default=0.0)
    acuracia_topdown = Column(Float, default=0.0)
    acuracia_bottomup = Column(Float, default=0.0)