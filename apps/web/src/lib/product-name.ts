export type ProductNameLength = "short" | "medium" | "long";

export function productNameLength(value: string): ProductNameLength {
  const length = Array.from(value.trim()).length;
  if (length > 28) return "long";
  if (length > 14) return "medium";
  return "short";
}

