import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { LineChart } from '@mui/x-charts/LineChart';
import dayjs from 'dayjs';
import type { Reading } from '../types';
import { fetchHistory } from '../api';
import { ROOM_COLORS } from '../theme';

type Range = '30d' | '7d' | '1d' | '12h' | '6h' | '1h';
const RANGES: Range[] = ['30d', '7d', '1d', '12h', '6h', '1h'];
const RANGE_SECONDS: Record<Range, number> = {
  '30d': 30 * 24 * 3600,
  '7d': 7 * 24 * 3600,
  '1d': 24 * 3600,
  '12h': 12 * 3600,
  '6h': 6 * 3600,
  '1h': 3600,
};

const ON_COLOR = '#43a047'; // green: AC turned on
const OFF_COLOR = '#e53935'; // red: AC turned off

// (seriesId -> dataIndex -> transition kind). Read by the custom mark so it can
// fill green/red per point without extra series cluttering the tooltip/legend.
const TransitionsCtx = createContext<Map<string, Map<number, 'on' | 'off'>>>(new Map());

function TransitionMark(props: any) {
  const { id, dataIndex, x, y, color } = props;
  const transitions = useContext(TransitionsCtx);
  const kind = transitions.get(String(id))?.get(dataIndex);
  const fill = kind === 'on' ? ON_COLOR : kind === 'off' ? OFF_COLOR : color;
  return <circle cx={x} cy={y} r={5} fill={fill} stroke="#0f1419" strokeWidth={1.5} />;
}

// Bucket readings to the minute (so all rooms align), then build, per room:
//  - a base temperature line (marks only at on/off transitions, coloured by the
//    custom mark), and
//  - an "on overlay" line covering only the minutes the room ran, which we
//    thicken via CSS — so a running stretch looks like a fat segment.
function buildChart(readings: Reading[]) {
  const names = new Map<string, string>();
  const tempByTs = new Map<number, Map<string, number | null>>();
  const onByTs = new Map<number, Map<string, boolean>>();
  for (const r of readings) {
    names.set(r.device_id, r.display_name ?? r.device_id);
    const minute = Math.round(r.ts / 60) * 60;
    if (!tempByTs.has(minute)) {
      tempByTs.set(minute, new Map());
      onByTs.set(minute, new Map());
    }
    tempByTs.get(minute)!.set(r.device_id, r.indoor_c);
    onByTs.get(minute)!.set(r.device_id, r.power_on);
  }
  const times = [...tempByTs.keys()].sort((a, b) => a - b);
  const x = times.map((t) => new Date(t * 1000));

  const transitions = new Map<string, Map<number, 'on' | 'off'>>();
  const legend: { name: string; color: string }[] = [];
  const series: any[] = [];

  [...names.entries()].forEach(([devId, name], i) => {
    const color = ROOM_COLORS[i % ROOM_COLORS.length];
    const sid = `r${i}`;
    const temps = times.map((t) => tempByTs.get(t)?.get(devId) ?? null);
    const onFlags = times.map((t) => onByTs.get(t)?.get(devId) === true);

    const tmap = new Map<number, 'on' | 'off'>();
    for (let k = 1; k < times.length; k++) {
      if (onFlags[k] !== onFlags[k - 1]) tmap.set(k, onFlags[k] ? 'on' : 'off');
    }
    transitions.set(sid, tmap);

    series.push({
      id: sid,
      label: name,
      color,
      data: temps,
      connectNulls: true,
      showMark: (p: { index: number }) => tmap.has(p.index),
      valueFormatter: (v: number | null, ctx: { dataIndex: number }) =>
        v == null ? null : `${v}° · ${onFlags[ctx.dataIndex] ? 'on' : 'off'}`,
    });
    series.push({
      id: `${sid}on`,
      color,
      connectNulls: false,
      showMark: false,
      data: times.map((t, k) => (onFlags[k] ? (tempByTs.get(t)?.get(devId) ?? null) : null)),
      valueFormatter: () => null, // keep this helper line out of the tooltip
    });
    legend.push({ name, color });
  });

  return { x, series, transitions, legend };
}

function humanizeSpan(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}

