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
        <span className={`lang-flag ${language === 'en' ? 'active' : ''}`}>🇬🇧</span>
        <span className={`lang-flag ${language === 'fr' ? 'active' : ''}`}>🇫🇷</span>
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
          <span className="attract-cta__icon">♡</span>
          <span className="attract-cta__text">{t('welcome.cta', language)}</span>
        </div>

      </button>
    </ScreenShell>
  );
}
