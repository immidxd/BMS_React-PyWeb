import React, { useRef, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import TextFormattingToolbar from './TextFormattingToolbar';


const Editor: React.FC<{ dialect: 'telegram' | 'viber' }> = ({ dialect }) => {
  const [value, setValue] = useState('EVA');
  const ref = useRef<HTMLTextAreaElement>(null);
  return (
    <>
      <TextFormattingToolbar dialect={dialect} targetRef={ref} value={value} onChange={setValue} />
      <textarea ref={ref} aria-label="Підпис" value={value} onChange={event => setValue(event.target.value)} />
    </>
  );
};

const selectAll = () => {
  const input = screen.getByLabelText('Підпис') as HTMLTextAreaElement;
  input.focus();
  input.setSelectionRange(0, input.value.length);
  return input;
};

describe('TextFormattingToolbar', () => {
  it.each([
    ['Жирний', '*EVA*'],
    ['Курсив', '_EVA_'],
    ['Моноширинний', '```EVA```'],
    ['Закреслений', '~EVA~'],
  ])('uses official Viber markup for %s', (button, expected) => {
    render(<Editor dialect="viber" />);
    const input = selectAll();
    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(input.value).toBe(expected);
  });

  it.each([
    ['Жирний', '**EVA**'],
    ['Курсив', '__EVA__'],
    ['Моноширинний', '`EVA`'],
    ['Закреслений', '~~EVA~~'],
  ])('uses Telegram markup for %s', (button, expected) => {
    render(<Editor dialect="telegram" />);
    const input = selectAll();
    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(input.value).toBe(expected);
  });
});
