import React, { useState, useEffect } from 'react';
import styled from 'styled-components';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faTimes, faPlay, faStop, faFileImport, faTable } from '@fortawesome/free-solid-svg-icons';
import { useQuery, useMutation, UseMutationOptions } from '@tanstack/react-query';
import { toast } from 'react-toastify';

import { 
  fetchParsingSources, 
  fetchParsingStyles, 
  startParsing, 
  startOrdersParsing, 
  startGoogleSheetsParsing 
} from '../../services/parsingService';
import { useTheme } from '../../contexts/ThemeContext';

interface ParsingDialogProps {
  open: boolean;
  onClose: () => void;
}

interface ParsingParams {
  source_id: number;
  style_id: number;
  request_interval: number;
  max_items?: number | null;
}

// Styled components for the dialog
const Overlay = styled.div`
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
`;

const DialogContainer = styled.div<{ isDarkTheme: boolean }>`
  background-color: ${props => props.isDarkTheme ? 'var(--dark-bg-color)' : 'white'};
  color: ${props => props.isDarkTheme ? 'var(--dark-text-color)' : 'var(--light-text-color)'};
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  width: 90%;
  max-width: 700px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
`;

const DialogHeader = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  border-bottom: 1px solid var(--light-border-color);
`;

const DialogTitle = styled.h2`
  margin: 0;
  font-size: 1.5rem;
`;

const CloseButtonStyled = styled.button`
  background: none;
  border: none;
  font-size: 1.25rem;
  cursor: pointer;
  color: inherit;
  display: flex;
  align-items: center;
  justify-content: center;
`;

const DialogContent = styled.div`
  padding: 1rem;
  overflow-y: auto;
  flex-grow: 1;
`;

const DialogFooter = styled.div`
  padding: 1rem;
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  border-top: 1px solid var(--light-border-color);
`;

const Form = styled.form`
  display: flex;
  flex-direction: column;
  gap: 1rem;
`;

const FormGroup = styled.div`
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
`;

const Label = styled.label`
  font-weight: 500;
`;

const SelectStyled = styled.select<{ isDarkTheme: boolean }>`
  padding: 0.5rem;
  border-radius: 4px;
  border: 1px solid var(--light-border-color);
  background-color: ${props => props.isDarkTheme ? '#333' : 'white'};
  color: ${props => props.isDarkTheme ? 'var(--dark-text-color)' : 'var(--light-text-color)'};
`;

const InputStyled = styled.input<{ isDarkTheme: boolean }>`
  padding: 0.5rem;
  border-radius: 4px;
  border: 1px solid var(--light-border-color);
  background-color: ${props => props.isDarkTheme ? '#333' : 'white'};
  color: ${props => props.isDarkTheme ? 'var(--dark-text-color)' : 'var(--light-text-color)'};
`;

const ButtonStyled = styled.button<{ variant?: 'primary' | 'secondary' | 'danger' }>`
  padding: 0.5rem 1rem;
  border-radius: 4px;
  border: none;
  cursor: pointer;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background-color: ${props => {
    if (props.variant === 'danger') return '#d32f2f';
    if (props.variant === 'secondary') return '#757575';
    return 'var(--light-accent-color)';
  }};
  color: white;
  transition: background-color 0.2s;

  &:hover {
    background-color: ${props => {
      if (props.variant === 'danger') return '#b71c1c';
      if (props.variant === 'secondary') return '#616161';
      return 'var(--dark-accent-color)';
    }};
  }

  &:disabled {
    background-color: #cccccc;
    cursor: not-allowed;
  }
`;

const ProgressContainer = styled.div`
  margin-top: 1rem;
`;

const ProgressBar = styled.div`
  width: 100%;
  height: 10px;
  background-color: #e0e0e0;
  border-radius: 5px;
  overflow: hidden;
  margin-bottom: 0.5rem;
`;

const ProgressFill = styled.div<{ progress: number }>`
  height: 100%;
  background-color: var(--light-accent-color);
  width: ${props => `${props.progress}%`};
  transition: width 0.3s ease;
