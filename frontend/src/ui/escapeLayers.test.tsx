import React from 'react';
import { act, render } from '@testing-library/react';
import { ActivePageProvider } from '../contexts/ActivePageContext';
import { __escapeStackSize, installEscapeLayers, modalOverlayOpen, useEscapeLayer, type EscapeHandler } from './escapeLayers';

installEscapeLayers();

const Layer: React.FC<{ open: boolean; onEscape: EscapeHandler; menu?: boolean; children?: React.ReactNode }> = ({ open, onEscape, menu, children }) => {
  useEscapeLayer(open, onEscape, { menu });
  return open ? <div>{children}</div> : null;
};

const pressEsc = (target: EventTarget = document.body) => {
  const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  act(() => { target.dispatchEvent(e); });
  return e;
};

afterEach(() => { document.body.innerHTML = ''; });

test('nothing open — Esc is left alone (the window may leave full screen as usual)', () => {
  expect(__escapeStackSize()).toBe(0);
  expect(pressEsc().defaultPrevented).toBe(false);
});

test('closes only the TOP window and swallows Esc', () => {
  const closeA = jest.fn();
  const closeB = jest.fn();
  const { rerender, unmount } = render(<Layer open onEscape={closeA}><Layer open={false} onEscape={closeB} /></Layer>);
  rerender(<Layer open onEscape={closeA}><Layer open onEscape={closeB} /></Layer>);
  const e = pressEsc();
  expect(e.defaultPrevented).toBe(true);
  expect(closeB).toHaveBeenCalledTimes(1);
  expect(closeA).not.toHaveBeenCalled();
  unmount();
  expect(__escapeStackSize()).toBe(0);
});

test('busy window (undefined) swallows Esc but stays; own-handler window is not closed by us', () => {
  const { rerender, unmount } = render(<Layer open onEscape={undefined} />);
  expect(pressEsc().defaultPrevented).toBe(true);
  rerender(<Layer open onEscape="own" />);
  expect(pressEsc().defaultPrevented).toBe(true);
  unmount();
});

test('a field inside the window that handled Esc itself (stopPropagation) keeps the window open', () => {
  const close = jest.fn();
  const cancelEdit = jest.fn();
  const { getByRole, unmount } = render(
    <Layer open onEscape={close}>
      <input onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); cancelEdit(); } }} />
    </Layer>,
  );
  const e = pressEsc(getByRole('textbox'));
  expect(cancelEdit).toHaveBeenCalledTimes(1);
  expect(close).not.toHaveBeenCalled();
  expect(e.defaultPrevented).toBe(true);
  unmount();
});

test('Esc in a plain field with nothing open is swallowed (does not shrink the app)', () => {
  const { getByRole, unmount } = render(<input />);
  expect(pressEsc(getByRole('textbox')).defaultPrevented).toBe(true);
  unmount();
});

test('windows of a hidden (keep-alive) tab are not in the stack', () => {
  const close = jest.fn();
  const { rerender, unmount } = render(<ActivePageProvider isActive={false}><Layer open onEscape={close} /></ActivePageProvider>);
  expect(__escapeStackSize()).toBe(0);
  pressEsc();
  expect(close).not.toHaveBeenCalled();
  rerender(<ActivePageProvider isActive><Layer open onEscape={close} /></ActivePageProvider>);
  pressEsc();
  expect(close).toHaveBeenCalledTimes(1);
  unmount();
});

test('returning to a tab keeps the order of opening (child above parent)', () => {
  const closeParent = jest.fn();
  const closeChild = jest.fn();
  const tree = (active: boolean, child: boolean) => (
    <ActivePageProvider isActive={active}>
      <Layer open onEscape={closeParent}><Layer open={child} onEscape={closeChild} /></Layer>
    </ActivePageProvider>
  );
  const { rerender, unmount } = render(tree(true, false));
  rerender(tree(true, true));
  rerender(tree(false, true));
  rerender(tree(true, true));   // діти реєструються раніше за батьків — порядок має лишитись
  pressEsc();
  expect(closeChild).toHaveBeenCalledTimes(1);
  expect(closeParent).not.toHaveBeenCalled();
  unmount();
});

test('a menu closes on Esc like a window, but does not block page hotkeys', () => {
  const closeMenu = jest.fn();
  const { rerender, unmount } = render(<Layer open menu onEscape={closeMenu} />);
  expect(modalOverlayOpen()).toBe(false);
  pressEsc();
  expect(closeMenu).toHaveBeenCalledTimes(1);
  rerender(<Layer open onEscape={closeMenu} />);
  expect(modalOverlayOpen()).toBe(true);
  unmount();
  expect(modalOverlayOpen()).toBe(false);
});
