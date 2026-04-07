#!/usr/bin/env bash
set -e

mkdir -p data
mkdir -p uploads

echo "Python version:"
python --version

echo "Pip version:"
pip --version

echo "Installing setuptools/wheel..."
python -m pip install --upgrade pip
python -m pip install setuptools wheel

echo "Testing pkg_resources..."
python - <<'PY'
import pkg_resources
print("pkg_resources OK")
PY

exec supervisord -c supervisord.conf
