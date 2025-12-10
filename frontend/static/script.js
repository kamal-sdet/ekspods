// =======================================================
// HELPER - Update Cluster Status Text
// =======================================================
function setStatus(text, color = "blue") {
    const el = document.getElementById("cluster_status");
    el.innerText = text;
    el.style.color = color;
}

// =======================================================
// CREATE CLUSTER  (NO AMI FIELDS ANYMORE)
// =======================================================
async function createCluster() {
    setStatus("Creating cluster...", "orange");

    const payload = {
        AWS_REGION: document.getElementById("aws_region").value,
        NODE_INSTANCE_TYPE: document.getElementById("instance_type").value,
        TESTPLAN_REPO: document.getElementById("testplan_repo").value,
        TARGET_BASE_URL: document.getElementById("target_base_url").value,
        MAX_SHARDS: document.getElementById("max_shards").value,
        THREADS: document.getElementById("threads").value,
        LOOP_COUNT: document.getElementById("loop_count").value
    };

    try {
        const resp = await fetch("/eks/create", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });

        if (!resp.ok) throw new Error(await resp.text());
        setStatus("Cluster Ready - Deploying JMeter...", "green");
        alert("Cluster creation started.");
    } catch (err) {
        console.error("Cluster creation failed:", err);
        setStatus("Error creating cluster", "red");
        alert("Cluster creation failed! Check backend logs.");
    }
}

// =======================================================
// LOAD REGIONS
// =======================================================
async function loadDropdowns() {
    try {
        const regions = await fetch("/aws/regions").then(r => r.json());
        const sel = document.getElementById("aws_region");
        sel.innerHTML = "";
        regions.forEach(r => sel.appendChild(new Option(r, r)));
        sel.value = regions[0];
        await loadInstanceTypes();
    } catch (err) {
        console.error("Failed loading regions:", err);
    }
}

// =======================================================
// INSTANCE TYPES
// =======================================================
async function loadInstanceTypes() {
    const region = document.getElementById("aws_region").value;
    const types = await fetch(`/aws/instance-types?region=${region}`).then(r => r.json());

    const sel = document.getElementById("instance_type");
    sel.innerHTML = "";
    types.forEach(t => sel.appendChild(new Option(`${t.type} (${t.arch})`, t.type)));

    sel.value = types[0].type;
    await loadInstanceInfo();
}

// =======================================================
// INSTANCE INFO
// =======================================================
async function loadInstanceInfo() {
    const region = document.getElementById("aws_region").value;
    const type = document.getElementById("instance_type").value;

    const info = await fetch(`/aws/instance-info?region=${region}&instance_type=${type}`).then(r => r.json());
    document.getElementById("instance_vcpus").innerText = `vCPU: ${info.vcpus}`;
    document.getElementById("instance_memory").innerText = `Memory: ${info.memory_gib} GiB`;
}

// =======================================================
// RUN TEST
// =======================================================
async function runTest() {
    const shards = document.getElementById("max_shards").value;
    setStatus("Running test...", "orange");

    try {
        const resp = await fetch("/test/run", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({MAX_SHARDS: shards})
        });

        if (!resp.ok) throw new Error(await resp.text());
        setStatus("Test completed", "green");
        document.getElementById("dashboard_btn").disabled = false;
    } catch (err) {
        console.error("Run test failed:", err);
        setStatus("Error running test", "red");
        alert("Test run failed! Check backend logs.");
    }
}

// =======================================================
// DOWNLOAD RESULTS
// =======================================================
function downloadResults() {
    window.location = "/test/results";
}

// =======================================================
// DELETE CLUSTER
// =======================================================
async function deleteCluster() {
    setStatus("Deleting cluster...", "orange");
    await fetch("/eks/delete", {method:"POST"});
    setStatus("Cluster Deleted", "red");
}

// =======================================================
// EVENT BINDINGS
// =======================================================
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("aws_region").addEventListener("change", loadInstanceTypes);
    document.getElementById("instance_type").addEventListener("change", loadInstanceInfo);
});

window.onload = loadDropdowns;
