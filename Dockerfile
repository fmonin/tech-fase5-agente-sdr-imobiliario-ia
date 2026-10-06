# Imagem simples para rodar a interface Streamlit do Sr. Agim (Agente SDR).
# Build:  docker build -t agente-sdr-imobiliario .
# Run:    docker run --env-file .env -p 8501:8501 agente-sdr-imobiliario
FROM python:3.11-slim

WORKDIR /app

# Dependências do sistema exigidas pelo SDK de voz do Azure (libssl, libasound)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev \
    libasound2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# PyTorch versão CPU (o pacote padrão do Linux traz CUDA e deixa a imagem
# ~2 GB maior). Necessário para os embeddings locais (sentence-transformers).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt
# Baixa o modelo de embeddings durante o build (o container não depende de
# internet para isso na primeira conversa).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

COPY . .

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
