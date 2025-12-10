# backend/eks_jmeter_manager.py

import os
import subprocess
import shlex
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import jinja2

LOG = logging.getLogger("eks_jmeter_manager")
logging.basicConfig(level=logging.INFO)


# ============================================================
#                KUBECTL HELPERS
# ============================================================
class KubeHelper:
    @staticmethod
    def apply_manifest(yaml_text: str, namespace: Optional[str] = None):
        cmd = ["kubectl", "apply", "-f", "-"]
        if namespace:
            cmd.insert(2, "-n")
            cmd.insert(3, namespace)

        LOG.info("Applying manifest (namespace=%s)...", namespace)
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = p.communicate(input=yaml_text.encode())

        if p.returncode != 0:
            msg = err.decode().strip()
            LOG.error("kubectl apply FAILED: %s", msg)
            raise RuntimeError(msg)

        LOG.info(out.decode().strip())

    @staticmethod
    def ensure_namespace(ns: str):
        try:
            subprocess.check_call(
                ["kubectl", "get", "ns", ns],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            LOG.info("Namespace '%s' already exists.", ns)
        except subprocess.CalledProcessError:
            LOG.info("Creating namespace '%s' ...", ns)
            subprocess.check_call(["kubectl", "create", "ns", ns])

    @staticmethod
    def get_pod_name(namespace: str, selector: str) -> Optional[str]:
        cmd = [
            "kubectl", "get", "pods", "-n", namespace,
            "-l", selector, "-o", "jsonpath={.items[0].metadata.name}"
        ]
        try:
            out = subprocess.check_output(
                cmd, stderr=subprocess.STDOUT
            ).decode().strip()
            return out or None
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def exec_in_pod(namespace: str, pod: str, command: str, container: Optional[str] = None):
        cmd = ["kubectl", "exec", "-n", namespace, pod]
        if container:
            cmd += ["-c", container]
        cmd += ["--", "sh", "-c", command]

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        return p.returncode, out.decode(), err.decode()

    @staticmethod
    def copy_from_pod(namespace: str, pod: str, remote_path: str, local_path: str):
        cmd = ["kubectl", "cp", f"{namespace}/{pod}:{remote_path}", local_path]
        LOG.info("Copying results: %s", " ".join(cmd))
        subprocess.check_call(cmd)


# ============================================================
#                MAIN MANAGER
# ============================================================
class EKSJMeterManager:
    def __init__(
        self,
        templates_dir: str = "backend/templates",
        jmeter_namespace: str = "jmeter",
        monitoring_namespace: str = "monitoring",
        cluster_name: str = "jmeter-cluster",
    ):
        self.templates_dir = Path(templates_dir)
        self.jmeter_namespace = jmeter_namespace
        self.monitoring_namespace = monitoring_namespace
        self.namespace = jmeter_namespace
        self.cluster_name = cluster_name

        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self.templates_dir)),
            autoescape=False,
            keep_trailing_newline=True,
        )

        self.kube = KubeHelper()

    # --------------------------------------------------------
    @staticmethod
    def _is_cluster_scoped_template(name: str) -> bool:
        base = Path(name).name
        for suf in (".yaml.j2", ".yml.j2", ".yaml", ".yml"):
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        return base in {"storageclass", "storageclass-and-pvcs"}

    # ============================================================
    # EKS CLUSTER LIFECYCLE
    # ============================================================
    def _wait_for_kube_ready(self, timeout: int = 300):
        start = time.time()
        while True:
            try:
                subprocess.check_call(
                    ["kubectl", "get", "nodes"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                LOG.info("Cluster API is ready.")
                return
            except subprocess.CalledProcessError:
                if time.time() - start > timeout:
                    raise TimeoutError("Timeout waiting for EKS cluster to be ready")
                time.sleep(5)

    def create_cluster(
        self,
        *,
        region: str,
        node_type: str,
        ami: Optional[str] = None,
        ami_family: Optional[str] = None,
        nodegroup_name: str = "jmeter-nodes",
        min_nodes: int = 1,
        max_nodes: int = 3,
    ):
        cmd = [
            "eksctl", "create", "cluster",
            "--name", self.cluster_name,
            "--region", region,
            "--nodegroup-name", nodegroup_name,
            "--node-type", node_type,
            "--nodes", str(min_nodes),
            "--nodes-min", str(min_nodes),
            "--nodes-max", str(max_nodes),
            "--managed",
        ]

        if ami and ami_family:
            cmd += ["--node-ami", ami, "--node-ami-family", ami_family]
            LOG.info("Using custom AMI: %s (%s)", ami, ami_family)

        LOG.info("Executing eksctl:\n%s", " ".join(shlex.quote(x) for x in cmd))
        subprocess.check_call(cmd)

        self._wait_for_kube_ready()
        self.kube.ensure_namespace(self.jmeter_namespace)
        self.kube.ensure_namespace(self.monitoring_namespace)

    # ============================================================
    # MANIFESTS
    # ============================================================
    def render_template(self, name: str, context: Dict[str, Any]) -> str:
        return self.jinja_env.get_template(name).render(**context)

    def apply_jmeter_manifests(self, context: Dict[str, Any]):
        files = [
            "storageclass-and-pvcs.yaml",
            "jmeter-configmap.yaml.j2",
            "jmeter-master-deployment.yaml.j2",
            "jmeter-master-service.yaml.j2",
            "jmeter-slaves-statefulset.yaml.j2",
            "jmeter-slaves-service.yaml.j2",
            "jmeter-slaves-hpa.yaml.j2",
            "monitor-influx.yaml.j2",
            "monitor-grafana.yaml.j2",
        ]

        for f in files:
            tpl = self.templates_dir / f
            if not tpl.exists():
                LOG.warning("Skipping missing template: %s", f)
                continue

            yaml_text = self.render_template(f, context)

            if self._is_cluster_scoped_template(f):
                ns = None
            elif f.startswith("monitor-"):
                ns = self.monitoring_namespace
            else:
                ns = self.jmeter_namespace

            self.kube.apply_manifest(yaml_text, namespace=ns)

    # ============================================================
    # SLAVES MGMT
    # ============================================================
    def scale_slaves(self, replicas: int):
        LOG.info("Scaling jmeter-slaves to %s replicas...", replicas)
        subprocess.check_call(
            [
                "kubectl",
                "scale",
                "statefulset",
                "jmeter-slaves",
                "-n",
                self.jmeter_namespace,
                f"--replicas={replicas}",
            ]
        )

    def wait_for_slaves(self, replicas: int, timeout_sec: int = 300):
        for i in range(replicas):
            pod = f"jmeter-slaves-{i}"
            LOG.info("Waiting for slave pod %s to be Ready...", pod)
            subprocess.check_call(
                [
                    "kubectl",
                    "wait",
                    "--for=condition=Ready",
                    f"pod/{pod}",
                    "-n",
                    self.jmeter_namespace,
                    f"--timeout={timeout_sec}s",
                ]
            )
            LOG.info("Pod %s Ready.", pod)

    # ============================================================
    # 🚀 RUN DISTRIBUTED TEST  (FIXED)
    # ============================================================
    def run_test(
        self,
        max_shards: int = 1,
        jmx_path: str = "/testplans/JPetStore_Registration.jmx",
    ):
        LOG.info("Removing old slave pods (if any)...")
        subprocess.call(
            [
                "kubectl",
                "delete",
                "pod",
                "-n",
                self.jmeter_namespace,
                "-l",
                "app=jmeter-slaves",
            ]
        )

        self.scale_slaves(max_shards)
        self.wait_for_slaves(max_shards)

        pod = self.kube.get_pod_name(self.jmeter_namespace, "app=jmeter-master")
        if not pod:
            raise RuntimeError("JMeter master pod NOT found")

        # FIX: Use correct container name explicitly
        container = "jmeter-master"
        LOG.info("Master container: %s", container)

        rc, out, err = self.kube.exec_in_pod(
            self.jmeter_namespace, pod,
            "ls -1 /testplans/*.jmx 2>/dev/null | head -n 1",
            container=container,
        )

        detected = out.strip()
        if detected:
            LOG.info("Using testplan: %s", detected)
        else:
            LOG.warning("Fallback testplan: %s", jmx_path)

        trigger_cmd = (
            "echo RUNNING > /tmp/test_status && rm -f /tmp/run_test && touch /tmp/run_test"
        )
        LOG.info("Triggering entrypoint in master via: %s", trigger_cmd)

        rc, out, err = self.kube.exec_in_pod(
            self.jmeter_namespace, pod, trigger_cmd, container=container
        )

        if rc != 0:
            LOG.error("Failed to trigger /tmp/run_test (rc=%s, stderr=%s)", rc, err)
            raise RuntimeError(f"Failed to trigger JMeter in master: {err}")

        LOG.info("Backend trigger created and status set to RUNNING.")
        time.sleep(3)

    # ============================================================
    # 🚦 TEST STATUS CHECK
    # ============================================================
    def get_status(self) -> str:
        pod = self.kube.get_pod_name(self.jmeter_namespace, "app=jmeter-master")
        if not pod:
            return "UNKNOWN"

        rc, out, err = self.kube.exec_in_pod(
            self.jmeter_namespace,
            pod,
            "cat /tmp/test_status 2>/dev/null || echo UNKNOWN",
        )
        return out.strip()

    # ============================================================
    # FETCH RESULTS
    # ============================================================
    def fetch_results(self, dest: str = "./results/results.jtl"):
        pod = self.kube.get_pod_name(self.jmeter_namespace, "app=jmeter-master")
        if not pod:
            raise RuntimeError("Master pod not found")

        local_dir = os.path.dirname(dest) or "."
        os.makedirs(local_dir, exist_ok=True)

        rc, out, err = self.kube.exec_in_pod(
            self.jmeter_namespace,
            pod,
            "ls -1 /results/*.jtl 2>/dev/null | head -n 1",
        )
        remote = out.strip()

        if not remote:
            rc, out, err = self.kube.exec_in_pod(
                self.jmeter_namespace,
                pod,
                "ls -1 /testplans/*.jtl 2>/dev/null | head -n 1",
            )
            remote = out.strip()

        if not remote:
            LOG.error("No JTL generated on master (test may still be running)")
            raise RuntimeError("No JTL generated on master")

        jtl_name = os.path.basename(remote)
        local_path = os.path.join(local_dir, jtl_name)
        self.kube.copy_from_pod(self.jmeter_namespace, pod, remote, local_path)

        LOG.info("Downloaded JTL → %s", local_path)
        return local_path

    # ============================================================
    # 🚨 DELETE CLUSTER
    # ============================================================
    def delete_cluster(self):
        LOG.info("Deleting EKS cluster: %s", self.cluster_name)
        try:
            subprocess.check_call(
                ["eksctl", "delete", "cluster", "--name", self.cluster_name, "--force"]
            )
            LOG.info("Cluster deletion started.")
        except subprocess.CalledProcessError as e:
            LOG.error("Cluster deletion failed: %s", e)
            raise RuntimeError("EKS deletion failed")


# SINGLETON
_default_manager = EKSJMeterManager()


def get_default_manager() -> EKSJMeterManager:
    return _default_manager
