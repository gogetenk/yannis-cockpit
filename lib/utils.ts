import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtKg(w: number) {
  return w.toFixed(1).replace(".", ",") + " kg";
}

export function fmtDelta(d: number) {
  const sign = d > 0 ? "+" : d < 0 ? "−" : "";
  return sign + Math.abs(d).toFixed(1).replace(".", ",") + " kg";
}

const MONTHS_FR = [
  "jan", "fév", "mar", "avr", "mai", "juin",
  "juil", "août", "sept", "oct", "nov", "déc",
];

export function fmtDateFr(d: Date) {
  return d.getDate() + " " + MONTHS_FR[d.getMonth()] + " " + d.getFullYear();
}

export function fmtDateLong(d: Date) {
  return d.getDate() + " " + MONTHS_FR[d.getMonth()] + " " + d.getFullYear();
}

export function fmtHeaderDate(d: Date) {
  const dows = ["DIM", "LUN", "MAR", "MER", "JEU", "VEN", "SAM"];
  return `${dows[d.getDay()]} ${d.getDate()} ${MONTHS_FR[d.getMonth()].toUpperCase()}`;
}
