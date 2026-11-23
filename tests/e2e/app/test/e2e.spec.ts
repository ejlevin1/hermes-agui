/**
 * Contract test: real CopilotKit 1.90.0 (browser) -> real hermes_agui bridge (tests/e2e/host_app.py) -> real Hermes
 * gateway (live sha, plugin/ installed, scripted mock LLM). State (ports, paths) comes from $E2E_STATE (stack.py).
 */
import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { readFileSync, existsSync } from "node:fs";

const ST = JSON.parse(readFileSync(process.env.E2E_STATE!, "utf8"));
const BASE = `http://127.0.0.1:${ST.host_port}`;
const HDR = { "X-Test-User": "alice" };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const uid = () => Math.random().toString(36).slice(2, 8);

type Ev = Record<string, any>;
const dbg = async (): Promise<Ev[]> => (await fetch(`${BASE}/__debug/log`)).json();
const dbgFor = async (sid: string) => (await dbg()).filter((e) => e.session_id === sid);
const mockReqs = (): Ev[] => (existsSync(ST.mock_log) ? readFileSync(ST.mock_log, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)).filter((r) => r.req?.messages && r.req?.tools) : []);
const sidOf = (surface: string, tid: string) => `agui:${surface}:alice:${tid}`;

type Tracked = { page: Page; runs: Ev[]; stops: string[]; ctx: BrowserContext };
async function open(ctx: BrowserContext, agent: string, tid: string): Promise<Tracked> {
  const page = await ctx.newPage();
  const t: Tracked = { page, runs: [], stops: [], ctx };
  page.on("request", (r) => {
    if (r.method() !== "POST") return;
    if (r.url().includes(`/agent/${agent}/run`)) t.runs.push(JSON.parse(r.postData() || "{}"));
    if (r.url().includes("/stop/")) t.stops.push(r.url());
  });
  await page.goto(`${BASE}/app/index.html?agent=${agent}&tid=${tid}`);
  await expect(page.getByTestId("send")).toBeVisible();
  return t;
}
const say = async (p: Page, text: string) => { await p.getByTestId("draft").fill(text); await p.getByTestId("send").click(); };
const lastAssistant = (p: Page) => p.getByTestId("msg-assistant").last();

let ctx: BrowserContext;
test.beforeEach(async ({ browser }) => { ctx = await browser.newContext({ extraHTTPHeaders: HDR }); });
test.afterEach(async () => { await ctx.close(); });

/** Raw bridge call (what the browser cannot show: heartbeat comments, exact event sequence). */
async function rawRun(surface: string, body: Ev, onChunk?: (t: number, s: string) => void) {
  const t0 = Date.now(); let text = "";
  const r = await fetch(`${BASE}/api/copilotkit/agent/${surface}/run`, { method: "POST", headers: { ...HDR, "content-type": "application/json", accept: "text/event-stream" }, body: JSON.stringify(body) });
  const reader = r.body!.getReader(); const dec = new TextDecoder();
  for (;;) { const { done, value } = await reader.read(); if (done) break; const s = dec.decode(value); text += s; onChunk?.(Date.now() - t0, s); }
  return { status: r.status, text, ms: Date.now() - t0 };
}
const userMsg = (text: string) => ({ id: `m-${uid()}`, role: "user", content: text });
const runBody = (tid: string, messages: Ev[]) => ({ threadId: tid, runId: `r-${uid()}`, state: {}, messages, tools: [], context: [], forwardedProps: {} });

test("1 text streams into the UI", async () => {
  const tid = `t-text-${uid()}`; const t = await open(ctx, "test.form", tid);
  await say(t.page, "[[SC:text]] hello");
  await expect(lastAssistant(t.page)).toHaveText("Hello from the mock.", { timeout: 30000 });
  expect(t.runs.length).toBe(1);
  expect(t.runs[0].threadId).toBe(tid);
});

test("2 frontend tool across two runs: form changes, final text quotes per-field result", async () => {
  const tid = `t-fill-${uid()}`; const t = await open(ctx, "test.form", tid);
  await say(t.page, "[[SC:fill]] fill it");
  await expect(t.page.getByTestId("f-title")).toHaveValue("Dune", { timeout: 30000 });
  await expect(t.page.getByTestId("f-year")).toHaveValue("1965");
  await expect(lastAssistant(t.page)).toContainText('"bogus": "unknown_field"', { timeout: 30000 });
  const txt = (await lastAssistant(t.page).textContent()) || "";
  expect(txt).toContain('"title": "ok"'); expect(txt).toContain('"year": "ok"');
  expect(t.runs.length).toBe(2);
  expect(t.runs[1].threadId).toBe(tid); expect(t.runs[1].runId).not.toBe(t.runs[0].runId);
  const last = t.runs[1].messages.at(-1); expect(last.role).toBe("tool");
  expect(JSON.parse(last.content)).toEqual({ title: "ok", year: "ok", bogus: "unknown_field" });
  const log = await dbgFor(sidOf("test.form", tid));
  expect(log.find((e) => e.ev === "register_call")).toMatchObject({ name: "set_fields", errors: null });
  expect(log.find((e) => e.ev === "resolve")).toMatchObject({ accepted: true });
});

