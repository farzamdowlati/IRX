#!/usr/bin/env python3
"""Shared config loader for irx tools — flat KEY=VALUE .env at the repo root.
No secrets in code; see .env.example. Stdlib only."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

def _read_env(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env

ENV = _read_env(os.path.join(HERE, ".env"))

def get(key, default=""):
    v = ENV.get(key, "")
    return v if v else default

def get_int(key, default):
    try:
        return int(get(key, "") or default)
    except ValueError:
        return default

def get_float(key, default):
    try:
        return float(get(key, "") or default)
    except ValueError:
        return default

DATA_DIR = os.path.join(HERE, "data")
