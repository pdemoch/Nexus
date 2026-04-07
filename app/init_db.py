from app.core.database import engine
from app.models.domain_models import Base

print("⚙️ Criando as tabelas do Nexus 3.0 no SQLite...")
Base.metadata.create_all(bind=engine)
print("✅ Banco de dados pronto para uso!")