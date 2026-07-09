import { Card, CardContent, Chip, Stack, Typography, Box } from '@mui/material';
import type { Room } from '../types';
import { fmtTemp, capitalize, isCooling } from '../format';

export function RoomCard({ room }: { room: Room }) {
  const cooling = isCooling(room.power_on, room.mode);

  return (
    <Card sx={{ height: '100%' }} elevation={3}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Typography variant="h6" noWrap>
            {room.display_name}
          </Typography>
          <Chip
            size="small"
            label={room.power_on ? 'On' : 'Off'}
            color={room.power_on ? 'success' : 'default'}
            variant={room.power_on ? 'filled' : 'outlined'}
          />
        </Stack>

        <Typography
          variant="h2"
          sx={{ my: 1, fontWeight: 600, color: cooling ? 'info.main' : 'text.primary' }}
        >
          {fmtTemp(room.indoor_c)}
        </Typography>

        <Stack direction="row" spacing={2} flexWrap="wrap">
          <Metric label="Mode" value={room.power_on ? capitalize(room.mode) : 'Off'} />
          <Metric label="Set" value={fmtTemp(room.target_c)} />
          <Metric label="Outdoor" value={fmtTemp(room.outdoor_c)} />
        </Stack>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}
