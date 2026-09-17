/* Inline-SVG chart renderers for the visa dashboard.
 *
 * Hand-rolled rather than pulled from a chart library so the marks follow one
 * spec: thin marks, 4px rounded data-ends anchored to the baseline, 2px lines,
 * a 2px surface gap between stacked fills, hairline solid gridlines, and a
 * hover layer on every form. Colour always follows the entity (its index in
 * `categories`), never its current rank, so filtering never repaints a series.
 */

(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var PALETTE_SIZE = 8;
  var CHAR_WIDTH = 6.2;      // ~11px system sans, used for layout reservations
  var MARK_RADIUS = 4;       // 8px marker diameter
  var STACK_GAP = 2;         // surface gap between stacked segments
  var CORNER = 4;            // rounded data-end radius

  var numberFormat = new Intl.NumberFormat('en-AU');
  var compactFormat = new Intl.NumberFormat('en-AU', {
    notation: 'compact',
    maximumFractionDigits: 1
  });

  function formatValue(value) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    return numberFormat.format(Math.round(value));
  }

  function formatCompact(value) {
    if (!isFinite(value)) return '—';
    return Math.abs(value) >= 10000 ? compactFormat.format(value) : numberFormat.format(value);
  }

  function seriesColor(index) {
    return 'var(--series-' + ((index % PALETTE_SIZE) + 1) + ')';
  }

  function truncate(text, max) {
    var value = String(text == null ? '' : text);
    return value.length > max ? value.slice(0, max - 1) + '…' : value;
  }

  function el(name, attrs, text) {
    var node = document.createElementNS(SVG_NS, name);
    for (var key in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, key) && attrs[key] !== null) {
        node.setAttribute(key, attrs[key]);
      }
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* Rounded-rectangle path with per-corner control, so a data-end can be
     rounded while the baseline end stays square and anchored. */
  function roundedRect(x, y, w, h, r, corners) {
    if (h <= 0 || w <= 0) return '';
    var radius = Math.max(0, Math.min(r, w / 2, h / 2));
    var tl = corners.tl ? radius : 0;
    var tr = corners.tr ? radius : 0;
    var br = corners.br ? radius : 0;
    var bl = corners.bl ? radius : 0;
    return [
      'M', x + tl, y,
      'H', x + w - tr,
      tr ? 'a' + tr + ',' + tr + ' 0 0 1 ' + tr + ',' + tr : '',
      'V', y + h - br,
      br ? 'a' + br + ',' + br + ' 0 0 1 ' + -br + ',' + br : '',
      'H', x + bl,
      bl ? 'a' + bl + ',' + bl + ' 0 0 1 ' + -bl + ',' + -bl : '',
      'V', y + tl,
      tl ? 'a' + tl + ',' + tl + ' 0 0 1 ' + tl + ',' + -tl : '',
      'Z'
    ].join(' ');
  }

  /* "Nice" axis maximum plus tick values, so gridlines land on round numbers. */
  function niceScale(max, tickCount) {
    if (!isFinite(max) || max <= 0) return { max: 1, ticks: [0, 1] };
    var rough = max / tickCount;
    var magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    var normalised = rough / magnitude;
    var step = (normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10) * magnitude;
    var top = Math.ceil(max / step) * step;
    var ticks = [];
    for (var value = 0; value <= top + step / 2; value += step) ticks.push(value);
    return { max: top, ticks: ticks };
  }

  /* ---------------------------------------------------------------- tooltip */

  function createTooltip(host) {
    var node = document.createElement('div');
    node.className = 'tooltip';
    node.setAttribute('role', 'status');
    host.appendChild(node);

    return {
      show: function (x, y, title, rows) {
        var html = '<div class="tooltip-title">' + escapeHtml(title) + '</div>';
        rows.forEach(function (row) {
          html += '<div class="tooltip-row">' +
            (row.color ? '<span class="legend-swatch" style="background:' + row.color + '"></span>' : '') +
            '<span class="tooltip-name">' + escapeHtml(row.name) + '</span>' +
            '<span class="tooltip-value">' + escapeHtml(row.value) + '</span>' +
            '</div>';
        });
        node.innerHTML = html;
        node.style.left = x + 'px';
        node.style.top = y + 'px';
        node.classList.add('is-visible');
      },
      hide: function () { node.classList.remove('is-visible'); }
    };
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  /* Re-render on container resize so the chart stays readable at any width. */
  function mount(host, draw) {
    host.innerHTML = '';
    var tooltip = createTooltip(host);
    var frame = null;

    function render() {
      var width = host.clientWidth || host.parentNode.clientWidth || 640;
      Array.prototype.slice.call(host.querySelectorAll('svg')).forEach(function (old) {
        old.remove();
      });
      host.classList.remove('is-hovering');
      tooltip.hide();
      var svg = draw(Math.max(280, width), tooltip, host);
      host.appendChild(svg);
    }

    render();

    if (typeof ResizeObserver === 'function') {
      var observer = new ResizeObserver(function () {
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(render);
      });
      observer.observe(host);
    } else {
      global.addEventListener('resize', function () {
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(render);
      });
    }
  }

  /* ------------------------------------------------------- stacked bar chart */

  function stackedBars(host, options) {
    var periods = options.periods;
    var categories = options.categories;
    var matrix = options.matrix;

    mount(host, function (width, tooltip) {
      var margin = { top: 26, right: 12, bottom: 34, left: 56 };
      var height = options.height || 300;
      var plotW = width - margin.left - margin.right;
      var plotH = height - margin.top - margin.bottom;

      var totals = periods.map(function (_, i) {
        return categories.reduce(function (sum, _c, c) { return sum + (matrix[c][i] || 0); }, 0);
      });
      var scale = niceScale(Math.max.apply(null, totals.concat([0])), 4);
      var y = function (value) { return margin.top + plotH - (value / scale.max) * plotH; };

      var band = plotW / Math.max(periods.length, 1);
      var barW = Math.min(44, band * 0.6);

      var svg = el('svg', {
        viewBox: '0 0 ' + width + ' ' + height,
        width: width, height: height, role: 'img',
        'aria-label': options.ariaLabel || 'Stacked bar chart'
      });

      drawValueAxis(svg, scale, y, margin, plotW, options.unit);
      drawPeriodLabels(svg, periods, margin, band, plotH);

      periods.forEach(function (period, i) {
        var cx = margin.left + band * i + band / 2;
        var cursor = 0;

        categories.forEach(function (name, c) {
          var value = matrix[c][i] || 0;
          if (value <= 0) return;
          var top = y(cursor + value);
          var bottom = y(cursor);
          var isTop = !categories.some(function (_n, other) {
            return other > c && (matrix[other][i] || 0) > 0;
          });
          var segH = Math.max(0, bottom - top - (isTop ? 0 : STACK_GAP));

          svg.appendChild(el('path', {
            d: roundedRect(cx - barW / 2, top, barW, segH, CORNER, {
              tl: isTop, tr: isTop, br: false, bl: false
            }),
            fill: seriesColor(c),
            class: 'mark',
            'data-period': String(i)
          }));
          cursor += value;
        });

        // One hit area per column: hovering anywhere in the column reports the
        // whole stack, which is what a reader actually wants to compare.
        var hit = el('rect', {
          x: margin.left + band * i, y: margin.top,
          width: band, height: plotH, class: 'hit'
        });
        hit.addEventListener('pointermove', function (event) {
          highlight(host, svg, '[data-period="' + i + '"]');
          var rows = categories.map(function (name, c) {
            return { name: name, color: seriesColor(c), value: formatValue(matrix[c][i]) };
          }).filter(function (row) { return row.value !== '0'; });
          rows.push({ name: 'Total', value: formatValue(totals[i]) });
          tooltip.show(clamp(cx, 90, width - 90), event.offsetY - 12, period, rows);
        });
        hit.addEventListener('pointerleave', function () {
          host.classList.remove('is-hovering');
          tooltip.hide();
        });
        svg.appendChild(hit);
      });

      return svg;
    });
  }

  /* ------------------------------------------------------- multi-line chart */

  function multiLine(host, options) {
    var periods = options.periods;
    var categories = options.categories;
    var matrix = options.matrix;

    mount(host, function (width, tooltip) {
      // Reserve room on the right for end-of-line direct labels. Only when
      // there is more than one series (the card title names a lone series) and
      // only when the plot is wide enough to spare the space.
      var labelled = categories.length > 1 && categories.length <= 4 && width >= 560;
      var labelRoom = labelled
        ? Math.min(140, Math.max.apply(null, categories.map(function (c) {
            // Series labels are bold, so allow more than CHAR_WIDTH per glyph.
            return truncate(c, 16).length * (CHAR_WIDTH + 0.8);
          })) + 18)
        : 14;

      var margin = { top: 26, right: labelRoom, bottom: 34, left: 56 };
      var height = options.height || 300;
      var plotW = width - margin.left - margin.right;
      var plotH = height - margin.top - margin.bottom;

      var peak = 0;
      matrix.forEach(function (row) {
        row.forEach(function (value) { if (value > peak) peak = value; });
      });
      var scale = niceScale(peak, 4);
      var y = function (value) { return margin.top + plotH - (value / scale.max) * plotH; };
      var x = function (i) {
        return periods.length === 1
          ? margin.left + plotW / 2
          : margin.left + (plotW * i) / (periods.length - 1);
      };

      var svg = el('svg', {
        viewBox: '0 0 ' + width + ' ' + height,
        width: width, height: height, role: 'img',
        'aria-label': options.ariaLabel || 'Line chart'
      });

      drawValueAxis(svg, scale, y, margin, plotW + margin.right - 12, options.unit);
      drawPeriodLabels(svg, periods, margin, plotW / Math.max(periods.length - 1, 1), plotH, true);

      var crosshair = el('line', {
        class: 'crosshair', y1: margin.top, y2: margin.top + plotH,
        x1: 0, x2: 0, opacity: 0
      });
      svg.appendChild(crosshair);

      categories.forEach(function (name, c) {
        var points = matrix[c].map(function (value, i) { return x(i) + ',' + y(value); });
        svg.appendChild(el('polyline', {
          points: points.join(' '),
          fill: 'none',
          stroke: seriesColor(c),
          'stroke-width': 2,
          'stroke-linejoin': 'round',
          'stroke-linecap': 'round',
          class: 'mark',
          'data-series': String(c)
        }));

        matrix[c].forEach(function (value, i) {
          svg.appendChild(el('circle', {
            cx: x(i), cy: y(value), r: MARK_RADIUS,
            fill: seriesColor(c),
            stroke: 'var(--surface-1)',
            'stroke-width': 2,
            class: 'mark',
            'data-series': String(c)
          }));
        });

        if (labelled && periods.length) {
          var last = matrix[c].length - 1;
          svg.appendChild(el('text', {
            x: x(last) + 8,
            y: y(matrix[c][last]) + 4,
            class: 'series-label',
            fill: seriesColor(c)
          }, truncate(name, 16)));
        }
      });

      var hit = el('rect', {
        x: margin.left, y: margin.top, width: Math.max(plotW, 1), height: plotH, class: 'hit'
      });
      hit.addEventListener('pointermove', function (event) {
        var ratio = (event.offsetX - margin.left) / Math.max(plotW, 1);
        var i = clamp(Math.round(ratio * (periods.length - 1)), 0, periods.length - 1);
        crosshair.setAttribute('x1', x(i));
        crosshair.setAttribute('x2', x(i));
        crosshair.setAttribute('opacity', 1);
        var rows = categories.map(function (name, c) {
          return { name: name, color: seriesColor(c), value: formatValue(matrix[c][i]) };
        });
        tooltip.show(clamp(x(i), 90, width - 90), event.offsetY - 12, periods[i], rows);
      });
      hit.addEventListener('pointerleave', function () {
        crosshair.setAttribute('opacity', 0);
        tooltip.hide();
      });
      svg.appendChild(hit);

      return svg;
    });
  }

  /* ---------------------------------------------------- ranked horizontal bars */

  function rankedBars(host, options) {
    var items = options.items;

    mount(host, function (width, tooltip) {
      var rowH = 26;
      var barH = 14;
      var margin = { top: 6, right: 68, bottom: 22, left: 0 };

      var labelWidth = Math.min(
        200,
        Math.max.apply(null, items.map(function (item) {
          return truncate(item.label, 24).length * CHAR_WIDTH;
        })) + 12
      );
      margin.left = labelWidth;

      var plotW = Math.max(60, width - margin.left - margin.right);
      var height = margin.top + items.length * rowH + margin.bottom;
      var peak = Math.max.apply(null, items.map(function (item) { return item.value; }).concat([0]));
      var scale = niceScale(peak, 3);
      var x = function (value) { return (value / scale.max) * plotW; };

      var svg = el('svg', {
        viewBox: '0 0 ' + width + ' ' + height,
        width: width, height: height, role: 'img',
        'aria-label': options.ariaLabel || 'Ranked bar chart'
      });

      scale.ticks.forEach(function (tick) {
        svg.appendChild(el('line', {
          class: 'grid-line',
          x1: margin.left + x(tick), x2: margin.left + x(tick),
          y1: margin.top, y2: margin.top + items.length * rowH
        }));
        svg.appendChild(el('text', {
          x: margin.left + x(tick), y: height - 8,
          class: 'tick-label is-value', 'text-anchor': 'middle'
        }, formatCompact(tick)));
      });

      items.forEach(function (item, i) {
        var top = margin.top + i * rowH + (rowH - barH) / 2;
        // The folded tail is not one of the ranked entities, so it is drawn in
        // muted ink rather than taking a categorical hue.
        var fill = item.is_tail ? 'var(--text-muted)' : seriesColor(0);
        var barW = Math.max(x(item.value), 2);

        svg.appendChild(el('text', {
          x: margin.left - 10, y: top + barH - 2,
          class: 'tick-label', 'text-anchor': 'end'
        }, truncate(item.label, 24)));

        svg.appendChild(el('path', {
          d: roundedRect(margin.left, top, barW, barH, CORNER, {
            tl: false, bl: false, tr: true, br: true
          }),
          fill: fill,
          class: 'mark',
          'data-row': String(i)
        }));

        svg.appendChild(el('text', {
          x: margin.left + barW + 8, y: top + barH - 2, class: 'bar-label'
        }, formatValue(item.value)));

        var hit = el('rect', {
          x: 0, y: margin.top + i * rowH, width: width, height: rowH, class: 'hit'
        });
        hit.addEventListener('pointermove', function (event) {
          highlight(host, svg, '[data-row="' + i + '"]');
          tooltip.show(
            clamp(margin.left + barW, 100, width - 100),
            event.offsetY - 10,
            item.label,
            [
              { name: options.unit || 'Count', color: fill, value: formatValue(item.value) },
              { name: 'Share', value: (item.share * 100).toFixed(1) + '%' }
            ]
          );
        });
        hit.addEventListener('pointerleave', function () {
          host.classList.remove('is-hovering');
          tooltip.hide();
        });
        svg.appendChild(hit);
      });

      svg.appendChild(el('line', {
        class: 'axis-line',
        x1: margin.left, x2: margin.left,
        y1: margin.top, y2: margin.top + items.length * rowH
      }));

      return svg;
    });
  }

  /* ------------------------------------------------------------- shared bits */

  function drawValueAxis(svg, scale, y, margin, plotW, unit) {
    scale.ticks.forEach(function (tick) {
      svg.appendChild(el('line', {
        class: tick === 0 ? 'axis-line' : 'grid-line',
        x1: margin.left, x2: margin.left + plotW,
        y1: y(tick), y2: y(tick)
      }));
      svg.appendChild(el('text', {
        x: margin.left - 8, y: y(tick) + 4,
        class: 'tick-label is-value', 'text-anchor': 'end'
      }, formatCompact(tick)));
    });
    if (unit) {
      svg.appendChild(el('text', {
        x: 0, y: margin.top - 12,
        class: 'tick-label', 'text-anchor': 'start'
      }, unit));
    }
  }

  function drawPeriodLabels(svg, periods, margin, band, plotH, centred) {
    // Thin the labels out rather than let them collide at narrow widths, and
    // step backwards from the end so the most recent period is always labelled.
    var needed = CHAR_WIDTH * 8 + 6;
    var step = Math.max(1, Math.ceil(needed / Math.max(band, 1)));
    var last = periods.length - 1;
    periods.forEach(function (period, i) {
      if ((last - i) % step !== 0) return;
      var cx = centred ? margin.left + band * i : margin.left + band * i + band / 2;
      svg.appendChild(el('text', {
        x: cx, y: margin.top + plotH + 18,
        class: 'tick-label', 'text-anchor': 'middle'
      }, truncate(period, 10)));
    });
  }

  function highlight(host, svg, selector) {
    host.classList.add('is-hovering');
    Array.prototype.slice.call(svg.querySelectorAll('.mark')).forEach(function (mark) {
      mark.classList.toggle('is-active', mark.matches(selector));
    });
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  global.Charts = {
    stackedBars: stackedBars,
    multiLine: multiLine,
    rankedBars: rankedBars,
    seriesColor: seriesColor,
    formatValue: formatValue,
    formatCompact: formatCompact,
    escapeHtml: escapeHtml
  };
})(window);
