// LokalLoop Vanilla JavaScript Utility Layer

// ============================================================================
// 0. Median / GoNative Android WebView Native Bridge & Status Bar Sync (WEB-01)
// ============================================================================
window.syncNativeStatusBar = function(hexColor, contentStyle) {
  try {
    if (!hexColor) return;

    // A. Update or create standard HTML5 theme-color meta tag
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'theme-color';
      document.head.appendChild(meta);
    }
    meta.setAttribute('content', hexColor);

    // B. Calculate contentStyle if not explicitly provided ('light' for dark bg, 'dark' for light bg)
    let style = contentStyle;
    if (!style) {
      const cleanHex = hexColor.replace('#', '');
      const r = parseInt(cleanHex.substring(0, 2), 16) || 0;
      const g = parseInt(cleanHex.substring(2, 4), 16) || 0;
      const b = parseInt(cleanHex.substring(4, 6), 16) || 0;
      // Perceived luminance formula (ITU-R BT.709)
      const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
      style = luminance > 0.6 ? 'dark' : 'light';
    }

    // C. Update iOS web clip status bar meta tag
    let appleMeta = document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]');
    if (!appleMeta) {
      appleMeta = document.createElement('meta');
      appleMeta.name = 'apple-mobile-web-app-status-bar-style';
      document.head.appendChild(appleMeta);
    }
    appleMeta.setAttribute('content', style === 'light' ? 'black-translucent' : 'default');

    // D. Median (GoNative) JavaScript Bridge invocation
    // Median documentation: style: 'light' (white text/icons) or 'dark' (dark text/icons)
    const bridgePayload = { color: hexColor, style: style };
    if (window.median && window.median.statusbar && typeof window.median.statusbar.set === 'function') {
      window.median.statusbar.set(bridgePayload);
    } else if (window.gonative && window.gonative.statusbar && typeof window.gonative.statusbar.set === 'function') {
      window.gonative.statusbar.set(bridgePayload);
    }
  } catch (err) {
    console.debug('StatusBar sync notice:', err);
  }
};

// ============================================================================
// 0.1 Universal Android Hardware Back-Stack History Trapping (WEB-03)
// ============================================================================
window._activeModalStack = window._activeModalStack || [];

window.pushModalHistory = function(modalRef) {
  try {
    window._activeModalStack.push(modalRef);
    window.history.pushState({ modalOpen: true, modalRef: typeof modalRef === 'string' ? modalRef : 'generic' }, '');
  } catch (e) {
    console.debug('pushModalHistory error:', e);
  }
};

window.popModalHistorySilent = function() {
  if (window._activeModalStack.length > 0) {
    window._activeModalStack.pop();
  }
};

window.addEventListener('popstate', function(e) {
  // 1. Check Public Mobile Navigation Drawer
  const publicDrawer = document.getElementById('mobileMenuDrawer');
  if (publicDrawer && (publicDrawer.classList.contains('show') || publicDrawer.style.display === 'flex')) {
    if (typeof closePublicMobileNav === 'function') {
      closePublicMobileNav(true);
      return;
    }
  }

  // 2. Check Buyer Mobile Account Drawer
  const accountDrawer = document.getElementById('mobileAccountDrawer');
  if (accountDrawer && accountDrawer.style.display === 'block') {
    if (typeof closeMobileAccountDrawer === 'function') {
      closeMobileAccountDrawer(true);
      return;
    }
  }

  // 2.5 Check Seller Mobile Sidebar Drawer
  const sellerBackdrop = document.getElementById('sellerSidebarBackdrop');
  if (sellerBackdrop && (sellerBackdrop.classList.contains('show') || sellerBackdrop.style.display === 'block')) {
    if (typeof closeSellerSidebar === 'function') {
      closeSellerSidebar(true);
      return;
    }
  }

  // 3. Check Admin Mobile Sidebar Drawer
  const adminBackdrop = document.querySelector('.admin-sidebar-backdrop.show');
  if (adminBackdrop) {
    if (typeof closeAdminSidebar === 'function') {
      closeAdminSidebar(true);
      return;
    }
  }

  // 4. Check Generic Modal Backdrops (.modal-backdrop)
  const openModals = document.querySelectorAll('.modal-backdrop');
  for (let i = openModals.length - 1; i >= 0; i--) {
    const modal = openModals[i];
    if (modal.style.display === 'flex' || modal.style.display === 'block' || modal.classList.contains('show')) {
      modal.style.display = 'none';
      modal.classList.remove('show');
      window.popModalHistorySilent();
      return;
    }
  }

  // 5. Check Counter Request Modal specifically
  const counterModal = document.getElementById('counterRequestModal');
  if (counterModal && (counterModal.style.display === 'flex' || counterModal.style.display === 'block')) {
    if (typeof closeCounterModal === 'function') {
      closeCounterModal(true);
      return;
    }
  }

  // 6. Check Custom Named Modals
  const customModalIds = ['sellerQuickActionsModal', 'qrScannerBackdrop', 'dealClaimModal'];
  for (const id of customModalIds) {
    const el = document.getElementById(id);
    if (el && (el.style.display === 'flex' || el.style.display === 'block' || el.classList.contains('show'))) {
      el.style.display = 'none';
      el.classList.remove('show');
      window.popModalHistorySilent();
      return;
    }
  }
});



