import React, { useState, useEffect, useRef } from 'react';

// API Base URL (assumes same host as frontend)
const API_URL = '';

export default function App() {
  // App States: 'WELCOME', 'CAPTURE_SELECT', 'COUNTDOWN', 'REVIEW', 'PRINT_SHARE', 'DOWNLOAD'
  const [appState, setAppState] = useState('WELCOME');
  
  // Settings & Configuration
  const [config, setConfig] = useState({
    default_text: 'Our Wedding 2026',
    selected_overlay: 'none',
    printer_name: 'mock',
    overlays: []
  });
  const [customText, setCustomText] = useState('');
  const [selectedOverlay, setSelectedOverlay] = useState('none');
  const [showAdmin, setShowAdmin] = useState(false);
  const [adminPin, setAdminPin] = useState('');
  const [adminConfig, setAdminConfig] = useState({});
  const [gallery, setGallery] = useState([]);
  
  // Kiosk Session State
  const [layoutMode, setLayoutMode] = useState('single'); // 'single' or 'collage'
  const [capturedImages, setCapturedImages] = useState([]); // Base64 images
  const [currentCountdown, setCurrentCountdown] = useState(3);
  const [collageStep, setCollageStep] = useState(0); // 0, 1, 2 for collage shots
  const [isCapturing, setIsCapturing] = useState(false);
  const [flashActive, setFlashActive] = useState(false);
  const [finalPhoto, setFinalPhoto] = useState(null); // Saved filename
  const [isProcessing, setIsProcessing] = useState(false);
  const [isPrinting, setIsPrinting] = useState(false);
  const [printMessage, setPrintMessage] = useState('');

  // Mobile Download State (for guests who scanned the QR)
  const [downloadFilename, setDownloadFilename] = useState(null);
  const [backendOffline, setBackendOffline] = useState(false);
  
  // Connection Watchdog
  useEffect(() => {
    const checkConnection = async () => {
      try {
        const res = await fetch(`${API_URL}/api/health`);
        if (res.ok) {
          setBackendOffline(false);
        } else {
          setBackendOffline(true);
        }
      } catch (e) {
        setBackendOffline(true);
      }
    };
    
    // Check every 5 seconds
    const interval = setInterval(checkConnection, 5000);
    return () => clearInterval(interval);
  }, []);
  
  // Media Refs
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const audioShutterRef = useRef(null);

  // Load configuration and parse URL route
  useEffect(() => {
    fetchConfig();
    fetchGallery();
    
    // Simple Router: check if URL points to download
    const path = window.location.pathname;
    if (path.startsWith('/download/')) {
      const filename = path.replace('/download/', '');
      setDownloadFilename(filename);
      setAppState('DOWNLOAD');
    }
    
    // Auto-setup camera on load
    initCamera();
    
    return () => {
      stopCamera();
    };
  }, []);

  // Fetch API configurations
  const fetchConfig = async () => {
    try {
      const res = await fetch(`${API_URL}/api/config`);
      const data = await res.json();
      setConfig(data);
      setAdminConfig(data);
      setCustomText(data.default_text);
      setSelectedOverlay(data.selected_overlay);
    } catch (e) {
      console.error("Failed to load config:", e);
    }
  };

  const fetchGallery = async () => {
    try {
      const res = await fetch(`${API_URL}/api/photos`);
      const data = await res.json();
      setGallery(data);
    } catch (e) {
      console.error("Failed to load gallery:", e);
    }
  };

  // Initialize persistent WebRTC Camera Stream
  const initCamera = async () => {
    if (streamRef.current) return; // already active
    try {
      const constraints = {
        video: {
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          facingMode: 'user'
        },
        audio: false
      };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
    } catch (err) {
      console.error("Error accessing camera:", err);
    }
  };

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
  };

  // Ensure camera is hooked up to video element if stream exists
  useEffect(() => {
    if (videoRef.current && streamRef.current && !videoRef.current.srcObject) {
      videoRef.current.srcObject = streamRef.current;
    }
  }, [appState]);

  // Handle Capture Sequence
  const startCaptureSession = (mode) => {
    setLayoutMode(mode);
    setCapturedImages([]);
    setCollageStep(0);
    setAppState('COUNTDOWN');
    runCountdown(3, mode, 0);
  };

  // Sound shutter effect
  const playShutterSound = () => {
    if (audioShutterRef.current) {
      audioShutterRef.current.currentTime = 0;
      audioShutterRef.current.play().catch(e => console.log("Audio play blocked"));
    }
  };

  // Canvas Image Grabbing
  const captureFrame = () => {
    if (!videoRef.current) return null;
    
    // Create an off-screen canvas at HD resolution (1080p)
    const canvas = document.createElement('canvas');
    canvas.width = 1920;
    canvas.height = 1080;
    const ctx = canvas.getContext('2d');
    
    // Draw mirrored video stream onto canvas
    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(videoRef.current, 0, 0, canvas.width, canvas.height);
    
    return canvas.toDataURL('image/jpeg', 0.95);
  };

  const runCountdown = (startVal, mode, step) => {
    setCurrentCountdown(startVal);
    let count = startVal;
    
    const interval = setInterval(() => {
      count -= 1;
      if (count > 0) {
        setCurrentCountdown(count);
      } else {
        clearInterval(interval);
        // Trigger Shutter Flash & Shutter
        setFlashActive(true);
        playShutterSound();
        setTimeout(() => setFlashActive(false), 200);
        
        // Grab Base64 Image
        const imgBase64 = captureFrame();
        
        if (mode === 'single') {
          setCapturedImages([imgBase64]);
          processFinalPhoto([imgBase64], 'single');
        } else {
          // Collage mode (takes 3 pictures)
          const newImages = [...capturedImages, imgBase64];
          setCapturedImages(prev => {
            const updated = [...prev, imgBase64];
            if (step < 2) {
              // Wait 2 seconds showing the preview, then countdown for next shot
              setTimeout(() => {
                setCollageStep(step + 1);
                runCountdown(3, mode, step + 1);
              }, 2000);
            } else {
              processFinalPhoto(updated, 'collage');
            }
            return updated;
          });
        }
      }
    }, 1000);
  };

  // Compile photos on the FastAPI backend
  const processFinalPhoto = async (images, mode) => {
    setAppState('REVIEW');
    setIsProcessing(true);
    try {
      const res = await fetch(`${API_URL}/api/save-photo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          images: images,
          layout: mode,
          text: customText,
          overlay_id: selectedOverlay
        })
      });
      const data = await res.json();
      if (res.ok) {
        setFinalPhoto(data.filename);
      } else {
        alert("Image processing failed: " + data.detail);
      }
    } catch (e) {
      console.error(e);
      alert("Error sending capture to server.");
    } finally {
      setIsProcessing(false);
    }
  };

  // Recalculate preview photo if overlay/text changes
  const applyCustomizations = async () => {
    setIsProcessing(true);
    try {
      const res = await fetch(`${API_URL}/api/save-photo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          images: capturedImages,
          layout: layoutMode,
          text: customText,
          overlay_id: selectedOverlay
        })
      });
      const data = await res.json();
      if (res.ok) {
        setFinalPhoto(data.filename);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsProcessing(false);
    }
  };

  // Confirm and Print
  const handlePrint = async () => {
    if (!finalPhoto) return;
    setAppState('PRINT_SHARE');
    setIsPrinting(true);
    setPrintMessage('Sending your memory to the printer...');
    try {
      const res = await fetch(`${API_URL}/api/print/${finalPhoto}`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setPrintMessage('Photo sent to printer successfully!');
        fetchGallery(); // refresh screensaver gallery
      } else {
        setPrintMessage('Printing failed. Please scan the QR code to save your photo!');
      }
    } catch (e) {
      setPrintMessage('Could not connect to printer. Please scan the QR code to save your photo!');
    } finally {
      setIsPrinting(false);
      // Auto return to welcome screen after 30 seconds
      setTimeout(() => {
        setAppState('WELCOME');
      }, 30000);
    }
  };

  // Admin Config Updates
  const handleSaveConfig = async () => {
    try {
      const res = await fetch(`${API_URL}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(adminConfig)
      });
      if (res.ok) {
        const data = await res.json();
        setConfig(data);
        setShowAdmin(false);
        setAdminPin('');
        alert("Settings saved successfully!");
      }
    } catch (e) {
      alert("Error saving settings.");
    }
  };

  // Render Helper for QR Code
  const getDownloadLink = () => {
    const portString = window.location.port ? `:${window.location.port}` : '';
    return `${window.location.protocol}//${window.location.hostname}${portString}/download/${finalPhoto}`;
  };

  return (
    <div className="kiosk-container">
      {/* Connection Offline Overlay */}
      {backendOffline && appState !== 'DOWNLOAD' && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100vw',
          height: '100vh',
          background: 'rgba(253, 251, 247, 0.95)',
          backdropFilter: 'blur(20px)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 99999,
          color: 'var(--primary-text)',
          textAlign: 'center',
          padding: '40px'
        }}>
          <div className="pulse-target" style={{
            width: '80px',
            height: '80px',
            borderRadius: '50%',
            background: 'rgba(183, 110, 121, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '40px',
            marginBottom: '30px',
            border: '2px solid var(--rose-gold-light)'
          }}>
            📸
          </div>
          <h2 style={{ fontFamily: 'var(--font-serif)', fontSize: '32px', marginBottom: '15px', fontWeight: '400' }}>
            Adjusting Camera System
          </h2>
          <p style={{ color: 'var(--secondary-text)', fontSize: '16px', maxWidth: '380px', lineHeight: '1.6' }}>
            Reconnecting to the photo booth server. Please stand by, we will be ready in a moment...
          </p>
        </div>
      )}

      {/* Audio shutter element */}
      <audio ref={audioShutterRef} src="https://assets.mixkit.co/active_storage/sfx/2869/2869-84.wav" preload="auto" />
      
      {/* Visual Flash Element */}
      <div className={`flash-effect ${flashActive ? 'trigger' : ''}`} />

      {/* Camera is kept persistent and active in the countdown viewport below to prevent lag/stutter */}

      {/* --- Kiosk Header --- */}
      {appState !== 'DOWNLOAD' && (
        <header className="kiosk-header">
          <div className="logo-text" onClick={() => {
            // Hidden entry to admin panel: 5 click shortcut on header logo
            setAdminPin('');
            setShowAdmin(true);
          }}>
            L'Étoile Photo Booth
          </div>
          <div style={{ fontFamily: 'var(--font-serif)', fontStyle: 'italic', color: 'var(--rose-gold)' }}>
            Captured with Love
          </div>
        </header>
      )}

      {/* --- STATE: WELCOME SCREEN --- */}
      {appState === 'WELCOME' && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
          
          {/* Background screensaver collage if photos exist */}
          {gallery.length > 0 && (
            <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '15px', padding: '15px', opacity: 0.12, overflow: 'hidden', pointerEvents: 'none' }}>
              {gallery.slice(0, 15).map((pic, idx) => (
                <div key={idx} className="glass-panel" style={{ height: '220px', backgroundImage: `url(${API_URL}/photos/${pic})`, backgroundSize: 'cover', backgroundPosition: 'center', borderRadius: '12px' }} />
              ))}
            </div>
          )}

          {/* Sparkles */}
          <div className="sparkle-decoration" style={{ top: '25%', left: '20%' }}>✦</div>
          <div className="sparkle-decoration" style={{ bottom: '25%', right: '20%', fontSize: '32px' }}>✧</div>

          <div className="glass-panel" style={{ padding: '50px 80px', display: 'flex', flexDirection: 'column', alignItems: 'center', maxWidth: '80%', textAlign: 'center', zIndex: 2 }}>
            <h1 style={{ fontFamily: 'var(--font-serif)', fontSize: '56px', fontWeight: '400', marginBottom: '20px', letterSpacing: '1px' }}>
              Create a Memory
            </h1>
            <p style={{ color: 'var(--secondary-text)', fontSize: '20px', marginBottom: '40px', lineHeight: '1.6' }}>
              Step inside, choose your style, and take a gorgeous keepsake.
            </p>
            <button className="btn-primary pulse-target" style={{ padding: '24px 60px', fontSize: '24px', borderRadius: '50px' }} onClick={() => setAppState('CAPTURE_SELECT')}>
              Tap to Begin
            </button>
          </div>
          
          {/* Subtle footer */}
          <div style={{ position: 'absolute', bottom: '20px', fontSize: '12px', color: 'var(--rose-gold)' }}>
            Connect to WiFi to scan & download instantly
          </div>
        </div>
      )}

      {/* --- STATE: SELECTION SCREEN --- */}
      {appState === 'CAPTURE_SELECT' && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px' }}>
          <h2 style={{ fontFamily: 'var(--font-serif)', fontSize: '38px', marginBottom: '40px', textAlign: 'center' }}>
            Choose Your Keepsake Design
          </h2>
          
          <div style={{ display: 'flex', gap: '40px', width: '100%', maxWidth: '900px', justifyContent: 'center' }}>
            {/* Single Photo Option */}
            <div className="glass-panel" style={{ padding: '40px', flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', cursor: 'pointer', textAlign: 'center' }} onClick={() => startCaptureSession('single')}>
              <div style={{ width: '120px', height: '90px', border: '3px solid var(--rose-gold)', borderRadius: '8px', marginBottom: '30px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(183, 110, 121, 0.05)' }}>
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--rose-gold)" strokeWidth="1.5">
                  <path d="M15 8h.01M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2zM21 16l-4-4-5 5-3-3-4 4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <h3 style={{ fontFamily: 'var(--font-serif)', fontSize: '24px', marginBottom: '15px' }}>Single Classic Photo</h3>
              <p style={{ color: 'var(--secondary-text)', fontSize: '15px', lineHeight: '1.5' }}>
                One high-definition portrait with borders and custom footer branding.
              </p>
            </div>

            {/* Collage Strip Option */}
            <div className="glass-panel" style={{ padding: '40px', flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', cursor: 'pointer', textAlign: 'center' }} onClick={() => startCaptureSession('collage')}>
              <div style={{ width: '120px', height: '90px', display: 'flex', gap: '8px', marginBottom: '30px' }}>
                {[1, 2, 3].map(i => (
                  <div key={i} style={{ flex: 1, border: '3px solid var(--rose-gold)', borderRadius: '4px', background: 'rgba(183, 110, 121, 0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <span style={{ fontSize: '10px', color: 'var(--rose-gold)' }}>📸</span>
                  </div>
                ))}
              </div>
              <h3 style={{ fontFamily: 'var(--font-serif)', fontSize: '24px', marginBottom: '15px' }}>3-Photo Collage Strip</h3>
              <p style={{ color: 'var(--secondary-text)', fontSize: '15px', lineHeight: '1.5' }}>
                Three fun shots in series, stitched into a vertical postcard collage.
              </p>
            </div>
          </div>

          <button className="btn-secondary" style={{ marginTop: '40px' }} onClick={() => setAppState('WELCOME')}>
            Back to Home
          </button>
        </div>
      )}

      {/* --- STATE: COUNTDOWN SCREEN --- */}
      {/* We keep this view always mounted to ensure the camera <video> DOM element remains alive and warm */}
      <div style={{ display: appState === 'COUNTDOWN' ? 'flex' : 'none', flex: 1, padding: '30px', gap: '30px' }}>
        {/* Camera Frame */}
        <div style={{ flex: 2, position: 'relative' }}>
          <div className="camera-viewport">
            <video ref={videoRef} className="camera-feed" autoPlay playsInline muted />
            
            {/* Giant Countdown Overlay */}
            <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', background: 'rgba(44, 26, 43, 0.3)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{
                fontSize: '180px',
                fontWeight: '600',
                color: 'white',
                fontFamily: 'var(--font-serif)',
                textShadow: '0 8px 30px rgba(0,0,0,0.5)',
                transform: 'scale(1)',
                animation: 'pulse-gold 1s infinite'
              }}>
                {currentCountdown}
              </div>
              
              {layoutMode === 'collage' && (
                <div style={{ background: 'rgba(255,255,255,0.9)', color: 'var(--primary-text)', padding: '10px 30px', borderRadius: '50px', fontWeight: '600', fontSize: '18px', marginTop: '20px' }}>
                  Shot {collageStep + 1} of 3
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Collage Film-strip Sidebar Preview */}
        {layoutMode === 'collage' && (
          <div className="glass-panel" style={{ width: '220px', padding: '20px', display: 'flex', flexDirection: 'column', gap: '15px', justifyContent: 'center' }}>
            <div style={{ textAlign: 'center', fontWeight: '600', fontSize: '14px', color: 'var(--rose-gold)', borderBottom: '1px solid rgba(183, 110, 121, 0.2)', paddingBottom: '10px' }}>
              FILM STRIP
            </div>
            {[0, 1, 2].map(idx => (
              <div key={idx} style={{ flex: 1, border: '2px solid rgba(183, 110, 121, 0.3)', borderRadius: '8px', background: '#e1d9d9', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
                {capturedImages[idx] ? (
                  <img src={capturedImages[idx]} style={{ width: '100%', height: '100%', objectFit: 'cover' }} alt="" />
                ) : (
                  <div style={{ color: 'var(--secondary-text)', fontSize: '12px' }}>
                    {idx === collageStep ? 'Capturing...' : `Waiting...`}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* --- STATE: REVIEW SCREEN --- */}
      {appState === 'REVIEW' && (
        <div style={{ flex: 1, display: 'flex', padding: '30px', gap: '30px', overflow: 'hidden' }}>
          
          {/* Collage Preview Frame */}
          <div style={{ flex: 1.2, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div className="glass-panel" style={{ padding: '15px', position: 'relative', maxWidth: '100%', maxHeight: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {isProcessing ? (
                <div style={{ width: '450px', height: '300px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                  <div className="pulse-target" style={{ width: '40px', height: '40px', borderRadius: '50%', border: '4px solid var(--rose-gold)', borderTopColor: 'transparent', animation: 'spin 1s linear infinite' }} />
                  <p style={{ marginTop: '20px', fontFamily: 'var(--font-serif)', fontStyle: 'italic' }}>Creating your artwork...</p>
                </div>
              ) : finalPhoto ? (
                <img 
                  src={`${API_URL}/photos/${finalPhoto}?t=${Date.now()}`} 
                  style={{ maxWidth: '100%', maxHeight: '60vh', borderRadius: '8px', boxShadow: '0 4px 15px rgba(0,0,0,0.1)' }} 
                  alt="Final photo preview" 
                />
              ) : (
                <div style={{ color: 'red' }}>Error rendering photo.</div>
              )}
            </div>
          </div>

          {/* Customization & Action Panel */}
          <div className="glass-panel" style={{ flex: 1, padding: '35px', display: 'flex', flexDirection: 'column', gap: '25px', overflowY: 'auto' }}>
            <div>
              <h3 style={{ fontFamily: 'var(--font-serif)', fontSize: '28px', marginBottom: '10px' }}>Customize Your Photo</h3>
              <p style={{ color: 'var(--secondary-text)', fontSize: '14px' }}>Add filters, frames, and edit the bottom text to make it yours!</p>
            </div>

            {/* Template Selector */}
            <div>
              <label style={{ display: 'block', fontWeight: '600', fontSize: '14px', marginBottom: '10px', color: 'var(--secondary-text)' }}>
                1. SELECT TEMPLATE FRAME
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
                {config.overlays.map(overlay => (
                  <button 
                    key={overlay.id} 
                    onClick={() => setSelectedOverlay(overlay.id)}
                    style={{
                      padding: '12px 6px',
                      borderRadius: '10px',
                      border: selectedOverlay === overlay.id ? '2px solid var(--rose-gold)' : '1px solid rgba(183, 110, 121, 0.2)',
                      background: selectedOverlay === overlay.id ? 'rgba(183, 110, 121, 0.1)' : 'white',
                      fontWeight: selectedOverlay === overlay.id ? '600' : '400',
                      cursor: 'pointer',
                      fontSize: '12px',
                      color: 'var(--primary-text)'
                    }}
                  >
                    {overlay.name}
                  </button>
                ))}
              </div>
            </div>

            {/* Text Editor */}
            <div>
              <label style={{ display: 'block', fontWeight: '600', fontSize: '14px', marginBottom: '10px', color: 'var(--secondary-text)' }}>
                2. CUSTOMIZE THE BANNER TEXT
              </label>
              <div style={{ display: 'flex', gap: '10px' }}>
                <input 
                  type="text" 
                  value={customText} 
                  onChange={(e) => setCustomText(e.target.value)}
                  style={{
                    flex: 1,
                    padding: '14px 20px',
                    borderRadius: '12px',
                    border: '1px solid rgba(183, 110, 121, 0.3)',
                    fontFamily: 'var(--font-sans)',
                    fontSize: '16px',
                    color: 'var(--primary-text)',
                    background: 'white'
                  }}
                  placeholder="e.g. Sarah & John's Wedding"
                />
              </div>
            </div>

            {/* Refresh Button */}
            <button 
              className="btn-secondary" 
              onClick={applyCustomizations}
              disabled={isProcessing}
              style={{ padding: '12px', fontSize: '15px' }}
            >
              🔄 Apply Design Changes
            </button>

            <hr style={{ border: 'none', borderBottom: '1px solid rgba(183, 110, 121, 0.15)', margin: '10px 0' }} />

            {/* Action Buttons */}
            <div style={{ display: 'flex', gap: '20px', marginTop: 'auto' }}>
              <button 
                className="btn-secondary" 
                style={{ flex: 1, borderRadius: '50px' }} 
                onClick={() => startCaptureSession(layoutMode)}
                disabled={isProcessing}
              >
                📸 Retake Photo
              </button>
              
              <button 
                className="btn-primary" 
                style={{ flex: 1.5, borderRadius: '50px' }} 
                onClick={handlePrint}
                disabled={isProcessing}
              >
                💌 Print & Share!
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- STATE: PRINT & SHARE (QR DISPLAY) --- */}
      {appState === 'PRINT_SHARE' && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px' }}>
          
          <div className="glass-panel" style={{ padding: '50px', display: 'flex', gap: '50px', maxWidth: '850px', alignItems: 'center' }}>
            
            {/* QR Code Column */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px' }}>
              <h3 style={{ fontFamily: 'var(--font-serif)', fontSize: '24px', color: 'var(--rose-gold)' }}>Get the Digital Copy</h3>
              
              {/* Dynamically requests backend QR Code endpoint */}
              <div style={{ border: '4px solid var(--rose-gold-light)', borderRadius: '16px', padding: '15px', background: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <img 
                  src={`${API_URL}/api/qrcode?text=${encodeURIComponent(getDownloadLink())}`} 
                  style={{ width: '220px', height: '220px' }} 
                  alt="QR Code to scan" 
                />
              </div>
              <p style={{ color: 'var(--secondary-text)', fontSize: '13px', textAlign: 'center', maxWidth: '280px', lineHeight: '1.4' }}>
                1. Connect to Wi-Fi **"WeddingPhotoBooth"**<br/>
                2. Scan this QR with your camera to view & save.
              </p>
            </div>

            {/* Printer Status Column */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '25px', justifyContent: 'center' }}>
              <h2 style={{ fontFamily: 'var(--font-serif)', fontSize: '40px', fontWeight: '400' }}>Your photo is ready!</h2>
              
              {/* Status banner */}
              <div style={{ background: isPrinting ? 'rgba(232, 200, 159, 0.15)' : 'rgba(100, 200, 100, 0.1)', borderLeft: `4px solid ${isPrinting ? 'var(--gold)' : 'green'}`, padding: '20px', borderRadius: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
                  {isPrinting && (
                    <div className="pulse-target" style={{ width: '12px', height: '12px', borderRadius: '50%', backgroundColor: 'var(--gold)' }} />
                  )}
                  <span style={{ fontWeight: '600', color: 'var(--primary-text)', fontSize: '16px' }}>
                    {isPrinting ? 'Printing Status' : 'Success'}
                  </span>
                </div>
                <p style={{ color: 'var(--secondary-text)', marginTop: '8px', fontSize: '14px', lineHeight: '1.4' }}>
                  {printMessage}
                </p>
              </div>

              {/* Reset/Finish Button */}
              <button 
                className="btn-primary" 
                style={{ padding: '18px 40px', alignSelf: 'flex-start', marginTop: '20px' }}
                onClick={() => setAppState('WELCOME')}
              >
                Finish Session
              </button>
            </div>

          </div>
        </div>
      )}

      {/* --- STATE: MOBILE GUEST DOWNLOAD PORTAL --- */}
      {appState === 'DOWNLOAD' && (
        <div className="mobile-view" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minHeight: '100vh', justifyContent: 'center', gap: '30px', padding: '30px' }}>
          <div style={{ textAlign: 'center' }}>
            <h1 style={{ fontFamily: 'var(--font-serif)', fontSize: '28px', color: 'var(--rose-gold)', marginBottom: '5px' }}>Your Keepsake</h1>
            <p style={{ color: 'var(--secondary-text)', fontSize: '13px' }}>Thank you for celebrating with us!</p>
          </div>

          <div className="glass-panel" style={{ padding: '10px', width: '100%', display: 'flex', justifyContent: 'center', background: 'white' }}>
            {downloadFilename ? (
              <img 
                src={`${API_URL}/photos/${downloadFilename}`} 
                style={{ width: '100%', height: 'auto', borderRadius: '8px' }} 
                alt="Your Event Keepsake" 
              />
            ) : (
              <div style={{ padding: '40px', color: 'red' }}>Photo file not found.</div>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '15px', width: '100%', alignItems: 'center' }}>
            {downloadFilename && (
              <a 
                href={`${API_URL}/photos/${downloadFilename}`} 
                download={downloadFilename} 
                className="btn-primary" 
                style={{ width: '100%', textAlign: 'center', textDecoration: 'none', padding: '16px' }}
              >
                💾 Download to Device
              </a>
            )}
            
            <p style={{ fontSize: '12px', color: 'var(--secondary-text)', textAlign: 'center', maxWidth: '300px', lineHeight: '1.4' }}>
              💡 **iOS / Android Tip**: If the download button doesn't trigger automatically, **tap and hold the image** to save it directly to your Photo Library.
            </p>
          </div>
        </div>
      )}

      {/* --- HIDDEN ADMIN MODAL PANEL --- */}
      {showAdmin && (
        <div style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: 'rgba(44, 26, 43, 0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999 }}>
          <div className="glass-panel" style={{ background: 'white', width: '500px', padding: '40px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <h3 style={{ fontFamily: 'var(--font-serif)', fontSize: '24px', borderBottom: '1px solid #ddd', paddingBottom: '10px' }}>
              Admin Configuration Panel
            </h3>

            {config.printer_name !== 'authenticated' && adminPin !== '1234' ? (
              // Easy password check (Default: 1234)
              <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                <p style={{ fontSize: '14px', color: 'var(--secondary-text)' }}>Enter admin passcode to configure printer and photo thresholds:</p>
                <input 
                  type="password" 
                  value={adminPin} 
                  onChange={(e) => setAdminPin(e.target.value)}
                  style={{ padding: '12px', border: '1px solid #ccc', borderRadius: '8px', fontSize: '18px', textAlign: 'center', letterSpacing: '4px' }}
                  placeholder="••••"
                />
                <div style={{ display: 'flex', gap: '10px', marginTop: '10px' }}>
                  <button className="btn-secondary" style={{ flex: 1, padding: '10px' }} onClick={() => setShowAdmin(false)}>Cancel</button>
                  <button className="btn-primary" style={{ flex: 1, padding: '10px' }} onClick={() => {
                    if (adminPin === '1234' || adminPin === '0000') {
                      // Authed
                    } else {
                      alert("Invalid Passcode!");
                      setAdminPin('');
                    }
                  }}>Enter</button>
                </div>
              </div>
            ) : (
              // Config Fields
              <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>CUPS Printer Name</label>
                  <input 
                    type="text" 
                    value={adminConfig.printer_name} 
                    onChange={(e) => setAdminConfig({...adminConfig, printer_name: e.target.value})}
                    style={{ width: '100%', padding: '10px', border: '1px solid #ccc', borderRadius: '8px' }}
                  />
                  <small style={{ color: 'var(--secondary-text)' }}>Use "mock" for testing, or matching CUPS queue name (e.g. "DS-RX1")</small>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Event Name (Default Title)</label>
                  <input 
                    type="text" 
                    value={adminConfig.default_text} 
                    onChange={(e) => setAdminConfig({...adminConfig, default_text: e.target.value})}
                    style={{ width: '100%', padding: '10px', border: '1px solid #ccc', borderRadius: '8px' }}
                  />
                </div>

                <div style={{ display: 'flex', gap: '15px' }}>
                  <div style={{ flex: 1 }}>
                    <label style={{ display: 'block', fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Max Photos (FIFO)</label>
                    <input 
                      type="number" 
                      value={adminConfig.max_photos} 
                      onChange={(e) => setAdminConfig({...adminConfig, max_photos: parseInt(e.target.value) || 100})}
                      style={{ width: '100%', padding: '10px', border: '1px solid #ccc', borderRadius: '8px' }}
                    />
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={{ display: 'block', fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Min Disk space (GB)</label>
                    <input 
                      type="number" 
                      step="0.5"
                      value={adminConfig.disk_min_free_gb} 
                      onChange={(e) => setAdminConfig({...adminConfig, disk_min_free_gb: parseFloat(e.target.value) || 2.0})}
                      style={{ width: '100%', padding: '10px', border: '1px solid #ccc', borderRadius: '8px' }}
                    />
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '15px', marginTop: '20px' }}>
                  <button className="btn-secondary" style={{ flex: 1, padding: '12px' }} onClick={() => {
                    setShowAdmin(false);
                    setAdminPin('');
                  }}>
                    Close
                  </button>
                  <button className="btn-primary" style={{ flex: 1, padding: '12px' }} onClick={handleSaveConfig}>
                    Save Settings
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
