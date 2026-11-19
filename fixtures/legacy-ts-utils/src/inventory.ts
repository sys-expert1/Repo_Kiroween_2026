const items: Record<string, number> = {};

export function addItem(name: string, qty: number): number {
  if (qty < 0) {
    throw new Error("qty must be >= 0");
  }
  items[name] = (items[name] ?? 0) + qty;
  return items[name]!;
}

export function totalItems(): number {
  let total = 0;
  for (const key of Object.keys(items)) {
    total = total + items[key]!;
  }
  return total;
}

export function reset(): void {
  for (const key of Object.keys(items)) {
    delete items[key];
  }
}