`;

const ProgressStats = styled.div`
  display: flex;
  justify-content: space-between;
  font-size: 0.9rem;
  margin-top: 0.5rem;
`;

const StatusText = styled.div<{ status: string }>`
  font-weight: 500;
  color: ${props => {
    switch (props.status) {
      case 'succeeded': return 'green';
      case 'failed': return 'red';
      case 'canceled': return 'orange';
      case 'stalled': return 'orangered';
      default: return 'inherit';
    }
  }};
`;

const TabContainer = styled.div`
  margin-bottom: 1.5rem;
`;

const TabButtons = styled.div`
  display: flex;
  border-bottom: 1px solid var(--light-border-color);
  margin-bottom: 1rem;
`;

const TabButtonStyled = styled.button<{ active: boolean, isDarkTheme: boolean }>`
  padding: 0.5rem 1rem;
  background-color: ${props => props.active ? (props.isDarkTheme ? '#444' : '#f0f0f0') : 'transparent'};
  border: none;
  border-bottom: 2px solid ${props => props.active ? 'var(--light-accent-color)' : 'transparent'};
  color: ${props => props.isDarkTheme ? 'var(--dark-text-color)' : 'var(--light-text-color)'};
  cursor: pointer;
  transition: all 0.2s;
  &:hover {
    background-color: ${props => props.isDarkTheme ? '#555' : '#f5f5f5'};
  }
`;

const SpecialParsersContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 1rem;
  margin-top: 1rem;
`;

const ParserCard = styled.div<{ isDarkTheme: boolean }>`
  padding: 1rem;
  border-radius: 8px;
  border: 1px solid var(--light-border-color);
  background-color: ${props => props.isDarkTheme ? '#333' : '#f9f9f9'};
`;

const ParserCardTitle = styled.h3`
  margin: 0 0 0.5rem 0;
  font-size: 1.1rem;
`;

const ParserCardDescription = styled.p`
  margin: 0 0 1rem 0;
  font-size: 0.9rem;
`;

