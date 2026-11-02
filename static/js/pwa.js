// LokalLoop PWA Controller: Service Worker Registration & Install Prompt Handler
(function() {
  'use strict';

  // 1. Register Service Worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
      navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
        .then(function(reg) {
          // Service worker active
        })
        .catch(function(err) {
          console.warn('[LokalLoop PWA] SW registration skipped:', err);
        });
    });
  }

  // 2. Install App Smart Mobile Prompt Banner
  let deferredPrompt = null;
  const installBanner = document.getElementById('pwaInstallBanner');
  const installButtons = document.querySelectorAll('.btn-install-pwa');

  function isStandaloneMode() {
    return window.matchMedia('(display-mode: standalone)').matches ||
           window.navigator.standalone === true ||
           document.referrer.includes('android-app://');
  }

  // If already running as an installed standalone app, hide banners
  if (isStandaloneMode()) {
    if (installBanner) installBanner.style.display = 'none';
    installButtons.forEach(function(btn) { btn.style.display = 'none'; });
    return;
  }

  // Capture beforeinstallprompt event
  window.addEventListener('beforeinstallprompt', function(e) {
    e.preventDefault();
    deferredPrompt = e;

    // Show custom floating banner if present and not previously dismissed
    if (installBanner && !localStorage.getItem('lokalloop_pwa_dismissed')) {
      installBanner.style.display = 'flex';
    }

    // Show dedicated install buttons
    installButtons.forEach(function(btn) {
      btn.style.display = 'inline-flex';
    });
  });

  // Handle click on install actions
  window.promptPwaInstall = function() {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function(choiceResult) {
        if (choiceResult.outcome === 'accepted') {
          if (installBanner) installBanner.style.display = 'none';
          installButtons.forEach(function(btn) { btn.style.display = 'none'; });
        }
        deferredPrompt = null;
      });
    } else {
      // Fallback instructions for iOS Safari or browsers without beforeinstallprompt
      const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
      if (isIOS) {
        alert("To install LokalLoop on iOS: Tap the Share button below (square with arrow up) and select 'Add to Home Screen' (+).");
      } else {
        alert("To install LokalLoop: Open your browser menu (⋮) and tap 'Install app' or 'Add to Home screen'.");
      }
    }
  };

  // Close/Dismiss floating banner
  window.dismissPwaBanner = function() {
    if (installBanner) installBanner.style.display = 'none';
    localStorage.setItem('lokalloop_pwa_dismissed', 'true');
  };

  // App installed event
  window.addEventListener('appinstalled', function() {
    if (installBanner) installBanner.style.display = 'none';
    installButtons.forEach(function(btn) { btn.style.display = 'none'; });
    deferredPrompt = null;
  });

  // 3. Smart Navigation State & Page Restoration in Android / PWA
  const path = window.location.pathname;
  const isAuthPage = path.startsWith('/buyer/') || path.startsWith('/seller/') || path.startsWith('/admin-portal/') || path.startsWith('/explore/');
  if (isAuthPage && !path.includes('login') && !path.includes('logout')) {
    try {
      localStorage.setItem('last_visited_url', path);
    } catch(e) {}
  }

  // Clear state on explicit user logout
  document.querySelectorAll('a[href*="logout"]').forEach(function(link) {
    link.addEventListener('click', function() {
      try {
        localStorage.removeItem('last_visited_url');
      } catch(e) {}
      document.cookie = 'last_visited_url=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;';
    });
  });

})();
