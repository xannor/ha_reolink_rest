#!/bin/sh

/usr/src/core/script/setup

if [ -f "../api/base/pyproject.toml" ]; then
    CWD=$(pwd)
    cd ../api/base
    python -m pip install -e .
    cd "$CWD"
fi
if [ -f "../api/rest/pyproject.toml" ]; then
    CWD=$(pwd)
    cd ../api/rest
    python -m pip install -e .
    cd "$CWD"
fi
if [ -f "../reolink_discovery/hacs.json" ]; then
    cd custom_components
    ln -s ../../reolink_discovery/custom_components/reolink_discovery reolink_discovery
fi

mkdir -p .config