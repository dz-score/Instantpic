import React from 'react';
import ScreenShell from '../components/ScreenShell';
import Button from '../components/Button';
import './ChooseStyleScreen.css';

/**
 * Layout selection — visual thumbnail cards for Single vs Collage.
 * Each card is a large, obvious tap target showing a miniature of the layout.
 */
export default function ChooseStyleScreen({ onSelect, onBack }) {
  return (
    <ScreenShell className="choose-screen">
      <h1 className="choose-title">Choose Your Style</h1>
      <p className="choose-subtitle">Pick a layout for your keepsake</p>

      <div className="choose-cards">
        {/* Single Photo */}
        <button className="choose-card" onClick={() => onSelect('single')}>
          <div className="choose-card__preview choose-card__preview--single">
            <div className="choose-card__frame" />
          </div>
          <h2 className="choose-card__title">Classic Photo</h2>
          <p className="choose-card__desc">1 beautiful shot</p>
        </button>

        {/* Collage Strip */}
        <button className="choose-card" onClick={() => onSelect('collage')}>
          <div className="choose-card__preview choose-card__preview--collage">
            <div className="choose-card__strip" />
            <div className="choose-card__strip" />
            <div className="choose-card__strip" />
          </div>
          <h2 className="choose-card__title">Photo Strip</h2>
          <p className="choose-card__desc">3 fun shots</p>
        </button>
      </div>

      <Button variant="ghost" size="small" className="choose-back" onClick={onBack}>
        ← Back
      </Button>
    </ScreenShell>
  );
}
