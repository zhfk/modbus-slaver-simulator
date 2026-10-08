class DomainError(Exception):
    def __init__(self, message: str, status: int = 422, details=None):
        super().__init__(message)
        self.message, self.status, self.details = message, status, details


class ProtocolError(Exception):
    def __init__(self, code: int):
        self.code = code
