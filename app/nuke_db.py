from app.core.database import engine
from app.models.domain_models import Base

print("💣 Destruindo tabelas antigas...")
Base.metadata.drop_all(bind=engine)

print("🧱 Construindo tabelas novas atualizadas...")
Base.metadata.create_all(bind=engine)

print("✅ Banco de dados 100% zerado e atualizado com as colunas novas!")