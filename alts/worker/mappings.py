from alts.worker.runners import DockerRunner, OpennebulaRunner


__all__ = ['RUNNER_MAPPING']


RUNNER_MAPPING = {
    'docker': DockerRunner,
    'opennebula': OpennebulaRunner,
}

