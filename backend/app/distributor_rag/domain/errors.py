class AppError(Exception):
    def __init__(self, message: str, code: str = "application_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class ProviderError(AppError):
    def __init__(self, message: str = "A required service is unavailable. Please try again."):
        super().__init__(message, "provider_unavailable", 503)


class IngestionBusy(AppError):
    def __init__(self):
        super().__init__("Another indexing job is running. This document is queued.", "ingestion_busy", 409)
