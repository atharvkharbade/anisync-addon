import logging

from quart import Response, jsonify, request


async def respond_with(data: dict) -> Response:
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def log_error(label: str, message: str, hint: str = "", code: int = 0):
    logging.error("%s [%s] %s | %s", label, code, message, hint)
