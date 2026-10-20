ARG PYTHON_IMAGE=python:3.10-alpine3.22@sha256:8be23d04f6f85e79a56f6b86761f8bd1db7d3f673917c2fcecb049d4af510dc8

FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build

# Build in a disposable stage so compilers and Python packaging tools never
# become part of the runtime attack surface.
COPY pyproject.toml README.md MANIFEST.in /build/
COPY src /build/src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade \
        "pip==26.2.1" "setuptools==82.0.1" "wheel==0.46.2" \
    && /opt/venv/bin/pip install --no-cache-dir /build \
    && /opt/venv/bin/pip uninstall --yes pip setuptools wheel

FROM ${PYTHON_IMAGE} AS runtime

# Ownership marker for the official MCP registry (registry.modelcontextprotocol.io).
LABEL io.modelcontextprotocol.server.name="io.github.erpipe-org/mcp-odoo"

WORKDIR /app

# Apply published Alpine security fixes and remove packaging tools that are not
# needed to run the gateway. The gateway runs as a fixed unprivileged UID/GID.
RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes pip setuptools wheel \
    && addgroup --gid 10001 odoo-mcp \
    && adduser --uid 10001 --ingroup odoo-mcp --disabled-password \
        --no-create-home --shell /sbin/nologin odoo-mcp \
    && mkdir -p /app/logs /var/log/odoo-mcp \
    && chown -R 10001:10001 /app/logs /var/log/odoo-mcp

COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv

# Runtime Odoo connection values should be supplied via `docker run -e ...`.
# Do not bake credential placeholders into the image; Docker flags password ENV
# declarations as secrets even when the default is empty.
ENV ODOO_TIMEOUT="30"
ENV ODOO_VERIFY_SSL="1"
ENV DEBUG="0"

# Set stdout/stderr to unbuffered mode
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:${PATH}"

# Streamable HTTP uses this port by default when enabled.
EXPOSE 8000

USER 10001:10001

# Run through the public package entry point.
ENTRYPOINT ["odoo-mcp"]
