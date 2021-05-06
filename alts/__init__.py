import os

from shared.config_loader import get_config_dict_from_yaml
from shared.models import CeleryConfig


__all__ = ['CONFIG', 'CONFIG_FILE_PATH']


CONFIG_FILE_PATH = os.path.abspath(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get('CELERY_CONFIG_PATH', '~/.config/alts/celery.yaml')
        )
    )
)
CONFIG = get_config_dict_from_yaml(CONFIG_FILE_PATH, CeleryConfig)
