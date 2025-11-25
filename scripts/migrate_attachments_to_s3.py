#!/usr/bin/env python3
"""
Migration script to move existing Odoo filestore attachments to DigitalOcean Spaces
Run this script after configuring S3 environment variables.
"""

import os
import sys
import logging

# Add Odoo to path if needed
if '/opt/odoo' not in sys.path:
    sys.path.insert(0, '/opt/odoo')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_s3_config():
    """Verify S3 environment variables are set"""
    required_vars = ['S3_ENDPOINT_URL', 'S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY', 'S3_BUCKET_NAME']
    missing = [var for var in required_vars if not os.environ.get(var)]
    
    if missing:
        logger.error("Missing required environment variables: %s", ', '.join(missing))
        return False
    
    logger.info("S3 configuration verified")
    return True

def test_s3_connection():
    """Test S3 connection and bucket access"""
    try:
        import boto3
        from botocore.exceptions import ClientError
        
        s3_client = boto3.client(
            's3',
            endpoint_url=os.environ.get('S3_ENDPOINT_URL'),
            aws_access_key_id=os.environ.get('S3_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('S3_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('S3_REGION', 'nyc3')
        )
        
        bucket_name = os.environ.get('S3_BUCKET_NAME')
        
        # Test bucket access
        s3_client.head_bucket(Bucket=bucket_name)
        logger.info("Successfully connected to S3 bucket: %s", bucket_name)
        return True
        
    except ClientError as e:
        logger.error("S3 connection failed: %s", e)
        return False
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return False

def run_migration():
    """Run the attachment migration using Odoo's ORM"""
    try:
        import odoo
        from odoo import api, SUPERUSER_ID
        
        # Initialize Odoo environment
        db_name = os.environ.get('ODOO_DB_NAME', 'postgres')
        
        with odoo.api.Environment.manage():
            registry = odoo.registry.Registry.new(db_name)
            
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                
                # Get the ir.attachment model
                attachment_model = env['ir.attachment']
                
                # Run migration
                result = attachment_model._migrate_to_s3(batch_size=50)
                
                logger.info("Migration completed successfully:")
                logger.info("  Total attachments: %d", result['total'])
                logger.info("  Successfully migrated: %d", result['migrated'])
                logger.info("  Errors: %d", result['errors'])
                
                return result['errors'] == 0
                
    except Exception as e:
        logger.error("Migration failed: %s", e)
        return False

def main():
    """Main migration workflow"""
    logger.info("Starting Odoo filestore to S3 migration...")
    
    # Step 1: Check configuration
    if not check_s3_config():
        sys.exit(1)
    
    # Step 2: Test S3 connection
    if not test_s3_connection():
        sys.exit(1)
    
    # Step 3: Run migration
    if run_migration():
        logger.info("Migration completed successfully!")
        sys.exit(0)
    else:
        logger.error("Migration failed!")
        sys.exit(1)

if __name__ == '__main__':
    main()