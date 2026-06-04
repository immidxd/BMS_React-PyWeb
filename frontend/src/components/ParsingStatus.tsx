import React, { useState, useEffect, useRef } from 'react';
import { Box, Paper, Typography, LinearProgress, IconButton, Collapse, Chip, Button, Alert, Fade } from '@mui/material';
import { ExpandLess, ExpandMore, Cancel } from '@mui/icons-material';
import axios from 'axios';

interface ParsingStatusProps { jobId?: number | null; onComplete?: () => void; }

interface LegacyStatus {
  is_running: boolean;
  task: string;
  current: number;
  total: number;
  elapsed_time: number;
  errors: string[];
}

export const ParsingStatus: React.FC<ParsingStatusProps> = ({ jobId = null, onComplete }) => {
  console.log('[ParsingStatus] Received jobId:', jobId);
  const [expanded, setExpanded] = useState(Boolean(jobId));
  const [lastPayloadTs, setLastPayloadTs] = useState<number | null>(null);
  const [job, setJob] = useState<any | null>(null);
  const [legacy, setLegacy] = useState<LegacyStatus | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<any>(null);
  const autoHideRef = useRef<any>(null);
  // Monotonic floor for the displayed percent — guards the bar from flickering
  // backwards when WS / poll payloads arrive out of order during a run.
  const percentRef = useRef(0);

  // Auto-hide widget 5 seconds after parsing completes
  const terminal = job?.status && ['succeeded','failed','canceled','stalled'].includes(job.status);
  useEffect(() => {
    if (terminal && onComplete) {
      autoHideRef.current = setTimeout(() => {
        onComplete();
        window.dispatchEvent(new Event('parsing-complete'));
      }, 5000);
    }
    return () => { if (autoHideRef.current) clearTimeout(autoHideRef.current); };
  }, [terminal, onComplete]);

  // Job-based live stream
  useEffect(() => {
    if (!jobId) { setJob(null); if (pollRef.current) { clearInterval(pollRef.current); pollRef.current=null; } return; }
    // jobId === -1 is the transient "pending" placeholder set the instant the
    // user starts a run, before POST /run returns the real id. Don't open a
    // doomed stream/poll for it (jobs/-1 → 404) — just show "З'єднання...".
    if (jobId <= 0) { setJob(null); if (pollRef.current) { clearInterval(pollRef.current); pollRef.current=null; } return; }
    setLegacy(null);
    percentRef.current = 0; // reset monotonic floor for the new job
    const base = axios.defaults.baseURL || window.location.origin;
    const u = new URL(base);
    const wsScheme = u.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${wsScheme}//${u.host}/api/parsing/jobs/${jobId}/stream`;

    // Poll is a TRUE fallback — it runs only while the WS is down. Running both
    // at once made the bar flicker between the WS value and a laggier poll value.
    const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
    const startPoll = () => {
      if (pollRef.current) return; // already polling — never stack intervals
      pollRef.current = setInterval(async () => {
        try {
          const r = await axios.get(`/api/parsing/jobs/${jobId}`);
          setJob(r.data); setLastPayloadTs(Date.now());
          if (['succeeded','failed','canceled','stalled'].includes(String(r.data?.status || ''))) stopPoll();
        } catch { stopPoll(); }
      }, 1000);
    };

    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => { stopPoll(); };            // WS is the single live source
    ws.onmessage = (ev) => { try { const p = JSON.parse(ev.data); setJob(p); setLastPayloadTs(Date.now()); } catch {} };
    ws.onerror = () => { startPoll(); };
    ws.onclose = () => { startPoll(); };

    // Failsafe: if the socket never opens, fall back to polling.
    const failsafe = setTimeout(() => { if (ws.readyState !== WebSocket.OPEN) startPoll(); }, 2000);

    return () => { clearTimeout(failsafe); try { ws.close(); } catch {} stopPoll(); };
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
  const showPendingJob = Boolean(jobId && !job); // показувати скелет поки не прийшов перший пакет
  
  const visible = showJob || showLegacy || showPendingJob;

  // Bar binds ONLY to the backend's single overall percent (job.percent), which
  // is monotonic across phases. processed_items/total_items are PER-SHEET — using
  // them as a fallback made the bar jump to the current sheet's ratio. When a
  // payload lacks percent we hold the last value instead of recomputing.
  let percent: number;
  if (showJob) {
    const p = Number(job?.percent);
    percent = terminal ? 100 : Math.max(percentRef.current, Number.isFinite(p) ? p : percentRef.current);
    percentRef.current = percent;
  } else {
    percent = (legacy && legacy.total > 0) ? Math.round((legacy.current / legacy.total) * 100) : 0;
  }

  const title = showJob ? `Парсинг (${job.mode || '...'})` : 'Парсинг даних';
  const terminalVisible = showJob && terminal;
  const subtitle = showJob
    ? (terminalVisible ? 'Парсинг завершено' : `Статус: ${job.status}`)
    : (legacy?.is_running ? 'Виконується...' : '');
  const statusLine = showJob ? `Статус: ${job.status}` : (showPendingJob ? 'З’єднання...' : (legacy?.task || ''));

  return (
    <Fade in={visible} unmountOnExit timeout={250}>
    <Box sx={{ position: 'fixed', bottom: 16, right: 16, zIndex: 2000, width: 380 }}>
      <Paper elevation={6} sx={{ overflow: 'hidden' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 1, bgcolor: '#111', color: '#fff', cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
          <Typography variant="subtitle2" sx={{ fontWeight: 'bold' }}>{subtitle || title}</Typography>
          <IconButton size="small" sx={{ color: 'inherit' }}>{expanded ? <ExpandMore/> : <ExpandLess/>}</IconButton>
        </Box>
        <LinearProgress variant={Number.isFinite(percent) ? 'determinate' : 'indeterminate'} value={Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : undefined} sx={{ height: 6, bgcolor: '#e5e5e5', '& .MuiLinearProgress-bar': { bgcolor: '#111' } }} />
        <Collapse in={expanded}>
          <Box sx={{ p: 2 }}>
            <Typography variant="body2" gutterBottom>{statusLine}</Typography>
            <Box display="flex" alignItems="center" gap={1} mb={1} sx={{ flexWrap: 'nowrap', minWidth: 0, '& .MuiChip-root': { flexShrink: 0 } }}>
              <Chip label={`${Number.isFinite(percent) ? percent : 0}%`} size="small" sx={{ bgcolor: '#111', color: '#fff' }} />
              {(showJob || showPendingJob) && (
                <>
                  <Chip label={`${job?.processed_items ?? 0} / ${job?.total_items ?? '...'}`} size="small" />
                  <Chip label={`Rate: ${job?.items_per_sec ?? 0}/s`} size="small" />
                  <Chip label={`ETA: ${job?.eta_seconds ?? '...'}s`} size="small" />
                  <Typography variant="caption" color="text.secondary" noWrap sx={{ minWidth: 0, flexShrink: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>Крок: {job?.current_step || '...'}</Typography>
                </>
              )}
              {!showJob && legacy && legacy.total > 0 && (
                <Typography variant="caption" color="text.secondary">{legacy.current} / {legacy.total}</Typography>
              )}
            </Box>
            {terminalVisible && (
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
              {(showJob || showPendingJob) ? (
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  startIcon={<Cancel/>}
                  onClick={async () => {
                    const id = Number(jobId);
                    // Оптимістичне оновлення UI: якщо job ще немає – створюємо мінімальний знімок
                    setJob((prev: any) => {
                      const snapshot = {
                        status: 'canceled',
                        processed_items: prev?.processed_items ?? 0,
                        total_items: prev?.total_items ?? 0,
                        percent: prev?.percent ?? 0,
                        items_per_sec: 0,
                        eta_seconds: 0,
                        current_step: 'canceled',
                        ended_at: new Date().toISOString(),
                        started_at: prev?.started_at ?? new Date().toISOString(),
                        ...(prev || {})
                      };
                      return snapshot as any;
                    });
                    setLegacy((prev: LegacyStatus | null) => prev ? { ...prev, is_running: false } as LegacyStatus : prev);
                    setLastPayloadTs(Date.now());
                    try {
                      const calls: Promise<any>[] = [axios.post('/api/parsing/cancel').catch(()=>{})];
                      if (Number.isFinite(id) && id > 0) {
                        calls.push(axios.post(`/api/parsing/jobs/${id}/cancel`).catch(()=>{}));
                      }
                      await Promise.allSettled(calls);
                    } catch {}
                  }}
                >
                  Скасувати
                </Button>
              ) : (
                <Button size="small" variant="outlined" color="error" startIcon={<Cancel/>} onClick={() => axios.post('/api/parsing/cancel').catch(()=>{})}>Скасувати</Button>
              )}
            </Box>
          </Box>
        </Collapse>
      </Paper>
    </Box>
    </Fade>
  );
}; 