// ============================================================================
// 0.3 Universal Form Double-Submission Lockout & Loading Indicator (COMP-01)
// ============================================================================
document.addEventListener('submit', function(e) {
  const form = e.target;
  if (!form || form.tagName !== 'FORM') return;

  // Skip GET forms (search, filters) or forms explicitly opting out
  if ((form.method && form.method.toLowerCase() === 'get') || form.hasAttribute('data-no-lockout')) {
    return;
  }

  // Prevent duplicate submission if already processing
  if (form.getAttribute('data-submitting') === 'true') {
    e.preventDefault();
    return false;
  }

  form.setAttribute('data-submitting', 'true');

  // Find primary submit button
  const submitBtn = form.querySelector('button[type="submit"], input[type="submit"], .btn-primary');
  if (submitBtn && !submitBtn.disabled) {
    submitBtn.classList.add('btn-loading');

    // For <button>, inject a loading spinner
    if (submitBtn.tagName === 'BUTTON' && !submitBtn.querySelector('.btn-spinner')) {
      const spinner = document.createElement('span');
      spinner.className = 'btn-spinner';
      spinner.setAttribute('role', 'status');
      spinner.setAttribute('aria-hidden', 'true');
      submitBtn.insertAdjacentElement('afterbegin', spinner);
    }

    // Safety fallback: Release lockout after 8 seconds in case of client-side validation interruptions
    setTimeout(() => {
      form.removeAttribute('data-submitting');
      submitBtn.classList.remove('btn-loading');
      const sp = submitBtn.querySelector('.btn-spinner');
      if (sp) sp.remove();
    }, 8000);
  }
});

