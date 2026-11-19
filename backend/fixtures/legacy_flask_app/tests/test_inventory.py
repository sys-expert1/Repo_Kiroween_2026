import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import inventory  # noqa: E402


def test_add_item_accumulates():
    inventory.ITEMS.clear()
    inventory.add_item("bolt", 2)
    assert inventory.add_item("bolt", 3) == 5


def test_total_items_known_failure():
    # Baseline failure on purpose: total_items ignores the reserved stock rule.
    inventory.ITEMS.clear()
    inventory.add_item("nut", 4)
    assert inventory.total_items() == 3
