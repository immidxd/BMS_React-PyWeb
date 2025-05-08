import React, { createContext, useState, useContext, useMemo, useCallback } from 'react';

interface FilterPanelContextType {
  isFilterPanelOpen: boolean;
  openFilterPanel: () => void;
  closeFilterPanel: () => void;
  toggleFilterPanel: () => void;
}

const FilterPanelContext = createContext<FilterPanelContextType | undefined>(undefined);

export const FilterPanelProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);

  const openFilterPanel = useCallback(() => {
    setIsOpen(true);
  }, []);

  const closeFilterPanel = useCallback(() => {
    setIsOpen(false);
  }, []);

  const toggleFilterPanel = useCallback(() => {
    setIsOpen(prev => !prev);
  }, []);

  const value = useMemo(() => ({
    isFilterPanelOpen: isOpen,
    openFilterPanel,
    closeFilterPanel,
    toggleFilterPanel
  }), [isOpen, openFilterPanel, closeFilterPanel, toggleFilterPanel]);

  return (
    <FilterPanelContext.Provider value={value}>
      {children}
    </FilterPanelContext.Provider>
  );
};

export const useFilterPanel = (): FilterPanelContextType => {
  const context = useContext(FilterPanelContext);
  if (context === undefined) {
    throw new Error('useFilterPanel must be used within a FilterPanelProvider');
  }
  return context;
}; 