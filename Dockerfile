FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY src/ ./src/
COPY themes/ ./themes/

EXPOSE 8000

# FastMCP 4.x serves RFC 9728 metadata at .../oauth-protected-resource/mcp; 3.x used the bare path.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request as u\nfor p in ('/.well-known/oauth-protected-resource/mcp', '/.well-known/oauth-protected-resource'):\n    try:\n        if u.urlopen('http://localhost:8000' + p, timeout=4).status == 200: sys.exit(0)\n    except OSError: pass\nsys.exit(1)"]

CMD ["python", "src/server.py"]
