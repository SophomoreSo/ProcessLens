# ProcessLens — single-user local container image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/processlens
RUN useradd --create-home --uid 10001 processlens

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY sample_data ./sample_data
COPY scripts ./scripts

# Inside the container the servers must listen on all interfaces so Docker can
# forward the ports. compose.yaml publishes the admin port (8000) on 127.0.0.1
# only — never publish the admin site on a public interface. The read-only guest
# site (8080, enabled with PROCESSLENS_GUEST_ENABLED=true) is the one for others.
ENV PROCESSLENS_HOST=0.0.0.0 \
    PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true \
    PROCESSLENS_DATA_DIR=/data
RUN mkdir -p /data && chown processlens:processlens /data
VOLUME ["/data"]
USER processlens

EXPOSE 8000 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "-m", "app"]
