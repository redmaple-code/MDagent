"""Print a public target descriptor on the final compute host; no files changed."""
from pathlib import Path
import argparse
import json
import os
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from bioagent.execution_target import host_identity, validate_target
from bioagent.errors import BioError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant-id", "node-id", "target-id", "python", "projects-root", "quota-root"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--target-revision", type=int, default=1)
    parser.add_argument("--cpu-threads", type=int, required=True)
    parser.add_argument("--max-concurrent-jobs", type=int, default=1)
    parser.add_argument("--gpu-devices", type=int, nargs="*", default=[])
    parser.add_argument("--max-output-bytes", type=int, default=10737418240)
    args = parser.parse_args()
    try:
        target = validate_target({"schemaVersion": "bio.execution-target.v1", "tenantId": args.tenant_id,
            "nodeId": args.node_id, "targetId": args.target_id, "targetRevision": args.target_revision,
            "hostIdentitySha256": host_identity(), "uid": os.getuid(), "python": args.python,
            "projectsRoot": args.projects_root, "releaseRoot": str(Path(__file__).resolve().parent),
            "resourcePolicy": {"quotaRoot": args.quota_root, "cpuThreads": args.cpu_threads,
                "maxConcurrentJobs": args.max_concurrent_jobs, "gpuDevices": args.gpu_devices,
                "maxOutputBytes": args.max_output_bytes}})
        print(json.dumps(target, sort_keys=True)); return 0
    except BioError as error:
        sys.stderr.write(error.code + "\n"); return 1


if __name__ == "__main__": raise SystemExit(main())
