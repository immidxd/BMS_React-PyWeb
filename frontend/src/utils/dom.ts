/** Поле, куди текст вводиться/вставляється сам собою (input/textarea/select/contenteditable). */
export function isEditableTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== 'string') return false;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = ((el as HTMLInputElement).type || 'text').toLowerCase();
    return !['button', 'checkbox', 'radio', 'submit', 'reset', 'file', 'image', 'range', 'color'].includes(type);
  }
  return !!el.isContentEditable;
}

/** Mac (⌘/⌥) чи ні (Windows-збірка BMS: Ctrl/Alt). */
export function isMacPlatform(): boolean {
  const p = (typeof navigator !== 'undefined' && (navigator.platform || navigator.userAgent)) || '';
  return /Mac|iPhone|iPad/i.test(p);
}

/** Підпис гарячої клавіші з Option/Alt: «⌥1» на Mac, «Alt+1» деінде. */
export const altKeyLabel = (key: string) => (isMacPlatform() ? `⌥${key}` : `Alt+${key}`);
