import os

import docker

_client = None


def get_docker_client():
    global _client
    if _client is None:
        _client = docker.from_env(timeout=int(os.getenv("DOCKER_API_TIMEOUT", "15")))
    return _client
