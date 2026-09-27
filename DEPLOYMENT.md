# Hospital Management System — Deployment Guide

This guide explains, step by step, how to take the project live after buying a domain and hosting.

## Prerequisites (after purchase)
- **Domain**: buy from Namecheap / GoDaddy
- **VPS Hosting**: Hostinger VPS, DigitalOcean, or Railway (Ubuntu 24.04 recommended)
- Once the VPS is provisioned: you will receive an IP address and root/SSH access

---

## Step 1: Connect the domain to the VPS
In the domain registrar's (Namecheap/GoDaddy) DNS settings:
- A Record: `@` → your VPS IP address
- A Record: `www` → your VPS IP address

(DNS propagation can take 1-24 hours)

## Step 2: Log in to the VPS over SSH
```bash
ssh root@your_server_ip
```

## Step 3: Server setup
```bash
apt update && apt upgrade -y
apt install -y python3-pip python3-venv nginx postgresql postgresql-contrib certbot python3-certbot-nginx git
```

## Step 4: Create the PostgreSQL database
```bash
sudo -u postgres psql
```
Run these commands inside:
```sql
CREATE DATABASE hospital_db;
CREATE USER hospital_user WITH PASSWORD 'PUT_STRONG_PASSWORD_HERE';
ALTER ROLE hospital_user SET client_encoding TO 'utf8';
GRANT ALL PRIVILEGES ON DATABASE hospital_db TO hospital_user;
\q
```

## Step 5: Upload the project
```bash
mkdir -p /var/www/hospital_system
cd /var/www/hospital_system
# Upload/extract your zip here, or git clone the repository
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Step 6: Set up the .env file for production
```bash
cp .env.example .env
nano .env
```
Fill in these values:
```
SECRET_KEY=<generate a new secret with the command below>
DEBUG=False
ALLOWED_HOSTS=yourhospital.com,www.yourhospital.com
DATABASE_URL=postgres://hospital_user:PUT_STRONG_PASSWORD_HERE@127.0.0.1:5432/hospital_db
```
To generate a new SECRET_KEY:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

## Step 7: Migrate the database and collect static files
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## Step 8: Start the Gunicorn service
```bash
mkdir -p /var/log/hospital_system
cp deploy/hospital_system.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hospital_system
systemctl status hospital_system   # confirm that it shows "active (running)"
```

## Step 9: Set up Nginx
```bash
cp deploy/nginx.conf /etc/nginx/sites-available/hospital_system
# Replace yourhospital.com in nginx.conf with your actual domain
ln -s /etc/nginx/sites-available/hospital_system /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

## Step 10: Install a free SSL certificate (HTTPS)
```bash
certbot --nginx -d yourhospital.com -d www.yourhospital.com
```
Certbot updates the Nginx config for HTTPS automatically. The certificate is free and keeps auto-renewing.

## Step 11: Set up the daily backup
```bash
chmod +x deploy/backup_db.sh
crontab -e
```
Add this line:
```
0 2 * * * /var/www/hospital_system/deploy/backup_db.sh >> /var/log/hospital_system/backup.log 2>&1
```
(An automatic backup runs every night at 2 AM and is retained for 14 days)

---

## Verify everything is working
1. Open `https://yourhospital.com` in a browser and check that the login page loads
2. The SSL lock icon should be visible (https, not http)
3. Log in and test the dashboard
4. Both `systemctl status hospital_system` and `systemctl status nginx` should be "active"

## Future code updates (whenever you need to deploy changes)
```bash
cd /var/www/hospital_system
git pull   # or upload a new zip
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
systemctl restart hospital_system
```

## If something goes wrong
```bash
journalctl -u hospital_system -n 50   # view the last 50 error lines
tail -50 /var/log/hospital_system/error.log
```
