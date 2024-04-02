# Unit tests
## Content
`conftest.py` - a module that setups pytest plugins and contains some base fixtures
`fixtures/` - a directory with pytest fixtures, a new module with fixtures should also be added in `conftest.pytest_plugins`

## How to run tests locally
1. Ensure that your SSH publickey is added to `~/.ssh/authorized_keys`
2. Start the sshd service
3. Export environment variables
```bash
CELERY_CONFIG_PATH="" # path to alts config
# Optionally you can set
SSH_USERNAME=""
SSH_PASSWORD=""
SSH_PRIVATE_KEY=""
IGNORE_ENCRYPTED_KEYS=True # ignore encrypted keys when no passphrase is specified
```
4. Create a python environment and install dependencies
```bash
python3 -m venv env
source env/bin/activate
pip3 install -r requirements/scheduler.txt -r requirements/devel.txt
```
5. Run tests
```bash
pytest -v
```
