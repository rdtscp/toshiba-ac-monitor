import type { Realtime, Reading } from './types';

// Configured at build/dev time via .env (see .env.example). Falls back to the
// Pi's mDNS hostname.
export const API_URL =
  import.meta.env.VITE_API_URL ?? 'http://rdtscp-pi.local:8787';
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? '';

function authHeaders(): Record<string, string> {
  return API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {};
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, { headers: authHeaders() });
  if (!resp.ok) {
    if (resp.status === 401) {
      throw new Error('Unauthorized — check VITE_API_TOKEN matches the Pi.');
    }
    throw new Error(`${path} failed: HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function fetchRealtime(): Promise<Realtime> {
  return getJson<Realtime>('/realtime');
}

export function fetchHistory(since: string, device?: string): Promise<Reading[]> {
  const q = new URLSearchParams({ since });
  if (device) q.set('device', device);
  return getJson<Reading[]>(`/history?${q.toString()}`);
}
