import React, { useEffect } from 'react';
import FilterPanel from '../components/common/FilterPanel';
import { useFilterPanel } from '../contexts/FilterPanelContext';
import { useIsActivePage } from '../contexts/ActivePageContext';
import { toast } from 'react-toastify';

interface MainLayoutProps {
  children: React.ReactNode;
  filterPanelContent: React.ReactNode;
  onRefresh: () => void;
  isRefreshing?: boolean;
  onResetFilters: () => void;
}

const MainLayout: React.FC<MainLayoutProps> = ({
  children,
  filterPanelContent,
  onRefresh,
  isRefreshing = false,
  onResetFilters,
}) => {
  const { isFilterPanelOpen, openFilterPanel, closeFilterPanel } = useFilterPanel();
  const isActivePage = useIsActivePage();

  // ⚠️ Тут БУВ опит `/api/parsing/status` кожні 1.5 с. Він писав у стан
  // isParsing/hideButtonUntil, які НЕ рендерились ніде (плаваючу кнопку
  // оновлення прибрали з UI ще раніше) — тобто чистий холостий трафік. І при
  // keep-alive таких інтервалів було стільки, скільки вкладок ти відвідав:
  // 8 вкладок = 8 запитів кожні півтори секунди, що змагались за бекенд із
  // завантаженням картки товару й фото. Прибрано разом із мертвим станом.

  // ⌘/Ctrl+R — скинути фільтри активної сторінки.
  // ВАЖЛИВО: слухаємо e.code === 'KeyR' (фізична клавіша), бо e.key на
  // кириличній розкладці повертає 'к' і умова `=== 'r'` ніколи не спрацьовувала.
  //
  // ⚠️ Слухач вішається ОДИН раз на весь застосунок, а не на кожен MainLayout.
  // Кожна сторінка рендерить власний <MainLayout>, і при переході між вкладками
  // cleanup попереднього не відпрацьовував — слухачі накопичувались (заміряно:
  // 3 переходи → 5 живих обробників). Наслідок був не лише косметичний (N тостів
  // «Фільтри скинуто» на одне натискання): кожен протеклий слухач тримав
  // onResetFilters СТАРОЇ сторінки й скидав її фільтри наосліп.
  //
  // Прапорець і колбек живуть на window, а не в модулі, бо MainLayout потрапляє
  // у кілька чанків збірки — модульна змінна існувала б у кількох копіях.
  //
  // 🐞 БУВ БАГ («скидання фільтрів працює через раз»): реєстрація
  // `w.__bmsResetFilters = resetRef` стояла в useEffect з ПОРОЖНІМИ deps, тобто
  // виконувалась один раз ПРИ МОНТУВАННІ кожної сторінки. А через keep-alive у
  // App змонтовані ВСІ відвідані вкладки — тож у глобальному слоті лишався
  // resetRef тієї сторінки, яку відкрили ОСТАННЬОЮ, і він там і застрягав.
  // Натиснувши ⌘R на «Товарах» після візиту в «Клієнти», ти скидав фільтри
  // Клієнтів — візуально «нічого не сталось». Тепер слот перезаписує лише
  // АКТИВНА сторінка (isActive з ActivePageContext), і при кожній її активації.
  const resetRef = React.useRef(onResetFilters);
  resetRef.current = onResetFilters;   // завжди актуальна активна сторінка

  useEffect(() => {
    if (!isActivePage) return;
    (window as any).__bmsResetFilters = resetRef;
  }, [isActivePage]);

  useEffect(() => {
    const w = window as any;
    if (w.__bmsResetHotkeyBound) return;
    w.__bmsResetHotkeyBound = true;
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod || e.altKey) return;
      if (e.code !== 'KeyR') return;
      e.preventDefault();   // блокуємо стандартне перезавантаження
      if (e.repeat) return; // утримана клавіша не має плодити повтори
      const handler = w.__bmsResetFilters?.current;
      if (typeof handler !== 'function') return;   // сторінка без фільтрів — мовчки
      handler();
      // toastId — навіть якщо щось спрацює двічі, тост лишиться один
      toast.info('Фільтри скинуто', {
        toastId: 'filters-reset', autoClose: 1200, hideProgressBar: true,
      });
    };
    window.addEventListener('keydown', onKey);
    // Слухач навмисно живе до кінця сесії (він один на застосунок) — знімати
    // його при розмонтуванні ОДНОГО з кількох MainLayout було б помилкою.
  }, []);

return (
  <div className="main-layout flex flex-col h-full min-h-0 relative">{/* заповнюємо доступну висоту <main>, а не цілий екран — інакше body теж скролився б (подвійний слайдер) */}
      <div className="flex flex-row flex-grow gap-4 overflow-hidden px-2 sm:px-4">{/* трохи більше корисної ширини */}
        <div 
          className="hidden sm:block fixed left-0 top-0 bottom-0 w-4 z-30 cursor-pointer"
          onMouseEnter={openFilterPanel}
        >
          <div className="h-full w-px bg-gray-300 dark:bg-gray-600 opacity-50 hover:opacity-100 transition-opacity"></div>
        </div>

        <div className="content-area flex-grow w-full overflow-auto">{/* контент займає доступну висоту */}
          {children}
        </div>
      </div>
      
      {/* Плаваюча кнопка оновлення — прибрана з UI */}

      <FilterPanel 
        isOpen={isFilterPanelOpen} 
        onClose={closeFilterPanel}
        onResetFilters={onResetFilters}
      >
        {filterPanelContent}
      </FilterPanel>

      {/* ⚠️ Тут БУЛИ власні <ParsingDialog> і <ParsingStatus> — дублікати тих, що
          вже рендерить App на весь застосунок. Діалог не мав чим відкритись
          (кнопка, що його викликала, прибрана з UI ще раніше), а ParsingStatus
          із jobId=null відкриває WebSocket на /api/parsing/ws — тобто при
          keep-alive застосунок тримав стільки зайвих сокетів, скільки вкладок
          ти відвідав. Прибрано: парсинг живе в App і показується так само. */}
    </div>
  );
};

export default MainLayout; 
