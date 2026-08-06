export const DEMO_SESSION_KEY = "launchscope.demo.session.v1";
export const DEMO_SESSION_SCHEMA = "launchscope.demo.session.v1";

export type DemoSession = {
  schemaVersion: typeof DEMO_SESSION_SCHEMA;
  tenantId: string;
  workspaceId: string;
  actorId: string;
  displayName: string;
  createdAt: string;
};

type StorageReader = Pick<Storage, "getItem" | "removeItem" | "setItem">;

export function parseDemoSession(value: string | null): DemoSession | null {
  if (!value) return null;
  try {
    const candidate = JSON.parse(value) as Partial<DemoSession>;
    if (
      candidate.schemaVersion !== DEMO_SESSION_SCHEMA ||
      !candidate.tenantId || !candidate.workspaceId || !candidate.actorId ||
      !candidate.displayName || !candidate.createdAt
    ) return null;
    return candidate as DemoSession;
  } catch {
    return null;
  }
}

export function loadDemoSession(storage: StorageReader): DemoSession | null {
  const raw = storage.getItem(DEMO_SESSION_KEY);
  const session = parseDemoSession(raw);
  if (raw && !session) storage.removeItem(DEMO_SESSION_KEY);
  return session;
}

export function saveDemoSession(storage: StorageReader, session: DemoSession): void {
  if (session.schemaVersion !== DEMO_SESSION_SCHEMA) throw new Error("Unsupported Demo session schema");
  storage.setItem(DEMO_SESSION_KEY, JSON.stringify(session));
}

export function clearDemoSession(storage: StorageReader): void {
  storage.removeItem(DEMO_SESSION_KEY);
}
