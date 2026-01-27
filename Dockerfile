# Stage 1: Build Odoo image for production
FROM odoo:16.0

USER root

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        python3-pip \
        curl \
        wget \
        libxrender1 \
        libxext6 \
        libssl-dev \
        libfontconfig1 \
        xfonts-base \
        xfonts-75dpi \
        fonts-dejavu-core \
        wkhtmltopdf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install Python dependencies
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel Cython==0.29.33

# Install specific Python packages for custom modules
RUN pip3 install --no-cache-dir \
    pandas==2.0.3 \
    xlrd==1.2.0 \
    numpy==1.24.3 \
    python-jose==3.3.0

# Copy Odoo configuration file
COPY --chown=odoo:odoo config/odoo.conf /etc/odoo/odoo.conf

# Copy custom modules into the container
# This bundles the modules so they work in swarm mode without bind mounts
COPY --chown=odoo:odoo odoo_custom_modules /mnt/extra-addons

USER odoo

# Expose Odoo port
EXPOSE 8069

# Use the configuration file
CMD ["odoo", "--config=/etc/odoo/odoo.conf"]
