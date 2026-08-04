import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("agent_cs")


def structured_log(
    event: str,
    item_id: Optional[int] = None,
    domain: Optional[str] = None,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "item_id": item_id,
        "domain": domain,
        "source_type": source_type,
        "status": status,
        "error_msg": error_msg,
    }
    if extra:
        payload.update(extra)
    # 过滤 None 值
    payload = {k: v for k, v in payload.items() if v is not None}
    logger.info(json.dumps(payload, ensure_ascii=False))
