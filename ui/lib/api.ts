import type {
  DocResponse,
  JourneyMap,
  Persona,
  RunCreated,
  RunDetail,
  RunRequest,
  RunSummary,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

async function asJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = "";
    try {
      const j = (await resp.json()) as { detail?: string };
      detail = j?.detail ?? "";
    } catch {
      detail = await resp.text();
    }
    throw new Error(`${resp.status} ${resp.statusText}: ${detail}`);
  }
  return (await resp.json()) as T;
}

export async function createRun(req: RunRequest): Promise<RunCreated> {
  const resp = await fetch(`${API_BASE}/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
  return asJson<RunCreated>(resp);
}

export async function listRuns(): Promise<RunSummary[]> {
  const resp = await fetch(`${API_BASE}/runs`, { cache: "no-store" });
  return asJson<RunSummary[]>(resp);
}

export async function getRun(runId: string): Promise<RunDetail> {
  const resp = await fetch(`${API_BASE}/runs/${runId}`, { cache: "no-store" });
  return asJson<RunDetail>(resp);
}

export async function getPersonas(runId: string): Promise<Persona[]> {
  const resp = await fetch(`${API_BASE}/runs/${runId}/personas`, {
    cache: "no-store",
  });
  return asJson<Persona[]>(resp);
}

export async function getJourney(
  runId: string,
  personaId: string,
): Promise<JourneyMap> {
  const resp = await fetch(
    `${API_BASE}/runs/${runId}/journeys/${personaId}`,
    { cache: "no-store" },
  );
  return asJson<JourneyMap>(resp);
}

export async function getDoc(
  runId: string,
  docId: string,
): Promise<DocResponse> {
  const resp = await fetch(`${API_BASE}/runs/${runId}/doc/${docId}`, {
    cache: "no-store",
  });
  return asJson<DocResponse>(resp);
}

export function streamUrl(runId: string): string {
  return `${API_BASE}/runs/${runId}/stream`;
}
