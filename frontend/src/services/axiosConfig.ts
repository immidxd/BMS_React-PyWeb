import axios from 'axios';

// Визначаємо базовий URL для API
// У розробці використовуємо localhost:8000, у production - поточний origin
const baseURL = process.env.NODE_ENV === 'development' 
  ? 'http://localhost:8000' 
  : window.location.origin;

// Налаштування базового URL для всіх запитів
axios.defaults.baseURL = baseURL;
axios.defaults.withCredentials = true; // Дозволяємо передачу credentials

// Додаємо обробник помилок для API
export const API_ERROR_EVENT = 'api-connection-error';

// Функція для емітування події про помилку з'єднання
export const emitConnectionError = () => {
  const event = new CustomEvent(API_ERROR_EVENT, { 
    detail: { message: 'Помилка з\'єднання з сервером: використовуються тестові дані.' } 
  });
  window.dispatchEvent(event);
};

// Додавання інтерсепторів для відстеження та обробки помилок
axios.interceptors.request.use(
  config => {
    console.log(`[API Request] ${config.method?.toUpperCase()} ${config.url}`);
    
    // Логуємо тільки в режимі розробки
    if (process.env.NODE_ENV === 'development') {
      console.log('Request Config:', config);
    }
    return config;
  },
  error => {
    console.error('[API Request Error]', error);
    return Promise.reject(error);
  }
);

axios.interceptors.response.use(
  response => {
    console.log(`[API Response] ${response.status} from ${response.config.url}`);
    
    // Логуємо тільки в режимі розробки
    if (process.env.NODE_ENV === 'development') {
      console.log('Response Data:', response.data);
    }
    return response;
  },
  error => {
    // Якщо бекенд недоступний - показуємо спеціальне повідомлення
    if (!error.response) {
      console.error('[API Network Error] Не вдалося з\'єднатися з сервером');
      emitConnectionError();
    } else {
      console.error('[API Response Error]', error.response.status, error.response.data);
    }
    return Promise.reject(error);
  }
);

export default axios; 