#!/bin/bash

echo -ne DOCKER_HOST_IP= > ./.devcontainer/devcontainer.env
getent ahostsv4 host.docker.internal | head -n1 | cut -d' ' -f1 >> ./.devcontainer/devcontainer.env

echo -ne DOCKER_GATEWAY_IP= >> ./.devcontainer/devcontainer.env
getent ahostsv4 gateway.docker.internal | head -n1 | cut -d' ' -f1 >> ./.devcontainer/devcontainer.env

