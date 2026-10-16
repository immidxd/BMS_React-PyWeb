import React from 'react';
import { createPortal } from 'react-dom';
import { CloseOutlined, ExpandAltOutlined } from '@ant-design/icons';

/**
 * Згорнуте вікно — «плашка» внизу праворуч, поруч із дзвіником Центру завдань.
 *
 * Навіщо. Пакет на 30+ карток готується хвилинами, і посеред нього часто треба
 * глянути щось у програмі (картку товару, іншу вкладку). Раніше вікно стояло
 * модально поверх усього, а клік повз нього викидав усі налаштування.
 * Згорнуте вікно лишається змонтованим (стан карток цілий), а плашка розгортає
 * його назад — на будь-якій вкладці.
 *
 * Плашки кількох згорнутих вікон стають у спільний лоток (`#bms-dock-tray`)
 * в ряд, а не одна на одну.
 */

function dockTray(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  let tray = document.getElementById('bms-dock-tray');
  if (!tray) {
    tray = document.createElement('div');
    tray.id = 'bms-dock-tray';
    tray.className = 'bms-dock-tray';
    document.body.appendChild(tray);
  }
  return tray;
}

/** Зупиняє React-спливання з порталу до батьків (див. react-portal-event-bubbling). */
export const portalBarrier = {
  onClick: (e: React.SyntheticEvent) => e.stopPropagation(),
  onMouseDown: (e: React.SyntheticEvent) => e.stopPropagation(),
  onMouseUp: (e: React.SyntheticEvent) => e.stopPropagation(),
  onPointerDown: (e: React.SyntheticEvent) => e.stopPropagation(),
  onPointerUp: (e: React.SyntheticEvent) => e.stopPropagation(),
  onDoubleClick: (e: React.SyntheticEvent) => e.stopPropagation(),
  onContextMenu: (e: React.SyntheticEvent) => e.stopPropagation(),
  onKeyDown: (e: React.SyntheticEvent) => e.stopPropagation(),
};

interface Props {
  icon: React.ReactNode;
  /** Колір квадратика з іконкою — той самий, що в шапці вікна. */
  accent: string;
  title: string;
  subtitle?: string;
  busy?: boolean;
  /** Щось пішло не так, поки вікно було згорнуте — крапка-нагадування. */
  attention?: boolean;
  onRestore: () => void;
  /** Без нього (напр. під час публікації) хрестика нема. */
  onClose?: () => void;
}

const MinimizedDock: React.FC<Props> = ({
  icon, accent, title, subtitle, busy, attention, onRestore, onClose,
}) => {
  const tray = dockTray();
  if (!tray) return null;
  return createPortal(
    <div className="bms-dock" role="group" aria-label={`Згорнуте вікно: ${title}`} {...portalBarrier}>
      <button type="button" onClick={onRestore} className="bms-dock-main" title="Розгорнути">
        <span className="bms-dock-icon" style={{ backgroundColor: accent }}>
          {busy ? <span className="bms-dock-spinner" aria-hidden /> : icon}
          {attention && !busy && <span className="bms-dock-dot" aria-label="Потребує уваги" />}
        </span>
        <span className="bms-dock-text">
          <span className="bms-dock-title">{title}</span>
          {subtitle && <span className="bms-dock-subtitle">{subtitle}</span>}
        </span>
        <ExpandAltOutlined className="bms-dock-expand" aria-hidden />
      </button>
      {onClose && !busy && (
        <button type="button" onClick={onClose} className="bms-dock-close" aria-label="Закрити вікно">
          <CloseOutlined style={{ fontSize: 10 }} />
        </button>
      )}
    </div>,
    tray,
  );
};

export default MinimizedDock;
