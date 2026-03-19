FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .

ENV MCP_HOMELAB_CONFIG_DIR=/config

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/.well-known/oauth-authorization-server')" || exit 1

CMD ["mcp-homelab", "serve"]
