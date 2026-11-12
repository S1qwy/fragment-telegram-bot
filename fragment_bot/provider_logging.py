"""Keep upstream diagnostics from leaking gateway secrets or customer payloads."""

import logging


def safe_exception_summary(error):
    return type(error).__name__


class ProviderLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)

    def __getattr__(self, level):
        def emit(*args, **kwargs):
            if level in {"warning", "error", "exception", "critical"}:
                self.logger.warning(
                    "Provider operation reported an error; inspect gateway dashboard"
                )

        return emit


def get_logger(name):
    return ProviderLogger(name)
