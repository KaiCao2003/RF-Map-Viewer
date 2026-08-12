export const CANVAS_FONT_FAMILY = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

export function canvasFont(sizePx: number, weight?: number): string {
  return `${weight == null ? "" : `${weight} `}${sizePx}px ${CANVAS_FONT_FAMILY}`;
}