const ParsingDialog: React.FC<ParsingDialogProps> = ({ open, onClose }) => {
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [styleId, setStyleId] = useState<number | null>(null);
  const [requestInterval, setRequestInterval] = useState<number>(1);
  const [maxItems, setMaxItems] = useState<number | null>(null);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [isPolling, setIsPolling] = useState<boolean>(false);
  const [jobSnapshot, setJobSnapshot] = useState<any | null>(null);
  const [ws, setWs] = useState<WebSocket | null>(null);
  const [activeTab, setActiveTab] = useState<'general' | 'special'>('general');

  // Fetch available parsing sources and styles
  const { data: sources = [] } = useQuery({
    queryKey: ['parsingSources'],
    queryFn: fetchParsingSources,
  });

  const { data: styles = [] } = useQuery({
    queryKey: ['parsingStyles'],
    queryFn: fetchParsingStyles,
  });

  // Start parsing mutation
  const startParsingMutation = useMutation<any, Error, ParsingParams>({
    mutationFn: async (variables: ParsingParams) => {
      const requestForService: import('../../services/parsingService').ParsingRequest = {
        source_id: variables.source_id,
        style_id: variables.style_id,
        request_interval: variables.request_interval,
        max_items: variables.max_items === null ? undefined : variables.max_items,
      };
      return startParsing(requestForService); 
    },
    onSuccess: (data) => {
      toast.success('Parsing started successfully!');
      setActiveJobId(data.jobId);
      setIsPolling(true);
    },
    onError: (error) => {
      toast.error(`Failed to start parsing: ${error.message}`);
      setIsPolling(false);
    }
  } as UseMutationOptions<any, Error, ParsingParams>);

  useEffect(() => {
    if (startParsingMutation.data?.jobId) {
      setActiveJobId(startParsingMutation.data.jobId);
      setIsPolling(true);
      toast.success('Parsing started successfully');
    }
  }, [startParsingMutation.data]);

  // WebSocket stream per job
  useEffect(() => {
    if (!activeJobId || !isPolling) return;
    const base = window.location.origin.replace('http', 'ws');
    const socket = new WebSocket(`${base}/api/parsing/jobs/${activeJobId}/stream`);
    socket.onmessage = (ev) => {
      try { setJobSnapshot(JSON.parse(ev.data)); } catch {}
    };
    socket.onerror = () => {};
    setWs(socket);
    return () => { socket.close(); setWs(null); };
  }, [activeJobId, isPolling]);

  useEffect(() => {
    if (jobSnapshot && ['succeeded','failed','canceled','stalled'].includes(jobSnapshot.status)) {
      setIsPolling(false);
      if (jobSnapshot.status === 'succeeded') toast.success('Parsing completed');
      if (jobSnapshot.status === 'failed') toast.error(jobSnapshot.error_summary || 'Parsing failed');
    }
  }, [jobSnapshot]);

  // Handle form submission
  const handleStartParsing = (e: React.FormEvent) => {
    e.preventDefault();
    if (sourceId && styleId) {
      const params: ParsingParams = {
        source_id: sourceId,
        style_id: styleId,
        request_interval: requestInterval,
        max_items: maxItems
      };
      startParsingMutation.mutate(params);
    } else {
      toast.warn('Please select a source and style.');
    }
  };

  // Handle stop parsing
  const handleStopParsing = () => {
    if (activeJobId) {
      fetch(`/api/parsing/jobs/${activeJobId}/cancel`, { method: 'POST' });
    }
  };

  // Handle starting orders parsing
  const handleStartOrdersParsing = () => {
    startOrdersParsing().then(() => toast.success('Orders parsing started')).catch(err => toast.error(String(err)));
  };

  // Handle starting Google Sheets parsing
  const handleStartGoogleSheetsParsing = () => {
    startGoogleSheetsParsing().then(() => toast.success('Products parsing started')).catch(err => toast.error(String(err)));
  };

  if (!open) return null;

  const isFormDisabled = startParsingMutation.isPending || isPolling;
  const showProgress = isPolling || !!jobSnapshot;
  const progress = jobSnapshot?.percent ?? 0;

  return (
    <Overlay onClick={e => e.target === e.currentTarget && onClose()}>
      <DialogContainer isDarkTheme={isDark} onClick={e => e.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>Керування Парсингом Даних</DialogTitle>
          <CloseButtonStyled onClick={onClose}>
            <FontAwesomeIcon icon={faTimes} />
          </CloseButtonStyled>
        </DialogHeader>
        <DialogContent>
          <TabContainer>
            <TabButtons>
              <TabButtonStyled 
                active={activeTab === 'general'} 
                isDarkTheme={isDark}
                onClick={() => setActiveTab('general')}
              >
                General Parsing
              </TabButtonStyled>
              <TabButtonStyled 
                active={activeTab === 'special'} 
                isDarkTheme={isDark}
                onClick={() => setActiveTab('special')}
              >
                Data Import Scripts
              </TabButtonStyled>
            </TabButtons>
          </TabContainer>

          {activeTab === 'general' && (
            <Form onSubmit={handleStartParsing}>
              <FormGroup>
                <Label htmlFor="source">Source</Label>
                <SelectStyled 
                  id="source" 
                  value={sourceId ?? ''} 
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSourceId(Number(e.target.value))}
                  disabled={isFormDisabled}
                  isDarkTheme={isDark}
                >
                  <option value="">Select a source</option>
                  {sources.map(source => (
                    <option key={source.id} value={source.id}>
                      {source.name}
                    </option>
                  ))}
                </SelectStyled>
              </FormGroup>
              
              <FormGroup>
                <Label htmlFor="style">Parsing Style</Label>
                <SelectStyled 
                  id="style" 
                  value={styleId ?? ''} 
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setStyleId(Number(e.target.value))}
                  disabled={isFormDisabled}
                  isDarkTheme={isDark}
                >
                  <option value="">Select a style</option>
                  {styles.map(style => (
                    <option key={style.id} value={style.id}>
                      {style.name} - {style.description}
                    </option>
                  ))}
                </SelectStyled>
              </FormGroup>
              
              <FormGroup>
                <Label htmlFor="interval">Request Interval (seconds)</Label>
                <InputStyled 
                  id="interval" 
                  type="number" 
                  min="0.1" 
                  step="0.1" 
                  value={requestInterval} 
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setRequestInterval(parseFloat(e.target.value))}
                  disabled={isFormDisabled}
                  isDarkTheme={isDark}
                />
              </FormGroup>
              
              <FormGroup>
                <Label htmlFor="maxItems">Max Items (optional)</Label>
                <InputStyled 
                  id="maxItems" 
                  type="number" 
                  min="1" 
                  value={maxItems ?? ''} 
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setMaxItems(e.target.value ? parseInt(e.target.value) : null)}
                  disabled={isFormDisabled}
                  isDarkTheme={isDark}
                />
              </FormGroup>
              <ButtonStyled type="submit" variant="primary" disabled={isFormDisabled || !sourceId || !styleId}>
                <FontAwesomeIcon icon={faPlay} />
                {startParsingMutation.isPending ? 'Starting...' : (isPolling ? 'Parsing...' : 'Start Parsing')}
              </ButtonStyled>
            </Form>
          )}

          {activeTab === 'special' && (
            <SpecialParsersContainer>
              <ParserCard isDarkTheme={isDark}>
                <ParserCardTitle>Orders Import</ParserCardTitle>
                <ParserCardDescription>
                  Run the orders_pars.py script to import orders from Google Sheets into the database.
                </ParserCardDescription>
                <ButtonStyled
                  type="button"
                  onClick={handleStartOrdersParsing}
                  disabled={false}
                >
                  <FontAwesomeIcon icon={faFileImport} />
                  Import Orders
                </ButtonStyled>
              </ParserCard>

              <ParserCard isDarkTheme={isDark}>
                <ParserCardTitle>Products Import</ParserCardTitle>
                <ParserCardDescription>
                  Run the googlesheets_pars.py script to import products from Google Sheets into the database.
                </ParserCardDescription>
                <ButtonStyled
                  type="button"
                  onClick={handleStartGoogleSheetsParsing}
                  disabled={false}
                >
                  <FontAwesomeIcon icon={faTable} />
                  Import Products
                </ButtonStyled>
              </ParserCard>
            </SpecialParsersContainer>
          )}

          {showProgress && (
            <ProgressContainer>
              <h3>Parsing Progress</h3>
              <ProgressBar>
                <ProgressFill progress={progress} />
              </ProgressBar>
              
              <StatusText status={jobSnapshot?.status || 'running'}>
                Status: {jobSnapshot?.status || 'Running'}
              </StatusText>
              
              {jobSnapshot && (
                <>
                  <div>Step: {jobSnapshot.current_step || '...'}</div>
                  
                  {jobSnapshot.processed_items >= 0 && (
                    <ProgressStats>
                      <div>{jobSnapshot.processed_items} / {jobSnapshot.total_items ?? '...'}</div>
                      <div>Rate: {jobSnapshot.items_per_sec ?? 0} items/sec</div>
                      <div>ETA: {jobSnapshot.eta_seconds ?? '...' }s</div>
                    </ProgressStats>
                  )}
                </>
              )}
            </ProgressContainer>
          )}
        </DialogContent>
        <DialogFooter>
          <ButtonStyled variant="secondary" onClick={onClose}>
            Close
          </ButtonStyled>
          {activeJobId && isPolling && (
            <ButtonStyled variant="danger" onClick={handleStopParsing}>
              <FontAwesomeIcon icon={faStop} />
              Stop Parsing
            </ButtonStyled>
          )}
        </DialogFooter>
      </DialogContainer>
    </Overlay>
  );
};

export default ParsingDialog; 