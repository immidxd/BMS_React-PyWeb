import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ViberBatchPublishDialog from './ViberBatchPublishDialog';
import { taskManager } from '../../services/taskManager';


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

const productPreview = {
  ok: true,
  product_id: 42,
  productnumber: 'Ф42',
  brand: 'Brand',
  model: 'Model',
  type: 'Кросівки',
  condition: 'Нові',
  condition_name: 'Новий',
  condition_confirmation_required: false,
  caption: '*Brand Model*',
  caption_len: 13,
  caption_limit: 768,
  sizes: [],
  image_count: 1,
  image_kind: 'official',
  image_urls: ['/product-images/Ф42_01.webp'],
  image_names: ['Ф42_01.webp'],
  default_image_idx: [0],
  collage: {
    version: 1, width: 1080, height: 1080, image_idx: [0], layout: 'auto',
    background: 'white', gap: 4, column_split: 0.63, left_split: 0.505,
    right_top: 0.347, right_middle: 0.307,
    frames: [{ image_idx: 0, zoom: 1, x: 0, y: 0 }],
  },
  layouts: [{ key: 'auto', label: 'Розумний' }],
  backgrounds: [{ key: 'white', label: 'Білий' }],
  channel: { title: 'Brandstoreua' },
  connection: {
    configured: false,
    live_publish_available: false,
    schedule_available: false,
    missing: ['VIBER_DISPATCHER_URL', 'VIBER_DISPATCHER_KEY'],
    collage: { width: 1080, height: 1080, max_bytes: 950000, max_photos: 5 },
  },
  already_published: 0,
  pending_publications: 0,
  batch_max_products: 10,
  default_publish_at: new Date(Date.now() + 60 * 60_000).toISOString(),
  warnings: [],
};

describe('ViberBatchPublishDialog dry-run', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (taskManager.run as jest.Mock).mockImplementation(
      (_label: string, fn: () => Promise<any>) => fn(),
    );
    global.fetch = jest.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          selected_count: 1, unique_count: 1, merged_count: 0, batch_max_products: 10,
          items: [{ product_id: 42, productnumber: 'Ф42', source_product_ids: [42], ok: true, preview: productPreview }],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true, dry_run: true, counts: { success: 1, error: 0, total: 1 } }),
      }) as any;
  });

  it('keeps live publishing disabled but fully validates the package without sending', async () => {
    const onPublish = jest.fn();
    render(<ViberBatchPublishDialog productIds={[42]} busy={false} onCancel={jest.fn()} onPublish={onPublish} />);

    const check = await screen.findByRole('button', { name: 'Перевірити 1 картку без надсилання' });
    const publish = screen.getByRole('button', { name: /Опублікувати 1/ }) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    expect((check as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(check);

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
    const request = JSON.parse((global.fetch as jest.Mock).mock.calls[1][1].body);
    expect(request.dry_run).toBe(true);
    expect(request.items).toHaveLength(1);
    expect(onPublish).not.toHaveBeenCalled();
    await screen.findByText(/Перевірено 1 картку/);
  });
});
