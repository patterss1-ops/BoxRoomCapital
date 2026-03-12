// switchTab and createTerminalChart are defined in base.html <head> for early availability

(function () {
  const actionTarget = document.getElementById('action-result');
  const researchSelectionKey = 'research:selected-chain';
  const researchQueueLaneKey = 'research:queue-lane';
  const researchActiveViewKey = 'research:active-view';
  const researchChainParam = 'research_chain';
  const researchLaneParam = 'research_lane';
  const researchViewParam = 'research_view';

  function setActionMessage(html) {
    if (!actionTarget) return;
    actionTarget.innerHTML = html || '';
  }

  function buildResearchRequestPath(basePath, params) {
    const search = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      const value = String(params[key] || '').trim();
      if (value) {
        search.set(key, value);
      }
    });
    const query = search.toString();
    return query ? basePath + '?' + query : basePath;
  }

  function refreshCommonPanels() {
    const targets = [
      '#top-strip',
      '#overview-engine-panel',
      '#jobs-panel',
      '#incidents-panel',
      '#events-panel',
      '#status-panel',
      '#order-actions-panel',
      '#control-actions-panel',
      '#job-detail-panel',
      '#research-panel',
      '#intelligence-feed-panel',
      '#pipeline-status-panel'
    ];
    for (const selector of targets) {
      const node = document.querySelector(selector);
      if (node && window.htmx) {
        htmx.trigger(node, 'refresh');
        htmx.ajax('GET', node.getAttribute('hx-get'), { target: selector, swap: 'innerHTML' });
      }
    }
  }

  function rememberResearchChain(chainId) {
    if (!chainId) return;
    try {
      window.sessionStorage.setItem(researchSelectionKey, String(chainId));
    } catch (error) {
      // Ignore storage failures; the workbench still functions without persistence.
    }
    updateResearchLocation({ chainId: chainId });
    syncResearchRequestTargets();
  }

  function clearRememberedResearchChain() {
    try {
      window.sessionStorage.removeItem(researchSelectionKey);
    } catch (error) {
      // Ignore storage failures; URL state is still authoritative when available.
    }
    updateResearchLocation({ chainId: '' });
    syncResearchRequestTargets();
  }

  function getRememberedResearchChain() {
    const fromLocation = getResearchLocationChain();
    if (fromLocation) {
      return fromLocation;
    }
    try {
      return window.sessionStorage.getItem(researchSelectionKey) || '';
    } catch (error) {
      return '';
    }
  }

  function syncResearchRequestTargets() {
    if (window.location.pathname !== '/research') return '';
    const chainId = getRememberedResearchChain();
    const queueLane = getRememberedResearchQueueLane();
    const activeView = getRememberedResearchActiveView();
    const researchPanel = document.getElementById('research-panel');
    const focusTarget = document.getElementById('research-focus-ribbon');
    const alertsTarget = document.getElementById('research-alerts');
    const activeChainsTarget = document.getElementById('research-active-hypotheses');
    const chainTarget = document.getElementById('research-artifact-chain-viewer');
    const workbenchTarget = document.getElementById('research-operator-output');
    if (researchPanel) {
      researchPanel.setAttribute(
        'hx-get',
        buildResearchRequestPath('/fragments/research', {
          queue_lane: queueLane !== 'all' ? queueLane : '',
          chain_id: chainId,
          active_view: activeView !== 'all' ? activeView : ''
        })
      );
    }
    if (alertsTarget) {
      alertsTarget.setAttribute(
        'hx-get',
        buildResearchRequestPath('/fragments/research/alerts', {
          queue_lane: queueLane !== 'all' ? queueLane : '',
          chain_id: chainId
        })
      );
    }
    if (activeChainsTarget) {
      activeChainsTarget.setAttribute(
        'hx-get',
        buildResearchRequestPath('/fragments/research/active-hypotheses', {
          active_view: activeView !== 'all' ? activeView : '',
          chain_id: chainId
        })
      );
    }
    if (focusTarget) {
      focusTarget.setAttribute(
        'hx-get',
        buildResearchRequestPath('/fragments/research/focus-ribbon', {
          chain_id: chainId,
          queue_lane: queueLane !== 'all' ? queueLane : '',
          active_view: activeView !== 'all' ? activeView : ''
        })
      );
      if (chainId) {
        focusTarget.setAttribute('data-initial-research-chain', chainId);
      } else {
        focusTarget.removeAttribute('data-initial-research-chain');
      }
    }
    if (chainTarget) {
      chainTarget.setAttribute(
        'hx-get',
        chainId
          ? '/fragments/research/artifact-chain/' + encodeURIComponent(chainId)
          : '/fragments/research/artifact-chain'
      );
      if (chainId) {
        chainTarget.setAttribute('data-initial-research-chain', chainId);
      } else {
        chainTarget.removeAttribute('data-initial-research-chain');
      }
    }
    if (workbenchTarget) {
      workbenchTarget.setAttribute(
        'hx-get',
        buildResearchRequestPath('/fragments/research/operator-output', {
          queue_lane: queueLane !== 'all' ? queueLane : '',
          chain_id: chainId,
          active_view: activeView !== 'all' ? activeView : ''
        })
      );
      if (chainId) {
        workbenchTarget.setAttribute('data-initial-research-chain', chainId);
      } else {
        workbenchTarget.removeAttribute('data-initial-research-chain');
      }
    }
    return chainId;
  }

  function prepareResearchInitialTargets() {
    return syncResearchRequestTargets();
  }

  function updateResearchLocation(options) {
    if (window.location.pathname !== '/research') return;
    if (!window.history || typeof window.history.replaceState !== 'function') return;
    try {
      const url = new URL(window.location.href);
      if (Object.prototype.hasOwnProperty.call(options, 'chainId')) {
        const chainId = String(options.chainId || '').trim();
        if (chainId) {
          url.searchParams.set(researchChainParam, chainId);
        } else {
          url.searchParams.delete(researchChainParam);
        }
      }
      if (Object.prototype.hasOwnProperty.call(options, 'queueLane')) {
        const queueLane = normalizeResearchQueueLane(options.queueLane);
        if (queueLane !== 'all') {
          url.searchParams.set(researchLaneParam, queueLane);
        } else {
          url.searchParams.delete(researchLaneParam);
        }
      }
      if (Object.prototype.hasOwnProperty.call(options, 'activeView')) {
        const activeView = normalizeResearchActiveView(options.activeView);
        if (activeView !== 'all') {
          url.searchParams.set(researchViewParam, activeView);
        } else {
          url.searchParams.delete(researchViewParam);
        }
      }
      window.history.replaceState(window.history.state, '', url);
    } catch (error) {
      // Ignore URL persistence failures; session storage still preserves state.
    }
  }

  function getResearchLocationChain() {
    if (window.location.pathname !== '/research') return '';
    try {
      return String(new URL(window.location.href).searchParams.get(researchChainParam) || '').trim();
    } catch (error) {
      return '';
    }
  }

  function normalizeResearchQueueLane(lane) {
    const value = String(lane || '').trim().toLowerCase();
    return ['all', 'review', 'pilot', 'rebalance', 'retirements'].includes(value) ? value : 'all';
  }

  function normalizeResearchActiveView(view) {
    const value = String(view || '').trim().toLowerCase();
    return ['all', 'focus', 'operator', 'flow', 'stale'].includes(value) ? value : 'all';
  }

  window.rememberResearchQueueLane = function (lane) {
    const normalized = normalizeResearchQueueLane(lane);
    try {
      window.sessionStorage.setItem(researchQueueLaneKey, normalized);
    } catch (error) {
      // Ignore storage failures; the queue still works without persistence.
    }
    updateResearchLocation({ queueLane: normalized });
    syncResearchRequestTargets();
    return normalized;
  };

  function getRememberedResearchQueueLane() {
    if (window.location.pathname === '/research') {
      try {
        const fromLocation = normalizeResearchQueueLane(new URL(window.location.href).searchParams.get(researchLaneParam) || 'all');
        if (fromLocation !== 'all' || window.location.search.indexOf(researchLaneParam + '=') >= 0) {
          return fromLocation;
        }
      } catch (error) {
        // Ignore URL parsing failures and fall back to session storage.
      }
    }
    try {
      return normalizeResearchQueueLane(window.sessionStorage.getItem(researchQueueLaneKey) || 'all');
    } catch (error) {
      return 'all';
    }
  }

  window.getResearchQueueLane = function () {
    return getRememberedResearchQueueLane();
  };

  window.getResearchActionVals = function (extra) {
    const payload = Object.assign({}, extra || {});
    payload.queue_lane = getRememberedResearchQueueLane();
    payload.active_view = getRememberedResearchActiveView();
    return payload;
  };

  window.rememberResearchActiveView = function (view) {
    const normalized = normalizeResearchActiveView(view);
    try {
      window.sessionStorage.setItem(researchActiveViewKey, normalized);
    } catch (error) {
      // Ignore storage failures; the board can still render without persistence.
    }
    updateResearchLocation({ activeView: normalized });
    syncResearchRequestTargets();
    return normalized;
  };

  function getRememberedResearchActiveView() {
    if (window.location.pathname === '/research') {
      try {
        const fromLocation = normalizeResearchActiveView(new URL(window.location.href).searchParams.get(researchViewParam) || 'all');
        if (fromLocation !== 'all' || window.location.search.indexOf(researchViewParam + '=') >= 0) {
          return fromLocation;
        }
      } catch (error) {
        // Ignore URL parsing failures and fall back to session storage.
      }
    }
    try {
      return normalizeResearchActiveView(window.sessionStorage.getItem(researchActiveViewKey) || 'all');
    } catch (error) {
      return 'all';
    }
  }

  function getVisibleResearchActiveCards(boardRoot) {
    const root = boardRoot || document.getElementById('research-active-hypotheses');
    if (!root) return [];
    return Array.from(root.querySelectorAll('[data-active-view-card]')).filter(function (node) {
      return !node.classList.contains('hidden');
    });
  }

  function setResearchActiveNavButtonState(button, enabled) {
    if (!button) return;
    button.disabled = !enabled;
    button.classList.toggle('opacity-50', !enabled);
    button.classList.toggle('cursor-not-allowed', !enabled);
  }

  function researchActiveViewLabel(view) {
    const normalized = normalizeResearchActiveView(view);
    if (normalized === 'focus') return 'Board Focus';
    if (normalized === 'operator') return 'Operator';
    if (normalized === 'flow') return 'Flow';
    if (normalized === 'stale') return 'Stale';
    return 'All';
  }

  function inferResearchActiveViewFromQueueLane(lane) {
    const normalized = normalizeResearchQueueLane(lane);
    if (normalized === 'review' || normalized === 'pilot') return 'operator';
    if (normalized === 'flow') return 'flow';
    return 'all';
  }

  function inferResearchActiveViewForCard(card) {
    if (!card) return 'all';
    const isFocusCard = String(card.getAttribute('data-active-view-focus') || '').trim().toLowerCase() === 'true';
    if (isFocusCard) return 'focus';
    const freshness = String(card.getAttribute('data-active-view-freshness') || '').trim().toLowerCase();
    if (freshness === 'stale') return 'stale';
    const kind = String(card.getAttribute('data-active-view-card') || '').trim().toLowerCase();
    if (kind === 'operator') return 'operator';
    if (kind === 'flow') return 'flow';
    return 'all';
  }

  function inferResearchQueueLaneForCard(card) {
    if (!card) return 'all';
    return normalizeResearchQueueLane(card.getAttribute('data-research-queue-lane') || '');
  }

  function openResearchActiveCard(card) {
    if (!card) return false;
    const chainId = String(card.getAttribute('data-research-chain-card') || '').trim();
    if (!chainId) return false;
    const queueLane = inferResearchQueueLaneForCard(card);
    const activeView = getRememberedResearchActiveView();
    window.refreshResearchChainViewer(chainId, queueLane, activeView);
    window.refreshResearchOperatorOutput();
    return true;
  }

  window.revealSelectedResearchActiveCard = function () {
    const boardRoot = document.getElementById('research-active-hypotheses');
    if (!boardRoot) return false;
    const selectedChainId = getRememberedResearchChain();
    if (!selectedChainId) return false;
    const selectedCard = Array.from(boardRoot.querySelectorAll('[data-active-view-card]')).find(function (node) {
      return String(node.getAttribute('data-research-chain-card') || '').trim() === selectedChainId;
    }) || null;
    const targetView = inferResearchActiveViewForCard(selectedCard);
    const queueLane = inferResearchQueueLaneForCard(selectedCard);
    window.setResearchQueueAndActiveView(queueLane, targetView, false);
    window.applyResearchSelectedChain(selectedChainId);
    window.refreshResearchAlerts();
    window.refreshResearchOperatorOutput();
    return true;
  };

  window.updateResearchActiveViewSelectionState = function () {
    const boardRoot = document.getElementById('research-active-hypotheses');
    if (!boardRoot) return;
    const allCards = Array.from(boardRoot.querySelectorAll('[data-active-view-card]'));
    const visibleCards = getVisibleResearchActiveCards(boardRoot);
    const selectedChainId = getRememberedResearchChain();
    const selectedCardAny = allCards.find(function (node) {
      return String(node.getAttribute('data-research-chain-card') || '').trim() === selectedChainId;
    }) || null;
    const selectedIndex = visibleCards.findIndex(function (node) {
      return String(node.getAttribute('data-research-chain-card') || '').trim() === selectedChainId;
    });
    const stateNode = boardRoot.querySelector('[data-active-view-selection-state]');
    const detailNode = boardRoot.querySelector('[data-active-view-selection-detail]');
    const warningNode = boardRoot.querySelector('[data-active-view-selection-warning]');
    const warningTitle = boardRoot.querySelector('[data-active-view-selection-warning-title]');
    const warningDetail = boardRoot.querySelector('[data-active-view-selection-warning-detail]');
    const revealButton = boardRoot.querySelector('[data-active-view-reveal-selected]');
    const firstButton = boardRoot.querySelector('[data-active-view-nav-first]');
    const prevButton = boardRoot.querySelector('[data-active-view-nav-prev]');
    const nextButton = boardRoot.querySelector('[data-active-view-nav-next]');

    if (warningNode) warningNode.classList.add('hidden');
    if (revealButton) revealButton.classList.add('hidden');

    if (visibleCards.length === 0) {
      if (stateNode) stateNode.textContent = 'No visible chains are in this slice.';
      if (detailNode) detailNode.textContent = 'Switch the board view or return to All to see other chains.';
      setResearchActiveNavButtonState(firstButton, false);
      setResearchActiveNavButtonState(prevButton, false);
      setResearchActiveNavButtonState(nextButton, false);
      return;
    }

    if (selectedIndex < 0) {
      if (selectedChainId && selectedCardAny) {
        const tickerNode = selectedCardAny.querySelector('.font-mono');
        const ticker = tickerNode ? tickerNode.textContent.trim() : selectedChainId;
        const targetView = inferResearchActiveViewForCard(selectedCardAny);
        const targetViewLabel = researchActiveViewLabel(targetView);
        if (warningNode) warningNode.classList.remove('hidden');
        if (warningTitle) warningTitle.textContent = ticker + ' is selected in the workbench but hidden by this slice.';
        if (warningDetail) warningDetail.textContent = 'Switch this board to ' + targetViewLabel + ' to reveal the selected chain, or move to the first visible chain in the current slice.';
        if (revealButton) {
          revealButton.classList.remove('hidden');
          revealButton.textContent = 'Show In ' + targetViewLabel;
        }
      }
      if (stateNode) stateNode.textContent = 'Open the first visible chain in this slice.';
      if (detailNode) detailNode.textContent = visibleCards.length === 1
        ? 'This slice has 1 visible chain.'
        : 'This slice has ' + visibleCards.length + ' visible chains.';
      setResearchActiveNavButtonState(firstButton, true);
      setResearchActiveNavButtonState(prevButton, false);
      setResearchActiveNavButtonState(nextButton, false);
      return;
    }

    const selectedCard = visibleCards[selectedIndex];
    const tickerNode = selectedCard.querySelector('.font-mono');
    const ticker = tickerNode ? tickerNode.textContent.trim() : selectedChainId;
    if (stateNode) {
      stateNode.textContent = 'Selected in slice: ' + ticker + ' (' + (selectedIndex + 1) + ' of ' + visibleCards.length + ').';
    }
    if (detailNode) {
      const hasPrev = selectedIndex > 0;
      const hasNext = selectedIndex < visibleCards.length - 1;
      if (!hasPrev && !hasNext) {
        detailNode.textContent = 'This is the only visible chain in the current slice.';
      } else if (!hasPrev) {
        detailNode.textContent = 'You are at the first visible chain. Use Next Visible to continue.';
      } else if (!hasNext) {
        detailNode.textContent = 'You are at the last visible chain. Use Previous Visible to go back.';
      } else {
        detailNode.textContent = 'Previous / Next will step through only the visible chains in this slice.';
      }
    }
    setResearchActiveNavButtonState(firstButton, true);
    setResearchActiveNavButtonState(prevButton, selectedIndex > 0);
    setResearchActiveNavButtonState(nextButton, selectedIndex < visibleCards.length - 1);
  };

  window.openFirstVisibleResearchActiveCard = function () {
    const visibleCards = getVisibleResearchActiveCards();
    if (!visibleCards.length) return false;
    return openResearchActiveCard(visibleCards[0]);
  };

  window.stepResearchActiveCard = function (direction) {
    const visibleCards = getVisibleResearchActiveCards();
    if (!visibleCards.length) return false;
    const selectedChainId = getRememberedResearchChain();
    const currentIndex = visibleCards.findIndex(function (node) {
      return String(node.getAttribute('data-research-chain-card') || '').trim() === selectedChainId;
    });
    const delta = Number(direction) < 0 ? -1 : 1;
    let targetIndex = currentIndex + delta;
    if (currentIndex < 0) {
      targetIndex = delta < 0 ? visibleCards.length - 1 : 0;
    }
    if (targetIndex < 0 || targetIndex >= visibleCards.length) {
      return false;
    }
    return openResearchActiveCard(visibleCards[targetIndex]);
  };

  window.applyResearchActiveView = function (view) {
    const normalized = normalizeResearchActiveView(view);
    const boardRoot = document.getElementById('research-active-hypotheses');
    if (!boardRoot) return normalized;

    const boardFocus = boardRoot.querySelector('[data-active-view-section="board-focus"]');
    const operatorSection = boardRoot.querySelector('[data-active-view-section="operator"]');
    const flowSection = boardRoot.querySelector('[data-active-view-section="flow"]');
    const cards = Array.from(boardRoot.querySelectorAll('[data-active-view-card]'));

    cards.forEach(function (node) {
      const kind = String(node.getAttribute('data-active-view-card') || '').trim().toLowerCase();
      const freshness = String(node.getAttribute('data-active-view-freshness') || '').trim().toLowerCase();
      const isFocusCard = String(node.getAttribute('data-active-view-focus') || '').trim().toLowerCase() === 'true';
      let visible = true;
      if (normalized === 'focus') {
        visible = isFocusCard;
      } else if (normalized === 'operator') {
        visible = kind === 'operator';
      } else if (normalized === 'flow') {
        visible = kind === 'flow';
      } else if (normalized === 'stale') {
        visible = freshness === 'stale';
      }
      node.classList.toggle('hidden', !visible);
    });

    boardRoot.querySelectorAll('[data-active-view-button]').forEach(function (node) {
      const buttonView = normalizeResearchActiveView(node.getAttribute('data-active-view-button'));
      const active = buttonView === normalized;
      node.setAttribute('aria-pressed', active ? 'true' : 'false');
      node.classList.toggle('bg-blue-600', active);
      node.classList.toggle('text-white', active);
      node.classList.toggle('border-blue-500', active);
      node.classList.toggle('bg-white', !active);
      node.classList.toggle('text-gray-700', !active);
      node.classList.toggle('border-gray-300', !active);
    });

    boardRoot.querySelectorAll('[data-active-view-detail]').forEach(function (node) {
      const detail = String(node.getAttribute('data-active-view-detail') || '').trim().toLowerCase();
      let visible = true;
      if (detail === 'top-summary') {
        visible = normalized === 'all';
      } else if (detail === 'operator-summary') {
        visible = normalized === 'all' || normalized === 'operator';
      } else if (detail === 'flow-summary') {
        visible = normalized === 'all' || normalized === 'flow';
      } else if (detail === 'operator-focus') {
        visible = normalized === 'all' || normalized === 'operator' || normalized === 'focus';
      } else if (detail === 'flow-focus') {
        visible = normalized === 'all' || normalized === 'flow' || normalized === 'focus';
      }
      if (normalized === 'stale' && detail !== 'top-summary') {
        visible = false;
      }
      node.classList.toggle('hidden', !visible);
    });

    function countVisibleCards(kind) {
      return cards.filter(function (node) {
        return String(node.getAttribute('data-active-view-card') || '').trim().toLowerCase() === kind && !node.classList.contains('hidden');
      }).length;
    }

    const visibleOperatorCards = countVisibleCards('operator');
    const visibleFlowCards = countVisibleCards('flow');
    const visibleTotalCards = visibleOperatorCards + visibleFlowCards;

    if (boardFocus) {
      const source = String(boardFocus.getAttribute('data-board-focus-source') || '').trim().toLowerCase();
      const visible = normalized === 'all'
        || normalized === 'focus'
        || (normalized === 'operator' && source === 'operator')
        || (normalized === 'flow' && source === 'flow');
      boardFocus.classList.toggle('hidden', !visible);
    }

    if (operatorSection) {
      const visible = normalized === 'all'
        || normalized === 'operator'
        || ((normalized === 'focus' || normalized === 'stale') && visibleOperatorCards > 0);
      operatorSection.classList.toggle('hidden', !visible);
    }

    if (flowSection) {
      const visible = normalized === 'all'
        || normalized === 'flow'
        || ((normalized === 'focus' || normalized === 'stale') && visibleFlowCards > 0);
      flowSection.classList.toggle('hidden', !visible);
    }

    const banner = boardRoot.querySelector('[data-active-view-banner]');
    const bannerTitle = boardRoot.querySelector('[data-active-view-banner-title]');
    const bannerDetail = boardRoot.querySelector('[data-active-view-banner-detail]');
    if (banner) {
      const bannerMap = {
        focus: {
          title: 'Board Focus',
          detail: 'Showing only the primary and secondary board-focus chains.'
        },
        operator: {
          title: 'Operator',
          detail: 'Showing only pilot and review handoff chains.'
        },
        flow: {
          title: 'Flow',
          detail: 'Showing only chains still moving through the research flow.'
        },
        stale: {
          title: 'Stale',
          detail: 'Showing only stale chains across operator and flow sections.'
        }
      };
      const bannerCopy = bannerMap[normalized] || { title: 'All', detail: 'Showing the full board.' };
      banner.classList.toggle('hidden', normalized === 'all');
      if (bannerTitle) bannerTitle.textContent = bannerCopy.title;
      if (bannerDetail) bannerDetail.textContent = bannerCopy.detail;
    }

    const emptyState = boardRoot.querySelector('[data-active-view-empty-state]');
    const emptyTitle = boardRoot.querySelector('[data-active-view-empty-title]');
    const emptyDetail = boardRoot.querySelector('[data-active-view-empty-detail]');
    if (emptyState) {
      const emptyMap = {
        focus: {
          title: 'No board-focus chains are available.',
          detail: 'Once the board has a primary or secondary recommendation, it will appear here.'
        },
        operator: {
          title: 'No operator-ready chains are waiting.',
          detail: 'Pilot and review handoff chains will appear here when operator action is required.'
        },
        flow: {
          title: 'No in-flight flow chains are visible.',
          detail: 'Formation, challenge, experiment, and follow-up lanes will appear here when research is moving.'
        },
        stale: {
          title: 'No stale chains are on the board.',
          detail: 'This view only surfaces chains that have gone stale across operator and flow sections.'
        }
      };
      const emptyCopy = emptyMap[normalized] || {
        title: 'No chains match this view yet.',
        detail: 'Switch back to All to see the full board.'
      };
      const showEmpty = normalized !== 'all' && visibleTotalCards === 0;
      emptyState.classList.toggle('hidden', !showEmpty);
      if (emptyTitle) emptyTitle.textContent = emptyCopy.title;
      if (emptyDetail) emptyDetail.textContent = emptyCopy.detail;
    }

    window.updateResearchActiveViewSelectionState();

    return normalized;
  };

  window.setResearchActiveView = function (view) {
    const currentActiveView = getRememberedResearchActiveView();
    const normalized = window.rememberResearchActiveView(view);
    window.applyResearchActiveView(normalized);
    if (normalized === currentActiveView) {
      return false;
    }
    window.refreshResearchFocusRibbon(getRememberedResearchChain(), { suppressAutoSync: true });
    window.refreshResearchOperatorOutput();
    return true;
  };

  window.setResearchQueueAndActiveView = function (lane, view, refreshWorkbench) {
    const shouldRefreshWorkbench = refreshWorkbench !== false;
    const currentQueueLane = getRememberedResearchQueueLane();
    const currentActiveView = getRememberedResearchActiveView();
    const laneValue = String(lane || '').trim();
    const inferredView = laneValue ? inferResearchActiveViewFromQueueLane(laneValue) : '';
    const viewValue = String(view || inferredView || '').trim();
    let normalizedLane = '';
    let normalizedView = '';
    if (laneValue) {
      normalizedLane = window.rememberResearchQueueLane(laneValue);
    }
    if (viewValue) {
      normalizedView = window.rememberResearchActiveView(viewValue);
    }
    if (normalizedLane) {
      window.applyResearchQueueLane(normalizedLane);
    }
    if (normalizedView) {
      window.applyResearchActiveView(normalizedView);
    }
    const laneChanged = !!normalizedLane && normalizedLane !== currentQueueLane;
    const viewChanged = !!normalizedView && normalizedView !== currentActiveView;
    if (shouldRefreshWorkbench && (laneChanged || viewChanged)) {
      window.refreshResearchFocusRibbon(getRememberedResearchChain(), { suppressAutoSync: true });
      window.refreshResearchOperatorOutput();
    }
    return laneChanged || viewChanged;
  };

  window.restoreResearchActiveView = function () {
    return window.applyResearchActiveView(getRememberedResearchActiveView());
  };

  window.applyResearchQueueLane = function (lane) {
    const normalized = normalizeResearchQueueLane(lane);
    const queueRoot = document.getElementById('research-alerts');
    if (!queueRoot) return normalized;

    queueRoot.querySelectorAll('[data-queue-lane-section]').forEach(function (node) {
      const sectionLane = normalizeResearchQueueLane(node.getAttribute('data-queue-lane'));
      const visible = normalized === 'all' || sectionLane === normalized;
      node.classList.toggle('hidden', !visible);
    });

    queueRoot.querySelectorAll('[data-queue-lane-button]').forEach(function (node) {
      const buttonLane = normalizeResearchQueueLane(node.getAttribute('data-queue-lane-button'));
      const active = buttonLane === normalized;
      node.setAttribute('aria-pressed', active ? 'true' : 'false');
      node.classList.toggle('bg-blue-600', active);
      node.classList.toggle('text-white', active);
      node.classList.toggle('border-blue-500', active);
      node.classList.toggle('bg-white', !active);
      node.classList.toggle('text-gray-700', !active);
      node.classList.toggle('border-gray-300', !active);
    });

    return normalized;
  };

  window.setResearchQueueLane = function (lane) {
    const currentQueueLane = getRememberedResearchQueueLane();
    const normalized = window.rememberResearchQueueLane(lane);
    window.applyResearchQueueLane(normalized);
    if (normalized === currentQueueLane) {
      return false;
    }
    window.refreshResearchFocusRibbon(getRememberedResearchChain(), { suppressAutoSync: true });
    window.refreshResearchOperatorOutput();
    return true;
  };

  window.restoreResearchQueueLane = function () {
    return window.applyResearchQueueLane(getRememberedResearchQueueLane());
  };

  window.refreshResearchAlerts = function () {
    if (!window.htmx || window.location.pathname !== '/research') return;
    const alertsTarget = document.getElementById('research-alerts');
    if (!alertsTarget) return;
    htmx.ajax('GET', alertsTarget.getAttribute('hx-get'), {
      target: '#research-alerts',
      swap: 'innerHTML'
    });
  };

  window.refreshResearchOperatorOutput = function () {
    if (!window.htmx || window.location.pathname !== '/research') return;
    const workbenchTarget = document.getElementById('research-operator-output');
    if (!workbenchTarget) return;
    htmx.ajax('GET', workbenchTarget.getAttribute('hx-get'), {
      target: '#research-operator-output',
      swap: 'innerHTML'
    });
  };

  function reloadResearchWorkbenchShell() {
    if (!window.htmx || window.location.pathname !== '/research') return;
    const focusTarget = document.getElementById('research-focus-ribbon');
    const alertsTarget = document.getElementById('research-alerts');
    const chainTarget = document.getElementById('research-artifact-chain-viewer');
    const workbenchTarget = document.getElementById('research-operator-output');
    if (focusTarget) {
      htmx.ajax('GET', focusTarget.getAttribute('hx-get'), {
        target: '#research-focus-ribbon',
        swap: 'innerHTML'
      });
    }
    if (alertsTarget) window.refreshResearchAlerts();
    if (chainTarget) {
      htmx.ajax('GET', chainTarget.getAttribute('hx-get'), {
        target: '#research-artifact-chain-viewer',
        swap: 'innerHTML'
      });
    }
    if (workbenchTarget) {
      window.refreshResearchOperatorOutput();
    }
  }

  window.syncResearchQueueWithFocusRibbon = function () {
    const ribbonRoot = document.getElementById('research-focus-ribbon');
    if (!ribbonRoot) return '';
    if (String(ribbonRoot.getAttribute('data-skip-focus-auto-sync') || '').trim().toLowerCase() === 'true') {
      return '';
    }
    const focusCard = ribbonRoot.querySelector('[data-focus-queue-lane]');
    if (!focusCard) return '';
    const autoSync = String(focusCard.getAttribute('data-auto-queue-sync') || '').trim().toLowerCase() === 'true';
    if (!autoSync) return '';
    const focusChainId = String(focusCard.getAttribute('data-focus-chain-id') || '').trim();
    const rememberedChainId = getRememberedResearchChain();
    if (focusChainId && rememberedChainId && focusChainId !== rememberedChainId) {
      return '';
    }
    const lane = normalizeResearchQueueLane(focusCard.getAttribute('data-focus-queue-lane'));
    const activeView = normalizeResearchActiveView(
      focusCard.getAttribute('data-focus-active-view') || inferResearchActiveViewFromQueueLane(lane)
    );
    const changed = window.setResearchQueueAndActiveView(lane, activeView, false);
    if (!changed) {
      return '';
    }
    window.refreshResearchAlerts();
    window.refreshResearchOperatorOutput();
    return lane;
  };

  window.refreshResearchFocusRibbon = function (chainId, options) {
    if (!window.htmx) return;
    const ribbonTarget = document.getElementById('research-focus-ribbon');
    if (!ribbonTarget) return;
    const normalized = String(chainId || '').trim();
    const suppressAutoSync = !!(options && options.suppressAutoSync);
    const requestPath = buildResearchRequestPath('/fragments/research/focus-ribbon', {
      chain_id: normalized,
      queue_lane: getRememberedResearchQueueLane() !== 'all' ? getRememberedResearchQueueLane() : '',
      active_view: getRememberedResearchActiveView() !== 'all' ? getRememberedResearchActiveView() : '',
      suppress_auto_sync: suppressAutoSync ? '1' : ''
    });
    htmx.ajax('GET', requestPath, {
      target: '#research-focus-ribbon',
      swap: 'innerHTML'
    });
  };

  window.syncResearchWorkbench = function (chainId, lane, view) {
    if (!chainId || !window.htmx) return;
    if (lane || view) {
      window.setResearchQueueAndActiveView(lane || '', view || '', false);
    }
    rememberResearchChain(chainId);
    window.applyResearchSelectedChain(chainId);
    window.refreshResearchFocusRibbon(chainId);
    window.refreshResearchOperatorOutput();
  };

  window.refreshResearchChainViewer = function (chainId, lane, view) {
    if (!chainId || !window.htmx) return;
    if (lane || view) {
      window.setResearchQueueAndActiveView(lane || '', view || '', false);
    }
    rememberResearchChain(chainId);
    window.applyResearchSelectedChain(chainId);
    window.refreshResearchFocusRibbon(chainId);
    htmx.ajax('GET', '/fragments/research/artifact-chain/' + encodeURIComponent(chainId), {
      target: '#research-artifact-chain-viewer',
      swap: 'innerHTML'
    });
  };

  window.applyResearchSelectedChain = function (chainId) {
    const selected = String(chainId || '').trim();
    document.querySelectorAll('[data-research-chain-card]').forEach(function (node) {
      const nodeChainId = String(node.getAttribute('data-research-chain-card') || '').trim();
      const isSelected = !!selected && nodeChainId === selected;
      node.setAttribute('data-selected', isSelected ? 'true' : 'false');
      node.setAttribute('aria-current', isSelected ? 'true' : 'false');
      node.classList.toggle('border-blue-400', isSelected);
      node.classList.toggle('bg-blue-50', isSelected);
      node.classList.toggle('ring-2', isSelected);
      node.classList.toggle('ring-blue-200', isSelected);
      node.classList.toggle('shadow-md', isSelected);
      node.classList.toggle('border-gray-200', !isSelected && !node.classList.contains('border-amber-200') && !node.classList.contains('border-emerald-200') && !node.classList.contains('border-purple-200'));
      node.querySelectorAll('[data-selected-chain-badge]').forEach(function (badge) {
        badge.classList.toggle('hidden', !isSelected);
      });
    });
    window.updateResearchActiveViewSelectionState();
  };

  window.restoreResearchSelectedChain = function () {
    const chainId = getRememberedResearchChain();
    window.applyResearchSelectedChain(chainId);
    return chainId;
  };

  window.clearResearchSelection = function () {
    clearRememberedResearchChain();
    window.rememberResearchQueueLane('all');
    window.rememberResearchActiveView('all');
    window.applyResearchQueueLane('all');
    window.applyResearchActiveView('all');
    window.applyResearchSelectedChain('');
    reloadResearchWorkbenchShell();
    return true;
  };

  window.returnResearchToQueue = function (lane, view) {
    const normalized = window.rememberResearchQueueLane(lane || getRememberedResearchQueueLane());
    const activeView = normalizeResearchActiveView(view || inferResearchActiveViewFromQueueLane(normalized));
    window.rememberResearchActiveView(activeView);
    clearRememberedResearchChain();
    window.applyResearchQueueLane(normalized);
    window.applyResearchActiveView(activeView);
    window.applyResearchSelectedChain('');
    reloadResearchWorkbenchShell();
    return true;
  };

  window.restoreResearchWorkbenchState = function () {
    if (!window.htmx) return;
    if (window.location.pathname !== '/research') return;
    const chainId = getRememberedResearchChain();
    const chainTarget = document.getElementById('research-artifact-chain-viewer');
    const focusTarget = document.getElementById('research-focus-ribbon');
    const workbenchTarget = document.getElementById('research-operator-output');
    if (!chainId || !chainTarget || !workbenchTarget) return;
    const initialChainTargetsMatch = [focusTarget, chainTarget, workbenchTarget]
      .filter(Boolean)
      .every(function (node) {
        return String(node.getAttribute('data-initial-research-chain') || '').trim() === chainId;
      });
    if (initialChainTargetsMatch) {
      window.applyResearchSelectedChain(chainId);
      return;
    }
    const rememberedLane = getRememberedResearchQueueLane();
    const rememberedView = getRememberedResearchActiveView();
    window.refreshResearchChainViewer(chainId, rememberedLane, rememberedView);
    window.refreshResearchOperatorOutput();
  };

  document.body.addEventListener('htmx:afterRequest', function (event) {
    const requestPath = event && event.detail && event.detail.pathInfo
      ? event.detail.pathInfo.requestPath || ''
      : '';
    if (requestPath.startsWith('/api/actions/')) {
      refreshCommonPanels();
    }
  });

  document.body.addEventListener('htmx:afterSwap', function (event) {
    const target = event && event.detail ? event.detail.target : null;
    if (target && target.id === 'research-alerts') {
      window.restoreResearchQueueLane();
    }
    if (target && target.id === 'research-active-hypotheses') {
      window.restoreResearchActiveView();
    }
    if (target && target.id === 'research-focus-ribbon') {
      window.syncResearchQueueWithFocusRibbon();
    }
    if (target && target.id && target.id.startsWith('research-')) {
      window.restoreResearchSelectedChain();
    }
  });

  document.body.addEventListener('htmx:configRequest', function (event) {
    var elt = event && event.detail ? event.detail.elt : null;
    if (!elt) return;
    if (elt.id === 'research-active-hypotheses') {
      var activeView = getRememberedResearchActiveView();
      if (activeView && activeView !== 'all') {
        event.detail.parameters['active_view'] = activeView;
      }
      var chainId = getRememberedResearchChain();
      if (chainId) {
        event.detail.parameters['chain_id'] = chainId;
      }
    }
    if (elt.id === 'research-alerts') {
      var queueLane = getRememberedResearchQueueLane();
      if (queueLane && queueLane !== 'all') {
        event.detail.parameters['queue_lane'] = queueLane;
      }
      var chainId2 = getRememberedResearchChain();
      if (chainId2) {
        event.detail.parameters['chain_id'] = chainId2;
      }
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
    { label: 'Start Scheduler', kind: 'post', url: '/api/actions/scheduler-start', body: {} },
    { label: 'Stop Scheduler', kind: 'post', url: '/api/actions/scheduler-stop', body: {} },
    { label: 'Start Dispatcher', kind: 'post', url: '/api/actions/dispatcher-start', body: {} },
    { label: 'Stop Dispatcher', kind: 'post', url: '/api/actions/dispatcher-stop', body: {} },
    { label: 'Run Daily DAG', kind: 'post', url: '/api/actions/run-daily-dag', body: {}, confirm: 'Run the full daily trading DAG now?' },
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
      empty.className = 'px-2 py-1.5 rounded text-xs text-gray-400';
      empty.textContent = 'No matching commands';
      paletteList.appendChild(empty);
      return;
    }
    visible.forEach((command, index) => {
      const li = document.createElement('li');
      const isActive = index === activeIndex;
      li.className = 'px-2 py-1.5 rounded cursor-pointer text-xs ' +
        (isActive ? 'bg-gray-100 text-gray-900' : 'text-gray-700 hover:bg-gray-100');
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
      setActionMessage("<div class='bg-red-500/10 text-red-400 border border-red-500/30 rounded-lg px-3 py-2 text-sm'>Command failed to execute.</div>");
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

  var openButtonSidebar = document.getElementById('command-open-sidebar');
  if (openButtonSidebar) {
    openButtonSidebar.addEventListener('click', function () {
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

  prepareResearchInitialTargets();

  if (window.location.pathname === '/research') {
    window.setTimeout(function () {
      window.restoreResearchWorkbenchState();
      window.restoreResearchQueueLane();
      window.restoreResearchActiveView();
      window.restoreResearchSelectedChain();
    }, 500);
  }
})();
