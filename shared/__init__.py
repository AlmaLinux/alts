from alts.runners import DockerRunner


__all__ = ['RUNNER_MAPPING']


RUNNER_MAPPING = {
    'docker': DockerRunner
}
