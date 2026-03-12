# patrol_mq.py — Publish events to cmd-patrol MQ
# Zero external dependencies (stdlib only). Fire-and-forget: never raises.
import json
import os
import urllib.request

_PATROL_URL = os.environ.get("CMD_PATROL_URL", "http://127.0.0.1:51314")

SOURCE = "civitai-downloader"


def publish_event(title: str, type: str = "", detail: str = "", meta: dict = None):
    """
    Publish an event to cmd-patrol MQ.

    Args:
        title:  Short summary of what happened (< 120 chars)
        type:   Category string, e.g. "skip_classify", "skip_generate", "download_fail"
        detail: Optional longer description
        meta:   Optional dict with extra structured data
    """
    payload = json.dumps({
        "source": SOURCE,
        "type": type,
        "title": title,
        "detail": detail,
        "meta": meta or {},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{_PATROL_URL}/api/mq/publish",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # MQ is best-effort; never crash the main process
