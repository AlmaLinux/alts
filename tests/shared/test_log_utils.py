import io
import logging

import pytest

from alts.shared.utils.log_utils import (
    REDACTED,
    RedactingFormatter,
    prepare_secrets,
    redact_secrets,
)


@pytest.mark.parametrize(
    'text, expected',
    [
        (
            'baseurl=https://alts:s3cr3tT0ken@build.almalinux.org/repo/',
            f'baseurl=https://alts:{REDACTED}@build.almalinux.org/repo/',
        ),
        (
            'deb [arch=amd64] https://alts:tok@host/repo ./',
            f'deb [arch=amd64] https://alts:{REDACTED}@host/repo ./',
        ),
        (
            "{'url': 'https://alts:tok@host/repo', 'name': 'x-br'}",
            f"{{'url': 'https://alts:{REDACTED}@host/repo', 'name': 'x-br'}}",
        ),
        (
            'Failure talking to https://user@host/repo',
            'Failure talking to https://user@host/repo',
        ),
        (
            'see https://host:8080/path and foo@bar',
            'see https://host:8080/path and foo@bar',
        ),
    ],
)
def test_redact_url_credentials(text, expected):
    assert redact_secrets(text) == expected


def test_redact_known_secrets():
    secrets = prepare_secrets(['onepassword', 'abc', None, ''])
    assert secrets == ['onepassword']
    text = 'TF_LOG: opennebula_password = "onepassword", abc'
    assert redact_secrets(text, secrets) == (
        f'TF_LOG: opennebula_password = "{REDACTED}", abc'
    )


@pytest.mark.parametrize('value', [None, '', b'bytes', 1])
def test_redact_non_text(value):
    assert redact_secrets(value, ['secret-value']) == value


def test_redacting_formatter():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        RedactingFormatter('%(message)s', secrets=['top-secret'])
    )
    logger = logging.getLogger('test-redacting-formatter')
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info(
        'Repositories: %s, token %s',
        [{'url': 'https://alts:tok@host/repo'}],
        'top-secret',
    )
    output = stream.getvalue()
    assert 'tok@' not in output
    assert 'top-secret' not in output
    assert f'https://alts:{REDACTED}@host/repo' in output
