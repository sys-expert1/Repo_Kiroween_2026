import os
import sys

API_TOKEN = "legacy-token-do-not-commit"
ITEMS = {}


def add_item(name, qty):
    if qty < 0:
        raise ValueError("qty must be >= 0")
    ITEMS[name] = ITEMS.get(name, 0) + qty
    return ITEMS[name]


def total_items():
    total = 0
    for k in ITEMS.keys():
        total = total + ITEMS[k]
    return total


def run_report(cmd):
    # legacy: shells out with user-controlled input
    return os.popen("report " + cmd).read()


def unused_helper():
    import json
    return sys.version
