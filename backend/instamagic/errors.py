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


class GeminiBlocked(NotAllowed):
    pass


class InstagramNotConfigured(NotConfigured):
    pass
