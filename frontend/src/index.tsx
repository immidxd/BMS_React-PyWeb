import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ReactDOM from 'react-dom/client';
import './styles/bms-tokens.css';
import './index.css';
import App from './App';
import { AppThemeProvider } from './contexts/ThemeContext';
import { RuntimeConfigProvider } from './contexts/RuntimeConfigContext';
import './services/axiosConfig';
import { installChunkErrorRecovery, isChunkLoadError } from './services/chunkReload';

// Вкладка, відкрита до перезбірки фронтенду, просить чанки зі старими хешами →
// 404. Перехоплюємо і підхоплюємо свіжу збірку замість зависання.
installChunkErrorRecovery();

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
      const stale = isChunkLoadError(this.state.error);
      return (
        <div style={{ padding: 16, fontFamily: 'sans-serif' }}>
          <h2>{stale ? 'Інтерфейс оновився' : 'Помилка завантаження інтерфейсу'}</h2>
          {stale ? (
            <p>Ця вкладка залишилась на попередній версії застосунку.</p>
          ) : (
            <pre style={{ whiteSpace: 'pre-wrap' }}>{String(this.state.error)}</pre>
          )}
          <button onClick={() => window.location.reload()} style={{ marginTop: 12, padding: '8px 14px', cursor: 'pointer' }}>
            Оновити сторінку
          </button>
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