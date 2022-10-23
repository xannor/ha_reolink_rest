#!/bin/sh

container install

if [ -d "./.libs" ]; then
    if [ -f "./.libs/api/base/pyproject.toml" ]; then
        CWD=$(pwd)
        cd ./.libs/api/base
        python -m pip install -e .
        cd "$CWD"
    fi
    if [ -f "./.libs/api/rest/pyproject.toml" ]; then
        CWD=$(pwd)
        cd ./.libs/api/rest
        python -m pip install -e .
        cd "$CWD"
    fi
    if [ -f "./.libs/reolink_discovery/hacs.json" ]; then
        cd custom_components
        ln -sf ../.libs/reolink_discovery/custom_components/reolink_discovery
        cd ..
    fi
fi

mkdir -p ./.scripts

CWD=$(pwd)
cd ./.scripts
mkdir -p ha_helpers
cd ha_helpers
if [ ! -d ".git"]; then
    git init
    git remote add -f origin "https://github.com/home-assistant/core"
    git config core.sparseCheckout true
    echo "script" > .git/info/sparse-checkout
fi
git pull origin master
cd "$CWD"