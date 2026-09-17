/* Dashboard shell: loads the published JSON and renders tiles, charts,
 * table views and provenance.
 *
 * Every number on the page comes from visa/data/latest.json, which is written
 * by scripts/build_visa_stats.py. When a series could not be built the card
 * says so and names the reason — nothing is estimated, interpolated or
 * carried over to fill a gap.
 */

(function () {
  'use strict';

  var DATA_URL = 'data/latest.json';
  var DEMO_URL = 'data/sample.json';

  /* How each series is drawn. Anything not listed falls back to its shape. */
  var DISPLAY = {
    pr_by_stream:           { chart: 'stacked', wide: true },
    temp_grants_by_program: { chart: 'line', wide: true },
    citizenship_conferrals: { chart: 'line', wide: false },
    pr_by_citizenship:      { chart: 'ranked', wide: false },
    pr_by_state:            { chart: 'ranked', wide: false },
    pr_by_subclass:         { chart: 'ranked', wide: false },
    student_by_citizenship: { chart: 'ranked', wide: false }
  };

  var ORDER = [
    'pr_by_stream',
    'pr_by_citizenship',
    'pr_by_state',
    'pr_by_subclass',
    'temp_grants_by_program',
    'student_by_citizenship',
    'citizenship_conferrals'
  ];

  var esc = Charts.escapeHtml;

  document.addEventListener('DOMContentLoaded', function () {
    setupTheme();
    var demo = new URLSearchParams(location.search).has('demo');
    load(demo ? DEMO_URL : DATA_URL, demo);
  });

  function load(url, isDemo) {
    fetch(url, { cache: 'no-cache' })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(function (payload) { render(payload, isDemo); })
      .catch(function (error) { renderLoadFailure(url, error); });
  }

  /* ------------------------------------------------------------------ render */

  function render(payload, isDemo) {
    document.getElementById('demoBanner').hidden = !isDemo;
    renderFreshness(payload);
    renderRunBanner(payload);
    renderTiles(payload.headline || []);
    renderCards(payload);
    renderSources(payload.sources || []);
  }

  function renderFreshness(payload) {
    var run = payload.run || {};
    var status = run.status || 'unknown';
    var checked = payload.last_checked_at || payload.generated_at;

    document.getElementById('freshness').innerHTML = [
      field('Source', 'Australian Department of Home Affairs, via data.gov.au'),
      field('Data built', formatDateTime(payload.generated_at)),
      field('Last checked', formatDateTime(checked)),
      '<div><dt>Pipeline</dt><dd><span class="status-dot status-' + esc(status) + '"></span>' +
        esc(statusText(status, run)) + '</dd></div>'
    ].join('');
  }

  function field(label, value) {
    return '<div><dt>' + esc(label) + '</dt><dd>' + esc(value) + '</dd></div>';
  }

  function statusText(status, run) {
    var counts = run.series_available + '/' + run.series_total + ' series';
    if (status === 'ok') return 'All series current (' + counts + ')';
    if (status === 'partial') return 'Some series unavailable (' + counts + ')';
    if (status === 'failed') return 'Last refresh failed';
    return 'Awaiting first run';
  }

  function renderRunBanner(payload) {
    var run = payload.run || {};
    var banner = document.getElementById('runBanner');
    var problems = run.problems || [];

    if (run.retained_previous) {
      banner.hidden = false;
      banner.innerHTML = '<div><strong>Showing the last successful refresh.</strong> ' +
        'Today\'s run could not reach the source, so the figures below are unchanged ' +
        'from ' + esc(formatDateTime(payload.generated_at)) + ' rather than blanked.</div>';
      return;
    }

    if (!problems.length) { banner.hidden = true; return; }

    banner.hidden = false;
    banner.innerHTML = '<div><strong>' + problems.length + ' of ' + run.series_total +
      ' series could not be rebuilt.</strong> Those cards are marked below. This usually ' +
      'means Home Affairs renamed or restructured a published file.</div>';
  }

  function renderTiles(tiles) {
    var host = document.getElementById('tiles');
    if (!tiles.length) { host.innerHTML = ''; return; }

    host.innerHTML = tiles.map(function (tile) {
      var meta = [];
      if (tile.caption) meta.push(esc(tile.caption));
      if (tile.share !== undefined) meta.push((tile.share * 100).toFixed(1) + '% of total');
      if (tile.period) meta.push(esc(tile.period));

      var delta = '';
      if (tile.change_pct !== undefined) {
        var up = tile.change_pct >= 0;
        delta = ' <span class="tile-delta ' + (up ? 'up' : 'down') + '">' +
          (up ? '▲' : '▼') + ' ' + Math.abs(tile.change_pct).toFixed(1) + '%</span>' +
          ' <span>vs ' + esc(tile.compare_period || 'previous') + '</span>';
      }

      return '<article class="tile">' +
        '<p class="tile-label">' + esc(tile.label) + '</p>' +
        '<p class="tile-value">' + Charts.formatValue(tile.value) +
          '<span class="tile-unit">' + esc(tile.unit || '') + '</span></p>' +
        '<p class="tile-meta">' + meta.join(' · ') + delta + '</p>' +
        '</article>';
    }).join('');
  }

  function renderCards(payload) {
    var host = document.getElementById('cards');
    host.innerHTML = '';
    var series = payload.series || {};

    var ids = ORDER.filter(function (id) { return series[id]; })
      .concat(Object.keys(series).filter(function (id) { return ORDER.indexOf(id) === -1; }));

    if (!ids.length) {
      host.innerHTML = '<div class="card is-wide"><div class="empty">' +
        'No series have been published yet. Run ' +
        '<code>python3 scripts/build_visa_stats.py</code> or wait for the daily workflow.' +
        '</div></div>';
      return;
    }

    ids.forEach(function (id) { host.appendChild(buildCard(series[id], id)); });
  }

  function buildCard(series, id) {
    var display = DISPLAY[id] || {};
    var card = document.createElement('section');
    card.className = 'card' + (display.wide ? ' is-wide' : '');

    var head = document.createElement('div');
    head.className = 'card-head';
    head.innerHTML = '<h2>' + esc(series.title) + '</h2>';
    card.appendChild(head);

    var sub = document.createElement('p');
    sub.className = 'card-sub';
    sub.textContent = subtitleFor(series);
    card.appendChild(sub);

    if (!series.available) {
      card.insertAdjacentHTML('beforeend',
        '<div class="empty"><p>This series could not be built from the current ' +
        'published files.</p><code>' + esc(series.reason || 'unknown reason') + '</code></div>');
      return card;
    }

    var chartType = display.chart || defaultChart(series.shape);
    var categories = chartType === 'ranked' ? [] : (series.categories || []);

    if (categories.length >= 2) card.appendChild(buildLegend(categories));

    var chartHost = document.createElement('div');
    chartHost.className = 'chart-host';
    card.appendChild(chartHost);

    var tableHost = document.createElement('div');
    tableHost.className = 'table-host';
    tableHost.hidden = true;
    tableHost.innerHTML = buildTable(series, chartType);
    card.appendChild(tableHost);

    head.appendChild(buildToggle(chartHost, tableHost));

    if (series.note) {
      card.insertAdjacentHTML('beforeend',
        '<p class="tile-meta">' + esc(series.note) + '</p>');
    }

    drawChart(chartHost, series, chartType);
    return card;
  }

  function subtitleFor(series) {
    var parts = [series.subtitle];
    if (series.shape === 'ranked' && series.category_count) {
      parts.push('Top ' + Math.min(series.items.length, series.category_count) +
        ' of ' + series.category_count);
    }
    return parts.filter(Boolean).join(' · ');
  }

  function defaultChart(shape) {
    if (shape === 'ranked') return 'ranked';
    if (shape === 'time_total') return 'line';
    return 'stacked';
  }

  function drawChart(host, series, chartType) {
    if (chartType === 'ranked') {
      Charts.rankedBars(host, {
        items: series.items,
        unit: series.unit,
        ariaLabel: series.title
      });
      return;
    }

    var options = {
      periods: series.periods,
      categories: series.categories,
      matrix: series.matrix,
      unit: series.unit,
      ariaLabel: series.title
    };
    if (chartType === 'line') Charts.multiLine(host, options);
    else Charts.stackedBars(host, options);
  }

  function buildLegend(categories) {
    var legend = document.createElement('div');
    legend.className = 'legend';
    legend.innerHTML = categories.map(function (name, index) {
      return '<span class="legend-item">' +
        '<span class="legend-swatch" style="background:' + Charts.seriesColor(index) + '"></span>' +
        esc(name) + '</span>';
    }).join('');
    return legend;
  }

  /* A table view for every chart: it carries the exact values, and it is the
     documented relief for series colours that sit below 3:1 on the light
     surface. */
  function buildToggle(chartHost, tableHost) {
    var toggle = document.createElement('div');
    toggle.className = 'view-toggle';
    toggle.innerHTML =
      '<button type="button" aria-pressed="true">Chart</button>' +
      '<button type="button" aria-pressed="false">Table</button>';

    var buttons = toggle.querySelectorAll('button');
    buttons[0].addEventListener('click', function () { switchTo(true); });
    buttons[1].addEventListener('click', function () { switchTo(false); });

    function switchTo(showChart) {
      chartHost.hidden = !showChart;
      tableHost.hidden = showChart;
      buttons[0].setAttribute('aria-pressed', String(showChart));
      buttons[1].setAttribute('aria-pressed', String(!showChart));
    }

    return toggle;
  }

  function buildTable(series, chartType) {
    if (chartType === 'ranked') {
      return '<table class="data"><caption>' + esc(series.period || '') +
        '</caption><thead><tr><th>Category</th><th class="num">' +
        esc(series.unit) + '</th><th class="num">Share</th></tr></thead><tbody>' +
        series.items.map(function (item) {
          return '<tr><td>' + esc(item.label) + '</td>' +
            '<td class="num">' + Charts.formatValue(item.value) + '</td>' +
            '<td class="num">' + (item.share * 100).toFixed(1) + '%</td></tr>';
        }).join('') + '</tbody></table>';
    }

    var header = '<tr><th>Period</th>' + series.categories.map(function (name) {
      return '<th class="num">' + esc(name) + '</th>';
    }).join('') + '<th class="num">Total</th></tr>';

    var body = series.periods.map(function (period, i) {
      var total = 0;
      var cells = series.categories.map(function (_name, c) {
        var value = series.matrix[c][i] || 0;
        total += value;
        return '<td class="num">' + Charts.formatValue(value) + '</td>';
      }).join('');
      return '<tr><td>' + esc(period) + '</td>' + cells +
        '<td class="num">' + Charts.formatValue(total) + '</td></tr>';
    }).join('');

    return '<table class="data"><caption>' + esc(series.unit) +
      '</caption><thead>' + header + '</thead><tbody>' + body + '</tbody></table>';
  }

  function renderSources(sources) {
    var host = document.getElementById('sourceList');
    if (!sources.length) {
      host.innerHTML = '<li>No sources recorded yet.</li>';
      return;
    }

    host.innerHTML = sources.map(function (source) {
      var meta = [
        source.format ? source.format.toUpperCase() : null,
        source.row_count ? Charts.formatValue(source.row_count) + ' rows' : null,
        source.sheet ? 'sheet: ' + source.sheet : null,
        source.resource_updated ? 'file updated ' + formatDate(source.resource_updated) : null,
        source.dataset_updated ? 'dataset updated ' + formatDate(source.dataset_updated) : null
      ].filter(Boolean).join(' · ');

      return '<li><a href="' + esc(source.dataset_url) + '" target="_blank" rel="noopener">' +
        esc(source.title) + '</a>' +
        (source.resource_name ? ' — ' + esc(source.resource_name) : '') +
        '<div class="meta">' + esc(meta) + '</div></li>';
    }).join('');
  }

  function renderLoadFailure(url, error) {
    document.getElementById('cards').innerHTML =
      '<div class="card is-wide"><div class="empty">' +
      '<p>Could not load <code>' + esc(url) + '</code> (' + esc(error.message) + ').</p>' +
      '<p>If you opened this file directly from disk, serve the folder over HTTP ' +
      'instead: <code>python3 -m http.server</code>.</p></div></div>';
    document.getElementById('freshness').innerHTML = field('Status', 'Data file unavailable');
  }

  /* ------------------------------------------------------------------ dates */

  function formatDateTime(value) {
    if (!value) return 'never';
    var date = new Date(value);
    if (isNaN(date)) return String(value);
    return date.toLocaleString('en-AU', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function formatDate(value) {
    if (!value) return '';
    var date = new Date(value);
    if (isNaN(date)) return String(value);
    return date.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  /* ------------------------------------------------------------------ theme */

  function setupTheme() {
    var button = document.getElementById('themeToggle');
    var stored = null;
    try { stored = localStorage.getItem('visa-theme'); } catch (e) { /* private mode */ }
    if (stored) document.documentElement.setAttribute('data-theme', stored);
    label();

    button.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('visa-theme', next); } catch (e) { /* ignore */ }
      label();
    });

    function current() {
      var explicit = document.documentElement.getAttribute('data-theme');
      if (explicit) return explicit;
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function label() {
      button.textContent = current() === 'dark' ? 'Light theme' : 'Dark theme';
    }
  }
})();