document.addEventListener('DOMContentLoaded', () => {
  // Auto-sync status bar based on initial meta tag (WEB-01)
  const initialMeta = document.querySelector('meta[name="theme-color"]');
  if (initialMeta && initialMeta.getAttribute('content')) {
    window.syncNativeStatusBar(initialMeta.getAttribute('content'));
  }
  // 1. Auto dismiss alerts after 5 seconds
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(alert => {
    setTimeout(() => {
      alert.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      alert.style.opacity = '0';
      alert.style.transform = 'translateY(-10px)';
      setTimeout(() => alert.remove(), 400);
    }, 5000);
  });

  // 2. Modal toggles
  document.querySelectorAll('[data-modal-target]').forEach(trigger => {
    trigger.addEventListener('click', (e) => {
      e.preventDefault();
      const targetId = trigger.getAttribute('data-modal-target');
      const modal = document.getElementById(targetId);
      if (modal) {
        modal.style.display = 'flex';
        window.pushModalHistory(targetId);
      }
    });
  });

  document.querySelectorAll('[data-modal-close]').forEach(closer => {
    closer.addEventListener('click', () => {
      const modal = closer.closest('.modal-backdrop');
      if (modal) {
        modal.style.display = 'none';
        if (window.history.state && window.history.state.modalOpen) {
          window.history.back();
        } else {
          window.popModalHistorySilent();
        }
      }
    });
  });

  // 3. Dropdown toggles
  document.querySelectorAll('.dropdown-toggle').forEach(dropdown => {
    dropdown.addEventListener('click', (e) => {
      e.stopPropagation();
      const menu = dropdown.nextElementSibling;
      if (menu && menu.classList.contains('dropdown-menu')) {
        menu.classList.toggle('show');
      }
    });
  });

  document.addEventListener('click', () => {
    document.querySelectorAll('.dropdown-menu.show').forEach(menu => {
      menu.classList.remove('show');
    });
  });

  function showLocationToast(msg, isSuccess = false) {
    let toast = document.getElementById('geo-toast-notice');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'geo-toast-notice';
      toast.style.cssText = 'position:fixed;top:80px;right:20px;z-index:99999;padding:0.75rem 1.25rem;border-radius:8px;font-size:0.875rem;font-weight:600;box-shadow:0 10px 25px rgba(0,0,0,0.18);transition:all 0.3s ease;';
      document.body.appendChild(toast);
    }
    toast.style.backgroundColor = isSuccess ? '#0F172A' : '#FEF2F2';
    toast.style.color = isSuccess ? '#FFFFFF' : '#991B1B';
    toast.style.border = isSuccess ? '1px solid #334155' : '1px solid #F87171';
    toast.innerHTML = msg;
    toast.style.display = 'block';
    setTimeout(() => {
      if (toast) toast.style.display = 'none';
    }, 4500);
  }

  function handleFindNearest(triggerBtn) {
    if (!navigator.geolocation) {
      showLocationToast('Geolocation is not supported by your browser. Please choose your city above.');
      return;
    }

    const originalText = triggerBtn ? triggerBtn.innerHTML : '';
    if (triggerBtn) {
      triggerBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" style="width:14px;height:14px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;display:inline-block;animation:spin 0.75s linear infinite;"></span> Detecting GPS...';
      triggerBtn.disabled = true;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const userLat = pos.coords.latitude.toFixed(6);
        const userLng = pos.coords.longitude.toFixed(6);
        sessionStorage.setItem('user_lat', userLat);
        sessionStorage.setItem('user_lng', userLng);

        const latInput = document.getElementById('user-lat');
        const lngInput = document.getElementById('user-lng');
        if (latInput) latInput.value = userLat;
        if (lngInput) lngInput.value = userLng;

        if (triggerBtn) {
          triggerBtn.innerHTML = '📍 Location Found!';
          triggerBtn.classList.remove('btn-secondary', 'btn-outline');
          triggerBtn.classList.add('btn-success');
        }

        const parentForm = triggerBtn ? triggerBtn.closest('form') : null;
        if (parentForm) {
          let latInp = parentForm.querySelector('input[name="lat"]');
          let lngInp = parentForm.querySelector('input[name="lng"]');
          if (!latInp) {
            latInp = document.createElement('input');
            latInp.type = 'hidden';
            latInp.name = 'lat';
            parentForm.appendChild(latInp);
          }
          if (!lngInp) {
            lngInp = document.createElement('input');
            lngInp.type = 'hidden';
            lngInp.name = 'lng';
            parentForm.appendChild(lngInp);
          }
          latInp.value = userLat;
          lngInp.value = userLng;
          parentForm.submit();
          return;
        }

        const currentUrl = new URL(window.location.href);
        if (window.location.pathname === '/') {
          window.location.href = `/explore/?lat=${userLat}&lng=${userLng}`;
        } else {
          currentUrl.searchParams.set('lat', userLat);
          currentUrl.searchParams.set('lng', userLng);
          window.location.href = currentUrl.toString();
        }
      },
      (err) => {
        if (triggerBtn) {
          triggerBtn.innerHTML = originalText;
          triggerBtn.disabled = false;
        }
        let reason = 'Location access was blocked. Please select your city manually from the dropdown.';
        if (err.code === 1) {
          reason = '📍 Location permission denied. Please enable location access or select your city manually.';
        } else if (err.code === 2) {
          reason = 'Unable to determine location. Please select your city from the dropdown.';
        } else if (err.code === 3) {
          reason = 'Location request timed out. Please try again.';
        }
        showLocationToast(reason, false);
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 60000
      }
    );
  }

  document.querySelectorAll('.find-nearest-trigger, #detect-location-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      handleFindNearest(btn);
    });
  });

  const savedLat = sessionStorage.getItem('user_lat');
  const savedLng = sessionStorage.getItem('user_lng');
  if (savedLat && savedLng) {
    const latInp = document.getElementById('user-lat');
    const lngInp = document.getElementById('user-lng');
    if (latInp && !latInp.value) latInp.value = savedLat;
    if (lngInp && !lngInp.value) lngInp.value = savedLng;
  }

  // 5. Mobile Navbar Drawer Toggle (guard against duplicate bindings)
  const mobileToggle = document.getElementById('mobileNavToggle');
  const mobileDrawer = document.getElementById('mobileMenuDrawer');
  if (mobileToggle && mobileDrawer && !mobileToggle.hasAttribute('onclick')) {
    mobileToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      mobileDrawer.classList.toggle('show');
      const icon = mobileToggle.querySelector('i');
      if (icon) {
        if (mobileDrawer.classList.contains('show')) {
          icon.className = 'bi bi-x-lg';
        } else {
          icon.className = 'bi bi-list';
        }
      }
    });

    // Close when clicking outside
    document.addEventListener('click', (e) => {
      if (!mobileDrawer.contains(e.target) && !mobileToggle.contains(e.target)) {
        mobileDrawer.classList.remove('show');
        const icon = mobileToggle.querySelector('i');
        if (icon) icon.className = 'bi bi-list';
      }
    });
  }

  // 6. Cookie Consent Banner Logic
  const consentBanner = document.getElementById('cookieConsentBanner');
  const acceptBtn = document.getElementById('acceptCookiesBtn');
  const rejectBtn = document.getElementById('rejectCookiesBtn');
  if (consentBanner) {
    const consentDecision = localStorage.getItem('lokalloop_cookie_consent');
    if (!consentDecision) {
      consentBanner.style.display = 'block';
    }
    if (acceptBtn) {
      acceptBtn.addEventListener('click', () => {
        localStorage.setItem('lokalloop_cookie_consent', 'accepted');
        consentBanner.style.display = 'none';
      });
    }
    if (rejectBtn) {
      rejectBtn.addEventListener('click', () => {
        localStorage.setItem('lokalloop_cookie_consent', 'essential_only');
        consentBanner.style.display = 'none';
      });
    }
  }
});

