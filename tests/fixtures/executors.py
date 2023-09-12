import os

import asyncssh
import pytest


@pytest.fixture(scope='session')
def local_ssh_credentials():
    credentials = {
        'host': os.getenv('SSH_HOST', 'localhost'),
        'username': os.getenv('SSH_USERNAME', 'root'),
        'ignore_encrypted_keys': bool(os.getenv('IGNORE_ENCRYPTED_KEYS')),
    }
    ssh_password = os.getenv('SSH_PASSWORD')
    ssh_public_key = os.getenv('SSH_PRIVATE_KEY')
    if ssh_password:
        credentials['password'] = ssh_password
    if ssh_public_key:
        credentials['client_keys_files'] = [ssh_public_key]
    return credentials
