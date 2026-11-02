// LokalLoop Vanilla JavaScript Utility Layer

document.addEventListener('DOMContentLoaded', () => {
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
      }
    });
  });

  document.querySelectorAll('[data-modal-close]').forEach(closer => {
    closer.addEventListener('click', () => {
      const modal = closer.closest('.modal-backdrop');
      if (modal) {
        modal.style.display = 'none';
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

  // 5. Mobile Navbar Drawer Toggle
  const mobileToggle = document.getElementById('mobileNavToggle');
  const mobileDrawer = document.getElementById('mobileMenuDrawer');
  if (mobileToggle && mobileDrawer) {
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
