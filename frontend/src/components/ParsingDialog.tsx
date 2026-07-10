import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Typography,
  Chip,
  TextField,
  Box,
  Alert,
  Slider,
  FormControl,
  FormLabel,
  LinearProgress
} from '@mui/material';
import {
  Refresh,
  TrendingUp,
  Bolt,
  Inventory,
  ShoppingCart,
  NewReleases,
  CheckCircle
} from '@mui/icons-material';
import axios from 'axios';
import LoadingSpinner from './common/LoadingSpinner';

interface ParsingMode {
  id: string;
  name: string;
  description: string;
  icon: string;
  estimated_time: string;
  params?: {
    [key: string]: {
      type: string;
      default: any;
      min?: number;
      max?: number;
      description: string;
    };
  };
}

interface ParsingDialogProps {
  open: boolean;
  onClose: () => void;
  onStartParsing: (mode: string, params: any) => void;
  initialJobId?: number | null;
}

const iconMap: { [key: string]: React.ReactElement } = {
  '🔄': <Refresh color="primary" />,
  '📈': <TrendingUp color="success" />,
  '⚡': <Bolt color="warning" />,
  '📦': <Inventory color="info" />,
  '🛒': <ShoppingCart color="secondary" />,
  '🆕': <NewReleases color="error" />
};

