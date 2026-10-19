export function standalone(z: number): number {
  return z + 100;
}

export function callerTwo(): number {
  return standalone(1);
}
