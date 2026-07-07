class DVRException(Exception):

    STATUS_MESSAGES = {
        401: "Your session has expired. Please refresh the page and try again.",
        403: "You don't have permission to access this. Contact your admin.",
        404: "We couldn't find what you were looking for. Please check the details and retry.",
        429: "You're making too many requests. Please wait a moment and try again.",
        500: "Something went wrong on our end. Please try again in a bit.",
    }

    NETWORK_MESSAGES = {
        'timeout': "The request is taking too long. Please check your connection and try again.",
        'connection': "We're having trouble reaching the server. Please check your internet connection.",
    }

    def __init__(self, message: str, status_code: int = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    @classmethod
    def from_status_code(cls, status_code: int, detail: str = None):
        message = cls.STATUS_MESSAGES.get(
            status_code,
            "Something unexpected happened. Please try again."
        )
        if detail:
            message = f"{message} ({detail})"
        return cls(message, status_code)

    @classmethod
    def from_timeout(cls):
        return cls(cls.NETWORK_MESSAGES['timeout'])

    @classmethod
    def from_connection_error(cls):
        return cls(cls.NETWORK_MESSAGES['connection'])

    def to_dict(self):
        return {"type": "error", "message": self.message}
    