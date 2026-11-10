import logging
import os
import re
from tempfile import NamedTemporaryFile
from typing import IO, Iterable, List, Optional, Tuple

from alts.shared.constants import ERROR_STRINGS

REDACTED = '***'
# Secrets shorter than this are not masked by value to avoid mangling
# unrelated output (e.g. a password like "test" or "1")
MIN_SECRET_LENGTH = 6
# Matches "scheme://user:password@" and keeps the user part
URL_CREDENTIALS_REGEX = re.compile(
    r'(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@\'"]+:)'
    r'[^\s/@\'"]+@'
)


def get_temp_log_files(prefix: str) -> Tuple[IO, IO]:
    temp_file_kwargs = {
        'delete': False,
        'mode': 'w+',
        'prefix': prefix,
    }
    return (
        NamedTemporaryFile(**temp_file_kwargs, suffix='.stdout.log'),
        NamedTemporaryFile(**temp_file_kwargs, suffix='.stderr.log'),
    )


def read_and_cleanup_temp_log_files(
    out_file: IO[str],
    err_file: IO[str],
) -> Tuple[str, str]:
    for file in (out_file, err_file):
        file.seek(0)
    out = f'\n{out_file.read()}'
    err = f'\n{err_file.read()}'
    for file in (out_file, err_file):
        file.close()
        os.unlink(file.name)
    return out, err


def check_for_error_string(stderr: str) -> bool:
    """
    Checks if we encounter errors during testing that worth keepin VM alive.

    Parameters
    ----------
    stderr: str

    Returns
    -------
    bool
        True if we encounter any error from the list. False otherwise
    """
    if stderr and any(error_str in stderr for error_str in ERROR_STRINGS):
        return True
    return False


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


class RedactingFilter(logging.Filter):
    """
    Logging filter that masks credentials in the records themselves.

    Unlike RedactingFormatter it also covers handlers of the parent loggers
    the records propagate to (e.g. the Celery worker log), since logger
    filters run before any handler gets the record.
    """

    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets = list(secrets)
        self._formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage(), self._secrets)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = self._formatter.formatException(
                record.exc_info,
            )
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text, self._secrets)
        if record.stack_info:
            record.stack_info = redact_secrets(
                record.stack_info,
                self._secrets,
            )
        return True


class RedactingFormatter(logging.Formatter):
    """
    Logging formatter that masks credentials in the formatted records.
    """

    def __init__(self, *args, secrets: Iterable[str] = (), **kwargs):
        super().__init__(*args, **kwargs)
        self._secrets = list(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record), self._secrets)
