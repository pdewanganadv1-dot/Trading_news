FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY relay.py delta_client.py ./
ENV PORT=8080 LOG_DIR=/app/logs
CMD ["sh", "-c", "uvicorn relay:app --host 0.0.0.0 --port ${PORT}"]
