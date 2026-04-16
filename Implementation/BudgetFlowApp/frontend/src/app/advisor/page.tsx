"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import AppShell from "@/components/AppShell";
import Button from "@/components/ui/Button";
import Spinner from "@/components/ui/Spinner";
import Alert from "@/components/ui/Alert";
import { apiFetch, ApiError } from "@/lib/api";

interface Session { id: string; title: string | null; created_at: string; updated_at: string }
interface Message { id: string; role: string; content: string; tool_name?: string; created_at: string }

function parseAssistantContent(content: string): { main: string; insights: string[]; actions: string[] } {
  const lines = content.split("\n").map(l => l.trim()).filter(Boolean);
  let section: "main" | "insights" | "actions" = "main";
  const main: string[] = [];
  const insights: string[] = [];
  const actions: string[] = [];

  for (const line of lines) {
    const lowered = line.toLowerCase();
    if (lowered.startsWith("direct answer:") || lowered === "answer:" || lowered === "direct answer") {
      section = "main";
      const tail = line.split(":").slice(1).join(":").trim();
      if (tail) main.push(tail);
      continue;
    }
    if (lowered.startsWith("insights:") || lowered === "insights") {
      section = "insights";
      const tail = line.split(":").slice(1).join(":").trim();
      if (tail) insights.push(tail);
      continue;
    }
    if (lowered.startsWith("next actions:") || lowered === "next actions") {
      section = "actions";
      const tail = line.split(":").slice(1).join(":").trim();
      if (tail) actions.push(tail);
      continue;
    }

    const normalized = line.startsWith("- ") ? line.slice(2).trim() : line;
    if (!normalized) continue;

    if (section === "insights") insights.push(normalized);
    else if (section === "actions") actions.push(normalized);
    else main.push(normalized);
  }

  return { main: main.join(" "), insights, actions };
}

const SUGGESTIONS = [
  "How much did I spend last month?",
  "What are my top spending categories?",
  "Show me my budget status",
  "Do I have any unread alerts?",
  "List my recent transactions",
];

export default function AdvisorPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadSessions = useCallback(async () => {
    try {
      const data = await apiFetch<Session[]>("/api/v1/advisor/sessions");
      setSessions(data);
    } catch (e) { setError((e as ApiError).detail); }
    setLoading(false);
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  async function loadSession(id: string) {
    setActiveId(id);
    try {
      const data = await apiFetch<{ messages: Message[] }>(`/api/v1/advisor/sessions/${id}`);
      setMessages(data.messages.filter(m => m.role === "user" || (m.role === "assistant" && m.content)));
    } catch (e) { setError((e as ApiError).detail); }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend(text?: string) {
    const content = text || input.trim();
    if (!content) return;
    setInput("");
    setSending(true);
    setError("");

    const optimistic: Message = { id: "pending", role: "user", content, created_at: new Date().toISOString() };
    setMessages(prev => [...prev, optimistic]);

    try {
      const res = await apiFetch<{ session_id: string; message: Message }>("/api/v1/advisor/message", {
        method: "POST",
        body: { content, session_id: activeId || undefined },
      });

      if (!activeId) {
        setActiveId(res.session_id);
        loadSessions();
      }

      setMessages(prev => {
        const withoutPending = prev.filter(m => m.id !== "pending");
        return [...withoutPending, { ...optimistic, id: `user-${Date.now()}` }, res.message];
      });
    } catch (e) {
      setError((e as ApiError).detail);
      setMessages(prev => prev.filter(m => m.id !== "pending"));
    }
    setSending(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function startNew() {
    setActiveId(null);
    setMessages([]);
    setInput("");
  }

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-4rem)] -m-8">
        {/* Sidebar */}
        <div className="w-64 border-r border-neutral-200 bg-white flex flex-col">
          <div className="p-3 border-b border-neutral-200">
            <Button onClick={startNew} className="w-full text-xs" variant="secondary">New conversation</Button>
          </div>
          <div className="flex-1 overflow-auto p-2 space-y-1">
            {loading ? <Spinner /> : sessions.map(s => (
              <button
                key={s.id}
                onClick={() => loadSession(s.id)}
                className={`w-full text-left rounded-lg px-3 py-2 text-xs transition-colors truncate ${
                  s.id === activeId ? "bg-neutral-100 text-neutral-900 font-medium" : "text-neutral-600 hover:bg-neutral-50"
                }`}
              >
                {s.title || "Untitled"}
              </button>
            ))}
          </div>
        </div>

        {/* Chat area */}
        <div className="flex-1 flex flex-col bg-neutral-50">
          {error && <div className="px-6 pt-3"><Alert>{error}</Alert></div>}

          <div className="flex-1 overflow-auto px-6 py-4 space-y-4">
            {messages.length === 0 && !sending && (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <p className="text-lg font-medium text-neutral-700 mb-1">BudgetFlow Advisor</p>
                <p className="text-sm text-neutral-500 mb-6">Ask me anything about your finances</p>
                <div className="space-y-2 w-full max-w-md">
                  {SUGGESTIONS.map(q => (
                    <button
                      key={q}
                      onClick={() => handleSend(q)}
                      className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-left text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={m.id || i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
                  m.role === "user"
                    ? "bg-neutral-900 text-white"
                    : "bg-white border border-neutral-200 text-neutral-800"
                }`}>
                  {m.role === "assistant" ? (
                    (() => {
                      const parsed = parseAssistantContent(m.content || "");
                      return (
                        <div className="space-y-3">
                          <p>{parsed.main || m.content}</p>
                          {parsed.insights.length > 0 && (
                            <div>
                              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Insights</p>
                              <ul className="list-disc space-y-1 pl-4 text-sm text-neutral-700">
                                {parsed.insights.map((it, idx) => <li key={idx}>{it}</li>)}
                              </ul>
                            </div>
                          )}
                          {parsed.actions.length > 0 && (
                            <div>
                              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Next actions</p>
                              <ul className="list-disc space-y-1 pl-4 text-sm text-neutral-700">
                                {parsed.actions.map((it, idx) => <li key={idx}>{it}</li>)}
                              </ul>
                            </div>
                          )}
                        </div>
                      );
                    })()
                  ) : (
                    m.content
                  )}
                </div>
              </div>
            ))}

            {sending && (
              <div className="flex justify-start">
                <div className="bg-white border border-neutral-200 rounded-2xl px-4 py-3">
                  <div className="flex items-center gap-2 text-sm text-neutral-500">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-600" />
                    Thinking...
                  </div>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Compose */}
          <div className="border-t border-neutral-200 bg-white p-4">
            <div className="flex gap-2 max-w-3xl mx-auto">
              <textarea
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about your spending, budgets, or transactions..."
                rows={1}
                className="flex-1 resize-none rounded-xl border border-neutral-300 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400"
                disabled={sending}
              />
              <Button onClick={() => handleSend()} loading={sending} disabled={!input.trim()}>
                Send
              </Button>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
