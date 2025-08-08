export interface Theme {
  background: string;
  text: string;
  headerBackground: string;
  headerText: string;
  contentBackground: string;
  sidebarBackground: string;
  borderColor: string;
  primary: string;
  primaryHover: string;
  secondary: string;
  buttonText: string;
  inputBackground: string;
  inputBorder: string;
  hoverBackground: string;
}

export const lightTheme: Theme = {
  background: '#f5f5f5',
  text: '#333333',
  headerBackground: '#ffffff',
  headerText: '#333333',
  contentBackground: '#ffffff',
  sidebarBackground: '#f9f9f9',
  borderColor: '#e0e0e0',
  primary: '#4a6da7',
  primaryHover: '#3a5d97',
  secondary: '#6c757d',
  buttonText: '#ffffff',
  inputBackground: '#ffffff',
  inputBorder: '#ced4da',
  hoverBackground: '#eaeaea'
};

export const darkTheme: Theme = {
  background: '#2d2d2d',
  text: '#e4e4e4',
  headerBackground: '#1e1e1e',
  headerText: '#ffffff',
  contentBackground: '#2d2d2d',
  sidebarBackground: '#252525',
  borderColor: '#444444',
  primary: '#5a7ebf',
  primaryHover: '#4a6eaf',
  secondary: '#6c757d',
  buttonText: '#ffffff',
  inputBackground: '#3a3a3a',
  inputBorder: '#555555',
  hoverBackground: '#3a3a3a'
}; 