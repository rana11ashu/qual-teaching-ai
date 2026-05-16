#!/bin/bash
set -e

source test-env/bin/activate
pip install -q -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
