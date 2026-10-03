# Single image: builds the React frontend, then serves it together with the API
# from the FastAPI backend.  Includes Tesseract so OCR works out of the box.
#
# NOTE: this file was written but not built in the development environment
# (Docker was not installed there).  See README "Docker (optional)".

FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /build/dist /app/frontend/dist

# Run as an unprivileged user; uploaded documents live on the /data volume.
RUN useradd --create-home --uid 10001 policygen && mkdir -p /data && chown policygen /data
USER policygen
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
