import React, { useState } from 'react';
import { Layout, Menu, theme, Typography, Button } from 'antd';
import {
  MenuUnfoldOutlined,
  MenuFoldOutlined,
  HomeOutlined,
  ShoppingOutlined,
  UserOutlined,
  FileOutlined,
  SettingOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { Link, useLocation } from 'react-router-dom';
import styled from 'styled-components';
import { ParsingDialog } from '../ParsingDialog';
import { ParsingStatus } from '../ParsingStatus';

const { Header, Sider, Content, Footer } = Layout;
const { Title } = Typography;

// Стилізовані компоненти
const Logo = styled.div`
  height: 32px;
  margin: 16px;
  background: rgba(255, 255, 255, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: bold;
`;

const StyledLayout = styled(Layout)`
  min-height: 100vh;
`;

const StyledHeader = styled(Header)`
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
`;

const StyledContent = styled(Content)`
  margin: 24px 16px;
  padding: 24px;
  background: #fff;
  min-height: 280px;
  overflow: auto;
`;

const StyledFooter = styled(Footer)`
  text-align: center;
`;

interface MainLayoutProps {
  children: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const [parsingDialogOpen, setParsingDialogOpen] = useState(false);
  const location = useLocation();
  const {
    token: { colorBgContainer, borderRadiusLG },
  } = theme.useToken();

  const handleStartParsing = async (mode: string, params: any) => {
    try {
      const response = await fetch('/api/parsing/parsing/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ mode, params }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to start parsing');
      }
    } catch (error) {
      console.error('Error starting parsing:', error);
    }
  };

  // Визначаємо ключі меню на основі поточного шляху
  const getSelectedKeys = () => {
    const path = location.pathname;
    if (path === '/' || path.startsWith('/products')) return ['products'];
    if (path.startsWith('/clients')) return ['clients'];
    if (path.startsWith('/orders')) return ['orders'];
    if (path.startsWith('/reports')) return ['reports'];
    if (path.startsWith('/settings')) return ['settings'];
    return [];
  };

  return (
    <StyledLayout>
      <Sider trigger={null} collapsible collapsed={collapsed} width={250}>
        <Logo>BS System</Logo>
        <Menu
          theme="dark"
          mode="inline"
          defaultSelectedKeys={['products']}
          selectedKeys={getSelectedKeys()}
          items={[
            {
              key: 'home',
              icon: <HomeOutlined />,
              label: <Link to="/">Головна</Link>,
            },
            {
              key: 'products',
              icon: <ShoppingOutlined />,
              label: <Link to="/products">Товари</Link>,
            },
            {
              key: 'clients',
              icon: <UserOutlined />,
              label: <Link to="/clients">Клієнти</Link>,
            },
            {
              key: 'orders',
              icon: <FileOutlined />,
              label: <Link to="/orders">Замовлення</Link>,
            },
            {
              key: 'settings',
              icon: <SettingOutlined />,
              label: <Link to="/settings">Налаштування</Link>,
            },
          ]}
        />
      </Sider>
      <Layout>
        <StyledHeader style={{ background: colorBgContainer }}>
          {React.createElement(
            collapsed ? MenuUnfoldOutlined : MenuFoldOutlined,
            {
              className: 'trigger',
              onClick: () => setCollapsed(!collapsed),
            }
          )}
          <Title level={4} style={{ margin: 0 }}>Система управління товарами та замовленнями</Title>
          <Button 
            type="primary" 
            icon={<ReloadOutlined />} 
            onClick={() => setParsingDialogOpen(true)}
            style={{ marginLeft: 'auto' }}
          >
            Оновити дані
          </Button>
        </StyledHeader>
        <StyledContent
          style={{
            background: colorBgContainer,
            borderRadius: borderRadiusLG,
          }}
        >
          {children}
        </StyledContent>
        <StyledFooter>BS System © {new Date().getFullYear()} Всі права захищено</StyledFooter>
      </Layout>

      {/* Діалог парсингу */}
      <ParsingDialog
        open={parsingDialogOpen}
        onClose={() => setParsingDialogOpen(false)}
        onStartParsing={handleStartParsing}
      />

      {/* Статус парсингу */}
      <ParsingStatus />
    </StyledLayout>
  );
};

export default MainLayout; 