import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { canonical, hash, MAX_BYTES, parseSession } from "../../src/session";

const raw = readFileSync(
  new URL("../../public/session.json", import.meta.url),
  "utf8",
);
const fixture = () => JSON.parse(raw);
async function rehash(value: any): Promise<string> {
  return JSON.stringify({
    payload: value.payload,
    sha256: await hash(value.payload),
  });
}
describe("session format and integrity", () => {
  it("reads the Python export with Unicode and its independent digest", async () => {
    const session = await parseSession(raw);
    expect(session.checkpoints).toHaveLength(2);
    expect(session.transcript).toContain("Straße");
    expect(session.transcript).toContain("[REDACTED]");
    expect(raw).not.toContain("DEMO_TOKEN_NEVER_USE");
  });
  it("canonicalizes deterministically", () => {
    expect(canonical({ z: [null, 3], a: { x: "ß", b: false } })).toBe(
      '{"a":{"b":false,"x":"ß"},"z":[null,3]}',
    );
  });
  it.each([null, "", "{", "[]", "null", "false", "{}"])(
    "rejects malformed payload %j",
    async (input) => {
      await expect(parseSession(String(input))).rejects.toThrow();
    },
  );
  it("rejects oversized input before parsing", async () => {
    await expect(parseSession("x".repeat(MAX_BYTES + 1))).rejects.toThrow(
      "5 MiB",
    );
  });
  it("rejects a changed patch with the original digest", async () => {
    const value = fixture();
    value.payload.checkpoints[0].patch += "changed";
    await expect(parseSession(JSON.stringify(value))).rejects.toThrow(
      "digest mismatch",
    );
  });
  it.each([
    (v: any) => {
      v.extra = "private";
    },
    (v: any) => {
      v.payload.extra = "private";
    },
    (v: any) => {
      delete v.payload.provenance;
    },
    (v: any) => {
      v.payload.version = 2;
    },
    (v: any) => {
      v.payload.mapping = "inferred";
    },
    (v: any) => {
      v.payload.checkpoints = [];
    },
    (v: any) => {
      v.payload.checkpoints = Array(51).fill(v.payload.checkpoints[0]);
    },
    (v: any) => {
      v.payload.checkpoints[1].id = v.payload.checkpoints[0].id;
    },
    (v: any) => {
      v.payload.checkpoints[0].commit = "--exec=bad";
    },
    (v: any) => {
      v.payload.checkpoints[0].parent = "short";
    },
    (v: any) => {
      v.payload.checkpoints[0].history_lines = [0, 5];
    },
    (v: any) => {
      v.payload.checkpoints[0].history_lines = [5, 2];
    },
    (v: any) => {
      v.payload.checkpoints[0].history_lines = [1, 999];
    },
    (v: any) => {
      v.payload.checkpoints[0].history_lines = [1.5, 5];
    },
    (v: any) => {
      v.payload.checkpoints[0].excerpt = "wrong lines";
    },
    (v: any) => {
      v.payload.checkpoints[0].secret = "hidden";
    },
    (v: any) => {
      v.payload.redaction_count = -1;
    },
    (v: any) => {
      v.payload.redaction_count = 0.2;
    },
    (v: any) => {
      v.payload.title = "";
    },
    (v: any) => {
      v.payload.transcript = null;
    },
  ])("rejects invalid schema case %#", async (mutate) => {
    const value = fixture();
    mutate(value);
    // Recompute the digest where possible so validation, rather than just
    // the integrity mismatch, is exercised.
    const input = Object.hasOwn(value, "extra")
      ? JSON.stringify(value)
      : await rehash(value);
    await expect(parseSession(input)).rejects.toThrow();
  });
  it("keeps HTML-looking text literal", async () => {
    const value = fixture();
    value.payload.title = '<img src=x onerror="alert(1)">';
    expect((await parseSession(await rehash(value))).title).toBe(
      value.payload.title,
    );
  });
  it("bounds patch line count before rendering", async () => {
    const value = fixture();
    value.payload.checkpoints[0].patch = "x\n".repeat(10000);
    await expect(parseSession(await rehash(value))).rejects.toThrow(
      "10000 lines",
    );
  });
});
