// This file can be used for global CSS resets or base styles if not using a CSS file directly.
// For TailwindCSS, this might not be strictly necessary if you import Tailwind's base styles in your main CSS file (e.g., index.css).
// However, if you were using styled-components or similar, you'd define global styles here.

// Example for styled-components (not currently used based on App.tsx structure):
/*
import { createGlobalStyle } from 'styled-components';

const GlobalStyle = createGlobalStyle`
  body {
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen',
      'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue',
      sans-serif;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    background-color: var(--background-color);
    color: var(--text-color);
    transition: background-color 0.3s ease, color 0.3s ease;
  }

  code {
    font-family: source-code-pro, Menlo, Monaco, Consolas, 'Courier New',
      monospace;
  }

  :root {
    --background-color: #fff;
    --text-color: #000;
  }

  [data-theme='dark'] {
    --background-color: #333;
    --text-color: #fff;
  }
`;

export default GlobalStyle;
*/

// If you are primarily using TailwindCSS, ensure your tailwind base, components, and utilities
// are imported in your main CSS file (e.g., frontend/src/index.css).
// This file can remain empty or be removed if not needed for other global styling approaches.

const GlobalStyle = () => null; // Placeholder if no specific global styles are needed here

export default GlobalStyle; 