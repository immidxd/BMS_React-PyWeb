import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  LinearProgress,
  IconButton,
  Collapse,
  Chip,
  Button,
  Alert
} from '@mui/material';
import {
  ExpandLess,
  ExpandMore,
  Close,
  Cancel,
  CheckCircle,
  Error
} from '@mui/icons-material';

interface ParsingStatusData {
  is_running: boolean;
  task: string;
  current: number;
  total: number;
  elapsed_time: number;
  errors: string[];
}

export const ParsingStatus: React.FC = () => {
  const [status, setStatus] = useState<ParsingStatusData | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [ws, setWs] = useState<WebSocket | null>(null);

  useEffect(() => {
    // Підключаємося до WebSocket для отримання оновлень в реальному часі
    connectWebSocket();

    return () => {
      if (ws) {
        ws.close();
      }
    };
  }, []);

  const connectWebSocket = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const websocket = new WebSocket(`${protocol}//${window.location.host}/api/parsing/parsing/ws`);
    
    websocket.onopen = () => {
      console.log('WebSocket connected');
    };

    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setStatus(data);
      
      // Автоматично розгортаємо при початку парсингу
      if (data.is_running && !status?.is_running) {
        setExpanded(true);
      }
    };

    websocket.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    websocket.onclose = () => {
      console.log('WebSocket disconnected');
      // Спробуємо перепідключитись через 5 секунд
      setTimeout(connectWebSocket, 5000);
    };

    setWs(websocket);
  };

  const handleCancel = async () => {
    try {
      const response = await fetch('/api/parsing/parsing/cancel', { method: 'POST' });
      if (!response.ok) {
        console.error('Failed to cancel parsing:', response.status);
        return;
      }
    } catch (error) {
      console.error('Error cancelling parsing:', error);
    }
  };

  const formatTime = (seconds: number): string => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
      return `${hours}г ${minutes}хв ${secs}с`;
    } else if (minutes > 0) {
      return `${minutes}хв ${secs}с`;
    } else {
      return `${secs}с`;
    }
  };

  const getProgress = (): number => {
    if (!status || status.total === 0) return 0;
    return (status.current / status.total) * 100;
  };

  if (!status || !status.is_running) {
    return null;
  }

  return (
    <Box
      sx={{
        position: 'fixed',
        bottom: 16,
        right: 16,
        zIndex: 1300,
        minWidth: 300,
        maxWidth: 400
      }}
    >
      <Paper elevation={6} sx={{ overflow: 'hidden' }}>
        {/* Заголовок */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            p: 1.5,
            bgcolor: 'primary.main',
            color: 'primary.contrastText'
          }}
        >
          <Typography variant="subtitle2" sx={{ fontWeight: 'bold' }}>
            Парсинг даних
          </Typography>
          <Box>
            <IconButton
              size="small"
              onClick={() => setExpanded(!expanded)}
              sx={{ color: 'inherit' }}
            >
              {expanded ? <ExpandMore /> : <ExpandLess />}
            </IconButton>
          </Box>
        </Box>

        {/* Прогрес бар */}
        <LinearProgress 
          variant={status.total > 0 ? "determinate" : "indeterminate"} 
          value={getProgress()} 
          sx={{ height: 4 }}
        />

        {/* Деталі */}
        <Collapse in={expanded}>
          <Box sx={{ p: 2 }}>
            {/* Поточна задача */}
            <Typography variant="body2" gutterBottom>
              {status.task}
            </Typography>

            {/* Прогрес */}
            {status.total > 0 && (
              <Box display="flex" alignItems="center" gap={1} mb={1}>
                <Typography variant="caption" color="text.secondary">
                  {status.current} / {status.total}
                </Typography>
                <Chip 
                  label={`${Math.round(getProgress())}%`}
                  size="small"
                  color="primary"
                />
              </Box>
            )}

            {/* Час виконання */}
            {status.elapsed_time > 0 && (
              <Typography variant="caption" color="text.secondary" display="block" mb={1}>
                Час виконання: {formatTime(status.elapsed_time)}
              </Typography>
            )}

            {/* Помилки */}
            {status.errors.length > 0 && (
              <Alert severity="error" sx={{ mt: 1, py: 0.5 }}>
                {status.errors[status.errors.length - 1]}
              </Alert>
            )}

            {/* Кнопка скасування */}
            <Button
              fullWidth
              size="small"
              variant="outlined"
              color="error"
              startIcon={<Cancel />}
              onClick={handleCancel}
              sx={{ mt: 1.5 }}
            >
              Скасувати парсинг
            </Button>
          </Box>
        </Collapse>
      </Paper>
    </Box>
  );
}; 