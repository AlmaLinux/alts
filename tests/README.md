# Unit tests
## Content
`conftest.py` - a module that setups pytest plugins and contains some base fixtures
`fixtures/` - a directory with pytest fixtures, a new module with fixtures should also be added in `conftest.pytest_plugins`

## How to run tests locally
1. Create a python environment and install dependencies
```bash
python -m venv env && \
source env/bin/activate && \
pip install -U pip && \
pip install -r requirements/celery.txt
```
2. Start the sshd service
3. Ensure that your SSH publickey is added to `~/.ssh/authorized_keys`
4. Export environment variables
```bash
CELERY_CONFIG_PATH="" # path to alts config
SSH_USERNAME=""
SSH_PASSWORD="" # optional
SSH_PRIVATE_KEY=""
IGNORE_ENCRYPTED_KEYS=True # ignore encrypted keys when no passphrase is specified
```
5. Run tests
```bash
source env/bin/activate && pytest tests/ -v
```
