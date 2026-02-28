(function () {
  const actionTarget = document.getElementById('action-result');

  function setActionMessage(html) {
    if (!actionTarget) return;
    actionTarget.innerHTML = html || '';
  }

  function refreshCommonPanels() {
    const targets = [
      '#top-strip',
      '#jobs-panel',
      '#incidents-panel',
      '#events-panel',
      '#status-panel',
      '#order-actions-panel',
      '#control-actions-panel',
      '#job-detail-panel',
      '#research-panel'
    ];
    for (const selector of targets) {
      const node = document.querySelector(selector);
      if (node && window.htmx) {
        htmx.trigger(node, 'refresh');
        htmx.ajax('GET', node.getAttribute('hx-get'), { target: selector, swap: 'innerHTML' });
      }
    }
  }

  document.body.addEventListener('htmx:afterRequest', function (event) {
    const requestPath = event && event.detail && event.detail.pathInfo
      ? event.detail.pathInfo.requestPath || ''
      : '';
    if (requestPath.startsWith('/api/actions/')) {
      refreshCommonPanels();
    }
  });

  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const confirmText = form.getAttribute('data-confirm');
    if (confirmText && !window.confirm(confirmText)) {
      event.preventDefault();
    }
  });

  const palette = document.getElementById('command-palette');
  const paletteInput = document.getElementById('palette-input');
  const paletteList = document.getElementById('palette-list');
  const openButton = document.getElementById('command-open');

  const commands = [
    { label: 'Go to Overview', kind: 'nav', href: '/overview' },
    { label: 'Go to Trading', kind: 'nav', href: '/trading' },
    { label: 'Go to Research', kind: 'nav', href: '/research' },
    { label: 'Go to Incidents & Jobs', kind: 'nav', href: '/incidents' },
    { label: 'Go to Settings', kind: 'nav', href: '/settings' },
    { label: 'Start Bot (Shadow)', kind: 'post', url: '/api/actions/start', body: { mode: 'shadow' } },
    { label: 'Start Bot (Live)', kind: 'post', url: '/api/actions/start', body: { mode: 'live' } },
    { label: 'Stop Bot', kind: 'post', url: '/api/actions/stop', body: {}, confirm: 'Stop the bot now?' },
    { label: 'Pause Bot', kind: 'post', url: '/api/actions/pause', body: {} },
    { label: 'Resume Bot', kind: 'post', url: '/api/actions/resume', body: {} },
    { label: 'Run One-Shot Scan', kind: 'post', url: '/api/actions/scan-now', body: { mode: 'shadow' } },
    { label: 'Run Reconcile', kind: 'post', url: '/api/actions/reconcile', body: {} },
    { label: 'Enable Kill Switch', kind: 'post', url: '/api/actions/kill-switch-enable', body: { reason: 'Command palette kill switch' }, confirm: 'Enable kill switch?' },
    { label: 'Disable Kill Switch', kind: 'post', url: '/api/actions/kill-switch-disable', body: { reason: 'Command palette clear kill switch' }, confirm: 'Disable kill switch?' },
  ];

  let visible = [];
  let activeIndex = 0;

  function closePalette() {
    if (!palette) return;
    palette.classList.add('hidden');
    palette.setAttribute('aria-hidden', 'true');
    if (paletteInput) paletteInput.value = '';
    visible = [];
    renderCommands();
  }

  function openPalette() {
    if (!palette) return;
    palette.classList.remove('hidden');
    palette.setAttribute('aria-hidden', 'false');
    activeIndex = 0;
    filterCommands('');
    if (paletteInput) paletteInput.focus();
  }

  function filterCommands(query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) {
      visible = commands.slice();
    } else {
      visible = commands.filter((c) => c.label.toLowerCase().includes(q));
    }
    activeIndex = 0;
    renderCommands();
  }

  function renderCommands() {
    if (!paletteList) return;
    paletteList.innerHTML = '';
    if (!visible.length) {
      const empty = document.createElement('li');
      empty.className = 'palette-item muted';
      empty.textContent = 'No matching commands';
      paletteList.appendChild(empty);
      return;
    }
    visible.forEach((command, index) => {
      const li = document.createElement('li');
      li.className = 'palette-item' + (index === activeIndex ? ' active' : '');
      li.textContent = command.label;
      li.addEventListener('mouseenter', () => {
        activeIndex = index;
        renderCommands();
      });
      li.addEventListener('click', () => {
        executeCommand(command);
      });
      paletteList.appendChild(li);
    });
  }

  async function executeCommand(command) {
    if (command.kind === 'nav') {
      window.location.href = command.href;
      return;
    }

    if (command.confirm && !window.confirm(command.confirm)) {
      return;
    }

    try {
      const body = new URLSearchParams(command.body || {});
      const response = await fetch(command.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      const html = await response.text();
      setActionMessage(html);
      refreshCommonPanels();
    } catch (error) {
      setActionMessage("<div class='action-msg error'>Command failed to execute.</div>");
    } finally {
      closePalette();
    }
  }

  document.addEventListener('click', function (event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.id === 'command-open') {
      openPalette();
      return;
    }
    if (target.hasAttribute('data-close-palette')) {
      closePalette();
    }
  });

  if (openButton) {
    openButton.addEventListener('click', function () {
      openPalette();
    });
  }

  if (paletteInput) {
    paletteInput.addEventListener('input', function () {
      filterCommands(paletteInput.value);
    });
    paletteInput.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (visible.length) {
          activeIndex = (activeIndex + 1) % visible.length;
          renderCommands();
        }
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (visible.length) {
          activeIndex = (activeIndex - 1 + visible.length) % visible.length;
          renderCommands();
        }
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (visible[activeIndex]) {
          executeCommand(visible[activeIndex]);
        }
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closePalette();
      }
    });
  }

  document.addEventListener('keydown', function (event) {
    const isModK = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k';
    if (isModK) {
      event.preventDefault();
      if (palette && !palette.classList.contains('hidden')) {
        closePalette();
      } else {
        openPalette();
      }
    }
    if (event.key === 'Escape' && palette && !palette.classList.contains('hidden')) {
      closePalette();
    }
  });
})();
