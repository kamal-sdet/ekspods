#!/usr/bin/env bash
set -euo pipefail

############################################################
# Runtime variables
############################################################
ROLE="${ROLE:-master}"
GIT_REPO_URL="${TESTPLAN_REPO:-}"
MAX_SHARDS="${MAX_SHARDS:-1}"
THREADS="${THREADS:-10}"
LOOP_COUNT="${LOOP_COUNT:-1}"
NAMESPACE="${NAMESPACE:-jmeter}"
HTTP_PORT="${HTTP_PORT:-8080}"
SLAVE_SERVICE_NAME="${SLAVE_SERVICE_NAME:-jmeter-slaves}"

JMETER_RMI_PORT="${JMETER_RMI_PORT:-50000}"

RESULTS_DIR="/results"
RESULTS_FILE="results.jtl"
RESULTS_PATH="${RESULTS_DIR}/${RESULTS_FILE}"

echo "ENTRYPOINT START – ROLE=${ROLE}, MAX_SHARDS=${MAX_SHARDS}, NAMESPACE=${NAMESPACE}, RMI_PORT=${JMETER_RMI_PORT}"

mkdir -p /testplans "${RESULTS_DIR}"

############################################################
# Detect JMeter Home
############################################################
JMETER_BIN="$(command -v jmeter)"
JMETER_HOME="$(dirname "$(dirname "${JMETER_BIN}")")"
USER_PROPS="${JMETER_HOME}/bin/user.properties"

echo "Detected JMeter home: ${JMETER_HOME}"
echo "Using user.properties at: ${USER_PROPS}"

############################################################
# Configure InfluxDB Backend Listener
############################################################
echo "Configuring InfluxDB Backend Listener..."

cat <<EOF >> "$USER_PROPS"

backend_influxdb.enabled=true
backend_influxdb.url=http://influxdb.${NAMESPACE}.svc.cluster.local:8086/write?db=jmeter
backend_influxdb.nodeName=\${HOSTNAME}

EOF

echo "✔ InfluxDB backend listener configured."

############################################################
# MASTER MODE
############################################################
if [[ "$ROLE" == "master" ]]; then
    echo "🔱 Running in MASTER mode"

    export JVM_ARGS="${JVM_ARGS} \
        -Dserver.rmi.ssl.disable=true \
        -Djava.rmi.server.hostname=jmeter-master.${NAMESPACE}.svc.cluster.local \
        -Dserver.rmi.localport=${JMETER_RMI_PORT} \
        -Djmeterengine.remote.system.exit=true \
        -Dserver.exitaftertest=true"

    echo "💡 Effective JVM_ARGS=${JVM_ARGS}"

    [[ -z "$GIT_REPO_URL" ]] && { echo "❌ TESTPLAN_REPO missing"; exit 1; }

    echo "📥 Cloning repo: $GIT_REPO_URL"
    rm -rf /testplans/repo
    git clone --depth 1 "$GIT_REPO_URL" /testplans/repo

    cp /testplans/repo/*.jmx /testplans/ || true
    cp /testplans/repo/*.csv /testplans/ || true

    cd /testplans || exit 1

    JMETER_JMX="$(ls *.jmx | head -n 1)"
    CSV_FILE="$(ls *.csv | head -n 1)"

    echo "📄 Testplan: $JMETER_JMX"
    echo "📄 CSV: $CSV_FILE"

    echo "🔀 Splitting CSV into ${MAX_SHARDS} shards…"
    split -d -n l/"$MAX_SHARDS" "$CSV_FILE" shard_

    n=0
    for file in shard_*; do
        mv "$file" "data_part_${n}.csv"
        echo "Created data_part_${n}.csv"
        n=$((n + 1))
    done

    echo "🕘 MASTER READY — waiting for backend trigger..."

    # 🔥 WAIT FOR BACKEND TRIGGER
    while [[ ! -f /tmp/run_test ]]; do
        sleep 1
    done

    echo "🚀 Backend triggered test execution"

    # ❌ Removed buggy CSV shard JVM_ARGS injection from MASTER (Option A)

    REMOTE_HOSTS=$(printf "jmeter-slaves-%d.${SLAVE_SERVICE_NAME}.${NAMESPACE}.svc.cluster.local:50000," \
        $(seq 0 $((MAX_SHARDS-1))) | sed 's/,$//')

    jmeter -n \
        -t "$JMETER_JMX" \
        -R "$REMOTE_HOSTS" \
        -l "$RESULTS_PATH"

    echo "⏳ Waiting for JMeter process to exit..."
    while pgrep -f jmeter >/dev/null; do
        sleep 1
    done

    echo "🎯 TEST FINISHED SUCCESSFULLY"
    echo "📁 Results saved at: $RESULTS_PATH"

    tail -f /dev/null
fi

############################################################
# SLAVE MODE
############################################################
if [[ "$ROLE" == "slave" ]]; then
    ordinal=$(echo "${HOSTNAME}" | grep -oE '[0-9]+$' || echo "0")
    FQDN="${HOSTNAME}.${SLAVE_SERVICE_NAME}.${NAMESPACE}.svc.cluster.local"

    echo "🔧 Slave ordinal=${ordinal}, FQDN=${FQDN}"

    ############################################################
    # 🟢 CORRECT CSV SHARD PATCH — THIS IS WHERE IT BELONGS
    ############################################################
    echo "csv.file=data_part_${ordinal}.csv" >> "$USER_PROPS"
    echo "✔ Slave CSV shard assignment: data_part_${ordinal}.csv"

    echo "🚀 Starting JMeter server..."

    exec "${JMETER_HOME}/bin/jmeter-server" \
        -Jserver.rmi.localport="${JMETER_RMI_PORT}" \
        -Jserver_port="${JMETER_RMI_PORT}" \
        -Dserver.rmi.ssl.disable=true \
        -Djava.rmi.server.hostname="${FQDN}"
fi

echo "❌ Unknown ROLE='${ROLE}'"
exit 1
