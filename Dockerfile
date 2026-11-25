FROM python:3.12-slim

WORKDIR /app

# Instala dependências do sistema (incluindo OCR em português opcional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala dependências do projeto
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copia código da aplicação
COPY . .

# Cria diretórios de persistência e garante permissões para qualquer ambiente (incluindo Hugging Face Spaces UID 1000)
RUN mkdir -p /data/backups /data/evidence && chmod -R 777 /data /app

# Configurações padrão de ambiente para produção
ENV SERVER_HOST=0.0.0.0
ENV SERVER_PORT=8000
ENV DB_PATH=/data/fc27_clubs.db
ENV BACKUP_DIR=/data/backups
ENV EVIDENCE_DIR=/data/evidence
ENV LOG_LEVEL=INFO
ENV PLATFORM=common-gen5

EXPOSE 8000

# Executa API FastAPI + Worker de Ingestão no mesmo container
CMD ["python", "__main__.py", "both"]
