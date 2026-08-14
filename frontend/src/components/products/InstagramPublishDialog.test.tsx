import React from 'react';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import InstagramPublishDialog, { type InstagramPreview } from './InstagramPublishDialog';

jest.mock('../common/SmartImage', () => ({
  __esModule: true,
  default: ({ src, alt }: { src: string; alt?: string }) => <img src={src} alt={alt || 'photo'} />,
}));

const preview: InstagramPreview = {
  ok: true,
  mode: 'production',
  product_id: 42,
  productnumber: 'Ф42',
  brand: 'TEVA',
  model: 'ReFlip',
  type: 'Шльопанці',
  condition: 'Нові',
  condition_name: 'Новий',
  condition_confirmation_required: false,
  caption: 'Товарний Instagram-підпис\n\n📲 Пиши #Ф42 нам в приватні',
  caption_len: 58,
  caption_limit: 2200,
  story_text: 'TEVA ReFlip • чоловічі шльопанці\nРозмір: 42\nПиши #Ф42 нам в приватні',
  story_text_limit: 320,
  image_count: 2,
  image_kind: 'official',
  image_urls: ['/photo-1.jpeg', '/photo-2.jpeg'],
  image_names: ['photo-1.jpeg', 'photo-2.jpeg'],
  default_image_idx: [0, 1],
  carousel_limit: 10,
  batch_max_products: 10,
  default_feed_preset: 'square',
  feed_presets: {
    portrait: { label: 'Вертикальний 4:5', width: 1080, height: 1350 },
    square: { label: 'Квадрат 1:1', width: 1080, height: 1080 },
  },
  feed_zoom_defaults: { portrait: [0.9, 1], square: [0.9, 1] },
  feed_edge_adjusted: { portrait: [false, true], square: [false, true] },
  story_preset: { label: 'Stories / Reels 9:16', width: 1080, height: 1920 },
  publish_types: {
    feed: { label: 'Пост / карусель', max_media: 10 },
    story: { label: 'Story', max_media: 1 },
    reel: { label: 'Reel зі слайдів', max_media: 10 },
  },
  default_publish_at: new Date(Date.now() + 60 * 60_000).toISOString(),
  connection: {
    configured: true,
    mode: 'production',
    account: '@brandxstoreua',
    live_publish_available: true,
    schedule_available: true,
    oauth_connected: true,
    missing: [],
    note: 'Готово',
  },
  warnings: [],
};

describe('InstagramPublishDialog defaults', () => {
  beforeEach(() => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['jpeg'], { type: 'image/jpeg' }),
    }) as any;
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: jest.fn(() => 'blob:instagram-preview') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: jest.fn() });
  });

  it('applies 0.90 for feed, 0.60 for Story and restores feed photos', () => {
    render(<InstagramPublishDialog data={preview} onCancel={jest.fn()} onConfirm={jest.fn()} />);

    const zoom = () => screen.getAllByRole('slider')[0] as HTMLInputElement;
    expect(zoom().value).toBe('0.9');
    expect(screen.getByText('2/10')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Story' }));
    expect(zoom().value).toBe('0.6');
    expect(screen.getByText('1/1')).toBeInTheDocument();
    expect(screen.getByLabelText('Текст на зображенні Story')).toHaveValue(preview.story_text);
    expect(screen.getByLabelText('Текст на зображенні Story')).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Пост / карусель' }));
    expect(zoom().value).toBe('0.9');
    expect(screen.getByText('2/10')).toBeInTheDocument();
  });

  it('disables shrinking for a photo that touches the frame', () => {
    render(<InstagramPublishDialog data={preview} onCancel={jest.fn()} onConfirm={jest.fn()} />);

    fireEvent.click(screen.getAllByTitle('Налаштувати кадр')[1]);

    expect((screen.getAllByRole('slider')[0] as HTMLInputElement).value).toBe('1');
    expect(screen.getByText(/Автоматичне зменшення вимкнено/)).toBeInTheDocument();
  });

  it('reveals an editable date after selecting the schedule in desktop-compatible input flow', async () => {
    render(<InstagramPublishDialog data={preview} onCancel={jest.fn()} onConfirm={jest.fn()} />);

    const timing = screen.getByLabelText('Коли публікувати');
    fireEvent.input(timing, { target: { value: 'scheduled' } });

    await waitFor(() => expect(screen.getByLabelText('Дата й час')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Запланувати/ })).toBeInTheDocument();
  });
});
