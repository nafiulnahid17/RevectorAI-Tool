import json
import logging

logger = logging.getLogger("revector")


def log_event(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str, sort_keys=True))
