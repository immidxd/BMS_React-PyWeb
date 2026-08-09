import React, { useCallback, useEffect, useRef, useState } from 'react';
import { thumbUrl, type ThumbWidth } from '../../services/imageUrls';

// ── Фото товару з ГАРАНТОВАНИМ візуальним станом ─────────────────────────────
//
// Проблема, яку це закриває: звичайний <img> між «URL відомий» і «пікселі
// намальовані» показує ПОРОЖНЄ місце. Для фото на 100–800 КБ це помітна пауза,
// у якій незрозуміло — воно вантажиться, його немає, чи все зламалось. У роботі
// на швидкість це найгірше: не видно, чого чекати й чи взагалі варто.
//
// SmartImage завжди показує один із чотирьох станів, без «невідомості»:
//   1. skeleton (мерехтіння)          — поки нічого не завантажено;
//   2. мініатюра (розмита)            — вже є, поки їде повний кадр;
//   3. повний кадр                    — плавна поява;
//   4. помилка + кнопка «Повторити»   — якщо файл не віддався.
//
// `thumb` вмикає прогресивне завантаження: спершу тягнемо мініатюру (одиниці
// КБ, у кеші бекенда), показуємо її розмитою, а повний кадр підміняє її, коли
// доїде. Око бачить фото майже одразу.

type Props = {
  src: string;
  alt?: string;
  className?: string;
  /** Ширина мініатюри-плейсхолдера. Не задано — грузимо одразу оригінал. */
  thumb?: ThumbWidth;
  /** Показувати ЛИШЕ мініатюру (для плиток/стрічки — оригінал там не потрібен). */
  thumbOnly?: boolean;
  /** Спінер поверх скелета — для великих кадрів, де сама пауза відчутна. */
  spinner?: boolean;
  loading?: 'lazy' | 'eager';
  draggable?: boolean;
  style?: React.CSSProperties;
  onClick?: (e: React.MouseEvent<HTMLElement>) => void;
  /** Підпис під іконкою помилки (напр. ім'я файлу). */
  title?: string;
};

const SmartImage: React.FC<Props> = ({
  src, alt = '', className = '', thumb, thumbOnly = false, spinner = false,
  loading = 'lazy', draggable, style, onClick, title,
}) => {
  const fullSrc = thumbOnly && thumb ? thumbUrl(src, thumb) : src;
  const thumbSrc = thumb && !thumbOnly ? thumbUrl(src, thumb) : null;

  const [fullLoaded, setFullLoaded] = useState(false);
  const [thumbLoaded, setThumbLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  // Змінюється при «Повторити» → додає ?retry=N і обходить кеш невдалої відповіді.
  const [attempt, setAttempt] = useState(0);
  const imgRef = useRef<HTMLImageElement>(null);

  // Зміна src (гортання галереї/картки) = новий кадр: скидаємо стани, інакше
  // наступне фото показалось би «вже завантаженим» і блимнуло б порожнечею.
  useEffect(() => {
    setFullLoaded(false);
    setThumbLoaded(false);
    setFailed(false);
    setAttempt(0);
  }, [src, thumbOnly]);

  // Кадр міг бути вже в кеші браузера — тоді onLoad не спрацює (картинка
  // «complete» ще до підписки). Перевіряємо явно після монтування.
  useEffect(() => {
    const el = imgRef.current;
    if (el?.complete && el.naturalWidth > 0) setFullLoaded(true);
  }, [fullSrc, attempt]);

  const retry = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setFailed(false);
    setFullLoaded(false);
    setThumbLoaded(false);
    setAttempt((n) => n + 1);
  }, []);

  const bust = (u: string) => (attempt ? `${u}${u.includes('?') ? '&' : '?'}retry=${attempt}` : u);

  if (failed) {
    return (
      <div
        className={`flex flex-col items-center justify-center gap-1 bg-gray-50 dark:bg-gray-900 text-gray-400 dark:text-gray-500 ${className}`}
        style={style}
        title={title}
      >
        <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round"
            d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
        </svg>
        <button type="button" onClick={retry}
          className="text-[10px] underline hover:text-gray-600 dark:hover:text-gray-300">
          Повторити
        </button>
      </div>
    );
  }

  return (
    <div className={`relative overflow-hidden ${className}`} style={style} onClick={onClick} title={title}>
      {/* 1. Скелет — видно доти, доки не з'явився хоч якийсь кадр */}
      {!fullLoaded && !thumbLoaded && (
        <div className="absolute inset-0 bms-img-skeleton" aria-hidden="true" />
      )}
      {spinner && !fullLoaded && !thumbLoaded && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="w-6 h-6 border-2 border-gray-300 dark:border-gray-600 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* 2. Мініатюра — легка, приїжджає майже миттєво; тримаємо розмитою,
             доки не намальовано повний кадр (класичний progressive-ефект) */}
      {thumbSrc && !fullLoaded && (
        <img
          src={bust(thumbSrc)}
          alt=""
          aria-hidden="true"
          draggable={false}
          className="absolute inset-0 w-full h-full object-cover scale-105 blur-[6px]"
          onLoad={() => setThumbLoaded(true)}
          // Мініатюра не віддалась — не помилка кадру: повний ще їде.
          onError={() => setThumbLoaded(false)}
        />
      )}

      {/* 3. Повний кадр */}
      <img
        ref={imgRef}
        src={bust(fullSrc)}
        alt={alt}
        loading={loading}
        decoding="async"
        draggable={draggable}
        className={`relative w-full h-full object-cover transition-opacity duration-200 ${fullLoaded ? 'opacity-100' : 'opacity-0'}`}
        onLoad={() => setFullLoaded(true)}
        onError={() => setFailed(true)}
      />
    </div>
  );
};

export default SmartImage;
