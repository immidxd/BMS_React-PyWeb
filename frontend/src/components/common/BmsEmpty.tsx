import React from 'react';

interface BmsEmptyProps {
  label: string;
  hint?: string;
}

const BmsEmpty: React.FC<BmsEmptyProps> = ({ label, hint }) => (
  <div className="bms-empty">
    <div className="bms-empty-icon">—</div>
    <div className="bms-empty-title">{label}</div>
    {hint && <div className="bms-empty-hint">{hint}</div>}
  </div>
);

export default BmsEmpty;
