// Mirrors the microservice JSON (metric_collector/server.py).

export interface Room {
  device_id: string;
  display_name: string;
  indoor_c: number | null;
  outdoor_c: number | null;
  target_c: number | null;
  power_on: boolean;
  mode: string | null;
  updated_at: number; // epoch seconds
}

export interface Realtime {
  status: string; // connected | reconnecting | connecting | error | ...
  last_update: number | null; // epoch seconds
  rooms: Room[];
}

export interface Reading {
  ts: number; // epoch seconds
  device_id: string;
  display_name: string | null;
  indoor_c: number | null;
  outdoor_c: number | null;
  target_c: number | null;
  power_on: boolean;
  mode: string | null;
}
