# Production Deployment Guide: SMK Flour Shop on AWS EC2

This guide outlines the step-by-step instructions to deploy the production-hardened SMK Flour Shop application onto a fresh **AWS EC2 Ubuntu Server**.

---

## Step 1: Provision your EC2 Instance
1. Launch a new EC2 instance:
   * **OS**: Ubuntu 22.04 LTS (x86_64 or arm64 for Graviton)
   * **Instance Type**: `t3.small` (General Purpose) or `t4g.small` (AWS Graviton) with 2 GB RAM (highly recommended for running Django, MySQL, Redis, and Celery together).
   * **Storage**: 20 GB gp3 SSD
2. In the **Security Group** rules, open the following inbound ports:
   * `22` (SSH) — Restricted to your IP
   * `80` (HTTP) — Open to anywhere (`0.0.0.0/0`)
   * `443` (HTTPS) — Open to anywhere (`0.0.0.0/0`)

---

## Step 2: Install System Dependencies & Setup Swap Memory

1. Connect to your EC2 instance via SSH and update system packages:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. Configure a **2GB Swap File** (this acts as virtual memory to prevent database or container crashes during order spikes):
   ```bash
   # Allocate a 2GB file for swap space on the SSD
   sudo fallocate -l 2G /swapfile

   # Restrict permissions so only the root user can read/write it (crucial for security)
   sudo chmod 600 /swapfile

   # Set up the file as a Linux swap area
   sudo mkswap /swapfile

   # Enable the swap space immediately in the running kernel
   sudo swapon /swapfile

   # Make the swap settings permanent so it remains active after server reboots
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

3. Verify that the Swap Memory is active and correctly configured:
   ```bash
   # Verify the active swap systems
   sudo swapon --show

   # Check the total system memory allocation (you should see a ~2.0G Swap line)
   free -h
   ```

4. Install Docker, Compose, and Git on the host:
   ```bash
   # Install git and Docker dependencies (docker-compose-v2 provides the modern 'docker compose' command)
   sudo apt install -y git docker.io docker-compose-v2

   # Start and enable the Docker daemon
   sudo systemctl start docker
   sudo systemctl enable docker

   # Add your current user to the docker group so you do not need sudo for docker commands
   sudo usermod -aG docker $USER
   ```
*Note: Log out and log back in to apply the group membership changes.*

---

## Step 3: Clone Repository and Configure Environment
1. Clone the project code to your server:
   ```bash
   git clone https://github.com/leoxurDev/SMK-Maavu-Kadai.git smk-flour-shop
   cd smk-flour-shop
   ```
2. Copy the environment configuration template:
   ```bash
   cp .env.example .env
   ```
3. Edit the `.env` file to set your production variables:
   ```bash
   nano .env
   ```
   **Important Production Values to Set:**
   * `DEBUG=False`
   * `SECRET_KEY=generate_a_secure_random_string_here`
   * `ALLOWED_HOSTS=yourdomain.com,your-ec2-public-ip`
   * `SITE_URL=https://yourdomain.com`
   * `DB_PASSWORD=set_a_secure_database_password`
   * `MYSQL_ROOT_PASSWORD=set_a_secure_root_password`
   * `TWILIO_ACCOUNT_SID=your_actual_sid`
   * `TWILIO_AUTH_TOKEN=your_actual_token`
   * `TWILIO_PHONE_NUMBER=your_twilio_number`

---

## Step 4: Build and Launch Services
Start the production docker compose services in detached/background mode:
```bash
docker compose up --build -d
```

---

## Step 5: Database Setup & Admin Creation
Run migrations, compile static files, and create a store manager superuser:
```bash
# Run database migrations
docker compose exec web python manage.py migrate

# Collect static files into the shared static volume
docker compose exec web python manage.py collectstatic --noinput

# Create your Django Admin superuser account
docker compose exec web python manage.py createsuperuser
```

---

## Step 6: Configure SSL Certificate (Let's Encrypt)
To obtain a free SSL certificate for `yourdomain.com`:
1. Stop the Nginx container temporarily so Certbot can run its verification server:
   ```bash
   docker compose stop nginx
   ```
2. Install Certbot on the host:
   ```bash
   sudo apt install -y certbot
   ```
3. Request the certificate:
   ```bash
   sudo certbot certonly --standalone -d yourdomain.com -d www.yourdomain.com
   ```
4. Update `docker-compose.yml` or `nginx.conf` if necessary, and start services again:
   ```bash
   docker compose start nginx
   ```

---

## Step 7: Useful Commands for Maintenance
* **View application logs**:
  ```bash
  docker compose logs -f web
  ```
* **Restart workers or celery tasks service**:
  ```bash
  docker compose restart celery
  ```
* **Stop all services**:
  ```bash
  docker compose down
  ```
