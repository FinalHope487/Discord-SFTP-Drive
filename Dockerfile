# Matches the interpreter the test suite runs on. It used to be 3.11 while
# every test ran on 3.12, so nothing had ever been executed on the version
# that serves real traffic. The risk was not "different versions" in the
# abstract: pytest.ini promotes DeprecationWarning from src.* to an error,
# and that is precisely what drifts between minor releases -- so a drift
# would have surfaced only inside the container, where no test could run.
#
# All four compiled dependencies publish wheels for cp312 on manylinux
# (cryptography and argon2-cffi-bindings via abi3, pymongo and aiohttp
# per-version), so the image still needs no build toolchain.
FROM python:3.12-slim

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