test("3 human tool waits for the click (nothing happens for 5 s)", async () => {
  const tid = `t-human-${uid()}`; const t = await open(ctx, "test.form", tid);
  await say(t.page, "[[SC:choice]] ask me");
  const card = t.page.getByTestId("choice-card");
  await expect(card).toBeVisible({ timeout: 30000 }); await expect(card).toHaveAttribute("data-status", "executing");
  await expect(t.page.getByTestId("choice-Movie")).toBeEnabled();
  const sid = sidOf("test.form", tid);
  await sleep(5000);
  expect(t.runs.length).toBe(1);                                   // no run 2 yet
  expect((await dbgFor(sid)).filter((e) => e.ev === "resolve")).toEqual([]);
  await expect(t.page.getByTestId("msg-assistant").filter({ hasText: "Done." })).toHaveCount(0);
  await t.page.getByTestId("choice-Movie").click();
  await expect(lastAssistant(t.page)).toContainText('"choice": "Movie"', { timeout: 30000 });
  expect(t.runs.length).toBe(2);
  await expect(card).toHaveAttribute("data-status", "complete");
});

test("4 stop during a human wait: handler exits <= 2 s, late result rejected, chat stopped", async () => {
  const tid = `t-stop-${uid()}`; const t = await open(ctx, "test.form", tid); const sid = sidOf("test.form", tid);
  await say(t.page, "[[SC:choice]] ask me");
  await expect(t.page.getByTestId("choice-card")).toBeVisible({ timeout: 30000 });
  await expect.poll(async () => (await dbgFor(sid)).filter((e) => e.ev === "poll").length, { timeout: 15000 }).toBeGreaterThan(0); // gateway handler is polling
  const calls = (await dbgFor(sid)).filter((e) => e.ev === "register_call"); expect(calls.length).toBe(1);
  const callId = calls[0].call; const modelCallsBefore = mockReqs().length;
  const tStop = Date.now();
  await t.page.getByTestId("stop").click();                        // UI Stop: CopilotKit 1.90.0 DOES call POST stop/:thread while idle (ui_sent_stop below)
  await sleep(500);
  const uiSentStop = t.stops.length > 0;
  const runId = t.runs[0].runId;
  const sr = await fetch(`${BASE}/api/copilotkit/agent/test.form/stop/${tid}`, { method: "POST", headers: { ...HDR, "content-type": "application/json" }, body: JSON.stringify({ runId }) });
  expect(sr.status).toBe(200);
  console.log(`stop: ui_sent_stop=${uiSentStop} http=${sr.status} body=${JSON.stringify(await sr.json().catch(() => null))}`);
  // handler exit == its poll got 410 (dropped) and no poll follows
  await expect.poll(async () => (await dbgFor(sid)).some((e) => e.ev === "drop_session"), { timeout: 5000 }).toBe(true);
  await expect.poll(async () => (await dbg()).filter((e) => e.ev === "poll" && e.call === callId && e.status === 410).length, { timeout: 5000 }).toBeGreaterThan(0);
  const gone = (await dbg()).filter((e) => e.ev === "poll" && e.call === callId && e.status === 410)[0];
  const exitMs = gone.t * 1000 - tStop; console.log(`stop->handler exit (410 poll): ${exitMs.toFixed(0)} ms`);
  expect(exitMs).toBeLessThanOrEqual(2000);
  const n = (await dbg()).filter((e) => e.ev === "poll" && e.call === callId).length; await sleep(3000);
  expect((await dbg()).filter((e) => e.ev === "poll" && e.call === callId).length).toBe(n); // handler really stopped polling
  expect((await dbg()).some((e) => e.ev === "hermes_stop")).toBe(true);
  // late result: click the choice in the UI -> run 2 carries a tool message -> rejected
  await t.page.getByTestId("choice-Book").click({ timeout: 5000 }).catch(() => {});
  const late = await rawRun("test.form", runBody(tid, [userMsg("x"), { id: `tm-${uid()}`, role: "tool", toolCallId: callId, content: JSON.stringify({ choice: "Book" }) }]));
  expect(late.text).toContain("RUN_ERROR"); expect(late.text).toContain("tool_result_rejected");
  expect((await dbgFor(sid)).filter((e) => e.ev === "resolve" && e.accepted)).toEqual([]);
  await expect(t.page.getByTestId("running")).toHaveCount(0);
  await expect(t.page.getByTestId("msg-assistant").filter({ hasText: "Done." })).toHaveCount(0);
  expect(mockReqs().length).toBe(modelCallsBefore);               // the model was never asked again
});

