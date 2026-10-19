import { helper, other } from "./lib.ts";

export function main(): number {
  return helper(1);
}

export function also(): number {
  return helper(2) + other();
}