export const ParsingDialog: React.FC<ParsingDialogProps> = ({ open, onClose, onStartParsing, initialJobId = null }) => {
  const [modes, setModes] = useState<ParsingMode[]>([]);
  const [selectedMode, setSelectedMode] = useState<ParsingMode | null>(null);
  const [params, setParams] = useState<{ [key: string]: any }>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Live job state
  const [jobId, setJobId] = useState<number | null>(null);
  const [job, setJob] = useState<any | null>(null);
  const [wsStatus, setWsStatus] = useState<'idle'|'open'|'error'|'closed'>('idle');
  const [lastPayloadTs, setLastPayloadTs] = useState<number | null>(null);

  useEffect(() => { if (!open) { resetState(); } }, [open]);

  useEffect(() => {
    if (open) { fetchParsingModes(); }
  }, [open]);

  // Adopt external jobId when provided
  useEffect(() => {
    if (open && initialJobId && !jobId) {
      console.log('[ParsingDialog] Adopting external jobId', initialJobId);
      setJobId(initialJobId);
    }
  }, [initialJobId, open]);

  const resetState = () => {
    setSelectedMode(null);
    setParams({});
    setError(null);
    setJobId(null);
    setJob(null);
    setWsStatus('idle');
    setLastPayloadTs(null);
  };

  const fetchParsingModes = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/parsing/modes');
      if (!response.ok) throw new Error('Failed to fetch parsing modes');
      const data = await response.json();
      setModes(data);
    } catch (err) {
      setError('Не вдалося завантажити режими парсингу');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleModeSelect = (mode: ParsingMode) => {
    setSelectedMode(mode);
    if (mode.params) {
      const defaultParams: { [key: string]: any } = {};
      Object.entries(mode.params).forEach(([key, param]) => { defaultParams[key] = param.default; });
      setParams(defaultParams);
    } else {
      setParams({});
    }
  };

  const handleParamChange = (key: string, value: any) => { setParams(prev => ({ ...prev, [key]: value })); };

  const wsUrlFor = (path: string) => {
    const base = axios.defaults.baseURL || window.location.origin;
    const u = new URL(base);
    const wsScheme = u.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsScheme}//${u.host}${path}`;
  };

  const startRun = async () => {
    if (!selectedMode) return;
    try {
      // Делегуємо запуск наверх і одразу закриваємо діалог.
      onStartParsing(selectedMode.id, { ...params });
      handleClose();
    } catch (e) {
      console.error('[Parsing] start error:', e);
      setError('Не вдалося запустити парсинг');
    }
  };

  const openStream = (jid: number) => {
    const ws = new WebSocket(wsUrlFor(`/api/parsing/jobs/${jid}/stream`));
    ws.onopen = () => { setWsStatus('open'); console.log('[WS] open'); };
    ws.onerror = (e) => { setWsStatus('error'); console.log('[WS] error', e); };
    ws.onclose = () => { setWsStatus(prev => prev === 'error' ? 'error' : 'closed'); console.log('[WS] close'); };
    ws.onmessage = (ev) => { try { const payload = JSON.parse(ev.data); setJob(payload); setLastPayloadTs(Date.now()); } catch {} };
    setTimeout(() => { if (wsStatus !== 'open') startPolling(jid); }, 1500);
  };

  const startPolling = (jid: number) => {
    console.log('[Polling] Fallback engaged');
    const iv = setInterval(async () => {
      try {
        const r = await axios.get(`/api/parsing/jobs/${jid}`);
        setJob(r.data);
        setLastPayloadTs(Date.now());
        if (r.data?.status && ['succeeded', 'failed', 'canceled'].includes(r.data.status)) {
          console.log('[Polling] stop on terminal state');
          clearInterval(iv);
        }
      } catch (e) {
        console.log('[Polling] error', e);
        clearInterval(iv);
      }
    }, 1000);
  };

  const handleClose = () => { resetState(); onClose(); };

  const renderParamInput = (key: string, param: any) => {
    if (param.type === 'number' && param.min !== undefined && param.max !== undefined) {
      return (
        <FormControl fullWidth margin="normal">
          <FormLabel>{param.description}</FormLabel>
          <Box sx={{ px: 2 }}>
            <Slider
              value={params[key] || param.default}
              onChange={(_, value) => handleParamChange(key, value)}
              min={param.min}
              max={param.max}
              marks
              valueLabelDisplay="auto"
            />
          </Box>
          <Typography variant="caption" color="text.secondary" align="center">
            {params[key] || param.default} днів
          </Typography>
        </FormControl>
      );
    }

    return (
      <TextField
        fullWidth
        margin="normal"
        label={param.description}
        type={param.type}
        value={params[key] || param.default}
        onChange={(e) => handleParamChange(key, e.target.value)}
      />
    );
  };

  const Heartbeat = () => {
    const fresh = lastPayloadTs ? (Date.now() - lastPayloadTs) < 2500 : false;
    return (
      <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
        <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: fresh ? 'green' : 'grey.500' }} />
        <Typography variant="caption" color="text.secondary">
          {lastPayloadTs ? `last ${Math.round((Date.now() - lastPayloadTs)/1000)}s` : 'waiting...'}
        </Typography>
      </Box>
    );
  };

  const ProgressHeader = () => {
    const percent = Number(job?.percent ?? (job?.total_items ? Math.round((job.processed_items / job.total_items) * 100) : 0));
    const terminal = job?.status && ['succeeded','failed','canceled','stalled'].includes(job.status);
    return (
      <Box sx={{ mb: 2 }}>
        <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
          <Typography variant="subtitle1">Статус: {job?.status || 'running'}</Typography>
          <Heartbeat />
        </Box>
        {/* Always visible progress bar */}
        <LinearProgress variant={Number.isFinite(percent) ? 'determinate' : 'indeterminate'} value={Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : undefined} sx={{ height: 8, borderRadius: 1 }} />
        <Box display="flex" alignItems="center" gap={2} mt={1}>
          <Typography variant="body2">{Number.isFinite(percent) ? `${percent}%` : '...'}</Typography>
          <Typography variant="caption">{job?.processed_items ?? 0} / {job?.total_items ?? '...'}</Typography>
          <Typography variant="caption">{(job?.items_per_sec ?? 0)} items/s</Typography>
          <Typography variant="caption">ETA: {job?.eta_seconds ?? '...'}s</Typography>
          <Typography variant="caption">Крок: {job?.current_step ?? '...'}</Typography>
        </Box>
        {job?.error_summary && (
          <Box mt={1}><Alert severity="error">{job.error_summary}</Alert></Box>
        )}
        <Box mt={1}>
          <Button size="small" variant="outlined" color="warning" onClick={() => jobId && axios.post(`/api/parsing/jobs/${jobId}/cancel`).then(()=>console.log('[Cancel] sent')).catch(err=>console.log('[Cancel] err', err))}>
            Зупинити
          </Button>
          {terminal && (
            <Button size="small" variant="contained" sx={{ ml: 1 }} onClick={() => setJob(null)}>Закрити підсумок</Button>
          )}
        </Box>
      </Box>
    );
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {jobId ? 'Прогрес парсингу' : (selectedMode ? selectedMode.name : 'Виберіть режим парсингу')}
      </DialogTitle>
      
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {/* show an indeterminate bar immediately while waiting first payload */}
        {false && jobId && !job && (
          <Box sx={{ mb: 2 }}>
            <LinearProgress sx={{ height: 8, borderRadius: 1 }} />
          </Box>
        )}

        {false && jobId && <ProgressHeader />}

        {loading ? (
          <LoadingSpinner variant="section" text="Завантаження режимів…" />
        ) : selectedMode && !jobId ? (
          <Box>
            <Box display="flex" alignItems="center" mb={2}>
              {iconMap[selectedMode.icon] || <Refresh />}
              <Box ml={2}>
                <Typography variant="h6">{selectedMode.name}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {selectedMode.description}
                </Typography>
                <Chip 
                  label={`Орієнтовний час: ${selectedMode.estimated_time}`}
                  size="small"
                  sx={{ mt: 1 }}
                />
              </Box>
            </Box>

            {selectedMode.params && Object.entries(selectedMode.params).map(([key, param]) => (
              <Box key={key}>
                {renderParamInput(key, param)}
              </Box>
            ))}
          </Box>
        ) : !jobId ? (
          <List>
            {modes.map((mode) => (
              <ListItem
                key={mode.id}
                onClick={() => handleModeSelect(mode)}
                sx={{
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  mb: 1,
                  cursor: 'pointer',
                  '&:hover': { backgroundColor: 'action.hover' }
                }}
              >
                <ListItemIcon>
                  {iconMap[mode.icon] || <Refresh />}
                </ListItemIcon>
                <ListItemText
                  primary={mode.name}
                  secondary={
                    <>
                      <Typography variant="body2" color="text.secondary">{mode.description}</Typography>
                      <Chip label={mode.estimated_time} size="small" sx={{ mt: 0.5 }} />
                    </>
                  }
                />
              </ListItem>
            ))}
          </List>
        ) : null}
      </DialogContent>

      <DialogActions>
        {!jobId ? (
          selectedMode ? (
            <>
              <Button onClick={() => setSelectedMode(null)}>Назад</Button>
              <Button onClick={handleClose}>Скасувати</Button>
              <Button onClick={startRun} variant="contained" startIcon={<CheckCircle />}>Почати парсинг</Button>
            </>
          ) : (
            <Button onClick={handleClose}>Закрити</Button>
          )
        ) : (
          <Button onClick={handleClose}>Закрити</Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
