import React, { useState, useEffect } from 'react';
import './CameraTab.css';

export default function CameraTab({ getCameraConfig, saveCameraConfig }) {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  
  // Format the setting keys for UI
  const labels = {
    iso: 'ISO Speed',
    aperture: 'Aperture (f-stop)',
    shutterspeed: 'Shutter Speed',
    whitebalance: 'White Balance'
  };

  const fetchSettings = async () => {
    setLoading(true);
    try {
      const data = await getCameraConfig();
      if (data.status === 'disconnected') {
        setError('Camera is not connected. Please check USB and power.');
      } else {
        setSettings(data);
        setError(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleChange = async (key, value) => {
    // Optimistic update
    setSettings(prev => ({
      ...prev,
      [key]: { ...prev[key], value }
    }));
    
    setSaving(true);
    try {
      await saveCameraConfig({ [key]: value });
      // Re-fetch to ensure camera accepted it
      await fetchSettings();
    } catch (err) {
      setError(`Failed to save ${labels[key] || key}.`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="camera-tab"><p>Loading camera settings...</p></div>;
  }

  if (error) {
    return (
      <div className="camera-tab">
        <div className="tab-header">
          <h2 className="tab-header__title">Camera Settings</h2>
        </div>
        <div className="camera-error">
          <p>{error}</p>
          <button className="camera-retry-btn" onClick={fetchSettings}>Retry Connection</button>
        </div>
      </div>
    );
  }

  return (
    <div className="camera-tab">
      <div className="tab-header">
        <h2 className="tab-header__title">Camera Settings</h2>
        <p className="tab-header__subtitle">Adjust Canon M50 exposure settings</p>
      </div>
      
      {saving && <div className="camera-saving-indicator">Saving to camera...</div>}

      <div className="camera-settings-grid">
        {['iso', 'aperture', 'shutterspeed', 'whitebalance'].map(key => {
          const setting = settings[key];
          if (!setting || !setting.choices) return null;
          
          return (
            <div key={key} className="camera-section">
              <span className="camera-section__label">{labels[key]}</span>
              <select 
                className="camera-select"
                value={setting.value || ''}
                onChange={(e) => handleChange(key, e.target.value)}
                disabled={saving}
              >
                {setting.choices.map(choice => (
                  <option key={choice} value={choice}>{choice}</option>
                ))}
              </select>
            </div>
          );
        })}
      </div>
      
      <div className="camera-footer-note">
        <p><strong>Note:</strong> The camera must be in Manual (M) mode on the physical dial for these settings to apply correctly.</p>
      </div>
    </div>
  );
}