// Copy to clipboard helper (BUG-2.3: Safe across HTTP, HTTPS, WebViews & Older Devices)
function copyToClipboard(text, btnElement) {
  function showFeedback() {
    if (btnElement) {
      const originalText = btnElement.innerText;
      btnElement.innerText = 'Copied! ✓';
      setTimeout(() => { btnElement.innerText = originalText; }, 2000);
    }
  }

  function fallbackExecCopy(str) {
    try {
      const textArea = document.createElement('textarea');
      textArea.value = str;
      textArea.setAttribute('readonly', '');
      textArea.style.position = 'fixed';
      textArea.style.left = '-9999px';
      textArea.style.top = '0';
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      const successful = document.execCommand('copy');
      document.body.removeChild(textArea);
      if (successful) showFeedback();
    } catch (err) {
      console.warn('Fallback copy command failed:', err);
    }
  }

  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    navigator.clipboard.writeText(text).then(showFeedback).catch(() => {
      fallbackExecCopy(text);
    });
  } else {
    fallbackExecCopy(text);
  }
}


// ============================================================================
// 7. Navigation State & City Filter Persistence (User Directive)
// ============================================================================
(function initCityPersistence() {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const urlCity = urlParams.get('city');
    if (urlCity) {
      sessionStorage.setItem('lokalloop_selected_city', urlCity);
    }

    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[href*="city="]');
      if (link) {
        try {
          const u = new URL(link.href, window.location.origin);
          const c = u.searchParams.get('city');
          if (c) sessionStorage.setItem('lokalloop_selected_city', c);
        } catch (err) {}
      }
    });
  } catch (e) {}
})();

