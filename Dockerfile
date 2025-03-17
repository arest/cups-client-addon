ARG BUILD_FROM
FROM $BUILD_FROM

# Install required packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    cups-client \
    cups-libs \
    cups-dev \
    gcc \
    python3-dev \
    musl-dev

# Create necessary directories
RUN mkdir -p /app/services

# Copy your application files
WORKDIR /app
COPY run.py /app/
COPY requirements.txt /app/
COPY services.yaml /app/

# Install Python dependencies
RUN pip3 install -r requirements.txt

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]