import boto3
from botocore.config import Config
import re

aws_cfg = Config(retries={'max_attempts': 4})

# ----------------------------------------------------
# LIST REGIONS
# ----------------------------------------------------
def list_regions():
    ec2 = boto3.client("ec2", config=aws_cfg)
    try:
        return [r["RegionName"] for r in ec2.describe_regions()["Regions"]]
    except Exception as e:
        print("Regions error:", e)
        return []


# ----------------------------------------------------
# LIST INSTANCE TYPES
# ----------------------------------------------------
def list_instance_types(region: str):
    ec2 = boto3.client("ec2", region_name=region, config=aws_cfg)
    paginator = ec2.get_paginator("describe_instance_types")
    items = []

    try:
        for page in paginator.paginate():
            for it in page["InstanceTypes"]:
                items.append({
                    "type": it["InstanceType"],
                    "arch": it.get("ProcessorInfo", {}).get("SupportedArchitectures", ["x86_64"])
                })
    except Exception as e:
        print("Instance types error:", e)
        return []

    def family(inst_type: str):
        match = re.match(r"([a-z]+)", inst_type)
        return match.group(1) if match else inst_type

    grouped = {}
    for item in items:
        grouped.setdefault(family(item["type"]), []).append(item)

    ordered = []
    for fam in sorted(grouped.keys()):
        ordered.extend(sorted(grouped[fam], key=lambda x: x["type"]))
    return ordered


# ----------------------------------------------------
# LIST AMIs — EKS OPTIMIZED **ONLY**
# Ensures eksctl NEVER asks for bootstrap scripts
# ----------------------------------------------------
def list_amis(region: str, arch: str):
    ec2 = boto3.client("ec2", region_name=region, config=aws_cfg)

    filters = [
        {"Name": "architecture", "Values": [arch]},
        {
            "Name": "name",
            "Values": [
                "amazon-eks-node-*",       # Official EKS worker AMIs
                "amazon-eks-arm64-node-*", # ARM64 variants
            ]
        },
        {"Name": "state", "Values": ["available"]},
    ]

    OWNERS = ["amazon"]  # AWS owns EKS AMIs

    try:
        resp = ec2.describe_images(Owners=OWNERS, Filters=filters)
    except Exception as e:
        print("AMI error:", e)
        return []

    # Sort latest - keep top results only
    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)

    final = []
    for img in images[:50]:  # more than enough
        final.append({
            "image_id": img["ImageId"],
            "name": img.get("Name", ""),
            "arch": img.get("Architecture", ""),
            "date": img.get("CreationDate", "")
        })

    return final


# ----------------------------------------------------
# INSTANCE INFO
# ----------------------------------------------------
def get_instance_info(region: str, instance_type: str):
    ec2 = boto3.client("ec2", region_name=region, config=aws_cfg)
    try:
        it = ec2.describe_instance_types(InstanceTypes=[instance_type])["InstanceTypes"][0]
    except Exception as e:
        print("Instance info error:", e)
        return None

    mem_mib = it["MemoryInfo"]["SizeInMiB"]
    arch = it.get("ProcessorInfo", {}).get("SupportedArchitectures", ["x86_64"])

    return {
        "instance_type": instance_type,
        "vcpus": it["VCpuInfo"]["DefaultVCpus"],
        "memory_gib": round(mem_mib / 1024, 2),
        "arch": arch[0]
    }


# ----------------------------------------------------
# OS FAMILY DETECTION — eksctl SAFE
# ----------------------------------------------------
def detect_os_family(region: str, ami_id: str):
    ec2 = boto3.client("ec2", region_name=region, config=aws_cfg)
    try:
        img = ec2.describe_images(ImageIds=[ami_id])["Images"][0]
    except Exception:
        return "Unknown"

    text = (img.get("Name", "") + " " + img.get("Description", "")).lower()

    # All EKS worker AMIs are Amazon Linux 2023 (from 2024+ releases)
    if "amazon-eks-node" in text or "amazon linux 2023" in text or "al2023" in text:
        return "AmazonLinux2023"

    return "Unknown"
