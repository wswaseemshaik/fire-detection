FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY . .
ENV FIRE_HOST=0.0.0.0 FIRE_PORT=8000 FIRE_DEVICE=cpu
EXPOSE 8000
VOLUME ["/srv/data"]
CMD ["python", "run.py"]
