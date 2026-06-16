import React from 'react';
import ScreenShell from '../components/ScreenShell';
import { t } from '../utils/i18n';
import { Home } from 'lucide-react';
import './ChooseStyleScreen.css';

/**
 * Mode selection — Single Classic vs 3-Photo Collage.
 */
export default function ChooseStyleScreen({ onSelect, onBack, language }) {
  return (
    <ScreenShell className="choose-screen">

      {/* ── Decorative top flourish ── */}
      <div className="choose-flourish" aria-hidden="true">
        <span className="choose-flourish__hearts">♡</span>
        <div className="choose-flourish__line">
          <span className="choose-flourish__curl">❧</span>
          <span className="choose-flourish__dash" />
          <span className="choose-flourish__curl choose-flourish__curl--flip">❧</span>
        </div>
      </div>

      {/* ── Heading ── */}
      <p className="choose-kicker">{t('chooseStyle.kicker', language)}</p>
      <h1 className="choose-title">{t('chooseStyle.title', language)}</h1>
      <span className="choose-title__heart" aria-hidden="true">♥</span>

      {/* ── Cards ── */}
      <div className="choose-cards">

        {/* Single Photo Card */}
        <button className="choose-card" onClick={() => onSelect('single')}>
          <div className="choose-card__badge">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="12" cy="12" r="3" />
              <path d="M3 16l5-5 4 4 3-3 6 6" />
            </svg>
          </div>
          <div className="choose-card__preview">
            <img src="/preview-single.png" alt="Single photo" className="choose-card__img" />
          </div>
          <h2 className="choose-card__title">{t('chooseStyle.singleTitle', language)}</h2>
        </button>

        {/* Collage Strip Card */}
        <button className="choose-card" onClick={() => onSelect('collage')}>
          <div className="choose-card__badge">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="2" width="16" height="6" rx="1" />
              <rect x="4" y="9" width="16" height="6" rx="1" />
              <rect x="4" y="16" width="16" height="6" rx="1" />
            </svg>
          </div>
          <div className="choose-card__preview">
            <img src="/preview-collage.png" alt="Photo strip" className="choose-card__img" />
          </div>
          <h2 className="choose-card__title">{t('chooseStyle.collageTitle', language)}</h2>
        </button>

      </div>

      {/* ── Back Button ── */}
      <button className="choose-back" onClick={onBack}>
        <span className="choose-back__icon btn-icon"><Home strokeWidth={1.5} size={20} /></span>
        <span className="choose-back__text">{t('framePicker.home', language)}</span>
      </button>

    </ScreenShell>
  );
}
