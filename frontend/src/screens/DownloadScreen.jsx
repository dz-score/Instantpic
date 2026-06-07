import React from 'react';
import Button from '../components/Button';
import { t } from '../utils/i18n';
import './DownloadScreen.css';

/**
 * Mobile guest download page (accessed via QR code / /download/:filename URL).
 * Standalone — no header, no kiosk layout.
 */
export default function DownloadScreen({ filename, language = 'en' }) {
  if (!filename) {
    return (
      <div className="download-screen">
        <div className="download-card">
          <h1 className="download-title">{t('download.notFound', language)}</h1>
          <p className="download-text">{t('download.expired', language)}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="download-screen">
      <div className="download-card">
        <div className="download-header">
          <h1 className="download-title">{t('download.keepsake', language)}</h1>
          <p className="download-text">{t('download.thankYou', language)}</p>
        </div>

        <div className="download-photo">
          <img
            src={`/photos/${filename}`}
            alt="Your event keepsake"
            className="download-photo__img"
          />
        </div>

        <a
          href={`/photos/${filename}`}
          download={filename}
          className="download-save-btn"
        >
          {t('download.saveBtn', language)}
        </a>

        <p className="download-tip">
          {t('download.tip', language)}
        </p>
      </div>
    </div>
  );
}
