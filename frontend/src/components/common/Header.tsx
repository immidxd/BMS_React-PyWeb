import React from 'react';
import styled from 'styled-components';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMoon, faSun, faSync } from '@fortawesome/free-solid-svg-icons';
import { useTheme } from '../../contexts/ThemeContext';

interface HeaderProps {
  onOpenParsingDialog: () => void;
}

const HeaderContainer = styled.header<{ isDarkTheme: boolean }>`
  background-color: ${props => props.isDarkTheme ? 'var(--dark-bg-color)' : 'white'};
  color: ${props => props.isDarkTheme ? 'var(--dark-text-color)' : 'var(--light-text-color)'};
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  padding: 1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 100;
`;

const Logo = styled.div`
  font-size: 1.5rem;
  font-weight: bold;
  display: flex;
  align-items: center;
  gap: 0.5rem;
`;

const Controls = styled.div`
  display: flex;
  align-items: center;
  gap: 1rem;
`;

const ThemeToggle = styled.button`
  background-color: transparent;
  border: none;
  cursor: pointer;
  font-size: 1.2rem;
  color: ${props => props.theme.isDarkTheme ? 'var(--dark-text-color)' : 'var(--light-text-color)'};
`;

const ParsingButton = styled.button`
  background-color: var(--light-accent-color);
  color: white;
  border: none;
  border-radius: 4px;
  padding: 0.5rem 1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  transition: background-color 0.2s;

  &:hover {
    background-color: var(--dark-accent-color);
  }
`;

const Header: React.FC<HeaderProps> = ({ onOpenParsingDialog }) => {
  const { darkTheme, toggleTheme } = useTheme();

  return (
    <HeaderContainer isDarkTheme={darkTheme}>
      <Logo>
        Product & Order Management System
      </Logo>
      <Controls>
        <ParsingButton onClick={onOpenParsingDialog}>
          <FontAwesomeIcon icon={faSync} />
          Parse Data
        </ParsingButton>
        <ThemeToggle onClick={toggleTheme} theme={{ isDarkTheme: darkTheme }}>
          <FontAwesomeIcon icon={darkTheme ? faSun : faMoon} />
        </ThemeToggle>
      </Controls>
    </HeaderContainer>
  );
};

export default Header; 