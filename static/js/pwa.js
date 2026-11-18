// Регистрация service worker и кнопка «Установить приложение»
(function () {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch((e) => console.warn('SW не зарегистрирован', e));
    });
  }

  const btn = document.getElementById('install-btn');
  let deferred = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferred = e;
    if (btn) btn.classList.remove('hidden');
  });

  if (btn) {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (!deferred) return;
      deferred.prompt();
      await deferred.userChoice;
      deferred = null;
      btn.classList.add('hidden');
    });
  }

  window.addEventListener('appinstalled', () => { if (btn) btn.classList.add('hidden'); });
})();
