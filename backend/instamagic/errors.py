class AppError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(AppError):
    status_code = 404


class NotAllowed(AppError):
    status_code = 409


class NotConfigured(AppError):
    status_code = 503


class GeminiResponseError(NotConfigured):
    """Gemini returned HTTP 200 but no usable model output."""


class GeminiRateLimited(NotConfigured):
    """Gemini answered HTTP 429 (after one retry). Callers back off instead of counting a failure."""


class GeminiEmpty(GeminiResponseError):
    pass


class GeminiTruncated(GeminiResponseError):
    pass


class GeminiBlocked(NotAllowed):
    def __init__(self, detail: str, *, reason: str | None = None) -> None:
        super().__init__(detail)
        self.reason = reason or detail


class InstagramNotConfigured(NotConfigured):
    pass
