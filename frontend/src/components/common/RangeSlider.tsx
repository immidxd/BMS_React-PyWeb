import React, { useState, useEffect } from 'react';
import styled from 'styled-components';

const SliderContainer = styled.div`
  width: 100%;
  padding: 10px 0;
`;

const SliderTrack = styled.div`
  width: 100%;
  height: 5px;
  background-color: #e0e0e0;
  border-radius: 3px;
  position: relative;
  margin: 15px 0;
`;

const SliderRange = styled.div<{ start: number; end: number }>`
  position: absolute;
  height: 100%;
  background-color: #007bff;
  border-radius: 3px;
  left: ${props => props.start}%;
  width: ${props => props.end - props.start}%;
`;

const SliderThumb = styled.div<{ position: number; active?: boolean }>`
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background-color: #007bff;
  position: absolute;
  top: 50%;
  left: ${props => props.position}%;
  transform: translate(-50%, -50%);
  cursor: pointer;
  box-shadow: ${props => props.active ? '0 0 0 5px rgba(0, 123, 255, 0.2)' : 'none'};
  transition: box-shadow 0.2s;
  
  &:hover {
    box-shadow: 0 0 0 5px rgba(0, 123, 255, 0.2);
  }
`;

const ValueDisplay = styled.div`
  display: flex;
  justify-content: space-between;
  margin-bottom: 5px;
  font-size: 14px;
`;

const InputsContainer = styled.div`
  display: flex;
  justify-content: space-between;
  gap: 10px;
`;

const Input = styled.input`
  width: 80px;
  padding: 5px;
  border: 1px solid #ddd;
  border-radius: 4px;
`;

interface RangeSliderProps {
  min: number;
  max: number;
  step?: number;
  defaultMin?: number;
  defaultMax?: number;
  onChange: (min: number, max: number) => void;
  formatValue?: (value: number) => string;
  formatLabel?: (value: number) => string;
  value?: [number, number];
}

const RangeSlider: React.FC<RangeSliderProps> = ({
  min,
  max,
  step = 1,
  defaultMin,
  defaultMax,
  onChange,
  formatValue = (value) => value.toString(),
  formatLabel,
  value
}) => {
  const [minValue, setMinValue] = useState<number>(
    value ? value[0] : (defaultMin !== undefined ? defaultMin : min)
  );
  const [maxValue, setMaxValue] = useState<number>(
    value ? value[1] : (defaultMax !== undefined ? defaultMax : max)
  );
  const [activeThumb, setActiveThumb] = useState<'min' | 'max' | null>(null);
  
  useEffect(() => {
    if (value) {
      setMinValue(value[0]);
      setMaxValue(value[1]);
    }
  }, [value]);
  
  useEffect(() => {
    if (minValue < min) setMinValue(min);
    if (maxValue > max) setMaxValue(max);
    if (minValue > maxValue) setMinValue(maxValue);
    if (maxValue < minValue) setMaxValue(minValue);
  }, [minValue, maxValue, min, max]);
  
  const minPosition = ((minValue - min) / (max - min)) * 100;
  const maxPosition = ((maxValue - min) / (max - min)) * 100;
  
  const handleMinInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value);
    if (!isNaN(value)) {
      setMinValue(value);
      onChange(value, maxValue);
    }
  };
  
  const handleMaxInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value);
    if (!isNaN(value)) {
      setMaxValue(value);
      onChange(minValue, value);
    }
  };
  
  const handleMouseMove = (e: MouseEvent) => {
    if (!activeThumb) return;
    
    const slider = document.getElementById('slider-track');
    if (!slider) return;
    
    const rect = slider.getBoundingClientRect();
    const percent = Math.min(Math.max(0, (e.clientX - rect.left) / rect.width), 1);
    const rawValue = min + percent * (max - min);
    const steppedValue = Math.round(rawValue / step) * step;
    
    if (activeThumb === 'min') {
      const newMin = Math.min(steppedValue, maxValue - step);
      setMinValue(newMin);
      onChange(newMin, maxValue);
    } else {
      const newMax = Math.max(steppedValue, minValue + step);
      setMaxValue(newMax);
      onChange(minValue, newMax);
    }
  };
  
  const handleMouseUp = () => {
    setActiveThumb(null);
    window.removeEventListener('mousemove', handleMouseMove);
    window.removeEventListener('mouseup', handleMouseUp);
  };
  
  const handleThumbMouseDown = (thumb: 'min' | 'max') => {
    setActiveThumb(thumb);
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };
  
  useEffect(() => {
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [handleMouseMove, handleMouseUp]);
  
  return (
    <SliderContainer>
      <ValueDisplay>
        <div>Мін: {(formatLabel || formatValue)(minValue)}</div>
        <div>Макс: {(formatLabel || formatValue)(maxValue)}</div>
      </ValueDisplay>
      
      <SliderTrack id="slider-track">
        <SliderRange start={minPosition} end={maxPosition} />
        <SliderThumb 
          position={minPosition} 
          active={activeThumb === 'min'}
          onMouseDown={() => handleThumbMouseDown('min')}
        />
        <SliderThumb 
          position={maxPosition} 
          active={activeThumb === 'max'}
          onMouseDown={() => handleThumbMouseDown('max')}
        />
      </SliderTrack>
      
      <InputsContainer>
        <Input 
          type="number" 
          value={minValue} 
          onChange={handleMinInputChange}
          min={min}
          max={maxValue}
          step={step}
        />
        <Input 
          type="number" 
          value={maxValue} 
          onChange={handleMaxInputChange}
          min={minValue}
          max={max}
          step={step}
        />
      </InputsContainer>
    </SliderContainer>
  );
};

export default RangeSlider; 