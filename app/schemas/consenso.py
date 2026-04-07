from pydantic import BaseModel
from typing import List

class MesConsenso(BaseModel):
    mes_str: str              
    mes_banco: str          
    vol_ia: int
    vol_ajustado: int
    pmv: float

class LinhaConsensoMicro(BaseModel):
    chave_matriz: str
    cliente: str
    razaosocial: str
    produto: str
    descricao: str
    acuracia_ia: float
    meses: List[MesConsenso]  

class RespostaConsensoMicro(BaseModel):
    total_linhas: int
    dados: List[LinhaConsensoMicro]

class CelulaAjustada(BaseModel):
    chave_matriz: str
    mes_projetado: str       
    novo_volume: int
    pmv_aplicado: float

class PayloadAjusteConsenso(BaseModel):
    origem_ajuste: str        
    ajustes: List[CelulaAjustada]