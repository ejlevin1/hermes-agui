import { useCallback, useMemo, useState } from "react";
import { z } from "zod";
import { Alert, Box, Button, Paper, Stack, TextField, Typography } from "@mui/material";
import { CopilotKitProvider, useAgent, useAgentContext, useCopilotKit, useFrontendTool, useHumanInTheLoop, useRenderToolCall } from "@copilotkit/react-core/v2";
import { FIELDS, Form, type Values } from "./Form";

const RUNTIME_URL = "/api/copilotkit";
const q = new URLSearchParams(location.search);
const AGENT = q.get("agent") ?? "test.form";

/** Chat box shared by both surfaces. */
function Chat({ agentId, children }: { agentId: string; children?: React.ReactNode }) {
  const [draft, setDraft] = useState("");
  const [lastError, setLastError] = useState("");
  const { agent } = useAgent({ agentId });
  const tid = q.get("tid");
  if (tid && agent.threadId !== tid) agent.threadId = tid; // fixed thread id => reload/connect test
  const { copilotkit } = useCopilotKit();
  const renderToolCall = useRenderToolCall();
  const send = useCallback(async (text: string) => {
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content: text });
    try { await copilotkit.runAgent({ agent }); } catch (e) { setLastError(String(e)); }
  }, [agent, copilotkit]);
  const toolMsgs = useMemo(() => new Map(agent.messages.filter((m: any) => m.role === "tool").map((m: any) => [m.toolCallId, m])), [agent.messages]);
  return (
    <Paper sx={{ p: 2, flex: 1 }}>
      {children}
      <Stack spacing={1} data-testid="chat" data-agent={agentId}>
        {agent.messages.map((m: any) => (
          <Box key={m.id} data-role={m.role}>
            {m.role !== "tool" && typeof m.content === "string" && m.content && <Typography data-testid={`msg-${m.role}`}>{m.content}</Typography>}
            {m.role === "assistant" && m.toolCalls?.map((tc: any) => <Box key={tc.id} data-testid={`tool-${tc.function.name}`}>{renderToolCall({ toolCall: tc, toolMessage: toolMsgs.get(tc.id) })}</Box>)}
          </Box>
        ))}
        {agent.isRunning && <Typography data-testid="running">running…</Typography>}
        <Typography data-testid="thread" variant="caption">{agent.threadId}</Typography>
        {lastError && <Alert severity="error" data-testid="error">{lastError}</Alert>}
        <TextField value={draft} onChange={(e) => setDraft(e.target.value)} inputProps={{ "data-testid": "draft" }} />
        <Button data-testid="send" onClick={() => { const t = draft; setDraft(""); void send(t); }}>Send</Button>
        <Button data-testid="stop" onClick={() => copilotkit.stopAgent({ agent })}>Stop</Button>
        <Button data-testid="reconnect" onClick={() => void copilotkit.connectAgent({ agent })}>Connect</Button>
      </Stack>
    </Paper>
  );
}

function FormSurface() {
  const [values, setValues] = useState<Values>({ title: "", year: "", type: "" });
  useAgentContext({ description: "Current form values (user-visible intake form)", value: values as unknown as Record<string, string> });
  useFrontendTool({
    agentId: "test.form",
    name: "set_fields",
    description: "Write values into the form. Returns a per-field result.",
    parameters: z.object({ values: z.record(z.string(), z.union([z.string(), z.number()])), reason: z.string().optional() }),
    handler: async ({ values: v }: { values: Record<string, string | number> }) => {
      const result: Record<string, string> = {}; const next: Partial<Values> = {};
      for (const [k, val] of Object.entries(v)) {
        if ((FIELDS as readonly string[]).includes(k)) { (next as Record<string, string>)[k] = String(val); result[k] = "ok"; }
        else result[k] = "unknown_field";
      }
      setValues((cur) => ({ ...cur, ...next }));
      return result;
    },
  }, []);
  useHumanInTheLoop({
    agentId: "test.form",
    name: "ask_choice",
    description: "Ask the user to pick one option; waits.",
    parameters: z.object({ question: z.string(), options: z.array(z.string()) }),
    render: ({ args, status, respond }: any) => (
      <Paper variant="outlined" sx={{ p: 1 }} data-testid="choice-card" data-status={status}>
        <Typography>{args?.question}</Typography>
        {(args?.options ?? []).map((o: string) => (
          <Button key={o} disabled={!respond} onClick={() => respond?.({ choice: o })} data-testid={`choice-${o}`}>{o}</Button>
        ))}
      </Paper>
    ),
  }, []);
  return (
    <Box sx={{ display: "flex", gap: 2, p: 2 }}>
      <Paper sx={{ p: 2, flex: 1 }}><Form values={values} onChange={(k, v) => setValues((c) => ({ ...c, [k]: v }))} /></Paper>
      <Chat agentId="test.form" />
    </Box>
  );
}

function OtherSurface() {
  const [note, setNote] = useState("");
  useFrontendTool({
    agentId: "test.other",
    name: "other_tool",
    description: "Only exists on the other surface.",
    parameters: z.object({ note: z.string() }),
    handler: async ({ note: n }: { note: string }) => { setNote(n); return { stored: n }; },
  }, []);
  return <Box sx={{ p: 2 }}><Chat agentId="test.other"><Typography data-testid="other-note">{note}</Typography></Chat></Box>;
}

export default function App() {
  return (
    <CopilotKitProvider runtimeUrl={RUNTIME_URL}>
      {AGENT === "test.other" ? <OtherSurface /> : <FormSurface />}
    </CopilotKitProvider>
  );
}
