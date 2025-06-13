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
  ListItemSecondaryAction,
  Typography,
  Chip,
  TextField,
  Box,
  CircularProgress,
  Alert,
  Slider,
  FormControl,
  FormLabel
} from '@mui/material';
import {
  Refresh,
  TrendingUp,
  Bolt,
  Inventory,
  ShoppingCart,
  NewReleases,
  CheckCircle,
  Cancel
} from '@mui/icons-material';

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
}

const iconMap: { [key: string]: React.ReactElement } = {
  '🔄': <Refresh color="primary" />,
  '📈': <TrendingUp color="success" />,
  '⚡': <Bolt color="warning" />,
  '📦': <Inventory color="info" />,
  '🛒': <ShoppingCart color="secondary" />,
  '🆕': <NewReleases color="error" />
};

export const ParsingDialog: React.FC<ParsingDialogProps> = ({ open, onClose, onStartParsing }) => {
  const [modes, setModes] = useState<ParsingMode[]>([]);
  const [selectedMode, setSelectedMode] = useState<ParsingMode | null>(null);
  const [params, setParams] = useState<{ [key: string]: any }>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      fetchParsingModes();
    }
  }, [open]);

  const fetchParsingModes = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/parsing/parsing/modes');
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
    // Ініціалізуємо параметри значеннями за замовчуванням
    if (mode.params) {
      const defaultParams: { [key: string]: any } = {};
      Object.entries(mode.params).forEach(([key, param]) => {
        defaultParams[key] = param.default;
      });
      setParams(defaultParams);
    } else {
      setParams({});
    }
  };

  const handleParamChange = (key: string, value: any) => {
    setParams(prev => ({ ...prev, [key]: value }));
  };

  const handleStart = () => {
    if (selectedMode) {
      onStartParsing(selectedMode.id, params);
      handleClose();
    }
  };

  const handleClose = () => {
    setSelectedMode(null);
    setParams({});
    setError(null);
    onClose();
  };

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

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {selectedMode ? selectedMode.name : 'Виберіть режим парсингу'}
      </DialogTitle>
      
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {loading ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : selectedMode ? (
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
        ) : (
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
                  '&:hover': {
                    backgroundColor: 'action.hover',
                  }
                }}
              >
                <ListItemIcon>
                  {iconMap[mode.icon] || <Refresh />}
                </ListItemIcon>
                <ListItemText
                  primary={mode.name}
                  secondary={
                    <>
                      <Typography variant="body2" color="text.secondary">
                        {mode.description}
                      </Typography>
                      <Chip 
                        label={mode.estimated_time}
                        size="small"
                        sx={{ mt: 0.5 }}
                      />
                    </>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>

      <DialogActions>
        {selectedMode ? (
          <>
            <Button onClick={() => setSelectedMode(null)}>
              Назад
            </Button>
            <Button onClick={handleClose}>
              Скасувати
            </Button>
            <Button 
              onClick={handleStart} 
              variant="contained" 
              startIcon={<CheckCircle />}
            >
              Почати парсинг
            </Button>
          </>
        ) : (
          <Button onClick={handleClose}>
            Закрити
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}; 