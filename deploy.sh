#!/usr/bin/env bash

# ==============================================================================
# Production Deployment Automation Script for SMK Flour Shop
# ==============================================================================
# This script is designed to run on a fresh AWS Ubuntu VM. It automates:
# - Swap memory creation (prevents crashes on small VMs)
# - System dependency installation (Docker, Compose, Certbot, Git)
# - Interactive environment configuration (with automatic secure credentials generation)
# - Dynamic Nginx & Let's Encrypt SSL certificate auto-setup (with HTTP fallback)
# - Database ready healthcheck synchronization (fixes "Connection refused" errors)
# - Django migrations, static file collection, permissions, and superuser creation
# ==============================================================================

set -o errexit
set -o pipefail
set -o nounset

# Colors for output styling
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0;80m' # No Color
CLEAR='\033[0m'

echo -e "${BLUE}======================================================================${CLEAR}"
echo -e "${GREEN}             SMK Flour Shop: Automated Production Deployer             ${CLEAR}"
echo -e "${BLUE}======================================================================${CLEAR}"

# 1. Require Root Privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root or with sudo.${CLEAR}"
    echo -e "Please run: ${YELLOW}sudo ./deploy.sh${CLEAR}"
    exit 1
fi

# Detect actual user behind sudo
REAL_USER="${SUDO_USER:-$USER}"

# 2. Swap Memory Configuration
echo -e "\n${BLUE}[1/7] Checking Swap Memory Space...${CLEAR}"
CURRENT_SWAP=$(swapon --show --noheadings | wc -l)
if [ "$CURRENT_SWAP" -eq 0 ]; then
    echo -e "${YELLOW}No active swap space detected. Setting up 2GB swap space to prevent memory crashes...${CLEAR}"
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo -e "${GREEN}✔ 2GB Swap Memory successfully configured and activated!${CLEAR}"
else
    echo -e "${GREEN}✔ Swap memory space is already active.${CLEAR}"
fi

# 3. System Dependencies Installation
echo -e "\n${BLUE}[2/7] Checking System Packages (Docker, Git, Certbot)...${CLEAR}"
apt-get update -y

install_if_missing() {
    local cmd=$1
    local pkg=$2
    if ! command -v "$cmd" &> /dev/null; then
        echo -e "${YELLOW}Installing $pkg...${CLEAR}"
        apt-get install -y "$pkg"
    fi
}

install_if_missing git git
install_if_missing curl curl

# Install Docker if not installed
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Installing Docker Engine...${CLEAR}"
    apt-get install -y docker.io
    systemctl start docker
    systemctl enable docker
fi

# Install Docker Compose Plugin if missing
if ! docker compose version &> /dev/null; then
    echo -e "${YELLOW}Installing Docker Compose Plugin...${CLEAR}"
    apt-get install -y docker-compose-v2
fi

# Ensure user is in docker group
usermod -aG docker "$REAL_USER" || true
echo -e "${GREEN}✔ System dependencies verified!${CLEAR}"

# 4. Load & Ask Environment Variables
echo -e "\n${BLUE}[3/7] Loading Environment Configurations...${CLEAR}"

# Parse existing .env file if it exists to serve as default inputs
if [ -f .env ]; then
    echo "Loading existing values from .env as defaults..."
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ ! "$line" =~ ^# ]] && [[ "$line" =~ = ]]; then
            key=$(echo "$line" | cut -d'=' -f1 | xargs)
            val=$(echo "$line" | cut -d'=' -f2- | xargs)
            # Remove any wrapping quotes
            val="${val%\"}"
            val="${val#\"}"
            val="${val%\'}"
            val="${val#\'}"
            declare "OLD_$key=$val"
        fi
    done < .env
fi

# Helper to read inputs with defaults
prompt_input() {
    local var_name=$1
    local prompt_text=$2
    local default_val=$3
    
    local old_var_name="OLD_$var_name"
    local old_val="${!old_var_name:-}"
    
    # Use previous env values as default if available
    if [ -n "$old_val" ]; then
        default_val="$old_val"
    fi
    
    local input_val
    read -r -p "$prompt_text [$default_val]: " input_val
    
    if [ -z "$input_val" ]; then
        eval "$var_name=\"\$default_val\""
    else
        eval "$var_name=\"\$input_val\""
    fi
}

# Domain & Secrets Configuration
prompt_input DOMAIN "Enter domain name or Public IP" "smkmaavukadai.duckdns.org"

# Django Secret Key
DEFAULT_SECRET=$(openssl rand -hex 24)
prompt_input SECRET_KEY "Enter Django SECRET_KEY (press Enter to use default/existing)" "$DEFAULT_SECRET"

