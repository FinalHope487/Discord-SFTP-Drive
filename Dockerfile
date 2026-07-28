FROM python:3.11-slim

# An unprivileged account to run as. A flaw reachable through the SFTP surface
# should not also hand over root inside the container.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# The host key volume mounts at /app/keys. Docker seeds a *newly created*
# named volume from the image path it covers, ownership included, so making
# the directory appuser-owned here is what lets the server write its host key
# without any runtime chown or an entrypoint that starts as root.
#
# This only applies to a volume Docker creates. A host_key_data volume left
# over from an earlier root-running build keeps its root ownership and the
# server will fail to read the key; ensure_host_key() in src/main.py explains
# the one-time migration in its error message.
RUN mkdir -p /app/keys && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "src.main"]