// ============================================================================
// 8. Form Draft Auto-Save & Accidental Loss Protection (User Directive)
// ============================================================================
(function initFormDraftProtection() {
  document.addEventListener('DOMContentLoaded', () => {
    const targetForms = document.querySelectorAll('form:not([method="get"]):not([data-no-draft])');

    targetForms.forEach(form => {
      const formKey = form.id || form.getAttribute('action') || window.location.pathname;
      const storageKey = 'll_draft_' + btoa(formKey).substring(0, 32);

      // 1. Restore draft if present and inputs are empty
      try {
        const savedRaw = sessionStorage.getItem(storageKey);
        if (savedRaw) {
          const savedData = JSON.parse(savedRaw);
          Object.keys(savedData).forEach(name => {
            const field = form.elements[name];
            if (field && field.type !== 'password' && field.type !== 'file' && field.type !== 'hidden') {
              if (!field.value || field.value.trim() === '') {
                if (field.type === 'checkbox') {
                  field.checked = Boolean(savedData[name]);
                } else {
                  field.value = savedData[name];
                }
              }
            }
          });
        }
      } catch (err) {}

      // 2. Track changes and auto-save (debounced)
      let saveTimer = null;
      let isFormDirty = false;

      form.addEventListener('input', (e) => {
        if (!e.target || e.target.type === 'password' || e.target.type === 'file') return;
        isFormDirty = true;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          try {
            const formData = {};
            const elements = form.elements;
            for (let i = 0; i < elements.length; i++) {
              const el = elements[i];
              if (el.name && el.type !== 'password' && el.type !== 'file' && el.type !== 'hidden') {
                formData[el.name] = el.type === 'checkbox' ? el.checked : el.value;
              }
            }
            sessionStorage.setItem(storageKey, JSON.stringify(formData));
          } catch (err) {}
        }, 400);
      });

      // 3. Clear draft on successful submit
      form.addEventListener('submit', () => {
        isFormDirty = false;
        try {
          sessionStorage.removeItem(storageKey);
        } catch (err) {}
      });

      // 4. Warn before navigating away if form was edited
      window.addEventListener('beforeunload', (e) => {
        if (isFormDirty) {
          e.preventDefault();
          e.returnValue = '';
        }
      });
    });
  });
})();

// ============================================================================
// 9. Root Page Double-Back to Exit (Android WebView Natural Feel)
// ============================================================================
(function initDoubleBackToExit() {
  const rootPaths = ['/', '/buyer/dashboard/', '/seller/dashboard/'];
  let lastBackPress = 0;

  if (rootPaths.includes(window.location.pathname)) {
    try {
      if (!window.history.state || !window.history.state.isRootSentinel) {
        window.history.pushState({ isRootSentinel: true }, '');
      }
    } catch (err) {}

    window.addEventListener('popstate', (e) => {
      const hasOpenModal = document.querySelector('.modal-backdrop.show, .mobile-menu-drawer.show, #mobileAccountDrawer[style*="block"], .admin-sidebar-backdrop.show, #counterRequestModal[style*="flex"]');
      if (hasOpenModal) return;

      const now = Date.now();
      if (now - lastBackPress < 2000) {
        if (window.median && window.median.screen && typeof window.median.screen.exit === 'function') {
          window.median.screen.exit();
        } else {
          window.history.back();
        }
      } else {
        lastBackPress = now;
        try {
          window.history.pushState({ isRootSentinel: true }, '');
        } catch (err) {}

        let toast = document.getElementById('ll-exit-toast');
        if (!toast) {
          toast = document.createElement('div');
          toast.id = 'll-exit-toast';
          toast.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:rgba(15,23,42,0.92);color:#ffffff;padding:0.6rem 1.25rem;border-radius:9999px;font-size:0.82rem;font-weight:700;z-index:99999;box-shadow:0 8px 20px rgba(0,0,0,0.3);backdrop-filter:blur(4px);pointer-events:none;transition:opacity 0.3s ease;';
          document.body.appendChild(toast);
        }
        toast.textContent = 'Press back again to exit';
        toast.style.opacity = '1';
        setTimeout(() => {
          if (toast) toast.style.opacity = '0';
        }, 1800);
      }
    });
  }
})();
