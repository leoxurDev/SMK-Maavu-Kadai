FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system build and runtime dependencies globally
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies globally
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Create a non-root system user and group
RUN groupadd -r appgroup && useradd -r -g appgroup -d /home/appuser -m appuser

# Copy project files and set ownership
COPY --chown=appuser:appgroup . /app/

# Expose Django port
EXPOSE 8000

# Switch to the non-root user
USER appuser

# Gunicorn runs the WSGI server in production
CMD ["gunicorn", "smk_flour_shop.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
