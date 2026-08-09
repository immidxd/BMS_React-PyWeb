import React, { createContext, useContext, useMemo } from 'react';

// ── Яка вкладка зараз АКТИВНА ────────────────────────────────────────────────
//
// Навіщо. App тримає keep-alive: кожна ВІДВІДАНА вкладка лишається змонтованою,
// неактивні просто сховані через CSS. Тобто одночасно живуть кілька сторінок, і
// кожна з них — свій <MainLayout>, свої слухачі, свої опитування бекенда.
//
// Без цього прапорця глобальна гаряча клавіша не знає, ЧИЯ вона: «скинути
// фільтри» діставалась тій сторінці, яка змонтувалась останньою, а не тій, що
// перед очима. Звідси й було «працює через раз» — залежно від того, які вкладки
// ти встиг відкрити.
//
// Провайдер ставить App навколо кожної вкладки. Поза провайдером (тести,
// окремий рендер) вважаємо сторінку активною — щоб нічого не «мовчало».

const ActivePageContext = createContext<boolean>(true);

export const ActivePageProvider: React.FC<{ isActive: boolean; children: React.ReactNode }> = ({
  isActive, children,
}) => {
  const value = useMemo(() => isActive, [isActive]);
  return <ActivePageContext.Provider value={value}>{children}</ActivePageContext.Provider>;
};

/** true, якщо сторінка, що викликає, зараз на екрані. */
export function useIsActivePage(): boolean {
  return useContext(ActivePageContext);
}

export default ActivePageContext;
