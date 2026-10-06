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


def _require_workspace(job_type: str, workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise RuntimeError(f"{job_type} requires a workspace")
    return workspace_id


def _check_workspace_write_access(workspace_id: uuid.UUID | None, job_type: str) -> None:
    if workspace_id is None or job_type == "noop":
        return
    from app import billing, db, models

    if not billing.BILLING_ENABLED:
        return
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise RuntimeError("Workspace no longer exists")
        if not billing.workspace_can_write(workspace):
            raise RuntimeError(
                "Workspace is read-only until the subscription is active or trialing"
            )


def _sync_biblio(payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_biblio_workspace

    resolved_workspace = _require_workspace("biblio_sync", workspace_id)
    raw_listing_id = payload.get("listing_id")
    listing_id = uuid.UUID(str(raw_listing_id)) if raw_listing_id else None
    result = sync_biblio_workspace(
        resolved_workspace,
        listing_id=listing_id,
        full_sync=bool(payload.get("full_sync")),
        force_photos=bool(payload.get("force_photos")),
        photos_only=bool(payload.get("photos_only")),
    )

    # BIBLIO explicitly ignores an image if it is picked up before the
    # matching listing becomes active. New inventory uploads therefore get
    # one durable delayed photo-only retry. If a manual retry succeeds first,
    # the delayed job becomes a no-op because force_photos is deliberately
    # false.
    if not payload.get("photos_only"):
        delay_seconds = max(
            0,
            int(os.getenv("BIBLIO_PHOTO_RETRY_DELAY_SECONDS", "93600")),
        )
        for raw_id in result.get("deferred_photo_retry_listing_ids") or []:
            jobs.enqueue(
                "biblio_sync",
                {
                    "listing_id": str(raw_id),
                    "photos_only": True,
                    "force_photos": False,
                    "automatic_photo_retry": True,
                },
                resolved_workspace,
                delay_seconds=delay_seconds,
            )


def _sync_ebay(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_ebay_workspace

    sync_ebay_workspace(_require_workspace("ebay_sync", workspace_id))


def _sync_etsy(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_etsy_workspace

    sync_etsy_workspace(_require_workspace("etsy_sync", workspace_id))


def _sync_woocommerce(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_woocommerce_workspace

    sync_woocommerce_workspace(_require_workspace("woocommerce_sync", workspace_id))


def _sync_shopify(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_shopify_workspace

    sync_shopify_workspace(_require_workspace("shopify_sync", workspace_id))


def _sync_bigcommerce(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_bigcommerce_workspace

    sync_bigcommerce_workspace(_require_workspace("bigcommerce_sync", workspace_id))


def _sync_squarespace(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_squarespace_workspace

    sync_squarespace_workspace(_require_workspace("squarespace_sync", workspace_id))


def _sync_wix(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_wix_workspace

    sync_wix_workspace(_require_workspace("wix_sync", workspace_id))


def _sync_depop(_payload: dict, workspace_id: uuid.UUID | None) -> None:
    from app.connectors.hosted import sync_depop_workspace

    sync_depop_workspace(_require_workspace("depop_sync", workspace_id))


def _cross_channel_close(payload: dict, workspace_id: uuid.UUID | None) -> None:
    _require_workspace("cross_channel_close", workspace_id)
    action_id = payload.get("action_id")
    if not action_id:
        raise RuntimeError("cross_channel_close job is missing action_id")
    from app.cross_channel import execute_action

    execute_action(uuid.UUID(str(action_id)))


def _noop(_payload: dict, _workspace_id: uuid.UUID | None) -> None:
    return


_JOB_HANDLERS = {
    "biblio_sync": _sync_biblio,
    "ebay_sync": _sync_ebay,
    "etsy_sync": _sync_etsy,
    "woocommerce_sync": _sync_woocommerce,
    "shopify_sync": _sync_shopify,
    "bigcommerce_sync": _sync_bigcommerce,
    "squarespace_sync": _sync_squarespace,
    "wix_sync": _sync_wix,
    "depop_sync": _sync_depop,
    "cross_channel_close": _cross_channel_close,
    "noop": _noop,
}


def handle(job: dict) -> None:
    job_type = str(job.get("job_type") or "")
    handler = _JOB_HANDLERS.get(job_type)
    if handler is None:
        raise RuntimeError(f"Unknown background job type: {job_type}")

    workspace_id = (
        uuid.UUID(str(job["workspace_id"]))
        if job.get("workspace_id")
        else None
    )
    _check_workspace_write_access(workspace_id, job_type)
    handler(job.get("payload") or {}, workspace_id)


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
            elif (
                job.get("job_type") == "biblio_sync"
                and str(exc) == "BIBLIO FTP sync failed"
            ):
                result = jobs.retry(job["id"], str(exc))
                if result.get("will_retry"):
                    payload = job.get("payload") or {}
                    raw_listing_id = payload.get("listing_id")
                    if raw_listing_id:
                        try:
                            from app.connectors.hosted import _set_biblio_listing_states
                            if payload.get("photos_only"):
                                _set_biblio_listing_states(
                                    uuid.UUID(str(job["workspace_id"])),
                                    [str(raw_listing_id)],
                                    photo_state="queued",
                                )
                            else:
                                _set_biblio_listing_states(
                                    uuid.UUID(str(job["workspace_id"])),
                                    [str(raw_listing_id)],
                                    publish_state="queued",
                                )
                        except Exception:
                            logger.exception("could not mark BIBLIO job for retry")
            else:
                jobs.fail(job["id"], str(exc))
        else:
            jobs.complete(job["id"])


if __name__ == "__main__":
    run_forever()
