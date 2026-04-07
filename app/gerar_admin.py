import bcrypt
from datetime import datetime
from app.core.database import SessionLocal
from app.models.domain_models import Usuario

def forjar_chave_mestra():
    print("🔐 Forjando a Chave Mestra do Nexus...")
    
    senha_plana = "admin123"
    senha_criptografada = bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    with SessionLocal() as db:
        try:
            admin_existente = db.query(Usuario).filter(Usuario.email == "admin@linea.com.br").first()
            if admin_existente:
                print("⚠️ O Admin já existe! Email: admin@linea.com.br | Senha: admin123")
                return

            # Retirámos o "id" para o banco gerar automaticamente (seja número ou uuid)
            # Adicionámos aprovado=True para garantir que você entra direto!
            novo_admin = Usuario(
                nome="Administrador Nexus",
                email="admin@linea.com.br",
                senha_hash=senha_criptografada, 
                funcao="Administrador",
                aprovado=True,
                primeiro_acesso=False,
                criado_em=datetime.now()
            )
            
            db.add(novo_admin)
            db.commit()
            
            print("✅ [SUCESSO] Chave Mestra injetada no Banco de Dados!")
            print("=========================================")
            print("👤 Email: admin@linea.com.br")
            print("🔑 Senha: admin123")
            print("=========================================")
            
        except Exception as e:
            db.rollback()
            print(f"❌ Erro ao forjar a chave: {e}")

if __name__ == "__main__":
    forjar_chave_mestra()