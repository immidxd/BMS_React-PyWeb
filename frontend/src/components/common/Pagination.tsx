import React from 'react';

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  totalItems?: number;
  itemsPerPage?: number;
  onPageChange: (page: number) => void;
  onPerPageChange?: (perPage: number) => void;
}

const Pagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  totalItems = 0,
  itemsPerPage = 20,
  onPageChange,
  onPerPageChange
}) => {
  const startItem = totalItems ? (currentPage - 1) * itemsPerPage + 1 : 0;
  const endItem = totalItems ? Math.min(currentPage * itemsPerPage, totalItems) : 0;

  const pageButtons = [];
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
        className={`mx-1 px-3 py-1 rounded-full border transition-colors duration-150 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-400
          ${i === currentPage ? 'bg-blue-500 text-white border-blue-500' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-100'}
        `}
        disabled={i === currentPage}
      >
        {i}
      </button>
    );
  }

  return (
    <div className="flex flex-col md:flex-row justify-between items-center w-full gap-2">
      <div className="text-xs md:text-sm text-gray-500 mb-2 md:mb-0">
        Показано {startItem}-{endItem} з {totalItems} записів
      </div>
      <div className="flex items-center">
        <button
          aria-label="Перша сторінка"
          onClick={() => onPageChange(1)}
          className="mx-1 px-2 py-1 rounded-full border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={currentPage === 1}
        >
          &#x21E4;
        </button>
        <button
          aria-label="Попередня сторінка"
          onClick={() => onPageChange(currentPage - 1)}
          className="mx-1 px-2 py-1 rounded-full border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={currentPage === 1}
        >
          &#8592;
        </button>
        {pageButtons}
        <button
          aria-label="Наступна сторінка"
          onClick={() => onPageChange(currentPage + 1)}
          className="mx-1 px-2 py-1 rounded-full border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={currentPage === totalPages || totalPages === 0}
        >
          &#8594;
        </button>
        <button
          aria-label="Остання сторінка"
          onClick={() => onPageChange(totalPages)}
          className="mx-1 px-2 py-1 rounded-full border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
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