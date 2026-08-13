export type RemoteFileKind = "rf-json" | "tuning-json" | "positions-csv";

function basename(path: string): string {
  return path.replace(/\/+$/, "").split("/").at(-1)?.toLocaleLowerCase() ?? "";
}

export function acceptsRemoteFile(path: string, kind: RemoteFileKind): boolean {
  const name = basename(path);
  if (kind === "positions-csv") return name.endsWith(".probe") || name === "positions.csv";
  if (kind === "tuning-json") return name.endsWith(".tc") || name === "tuning_curves.json";
  return (name.endsWith(".rfmap") || name.endsWith(".json")) && name !== "tuning_curves.json";
}

export function hasArtifactExtension(path: string): boolean {
  return /\.(?:rfmap|json|tc|probe|csv)$/i.test(path);
}
