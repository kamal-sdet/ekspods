# docker/Dockerfile
FROM alpine:3.18

# Install packages (openjdk, git, curl, python3, coreutils for split, bash)
RUN apk update && apk add --no-cache \
    openjdk11-jre \
    bash \
    curl \
    git \
    python3 \
    py3-pip \
    unzip \
    coreutils \
    ca-certificates

# JMeter version
ENV JMETER_VERSION=5.6.3
ENV JMETER_HOME=/opt/jmeter
ENV PATH="${JMETER_HOME}/bin:${PATH}"

# -------------------------------------------------
# Install JMeter
# -------------------------------------------------
RUN mkdir -p /opt \
    && curl -L "https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" \
       | tar -xz -C /opt \
    && mv /opt/apache-jmeter-${JMETER_VERSION} "${JMETER_HOME}" \
    && chmod -R a+rX "${JMETER_HOME}"

# -------------------------------------------------
# Add JMeter Plugins (InfluxDB Listener + others)
# -------------------------------------------------
# Your plugins folder structure:
# docker/plugins/*.jar
COPY plugins/*.jar ${JMETER_HOME}/lib/ext/

# -------------------------------------------------
# Create directories for testplans & results
# -------------------------------------------------
RUN mkdir -p /testplans /results

# -------------------------------------------------
# Add entrypoint
# -------------------------------------------------
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
