import React, { useState, useEffect, useRef } from 'react';
import { Box, Paper, Typography, LinearProgress, IconButton, Collapse, Chip, Button, Alert } from '@mui/material';
import { ExpandLess, ExpandMore, Cancel } from '@mui/icons-material';
import axios from 'axios';

interface ParsingStatusProps { jobId?: number | null; }

interface LegacyStatus {
  is_running: boolean;
  task: string;
  current: number;
  total: number;
  elapsed_time: number;
  errors: string[];
}

export const ParsingStatus: React.FC<ParsingStatusProps> = ({ jobId = null }) => {
  const [expanded, setExpanded] = useState(Boolean(jobId));
  const [lastPayloadTs, setLastPayloadTs] = useState<number | null>(null);
  const [job, setJob] = useState<any | null>(null);
  const [legacy, setLegacy] = useState<LegacyStatus | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<any>(null);

  // Job-based live stream
  useEffect(() => {
    if (!jobId) { setJob(null); if (pollRef.current) { clearInterval(pollRef.current); pollRef.current=null; } return; }
    setLegacy(null);
    const base = axios.defaults.baseURL || window.location.origin;
    const u = new URL(base);
    const wsScheme = u.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${wsScheme}//${u.host}/api/parsing/jobs/${jobId}/stream`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (ev) => { try { const p = JSON.parse(ev.data); setJob(p); setLastPayloadTs(Date.now()); } catch {} };
    ws.onerror = () => { startJobPolling(jobId); };
    ws.onclose = () => { startJobPolling(jobId); };

    // Завжди паралельно підтягуємо стан з REST, навіть коли WS відкритий
    if (pollRef.current) { clearInterval(pollRef.current); }
    pollRef.current = setInterval(() => startJobPolling(jobId), 1000);

    return () => { try { ws.close(); } catch {} if (pollRef.current) { clearInterval(pollRef.current); pollRef.current=null; } };
  }, [jobId]);

  // Legacy global status as fallback when no jobId
  useEffect(() => {
    if (jobId) return; // job view active
    setJob(null);

    let reconnectTimer: any;
    const connectLegacyWS = () => {
      const base = axios.defaults.baseURL || window.location.origin;
      const u = new URL(base);
      const wsScheme = u.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${wsScheme}//${u.host}/api/parsing/ws`;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onmessage = (ev) => { try { const s = JSON.parse(ev.data); setLegacy(s); setLastPayloadTs(Date.now()); } catch {} };
      ws.onerror = () => { startLegacyPolling(); };
      ws.onclose = () => { startLegacyPolling(); reconnectTimer = setTimeout(connectLegacyWS, 5000); };
    };

    connectLegacyWS();
    return () => { clearTimeout(reconnectTimer); try { wsRef.current?.close(); } catch {} };
  }, [jobId]);

  const startJobPolling = (jid: number) => {
    const iv = setInterval(async () => {
      try {
        const r = await axios.get(`/api/parsing/jobs/${jid}`);
        setJob(r.data); setLastPayloadTs(Date.now());
        if (['succeeded','failed','canceled','stalled'].includes(String(r.data?.status || ''))) clearInterval(iv);
      } catch { clearInterval(iv); }
    }, 1000);
  };

  const startLegacyPolling = () => {
    const iv = setInterval(async () => {
      try {
        const r = await axios.get('/api/parsing/status');
        setLegacy(r.data); setLastPayloadTs(Date.now());
        if (!r.data?.is_running) clearInterval(iv);
      } catch { clearInterval(iv); }
    }, 1500);
  };

  const fresh = lastPayloadTs ? (Date.now() - lastPayloadTs) < 2500 : false;

  const showJob = Boolean(jobId && job);
  const showLegacy = Boolean(!jobId && legacy && (legacy.is_running || (legacy.total ?? 0) > 0));
  if (!showJob && !showLegacy) return null;

  const percent = showJob
    ? (Number(job.percent ?? (job.total_items ? Math.round((job.processed_items / job.total_items) * 100) : 0)))
    : (legacy && legacy.total > 0 ? Math.round((legacy.current / legacy.total) * 100) : 0);

  const title = showJob ? `Парсинг (${job.mode || '...'})` : 'Парсинг даних';
  const terminal = showJob && job?.status && ['succeeded','failed','canceled','stalled'].includes(job.status);
  const subtitle = showJob
    ? (terminal ? 'Парсинг завершено' : `Статус: ${job.status}`)
    : (legacy?.is_running ? 'Виконується...' : '');
  const statusLine = showJob ? `Статус: ${job.status}` : (legacy?.task || '');

  return (
    <Box sx={{ position: 'fixed', bottom: 16, right: 16, zIndex: 1300, minWidth: 300, maxWidth: 420 }}>
      <Paper elevation={6} sx={{ overflow: 'hidden' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 1, bgcolor: 'primary.main', color: 'primary.contrastText', cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
          <Typography variant="subtitle2" sx={{ fontWeight: 'bold' }}>{subtitle || title}</Typography>
          <IconButton size="small" sx={{ color: 'inherit' }}>{expanded ? <ExpandMore/> : <ExpandLess/>}</IconButton>
        </Box>
        <LinearProgress variant={Number.isFinite(percent) ? 'determinate' : 'indeterminate'} value={Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : undefined} sx={{ height: 6 }} />
        <Collapse in={expanded}>
          <Box sx={{ p: 2 }}>
            <Typography variant="body2" gutterBottom>{statusLine}</Typography>
            <Box display="flex" alignItems="center" gap={1} mb={1}>
              <Chip label={`${Number.isFinite(percent) ? percent : 0}%`} size="small" color="primary" />
              {showJob && (
                <>
                  <Chip label={`${job.processed_items ?? 0} / ${job.total_items ?? '...'}`} size="small" />
                  <Chip label={`Rate: ${job.items_per_sec ?? 0}/s`} size="small" />
                  <Chip label={`ETA: ${job.eta_seconds ?? '...'}s`} size="small" />
                  <Typography variant="caption" color="text.secondary">Крок: {job.current_step || '...'}</Typography>
                </>
              )}
              {!showJob && legacy && legacy.total > 0 && (
                <Typography variant="caption" color="text.secondary">{legacy.current} / {legacy.total}</Typography>
              )}
            </Box>
            {terminal && (
              <Box display="flex" alignItems="center" gap={1}>
                <Chip label={`Завершено`} size="small" color="success" />
                <Typography variant="caption" color="text.secondary">
                  {`Опрацьовано: ${job.processed_items ?? 0} за ${Math.max(0, Math.round(((job.ended_at ? new Date(job.ended_at).getTime() : Date.now()) - (job.started_at ? new Date(job.started_at).getTime() : Date.now()))/1000))}s`}
                </Typography>
              </Box>
            )}
            <Box display="flex" alignItems="center" gap={1}>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: fresh ? 'green' : 'grey.500' }} />
              <Typography variant="caption" color="text.secondary">{lastPayloadTs ? `last ${Math.round((Date.now() - lastPayloadTs)/1000)}s` : '...'}</Typography>
              <Box flexGrow={1} />
              {showJob ? (
                <Button size="small" variant="outlined" color="error" startIcon={<Cancel/>} onClick={() => axios.post(`/api/parsing/jobs/${jobId}/cancel`).catch(()=>{})}>Скасувати</Button>
              ) : (
                <Button size="small" variant="outlined" color="error" startIcon={<Cancel/>} onClick={() => axios.post('/api/parsing/cancel').catch(()=>{})}>Скасувати</Button>
              )}
            </Box>
          </Box>
        </Collapse>
      </Paper>
    </Box>
  );
}; 