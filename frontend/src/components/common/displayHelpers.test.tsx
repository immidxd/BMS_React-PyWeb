import { getProductDisplayStatus } from './displayHelpers';

describe('getProductDisplayStatus', () => {
  it('shows a live paid sale even when the journal snapshot is still unsold', () => {
    expect(getProductDisplayStatus({
      sold_count: 1,
      quantity: 1,
      order_count: 1,
      status_name: 'Непродано',
    })).toEqual({ text: 'Продано', color: 'red' });
  });

  it('keeps an unsold product unsold without a completed sale', () => {
    expect(getProductDisplayStatus({
      sold_count: 0,
      quantity: 1,
      order_count: 1,
      status_name: 'Непродано',
    })).toEqual({ text: 'Непродано', color: 'green' });
  });

  it.each([
    ['Повернуто', 'orange'],
    ['Пошкоджений', 'volcano'],
  ])('preserves the manual journal state %s', (status_name, color) => {
    expect(getProductDisplayStatus({
      sold_count: 0,
      quantity: 1,
      order_count: 0,
      status_name,
    })).toEqual({ text: status_name, color });
  });

  it('does not trust a stale sold snapshot contradicted by live orders', () => {
    expect(getProductDisplayStatus({
      sold_count: 0,
      quantity: 1,
      order_count: 1,
      status_name: 'Продано',
    })).toEqual({ text: 'Непродано', color: 'green' });
  });
});
