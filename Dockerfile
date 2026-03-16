FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Secrets injected via environment variables at runtime
# e.g. docker run --env-file .env mcp-homelab

EXPOSE 8000

CMD ["python", "server.py"]
