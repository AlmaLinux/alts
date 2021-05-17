import os
from pathlib import Path

from alts.shared.config_loader import get_config_dict_from_yaml
from alts.shared.models import CeleryConfig


__all__ = ['CONFIG', 'CONFIG_FILE_PATH', 'RESOURCES_DIR']


CONFIG_FILE_PATH = os.path.abspath(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get('CELERY_CONFIG_PATH', '~/.config/alts/celery.yaml')
        )
    )
)
CONFIG = get_config_dict_from_yaml(CONFIG_FILE_PATH, CeleryConfig)
# Point to the project root directory
BASE_DIR = os.path.abspath(Path(os.path.dirname(__file__)) / '../..')
RESOURCES_DIR = os.path.join(BASE_DIR, 'resources')
