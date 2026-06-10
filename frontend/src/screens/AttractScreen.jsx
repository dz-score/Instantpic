import React, { useCallback } from 'react';
import ScreenShell from '../components/ScreenShell';
import { unlockAudio } from '../utils/sounds';
import { t } from '../utils/i18n';
import './AttractScreen.css';

/**
 * Welcome / attract screen — the first thing guests see.
 */
export default function AttractScreen({ config, onStart, language, setLanguage }) {

  const handleTap = useCallback((e) => {
    // Prevent starting if clicking the language toggle
    if (e.target.closest('.attract-lang-toggle')) return;
    unlockAudio();
    onStart();
  }, [onStart]);

  const toggleLanguage = () => {
    setLanguage(language === 'en' ? 'fr' : 'en');
  };

  const coupleNames = config?.couple_names || 'Welcome';
  const welcomeMsg = config?.welcome_message || 'Capture the love. Create memories.';

  return (
    <ScreenShell className="attract-screen">
      {/* ── Language Toggle (Top Right) ── */}
      <button className="attract-lang-toggle" onClick={toggleLanguage} aria-label="Toggle language">
        <span className={`lang-flag ${language === 'en' ? 'active' : ''}`}>
          <svg viewBox="0 0 60 30" width="24" height="16" style={{ borderRadius: '2px', display: 'block' }}>
            <clipPath id="uk-clip"><path d="M0,0 v30 h60 v-30 z"/></clipPath>
            <clipPath id="uk-cross"><path d="M30,15 h30 v15 z v15 h-30 z h-30 v-15 z v-15 h30 z"/></clipPath>
            <g clipPath="url(#uk-clip)">
              <path d="M0,0 v30 h60 v-30 z" fill="#012169"/>
              <path d="M0,0 L60,30 M60,0 L0,30" stroke="#fff" strokeWidth="6"/>
              <path d="M0,0 L60,30 M60,0 L0,30" clipPath="url(#uk-cross)" stroke="#C8102E" strokeWidth="4"/>
              <path d="M30,0 v30 M0,15 h60" stroke="#fff" strokeWidth="10"/>
              <path d="M30,0 v30 M0,15 h60" stroke="#C8102E" strokeWidth="6"/>
            </g>
          </svg>
        </span>
        <span className={`lang-flag ${language === 'fr' ? 'active' : ''}`}>
          <svg viewBox="0 0 3 2" width="24" height="16" style={{ borderRadius: '2px', display: 'block' }}>
            <rect width="1" height="2" fill="#0055A4" />
            <rect x="1" width="1" height="2" fill="#FFFFFF" />
            <rect x="2" width="1" height="2" fill="#EF4135" />
          </svg>
        </span>
      </button>

      {/* Full-screen tap target */}
      <button className="attract-content" onClick={handleTap}>

        {/* ── Decorative top flourish ── */}
        <div className="attract-flourish" aria-hidden="true">
          <span className="attract-flourish__hearts">♡♡</span>
          <div className="attract-flourish__line">
            <span className="attract-flourish__curl">❧</span>
            <span className="attract-flourish__dash" />
            <span className="attract-flourish__curl attract-flourish__curl--flip">❧</span>
          </div>
        </div>

        {/* ── Heading block ── */}
        <p className="attract-kicker">{t('welcome.kicker', language)}</p>
        <h1 className="attract-names">{coupleNames}</h1>
        <p className="attract-label">{t('welcome.label', language)}</p>

        {/* ── Heart divider ── */}
        <span className="attract-heart" aria-hidden="true">♥</span>

        {/* ── Subtitle ── */}
        <p className="attract-subtitle">{welcomeMsg}</p>

        {/* ── CTA Button ── */}
        <div className="attract-cta">
          <span className="attract-cta__shimmer" aria-hidden="true" />
          <span className="attract-cta__icon btn-icon" style={{ WebkitMaskImage: 'url(/icons/camera.png)' }} />
          <span className="attract-cta__text">{t('welcome.cta', language)}</span>
        </div>

      </button>
    </ScreenShell>
  );
}
