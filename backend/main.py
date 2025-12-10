# backend/main.py

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import subprocess
import os
import logging

from backend.aws_utils import (
    list_regions,
    list_instance_types,
    list_amis,
    get_instance_info,
    detect_os_family,
)

from backend.eks_jmeter_manager import get_default_manager

LOG = logging.getLogger("backend")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="JMeter EKS Platform")

# ============================================================
# CORS CONFIG
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# SERVE FRONTEND (UI OUTSIDE BACKEND)
# ============================================================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FRONTEND_PATH = os.path.join(PROJECT_ROOT, "frontend")
STATIC_PATH = os.path.join(FRONTEND_PATH, "static")

if os.path.isdir(STATIC_PATH):
    app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    index = os.path.join(FRONTEND_PATH, "index.html")
    if not os.path.exists(index):
        raise HTTPException(500, "index.html not found in /frontend")
    with open(index, "r", encoding="utf-8") as f:
        return f.read()

# ============================================================
# GLOBAL JMETER MANAGER
# ============================================================
manager = get_default_manager()

TEST_STATUS_FILE = "/tmp/test_status"

# ============================================================
# AWS ENDPOINTS
# ============================================================
@app.get("/aws/regions")
def api_regions():
    return list_regions()

@app.get("/aws/instance-types")
def api_instance_types(region: str = Query(...)):
    return list_instance_types(region)

@app.get("/aws/instance-info")
def api_instance_info(region: str, instance_type: str):
    info = get_instance_info(region, instance_type)
    if not info:
        raise HTTPException(404, "Instance type not found")
    return info

@app.get("/aws/os-family")
def api_os_family(region: str, ami_id: str):
    return {"family": detect_os_family(region, ami_id)}

# ============================================================
# EKS CLUSTER CREATION — NO AMI REQUIRED
# ============================================================
@app.post("/eks/create")
def api_eks_create(data: dict):
    region = data.get("AWS_REGION")
    node_type = data.get("NODE_INSTANCE_TYPE")

    if not region or not node_type:
        raise HTTPException(400, "AWS_REGION and NODE_INSTANCE_TYPE are required")

    try:
        manager.create_cluster(region=region, node_type=node_type)

        ctx = {
            "TESTPLAN_REPO": data.get("TESTPLAN_REPO"),
            "MAX_SHARDS": int(data.get("MAX_SHARDS", 1)),
            "THREADS": int(data.get("THREADS", 1)),
            "LOOP_COUNT": int(data.get("LOOP_COUNT", 1)),
            "TARGET_BASE_URL": data.get("TARGET_BASE_URL"),
            "NAMESPACE": manager.jmeter_namespace,
            "HTTP_PORT": 8080,
            "JMETER_RMI_PORT": 50000,
        }

        manager.apply_jmeter_manifests(ctx)
        return {"status": "Cluster creation started"}

    except Exception as e:
        LOG.exception("Cluster creation failed")
        raise HTTPException(500, str(e))

@app.post("/eks/delete")
def api_delete():
    try:
        manager.delete_cluster()
        return {"status": "Deletion triggered"}
    except Exception as e:
        raise HTTPException(500, str(e))

# ============================================================
# RUN JMETER TEST
# ============================================================
def _reset_status():
    """
    Clears /tmp/test_status from master so UI starts fresh
    """
    pod = manager.kube.get_pod_name(manager.jmeter_namespace, "app=jmeter-master")
    if pod:
        manager.kube.exec_in_pod(manager.jmeter_namespace, pod, f"rm -f {TEST_STATUS_FILE}")
        LOG.info("Reset test status inside master pod")

@app.post("/test/run")
def api_run(data: dict):
    shards = int(data.get("MAX_SHARDS", 1))
    try:
        _reset_status()   # 👉 NEW tiny patch
        manager.run_test(shards)
        return {"status": f"Test started with {shards} shard(s)"}
    except Exception as e:
        LOG.exception("Test failed")
        raise HTTPException(500, str(e))

# ============================================================
# TEST STATUS ENDPOINT (NEW)
# ============================================================
@app.get("/test/status")
def api_status():
    """
    Reads /tmp/test_status created by entrypoint.sh
    Possible values: RUNNING | FINISHED | ERROR
    """

    pod = manager.kube.get_pod_name(manager.jmeter_namespace, "app=jmeter-master")
    if not pod:
        raise HTTPException(500, "Master pod not found")

    rc, out, err = manager.kube.exec_in_pod(
        manager.jmeter_namespace,
        pod,
        f"cat {TEST_STATUS_FILE} 2>/dev/null || echo RUNNING"
    )

    status = out.strip()
    LOG.info("Current JMeter test status = %s", status)

    return {"status": status}

# ============================================================
# DOWNLOAD RESULTS
# ============================================================
@app.get("/test/results")
def api_results():
    try:
        path = manager.fetch_results("./results/results.jtl")
        return FileResponse(path, filename=os.path.basename(path))
    except Exception as e:
        raise HTTPException(500, str(e))

# ============================================================
# GRAFANA URL
# ============================================================
@app.get("/grafana/url")
def grafana():
    try:
        out = subprocess.check_output(
            [
                "kubectl", "get", "svc", "grafana",
                "-n", manager.jmeter_namespace,
                "-o", "jsonpath={.status.loadBalancer.ingress[0].hostname}"
            ]
        ).decode().strip()
        return {"url": f"http://{out}:3000/d/jmeter-dashboard"}
    except:
        raise HTTPException(500, "Grafana not ready yet")
