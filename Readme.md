# This is an inital repo used to deploy a semi-production ready docker-compose based Odoo Application

## Setup configuration:
This docker-compose derives on instruction available on https://www.cloudbooklet.com/install-odoo-using-docker-nginx-on-ubuntu-20-04-aws/
The main purpose is to enable a user to deploy an Odoo instance with an external cloud based Postgres database. 

## Requirements:
- Getting started info from: https://hub.docker.com/_/odoo
- Install or use a Docker + docker-compose environment setup or server (Hint: Digital Ocean [DO] and other cloud service providers do enable images with this pre-installed)

## Installation steps:
*1 - Clone the repo 
*2 - Create or copy .env_template into .env file in the root directory of this project
*3 - Edit the enviornment variables to match you repository settings, specifically: database host, user and password that corresponds to your Postgres installation and the domain for certbot. We use DO's managed Postgres DB, you may use whatever you feel most suitable.
*3.1 - Ensure that your desired domain is pointing to the server that will host your application, so that it can discover and create SSL certs with letsencrypt
*4 - Spin up the script with `docker-compose up` 
*5 - Update the nginx/conf/default_ssl.conf file with details that 
*5 - Make relevant amendments of the nginx/conf/default.conf file to match your specific domain setup, for instance: replacing all instances of odoo.yourdomain.com to match the domain you setup on step 3.1
*6 - Restart nginx with the command `docker restart nginx`

You should have your app running and with an SSL on the configured DOmain.
