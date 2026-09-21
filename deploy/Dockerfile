FROM python:3.12-slim

WORKDIR /app

# Install system deps
# libreoffice-writer + Word-metric fonts: DOCX -> PDF for the per-plan page
# limit (core/gateway.py _docx_page_count). Carlito/Caladea/Liberation match
# Calibri/Cambria/Times/Arial widths so page counts track Word.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libreoffice-writer-nogui \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create required directories
RUN mkdir -p data logs chroma_store

# Run with gunicorn + 2 uvicorn workers
# Keep workers=2: embedding models load into RAM per worker
CMD ["gunicorn", "core.gateway:app", \
     "--workers", "2", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:5000", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--access-logfile", "logs/access.log", \
     "--error-logfile", "logs/error.log"]
