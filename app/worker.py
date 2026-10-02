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
HEARTBEAT_SECONDS = max(10, int(os.getenv("WORKER_HEARTBEAT_SECONDS", "30")))


def handle(job: dict) -> None:
    job_type = job["job_type"]
    payload = job.get("payload") or {}
    workspace_id = uuid.UUID(job["workspace_id"]) if job.get("workspace_id") else None
    if workspace_id is not None and job_type != "noop":
        from app import billing, db, models
        if billing.BILLING_ENABLED:
            with db.session_scope() as session:
                workspace = session.get(models.Workspace, workspace_id)
                if workspace is None:
                    raise RuntimeError("Workspace no longer exists")
                if not billing.workspace_can_write(workspace):
                    raise RuntimeError(
                        "Workspace is read-only until the subscription is active or trialing"
                    )
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
    if job_type == "etsy_sync":
        if workspace_id is None:
            raise RuntimeError("etsy_sync requires a workspace")
        from app.connectors.hosted import sync_etsy_workspace
        sync_etsy_workspace(workspace_id)
        return
    if job_type == "woocommerce_sync":
        if workspace_id is None:
            raise RuntimeError("woocommerce_sync requires a workspace")
        from app.connectors.hosted import sync_woocommerce_workspace
        sync_woocommerce_workspace(workspace_id)
        return
    if job_type == "shopify_sync":
        if workspace_id is None:
            raise RuntimeError("shopify_sync requires a workspace")
        from app.connectors.hosted import sync_shopify_workspace
        sync_shopify_workspace(workspace_id)
        return
    if job_type == "bigcommerce_sync":
        if workspace_id is None:
            raise RuntimeError("bigcommerce_sync requires a workspace")
        from app.connectors.hosted import sync_bigcommerce_workspace
        sync_bigcommerce_workspace(workspace_id)
        return
    if job_type == "cross_channel_close":
        action_id = payload.get("action_id")
        if not action_id:
            raise RuntimeError("cross_channel_close job is missing action_id")
        from app.cross_channel import execute_action
        execute_action(uuid.UUID(str(action_id)))
        return
    if job_type == "noop":
        return
    raise RuntimeError(f"Unknown background job type: {job_type}")


def run_forever() -> None:
    logger.info("worker started")
    last_heartbeat = 0.0
    while True:
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            try:
                from app.service_status import touch
                touch("worker", detail={"poll_seconds": POLL_SECONDS})
                last_heartbeat = now
            except Exception:
                logger.exception("worker heartbeat is not ready yet")
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
            if job.get("job_type") == "cross_channel_close":
                result = jobs.retry(job["id"], str(exc))
                action_id = (job.get("payload") or {}).get("action_id")
                if action_id:
                    try:
                        from app.cross_channel import record_action_failure
                        record_action_failure(
                            uuid.UUID(str(action_id)),
                            str(exc),
                            will_retry=bool(result.get("will_retry")),
                        )
                    except Exception:
                        logger.exception("could not record cross-channel action failure")
            else:
                jobs.fail(job["id"], str(exc))
        else:
            jobs.complete(job["id"])


if __name__ == "__main__":
    run_forever()
