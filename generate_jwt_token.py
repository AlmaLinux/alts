#!/usr/bin/env python3
# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-05

"""AlmaLinux Test System generating JTW token script."""

import os
import sys
from argparse import ArgumentParser

from jose import jwt
from pydantic import BaseModel

from alts.shared.config_loader import get_config_dict_from_yaml


class Config(BaseModel):

    """Config for jwt token."""

    jwt_secret: str
    hash_algorithm: str = 'HS256'


def generate_token(jwt_secret: str, email: str,
                   hashing_algorithm: str = 'HS256'):
    """
    Generates jwt token for authenticating user.
    Parameters
    ----------
    jwt_secret : str
        Secret passphrase for generating token.
    email : str
        User's email for generating token.
    hashing_algorithm : str
        Hashing algorithm which will be used for encoding.

    Returns
    -------
    str
        Generated jwt token.
    """
    return jwt.encode({'email': email}, jwt_secret,
                      algorithm=hashing_algorithm)


def main():
    """
    Test System script for generating user's jwt token.

    Returns
    -------
    int
        Program exit code.
    """
    parser = ArgumentParser()
    parser.add_argument('-c', '--config', default='',
                        help='Path to config file')
    parser.add_argument('-e', '--email', default='',
                        help='E-mail of a person to generate token for')
    parser.add_argument('-a', '--hash-algorithm', default='HS256',
                        help='Algorithm to hash token')
    parser.add_argument('-s', '--jwt-secret', default='',
                        help='JWT secret for token generation')
    args = parser.parse_args()

    print()
    if not args.config and not args.jwt_secret:
        print('Need either config file or JWT secret for token generation')
        return 1

    if not args.email:
        print('Need E-mail address to generate token')
        return 1

    if args.config and args.jwt_secret:
        print('Specify either config file or JWT secret')
        return 1

    if args.config:
        config_path = os.path.abspath(os.path.expandvars(
            os.path.expanduser(args.config)))
        if not os.path.exists(config_path):
            print('Config file is missing')
            return 1

        config = get_config_dict_from_yaml(config_path, Config)
        if args.hash_algorithm:
            hash_algorithm = args.hash_algorithm
        else:
            hash_algorithm = config.hash_algorithm
        token = generate_token(config.jwt_secret, args.email,
                               hashing_algorithm=hash_algorithm)
        print(f'Token: {token}')
        return 0

    if args.jwt_secret:
        token = generate_token(args.jwt_secret, args.email,
                               hashing_algorithm=args.hash_algorithm)
        print(f'Token: {token}')
        return 0

    return 0


if __name__ == '__main__':
    sys.exit(main())
