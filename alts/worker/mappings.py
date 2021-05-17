# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System environments mapping."""

from alts.worker.runners import DockerRunner, OpennebulaRunner


__all__ = ['RUNNER_MAPPING']


RUNNER_MAPPING = {
    'docker': DockerRunner,
    'opennebula': OpennebulaRunner,
}
