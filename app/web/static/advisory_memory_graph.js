// Advisory memory graph renderer for the Advisory page.

(function () {
  const GRAPH_HEIGHT = 320;
  const TYPE_COLORS = {
    decision: '#2563eb',
    observation: '#059669',
    preference: '#7c3aed',
    bookmark: '#d97706',
    fact: '#0f766e',
    goal: '#dc2626',
    note: '#6b7280'
  };
  const EDGE_STYLES = {
    superseded_by: { color: '#d97706', opacity: 0.8, width: 2.2, dasharray: '6 3' },
    same_session: { color: '#6b7280', opacity: 0.45, width: 1.7, dasharray: '' },
    shared_ticker: { color: '#2563eb', opacity: 0.55, width: 1.9, dasharray: '' },
    shared_tag: { color: '#059669', opacity: 0.35, width: 1.4, dasharray: '' },
    related: { color: '#9ca3af', opacity: 0.3, width: 1.2, dasharray: '' }
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function truncate(value, maxLength) {
    const text = String(value || '').trim();
    if (!text) return 'Untitled';
    return text.length > maxLength ? text.slice(0, Math.max(1, maxLength - 1)) + '…' : text;
  }

  function formatDate(value) {
    const text = String(value || '').trim();
    if (!text) return 'Unknown';
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) return text.slice(0, 10);
    return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function formatConfidence(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return '0.00';
    return number.toFixed(2);
  }

  function getNodeId(nodeRef) {
    if (!nodeRef) return '';
    if (typeof nodeRef === 'string') return nodeRef;
    return String(nodeRef.id || '');
  }

  function getNodeColor(memoryType) {
    return TYPE_COLORS[String(memoryType || '').toLowerCase()] || TYPE_COLORS.note;
  }

  function getEdgeStyle(edge) {
    const key = edge && edge.kind ? edge.kind : 'related';
    return EDGE_STYLES[key] || EDGE_STYLES.related;
  }

  function relationshipLabel(reason) {
    const type = String(reason && reason.type || '');
    const value = String(reason && reason.value || '').trim();
    if (type === 'superseded_by') return 'Superseded';
    if (type === 'same_session') return 'Same session';
    if (type === 'shared_ticker') return value ? 'Ticker: ' + value : 'Shared ticker';
    if (type === 'shared_tag') return value ? 'Tag: ' + value : 'Shared tag';
    return 'Related';
  }

  function buildAdjacency(graph) {
    const adjacency = {};
    (graph.nodes || []).forEach(function (node) {
      adjacency[node.id] = [];
    });
    (graph.edges || []).forEach(function (edge) {
      const sourceId = getNodeId(edge.source);
      const targetId = getNodeId(edge.target);
      if (!adjacency[sourceId]) adjacency[sourceId] = [];
      if (!adjacency[targetId]) adjacency[targetId] = [];
      adjacency[sourceId].push({ nodeId: targetId, edge: edge });
      adjacency[targetId].push({ nodeId: sourceId, edge: edge });
    });
    return adjacency;
  }

  function getGraphShells(root) {
    if (!root) return [];
    const shells = [];
    if (root.matches && root.matches('[data-advisory-memory-graph]')) {
      shells.push(root);
    }
    if (root.querySelectorAll) {
      root.querySelectorAll('[data-advisory-memory-graph]').forEach(function (shell) {
        shells.push(shell);
      });
    }
    return shells;
  }

  function setStatus(shell, text, metaText) {
    const status = shell.querySelector('[data-graph-status]');
    const meta = shell.querySelector('[data-graph-meta]');
    if (status) status.textContent = text || '';
    if (meta) meta.textContent = metaText || '';
  }

  function setError(shell, text) {
    const error = shell.querySelector('[data-graph-error]');
    if (!error) return;
    if (text) {
      error.textContent = text;
      error.classList.remove('hidden');
    } else {
      error.textContent = '';
      error.classList.add('hidden');
    }
  }

  function renderDetailPanel(shell, node, graph, adjacency) {
    const detail = shell.querySelector('[data-graph-detail]');
    if (!detail) return;
    if (!node) {
      detail.innerHTML = '<div class="text-[10px] font-mono text-gray-400">Select a node to inspect its topic, summary, confidence, tags, and related memories.</div>';
      return;
    }
    const nodeMap = {};
    (graph.nodes || []).forEach(function (item) {
      nodeMap[item.id] = item;
    });
    const relatedItems = (adjacency[node.id] || []).map(function (entry) {
      return {
        node: nodeMap[entry.nodeId],
        edge: entry.edge
      };
    }).filter(function (entry) {
      return !!entry.node;
    }).sort(function (left, right) {
      return (right.node.degree || 0) - (left.node.degree || 0);
    });
    const tags = (node.tags || []).length ? node.tags.map(function (tag) {
      return '<span class="px-1.5 py-0.5 rounded bg-gray-200 text-gray-600">' + escapeHtml(tag) + '</span>';
    }).join(' ') : '<span class="text-gray-400">none</span>';
    const related = relatedItems.length ? relatedItems.map(function (entry) {
      const reasons = (entry.edge.reasons || []).map(relationshipLabel).join(' · ');
      return '<button type="button" data-related-node-id="' + escapeHtml(entry.node.id) + '" class="w-full text-left px-2 py-1 rounded border border-gray-200 bg-white hover:bg-gray-100">' +
        '<div class="flex items-center gap-2">' +
        '<span class="inline-block w-2 h-2 rounded-full" style="background:' + escapeHtml(getNodeColor(entry.node.memory_type)) + '"></span>' +
        '<span class="text-[10px] font-semibold text-gray-700">' + escapeHtml(truncate(entry.node.topic || entry.node.label, 40)) + '</span>' +
        '</div>' +
        '<div class="text-[9px] text-gray-400 mt-0.5">' + escapeHtml(reasons) + '</div>' +
        '</button>';
    }).join('') : '<div class="text-[10px] font-mono text-gray-400">No connected memories.</div>';

    detail.innerHTML =
      '<div class="space-y-1.5">' +
        '<div class="flex items-center gap-2">' +
          '<span class="inline-block w-2.5 h-2.5 rounded-full" style="background:' + escapeHtml(getNodeColor(node.memory_type)) + '"></span>' +
          '<span class="text-[9px] font-semibold uppercase tracking-wide text-gray-500">' + escapeHtml(node.memory_type || 'note') + '</span>' +
          '<span class="text-[9px] font-mono text-gray-300 ml-auto">' + escapeHtml(formatDate(node.created_at)) + '</span>' +
        '</div>' +
        '<div>' +
          '<div class="text-[11px] font-semibold text-gray-800">' + escapeHtml(node.topic || 'General') + '</div>' +
          '<div class="text-[10px] font-mono text-gray-600 mt-0.5">' + escapeHtml(node.summary || '') + '</div>' +
          (node.detail ? '<div class="text-[9px] font-mono text-gray-400 mt-1">' + escapeHtml(node.detail) + '</div>' : '') +
        '</div>' +
        '<div class="grid grid-cols-2 gap-1 text-[9px] font-mono text-gray-500">' +
          '<div class="rounded bg-white border border-gray-200 px-2 py-1">confidence: <span class="text-gray-700">' + escapeHtml(formatConfidence(node.confidence)) + '</span></div>' +
          '<div class="rounded bg-white border border-gray-200 px-2 py-1">degree: <span class="text-gray-700">' + escapeHtml(String(node.degree || 0)) + '</span></div>' +
        '</div>' +
        '<div class="text-[9px] font-mono text-gray-500">tags: ' + tags + '</div>' +
        '<div class="space-y-1">' +
          '<div class="text-[9px] font-semibold uppercase tracking-wide text-gray-400">Related memories</div>' +
          '<div class="space-y-1">' + related + '</div>' +
        '</div>' +
      '</div>';
  }

  function buildCanvasEmptyState(canvas, text) {
    if (!canvas) return;
    canvas.innerHTML = '<div class="w-full h-full flex items-center justify-center text-[10px] font-mono text-gray-400">' + escapeHtml(text) + '</div>';
  }

  function applySelection(controller) {
    if (!controller) return;
    const adjacency = controller.adjacency || {};
    const selectedId = controller.selectedNodeId || '';
    const connectedIds = new Set([selectedId]);
    (adjacency[selectedId] || []).forEach(function (entry) {
      connectedIds.add(entry.nodeId);
    });

    controller.linkSelection
      .attr('stroke-opacity', function (edge) {
        const base = getEdgeStyle(edge).opacity;
        if (!selectedId) return base;
        const sourceId = getNodeId(edge.source);
        const targetId = getNodeId(edge.target);
        return sourceId === selectedId || targetId === selectedId ? Math.min(0.95, base + 0.25) : 0.08;
      })
      .attr('stroke-width', function (edge) {
        const base = getEdgeStyle(edge).width;
        if (!selectedId) return base;
        const sourceId = getNodeId(edge.source);
        const targetId = getNodeId(edge.target);
        return sourceId === selectedId || targetId === selectedId ? base + 1.1 : base;
      });

    controller.nodeSelection.select('circle')
      .attr('opacity', function (node) {
        if (!selectedId) return 1;
        return connectedIds.has(node.id) ? 1 : 0.4;
      })
      .attr('stroke', function (node) {
        return node.id === selectedId ? '#111827' : '#ffffff';
      })
      .attr('stroke-width', function (node) {
        return node.id === selectedId ? 2.4 : 1.4;
      })
      .attr('r', function (node) {
        const radius = 6 + Math.max(0, Math.min(6, Number(node.confidence || 0) * 4));
        return node.id === selectedId ? radius + 1.5 : radius;
      });

    controller.nodeSelection.select('text')
      .attr('opacity', function (node) {
        if (!selectedId) return 0.9;
        return connectedIds.has(node.id) ? 1 : 0.35;
      })
      .attr('font-weight', function (node) {
        return node.id === selectedId ? '700' : '500';
      });
  }

  function selectNode(shell, nodeId) {
    const controller = shell.__advisoryMemoryGraph;
    if (!controller) return;
    controller.selectedNodeId = nodeId || '';
    const selectedNode = (controller.graph.nodes || []).find(function (node) {
      return node.id === controller.selectedNodeId;
    }) || null;
    renderDetailPanel(shell, selectedNode, controller.graph, controller.adjacency);
    applySelection(controller);
  }

  function destroyGraph(shell) {
    const controller = shell && shell.__advisoryMemoryGraph;
    if (shell && shell.__advisoryMemoryGraphRequest) {
      shell.__advisoryMemoryGraphRequest.abort();
      delete shell.__advisoryMemoryGraphRequest;
    }
    if (!controller) return;
    if (controller.simulation && typeof controller.simulation.stop === 'function') {
      controller.simulation.stop();
    }
    const canvas = shell.querySelector('[data-graph-canvas]');
    if (canvas) {
      canvas.innerHTML = '';
    }
    delete shell.__advisoryMemoryGraph;
  }

  function renderGraph(shell, payload) {
    if (typeof d3 === 'undefined') {
      setStatus(shell, 'D3 failed to load', '');
      setError(shell, 'Graph renderer unavailable because D3.js did not load.');
      return;
    }
    destroyGraph(shell);

    const graph = {
      nodes: Array.isArray(payload && payload.nodes) ? payload.nodes.slice() : [],
      edges: Array.isArray(payload && payload.edges) ? payload.edges.slice() : [],
      meta: payload && payload.meta ? payload.meta : {}
    };
    const canvas = shell.querySelector('[data-graph-canvas]');
    if (!canvas) return;

    if (!graph.nodes.length) {
      setStatus(shell, 'No memories available', '');
      setError(shell, '');
      renderDetailPanel(shell, null, graph, {});
      buildCanvasEmptyState(canvas, 'No active memories to graph yet.');
      return;
    }

    const width = Math.max(canvas.clientWidth || 0, 280);
    const height = Math.max(canvas.clientHeight || 0, GRAPH_HEIGHT);
    canvas.innerHTML = '';

    const svg = d3.select(canvas)
      .append('svg')
      .attr('width', '100%')
      .attr('height', height)
      .attr('viewBox', '0 0 ' + width + ' ' + height)
      .attr('class', 'block');

    const zoomLayer = svg.append('g');
    svg.call(
      d3.zoom()
        .scaleExtent([0.5, 3])
        .on('zoom', function (event) {
          zoomLayer.attr('transform', event.transform);
        })
    );

    const linkSelection = zoomLayer.append('g')
      .attr('stroke-linecap', 'round')
      .selectAll('line')
      .data(graph.edges)
      .join('line')
      .attr('stroke', function (edge) { return getEdgeStyle(edge).color; })
      .attr('stroke-opacity', function (edge) { return getEdgeStyle(edge).opacity; })
      .attr('stroke-width', function (edge) { return getEdgeStyle(edge).width; })
      .attr('stroke-dasharray', function (edge) { return getEdgeStyle(edge).dasharray; });

    const nodeSelection = zoomLayer.append('g')
      .selectAll('g')
      .data(graph.nodes)
      .join('g')
      .attr('cursor', 'pointer');

    nodeSelection.append('circle')
      .attr('r', function (node) {
        return 6 + Math.max(0, Math.min(6, Number(node.confidence || 0) * 4));
      })
      .attr('fill', function (node) { return getNodeColor(node.memory_type); })
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1.4);

    nodeSelection.append('text')
      .text(function (node) { return truncate(node.label || node.topic || node.summary, 20); })
      .attr('x', 10)
      .attr('y', 3)
      .attr('font-size', 9)
      .attr('font-family', 'JetBrains Mono, monospace')
      .attr('font-weight', 500)
      .attr('fill', '#4b5563')
      .attr('stroke', '#f9fafb')
      .attr('stroke-width', 2.4)
      .style('paint-order', 'stroke');

    const simulation = d3.forceSimulation(graph.nodes)
      .force('link', d3.forceLink(graph.edges).id(function (node) { return node.id; }).distance(function (edge) {
        return Math.max(48, 90 - (Number(edge.weight || 1) * 10));
      }))
      .force('charge', d3.forceManyBody().strength(-180))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide().radius(function (node) {
        return 18 + Math.max(0, Math.min(6, Number(node.confidence || 0) * 4));
      }));

    nodeSelection.call(
      d3.drag()
        .on('start', function (event, node) {
          if (!event.active) simulation.alphaTarget(0.2).restart();
          node.fx = node.x;
          node.fy = node.y;
        })
        .on('drag', function (event, node) {
          node.fx = event.x;
          node.fy = event.y;
        })
        .on('end', function (event, node) {
          if (!event.active) simulation.alphaTarget(0);
          node.fx = null;
          node.fy = null;
        })
    );

    simulation.on('tick', function () {
      linkSelection
        .attr('x1', function (edge) { return edge.source.x; })
        .attr('y1', function (edge) { return edge.source.y; })
        .attr('x2', function (edge) { return edge.target.x; })
        .attr('y2', function (edge) { return edge.target.y; });

      nodeSelection.attr('transform', function (node) {
        return 'translate(' + node.x + ',' + node.y + ')';
      });
    });

    nodeSelection.on('click', function (event, node) {
      event.stopPropagation();
      selectNode(shell, node.id);
    });

    svg.on('click', function () {
      selectNode(shell, '');
    });

    const adjacency = buildAdjacency(graph);
    shell.__advisoryMemoryGraph = {
      graph: graph,
      adjacency: adjacency,
      simulation: simulation,
      nodeSelection: nodeSelection,
      linkSelection: linkSelection,
      selectedNodeId: '',
      selectNode: function (nodeId) {
        selectNode(shell, nodeId);
      }
    };

    const meta = graph.meta || {};
    setStatus(
      shell,
      'Graph ready',
      String(meta.node_count || graph.nodes.length) + ' nodes / ' + String(meta.edge_count || graph.edges.length) + ' edges'
    );
    setError(shell, '');
    if (graph.nodes.length) {
      selectNode(shell, graph.nodes[0].id);
    } else {
      renderDetailPanel(shell, null, graph, adjacency);
    }
  }

  function loadGraph(shell) {
    const url = shell.getAttribute('data-graph-url') || '';
    if (!url) {
      setStatus(shell, 'Graph endpoint missing', '');
      setError(shell, 'No graph endpoint is configured for this panel.');
      return;
    }
    if (shell.__advisoryMemoryGraphRequest) {
      shell.__advisoryMemoryGraphRequest.abort();
    }
    const controller = new AbortController();
    shell.__advisoryMemoryGraphRequest = controller;
    setStatus(shell, 'Loading memory graph...', '');
    setError(shell, '');

    fetch(url, {
      headers: { Accept: 'application/json' },
      signal: controller.signal
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('HTTP ' + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        if (controller.signal.aborted) return;
        if (!payload || payload.ok === false) {
          throw new Error(payload && payload.error ? payload.error : 'Unable to load memory graph');
        }
        renderGraph(shell, payload);
      })
      .catch(function (error) {
        if (controller.signal.aborted) return;
        destroyGraph(shell);
        setStatus(shell, 'Graph unavailable', '');
        setError(shell, String(error && error.message || error || 'Unable to load memory graph'));
        buildCanvasEmptyState(shell.querySelector('[data-graph-canvas]'), 'Unable to load graph.');
        renderDetailPanel(shell, null, { nodes: [], edges: [] }, {});
      })
      .finally(function () {
        if (shell.__advisoryMemoryGraphRequest === controller) {
          delete shell.__advisoryMemoryGraphRequest;
        }
      });
  }

  function initShell(shell) {
    if (!shell || shell.dataset.graphReady === 'true') return;
    shell.dataset.graphReady = 'true';

    const refresh = shell.querySelector('[data-graph-refresh]');
    if (refresh) {
      refresh.addEventListener('click', function () {
        loadGraph(shell);
      });
    }

    shell.addEventListener('click', function (event) {
      const relatedButton = event.target.closest('[data-related-node-id]');
      if (!relatedButton) return;
      event.preventDefault();
      const nodeId = relatedButton.getAttribute('data-related-node-id') || '';
      selectNode(shell, nodeId);
    });

    loadGraph(shell);
  }

  function initAll(root) {
    getGraphShells(root).forEach(initShell);
  }

  function destroyAll(root) {
    getGraphShells(root).forEach(destroyGraph);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);
  });

  document.addEventListener('htmx:afterSwap', function (event) {
    initAll(event && event.detail ? event.detail.target : document);
  });

  document.addEventListener('htmx:beforeSwap', function (event) {
    if (event && event.detail && event.detail.target) {
      destroyAll(event.detail.target);
    }
  });

  window.initAdvisoryMemoryGraphs = initAll;
})();
