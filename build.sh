#!/usr/bin/env bash
# Render Build Script — runs during every deploy

set -o errexit  # exit on error

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Ensuring media directory structure exists..."
mkdir -p media/products media/qr_codes

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Syncing media folder to staticfiles..."
mkdir -p staticfiles/media
cp -rn media/. staticfiles/media/ 2>/dev/null || true

echo "Running database migrations..."
python manage.py migrate

echo "Creating superuser (if not exists)..."
python manage.py createsuperuser --noinput || echo "Superuser already exists, skipping."
