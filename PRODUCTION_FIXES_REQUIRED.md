# Production Docker Compose - Analysis & Applied Fixes

## ✅ FIXES APPLIED

### 1. ✅ Fixed Redis Healthcheck Authentication
**Problem:** Healthcheck would fail because Redis requires password authentication.
**Applied:** Updated healthcheck to use `-a redis_password`

### 2. ✅ Removed Build/Image Conflict  
**Problem:** Both `image` and `build` specified - Docker would ignore build directive.
**Applied:** Commented out `build: .` to use pre-built image in production.

### 3. ✅ Fixed Network Configuration
**Problem:** External network cannot have driver specified.
**Applied:** Removed `driver: overlay` from `traefik-network`.

### 4. ✅ Removed Development Flag from FastAPI
**Problem:** Using `--reload` in production causes performance degradation.
**Applied:** Removed `--reload` and added `--workers 4` for production performance.

### 5. ✅ Improved Service Dependencies
**Applied:** Added `receevi-web` as dependency for `receevi-sidekiq` for proper startup order.

## ⚠️ REMAINING WARNINGS (Manual Action Required)

### Security: Weak/Placeholder Secrets in .env
**CRITICAL - Must fix before production deployment:**
### Security: Weak/Placeholder Secrets in .env
**CRITICAL - Must fix before production deployment:**

1. **SECRET_KEY_BASE** still has placeholder value:
   ```bash
   # Generate strong secret:
   openssl rand -hex 64
   ```

2. **Weak passwords** in .env:
   - `PASSWORD=Password` (too weak)
   - `REDIS_PASSWORD=redis_password` (default value)

3. **Update production .env file:**
   ```env
   SECRET_KEY_BASE=<your-generated-64-char-hex-string>
   PASSWORD=<strong-database-password>
   REDIS_PASSWORD=<strong-redis-password>
   ```

### Chatwoot Configuration for Production

1. **FRONTEND_URL** must match production domain:
   ```env
   FRONTEND_URL=https://oc.linhafala.org.mz
   ```

2. **Database Initialization Required:**
   Before first deployment, initialize Chatwoot database on Digital Ocean PostgreSQL:
   ```bash
   docker compose run --rm receevi-web bundle exec rails db:prepare
   ```

3. **Ensure pgvector extension** is enabled on Digital Ocean database:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

### FastAPI Configuration

**Consideration:** Port 8000 is directly exposed. If you want all traffic through Traefik:
- Remove `ports:` section
- Add Traefik labels for routing

Example (optional):
```yaml
fastapi:
  build: ./fastapi
  # Remove direct port exposure:
  # ports:
  #   - "8000:8000"
  labels:
    - "traefik.enable=true"
    - "traefik.http.services.fastapi.loadbalancer.server.port=8000"
    - "traefik.http.routers.fastapi.rule=Host(`api.linhafala.org.mz`)"
    - "traefik.http.routers.fastapi.entrypoints=websecure"
    - "traefik.http.routers.fastapi.tls.certresolver=myresolver"
```

## 📋 Pre-Deployment Checklist

Production .env configuration:
- [ ] Verify Digital Ocean PostgreSQL connection details in .env
- [ ] Generate and set strong SECRET_KEY_BASE (use `openssl rand -hex 64`)
- [ ] Update all passwords to strong values
- [ ] Fix FRONTEND_URL to https://oc.linhafala.org.mz
- [ ] Set RECEEVI_URL=http://receevi-web:3000 (internal service URL)

Database preparation:
- [ ] Enable pgvector extension on Digital Ocean PostgreSQL
- [ ] Run Chatwoot database initialization: `docker compose run --rm receevi-web bundle exec rails db:prepare`

Infrastructure verification:
- [ ] Verify ./addons-extra directory exists on production server
- [ ] Verify ./config directory exists with odoo.conf
- [ ] Ensure Traefik network exists: `docker network ls | grep traefik`
- [ ] Verify Digital Ocean PostgreSQL is accessible from production server
- [ ] Test Redis connectivity

## ✅ Current Status

**Docker Compose Configuration:** ✅ READY for production
- All syntax errors fixed
- Service dependencies correct
- Network configuration valid
- Production optimizations applied

**Environment Configuration:** ⚠️ REQUIRES ATTENTION
- Secrets must be updated manually
- Database must be initialized
- Environment URLs must match production domains

## 🚀 Deployment Steps

1. **Update .env file** with production values (secrets, URLs)

2. **Initialize Chatwoot database** (one-time):
   ```bash
   docker compose run --rm receevi-web bundle exec rails db:prepare
   ```

3. **Deploy services:**
   ```bash
   docker compose up -d
   ```

4. **Verify all services:**
   ```bash
   docker compose ps
   docker compose logs -f
   ```

5. **Test endpoints:**
   - https://odoo.linhafala.org.mz
   - https://oc.linhafala.org.mz
   - http://your-server:8000 (FastAPI)

