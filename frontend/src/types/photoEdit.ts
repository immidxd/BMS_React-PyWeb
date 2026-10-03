/** Кадр фото товару: поворот за годинниковою (до обрізки) + рамка в частках
 *  повернутого знімка. Пікселі ріже бекенд з оригіналу — див.
 *  backend/services/photo_edit.py. */
export interface PhotoCrop { x: number; y: number; w: number; h: number; }
export interface PhotoEdit { rotate: 0 | 90 | 180 | 270; crop: PhotoCrop | null; }
