import json
import logging
import os
from datetime import datetime

from app.factory import create_app
from app.services.http import correlation_id_var


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        try:
            corr_id = correlation_id_var.get()
            if corr_id:
                log_record["correlation_id"] = corr_id
        except Exception:
            pass

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


is_debug = os.getenv("FLASK_DEBUG", "False").lower() in ["1", "true"]

logger = logging.getLogger()
logger.setLevel(logging.INFO)
for h in logger.handlers[:]:
    logger.removeHandler(h)

handler = logging.StreamHandler()
if is_debug:
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
else:
    handler.setFormatter(JsonFormatter())
logger.addHandler(handler)

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
