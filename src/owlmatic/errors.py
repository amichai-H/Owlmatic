class OwlError(Exception):
    """A stable, safe-to-display application failure. Never interpolate secret payloads."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
