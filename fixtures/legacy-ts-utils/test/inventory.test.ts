import assert from "node:assert/strict";
import { test } from "node:test";

import { addItem, reset, totalItems } from "../src/inventory.ts";

test("addItem accumulates", () => {
  reset();
  addItem("bolt", 2);
  assert.equal(addItem("bolt", 3), 5);
});

test("totalItems known failure", () => {
  // Baseline failure on purpose: total_items ignores the reserved stock rule.
  reset();
  addItem("nut", 4);
  assert.equal(totalItems(), 3);
});
