# Odoo 16 Docker Swarm Production Deployment Guide

This guide provides comprehensive instructions for deploying a production-ready Odoo 16 instance on Docker Swarm with multiple replicas, load balancing via Traefik, and S3-backed attachment storage.

## 📋 Table of Contents

- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Multi-Replica Swarm Deployment](#multi-replica-swarm-deployment)
- [S3 Storage Setup](#s3-storage-setup)
- [Monitoring & Scaling](#monitoring--scaling)
- [Troubleshooting](#troubleshooting)
- [Security Best Practices](#security-best-practices)

---

## Architecture Overview

### System Components

```
                          ┌─────────────────┐
                          │  Traefik (LB)   │
                          │   Port 80/443   │
                          └────────┬────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
            ┌───────▼──────┐ ┌────▼──────┐ ┌────▼──────┐
            │  Odoo Replica│ │Odoo Replica│ │Odoo Replica│
            │     (Cron)   │ │   (App)    │ │   (App)    │
            │   Port 8069  │ │ Port 8069  │ │ Port 8069  │
            └───────┬──────┘ └────┬──────┘ └────┬──────┘
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
            ┌───────▼──────┐ ┌────▼──────┐ ┌────▼──────┐
            │  PostgreSQL  │ │  S3 Bucket│ │   Volume  │
            │  (Shared DB) │ │(Attachments)│ │ (Data)   │
            └──────────────┘ └───────────┘ └───────────┘
```

### Key Features

- **Multi-Replica**: Run 2+ Odoo instances for high availability
- **Sticky Sessions**: User sessions stay on same instance
- **S3 Storage**: Shared attachment storage across all replicas
- **Auto-Scaling**: Add replicas without downtime
- **Health Checks**: Automatic detection and removal of failed instances
- **Rolling Updates**: Zero-downtime deployments
- **Cron Isolation**: Prevent duplicate scheduled job execution

## Requirements

### Infrastructure Requirements

1. **Docker Swarm Cluster** (minimum 2 nodes for HA)
   ```bash
   docker node ls
   ```

2. **PostgreSQL Database** (AWS RDS recommended)
   - Must be accessible from all Swarm nodes
   - Minimum: db.t3.small (2GB RAM)
   - Multi-AZ enabled for production

3. **S3 Storage** (AWS S3, MinIO, DigitalOcean Spaces, etc.)
   - Bucket for Odoo attachments
   - IAM role or access keys with S3 permissions

4. **Traefik** (already deployed as external service)
   - Getting started info from: https://hub.docker.com/_/odoo
   - Install or use a Docker + docker-compose environment setup or server

## Files Overview

Key files in this deployment:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Swarm deployment configuration with 2+ replicas, health checks, load balancing |
| `.env.template` | Environment variables template (copy to `.env` and fill) |
| `Dockerfile` | Custom Odoo image with bundled config, modules, and entrypoint script |
| **`entrypoint.sh`** | ⭐ **Generates odoo.conf from environment variables at startup** |
| `config/odoo.conf` | Reference template (actual config is generated at runtime) |
| `odoo_custom_modules/` | Custom Odoo modules bundled in image |

### The Entrypoint Script (entrypoint.sh)

This script runs when the container starts:

1. **Validates** required environment variables are set (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
2. **Generates** `/etc/odoo/odoo.conf` with actual values from `.env` file
3. **Adds** optional S3 storage configuration if provided (S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY)
4. **Starts** Odoo with the generated config

**Why this approach?** Odoo doesn't support shell-style variable substitution in `.conf` files. This entrypoint script solves that by generating the config at runtime, keeping secrets out of the image.

## Installation steps:
1. Clone the repo 
2. Create or copy `.env.template` into `.env` file in the root directory
3. Edit the environment variables to match your infrastructure
4. Generate strong credentials (see Configuration section)
5. Build custom Docker image: `docker build -t odoo-custom:16.0 .`
6. Push to registry for multi-node swarm: `docker push your-registry/odoo-custom:16.0`
7. Initialize database: `docker run --rm --env-file .env odoo-custom:16.0 odoo --stop-after-init`
8. Deploy to swarm: `docker stack deploy -c docker-compose.yml odoo`

## Configuration

### How Environment Variables Work

**Important**: Odoo doesn't support shell-style variable substitution in `.conf` files (like `${DB_HOST}`). 

**Solution**: We use an **entrypoint script** that generates the `odoo.conf` file at container startup from environment variables.

```bash
# Flow:
1. Container starts → entrypoint.sh runs
2. entrypoint.sh reads environment variables from .env
3. Generates /etc/odoo/odoo.conf with actual values
4. Starts Odoo with the generated config
```

This means:
- ✅ All configuration comes from environment variables
- ✅ No hardcoded secrets in the Docker image
- ✅ Easy to deploy to different environments
- ✅ Works perfectly with Docker Swarm and CI/CD

### Environment Variables

Create `.env` file from template. Key variables:

```env
# Database (Required)
DB_HOST=postgres.example.com
DB_PORT=5432
DB_USER=odoo
DB_PASSWORD=<generate_strong_password>

# S3 Storage (Recommended for multi-replica)
S3_ENDPOINT=https://s3.amazonaws.com
S3_BUCKET=my-odoo-attachments-prod
S3_ACCESS_KEY=<your_key>
S3_SECRET_KEY=<your_secret>

# Security
SECRET_KEY_BASE=<generate_64_char_hex>

# Cron Mode (Important for multi-replica)
ODOO_CRON_MODE=0  # Set to 2 on cron worker replica only
```

**Generate strong credentials**:
```bash
openssl rand -hex 32  # For DB_PASSWORD
openssl rand -hex 64  # For SECRET_KEY_BASE
```

### S3 Storage Setup

#### AWS S3

1. Create S3 bucket:
```bash
aws s3 mb s3://my-odoo-attachments-prod --region eu-west-1
```

2. Create IAM user with S3 permissions:
```bash
aws iam create-user --user-name odoo-app
aws iam create-access-key --user-name odoo-app
```

3. Attach S3 policy (see full README for detailed policy JSON)

4. Configure in `.env`:
```env
S3_ENDPOINT=https://s3.amazonaws.com
S3_BUCKET=my-odoo-attachments-prod
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=wJal...
```

5. Uncomment S3 settings in `config/odoo.conf`:
```ini
ir_attachment.location = s3://odoo-attachments
s3_server = ${S3_ENDPOINT}
s3_bucket = ${S3_BUCKET}
s3_access_key_id = ${S3_ACCESS_KEY}
s3_secret_access_key = ${S3_SECRET_KEY}
```

#### DigitalOcean Spaces (Alternative)

```env
S3_ENDPOINT=https://fra1.digitaloceanspaces.com
S3_BUCKET=my-odoo-attachments
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
```

#### MinIO (Self-Hosted)

```env
S3_ENDPOINT=https://minio.example.com:9000
S3_BUCKET=odoo-attachments
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
```

## Multi-Replica Deployment

### Build Custom Image

```bash
# Build locally
docker build -t odoo-custom:16.0 .

# Push to registry (required for multi-node swarm)
docker tag odoo-custom:16.0 your-registry/odoo-custom:16.0
docker push your-registry/odoo-custom:16.0
```

### Initialize Database (First Time Only)

```bash
docker run --rm --env-file .env odoo-custom:16.0 odoo --stop-after-init
```

### Deploy to Swarm

```bash
# Create overlay network
docker network create --driver overlay traefik-network

# Deploy stack
docker stack deploy -c docker-compose.yml odoo

# Monitor deployment
watch docker service ls
docker service logs odoo_odoo -f
```

### Scaling

```bash
# Scale to 3 replicas
docker service update --replicas 3 odoo_odoo

# Watch rollout
docker service ps odoo_odoo

# Designate cron worker (run once per scaling change):
# On the first replica (Cron):
docker service update --env-add ODOO_CRON_MODE=2 odoo_odoo

# On other replicas:
docker service update --env-add ODOO_CRON_MODE=0 odoo_odoo
```

### Rolling Updates

```bash
# Push new image to registry
docker push your-registry/odoo-custom:16.0

# Force update (pulls latest image)
docker service update --image your-registry/odoo-custom:16.0 --force odoo_odoo

# Monitor rollout
docker service logs odoo_odoo -f
```

## Important: Cron Job Configuration

**Critical for multi-replica deployments**: Scheduled jobs must run on only ONE instance.

**Problem**: Without this, invoicing, email reminders, and other scheduled tasks run on ALL replicas → duplicate work, data corruption.

**Solution**: Use `ODOO_CRON_MODE` environment variable

```bash
# Example: 2 replicas setup

# Identify first replica (will be cron worker):
docker service ps odoo_odoo

# Set cron mode for replicas:
# Replica 1: ODOO_CRON_MODE=2 (handles all cron jobs)
# Replica 2+: ODOO_CRON_MODE=0 (regular app servers)
```

In `odoo.conf`, the setting is already configured to use this:
```ini
max_cron_threads = ${ODOO_CRON_MODE:0}
```

## Known Issues & Fixes

### Issue: Users Getting Logged Out

**Fix**: Sticky sessions are configured via Traefik labels in `docker-compose.yml`. Verify these labels exist:

```yaml
- traefik.http.services.odoo.loadbalancer.sticky=true
- traefik.http.services.odoo.loadbalancer.sticky.cookie.httponly=true
- traefik.http.services.odoo.loadbalancer.sticky.cookie.secure=true
```

### Issue: Files Uploaded to Replica A Not Visible on Replica B

**Fix**: Use S3 storage (see S3 Storage Setup section above). Local volumes only work for single-replica deployments.

### Issue: Duplicate Scheduled Jobs Running

**Fix**: Configure `ODOO_CRON_MODE` properly (see Important: Cron Job Configuration section above).

### Issue: Slow Performance

**Checks**:
```bash
# Monitor resource usage
docker stats

# Check Odoo worker count
curl http://localhost:8069/web/health | jq '.workers'

# Increase if needed in config/odoo.conf:
workers = 8  # (default: 5)
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker service logs odoo_odoo --tail 200 -f

# Verify image exists
docker pull your-registry/odoo-custom:16.0

# Check database connectivity
docker run --rm postgres:15 psql -h $DB_HOST -U $DB_USER -c "SELECT 1"

# Restart service
docker service update --force odoo_odoo
```

### Health Checks Failing

```bash
# Test health endpoint manually
curl -s http://localhost:8069/web/health

# Check container logs
docker logs <container_id>

# Increase health check grace period in docker-compose.yml if needed
```

### S3 Connection Issues

```bash
# Verify credentials
export AWS_ACCESS_KEY_ID=$S3_ACCESS_KEY
export AWS_SECRET_ACCESS_KEY=$S3_SECRET_KEY

# Test bucket access
aws s3 ls s3://$S3_BUCKET

# Check bucket policy and permissions
aws s3api get-bucket-policy --bucket $S3_BUCKET
```

## Security

### Important Security Notes

1. **Never commit `.env` to git**:
   ```bash
   echo ".env" >> .gitignore
   ```

2. **Use Docker Secrets for sensitive data** (recommended for Swarm):
   ```bash
   docker secret create db_password - < password.txt
   ```

3. **Generate strong passwords**:
   ```bash
   openssl rand -hex 32  # Database password
   openssl rand -hex 64  # SECRET_KEY_BASE
   ```

4. **Enable S3 encryption**:
   ```bash
   aws s3api put-bucket-encryption \
     --bucket my-odoo-attachments-prod \
     --server-side-encryption-configuration '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}'
   ```

5. **Restrict S3 bucket access**:
   ```bash
   aws s3api put-public-access-block \
     --bucket my-odoo-attachments-prod \
     --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   ```

6. **Database security**:
   - Enable Multi-AZ on RDS
   - Enable encryption at rest
   - Restrict security group to swarm nodes only
   - Enable automated backups (30-day retention)

7. **Session security** (already configured in `odoo.conf`):
   ```ini
   session_cookie_secure = True      # HTTPS only
   session_cookie_http_only = True   # No JavaScript access
   proxy_mode = True                 # Trust X-Forwarded headers
   ```

8. **Disable database listing** (already configured):
   ```ini
   list_db = False  # Production security requirement
   ```

## Monitoring

```bash
# View running services
docker service ls

# Check replica status
docker service ps odoo_odoo

# Live service logs
docker service logs odoo_odoo -f

# Service details
docker service inspect odoo_odoo

# Resource usage
docker stats $(docker ps -q -f "label=com.docker.swarm.service.name=odoo_odoo")
```

## Deployment Checklist

Before deploying to production:

- [ ] PostgreSQL RDS created and accessible (test connection)
- [ ] Multi-AZ enabled on RDS
- [ ] S3 bucket created with proper permissions
- [ ] Strong passwords generated (32+ chars for DB, 64+ for SECRET_KEY)
- [ ] `.env` file created from template with all values filled
- [ ] `.env` file NOT committed to git
- [ ] Docker image built and pushed to registry
- [ ] Database initialized: `odoo --stop-after-init`
- [ ] Traefik deployed and working
- [ ] SSL certificates configured
- [ ] Sticky sessions configured in docker-compose.yml
- [ ] Health checks working: `curl http://localhost:8069/web/health`
- [ ] All replicas healthy: `docker service ps odoo_odoo`
- [ ] File uploads working via S3
- [ ] Scheduled jobs running on cron replica only
- [ ] User logins persist across replicas
- [ ] Backups configured and tested

## Quick Commands

```bash
# Deploy
docker stack deploy -c docker-compose.yml odoo

# Scale up
docker service update --replicas 3 odoo_odoo

# Update image
docker service update --image registry/odoo-custom:16.0 --force odoo_odoo

# Rollback
docker service rollback odoo_odoo

# View logs
docker service logs odoo_odoo -f

# Remove stack
docker stack rm odoo
```

## IMPORTANT

If encountering "Cache ir_attachment: IOError: [Errno 2] No such file or directory" or related error:

1. Make sure S3 storage is properly configured
2. Or via database directly:
   ```bash
   docker-compose exec db bash
   psql -U odoo -d <dbname>
   DELETE FROM public.ir_attachment;
   ```
3. Then in Odoo UI: Apps → Search "Base" → Upgrade