test("5 reload: connect replays the earlier messages", async () => {
  const tid = `t-reload-${uid()}`; const t = await open(ctx, "test.form", tid);
  await say(t.page, "[[SC:text]] remember me");
  await expect(lastAssistant(t.page)).toHaveText("Hello from the mock.", { timeout: 30000 });
  await expect(t.page.getByTestId("running")).toHaveCount(0, { timeout: 15000 }); await sleep(500); // let the bridge finish the run (store the reply) before the client goes away
  await t.page.close();
  // connect replays a one-shot snapshot of what Hermes has persisted: wait until the assistant reply is stored too
  await expect.poll(async () => (await fetch(`${BASE}/api/copilotkit/agent/test.form/connect`, { method: "POST", headers: { ...HDR, "content-type": "application/json" }, body: JSON.stringify({ threadId: tid, runId: `c-${uid()}` }) })).text(), { timeout: 15000 }).toContain("Hello from the mock.");
  const t2 = await open(ctx, "test.form", tid);                    // fresh page: client has nothing
  await expect(t2.page.getByTestId("msg-user")).toHaveCount(0);
  await t2.page.getByTestId("reconnect").click();
  await expect(t2.page.getByTestId("msg-user").first()).toContainText("remember me", { timeout: 15000 });
  await expect(lastAssistant(t2.page)).toHaveText("Hello from the mock.", { timeout: 15000 });
  const t3 = await open(ctx, "test.form", `t-never-${uid()}`); await t3.page.getByTestId("reconnect").click(); await sleep(800);
  await expect(t3.page.getByTestId("msg-user")).toHaveCount(0); await expect(t3.page.getByTestId("error")).toHaveCount(0);
});

test("6 two surfaces stay isolated (session ids, tools)", async () => {
  const tidA = `t-iso-a-${uid()}`, tidB = `t-iso-b-${uid()}`;
  const a = await open(ctx, "test.form", tidA); const b = await open(ctx, "test.other", tidB);
  await say(a.page, "[[SC:fill]] fill it"); await say(b.page, "[[SC:other]] go");
  await expect(a.page.getByTestId("f-title")).toHaveValue("Dune", { timeout: 30000 });
  await expect(b.page.getByTestId("other-note")).toHaveText("hi", { timeout: 30000 });
  await expect(lastAssistant(b.page)).toContainText('"stored": "hi"', { timeout: 30000 });
  const sa = sidOf("test.form", tidA), sb = sidOf("test.other", tidB);
  const open_ = (await dbg()).filter((e) => e.ev === "open_session");
  expect(open_.find((e) => e.session_id === sa)!.tools.sort()).toEqual(["ask_choice", "set_fields"]);
  expect(open_.find((e) => e.session_id === sb)!.tools).toEqual(["other_tool"]);
  expect(sa).not.toBe(sb);
  const names = (r: Ev) => r.tools.map((x: Ev) => x.name).sort();
  expect(names(a.runs[0])).toEqual(["ask_choice", "set_fields"]); expect(names(b.runs[0])).toEqual(["other_tool"]);
  expect((await dbgFor(sb)).some((e) => e.ev === "register_call" && e.name === "set_fields")).toBe(false);
  // the other surface cannot call this surface's tool: the gateway tool gets a validation error and the model sees it
  const tidC = `t-iso-c-${uid()}`; const c = await open(ctx, "test.other", tidC); const tC = Date.now();
  await say(c.page, "[[SC:crosscall]] try");
  await expect(lastAssistant(c.page)).toContainText("unknown frontend tool", { timeout: 60000 });
  expect(Date.now() - tC).toBeLessThan(15000);
  expect(await c.page.getByTestId("other-note").textContent()).toBe("");
});

