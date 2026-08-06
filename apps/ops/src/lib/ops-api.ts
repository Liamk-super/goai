export type OpsRun = { run_id: string; tenant_id: string; project_id: string; status: string; current_stage?: string | null; standard_version: string; attention_reason?: string | null; updated_at: string };
export type OpsEvent = { event_id: string; tenant_id: string; run_id: string; event_type: string; status: string; occurred_at: string };

const base = () => process.env.NEXT_PUBLIC_LAUNCHSCOPE_API_BASE ?? "";

function identity(): string {
  const actor = document.querySelector('meta[name="launchscope-ops-actor-id"]')?.getAttribute("content");
  if (!actor) throw new Error("No separately authenticated Ops session is available.");
  return actor;
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${base()}/api/v1${path}`, { headers: { "X-Ops-Actor-Id": identity() }, credentials: "include" });
  if (!response.ok) throw new Error(`Ops request failed with HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export const opsApi = { getRun: (runId: string) => request<OpsRun>(`/ops/audit/runs/${runId}`), listEvents: () => request<{ items: OpsEvent[] }>("/ops/audit/events") };
