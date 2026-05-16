from enum import Enum

from pydantic import BaseModel, field_validator

from .config import MAX_CODE


class Status(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    killed = "killed"


class ExecuteRequest(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def validate(cls, v):
        if not v.strip():
            raise ValueError("code is empty")
        if len(v.encode()) > MAX_CODE:
            raise ValueError("code too large")
        return v
