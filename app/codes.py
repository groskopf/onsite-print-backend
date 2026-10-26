import base64
import secrets
import uuid

CODE_PATTERN = r"^[A-Z2-7]{16}$"
ATTENDEE_CODE_PATTERN = r"^[0-9a-f]{64}$"


def generate_code() -> str:
    return base64.b32encode(uuid.uuid4().bytes)[:16].decode()


def generate_attendee_code() -> str:
    return secrets.token_hex(32)
