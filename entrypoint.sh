#!/bin/bash
# Entrypoint script for Odoo Docker container
# Generates odoo.conf from environment variables for production deployments

set -e

# Default values
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
    echo "Required: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME"
    exit 1
fi

# Create/generate odoo.conf file from template
cat > /etc/odoo/odoo.conf << EOF
[options]
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/var/lib/odoo/.local/share/Odoo/addons/16.0,/mnt/extra-addons

; Important security restriction in production!
list_db = False

; Database configuration - sourced from environment variables
db_host = ${DB_HOST}
db_port = ${DB_PORT}
db_user = ${DB_USER}
db_password = ${DB_PASSWORD}
db_name = ${DB_NAME}

EOF

# Add S3 configuration if provided
if [ -n "$S3_ENDPOINT" ] && [ -n "$S3_BUCKET" ] && [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    cat >> /etc/odoo/odoo.conf << EOF
; S3 Attachment Storage Configuration
ir_attachment.location = s3://odoo-attachments
s3_server = ${S3_ENDPOINT}
s3_bucket = ${S3_BUCKET}
s3_access_key_id = ${S3_ACCESS_KEY}
s3_secret_access_key = ${S3_SECRET_KEY}

EOF
fi

# Add rest of configuration
cat >> /etc/odoo/odoo.conf << EOF
; Session Configuration for Multi-Replica Deployments
; Session data is stored in database by default

; Optimization Settings for production
limit_memory_hard = 1677721600
limit_memory_soft = 629145600
limit_request = 8192
limit_time_cpu = 600
limit_time_real = 1200

; Cron Configuration - IMPORTANT FOR SWARM MODE
; Set to 2 on the designated cron worker instance
; Set to 0 on all other instances to prevent duplicate job execution
max_cron_threads = ${ODOO_CRON_MODE}

; Worker Configuration
workers = 5

; Security Settings
session_cookie_secure = True
session_cookie_http_only = True
proxy_mode = True
dev_mode = False

; Logging
log_level = ${LOG_LEVEL}
EOF

echo "✓ Generated /etc/odoo/odoo.conf from environment variables"

# Execute the Odoo command
exec "$@"
