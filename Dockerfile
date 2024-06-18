# Stage 1: Build Odoo image
FROM odoo:16 AS odoo

USER root

# Install any additional dependencies if needed
# Install unzip and pandas
RUN apt-get update
RUN apt-get install --no-install-recommends -y wget git unzip \
    && apt-get clean
RUN pip3 install pandas==2.0.3 xlrd==1.2.0 \
    && pip3 install pip setuptools wheel Cython==3.0.0a10 \
    && pip3 install gevent==21.12.0 --no-build-isolation \
    && pip3 list

USER odoo

# Copy custom modules from the local directory to the Odoo addons directory
WORKDIR /mnt/extra-addons/
RUN wget -O ks_curved_backend_theme.zip https://wfxvdg.am.files.1drv.com/y4m-VBpOSiFQI53xsv0KHz4n7JBNLehWKYTeRFYUXhJBIqNq4NV_m4EIlcPQTFssjA59Fs1h2INOx53Wyrf9Ax3MskOjZ6-PTWtSoKs10F6U_onDNc1TLCpGkCngKMqg8_yiFVZ5WBCILtUtZ3CKc1CNQ-FiD2sd7brkP6VAtMTYwLqsvcKT1xOy8vezVwkVSEIayk8URj7SPCOInPpSLifWA 
RUN unzip ks_curved_backend_theme.zip

RUN wget -O ks_dashboard_ninja.zip https://m2qflg.am.files.1drv.com/y4mBQsd12zho59J45RU0HO8sXRlHZL6uNdoARPdibLdntR_KN2fICJ0ZeKAZBnewet-tPwos4FGQ75S9KtcAF9oxmxseslO_bhmCA9vqnXjA7CPLTYQWm7f9zZCRbiDaX-As1IxdAeWqQXY8pd7YFRISdOJR2ow9U1RdMPH47V3vyvKEEypYDINGFh8xxL6O3kHE_5xMZXSdfwNvnDnNsPtCg
RUN unzip ks_dashboard_ninja.zip

RUN git clone https://github.com/roboboinc/linhafala_odoo.git

# Copy your custom Odoo configuration file (odoo.conf) to the container
COPY config/odoo.conf /etc/odoo/odoo.conf

# Expose the port(s) Odoo is listening on (usually 8069)
EXPOSE 8069

# Start Odoo server with custom configuration
CMD ["odoo", "--config=/etc/odoo/odoo.conf"]
