// app.js — CAHazards frontend controller
// Handles: Photon autocomplete, API calls, loading state, error handling, URL state

(function () {
  'use strict';

  const input = document.getElementById('address-input');
  const dropdown = document.getElementById('autocomplete');
  const searchArea = document.getElementById('search-area');
  const loadingEl = document.getElementById('loading');
  const reportEl = document.getElementById('report');
  const toastEl = document.getElementById('error-toast');

  let debounceTimer = null;
  let activeIndex = -1;
  let acItems = [];
  let toastTimer = null;
  let lastQuery = null; // { address, lat, lon } for retrofit toggle re-fetch
  window._cahazards_retrofitted = false;
  let acCache = null; // { prefix5, results } — client-side autocomplete cache

  // ── Helpers ──

  function showLoading() {
    loadingEl.classList.add('visible');
    reportEl.classList.remove('visible');
  }

  function hideLoading() {
    loadingEl.classList.remove('visible');
  }

  function showError(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.classList.remove('visible');
    }, 5000);
  }

  function compactSearch() {
    searchArea.classList.add('compact');
  }

  function expandSearch() {
    searchArea.classList.remove('compact');
  }

  // ── URL State ──

  function setHash(address) {
    var url = '#' + encodeURIComponent(address);
    if (window._cahazards_retrofitted) url += '&retrofit=1';
    history.replaceState(null, '', url);
  }

  function updateRetrofitInHash() {
    var h = location.hash;
    if (!h || h.length < 2) return;
    var parts = h.split('&');
    // Remove existing retrofit param
    parts = parts.filter(function(p) { return p.indexOf('retrofit=') === -1; });
    var url = parts.join('&');
    if (window._cahazards_retrofitted) url += '&retrofit=1';
    history.replaceState(null, '', url);
  }

  function getHash() {
    var h = location.hash;
    if (h && h.length > 1) {
      // Parse address and params from hash
      var raw = h.slice(1); // remove #
      var parts = raw.split('&');
      return decodeURIComponent(parts[0]);
    }
    return null;
  }

  function getHashRetrofit() {
    var h = location.hash;
    if (h) {
      return h.indexOf('retrofit=1') !== -1;
    }
    return false;
  }

  // ── Autocomplete ──

  function formatPhotonResult(feature) {
    var display = feature._display || '';
    var parts = display.split(', ');
    var main = parts[0] || feature.properties.name || '';
    var sub = parts.slice(1).join(', ');
    return { main: main, sub: sub };
  }

  function renderDropdown(features) {
    acItems = features;
    activeIndex = -1;

    if (features.length === 0) {
      dropdown.classList.remove('visible');
      dropdown.innerHTML = '';
      return;
    }

    var html = '';
    for (var i = 0; i < features.length; i++) {
      var f = features[i];
      var formatted = formatPhotonResult(f);
      html += '<div class="ac-item" data-index="' + i + '">';
      html += '<div class="ac-item-main">' + escapeHtml(formatted.main) + '</div>';
      if (formatted.sub) {
        html += '<div class="ac-item-sub">' + escapeHtml(formatted.sub) + '</div>';
      }
      html += '</div>';
    }

    dropdown.innerHTML = html;
    dropdown.classList.add('visible');

    // Attach click listeners
    var items = dropdown.querySelectorAll('.ac-item');
    for (var j = 0; j < items.length; j++) {
      (function (idx) {
        items[idx].addEventListener('click', function () {
          selectResult(idx);
        });
      })(j);
    }
  }

  function highlightItem(idx) {
    var items = dropdown.querySelectorAll('.ac-item');
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle('active', i === idx);
    }
    activeIndex = idx;
  }

  function selectResult(idx) {
    var feature = acItems[idx];
    if (!feature) return;

    var formatted = formatPhotonResult(feature);
    var fullAddress = formatted.main + (formatted.sub ? ', ' + formatted.sub : '');
    input.value = fullAddress;
    dropdown.classList.remove('visible');
    dropdown.innerHTML = '';

    var coords = feature.geometry.coordinates; // [lon, lat]
    fetchReport(fullAddress, coords[1], coords[0]);
  }

  function fetchAutocomplete(query) {
    fetch('/api/autocomplete?q=' + encodeURIComponent(query))
      .then(function (res) { return res.json(); })
      .then(function (results) {
        var features = results.map(function (r) {
          var parts = r.display.split(', ');
          return {
            properties: {
              name: parts[0] || '',
              city: parts[1] || '',
              state: 'California',
              postcode: parts[3] || '',
            },
            geometry: { coordinates: [r.lon, r.lat] },
            _display: r.display,
          };
        });
        // Cache for client-side filtering as user keeps typing
        acCache = { prefix5: query.substring(0, 5).toLowerCase(), results: features };
        // Re-filter against current input (user may have typed more while waiting)
        var current = input.value.trim().toLowerCase();
        var filtered = features.filter(function(r) {
          return r._display.toLowerCase().indexOf(current) === 0;
        });
        renderDropdown(filtered.slice(0, 6));
      })
      .catch(function () {});
  }

  // ── API Call ──

  function wireRetrofitToggle() {
    var cb = document.getElementById('retrofit-checkbox');
    if (!cb) return;
    cb.addEventListener('change', function () {
      window._cahazards_retrofitted = cb.checked;
      updateRetrofitInHash();
      var d = window._cahazards_lastData;
      if (d && d.retrofitted_structural) {
        d._active_structural = cb.checked ? d.retrofitted_structural : d.structural;
        renderReport(d);
        wireRetrofitToggle(); // re-attach after re-render
      }
    });
  }

  function fetchReport(address, lat, lon) {
    showLoading();
    compactSearch();
    window._cahazards_lastData = null;
    reportEl.innerHTML = '';
    reportEl.classList.remove('visible');
    setHash(address);
    lastQuery = { address: address, lat: lat, lon: lon };

    // Step 1: Refine via Census geocoder (canonical address + precise coords)
    // Then use refined coords for the report API call
    refineCensusGeocode(address, lat, lon, function(refined) {
      var reportAddress = refined ? refined.address : address;
      var reportLat = refined ? refined.lat : lat;
      var reportLon = refined ? refined.lon : lon;

      if (refined) {
        input.value = refined.address;
        setHash(refined.address);
      }

      // Step 2: Fetch report with best available coords
      var retrofitted = window._cahazards_retrofitted;
      var promise;

      if (reportLat != null && reportLon != null) {
        promise = fetch('/api/report', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ address: reportAddress, lat: reportLat, lon: reportLon, retrofitted: retrofitted }),
        });
      } else {
        var qs = 'address=' + encodeURIComponent(reportAddress);
        if (retrofitted) qs += '&retrofitted=true';
        promise = fetch('/api/report?' + qs);
      }

      // Step 3: Report + earthquake in parallel
      // Earthquake needs Vs30 from the report, so it starts when report arrives
      var eqDone = false, reportDone = false;
      var eqResult = null, eqError = null;
      var reportResult = null;

      function tryRender() {
        if (!eqDone || !reportDone) return;
        if (eqError) { hideLoading(); showError(eqError); return; }

        var d = reportResult;
        d.structural.earthquake = eqResult.earthquake;
        if (d.retrofitted_structural) {
          d.retrofitted_structural.earthquake = eqResult.earthquake_retrofitted;
        }
        function recomputeCombined(s) {
          var hazards = [s.earthquake, s.wildfire, s.flood, s.tsunami, s.landslide, s.erosion, s.dam_inundation];
          var survival = 1;
          for (var i = 0; i < hazards.length; i++) {
            survival *= (1 - (hazards[i].annual_p || 0));
          }
          s.combined_30yr = 1 - Math.pow(survival, 30);
        }
        recomputeCombined(d.structural);
        if (d.retrofitted_structural) recomputeCombined(d.retrofitted_structural);

        if (window._cahazards_retrofitted && d.retrofitted_structural) {
          d._active_structural = d.retrofitted_structural;
        }

        window._cahazards_lastData = d;
        renderReport(d);
        wireRetrofitToggle();
        requestAnimationFrame(function() {
          requestAnimationFrame(function() {
            hideLoading();
            reportEl.classList.add('visible');
          });
        });
      }

      promise
        .then(function (res) {
          return res.json().then(function (data) {
            return { status: res.status, data: data };
          });
        })
        .then(function (result) {
          if (result.status !== 200 || result.data.error) {
            hideLoading();
            showError(result.data.error || 'An error occurred');
            return;
          }
          reportResult = result.data;
          reportDone = true;
          // Start earthquake fetch now that we have Vs30 from the report
          fetchAndComputeEarthquake(result.data, function(eq, err) {
            eqResult = eq;
            eqError = err;
            eqDone = true;
            tryRender();
          });
        })
        .catch(function (err) {
          hideLoading();
          showError('Network error: ' + (err.message || 'Could not reach server'));
        });
    });
  }

  // ── Input Events ──

  input.addEventListener('input', function () {
    var val = input.value.trim();
    clearTimeout(debounceTimer);

    if (val.length < 5) {
      dropdown.classList.remove('visible');
      dropdown.innerHTML = '';
      acCache = null;
      return;
    }

    var prefix5 = val.substring(0, 5).toLowerCase();

    // Same first 5 chars — filter cached results locally
    if (acCache && acCache.prefix5 === prefix5) {
      var filtered = acCache.results.filter(function(r) {
        return fuzzyMatch(val, r._display);
      });
      renderDropdown(filtered.slice(0, 6));
      return;
    }

    // New prefix — fetch from server (only if not already pending)
    if (!acCache || acCache.prefix5 !== prefix5) {
      acCache = { prefix5: prefix5, results: [] }; // placeholder to block duplicate fetches
      fetchAutocomplete(val);
    }
  });

  input.addEventListener('keydown', function (e) {
    var items = dropdown.querySelectorAll('.ac-item');
    if (!items.length) {
      // Enter without autocomplete: search by address
      if (e.key === 'Enter') {
        e.preventDefault();
        var val = input.value.trim();
        if (val.length >= 5) {
          dropdown.classList.remove('visible');
          fetchReport(val, null, null);
        }
      }
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      var next = activeIndex < items.length - 1 ? activeIndex + 1 : 0;
      highlightItem(next);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      var prev = activeIndex > 0 ? activeIndex - 1 : items.length - 1;
      highlightItem(prev);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIndex >= 0) {
        selectResult(activeIndex);
      } else {
        dropdown.classList.remove('visible');
        var val2 = input.value.trim();
        if (val2.length >= 5) {
          fetchReport(val2, null, null);
        }
      }
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('visible');
    }
  });

  // Close dropdown on outside click
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#search-wrap')) {
      dropdown.classList.remove('visible');
    }
  });

  // ── Init: check URL hash ──

  if (getHashRetrofit()) {
    window._cahazards_retrofitted = true;
  }

  var initialAddress = getHash();
  if (initialAddress) {
    input.value = initialAddress;
    fetchReport(initialAddress, null, null);
  }

  // ── Census Geocoder Refinement ──

  function refineCensusGeocode(address, lat, lon, callback) {
    // Try Census geocoder for canonical address + precise coords. Timeout after 3s.
    var done = false;
    var timer = setTimeout(function() {
      if (!done) { done = true; callback(null); }
    }, 3000);

    var url = '/api/geocode?address=' + encodeURIComponent(address);

    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(resp) {
        if (done) return;
        done = true;
        clearTimeout(timer);

        var matches = resp && resp.result && resp.result.addressMatches;
        if (!matches || matches.length === 0) { callback(null); return; }

        var match = matches[0];
        var canonical = match.matchedAddress || address;
        var coords = match.coordinates || {};

        callback({
          address: canonical,
          lat: coords.y != null ? coords.y : lat,
          lon: coords.x != null ? coords.x : lon,
        });
      })
      .catch(function() {
        if (!done) { done = true; clearTimeout(timer); callback(null); }
      });
  }

  // ── Fuzzy address matching ──

  var ABBREVS = {
    'st': 'street', 'street': 'st',
    'dr': 'drive', 'drive': 'dr',
    'ave': 'avenue', 'avenue': 'ave',
    'av': 'avenue', 'avenue': 'av',
    'blvd': 'boulevard', 'boulevard': 'blvd',
    'rd': 'road', 'road': 'rd',
    'ln': 'lane', 'lane': 'ln',
    'ct': 'court', 'court': 'ct',
    'pl': 'place', 'place': 'pl',
    'way': 'way',
    'cir': 'circle', 'circle': 'cir',
    'pkwy': 'parkway', 'parkway': 'pkwy',
    'ter': 'terrace', 'terrace': 'ter',
    'n': 'north', 'north': 'n',
    's': 'south', 'south': 's',
    'e': 'east', 'east': 'e',
    'w': 'west', 'west': 'w',
  };

  function fuzzyMatch(query, candidate) {
    var qWords = query.toLowerCase().replace(/[,.\-#]/g, ' ').split(/\s+/).filter(Boolean);
    var cWords = candidate.toLowerCase().replace(/[,.\-#]/g, ' ').split(/\s+/).filter(Boolean);

    // Every query word must match the start of some candidate word (in order)
    var ci = 0;
    for (var qi = 0; qi < qWords.length; qi++) {
      var qw = qWords[qi];
      var matched = false;
      while (ci < cWords.length) {
        var cw = cWords[ci];
        ci++;
        if (cw.indexOf(qw) === 0) { matched = true; break; }
        // Try abbreviation expansion
        var alt = ABBREVS[qw];
        if (alt && cw.indexOf(alt) === 0) { matched = true; break; }
        // Only skip candidate words that come after matched ones
        if (qi > 0) continue;
        break;
      }
      if (!matched) return false;
    }
    return true;
  }

  // ── Utility ──

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
})();
