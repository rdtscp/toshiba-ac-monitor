import { createTheme } from '@mui/material/styles';

export const theme = createTheme({
  palette: {
    mode: 'dark',
    background: { default: '#0f1419', paper: '#1a2230' },
    primary: { main: '#4e79a7' },
    info: { main: '#5aa9e6' }, // "cooling" blue, mirrors the menu bar
    success: { main: '#59a14f' },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  },
});

// Muted tableau-style palette, matching finance-dashboard's ASSET_PALETTE.
export const ROOM_COLORS = [
  '#4e79a7',
  '#59a14f',
  '#e15759',
  '#edc948',
  '#b07aa1',
  '#76b7b2',
  '#ff9da7',
  '#9c755f',
];
