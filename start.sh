#!/usr/bin/env bash
set -e

mkdir -p data
mkdir -p uploads

echo "Python version:"
python --version

echo "Pip version:"
pip --version

echo "Installing compatible setuptools/wheel..."
python -m pip install --upgrade pip
python -m pip install setuptools==80.9.0 wheel==0.46.3

echo "Testing pkg_resources..."
python - <<'PY'
import pkg_resources
print("pkg_resources OK")
PY

exec supervisord -c supervisord.conf
