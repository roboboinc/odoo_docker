# Docker Swarm Deployment Guide

## Changes Made for Swarm Compatibility

### Problem
Docker Swarm doesn't support bind mounts (local paths like `./config` or `../odoo_custom_modules`) because services can be scheduled on any node in the swarm where those paths don't exist.

### Solution
Built a custom Docker image that bundles:
- Odoo configuration (`config/odoo.conf`)
- Custom modules (`../odoo_custom_modules`)

## Build & Deploy Instructions

### 1. Build the Custom Image

From the `odoo_docker` directory:

```bash
docker build -t odoo-custom:16.0 .
```

### 2. Push to Registry (For Multi-Node Swarm)

If deploying to multiple nodes, push to a container registry:

```bash
# Tag for your registry
docker tag odoo-custom:16.0 your-registry.com/odoo-custom:16.0

# Push to registry
docker push your-registry.com/odoo-custom:16.0
```

Then update `docker-compose.yml`:
```yaml
services:
  odoo:
    image: your-registry.com/odoo-custom:16.0
    # Remove the 'build' section
```

### 3. Deploy to Swarm

```bash
# Initialize swarm (if not already done)
docker swarm init

# Create external network (if not exists)
docker network create --driver overlay traefik-network

# Deploy the stack
docker stack deploy -c docker-compose.yml odoo
```

### 4. Verify Deployment

```bash
# Check services
docker service ls

# Check service logs
docker service logs odoo_odoo -f

# Check if service is running
docker service ps odoo_odoo
```

## Updating the Application

When you modify config or custom modules:

### 1. Rebuild the Image
```bash
docker build -t odoo-custom:16.0 .
```

### 2. Push to Registry (if using multi-node)
```bash
docker push your-registry.com/odoo-custom:16.0
```

### 3. Update the Service
```bash
# Force update to pull new image
docker service update --image odoo-custom:16.0 --force odoo_odoo
```

Or redeploy the entire stack:
```bash
docker stack deploy -c docker-compose.yml odoo
```

## Rollback

If something goes wrong:
```bash
# Rollback to previous version
docker service rollback odoo_odoo
```

## Single Node Deployment

For single-node swarm on AWS EC2, the built image stays local:

```bash
# Build image
docker build -t odoo-custom:16.0 .

# Deploy stack
docker stack deploy -c docker-compose.yml odoo
```

## Important Notes

1. **Data Persistence**: The `odoo-web-data` volume persists Odoo data across deployments
2. **Environment Variables**: Ensure `.env` file is present with proper configuration
3. **Network**: The `traefik-network` must exist before deployment
4. **Secrets**: For production, consider using Docker secrets for sensitive data

## Troubleshooting

### Service Fails to Start
```bash
# Check service events
docker service ps odoo_odoo --no-trunc

# Check logs
docker service logs odoo_odoo --tail 100
```

### Image Not Found
Ensure the image is:
- Built locally (for single node), OR
- Available in a registry accessible by all swarm nodes

### Volume Mount Issues
If you see volume-related errors, verify:
```bash
# List volumes
docker volume ls

# Inspect volume
docker volume inspect odoo_odoo-web-data
```
