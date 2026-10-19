import path from "node:path";

export const LIMITS: number[] = [10, 20, 30];
export const TOTAL = LIMITS.reduce((a: number, b: number) => a + b, 0) * 2;
const NAME = "svc";
export const GREETING = NAME.toUpperCase() + "!";
const STAMP = Date.now();

export function add(a: number, b: number): number {
  return a + b;
}

export function shout(s: string, times: number): string {
  return (s.toUpperCase() + "!").repeat(Math.max(0, Math.floor(Math.abs(times)) % 4));
}

export const first = (xs: number[]): number | undefined => xs[0];

function inner(x: number): number {
  return x * 2;
}

export function roll(n: number): number {
  return Math.floor(Math.random() * n);
}

export function reads(): string {
  return process.cwd();
}

export function pure(opt?: string): string {
  return opt ?? "none";
}
