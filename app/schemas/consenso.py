from pydantic import BaseModel
from typing import List, Optional

# --- SCHEMAS DE LEITURA (GET) ---
class MesConsenso(BaseModel):
    mes_str: str              # Ex: "04/2026"
    mes_banco: str            # Ex: "2026-04-01"
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
    meses: List[MesConsenso]  # Aninhamento perfeito para o TanStack Table no React

class RespostaConsensoMicro(BaseModel):
    total_linhas: int
    dados: List[LinhaConsensoMicro]

# --- SCHEMAS DE GRAVAÇÃO (POST) ---
class CelulaAjustada(BaseModel):
    chave_matriz: str
    mes_projetado: str        # Formato "YYYY-MM-DD" para casar com o banco
    novo_volume: int
    pmv_aplicado: float

class PayloadAjusteConsenso(BaseModel):
    origem_ajuste: str        # Ex: "S&OP Micro (Vendedor)"
    ajustes: List[CelulaAjustada]