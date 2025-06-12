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
  stopParsing, 
  fetchParsingStatus, 
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
      case 'completed': return 'green';
      case 'failed': return 'red';
      case 'cancelled': return 'orange';
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
  const [activeLogId, setActiveLogId] = useState<number | null>(null);
  const [isPolling, setIsPolling] = useState<boolean>(false);
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
      // Prepare the request object ensuring max_items is number or undefined, not null.
      const requestForService: import('../../services/parsingService').ParsingRequest = {
        source_id: variables.source_id,
        style_id: variables.style_id,
        request_interval: variables.request_interval,
        // If max_items is null, pass undefined; otherwise, pass the value.
        max_items: variables.max_items === null ? undefined : variables.max_items,
      };
      return startParsing(requestForService); 
    },
    onSuccess: (data) => {
      toast.success(data.message || 'Parsing started successfully!');
      setActiveLogId(data.log_id);
      setIsPolling(true);
    },
    onError: (error) => {
      toast.error(`Failed to start parsing: ${error.message}`);
      setIsPolling(false);
    }
  } as UseMutationOptions<any, Error, ParsingParams>);

  useEffect(() => {
    if (startParsingMutation.data) {
      setActiveLogId(startParsingMutation.data.log_id);
      setIsPolling(true);
      toast.success("Parsing started successfully");
    }
  }, [startParsingMutation.data]);

  // Stop parsing mutation
  const stopParsingMutation = useMutation<any, Error, number>({
    mutationFn: stopParsing,
    onSuccess: (data) => {
      toast.info(data.message || 'Parsing stopped successfully!');
      setIsPolling(false);
      if (activeLogId) refetchStatus(); // Fetch final status
    },
    onError: (error) => {
      toast.error(`Failed to stop parsing: ${error.message}`);
    }
  });

  useEffect(() => {
    if (stopParsingMutation.isSuccess) {
      setIsPolling(false);
      toast.info("Parsing stopped");
    }
  }, [stopParsingMutation.isSuccess]);

  // Fetch parsing status
  const { data: parsingStatus, refetch: refetchStatus } = useQuery({
    queryKey: ['parsingStatus', activeLogId],
    queryFn: () => activeLogId ? fetchParsingStatus(activeLogId) : Promise.resolve(null),
    enabled: !!activeLogId && isPolling,
    refetchInterval: isPolling ? 1000 : false,
  });

  useEffect(() => {
    if (parsingStatus) {
      if (parsingStatus.status === 'completed' || parsingStatus.status === 'failed' || parsingStatus.status === 'cancelled') {
        setIsPolling(false);
        if (parsingStatus.status === 'completed') {
          toast.success(`Parsing completed: ${parsingStatus.items_processed} items processed`);
        } else if (parsingStatus.status === 'failed') {
          toast.error(`Parsing failed: ${parsingStatus.message}`);
        }
      }
    }
  }, [parsingStatus]);

  // Add mutations for special parsers
  const ordersParseMutation = useMutation<any, Error, void>({
    mutationFn: startOrdersParsing,
    onSuccess: (data) => toast.success(data.message || 'Orders parsing started!'),
    onError: (error) => toast.error(`Orders parsing failed: ${error.message}`),
  });

  useEffect(() => {
    if (ordersParseMutation.data) {
      toast.success('Orders parsing started successfully');
      setActiveLogId(ordersParseMutation.data.log_id);
      setIsPolling(true);
    }
  }, [ordersParseMutation.data]);

  const googleSheetsParseMutation = useMutation<any, Error, void>({
    mutationFn: startGoogleSheetsParsing,
    onSuccess: (data) => toast.success(data.message || 'Google Sheets parsing started!'),
    onError: (error) => toast.error(`Google Sheets parsing failed: ${error.message}`),
  });

  useEffect(() => {
    if (googleSheetsParseMutation.data) {
      toast.success('Google Sheets parsing started successfully');
      setActiveLogId(googleSheetsParseMutation.data.log_id);
      setIsPolling(true);
    }
  }, [googleSheetsParseMutation.data]);

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
    if (activeLogId) {
      stopParsingMutation.mutate(activeLogId);
    }
  };

  // Handle starting orders parsing
  const handleStartOrdersParsing = () => {
    ordersParseMutation.mutate();
  };

  // Handle starting Google Sheets parsing
  const handleStartGoogleSheetsParsing = () => {
    googleSheetsParseMutation.mutate();
  };

  // Clean up polling when dialog closes
  useEffect(() => {
    if (!open) {
      setIsPolling(false);
    }
  }, [open]);

  // Check polling status and set up interval
  useEffect(() => {
    let interval: NodeJS.Timeout;
    
    if (isPolling && activeLogId) {
      interval = setInterval(() => {
        refetchStatus();
      }, 1000);
    }
    
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isPolling, activeLogId, refetchStatus]);

  if (!open) return null;

  const isFormDisabled = startParsingMutation.isPending || isPolling;
  const showProgress = isPolling || (parsingStatus && parsingStatus.status !== 'unknown');
  
  // Calculate progress percentage
  const progress = parsingStatus ? 
    parsingStatus.progress || 
    (parsingStatus.total_items && parsingStatus.current_item ? 
      Math.round((parsingStatus.current_item / parsingStatus.total_items) * 100) : 0) : 0;

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
                  disabled={ordersParseMutation.isPending}
                >
                  <FontAwesomeIcon icon={faFileImport} />
                  {ordersParseMutation.isPending ? 'Starting...' : 'Import Orders'}
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
                  disabled={googleSheetsParseMutation.isPending}
                >
                  <FontAwesomeIcon icon={faTable} />
                  {googleSheetsParseMutation.isPending ? 'Starting...' : 'Import Products'}
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
              
              <StatusText status={parsingStatus?.status || 'in_progress'}>
                Status: {parsingStatus?.status || 'In progress'}
              </StatusText>
              
              {parsingStatus && (
                <>
                  <div>{parsingStatus.message}</div>
                  
                  {parsingStatus.items_processed > 0 && (
                    <ProgressStats>
                      <div>Processed: {parsingStatus.items_processed}</div>
                      <div>Added: {parsingStatus.items_added}</div>
                      <div>Updated: {parsingStatus.items_updated}</div>
                      <div>Failed: {parsingStatus.items_failed}</div>
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
          {activeLogId && isPolling && (
            <ButtonStyled variant="danger" onClick={handleStopParsing} disabled={stopParsingMutation.isPending}>
              <FontAwesomeIcon icon={faStop} />
              {stopParsingMutation.isPending ? 'Stopping...' : 'Stop Parsing'}
            </ButtonStyled>
          )}
        </DialogFooter>
      </DialogContainer>
    </Overlay>
  );
};

export default ParsingDialog; 