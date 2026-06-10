import React, { useState, useEffect, useRef } from 'react';
import { searchService, GlobalSearchResponse } from '../../services/searchService';
import SearchResultsPreview from '../search/SearchResultsPreview';
import SearchInsights from '../search/SearchInsights';

interface SearchBarProps {
  onSearch: (searchTerm: string) => void;
  placeholder?: string;
  showGlobalResults?: boolean;
  currentScope?: string;
}

const SearchBar: React.FC<SearchBarProps> = ({ 
  onSearch,
  placeholder = "Пошук по базі...",
  showGlobalResults = false,
  currentScope
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [globalResults, setGlobalResults] = useState<GlobalSearchResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  // Закриття dropdown при кліку поза ним
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Очищення debounce при розмонтуванні
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setSearchTerm(value);
    
    // Викликаємо onSearch одразу для динамічного оновлення
    onSearch(value);
    
    // Очищаємо попередній debounce
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    
    // Якщо включено глобальні результати
    if (showGlobalResults) {
      if (value.length >= 2) {
        // Використовуємо debounce для глобального пошуку
        debounceRef.current = setTimeout(() => {
          performGlobalSearch(value);
        }, 300); // 300ms затримка
      } else {
        setGlobalResults(null);
        setShowDropdown(false);
      }
    }
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (searchTerm.trim()) {
      onSearch(searchTerm.trim());
      setShowDropdown(false);
    }
  };

  const handleInputFocus = () => {
    if (globalResults && searchTerm.length >= 2) {
      setShowDropdown(true);
    }
  };

  const handleClear = () => {
    setSearchTerm('');
    onSearch('');
    setGlobalResults(null);
    setShowDropdown(false);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    inputRef.current?.focus();
  };

  const performGlobalSearch = async (query: string) => {
    setIsLoading(true);
    try {
      const results = await searchService.globalSearch(query, {
        limit: 3, // Показуємо тільки превью
        include_insights: true,
        scope: currentScope as any
      });
      setGlobalResults(results);
      setShowDropdown(true);
    } catch (error) {
      console.error('Global search error:', error);
      setGlobalResults(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleNavigateToCategory = (category: string, query: string) => {
    // TODO: Реалізувати навігацію до категорії
    console.log(`Navigate to ${category} with query: ${query}`);
    setShowDropdown(false);
  };

  const handleSelectItem = (category: string, item: any) => {
    // TODO: Реалізувати вибір елемента
    console.log(`Selected ${category} item:`, item);
    setShowDropdown(false);
  };

  return (
    <div className="w-full relative" ref={dropdownRef}>
      <form onSubmit={handleSubmit} className="w-full">
        <div className="relative flex items-center">
          <input
            ref={inputRef}
            type="text"
            // Вимикаємо браузерний autofill/історію: інакше Chrome/Safari підміняють
            // щойно введений текст (напр. «сітк») на схожий збережений запис («Стік»),
            // і список «через кілька секунд» стає порожнім. role=presentation +
            // нестандартний name глушать евристику автозаповнення навіть у Chrome,
            // що ігнорує autocomplete="off" для текстових полів.
            name="bms-search-no-autofill"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            value={searchTerm}
            onChange={handleInputChange}
            onFocus={handleInputFocus}
            placeholder={placeholder}
            className={`w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-primary-500 dark:focus:ring-primary-400 focus:border-transparent outline-none bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-shadow shadow-sm hover:shadow focus:shadow-md ${searchTerm ? 'pr-16' : 'pr-10'}`}
          />
          {/* Кнопка очищення (показується тільки коли є текст) */}
          {searchTerm && (
            <button 
              type="button"
              onClick={handleClear}
              aria-label="Очистити пошук"
              className="absolute right-10 top-0 bottom-0 px-2 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 focus:outline-none"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </button>
          )}

          {/* Кнопка пошуку */}
          <button 
            type="submit" 
            aria-label="Пошук"
            className="absolute right-0 top-0 bottom-0 px-3 text-gray-500 dark:text-gray-400 hover:text-primary-600 dark:hover:text-primary-300 focus:outline-none focus:text-primary-600 dark:focus:text-primary-300"
          >
            {isLoading ? (
              <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="m4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clipRule="evenodd" />
              </svg>
            )}
          </button>
        </div>
      </form>

      {/* Dropdown з результатами глобального пошуку */}
      {showGlobalResults && showDropdown && globalResults && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 max-h-96 overflow-y-auto">
          <div className="p-4">
            {/* Інсайти */}
            <SearchInsights 
              insights={globalResults.insights}
              query={globalResults.query}
              onNavigateToCategory={handleNavigateToCategory}
            />
            
            {/* Превью результатів */}
            <SearchResultsPreview 
              results={globalResults}
              onNavigateToCategory={handleNavigateToCategory}
              onSelectItem={handleSelectItem}
            />
          </div>
        </div>
      )}
    </div>
  );
};

export default SearchBar; 