import logging


class SecretRedactingFormatter(logging.Formatter):
    def __init__(
        self,
        token: str,
        delegate: logging.Formatter | None = None,
    ) -> None:
        super().__init__()
        self.token = token
        self.delegate = delegate

    def format(self, record: logging.LogRecord) -> str:
        if self.delegate:
            message = self.delegate.format(record)
        else:
            message = super().format(record)
        if self.token:
            message = message.replace(self.token, "[REDACTED]")
        return message


def configure_secret_redaction(token: str) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not token:
        return
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root_logger.handlers:
        current = handler.formatter or logging.Formatter()
        if not isinstance(current, SecretRedactingFormatter):
            handler.setFormatter(SecretRedactingFormatter(token, current))
