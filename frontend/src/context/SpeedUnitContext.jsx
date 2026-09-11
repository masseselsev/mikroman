import React, { createContext, useContext, useState, useEffect } from 'react';

const SpeedUnitContext = createContext({
  speedUnit: 'bits', // 'bits' | 'bytes'
  toggleSpeedUnit: () => {},
  setSpeedUnit: () => {}
});

export const SpeedUnitProvider = ({ children }) => {
  const [speedUnit, setSpeedUnit] = useState(() => {
    try {
      return localStorage.getItem('mikroman_speed_unit') || 'bits';
    } catch {
      return 'bits';
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem('mikroman_speed_unit', speedUnit);
    } catch {
      // Ignore in private storage
    }
  }, [speedUnit]);

  const toggleSpeedUnit = () => {
    setSpeedUnit(prev => (prev === 'bits' ? 'bytes' : 'bits'));
  };

  return (
    <SpeedUnitContext.Provider value={{ speedUnit, toggleSpeedUnit, setSpeedUnit }}>
      {children}
    </SpeedUnitContext.Provider>
  );
};

export const useSpeedUnit = () => useContext(SpeedUnitContext);
