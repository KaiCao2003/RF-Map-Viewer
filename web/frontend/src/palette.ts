import type { Palette } from "./types";
import { clamp } from "./math";

type Rgb = readonly [number, number, number];
type Stop = readonly [number, Rgb];

function interpolate(a: number, b: number, amount: number): number {
  return Math.round(a + (b - a) * amount);
}

function rgbToCss(rgb: Rgb): string {
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function gradient(amount: number, stops: readonly Stop[]): string {
  const value = clamp(amount, 0, 1);
  for (let index = 0; index < stops.length - 1; index += 1) {
    const [leftPosition, left] = stops[index];
    const [rightPosition, right] = stops[index + 1];
    if (leftPosition <= value && value <= rightPosition) {
      const local = (value - leftPosition) / Math.max(rightPosition - leftPosition, Number.EPSILON);
      return rgbToCss([
        interpolate(left[0], right[0], local),
        interpolate(left[1], right[1], local),
        interpolate(left[2], right[2], local),
      ]);
    }
  }
  return rgbToCss(stops.at(-1)![1]);
}

const viridis: Stop[] = [
  [0, [68, 1, 84]],
  [0.25, [59, 82, 139]],
  [0.5, [33, 145, 140]],
  [0.75, [94, 201, 98]],
  [1, [253, 231, 37]],
];

const inferno: Stop[] = [
  [0, [22, 11, 57]],
  [0.25, [90, 18, 110]],
  [0.5, [190, 54, 85]],
  [0.75, [249, 140, 10]],
  [1, [252, 255, 164]],
];

const delayStops: Stop[] = [
  [0, [47, 88, 167]],
  [0.35, [44, 171, 184]],
  [0.68, [246, 204, 89]],
  [1, [203, 71, 45]],
];

export function paletteColor(value: number | null, low: number, high: number, palette: Palette): string {
  if (value == null || !Number.isFinite(value)) return "#e6e8eb";
  const amount = clamp((value - low) / Math.max(high - low, Number.EPSILON), 0, 1);
  if (palette === "Gray") {
    const shade = Math.round(18 + amount * 232);
    return `rgb(${shade}, ${shade}, ${shade})`;
  }
  return gradient(amount, palette === "Inferno" ? inferno : viridis);
}

export function responseRangeForPalette(
  finiteLow: number,
  finiteHigh: number,
  palette: Palette,
): readonly [number, number] {
  return palette === "Gray" ? [finiteLow, finiteHigh] : [0, finiteHigh];
}

export function delayColor(value: number | null, low: number, high: number): string {
  if (value == null || !Number.isFinite(value)) return "#eceff2";
  return gradient((value - low) / Math.max(high - low, Number.EPSILON), delayStops);
}

export function rgbComposite(total: number | null, delay: number | null, entropy: number | null, maxTotal: number, minDelay: number, maxDelay: number): string {
  if (total == null || !Number.isFinite(total)) return "#e6e8eb";
  if (total <= 0) return "#000000";
  return rgbToCss([
    Math.round(clamp(total / Math.max(maxTotal, 1), 0, 1) * 255),
    Math.round(clamp(((delay ?? minDelay) - minDelay) / Math.max(maxDelay - minDelay, 1), 0, 1) * 255),
    Math.round(clamp(entropy ?? 0, 0, 1) * 255),
  ]);
}

export function colorLuminance(cssColor: string): number {
  const values = cssColor.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [230, 232, 235];
  return (0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]) / 255;
}
