# ---- frontend build ----
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend .
RUN npm run build

# ---- runtime ----
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY app ./app
COPY models ./models
COPY --from=ui /ui/dist ./frontend/dist
ENV FIRE_HOST=0.0.0.0 FIRE_PORT=8000 FIRE_DEVICE=cpu FIRE_DATA_DIR=/srv/data
EXPOSE 8000
VOLUME ["/srv/data"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
