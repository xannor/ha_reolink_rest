ARG VARIANT=0-3.9
FROM mcr.microsoft.com/vscode/devcontainers/python:${VARIANT}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN \
    curl -sS https://dl.yarnpkg.com/debian/pubkey.gpg | apt-key add - \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        # Additional library needed by some tests and accordingly by VScode Tests Discovery
        bluez \
        libudev-dev \
        libavformat-dev \
        libavcodec-dev \
        libavdevice-dev \
        libavutil-dev \
        libswscale-dev \
        libswresample-dev \
        libavfilter-dev \
        libpcap-dev \
        libturbojpeg0 \
        libyaml-dev \
        libxml2 \
        git \
        cmake \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src

# Setup hass-release
RUN git clone --depth 1 https://github.com/home-assistant/hass-release \
    && pip3 install -e hass-release/

RUN git clone https://github.com/home-assistant/core

WORKDIR /usr/src/core

# Install Python dependencies from requirements
RUN pip3 install -r requirements.txt --use-deprecated=legacy-resolver
RUN pip3 install -r requirements_test.txt --use-deprecated=legacy-resolver

WORKDIR /workspaces

# Set the default shell to bash instead of sh
ENV SHELL /bin/bash