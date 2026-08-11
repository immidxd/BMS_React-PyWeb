import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ViberPublishDialog, { type ViberPreview } from './ViberPublishDialog';
import { productService } from '../../services/productService';
import { emitProductPhotosChanged, taskManager } from '../../services/taskManager';

jest.mock('../common/SmartImage', () => ({
  __esModule: true,
  default: ({ src }: { src: string }) => <img src={src} alt="photo" />,
}));

jest.mock('../../services/productService', () => ({
  productService: { transformProductPhoto: jest.fn() },
}));

jest.mock('../../services/taskManager', () => ({
  taskManager: { run: jest.fn((_label: string, fn: () => Promise<any>) => fn()) },
  emitProductPhotosChanged: jest.fn(),
}));

const preview: ViberPreview = {
  ok: true,
  product_id: 42,
  productnumber: 'Ф42',
  brand: 'Brand',
  model: 'Model',
  type: 'Кросівки',
  condition: 'Нові',
  condition_name: 'Новий',
  condition_confirmation_required: false,
  caption: 'Тестовий підпис',
  caption_len: 15,
  caption_limit: 768,
  sizes: [],
  image_count: 1,
  image_kind: 'official',
  image_urls: ['/product-images/Ф42_01.webp?v=old'],
  image_names: ['Ф42_01.webp'],
  default_image_idx: [0],
  collage: {
    version: 1,
    width: 1080,
    height: 1080,
    image_idx: [0],
    layout: 'auto',
    background: 'white',
    gap: 4,
    column_split: 0.63,
    left_split: 0.505,
    right_top: 0.347,
    right_middle: 0.307,
    frames: [{ image_idx: 0, zoom: 1, x: 0, y: 0 }],
  },
  layouts: [
    { key: 'auto', label: 'Розумний' },
    { key: 'hero', label: 'Головне фото' },
    { key: 'grid', label: 'Рівна сітка' },
  ],
  backgrounds: [{ key: 'white', label: 'Білий' }],
  channel: { title: 'Brandstoreua' },
  connection: {
    configured: false,
    live_publish_available: false,
    schedule_available: false,
    missing: [],
    collage: { width: 1080, height: 1080, max_bytes: 1_000_000, max_photos: 5 },
  },
  already_published: 0,
  pending_publications: 0,
  batch_max_products: 10,
  default_publish_at: new Date(Date.now() + 60 * 60_000).toISOString(),
  warnings: [],
};

describe('ViberPublishDialog photo editing', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (taskManager.run as jest.Mock).mockImplementation(
      (_label: string, fn: () => Promise<any>) => fn(),
    );
    (productService.transformProductPhoto as jest.Mock).mockResolvedValue({
      transformed: 'Ф42_01.webp',
      operation: 'flip_horizontal',
      width: 100,
      height: 100,
      version: 'fresh-version',
    });
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => '12000' },
      blob: async () => new Blob(['jpeg'], { type: 'image/jpeg' }),
    }) as any;
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: jest.fn(() => 'blob:viber-preview') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: jest.fn() });
  });

  it('mirrors the canonical image, refreshes the collage and updates its batch preview', async () => {
    const onPreviewChange = jest.fn();
    render(
      <ViberPublishDialog
        data={preview}
        busy={false}
        mode="draft"
        onCancel={jest.fn()}
        onConfirm={jest.fn()}
        onPreviewChange={onPreviewChange}
      />,
    );

    fireEvent.click(screen.getByLabelText('Віддзеркалити фото 1'));

    expect(taskManager.run).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(productService.transformProductPhoto).toHaveBeenCalledWith(
      42, 'Ф42_01.webp', 'flip_horizontal',
    ));
    await waitFor(() => expect(
      screen.getAllByAltText('photo').some(image => (image as HTMLImageElement).src.includes('v=fresh-version')),
    ).toBe(true));
    expect(onPreviewChange.mock.calls[0][0].image_urls[0]).toContain('v=fresh-version');
    expect(emitProductPhotosChanged).toHaveBeenCalledWith(42);
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
      '/api/publications/viber/render-collage',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('lets all selected frames shrink below the neutral 100% scale', () => {
    const onConfirm = jest.fn();
    const twoPhotos: ViberPreview = {
      ...preview,
      image_count: 2,
      image_urls: ['/product-images/Ф42_01.webp', '/product-images/Ф42_02.webp'],
      image_names: ['Ф42_01.webp', 'Ф42_02.webp'],
      default_image_idx: [0, 1],
      collage: {
        ...preview.collage,
        image_idx: [0, 1],
        frames: [
          { image_idx: 0, zoom: 1, x: 0, y: 0 },
          { image_idx: 1, zoom: 1, x: 0, y: 0 },
        ],
      },
    };
    render(
      <ViberPublishDialog
        data={twoPhotos}
        busy={false}
        mode="draft"
        onCancel={jest.fn()}
        onConfirm={onConfirm}
      />,
    );

    const singleZoom = screen.getByLabelText('Масштаб фото') as HTMLInputElement;
    expect(singleZoom.min).toBe('0.5');
    expect(singleZoom.value).toBe('1');

    fireEvent.click(screen.getByRole('button', { name: 'Усі 2' }));
    const groupZoom = screen.getByLabelText('Масштаб вибраних фото') as HTMLInputElement;
    expect(groupZoom.min).toBe('-0.5');
    expect(groupZoom.value).toBe('0');
    fireEvent.change(groupZoom, { target: { value: '-0.25' } });
    fireEvent.click(screen.getByRole('button', { name: /Зберегти картку/ }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0].collage.frames.map((frame: any) => frame.zoom)).toEqual([0.75, 0.75]);
    expect(screen.queryByText('×')).toBeNull();
  });
});