# Database Configuration
prompt_input DB_NAME "Enter Database Name" "smk_flour_shop"
prompt_input DB_USER "Enter Database User" "smk_user"

DEFAULT_DB_PASS=$(openssl rand -hex 12)
prompt_input DB_PASSWORD "Enter Database Password" "$DEFAULT_DB_PASS"

DEFAULT_ROOT_PASS=$(openssl rand -hex 16)
prompt_input DB_ROOT_PASSWORD "Enter MySQL Root Password" "$DEFAULT_ROOT_PASS"

# Twilio Credentials
prompt_input TWILIO_ACCOUNT_SID "Enter Twilio Account SID (Optional)" ""
prompt_input TWILIO_AUTH_TOKEN "Enter Twilio Auth Token (Optional)" ""
prompt_input TWILIO_PHONE_NUMBER "Enter Twilio Phone Number (Optional)" ""

# Razorpay Credentials
prompt_input RAZORPAY_KEY_ID "Enter Razorpay Key ID (Optional)" "rzp_test_example"
prompt_input RAZORPAY_KEY_SECRET "Enter Razorpay Key Secret (Optional)" "example_secret"

# 5. SSL / Let's Encrypt Certificate Setup
echo -e "\n${BLUE}[4/7] Configuring SSL & Nginx Routing...${CLEAR}"

# Backup original nginx.conf if not done yet
if [ ! -f nginx.conf.backup ]; then
    cp nginx.conf nginx.conf.backup
fi

# Ask if they want SSL
read -r -p "Do you want to enable SSL (HTTPS) via Let's Encrypt? (y/n) [n]: " ENABLE_SSL
ENABLE_SSL=$(echo "$ENABLE_SSL" | tr '[:upper:]' '[:lower:]')

SSL_SUCCESS=false
if [[ "$ENABLE_SSL" =~ ^(y|yes)$ ]]; then
    install_if_missing certbot certbot
    prompt_input CERTBOT_EMAIL "Enter your email for Let's Encrypt security notices" "admin@$DOMAIN"
    
    echo -e "${YELLOW}Temporarily freeing port 80 to obtain SSL certificates...${CLEAR}"
    # Stop local Nginx service and docker-nginx container if running
    if systemctl is-active --quiet nginx; then
        systemctl stop nginx
    fi
    docker compose stop nginx 2>/dev/null || true
    
    echo -e "${YELLOW}Obtaining certificate for $DOMAIN...${CLEAR}"
    if certbot certonly --standalone -d "$DOMAIN" --agree-tos --email "$CERTBOT_EMAIL" --non-interactive; then
        echo -e "${GREEN}✔ SSL Certificates obtained successfully!${CLEAR}"
        SSL_SUCCESS=true
    else
        echo -e "${RED}Warning: Certbot was unable to obtain certificates for $DOMAIN.${CLEAR}"
        echo -e "This usually occurs if DNS is not yet pointed to this VM's IP, or port 80 is blocked."
        read -r -p "Would you like to fall back to HTTP-only for now? (y/n) [y]: " FALLBACK_HTTP
        FALLBACK_HTTP=$(echo "$FALLBACK_HTTP" | tr '[:upper:]' '[:lower:]')
        if [[ "$FALLBACK_HTTP" =~ ^(n|no)$ ]]; then
            echo -e "${RED}Aborting deployment to allow DNS resolving adjustments.${CLEAR}"
            exit 1
        fi
    fi
fi

# Write environment variables to .env
cat << EOF > .env
# Django Settings
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1,$DOMAIN
SITE_URL=$( [ "$SSL_SUCCESS" = true ] && echo "https://$DOMAIN" || echo "http://$DOMAIN" )

# Database Configuration
DB_ENGINE=mysql
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=db
DB_PORT=3306
MYSQL_ROOT_PASSWORD=$DB_ROOT_PASSWORD

# Redis and Celery Configuration
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Razorpay settings
RAZORPAY_KEY_ID=$RAZORPAY_KEY_ID
RAZORPAY_KEY_SECRET=$RAZORPAY_KEY_SECRET

# Twilio Settings
TWILIO_ACCOUNT_SID=$TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN=$TWILIO_AUTH_TOKEN
TWILIO_PHONE_NUMBER=$TWILIO_PHONE_NUMBER
EOF

# Generate Nginx config
if [ "$SSL_SUCCESS" = true ]; then
    echo -e "${YELLOW}Writing HTTPS configuration to nginx.conf...${CLEAR}"
    cat << EOF > nginx.conf
# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name $DOMAIN;
    
    # Let's Encrypt Certbot challenge directory
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

