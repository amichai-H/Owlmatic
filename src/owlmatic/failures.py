"""Safe exception serialization shared by public transports and worker diagnostics."""

import errno

from pydantic import ValidationError

from .domain import Failure
from .errors import OwlError


def public_failure(error: Exception) -> Failure:
    if isinstance(error, OwlError):
        return Failure(code=error.code, message=str(error))
    if isinstance(error, PermissionError):
        return Failure(code="ACCESS_DENIED", message="Check permissions for the requested local resource")
    if isinstance(error, FileNotFoundError):
        return Failure(code="PATH_NOT_FOUND", message="A required local file or directory is missing")
    if isinstance(error, OSError):
        if error.errno == errno.ENOSPC:
            return Failure(code="STORAGE_FULL", message="Free local disk space, then retry recovery")
        return Failure(code="IO_ERROR", message="Local I/O failed; check storage and runtime availability")
    if isinstance(error, (ValueError, ValidationError)):
        return Failure(code="INVALID_REQUEST", message="Check paths and input contracts")
    return Failure(
        code="INTERNAL_ERROR", message="Unexpected runtime failure; inspect installation and local storage"
    )
