export function fmtTemp(celsius: number | null | undefined): string {
  return celsius == null ? '--' : `${Math.round(celsius)}°C`;
}

export function capitalize(s: string | null | undefined): string {
  if (!s) return '—';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function isCooling(power_on: boolean, mode: string | null): boolean {
  return power_on && (mode ?? '').toLowerCase() === 'cooling';
}
