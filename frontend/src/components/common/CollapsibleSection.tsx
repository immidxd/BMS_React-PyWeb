import React, { useState } from 'react';
import styled from 'styled-components';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faChevronDown, faChevronUp } from '@fortawesome/free-solid-svg-icons';

const SectionContainer = styled.div`
  margin-bottom: 15px;
  border: 1px solid #ddd;
  border-radius: 4px;
  overflow: hidden;
`;

const SectionHeader = styled.div<{ isOpen: boolean }>`
  padding: 10px 15px;
  background-color: ${props => props.isOpen ? '#e9ecef' : '#f8f9fa'};
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  user-select: none;
  border-bottom: ${props => props.isOpen ? '1px solid #ddd' : 'none'};
  
  &:hover {
    background-color: #e9ecef;
  }
`;

const SectionTitle = styled.h3`
  margin: 0;
  font-size: 16px;
  font-weight: 600;
`;

const SectionContent = styled.div<{ isOpen: boolean }>`
  padding: ${props => props.isOpen ? '15px' : '0'};
  max-height: ${props => props.isOpen ? '1000px' : '0'};
  overflow: hidden;
  transition: all 0.3s ease-in-out;
`;

interface CollapsibleSectionProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
  title,
  children,
  defaultOpen = false
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  
  const toggleSection = () => {
    setIsOpen(!isOpen);
  };
  
  return (
    <SectionContainer>
      <SectionHeader isOpen={isOpen} onClick={toggleSection}>
        <SectionTitle>{title}</SectionTitle>
        <FontAwesomeIcon icon={isOpen ? faChevronUp : faChevronDown} />
      </SectionHeader>
      <SectionContent isOpen={isOpen}>
        {children}
      </SectionContent>
    </SectionContainer>
  );
};

export default CollapsibleSection; 