# HTTPS Server configuration
server {
    listen 443 ssl;
    server_name $DOMAIN;

    # SSL Certificates (mounted from Certbot on host)
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    # SSL session settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Max file upload size
    client_max_body_size 20M;

    # Gzip Compression settings
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript image/svg+xml;
    gzip_min_length 1000;

    # Serve static assets directly from the mounted volume
    location /static/ {
        alias /usr/share/nginx/html/static/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, no-transform";
    }

    # Serve uploaded media assets directly from the mounted volume
    location /media/ {
        alias /usr/share/nginx/html/media/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, no-transform";
    }

    # Reverse proxy to Gunicorn WSGI container
    location / {
        proxy_pass http://web:8000;
        
        # Standard proxy headers
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Hardened Security Headers
        add_header X-Frame-Options "DENY" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
        add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://*.openstreetmap.org https://*.tile.openstreetmap.org; frame-ancestors 'none';" always;
        add_header Referrer-Policy "same-origin" always;
    }
}
EOF
else
    echo -e "${YELLOW}Writing HTTP-only configuration to nginx.conf...${CLEAR}"
    cat << EOF > nginx.conf
server {
    listen 80;
    server_name $DOMAIN;

    # Max file upload size
    client_max_body_size 20M;

    # Gzip Compression settings
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript image/svg+xml;
    gzip_min_length 1000;

    # Serve static assets directly from the mounted volume
    location /static/ {
        alias /usr/share/nginx/html/static/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, no-transform";
    }

    # Serve uploaded media assets directly from the mounted volume
    location /media/ {
        alias /usr/share/nginx/html/media/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, no-transform";
    }

    # Reverse proxy to Gunicorn WSGI container
    location / {
        proxy_pass http://web:8000;
        
        # Standard proxy headers
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Hardened Security Headers
        add_header X-Frame-Options "DENY" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
        add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://*.openstreetmap.org https://*.tile.openstreetmap.org; frame-ancestors 'none';" always;
        add_header Referrer-Policy "same-origin" always;
    }
}
EOF
fi

# 6. Rebuilding & Starting Services
echo -e "\n${BLUE}[5/7] Starting Docker Compose Containers...${CLEAR}"
echo "Running: docker compose down..."
docker compose down

echo "Pruning unused Docker build cache and dangling images to free up VM disk space..."
docker image prune -f || true
docker builder prune -f || true

echo "Running: docker compose up -d --build..."
echo -e "${YELLOW}Compose is downloading/compiling and building images. Since Docker contains depends_on conditions, it will wait for the MySQL database to finish initializing and report healthy status before initiating web/celery.${CLEAR}"


docker compose up -d --build

echo -e "${GREEN}✔ Services are running and database has finished initialization!${CLEAR}"

# 7. Django Deployment Hooks (Migration, Static, Permissions)
echo -e "\n${BLUE}[6/7] Running Django Initialization Commands...${CLEAR}"

echo "Applying Django migrations..."
docker compose exec web python manage.py migrate

echo "Collecting static assets..."
docker compose exec --user root web python manage.py collectstatic --noinput

echo "Fixing file system ownership permissions..."
docker compose exec --user root web chown -R appuser:appgroup /app/staticfiles /app/media

# 8. Admin Superuser Options
echo -e "\n${BLUE}[7/7] Django Administrative Account Configuration...${CLEAR}"
read -r -p "Do you want to create a new Django superuser account right now? (y/n) [n]: " CREATE_ADMIN
CREATE_ADMIN=$(echo "$CREATE_ADMIN" | tr '[:upper:]' '[:lower:]')
if [[ "$CREATE_ADMIN" =~ ^(y|yes)$ ]]; then
    docker compose exec web python manage.py createsuperuser || true
fi

# Summary report
echo -e "\n${BLUE}======================================================================${CLEAR}"
echo -e "${GREEN}✔ SMK Flour Shop deployment completed successfully!                  ${CLEAR}"
echo -e "${BLUE}======================================================================${CLEAR}"
if [ "$SSL_SUCCESS" = true ]; then
    echo -e "🌐 App URL:    ${GREEN}https://$DOMAIN${CLEAR}"
else
    echo -e "🌐 App URL:    ${YELLOW}http://$DOMAIN${CLEAR} (No SSL)"
fi
echo -e "🗄 Adminer UI:  ${GREEN}http://$DOMAIN:8080${CLEAR} (Credentials inside .env)"
echo -e "💡 To view logs: ${YELLOW}docker compose logs -f web${CLEAR}"
echo -e "${BLUE}======================================================================${CLEAR}"
