import React from 'react';

interface RefreshButtonProps {
  onClick: () => void;
  isLoading?: boolean;
  label?: string;
}

const RefreshButton: React.FC<RefreshButtonProps> = ({
  onClick,
  isLoading = false,
  label = "Оновити дані"
}) => {
  return (
    <button
      onClick={onClick}
      disabled={isLoading}
      aria-label={label}
      className={`p-2 rounded-full flex items-center justify-center 
                 bg-blue-500 hover:bg-blue-600 dark:bg-blue-600 dark:hover:bg-blue-700 
                 text-white transition-all duration-150 ease-in-out 
                 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 dark:focus:ring-offset-gray-800
                 disabled:opacity-50 disabled:cursor-not-allowed 
                 transform hover:scale-105 active:scale-95`}
    >
      {isLoading ? (
        <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      ) : (
        // Refresh Icon SVG (Unicode U+1F504 might not render consistently, so SVG is better)
        <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clipRule="evenodd" />
        </svg>
      )}
      {/* {label && !isLoading && <span className="ml-2">{label}</span>} */}
    </button>
  );
};

export default RefreshButton; 