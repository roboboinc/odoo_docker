# Environment Variables Fix - Technical Documentation

## Problem

Odoo's `.conf` configuration files do not support shell-style environment variable substitution like `${DB_HOST}` or `${DB_PASSWORD}`.

**What doesn't work:**
```ini
# This does NOT get replaced with actual values:
db_host = ${DB_HOST}
db_password = ${DB_PASSWORD}
```

Result: Odoo would literally try to connect to a host named `${DB_HOST}`, causing connection failures.

## Solution

We implemented an **entrypoint script** that generates the `odoo.conf` file at container startup from environment variables. This is the standard Docker pattern for environment-driven configuration.

## How It Works

### Flow Diagram

```
Docker Container Start
        ↓
entrypoint.sh Executes
        ↓
Read Environment Variables (.env)
        ↓
Validate Required Variables
        ↓
Generate /etc/odoo/odoo.conf
        ↓
Start Odoo Process
```

### Step-by-Step

1. **Container starts** with `docker-compose up` or `docker service create`

2. **Dockerfile's ENTRYPOINT** runs `entrypoint.sh` first:
   ```dockerfile
   ENTRYPOINT ["/entrypoint.sh"]
   CMD ["odoo", "--config=/etc/odoo/odoo.conf"]
   ```

3. **entrypoint.sh script**:
   - Reads environment variables from `.env` file
   - Validates required variables (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
   - Generates `/etc/odoo/odoo.conf` with actual values
   - Adds optional S3 configuration if provided
   - Executes `odoo` command with generated config

4. **Odoo starts** with the dynamically generated configuration

## Files Changed

### 1. Created: `entrypoint.sh`

```bash
#!/bin/bash
# Entrypoint script for Odoo Docker container
# Generates odoo.conf from environment variables for production deployments

set -e

# Read environment variables
DB_PORT=${DB_PORT:-5432}
DB_HOST=${DB_HOST:-db}
DB_USER=${DB_USER:-odoo}
DB_PASSWORD=${DB_PASSWORD:-}
DB_NAME=${DB_NAME:-odoo}
ODOO_CRON_MODE=${ODOO_CRON_MODE:-0}
LOG_LEVEL=${LOG_LEVEL:-info}
S3_ENDPOINT=${S3_ENDPOINT:-}
S3_BUCKET=${S3_BUCKET:-}
S3_ACCESS_KEY=${S3_ACCESS_KEY:-}
S3_SECRET_KEY=${S3_SECRET_KEY:-}

# Validate required variables
if [ -z "$DB_HOST" ] || [ -z "$DB_USER" ] || [ -z "$DB_PASSWORD" ] || [ -z "$DB_NAME" ]; then
    echo "ERROR: Missing required database environment variables"
    exit 1
fi

# Generate odoo.conf file with actual values
cat > /etc/odoo/odoo.conf << EOF
[options]
db_host = ${DB_HOST}
db_port = ${DB_PORT}
db_user = ${DB_USER}
db_password = ${DB_PASSWORD}
db_name = ${DB_NAME}
max_cron_threads = ${ODOO_CRON_MODE}
log_level = ${LOG_LEVEL}
...
EOF

# Add S3 configuration if all S3 variables provided
if [ -n "$S3_ENDPOINT" ] && [ -n "$S3_BUCKET" ] && [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    # Append S3 config to generated file
fi

# Start Odoo with the generated config
exec "$@"
```

**Key features:**
- Validates all required database variables
- Uses bash parameter expansion for default values: `${VAR:-default}`
- Conditionally adds S3 configuration only if all S3 variables provided
- Uses `exec "$@"` to pass control to the main Odoo process
- Error handling with `set -e` to fail fast on any error

### 2. Updated: `Dockerfile`

```dockerfile
# Copy entrypoint script to generate config from environment variables
COPY --chown=root:root odoo_docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER odoo

# Expose Odoo port
EXPOSE 8069

# Use entrypoint script to generate config and start Odoo
ENTRYPOINT ["/entrypoint.sh"]
CMD ["odoo", "--config=/etc/odoo/odoo.conf"]
```

Changes:
- Copy `entrypoint.sh` into image
- Make it executable with `chmod +x`
- Set as `ENTRYPOINT` (runs first, before CMD)
- Odoo config file no longer needs to be copied (it's generated at runtime)

### 3. Updated: `config/odoo.conf`

Changed from actual configuration to reference template:

```ini
[options]
; DEPRECATED: This file is now used as reference only.
; The actual odoo.conf is generated at runtime from environment variables.
; See entrypoint.sh for the configuration generation logic.

; This template shows the configuration structure:
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/var/lib/odoo/.local/share/Odoo/addons/16.0,/mnt/extra-addons
list_db = False

; Database configuration - sourced from environment variables
; DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
...
```

Reason: The actual config is now generated at runtime, so this file serves as documentation of the configuration structure.

### 4. Updated: `docker-compose.yml`

```yaml
command: odoo --config=/etc/odoo/odoo.conf
```

Changed from:
```yaml
command: --log-level=info -u linhaverde_odoo,linhaverde_offline
```

Reason: Let the entrypoint script handle configuration. The `--config` flag points to the file it will generate.

## Environment Variables

All configuration is now environment-driven. Set these in `.env` file:

### Required
- `DB_HOST` - PostgreSQL hostname
- `DB_PORT` - PostgreSQL port (default: 5432)
- `DB_USER` - PostgreSQL username
- `DB_PASSWORD` - PostgreSQL password (MUST be strong)
- `DB_NAME` - Database name

### Optional
- `LOG_LEVEL` - Logging level (default: info)
- `ODOO_CRON_MODE` - Cron worker mode (default: 0, set to 2 on cron instance)
- `S3_ENDPOINT` - S3 endpoint URL (if using S3 storage)
- `S3_BUCKET` - S3 bucket name
- `S3_ACCESS_KEY` - S3 access key
- `S3_SECRET_KEY` - S3 secret key

## Benefits

### Security
✅ **No hardcoded credentials in Docker image** - All secrets come from `.env` file  
✅ **Easy secret rotation** - Change `.env`, redeploy, no rebuild needed  
✅ **CI/CD friendly** - Secrets never stored in image registry  

### Flexibility
✅ **Environment-specific configs** - Same image, different `.env` per environment  
✅ **Dynamic S3 setup** - Enable/disable S3 without rebuilding  
✅ **Easy scaling** - All replicas use same config via environment variables  

### Reliability
✅ **Validation at startup** - Fails fast if required variables missing  
✅ **Default values** - Optional variables have sensible defaults  
✅ **Conditional features** - S3 only enabled if all variables provided  

## Testing

### Test Locally

```bash
# Create .env file with test values
cat > .env << EOF
DB_HOST=localhost
DB_PORT=5432
DB_USER=odoo
DB_PASSWORD=test_password
DB_NAME=test_odoo
LOG_LEVEL=info
ODOO_CRON_MODE=0
EOF

# Build image
docker build -t odoo-custom:16.0 .

# Run to verify config generation
docker run --rm --env-file .env odoo-custom:16.0 cat /etc/odoo/odoo.conf

# Should output the generated configuration with actual values
```

### Test in Docker Compose

```bash
# Deploy with docker-compose
docker-compose up -d

# Verify config was generated correctly
docker-compose exec odoo cat /etc/odoo/odoo.conf

# Check logs
docker-compose logs odoo | grep "Generated /etc/odoo/odoo.conf"
```

### Test in Docker Swarm

```bash
# Deploy to swarm
docker stack deploy -c docker-compose.yml odoo

# Check logs from any replica
docker service logs odoo_odoo | grep "Generated /etc/odoo/odoo.conf"

# Verify service is running
docker service ps odoo_odoo
```

## Troubleshooting

### Error: "Missing required database environment variables"

**Cause**: One or more of DB_HOST, DB_USER, DB_PASSWORD, DB_NAME not set in `.env`

**Fix**:
```bash
# Check .env file
cat .env

# Ensure all required variables are set
grep -E "^DB_(HOST|USER|PASSWORD|NAME)=" .env
```

### Container exits immediately

**Debug**:
```bash
# View entrypoint.sh output
docker logs <container_id>

# Should see "Generated /etc/odoo/odoo.conf" message
```

### Configuration values not being used

**Debug**:
```bash
# Verify generated config
docker exec <container_id> cat /etc/odoo/odoo.conf

# Should show actual values, not variable names like ${DB_HOST}
```

### Odoo can't connect to database

**Check**:
```bash
# Verify generated config has correct values
docker exec <container_id> grep "db_host\|db_user\|db_name" /etc/odoo/odoo.conf

# Test connection from container
docker exec <container_id> psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT 1"
```

## Migration from Old Setup

If you were using hardcoded config file:

1. **The image still builds** - `entrypoint.sh` is included
2. **No code changes needed** - Just update `.env` with your values
3. **Rebuild image**:
   ```bash
   docker build -t odoo-custom:16.0 .
   ```
4. **Deploy with .env**:
   ```bash
   docker stack deploy -c docker-compose.yml odoo
   ```

The entrypoint script will automatically generate the config at runtime.

## Summary

| Aspect | Old Approach | New Approach |
|--------|--------------|--------------|
| Config location | `config/odoo.conf` (file in image) | Generated at runtime from `.env` |
| Environment support | ❌ Not supported (literals used) | ✅ Full environment variable support |
| Secrets in image | ❌ Yes (hardcoded) | ✅ No (from .env file) |
| Flexibility | ❌ Rebuild for each environment | ✅ Same image, different .env |
| S3 configuration | ❌ Commented out, manual edit | ✅ Automatic if env vars set |
| Security | ❌ Secrets in Docker image | ✅ Secrets in .env file |
| Deployment complexity | Medium | Simple |

---

**The Fix enables**: Environment-driven configuration, better security, and easy multi-environment deployments.
