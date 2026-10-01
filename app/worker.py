"""Optional lightweight background worker."""

from __future__ import annotations

import logging
import os
import time
import uuid

from app import jobs


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("reseller-worker")
POLL_SECONDS = max(1, int(os.getenv("WORKER_POLL_SECONDS", "5")))


def handle(job: dict) -> None:
    job_type = job["job_type"]
    payload = job.get("payload") or {}
    workspace_id = uuid.UUID(job["workspace_id"]) if job.get("workspace_id") else None
    if job_type == "biblio_sync":
        if workspace_id is not None:
            from app.connectors.hosted import has_credentials, sync_biblio_workspace
            if has_credentials(workspace_id, "biblio"):
                sync_biblio_workspace(workspace_id)
                return
        from app.channels import sync_biblio_ftp
        sync_biblio_ftp()
        return
    if job_type == "ebay_sync":
        if workspace_id is not None:
            from app.connectors.hosted import has_credentials, sync_ebay_workspace
            if has_credentials(workspace_id, "ebay"):
                sync_ebay_workspace(workspace_id)
                return
        from app.channels import sync_ebay_inventory
        sync_ebay_inventory()
        return
    if job_type == "noop":
        return
    raise RuntimeError(f"Unknown background job type: {job_type}")


def run_forever() -> None:
    logger.info("worker started")
    while True:
        try:
            job = jobs.claim_one()
        except Exception:
            logger.exception("job queue is not ready yet")
            time.sleep(POLL_SECONDS)
            continue
        if job is None:
            time.sleep(POLL_SECONDS)
            continue
        try:
            handle(job)
        except Exception as exc:
            logger.exception("job %s failed", job["id"])
            jobs.fail(job["id"], str(exc))
        else:
            jobs.complete(job["id"])


if __name__ == "__main__":
    run_forever()
