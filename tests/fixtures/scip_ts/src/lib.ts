export function helper(x: number): number {
  return x + 1;
}

export function other(): number {
  return helper(2);
}
