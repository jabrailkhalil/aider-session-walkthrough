export const MAX_BYTES = 5 * 1024 * 1024;
export type Checkpoint = {
  id: string;
  title: string;
  commit: string;
  parent: string | null;
  history_lines: [number, number];
  excerpt: string;
  patch: string;
};
export type Session = {
  version: 1;
  title: string;
  provenance: string;
  mapping: "explicit";
  transcript: string;
  redaction_count: number;
  checkpoints: Checkpoint[];
};
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Expected an object");
  return value as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, expected: string[]): void {
  if (Object.keys(value).sort().join(",") !== expected.sort().join(","))
    throw new Error("Unexpected or missing fields");
}
function text(value: unknown, allowEmpty = false): string {
  if (
    typeof value !== "string" ||
    (!allowEmpty && !value.trim()) ||
    value.includes("\0")
  )
    throw new Error("Invalid text field");
  return value;
}
function sha(value: unknown): string {
  const s = text(value);
  if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(s))
    throw new Error("Invalid commit SHA");
  return s;
}
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    return `{${Object.keys(obj)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(obj[k])}`)
      .join(",")}}`;
  }
  const result = JSON.stringify(value);
  if (result === undefined) throw new Error("Invalid JSON value");
  return result;
}
export async function hash(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonical(value));
  const buffer = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(buffer), (v) =>
    v.toString(16).padStart(2, "0"),
  ).join("");
}
export async function parseSession(raw: string): Promise<Session> {
  if (new TextEncoder().encode(raw).byteLength > MAX_BYTES)
    throw new Error("Session exceeds 5 MiB");
  const envelope = object(JSON.parse(raw));
  keys(envelope, ["payload", "sha256"]);
  const p = object(envelope.payload);
  keys(p, [
    "version",
    "title",
    "provenance",
    "mapping",
    "transcript",
    "redaction_count",
    "checkpoints",
  ]);
  if (p.version !== 1 || p.mapping !== "explicit")
    throw new Error("Unsupported session format");
  if (
    !Array.isArray(p.checkpoints) ||
    p.checkpoints.length < 1 ||
    p.checkpoints.length > 50
  )
    throw new Error("Expected 1–50 checkpoints");
  if (
    !Number.isSafeInteger(p.redaction_count) ||
    (p.redaction_count as number) < 0
  )
    throw new Error("Invalid redaction count");
  const transcript = text(p.transcript, true);
  if (transcript.includes("\r"))
    throw new Error("Transcript must be LF-normalized text");
  const lines = transcript.split("\n");
  const checkpoints = p.checkpoints.map((rawCheckpoint) => {
    const c = object(rawCheckpoint);
    keys(c, [
      "id",
      "title",
      "commit",
      "parent",
      "history_lines",
      "excerpt",
      "patch",
    ]);
    const bounds = c.history_lines;
    if (
      !Array.isArray(bounds) ||
      bounds.length !== 2 ||
      bounds.some((v) => !Number.isSafeInteger(v)) ||
      bounds[0] < 1 ||
      bounds[1] < bounds[0] ||
      bounds[1] > lines.length
    )
      throw new Error("Invalid history line range");
    if (c.excerpt !== lines.slice(bounds[0] - 1, bounds[1]).join("\n"))
      throw new Error("Excerpt does not match the reviewed transcript");
    const patch = text(c.patch, true);
    if (
      new TextEncoder().encode(patch).byteLength > 2 * 1024 * 1024 ||
      patch.split("\n").length > 10000
    )
      throw new Error("Patch exceeds 2 MiB or 10000 lines");
    return {
      id: text(c.id),
      title: text(c.title),
      commit: sha(c.commit),
      parent: c.parent === null ? null : sha(c.parent),
      history_lines: bounds as [number, number],
      excerpt: text(c.excerpt, true),
      patch,
    };
  });
  if (new Set(checkpoints.map((c) => c.id)).size !== checkpoints.length)
    throw new Error("Duplicate checkpoint ID");
  const result: Session = {
    version: 1,
    title: text(p.title),
    provenance: text(p.provenance),
    mapping: "explicit",
    transcript,
    redaction_count: p.redaction_count as number,
    checkpoints,
  };
  if (envelope.sha256 !== (await hash(p)))
    throw new Error("Content digest mismatch");
  return result;
}
