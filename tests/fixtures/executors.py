import os
from typing import Any, Dict

import pytest


@pytest.fixture(scope='session')
def local_ssh_credentials() -> Dict[str, Any]:
    credentials = {
        'host': os.getenv('SSH_HOST', 'localhost'),
        'username': os.getenv('SSH_USERNAME', 'root'),
        'ignore_encrypted_keys': os.getenv('IGNORE_ENCRYPTED_KEYS', True),
    }
    ssh_password = os.getenv('SSH_PASSWORD')
    ssh_public_key = os.getenv('SSH_PUBLIC_KEY')
    if ssh_password:
        credentials['password'] = ssh_password
    if ssh_public_key:
        credentials['client_keys_files'] = [ssh_public_key]
    return credentials
