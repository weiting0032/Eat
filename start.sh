#!/usr/bin/env bash
set -e

mkdir -p data
mkdir -p uploads

exec supervisord -c supervisord.conf
