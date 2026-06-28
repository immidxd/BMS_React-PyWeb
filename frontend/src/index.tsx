import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ReactDOM from 'react-dom/client';
import './styles/bms-tokens.css';
import './index.css';
import App from './App';
import { AppThemeProvider } from './contexts/ThemeContext';
import { RuntimeConfigProvider } from './contexts/RuntimeConfigContext';
import './services/axiosConfig';

class RootErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error('Root render error:', error);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 16, fontFamily: 'sans-serif' }}>
          <h2>Помилка завантаження інтерфейсу</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{String(this.state.error)}</pre>
        </div>
      );
    }
    return this.props.children as any;
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <RootErrorBoundary>
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <RuntimeConfigProvider>
          <AppThemeProvider>
            <App />
          </AppThemeProvider>
        </RuntimeConfigProvider>
      </QueryClientProvider>
    </React.StrictMode>
  </RootErrorBoundary>
);