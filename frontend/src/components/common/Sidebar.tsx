import React from 'react';
import { Link } from 'react-router-dom';
import styled from 'styled-components';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCubes, faShoppingCart, faUsers, faCog } from '@fortawesome/free-solid-svg-icons';
import { useTheme } from '../../contexts/ThemeContext';

interface SidebarProps {
  activePath: string;
}

const SidebarContainer = styled.aside<{ isDarkTheme: boolean }>`
  width: 250px;
  background-color: ${props => props.isDarkTheme ? '#222' : '#f0f0f0'};
  color: ${props => props.isDarkTheme ? '#f0f0f0' : '#222'};
  padding: 1rem 0;
  height: 100%;
  overflow-y: auto;
  transition: width 0.3s;
`;

const SidebarMenu = styled.ul`
  list-style: none;
  padding: 0;
  margin: 0;
`;

const SidebarMenuItem = styled.li<{ active: boolean; isDarkTheme: boolean }>`
  padding: 0;
  margin: 0;
  
  a {
    display: flex;
    align-items: center;
    padding: 1rem 1.5rem;
    text-decoration: none;
    color: inherit;
    transition: background-color 0.2s;
    background-color: ${props => props.active 
      ? (props.isDarkTheme ? '#333' : '#e0e0e0') 
      : 'transparent'
    };
    border-left: ${props => props.active 
      ? `4px solid var(--light-accent-color)` 
      : `4px solid transparent`
    };
    font-weight: ${props => props.active ? 'bold' : 'normal'};
    
    &:hover {
      background-color: ${props => props.isDarkTheme ? '#333' : '#e0e0e0'};
    }
    
    svg {
      margin-right: 10px;
      width: 20px;
    }
  }
`;

const SidebarFooter = styled.div`
  padding: 1rem 1.5rem;
  font-size: 0.8rem;
  text-align: center;
  margin-top: 2rem;
  opacity: 0.7;
`;

const Sidebar: React.FC<SidebarProps> = ({ activePath }) => {
  const { darkTheme } = useTheme();
  
  return (
    <SidebarContainer isDarkTheme={darkTheme}>
      <SidebarMenu>
        <SidebarMenuItem active={activePath === '/products'} isDarkTheme={darkTheme}>
          <Link to="/products">
            <FontAwesomeIcon icon={faCubes} />
            Products
          </Link>
        </SidebarMenuItem>
        <SidebarMenuItem active={activePath === '/orders'} isDarkTheme={darkTheme}>
          <Link to="/orders">
            <FontAwesomeIcon icon={faShoppingCart} />
            Orders
          </Link>
        </SidebarMenuItem>
        <SidebarMenuItem active={activePath === '/clients'} isDarkTheme={darkTheme}>
          <Link to="/clients">
            <FontAwesomeIcon icon={faUsers} />
            Clients
          </Link>
        </SidebarMenuItem>
        <SidebarMenuItem active={activePath === '/settings'} isDarkTheme={darkTheme}>
          <Link to="/settings">
            <FontAwesomeIcon icon={faCog} />
            Settings
          </Link>
        </SidebarMenuItem>
      </SidebarMenu>
      <SidebarFooter>
        Version 1.0.0
      </SidebarFooter>
    </SidebarContainer>
  );
};

export default Sidebar; 