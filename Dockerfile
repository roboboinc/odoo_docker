# Stage 1: Build Odoo image
FROM odoo:16 AS odoo

USER root

# Install any additional dependencies if needed
# Install unzip and pandas
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        python3-pip \
    && apt-get clean

# Upgrade pip, setuptools, wheel, and install specific Cython version
RUN pip3 install --upgrade pip setuptools wheel Cython==0.29.33

# Install specific Python packages and handle numpy incompatibility
RUN pip3 install pandas==2.0.3 xlrd==1.2.0 numpy==1.24.3

# Install wkhtmltopdf and dependencies
RUN apt-get install -y \
    curl \
    wget \
    libxrender1 \
    libxext6 \
    libssl-dev \
    libfontconfig1 \
    xfonts-base \
    xfonts-75dpi \
    fonts-dejavu-core \
    wkhtmltopdf

USER odoo

# Copy custom modules from the local directory to the Odoo addons directory
# WORKDIR /mnt/extra-addons/
# RUN wget -O ks_curved_backend_theme.zip https://wfxvdg.am.files.1drv.com/y4m-VBpOSiFQI53xsv0KHz4n7JBNLehWKYTeRFYUXhJBIqNq4NV_m4EIlcPQTFssjA59Fs1h2INOx53Wyrf9Ax3MskOjZ6-PTWtSoKs10F6U_onDNc1TLCpGkCngKMqg8_yiFVZ5WBCILtUtZ3CKc1CNQ-FiD2sd7brkP6VAtMTYwLqsvcKT1xOy8vezVwkVSEIayk8URj7SPCOInPpSLifWA 
# RUN unzip ks_curved_backend_theme.zip

# RUN wget -O ks_dashboard_ninja.zip https://m2qflg.am.files.1drv.com/y4mBQsd12zho59J45RU0HO8sXRlHZL6uNdoARPdibLdntR_KN2fICJ0ZeKAZBnewet-tPwos4FGQ75S9KtcAF9oxmxseslO_bhmCA9vqnXjA7CPLTYQWm7f9zZCRbiDaX-As1IxdAeWqQXY8pd7YFRISdOJR2ow9U1RdMPH47V3vyvKEEypYDINGFh8xxL6O3kHE_5xMZXSdfwNvnDnNsPtCg
# RUN unzip ks_dashboard_ninja.zip

# RUN git clone https://github.com/roboboinc/linhafala_odoo.git

# # Copy your custom Odoo configuration file (odoo.conf) to the container
# COPY config/odoo.conf /etc/odoo/odoo.conf

# # Expose the port(s) Odoo is listening on (usually 8069)
# EXPOSE 8069

# # Start Odoo server with custom configuration
# CMD ["odoo", "--config=/etc/odoo/odoo.conf"]
