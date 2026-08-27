import json
import logging
from datetime import UTC, datetime

from local_dev.logging import JsonFormatter


def test_json_formatter_uses_log_record_timestamp() -> None:
    record = logging.LogRecord(
        name="local_dev.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.created = 0.0

    payload = json.loads(JsonFormatter().format(record))

    assert payload == {
        "timestamp": datetime.fromtimestamp(0.0, UTC).isoformat(),
        "level": "INFO",
        "logger": "local_dev.test",
        "message": "hello world",
    }
