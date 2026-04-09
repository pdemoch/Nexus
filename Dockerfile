# Usa uma versão leve do Python 3.10
FROM python:3.10-slim

# Define a pasta de trabalho dentro do contentor
WORKDIR /app

# Instala dependências do sistema necessárias para compilar algumas libs de IA (como o Prophet)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia a lista de requisitos e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código do projeto para dentro do contentor
COPY . .

# Expõe a porta 8000 que a nossa API usa
EXPOSE 8000

# Comando para rodar o servidor FastAPI
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]