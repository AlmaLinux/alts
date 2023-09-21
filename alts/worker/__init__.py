# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System testing environments worker."""

import os
from pathlib import Path

from alts.shared.config_loader import get_config_dict_from_yaml
from alts.shared.models import CeleryConfig
from alts.shared.utils.path_utils import get_abspath


__all__ = ['CONFIG', 'CONFIG_FILE_PATH', 'RESOURCES_DIR']


CONFIG_FILE_PATH = get_abspath(
    os.environ.get('CELERY_CONFIG_PATH', '~/.config/alts/celery.yaml'))
CONFIG = get_config_dict_from_yaml(CONFIG_FILE_PATH, CeleryConfig)
# Point to the project root directory
BASE_DIR = os.path.abspath(Path(os.path.dirname(__file__)) / '../..')
RESOURCES_DIR = os.path.join(BASE_DIR, 'resources')