export function HistoryChart() {
  const [range, setRange] = useState<Range>('6h');
  const [readings, setReadings] = useState<Reading[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchHistory(range)
        .then((rows) => {
          if (cancelled) return;
          setReadings(rows);
          setError(null);
        })
        .catch((e) => !cancelled && setError(String(e.message ?? e)))
        .finally(() => !cancelled && setLoading(false));
    };
    setLoading(true);
    load();
    const id = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [range]);

  const { x, series, transitions, legend } = useMemo(() => buildChart(readings), [readings]);
  const tickFormat = RANGE_SECONDS[range] <= 24 * 3600 ? 'HH:mm' : 'DD MMM';

  // Thicken each room's "on overlay" line.
  const lineSx = useMemo(() => {
    const sx: Record<string, object> = {};
    legend.forEach((_l, i) => {
      sx[`& .MuiLineElement-series-r${i}on`] = { strokeWidth: 4 };
    });
    return sx;
  }, [legend.length]);

  const nowMs = Date.now();
  const xMin = new Date(nowMs - RANGE_SECONDS[range] * 1000);
  const xMax = new Date(nowMs);

  const temps = readings.map((r) => r.indoor_c).filter((v): v is number => v != null);
  const yMin = temps.length ? Math.floor(Math.min(...temps)) - 2 : undefined;
  const yMax = temps.length ? Math.ceil(Math.max(...temps)) + 2 : undefined;

  const span = readings.length
    ? Math.max(...readings.map((r) => r.ts)) - Math.min(...readings.map((r) => r.ts))
    : 0;

  return (
    <Paper sx={{ p: 2 }} elevation={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={0.5}>
        <Typography variant="h6">Indoor temperature history</Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={range}
          onChange={(_e, v: Range | null) => v && setRange(v)}
        >
          {RANGES.map((r) => (
            <ToggleButton key={r} value={r}>
              {r.toUpperCase()}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      <Typography variant="caption" color="text.secondary" display="block" mb={1}>
        {readings.length > 0
          ? `${readings.length} samples · ${humanizeSpan(span)} of data`
          : 'collecting…'}
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {loading && readings.length === 0 ? (
        <Box display="flex" justifyContent="center" alignItems="center" height={360}>
          <CircularProgress />
        </Box>
      ) : x.length === 0 ? (
        <Box display="flex" justifyContent="center" alignItems="center" height={360}>
          <Typography color="text.secondary">No data in this range yet.</Typography>
        </Box>
      ) : (
        <>
          <TransitionsCtx.Provider value={transitions}>
            <LineChart
              height={360}
              xAxis={[
                {
                  data: x,
                  scaleType: 'time',
                  min: xMin,
                  max: xMax,
                  valueFormatter: (value: Date, ctx: { location: string }) =>
                    ctx.location === 'tooltip'
                      ? dayjs(value).format('DD MMM HH:mm')
                      : dayjs(value).format(tickFormat),
                },
              ]}
              yAxis={[{ min: yMin, max: yMax, valueFormatter: (v: number) => `${v}°` }]}
              series={series}
              slots={{ mark: TransitionMark as any }}
              slotProps={{ legend: { hidden: true } as any }}
              tooltip={{ trigger: 'axis' }}
              sx={lineSx}
              margin={{ left: 50, right: 20, top: 20, bottom: 40 }}
              grid={{ horizontal: true }}
            />
          </TransitionsCtx.Provider>

          {/* Custom legend (rooms + the on/off dot key). */}
          <Stack direction="row" spacing={2} flexWrap="wrap" alignItems="center" mt={1}>
            {legend.map((l) => (
              <Stack key={l.name} direction="row" spacing={0.75} alignItems="center">
                <Box sx={{ width: 16, height: 3, bgcolor: l.color, borderRadius: 1 }} />
                <Typography variant="caption">{l.name}</Typography>
              </Stack>
            ))}
            <Box flexGrow={1} />
            <LegendDot color={ON_COLOR} label="turned on" />
            <LegendDot color={OFF_COLOR} label="turned off" />
            <Typography variant="caption" color="text.secondary">
              thick = running
            </Typography>
          </Stack>
        </>
      )}
    </Paper>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <Stack direction="row" spacing={0.5} alignItems="center">
      <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: color }} />
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
    </Stack>
  );
}
