FROM python:3.10-slim

# Ownership marker for the official MCP registry (registry.modelcontextprotocol.io).
LABEL io.modelcontextprotocol.server.name="io.github.erpipe-org/mcp-odoo"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy source code
COPY . /app/

# Install the package using the dependency constraints declared by the project.
RUN pip install --no-cache-dir .

# The gateway never needs root or a shell-writable application tree at runtime.
RUN groupadd --gid 10001 odoo-mcp \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin odoo-mcp \
    && mkdir -p /app/logs /var/log/odoo-mcp \
    && chown -R 10001:10001 /app/logs /var/log/odoo-mcp

# Runtime Odoo connection values should be supplied via `docker run -e ...`.
# Do not bake credential placeholders into the image; Docker flags password ENV
# declarations as secrets even when the default is empty.
ENV ODOO_TIMEOUT="30"
ENV ODOO_VERIFY_SSL="1"
ENV DEBUG="0"

# Set stdout/stderr to unbuffered mode
ENV PYTHONUNBUFFERED=1

# Streamable HTTP uses this port by default when enabled.
EXPOSE 8000

USER 10001:10001

# Run through the public package entry point.
ENTRYPOINT ["odoo-mcp"]
