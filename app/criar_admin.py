from app.core.database import SessionLocal
from app.models.domain_models import Usuario
import bcrypt # Certifique-se de ter o bcrypt instalado (pip install bcrypt)

def criar_primeiro_admin():
    db = SessionLocal()
    try:
        # Verifica se já existe algum admin
        admin_existente = db.query(Usuario).filter(Usuario.email == "admin@lineaalimentos.com.br").first()
        if admin_existente:
            print("⚠️ O usuário Administrador já existe!")
            return

        # Criptografa a senha "Nexus@2026"
        senha_plana = "Nexus@2026"
        senha_hash = bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Cria o usuário
        novo_admin = Usuario(
            nome="Administrador Nexus",
            email="admin@lineaalimentos.com.br",
            senha_hash=senha_hash,
            funcao="Administrador",
            aprovado=True,
            primeiro_acesso=False
        )
        
        db.add(novo_admin)
        db.commit()
        print("✅ Usuário Mestre criado com sucesso!")
        print("📧 Email: admin@lineaalimentos.com.br")
        print("🔑 Senha: Nexus@2026")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Erro ao criar usuário: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    criar_primeiro_admin()