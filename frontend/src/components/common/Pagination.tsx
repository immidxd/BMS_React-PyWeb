import React from 'react';

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  totalItems?: number;
  itemsPerPage?: number;
  onPageChange: (page: number) => void;
  onPerPageChange?: (perPage: number) => void;
  showRange?: boolean;
}

const Pagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  totalItems = 0,
  itemsPerPage = 20,
  onPageChange,
  onPerPageChange,
  showRange = true
}) => {
  const startItem = totalItems ? (currentPage - 1) * itemsPerPage + 1 : 0;
  const endItem = totalItems ? Math.min(currentPage * itemsPerPage, totalItems) : 0;

  const pageButtons: React.ReactElement[] = [];
  const maxButtons = 5;
  let startPage = Math.max(1, currentPage - Math.floor(maxButtons / 2));
  let endPage = startPage + maxButtons - 1;
  if (endPage > totalPages) {
    endPage = totalPages;
    startPage = Math.max(1, endPage - maxButtons + 1);
  }
  for (let i = startPage; i <= endPage; i++) {
    pageButtons.push(
      <button
        key={i}
        aria-label={`Сторінка ${i}`}
        onClick={() => onPageChange(i)}
        className={`bms-page-btn ${i === currentPage ? 'is-active' : ''}`}
        disabled={i === currentPage}
      >
        {i}
      </button>
    );
  }

  return (
    <div className="flex flex-col md:flex-row justify-center items-center w-full gap-2 select-none">
      {showRange && (
        <div className="hidden md:block text-xs md:text-sm text-gray-500 md:mr-2">
          Показано {startItem}-{endItem} з {totalItems}
        </div>
      )}
      <div className="flex items-center justify-center gap-1 px-1 py-1">
        <button
          aria-label="Перша сторінка"
          onClick={() => onPageChange(1)}
          className="bms-page-nav"
          disabled={currentPage === 1}
        >
          &#x21E4;
        </button>
        <button
          aria-label="Попередня сторінка"
          onClick={() => onPageChange(currentPage - 1)}
          className="bms-page-nav"
          disabled={currentPage === 1}
        >
          &#8592;
        </button>
        <div className="flex items-center gap-1">
          {pageButtons.map((btn, idx) => {
            const p: any = btn.props as any;
            return (
            <button
              key={idx}
              aria-label={`Сторінка ${p.children}`}
              onClick={p.onClick}
              disabled={p.disabled}
              className={`bms-page-btn ${p.disabled ? 'is-active' : ''}`}
            >
              {p.children}
            </button>
            );
          })}
        </div>
        <button
          aria-label="Наступна сторінка"
          onClick={() => onPageChange(currentPage + 1)}
          className="bms-page-nav"
          disabled={currentPage === totalPages || totalPages === 0}
        >
          &#8594;
        </button>
        <button
          aria-label="Остання сторінка"
          onClick={() => onPageChange(totalPages)}
          className="bms-page-nav"
          disabled={currentPage === totalPages || totalPages === 0}
        >
          &#x21E5;
        </button>
      </div>
      {onPerPageChange && (
        <div className="ml-2">
          <select
            value={itemsPerPage}
            onChange={(e) => onPerPageChange(Number(e.target.value))}
            className="px-2 py-1 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-400 text-sm"
            aria-label="Кількість на сторінці"
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      )}
    </div>
  );
};

export default Pagination; 