import { getProductDisplayStatus, visibleGalleryPhotos } from './displayHelpers';

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

// ── Штатний перегляд не показує приховане ───────────────────────────────────
//
// У картці ДВІ галереї зі спільного списку: верхня показує товар як його
// побачить покупець, нижня (менеджер фото) дозволяє приховане повернути. Коли
// приховування додавали, фільтр спершу поставили в бекенді на всіх одразу — і
// сховане зникло навіть із менеджера; потім, навпаки, картці віддали все — і
// сховане знову зʼявилось у штатному перегляді. Різниця між цими двома
// галереями і є те, що тут закріплено.
describe('visibleGalleryPhotos', () => {
    const p = (kind: string, hidden = false) => ({ kind, hidden, id: `${kind}${hidden}` });

    it('приховане не потрапляє у перегляд', () => {
        const all = [p('official'), { ...p('official', true), id: 'x' }];
        expect(visibleGalleryPhotos(all, 'official', false)).toHaveLength(1);
    });

    it('приховане не потрапляє і в набір «Реальні»', () => {
        const all = [p('real'), { ...p('real', true), id: 'y' }];
        expect(visibleGalleryPhotos(all, 'real', false)).toHaveLength(1);
    });

    it('приховане не потрапляє навіть коли увімкнено дефекти', () => {
        const all = [{ ...p('defect', true), id: 'd' }];
        expect(visibleGalleryPhotos(all, 'official', true)).toHaveLength(0);
    });

    it('видиме фільтрується за набором, як і раніше', () => {
        const all = [p('official'), p('real')];
        expect(visibleGalleryPhotos(all, 'official', false).map((i) => i.kind)).toEqual(['official']);
        expect(visibleGalleryPhotos(all, 'real', false).map((i) => i.kind)).toEqual(['real']);
    });

    it('дефекти показуються лише як оверлей або у власному наборі', () => {
        const all = [p('defect')];
        expect(visibleGalleryPhotos(all, 'official', false)).toHaveLength(0);
        expect(visibleGalleryPhotos(all, 'official', true)).toHaveLength(1);
        expect(visibleGalleryPhotos(all, 'defect', false)).toHaveLength(1);
    });

    it('відсутній kind вважається офіційним', () => {
        expect(visibleGalleryPhotos([{ hidden: false }], 'official', false)).toHaveLength(1);
    });
});
