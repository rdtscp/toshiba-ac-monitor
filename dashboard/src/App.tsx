import { useEffect, useState } from 'react';
import {
  AppBar,
  Box,
  Chip,
  Container,
  Grid,
  Toolbar,
  Typography,
  Alert,
} from '@mui/material';
import dayjs from 'dayjs';
import type { Realtime } from './types';
import { fetchRealtime, API_URL } from './api';
import { RoomCard } from './components/RoomCard';
import { HistoryChart } from './components/HistoryChart';

const STALE_SECONDS = 300;
const POLL_MS = 5000;

function statusChip(rt: Realtime | null, fetchError: string | null) {
  if (fetchError) return <Chip size="small" color="error" label="Disconnected" />;
  if (!rt) return <Chip size="small" label="Connecting…" />;
  const ageStale =
    rt.last_update != null && Date.now() / 1000 - rt.last_update > STALE_SECONDS;
  if (rt.status !== 'connected')
    return <Chip size="small" color="warning" label={rt.status} />;
  if (ageStale) return <Chip size="small" color="warning" label="Stale" />;
  return <Chip size="small" color="success" label="Connected" />;
}

export default function App() {
  const [realtime, setRealtime] = useState<Realtime | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetchRealtime()
        .then((rt) => {
          if (cancelled) return;
          setRealtime(rt);
          setError(null);
        })
        .catch((e) => !cancelled && setError(String(e.message ?? e)));
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const lastUpdated =
    realtime?.last_update != null
      ? dayjs(realtime.last_update * 1000).format('HH:mm:ss')
      : '—';

  return (
    <Box sx={{ minHeight: '100vh', pb: 6 }}>
      <AppBar position="static" color="transparent" enableColorOnDark elevation={0}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            Toshiba AC
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mr: 2 }}>
            updated {lastUpdated}
          </Typography>
          {statusChip(realtime, error)}
        </Toolbar>
      </AppBar>

      <Container maxWidth="lg" sx={{ mt: 3 }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error} (API: {API_URL})
          </Alert>
        )}

        <Grid container spacing={2} sx={{ mb: 3 }}>
          {(realtime?.rooms ?? []).map((room) => (
            <Grid item xs={12} sm={6} md={4} key={room.device_id}>
              <RoomCard room={room} />
            </Grid>
          ))}
        </Grid>

        <HistoryChart />
      </Container>
    </Box>
  );
}
