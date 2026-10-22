import logging
import re
from typing import Iterable, List, Optional

REDACTED = '***'
# Secrets shorter than this are not masked by value to avoid mangling
# unrelated output (e.g. a password like "test" or "1")
MIN_SECRET_LENGTH = 6
# Matches "scheme://user:password@" and keeps the user part
URL_CREDENTIALS_REGEX = re.compile(
    r'(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@\'"]+:)'
    r'[^\s/@\'"]+@'
)


def prepare_secrets(secrets: Iterable[Optional[str]]) -> List[str]:
    """
    Filters out empty and too short secrets and sorts the rest by length,
    so longer secrets are replaced before their possible substrings.

    Parameters
    ----------
    secrets : Iterable[Optional[str]]
        Secret values.

    Returns
    -------
    List[str]
        Secret values suitable for masking.
    """
    return sorted(
        {
            str(secret)
            for secret in secrets
            if secret and len(str(secret)) >= MIN_SECRET_LENGTH
        },
        key=len,
        reverse=True,
    )


def redact_secrets(text, secrets: Iterable[str] = ()):
    """
    Masks credentials embedded into URLs and known secret values.

    Parameters
    ----------
    text : str
        Text to redact. Non-string values are returned as is.
    secrets : Iterable[str]
        Known secret values to mask, should be prepared
        with prepare_secrets.

    Returns
    -------
    str
        Redacted text.
    """
    if not text or not isinstance(text, str):
        return text
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return URL_CREDENTIALS_REGEX.sub(rf'\g<prefix>{REDACTED}@', text)


class RedactingFormatter(logging.Formatter):
    """
    Logging formatter that masks credentials in the formatted records.
    """

    def __init__(self, *args, secrets: Iterable[str] = (), **kwargs):
        super().__init__(*args, **kwargs)
        self._secrets = list(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record), self._secrets)
