#!/usr/bin/env bash
# Arranca la app. La inferencia corre en este dispositivo.
set -e
cd "$(dirname "$0")"
exec streamlit run app.py
