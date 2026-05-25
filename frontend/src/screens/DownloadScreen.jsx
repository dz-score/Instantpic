import React from 'react';
import Button from '../components/Button';
import './DownloadScreen.css';

/**
 * Mobile guest download page (accessed via QR code / /download/:filename URL).
 * Standalone — no header, no kiosk layout.
 */
export default function DownloadScreen({ filename }) {
  if (!filename) {
    return (
      <div className="download-screen">
        <div className="download-card">
          <h1 className="download-title">Photo Not Found</h1>
          <p className="download-text">This link may have expired.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="download-screen">
      <div className="download-card">
        <div className="download-header">
          <h1 className="download-title">Your Keepsake</h1>
          <p className="download-text">Thank you for celebrating with us!</p>
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
          Save to Device
        </a>

        <p className="download-tip">
          Tip: If the download doesn't start, tap and hold the image to save it to your photo library.
        </p>
      </div>
    </div>
  );
}
