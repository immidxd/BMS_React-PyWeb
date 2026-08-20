import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import SearchBar from './SearchBar';
import { searchService } from '../../services/searchService';

jest.mock('../../services/searchService', () => ({
  searchService: { globalSearch: jest.fn() },
}));

describe('SearchBar request pacing', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it('updates the input immediately but sends one list search after typing stops', () => {
    const onSearch = jest.fn();
    render(<SearchBar onSearch={onSearch} showGlobalResults={false} />);

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'a' } });
    fireEvent.change(input, { target: { value: 'ad' } });
    fireEvent.change(input, { target: { value: 'adidas' } });

    expect((input as HTMLInputElement).value).toBe('adidas');
    expect(onSearch).not.toHaveBeenCalled();

    act(() => { jest.advanceTimersByTime(249); });
    expect(onSearch).not.toHaveBeenCalled();
    act(() => { jest.advanceTimersByTime(1); });
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenLastCalledWith('adidas');

    fireEvent.click(screen.getByRole('button', { name: 'Очистити пошук' }));
    expect(onSearch).toHaveBeenLastCalledWith('');
  });

  it('aborts an obsolete preview request before starting the next one', () => {
    (searchService.globalSearch as jest.Mock).mockReturnValue(new Promise(() => undefined));
    render(
      <SearchBar
        onSearch={jest.fn()}
        showGlobalResults
        currentScope="products"
      />,
    );

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'adidas' } });
    act(() => { jest.advanceTimersByTime(300); });
    expect(searchService.globalSearch).toHaveBeenCalledTimes(1);
    const firstSignal = (searchService.globalSearch as jest.Mock).mock.calls[0][1].signal as AbortSignal;
    expect(firstSignal.aborted).toBe(false);

    fireEvent.change(input, { target: { value: 'nike' } });
    expect(firstSignal.aborted).toBe(true);
    act(() => { jest.advanceTimersByTime(300); });
    expect(searchService.globalSearch).toHaveBeenCalledTimes(2);
    expect((searchService.globalSearch as jest.Mock).mock.calls[1][0]).toBe('nike');
  });
});
