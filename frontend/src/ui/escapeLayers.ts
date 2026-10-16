/**
 * Esc у всій програмі: «закрити ВЕРХНЄ відкрите вікно чи меню».
 *
 * Чому окремий механізм. У вебвʼю BMS на Mac Esc, якого сторінка не погасила
 * (preventDefault), доходить до вікна й виводить його з повного екрана — уся
 * програма «зменшується». Раніше частина вікон закривалась І водночас
 * зменшувала програму, а частина на Esc не реагувала зовсім.
 *
 * Правила:
 *   • щось відкрите (вікно, меню, список) або фокус у полі вводу — Esc гаситься,
 *     вікно програми не змінюється;
 *   • закривається лише ВЕРХНЄ: вікна стають у стек у порядку відкриття;
 *   • Esc робить те саме, що клік повз вікно (зайняте вікно — нічого);
 *   • поле чи список усередині вікна, що саме обробило Esc (скасувати правку,
 *     закрити підказки), зупиняє подію (stopPropagation) — тоді вікно лишається;
 *   • вікно зі своєю логікою Esc (картка товару: спершу виділення, потім
 *     редагування…) стає в стек як 'own' і діє, лише коли воно згори (isTop);
 *   • меню й списки antd/MUI закривають себе самі — ми лише гасимо Esc;
 *   • нічого не відкрито — Esc поводиться як завжди.
 *
 * Шари неактивної вкладки (keep-alive) у стек не потрапляють: Esc на «Замовленнях»
 * не закриє картку, що лишилась відкритою на «Товарах».
 */
import { useCallback, useEffect, useRef } from 'react';
import { useIsActivePage } from '../contexts/ActivePageContext';
import { isEditableTarget } from '../utils/dom';

/** функція — що зробити; undefined — погасити й нічого (вікно зайняте); 'own' — вікно обробляє Esc саме. */
export type EscapeHandler = (() => void) | undefined | 'own';

interface Layer { id: number; order: number; modal: boolean; handler: () => EscapeHandler }

const stack: Layer[] = [];
let seq = 0;

// Відкриті меню/списки/вікна бібліотек — вони самі закриваються за Esc.
const LIBRARY_POPUPS = [
  '.ant-select-dropdown:not(.ant-select-dropdown-hidden)',
  '.ant-dropdown:not(.ant-dropdown-hidden)',
  '.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)',
  '.ant-popconfirm:not(.ant-popover-hidden)',
  '.ant-modal-wrap',
  '.ant-image-preview-wrap',
  '.MuiModal-root:not(.MuiModal-hidden)',
].join(',');

const isVisible = (el: Element) => el.getClientRects().length > 0;

export function libraryPopupOpen(): boolean {
  if (typeof document === 'undefined') return false;
  return Array.from(document.querySelectorAll(LIBRARY_POPUPS)).some(isVisible);
}

const topLayer = (): Layer | undefined => {
  let top: Layer | undefined;
  for (const l of stack) if (!top || l.order > top.order) top = l;
  return top;
};

let popupAtStart = false;

function onKeyCapture(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.isComposing) return;
  // Запам'ятовуємо ДО обробників: список antd закриється вже в них.
  popupAtStart = libraryPopupOpen();
  if (stack.length > 0 || popupAtStart || isEditableTarget(e.target)) e.preventDefault();
}

function onKeyBubble(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.isComposing) return;
  if (popupAtStart) return;             // Esc був для меню/списку бібліотеки
  const top = topLayer();
  if (!top) return;
  const h = top.handler();
  if (h === 'own') return;              // вікно обробило Esc своїм слухачем
  e.stopImmediatePropagation();         // сторінка позаду Esc уже не отримує
  if (typeof h === 'function') h();
}

let installed = false;
/** Один раз, ДО рендера: слухач у фазі перехоплення має йти першим. */
export function installEscapeLayers(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('keydown', onKeyCapture, true);
  window.addEventListener('keydown', onKeyBubble);
}

/**
 * Шар Esc, поки `active`. Повертає isTop() — для вікон зі своїм слухачем
 * (`'own'`): обробляти клавіші, лише коли вони згори.
 * `menu: true` — випадне меню/список/панель: Esc закриває його так само, але
 * гарячі клавіші сторінки (папки ⌥1…⌥0) воно не блокує, на відміну від вікна.
 */
export function useEscapeLayer(active: boolean, onEscape: EscapeHandler, opts?: { menu?: boolean }): () => boolean {
  const modal = !opts?.menu;
  const isActivePage = useIsActivePage();
  const handlerRef = useRef<EscapeHandler>(onEscape);
  handlerRef.current = onEscape;
  const idRef = useRef<number | null>(null);
  // Порядок — від моменту ВІДКРИТТЯ: повернення на вкладку не робить вікно,
  // відкрите першим, верхнім (діти реєструються раніше за батьків).
  const orderRef = useRef(0);
  useEffect(() => { if (active) orderRef.current = ++seq; }, [active]);
  useEffect(() => {
    if (!active || !isActivePage) return;
    const layer: Layer = { id: ++seq, order: orderRef.current || ++seq, modal, handler: () => handlerRef.current };
    stack.push(layer);
    idRef.current = layer.id;
    return () => {
      const i = stack.indexOf(layer);
      if (i >= 0) stack.splice(i, 1);
      if (idRef.current === layer.id) idRef.current = null;
    };
  }, [active, isActivePage, modal]);
  return useCallback(() => idRef.current != null && topLayer()?.id === idRef.current, []);
}

/** Поверх сторінки відкрите вікно (не меню): гарячі клавіші сторінки тоді мовчать. */
export const modalOverlayOpen = () => stack.some((l) => l.modal) || libraryPopupOpen();

/** Для тестів. */
export const __escapeStackSize = () => stack.length;