test("7 idle 40 s model silence sees heartbeats <= 15 s apart", async () => {
  test.setTimeout(120000);
  const stamps: number[] = []; const all: number[] = [];
  const tid = `t-hb-${uid()}`;
  const r = await rawRun("test.form", runBody(tid, [userMsg("[[SC:slow]] wait")]), (t, s) => { all.push(t); if (s.includes(": heartbeat")) stamps.push(t); });
  console.log(`heartbeat stamps(ms)=${JSON.stringify(stamps)} first_chunk=${all[0]}ms total=${r.ms}ms`);
  expect(r.status).toBe(200); expect(r.text).toContain("RUN_STARTED"); expect(r.text).toContain("RUN_FINISHED");
  expect(all[0]).toBeLessThan(3000);                               // headers + RUN_STARTED at once
  expect(r.ms).toBeGreaterThanOrEqual(38000);
  expect(stamps.length).toBeGreaterThanOrEqual(2);
  const marks = [0, ...all]; let maxGap = 0; for (let i = 1; i < marks.length; i++) maxGap = Math.max(maxGap, marks[i] - marks[i - 1]);
  console.log(`max gap between any two chunks: ${maxGap} ms`); expect(maxGap).toBeLessThanOrEqual(15000);
});

test("8 invalid frontend args: validation error reaches the model", async () => {
  test.setTimeout(120000);
  const tid = `t-bad-${uid()}`; const t = await open(ctx, "test.form", tid); const t0 = Date.now();
  await say(t.page, "[[SC:invalid]] break it");
  await expect(lastAssistant(t.page)).toContainText("invalid_arguments", { timeout: 90000 });
  const ms = Date.now() - t0; console.log(`invalid-args round trip: ${ms} ms`);
  await expect(lastAssistant(t.page)).toContainText("values");
  await expect(t.page.getByTestId("f-title")).toHaveValue("");
  const rc = (await dbgFor(sidOf("test.form", tid))).find((e) => e.ev === "register_call")!;
  expect(rc.errors.length).toBeGreaterThan(0);
  expect(t.runs.length).toBe(1);                                   // the browser was never asked to run the tool
  expect(ms).toBeLessThan(15000);
});

test("9 stop during a human wait does not cost another model call", async () => {
  const tid = `t-stop2-${uid()}`; const t = await open(ctx, "test.form", tid); const sid = sidOf("test.form", tid);
  await say(t.page, "[[SC:choice]] ask me");
  await expect(t.page.getByTestId("choice-card")).toBeVisible({ timeout: 30000 });
  await expect.poll(async () => (await dbgFor(sid)).filter((e) => e.ev === "poll").length, { timeout: 15000 }).toBeGreaterThan(0);
  const before = mockReqs().length;                                // model calls so far (the one that issued the tool call)
  const r = await fetch(`${BASE}/api/copilotkit/agent/test.form/stop/${tid}`, { method: "POST", headers: { ...HDR, "content-type": "application/json" }, body: JSON.stringify({ runId: t.runs[0].runId }) });
  expect(r.status).toBe(200); await sleep(4000);
  console.log(`model calls: before stop=${before} after stop+4s=${mockReqs().length}`);
  expect(mockReqs().length).toBe(before);                          // the cancelled tool result must not trigger another LLM turn
});

// Runs only on the multiplexed stack (run.sh phase 2: `stack.py start --multiplex`): one gateway process serves the
// default profile and `agui-test`; AGUI_BROKER_TOKEN is ONLY in agui-test's .env, never in the gateway's process env.
test("10 multiplexed gateway: frontend tool across two runs via /p/agui-test, token only in the profile .env", async () => {
  test.skip(!ST.multiplex, "needs the multiplexed stack (stack.py start --multiplex)");
  expect(ST.token_in_gateway_env).toBe(false);
  const tid = `t-mux-${uid()}`; const t = await open(ctx, "test.form", tid); const sid = sidOf("test.form", tid);
  await say(t.page, "[[SC:fill]] fill it");
  await expect(t.page.getByTestId("f-title")).toHaveValue("Dune", { timeout: 30000 });
  await expect(t.page.getByTestId("f-year")).toHaveValue("1965");
  await expect(lastAssistant(t.page)).toContainText('"bogus": "unknown_field"', { timeout: 30000 });
  expect(t.runs.length).toBe(2);
  expect(JSON.parse(t.runs[1].messages.at(-1).content)).toEqual({ title: "ok", year: "ok", bogus: "unknown_field" });
  const log = await dbgFor(sid);
  expect(log.find((e) => e.ev === "register_call")).toMatchObject({ name: "set_fields", errors: null });
  expect(log.find((e) => e.ev === "resolve")).toMatchObject({ accepted: true });
  // both AG-UI runs are served by ONE Hermes run (the 2nd delivers the tool result via the broker); every Hermes
  // request of this stack went through the multiplexed profile route
  const http = (await dbg()).filter((e) => e.ev === "hermes_http");
  expect(http.filter((e) => e.method === "POST" && e.path === "/p/agui-test/v1/runs").length).toBeGreaterThanOrEqual(1);
  for (const e of http) expect(e.path.startsWith("/p/agui-test/v1/runs")).toBe(true);
});
