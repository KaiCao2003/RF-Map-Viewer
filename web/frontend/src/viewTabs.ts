import type { ViewTab } from "./types";

export interface ViewerTabDefinition {
  key: ViewTab;
  label: string;
  short: string;
}

export const VIEWER_TABS: ViewerTabDefinition[] = [
  { key: "rf", label: "RF", short: "1" },
  { key: "delay", label: "Delay / RGB", short: "2" },
  { key: "timeline", label: "Timeline", short: "3" },
];
