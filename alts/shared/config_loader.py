# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""Configuration loader for Test System."""

import os
from typing import Type

from pydantic import BaseModel
from ruamel.yaml import YAML

from alts.shared.exceptions import ConfigNotFoundError


__all__ = ['get_config_dict_from_yaml']


def get_config_dict_from_yaml(file_path: str,
                              config_class: Type[BaseModel]) -> BaseModel:
    """

    Parameters
    ----------
    file_path : str
        Test System configuration file path.
    config_class : BaseModel
        Test System configuration.

    Returns
    -------
    BaseModel
        Test System configuration parsed from config file.

    Raises
    ------
    ConfigNotFoundError
        If config file doesn't exist at given path.

    """
    if not os.path.exists(file_path):
        raise ConfigNotFoundError(f'Cannot load file {file_path}')

    loader = YAML(typ='safe')
    with open(file_path, 'rt') as f:
        return config_class.parse_obj(loader.load(f))
