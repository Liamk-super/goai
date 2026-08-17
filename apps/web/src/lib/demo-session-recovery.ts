import { apiBase, ApiError } from "./api-client.ts";
import {
  DEMO_SESSION_SCHEMA,
  clearDemoSession,
  loadDemoSession,
  saveDemoSession,
  type DemoSession,
} from "./demo-session.ts";

type SessionStorage = Pick<Storage, "getItem" | "removeItem" | "setItem">;

async function responseError(response: Response): Promise<ApiError> {
  return new ApiError(response.status, await response.json().catch(() => ({})));
}

function assertDemoSession(value: unknown): DemoSession {
  const session = value as Partial<DemoSession>;
  if (
    session.schemaVersion !== DEMO_SESSION_SCHEMA
    || !session.tenantId
    || !session.workspaceId
    || !session.actorId
    || !session.displayName
    || !session.createdAt
  ) {
    throw new Error("The fixed Demo workspace binding is invalid.");
  }
  return session as DemoSession;
}

async function validate(session: DemoSession, signal?: AbortSignal): Promise<boolean> {
  const response = await fetch(`${apiBase()}/api/v1/demo/session`, {
    credentials: "include",
    signal,
    headers: {
      "X-Tenant-Id": session.tenantId,
      "X-Actor-Id": session.actorId,
      "X-Workspace-Id": session.workspaceId,
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  if (response.ok) return true;
  if (response.status === 403 || response.status === 404) return false;
  throw await responseError(response);
}

export async function restoreDemoSession(storage: SessionStorage, signal?: AbortSignal): Promise<DemoSession> {
  const stored = loadDemoSession(storage);
  if (stored && await validate(stored, signal)) return stored;
  if (stored) clearDemoSession(storage);
  const response = await fetch(`${apiBase()}/api/v1/demo/default-session`, {
    credentials: "include",
    signal,
    headers: { "X-Correlation-Id": crypto.randomUUID() },
  });
  if (!response.ok) throw await responseError(response);
  const restored = assertDemoSession(await response.json());
  saveDemoSession(storage, restored);
  return restored;
}
