# --- Stage 1: Build dependencies ---
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: Final minimal runtime ---
FROM python:3.11-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install runtime system libraries (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root system user and group
RUN groupadd -r appgroup && useradd -r -g appgroup -d /home/appuser -m appuser

# Copy installed python dependencies from builder globally so all users (including root) can access them
COPY --from=builder /usr/local /usr/local

# Copy project files and set ownership
COPY --chown=appuser:appgroup . /app/

# Expose Django port
EXPOSE 8000

# Switch to the non-root user
USER appuser

# Gunicorn runs the WSGI server in production
CMD ["gunicorn", "smk_flour_shop.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
