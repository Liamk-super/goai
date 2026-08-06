export type SseEvent = { id: string; event: string; data: Record<string, unknown> };
export type SseHandlers = { onEvent(event: SseEvent): void; onSnapshot(snapshot: Record<string, unknown>): void; onError(error: Error): void };
export type SseFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class DurableRunStream {
  private cursor: string | undefined;
  private stopped = false;
  private readonly url: string;
  private readonly headers: HeadersInit;
  private readonly handlers: SseHandlers;
  private readonly fetcher: SseFetch;
  private readonly refetchSnapshot: (() => Promise<Record<string, unknown>>) | undefined;

  constructor(
    url: string,
    headers: HeadersInit,
    handlers: SseHandlers,
    fetcher: SseFetch = (input, init) => globalThis.fetch(input, init),
    refetchSnapshot?: () => Promise<Record<string, unknown>>,
  ) {
    this.url = url;
    this.headers = headers;
    this.handlers = handlers;
    this.fetcher = fetcher;
    this.refetchSnapshot = refetchSnapshot;
  }

  async connect(): Promise<void> {
    if (this.stopped) return;
    const headers = new Headers(this.headers);
    if (this.cursor) headers.set("Last-Event-ID", this.cursor);
    try {
      const response = await this.fetcher(this.url, { headers, credentials: "include" });
      if (response.status === 409) {
        if (!this.refetchSnapshot) throw new Error("SSE cursor is invalid and no durable snapshot fetcher is configured");
        const snapshot = await this.refetchSnapshot();
        this.cursor = typeof snapshot.current_cursor === "string" ? snapshot.current_cursor : undefined;
        this.handlers.onSnapshot(snapshot);
        if (!this.stopped) await this.connect();
        return;
      }
      if (!response.ok || !response.body) throw new Error(`SSE request failed with HTTP ${response.status}`);
      for await (const event of parseSse(response.body)) {
        if (event.id) this.cursor = event.id;
        if (event.event === "run.snapshot") this.handlers.onSnapshot(event.data);
        else this.handlers.onEvent(event);
      }
    } catch (error) {
      if (!this.stopped) this.handlers.onError(error instanceof Error ? error : new Error("SSE connection failed"));
    }
  }

  stop(): void { this.stopped = true; }
  lastCursor(): string | undefined { return this.cursor; }
}

export async function* parseSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = stream.getReader(); const decoder = new TextDecoder(); let buffer = "";
  let id = ""; let event = "message"; let data = "";
  const emit = (): SseEvent | undefined => {
    if (!data) return undefined;
    const raw = data.endsWith("\n") ? data.slice(0, -1) : data;
    const value = JSON.parse(raw) as Record<string, unknown>;
    const frame = { id, event, data: value }; id = ""; event = "message"; data = ""; return frame;
  };
  while (true) {
    const result = await reader.read(); buffer += decoder.decode(result.value ?? new Uint8Array(), { stream: !result.done });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).replace(/\r$/, ""); buffer = buffer.slice(newline + 1);
      if (!line) { const frame = emit(); if (frame) yield frame; continue; }
      const split = line.indexOf(":"); const field = split < 0 ? line : line.slice(0, split); const value = split < 0 ? "" : line.slice(split + 1).replace(/^ /, "");
      if (field === "id") id = value; else if (field === "event") event = value; else if (field === "data") data += `${value}\n`;
    }
    if (result.done) { const frame = emit(); if (frame) yield frame; return; }
  }
}
