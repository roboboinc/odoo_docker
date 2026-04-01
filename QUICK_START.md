# Quick Start Guide - Odoo Production Deployment

## 5-Minute Setup

### 1. Prepare Environment (2 min)

```bash
# Copy template
cp .env.template .env

# Generate credentials
DB_PASS=$(openssl rand -hex 32)
SECRET_KEY=$(openssl rand -hex 64)

# Edit .env with your values
# Key fields to update:
# DB_HOST=your-rds-endpoint.eu-west-1.rds.amazonaws.com
# DB_USER=odoo
# DB_PASSWORD=$DB_PASS
# SECRET_KEY_BASE=$SECRET_KEY
# S3_ENDPOINT=https://s3.amazonaws.com (or your provider)
# S3_BUCKET=my-odoo-attachments-prod
# S3_ACCESS_KEY=your-aws-key
# S3_SECRET_KEY=your-aws-secret

nano .env  # or use your editor
```

### 2. Build Image (1 min)

```bash
# Build locally
docker build -t odoo-custom:16.0 .

# For multi-node swarm, push to registry:
docker tag odoo-custom:16.0 your-registry.com/odoo-custom:16.0
docker push your-registry.com/odoo-custom:16.0
```

### 3. Deploy (2 min)

```bash
# Initialize database (one-time)
docker run --rm --env-file .env odoo-custom:16.0 odoo --stop-after-init

# Deploy to swarm
docker stack deploy -c docker-compose.yml odoo

# Verify deployment
watch docker service ps odoo_odoo
```

That's it! Your multi-replica Odoo is running.

---

## Verify It's Working

```bash
# Check replicas are healthy
docker service ps odoo_odoo

# Test health endpoint
curl http://localhost:8069/web/health

# View logs
docker service logs odoo_odoo -f
```

---

## Scaling Up

```bash
# Add another replica
docker service update --replicas 3 odoo_odoo

# View the new replica starting
docker service ps odoo_odoo
```

---

## Important: S3 Setup (Optional but Recommended)

Without S3, files uploaded to one replica won't be visible on others.

```bash
# Create AWS S3 bucket
aws s3 mb s3://my-odoo-attachments-prod --region eu-west-1

# Create IAM user
aws iam create-user --user-name odoo-app
aws iam create-access-key --user-name odoo-app

# Note the Access Key ID and Secret Access Key, add to .env:
# S3_ENDPOINT=https://s3.amazonaws.com
# S3_BUCKET=my-odoo-attachments-prod
# S3_ACCESS_KEY=AKIA...
# S3_SECRET_KEY=wJal...

# Uncomment S3 lines in config/odoo.conf
# Rebuild and redeploy image
docker build -t odoo-custom:16.0 .
docker push your-registry.com/odoo-custom:16.0
docker service update --image your-registry.com/odoo-custom:16.0 --force odoo_odoo
```

---

## Important: Cron Job Configuration

With multiple replicas, scheduled jobs (invoicing, etc.) would run on ALL replicas → duplicates.

Solution: Designate ONE instance as cron worker:

```bash
# Set ODOO_CRON_MODE=2 on first replica (cron worker)
# Set ODOO_CRON_MODE=0 on other replicas (app servers)

# This is controlled via environment variables in .env
# Or set directly on service:
docker service update --env-add ODOO_CRON_MODE=2 odoo_odoo
```

---

## Troubleshooting

### Users Getting Logged Out Between Requests?
- Sticky sessions should be automatic via Traefik
- Verify docker-compose.yml has sticky session labels
- Restart service: `docker service update --force odoo_odoo`

### Files Uploaded Not Visible on Other Replicas?
- Enable S3 storage (see S3 Setup section above)
- Rebuild and redeploy image after configuring S3

### Service Won't Start?
```bash
# Check logs
docker service logs odoo_odoo -f

# Verify database is accessible
docker run --rm postgres:15 psql -h $DB_HOST -U $DB_USER -c "SELECT 1"

# Restart
docker service update --force odoo_odoo
```

### Duplicate Scheduled Jobs Running?
- Configure ODOO_CRON_MODE properly (set to 2 on ONE instance, 0 on others)
- Check odoo.conf has: `max_cron_threads = ${ODOO_CRON_MODE:0}`

---

## Common Commands

```bash
# View status
docker service ls
docker service ps odoo_odoo

# View logs
docker service logs odoo_odoo -f

# Scale
docker service update --replicas 3 odoo_odoo

# Update image
docker service update --image your-registry/odoo-custom:16.0 --force odoo_odoo

# Rollback
docker service rollback odoo_odoo

# Remove
docker stack rm odoo
```

---

## Full Documentation

See [README.md](README.md) for complete deployment guide covering:
- Architecture details
- Prerequisites checklist
- Multi-node swarm setup
- S3 setup for all providers (AWS, DigitalOcean, MinIO)
- Monitoring and scaling
- Security best practices
- Troubleshooting guide

---

## Key Files

- **docker-compose.yml** - Swarm deployment configuration
- **.env.template** - Environment variable template
- **config/odoo.conf** - Odoo configuration
- **Dockerfile** - Custom image with bundled config
- **README.md** - Complete deployment guide
- **DEPLOYMENT_FIXES_SUMMARY.md** - Changes applied for production readiness

---

## Next Steps

1. ✅ Setup .env with your credentials
2. ✅ Build and push Docker image
3. ✅ Deploy to swarm
4. ✅ Configure S3 storage (recommended)
5. ✅ Set up monitoring and alerts
6. ✅ Configure backups
7. ✅ Test failover scenarios

---

Need help? Check the [README.md](README.md) for detailed documentation.
