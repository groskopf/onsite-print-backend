import base64
import uuid

CODE_PATTERN = r"^[A-Z2-7]{16}$"


def generate_code() -> str:
    return base64.b32encode(uuid.uuid4().bytes)[:16].decode()
