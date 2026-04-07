#!/usr/bin/env bash
set -e

mkdir -p data
mkdir -p uploads

python --version
pip --version

exec supervisord -c supervisord.conf
