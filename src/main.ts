import "./style.css";
import { MAX_BYTES, parseSession, type Session } from "./session";

const app = document.querySelector<HTMLDivElement>("#app")!;
const status = document.querySelector<HTMLParagraphElement>("#import-status")!;
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}
let session: Session;
let current = 0;
let loadSequence = 0;
async function load(raw: string, sequence: number): Promise<void> {
  const parsed = await parseSession(raw);
  if (sequence !== loadSequence) return;
  session = parsed;
  current = 0;
  status.textContent = "";
  render(false);
}
function render(focus: boolean): void {
  app.replaceChildren();
  app.setAttribute("aria-busy", "false");
  const summary = el("section");
  summary.className = "session-summary";
  summary.append(
    el("h2", session.title),
    el("p", session.provenance),
    el(
      "p",
      `Explicit checkpoint mapping · ${session.redaction_count} redaction replacements`,
    ),
  );
  const grid = el("div");
  grid.className = "workspace";
  const nav = el("nav");
  nav.setAttribute("aria-label", "Checkpoints");
  for (const [index, checkpoint] of session.checkpoints.entries()) {
    const button = el("button", `${index + 1}. ${checkpoint.title}`);
    button.className = "checkpoint";
    if (index === current) button.setAttribute("aria-current", "step");
    button.onclick = () => {
      current = index;
      render(true);
    };
    nav.append(button);
  }
  const checkpoint = session.checkpoints[current]!;
  const article = el("article");
  const heading = el("h2", checkpoint.title);
  heading.tabIndex = -1;
  const meta = el(
    "p",
    `Commit ${checkpoint.commit} · parent ${checkpoint.parent ?? "none (root commit)"}`,
  );
  meta.className = "commit";
  const excerpt = el("pre", checkpoint.excerpt);
  excerpt.className = "excerpt";
  const patch = el("pre");
  patch.className = "patch";
  for (const line of checkpoint.patch.split("\n")) {
    const span = el("span", line + "\n");
    if (line.startsWith("+")) span.className = "added";
    else if (line.startsWith("-")) span.className = "removed";
    else if (line.startsWith("@@")) span.className = "hunk";
    patch.append(span);
  }
  const controls = el("div");
  controls.className = "controls";
  const previous = el("button", "Previous checkpoint");
  previous.disabled = current === 0;
  previous.onclick = () => {
    current--;
    render(true);
  };
  const next = el("button", "Next checkpoint");
  next.disabled = current === session.checkpoints.length - 1;
  next.onclick = () => {
    current++;
    render(true);
  };
  controls.append(
    previous,
    el("span", `${current + 1} / ${session.checkpoints.length}`),
    next,
  );
  article.append(
    heading,
    meta,
    el(
      "h3",
      `Selected conversation · lines ${checkpoint.history_lines.join("–")}`,
    ),
    excerpt,
    el("h3", "Recorded Git patch"),
    patch,
    controls,
  );
  grid.append(nav, article);
  const history = el("details");
  const transcript = el("pre", session.transcript);
  history.append(el("summary", "Full reviewed transcript"), transcript);
  app.append(summary, grid, history);
  if (focus) heading.focus();
}
document
  .querySelector<HTMLInputElement>("#session-file")!
  .addEventListener("change", async (event) => {
    const sequence = ++loadSequence;
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      if (file.size > MAX_BYTES) throw new Error("Session exceeds 5 MiB");
      await load(await file.text(), sequence);
    } catch (error) {
      if (sequence === loadSequence)
        status.textContent =
          error instanceof Error ? error.message : "Unable to read session";
    }
  });
const initialSequence = loadSequence;
void fetch("./session.json")
  .then(async (response) => {
    if (!response.ok)
      throw new Error("The example session could not be loaded");
    await load(await response.text(), initialSequence);
  })
  .catch((error: unknown) => {
    if (initialSequence !== loadSequence) return;
    app.setAttribute("aria-busy", "false");
    status.textContent =
      error instanceof Error ? error.message : "Unable to load example";
    app.replaceChildren();
  });
