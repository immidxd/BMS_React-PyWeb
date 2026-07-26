import React from 'react';
import LoadingSpinner from './LoadingSpinner';
import { isChunkLoadError, reloadForNewBuild } from '../../services/chunkReload';

/**
 * Захист області лінивих сторінок: спінер, який не може «зависнути назавжди»,
 * плюс межа помилок замість білого екрана.
 *
 * Сценарій, заради якого це існує: фронтенд перезібрали, а вкладка стара —
 * чанк сторінки віддає 404. Див. services/chunkReload.ts.
 */

const Card: React.FC<{ title: string; hint: string }> = ({ title, hint }) => (
  <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
    <div className="text-3xl mb-3">⟳</div>
    <div className="text-base font-semibold text-gray-800 dark:text-gray-100">{title}</div>
    <div className="text-sm text-gray-500 dark:text-gray-400 mt-1.5 max-w-md">{hint}</div>
    <button
      type="button"
      onClick={() => window.location.reload()}
      className="mt-5 px-4 py-2 rounded-lg text-sm font-medium bg-gray-900 text-white hover:bg-gray-700 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white transition-colors"
    >
      Оновити сторінку
    </button>
  </div>
);

/** Скільки чекати, поки визнаємо спінер «застряглим» (локальний бекенд — це вічність). */
const STUCK_AFTER_MS = 10_000;

const PageLoading: React.FC = () => {
  const [stuck, setStuck] = React.useState(false);

  React.useEffect(() => {
    const timer = setTimeout(() => {
      // Перша підозра — застаріла збірка: пробуємо одне перезавантаження.
      // Якщо воно вже було (cooldown) — показуємо картку, а не крутимо далі.
      if (!reloadForNewBuild('suspense-stuck')) setStuck(true);
    }, STUCK_AFTER_MS);
    return () => clearTimeout(timer);
  }, []);

  if (stuck) {
    return (
      <Card
        title="Сторінка не завантажується"
        hint="Схоже, вкладка працює на старій версії інтерфейсу або бекенд не відповідає. Оновіть сторінку — якщо не допомогло, перезапустіть застосунок."
      />
    );
  }
  return <LoadingSpinner variant="page" size="large" text="Завантаження сторінки…" />;
};

interface State { error: Error | null }

class PageBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error('Page load error:', error);
    // Помилка чанка вже спробувала перезавантажити себе в lazyWithRetry;
    // сюди доходить лише те, що не полагодилось — показуємо картку.
    if (isChunkLoadError(error)) reloadForNewBuild('page-boundary');
  }

  render() {
    if (this.state.error) {
      return isChunkLoadError(this.state.error) ? (
        <Card
          title="Інтерфейс оновився"
          hint="Ця вкладка залишилась на попередній версії. Оновіть сторінку, щоб підхопити свіжу збірку."
        />
      ) : (
        <Card
          title="Не вдалося відкрити сторінку"
          hint={String(this.state.error?.message || this.state.error)}
        />
      );
    }
    return <React.Suspense fallback={<PageLoading />}>{this.props.children}</React.Suspense>;
  }
}

export default PageBoundary;
