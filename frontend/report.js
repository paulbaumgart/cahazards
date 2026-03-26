// report.js — Renders the full hazard report into the DOM
// Expects the API response shape from computeHazardReport + address/census_tract

/* exported renderReport */

function renderReport(data) {
  'use strict';

  var el = document.getElementById('report');
  if (!el) return;

  // Use retrofit-toggled structural data if available
  var activeStructural = data._active_structural || data.structural;
  data = Object.assign({}, data, { structural: activeStructural });

  var html = '';

  // ── Address header ──
  html += '<div id="report-address">' + esc(data.address || 'Unknown Address') + '</div>';

  // ── BLUF Card ──
  html += renderBLUF(data);

  // ── Site Parameters ──
  html += renderSiteParams(data);

  // ── Zone Designations ──
  html += renderZoneFlags(data.zones, data);

  // ── Structural Hazards ──
  html += '<div class="section-title">Structural Hazards</div>';
  html += '<div class="section-subtitle">30-year damage probability</div>';
  html += '<div class="retrofit-toggle">' +
    '<label class="toggle-label">' +
    '<input type="checkbox" id="retrofit-checkbox" ' + (window._cahazards_retrofitted ? 'checked' : '') + '>' +
    '<span class="toggle-slider"></span>' +
    ' Seismic retrofit applied' +
    '</label>' +
    '<span class="retrofit-info tooltip" title="Bolt, brace, and strap retrofit reduces earthquake shaking damage by ~70% (FEMA P-807). Does not reduce liquefaction risk — earthquake number includes both.">' +
    ' <a href="https://www.earthquakebracebolt.com" target="_blank" rel="noopener">EarthquakeBraceBolt.com</a>' +
    '</span>' +
    '</div>';
  html += renderHazardBars(data.structural);

  // ── Environmental ──
  html += '<div class="section-title">Environmental &amp; Health</div>';
  html += renderEnvironmental(data);

  // ── Faults ──
  if (data.faults && data.faults.length > 0) {
    html += '<div class="section-title">Nearby Faults</div>';
    html += '<div class="section-subtitle">Within 30 miles. <a href="https://pubs.usgs.gov/of/2013/1165/" target="_blank" rel="noopener" style="color:var(--accent-cyan)">UCERF3</a> probabilities shown are per-fault marginals — faults are not independent (a rupture on one changes stress on neighbors).</div>';
    html += renderFaults(data.faults);
  }

  // ── Footer ──
  html += renderFooter();

  el.innerHTML = html;

  // Trigger animated bars after a brief layout tick
  requestAnimationFrame(function () {
    requestAnimationFrame(function () {
      animateBars();
      animateCESGauge();
    });
  });
}

// ── Helpers ──

function esc(str) {
  var d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}

function pctFmt(p) {
  if (p == null) return '--';
  var pct = p * 100;
  if (pct < 0.1) return '~0%';
  if (pct < 1) return pct.toFixed(1) + '%';
  return pct.toFixed(1) + '%';
}

function probToBarWidth(p) {
  // Linear scale: full width = 100% probability
  // Below 0.1% (~0%) — show no bar at all
  var pct = p * 100; // convert to percentage points
  if (pct < 0.1) return 0;
  return Math.max(2, Math.min(100, pct));
}

function combinedColor(p) {
  if (p < 0.05) return 'var(--accent-green)';
  if (p < 0.15) return 'var(--accent-yellow)';
  if (p < 0.30) return 'var(--accent-orange)';
  return 'var(--accent-red)';
}

var HAZARD_COLORS = {
  earthquake: '#e74c3c',
  wildfire: '#e67e22',
  flood: '#3498db',
  erosion: '#8b5e3c',
  landslide: '#a0522d',
  tsunami: '#2980b9',
  dam_inundation: '#34495e',
};

var HAZARD_LABELS = {
  earthquake: 'Earthquake',
  wildfire: 'Wildfire',
  flood: 'Flood',
  erosion: 'Erosion',
  landslide: 'Landslide',
  tsunami: 'Tsunami',
  dam_inundation: 'Dam Failure',
  aviation_lead: 'Aviation Lead Exposure',
};

var HAZARD_DOCS = {
  earthquake: 'earthquake-model',
  wildfire: 'wildfire-model',
  flood: 'flood-model',
  erosion: 'erosion-model',
  landslide: 'landslide-model',
  tsunami: 'tsunami-model',
  dam_inundation: 'dam-inundation-model',
  aviation_lead: 'aviation-lead-model',
  traffic_pollution: 'traffic-pollution-model',
};

// ── BLUF ──

function renderBLUF(data) {
  var s = data.structural;
  var combined = s.combined_30yr;
  var color = combinedColor(combined);

  // Top 2 hazards
  var hazards = [
    { key: 'earthquake', p: s.earthquake.p30yr },
    { key: 'wildfire', p: s.wildfire.p30yr },
    { key: 'flood', p: s.flood.p30yr },
    { key: 'erosion', p: s.erosion.p30yr },
    { key: 'landslide', p: s.landslide.p30yr },
    { key: 'tsunami', p: s.tsunami.p30yr },
    { key: 'dam_inundation', p: s.dam_inundation.p30yr },
  ];
  hazards.sort(function (a, b) { return b.p - a.p; });

  var top2 = hazards.filter(function (h) { return h.p >= 0.001; }).slice(0, 2);
  var top2Text = '';
  if (top2.length > 0) {
    var parts = top2.map(function (h) {
      return '<strong>' + HAZARD_LABELS[h.key] + '</strong> (' + pctFmt(h.p) + ')';
    });
    top2Text = 'Top risks: ' + parts.join(' and ');
  } else {
    top2Text = 'No significant structural hazards detected.';
  }

  return '<div class="bluf-card">' +
    '<div class="bluf-label">Combined 30-Year Structural Risk</div>' +
    '<div class="bluf-number" style="color:' + color + '">' + pctFmt(combined) + '</div>' +
    '<div class="bluf-unit">probability of <span class="tooltip" title="Major structural damage: foundation cracking, partial wall collapse, fire/water destruction, or equivalent. Repair costs typically exceed 20% of structure value. Based on HAZUS extensive + complete damage states.">major damage<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-left:4px;opacity:0.5"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg></span></div>' +
    '<div class="bluf-top-hazards">' + top2Text + '</div>' +
    '</div>';
}

// ── Site Params ──

function renderSiteParams(data) {
  return '<div class="site-params">' +
    siteParam('Elevation', Math.round(data.elevation_m * 3.281).toLocaleString() + ' ft') +
    siteParam('Slope', Math.round(data.slope_deg) + '\u00B0') +
    siteParam('Lat', data.coordinates.lat.toFixed(5)) +
    siteParam('Lon', data.coordinates.lon.toFixed(5)) +
    '</div>';
}

function siteParam(label, value) {
  return '<div class="site-param">' +
    '<div class="site-param-label">' + esc(label) + '</div>' +
    '<div class="site-param-value">' + esc(String(value)) + '</div>' +
    '</div>';
}

// ── Zone Flags ──

function zoneFlag(label, value, color, tooltip) {
  var tip = tooltip ? ' title="' + esc(tooltip) + '"' : '';
  var cls = tooltip ? ' tooltip' : '';
  return '<div class="zone-flag' + cls + '" style="border-color:' + color + '"' + tip + '>' +
    '<div class="zone-flag-label">' + esc(label) + '</div>' +
    '<div class="zone-flag-value" style="color:' + color + '">' + esc(value) + '</div>' +
    '</div>';
}

function fireZoneColor(zone) {
  if (zone === 'Very High') return 'var(--accent-red)';
  if (zone === 'High') return 'var(--accent-orange)';
  if (zone === 'Moderate') return 'var(--accent-yellow)';
  return 'var(--text-dim)';
}

function renderZoneFlags(zones, data) {
  var html = '<div class="section-title">Zone Designations</div>';
  html += '<div class="zone-flags">';
  html += zoneFlag('Fire Hazard', zones.fire_hazard || 'None', fireZoneColor(zones.fire_hazard),
    'CAL FIRE Fire Hazard Severity Zone. Based on vegetation, slope, and weather — not actual burn probability.');
  html += zoneFlag('FEMA Flood', zones.fema_flood || 'None', zones.fema_flood === 'V/VE' || zones.fema_flood === 'AE' || zones.fema_flood === 'A' ? 'var(--accent-blue)' : 'var(--text-dim)',
    'FEMA National Flood Hazard Layer zone. X = minimal risk. AE/A = 1% annual flood chance (100-year floodplain). V/VE = coastal high-hazard with wave action.');
  html += zoneFlag('Liquefaction', zones.liquefaction ? 'In Zone' : 'No', zones.liquefaction ? 'var(--accent-orange)' : 'var(--accent-green)',
    'CGS Seismic Hazard Zone for liquefaction. Saturated, loose soils that can behave like liquid during earthquake shaking, causing buildings to settle or tilt.');
  html += zoneFlag('Landslide', zones.landslide ? 'In Zone' : 'No', zones.landslide ? 'var(--accent-orange)' : 'var(--accent-green)',
    'CGS Seismic Hazard Zone for earthquake-induced landslides. Areas where slope stability may be compromised during strong shaking.');
  var realTsunamiRisk = zones.tsunami_inundation && data.elevation_m < 30;
  var tsunamiLabel = realTsunamiRisk ? 'In Zone' : 'No';
  var tsunamiColor = realTsunamiRisk ? 'var(--accent-cyan)' : 'var(--accent-green)';
  html += zoneFlag('Tsunami', tsunamiLabel, tsunamiColor,
    'CGS Tsunami Hazard Area. Modeled inundation from a credible worst-case tsunami scenario.');
  html += zoneFlag('Soil (Expansive)', zones.expansive_soil || 'Unknown', zones.expansive_soil === 'High' || zones.expansive_soil === 'Very High' ? 'var(--accent-orange)' : 'var(--text-dim)',
    'USDA SSURGO shrink-swell potential. Expansive clay soils swell when wet and shrink when dry, causing foundation cracking and slab heave.');
  html += '</div>';
  return html;
}

// ── Hazard Bars ──

function renderHazardBars(structural) {
  var keys = ['earthquake', 'wildfire', 'flood', 'erosion', 'landslide', 'tsunami', 'dam_inundation'];

  // Sort by probability, highest first
  keys.sort(function(a, b) { return structural[b].p30yr - structural[a].p30yr; });

  var html = '<div class="hazard-bars">';

  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var h = structural[k];
    var p30 = h.p30yr;
    var width = probToBarWidth(p30);
    var color = HAZARD_COLORS[k];

    var wuiFlag = (k === 'wildfire' && h.wui_underestimate)
    var wuiPrefix = (k === 'wildfire' && h.wui_underestimate)
      ? '<span class="wui-flag" onclick="event.stopPropagation()">⚠<span class="wui-tooltip">CalFire designates this area as high or very high fire hazard, but the fire simulation cannot model fire spread through developed areas. The actual risk may be higher than shown.</span></span>'
      : '';
    html += '<div class="hazard-bar-row clickable" onclick="showHazardDoc(\'' + k + '\')">';
    html += '<div class="hazard-bar-label">' + (wuiPrefix ? '<span class="hazard-bar-warn">' + wuiPrefix + '</span>' : '') + '<span class="hazard-bar-label-text">' + HAZARD_LABELS[k] + '</span></div>';
    html += '<div class="hazard-bar-track">';
    html += '<div class="hazard-bar-fill" data-width="' + width + '" style="background:' + color + ';"></div>';
    html += '</div>';
    html += '<div class="hazard-bar-value">' + pctFmt(p30) + '</div>';
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function animateBars() {
  var fills = document.querySelectorAll('.hazard-bar-fill');
  for (var i = 0; i < fills.length; i++) {
    var w = fills[i].getAttribute('data-width');
    fills[i].style.width = w + '%';
  }
}

// ── Environmental ──

function renderEnvironmental(data) {
  var html = '<div class="env-grid">';

  // Cal EnviroScreen
  html += '<div class="env-card">';
  html += '<div class="env-card-title"><a href="https://oehha.ca.gov/calenviroscreen/report/calenviroscreen-40" target="_blank" rel="noopener" class="clickable-title">CalEnviroScreen 4.0</a></div>';
  html += renderCESGauge(data.calenviroscreen);
  html += '</div>';

  // Contamination
  html += '<div class="env-card">';
  html += '<div class="env-card-title">Contamination Sites</div>';
  html += renderContamination(data.contamination);
  html += '</div>';

  // Aviation Lead
  html += '<div class="env-card">';
  html += '<div class="env-card-title"><span class="clickable-title" onclick="showHazardDoc(\'aviation_lead\')">Aviation Lead Exposure</span></div>';
  html += renderAviationLead(data.aviation_lead);
  html += '</div>';

  // Traffic Pollution
  html += '<div class="env-card">';
  html += '<div class="env-card-title"><span class="clickable-title" onclick="showHazardDoc(\'traffic_pollution\')">Traffic Pollution</span></div>';
  html += renderTrafficPollution(data.traffic_pollution);
  html += '</div>';

  // SLR (full-width bar)
  html += '</div>';  // close env-grid
  html += renderSLRBar(data.sea_level_rise);
  html += '<div class="env-grid">';  // reopen for any remaining cards

  html += '</div>';
  return html;
}

// ── CES Gauge ──

function cesColor(pctl) {
  if (pctl == null) return 'var(--text-dim)';
  if (pctl < 25) return 'var(--accent-green)';
  if (pctl < 50) return 'var(--accent-yellow)';
  if (pctl < 75) return 'var(--accent-orange)';
  return 'var(--accent-red)';
}

function renderCESGauge(ces) {
  if (!ces) {
    return '<div class="env-empty">No Cal EnviroScreen data for this location.</div>';
  }

  var pctl = ces.overall_percentile;
  var color = cesColor(pctl);
  var circumference = 2 * Math.PI * 36; // r=36
  var offset = circumference; // start fully hidden, animate via JS

  var html = '<div class="ces-gauge-wrap">';
  html += '<div class="ces-gauge">';
  html += '<svg width="90" height="90" viewBox="0 0 80 80">';
  html += '<circle class="ces-gauge-bg" cx="40" cy="40" r="36"/>';
  html += '<circle class="ces-gauge-fill" cx="40" cy="40" r="36" ' +
    'stroke="' + color + '" ' +
    'stroke-dasharray="' + circumference + '" ' +
    'stroke-dashoffset="' + offset + '" ' +
    'data-pctl="' + (pctl || 0) + '" ' +
    'data-circumference="' + circumference + '"/>';
  html += '</svg>';
  html += '<div class="ces-gauge-text">' + (pctl != null ? Math.round(pctl) : '--') + '</div>';
  html += '</div>';

  html += '<div class="ces-details">';
  html += 'Percentile: <span>' + (pctl != null ? Math.round(pctl) + 'th' : 'N/A') + '</span><br>';
  if (ces.tract) html += 'Tract: <span>' + esc(ces.tract) + '</span><br>';
  if (ces.pm25_pctl != null) html += 'PM2.5: <span>' + Math.round(ces.pm25_pctl) + 'th</span><br>';
  if (ces.diesel_pm_pctl != null) html += 'Diesel PM: <span>' + Math.round(ces.diesel_pm_pctl) + 'th</span><br>';
  if (ces.traffic_pctl != null) html += 'Traffic: <span>' + Math.round(ces.traffic_pctl) + 'th</span><br>';
  html += '</div>';

  html += '</div>';
  return html;
}

function animateCESGauge() {
  var fill = document.querySelector('.ces-gauge-fill');
  if (!fill) return;
  var pctl = parseFloat(fill.getAttribute('data-pctl')) || 0;
  var circ = parseFloat(fill.getAttribute('data-circumference'));
  var target = circ - (pctl / 100) * circ;
  fill.style.strokeDashoffset = target;
}

// ── Contamination ──

function contamColor(distM) {
  if (distM < 200) return 'var(--accent-red)';
  if (distM < 500) return 'var(--accent-orange)';
  if (distM < 1000) return 'var(--accent-yellow)';
  return 'var(--text-dim)';
}

function metersToFt(m) {
  var mi = m / 1609.34;
  if (mi >= 0.1) return mi.toFixed(1) + ' mi';
  return Math.round(m * 3.281).toLocaleString() + ' ft';
}

function kmToMi(km) {
  var mi = km * 0.6214;
  if (mi >= 0.1) return mi.toFixed(1) + ' mi';
  return Math.round(km * 3281).toLocaleString() + ' ft';
}

var CONTAM_TYPES = {
  'lust': 'Leaking Underground Storage Tank',
  'slic': 'Spills/Leaks/Cleanup',
  'fuds': 'Formerly Used Defense Site',
  'hwcma': 'Hazardous Waste',
  'vcp': 'Voluntary Cleanup',
  'military': 'Military Cleanup',
  'school': 'School Investigation',
  'brownfield': 'Brownfield',
  'npl': 'Superfund (NPL)',
};

var CONTAM_STATUSES = {
  'closed_restricted': 'Closed (land use restrictions)',
  'closed': 'Closed',
  'open_active': 'Active cleanup',
  'open_inactive': 'Open (inactive)',
  'open_assessment': 'Under assessment',
  'open_remediation': 'Remediation in progress',
  'open_verification_monitoring': 'Monitoring',
  'open': 'Open',
  'certified': 'Certified',
  'no_action_required': 'No action required',
  'refer_other_agency': 'Referred to other agency',
};

function formatContamType(raw) {
  return CONTAM_TYPES[raw] || CONTAM_TYPES[raw.toLowerCase()] || raw.replace(/_/g, ' ');
}

function formatContamStatus(raw) {
  return CONTAM_STATUSES[raw] || CONTAM_STATUSES[raw.toLowerCase()] || raw.replace(/_/g, ' ');
}

function renderContamination(contam) {
  if (!contam) return '<div class="env-empty">No data available.</div>';

  var all = (contam.sites_within_1km || []).slice();
  all.sort(function (a, b) { return a.distance_m - b.distance_m; });

  if (all.length === 0) {
    return '<div class="env-empty">No contamination sites within 0.5 miles.</div>';
  }

  var html = '<ul class="contam-list">';
  var max = all.length;
  for (var i = 0; i < max; i++) {
    var s = all[i];
    var color = contamColor(s.distance_m);
    var name = (s.name && s.name !== 'Unknown') ? esc(s.name) : formatContamType(s.type);
    var status = formatContamStatus(s.status);
    var geoTrackerLink = 'https://geotracker.waterboards.ca.gov/map/?CMD=runreport&myaddress=' + s.lat + '%2C' + s.lon;
    var enviroStorLink = 'https://www.envirostor.dtsc.ca.gov/public/map/?lat=' + s.lat + '&lng=' + s.lon + '&zoom=16';
    var detailLink = (s.source === 'geotracker') ? geoTrackerLink : enviroStorLink;
    html += '<li class="contam-item">';
    if (s.lat && s.lon) {
      html += '<div class="contam-name"><a href="' + detailLink + '" target="_blank" rel="noopener" class="contam-name-link">' + name + '</a></div>';
    } else {
      html += '<div class="contam-name">' + name + '</div>';
    }
    html += '<div class="contam-meta">' + esc(status) + '</div>';
    html += '<div class="contam-distance" style="color:' + color + '">' + metersToFt(s.distance_m) + '</div>';
    html += '</li>';
  }
  html += '</ul>';
  return html;
}

// ── Aviation Lead ──

function renderAviationLead(av) {
  if (!av) return '<div class="env-empty">No data available.</div>';

  var flagClass = riskToFlagClass(av.risk_level);
  var html = '<div class="flag-pill ' + flagClass + '">' + esc(av.risk_level) + '</div>';

  if (av.nearest_airport) {
    var a = av.nearest_airport;
    html += '<div style="margin-top:12px; font-size:0.85rem; line-height:1.7">';
    html += '<span style="color:var(--text-bright); font-weight:500">' + esc(a.name) + '</span>';
    if (a.code) html += ' <span style="color:var(--text-dim)">(' + esc(a.code) + ')</span>';
    html += '<br>';
    html += '<span style="font-family:var(--font-mono); font-size:0.78rem; color:var(--text-dim)">';
    html += kmToMi(a.distance_km) + ' &middot; ' + formatNumber(a.piston_ops) + ' piston ops/yr';
    html += '</span>';
    html += '</div>';
  } else {
    html += '<div style="margin-top:8px; font-size:0.85rem; color:var(--text-dim)">No nearby piston-engine airports.</div>';
  }

  return html;
}

// ── Traffic Pollution ──

function renderTrafficPollution(tp) {
  if (!tp) return '<div class="env-empty">No data available.</div>';

  var flagClass = riskToFlagClass(tp.risk_level);
  var html = '<div class="flag-pill ' + flagClass + '">' + esc(tp.risk_level) + '</div>';

  if (tp.nearest_major_road) {
    var r = tp.nearest_major_road;
    html += '<div style="margin-top:12px; font-family:var(--font-mono); font-size:0.82rem; line-height:1.7">';
    html += 'Distance: <span style="color:var(--text-bright)">' + metersToFt(r.distance_m) + '</span><br>';
    html += '<span class="aadt-label tooltip" title="Annual Average Daily Traffic — the number of vehicles passing this road segment per day (FHWA Highway Performance Monitoring System)">AADT</span>: <span style="color:var(--text-bright)">' + formatNumber(r.aadt) + ' vehicles/day</span>';
    html += '</div>';
  }

  return html;
}

// ── SLR ──

function renderSLRBar(slr) {
  if (!slr) return '';
  var inundated = slr.inundated_at || {};
  var thresholds = ['1ft', '2ft', '3ft', '4ft', '6ft', '10ft'];
  var anyInundated = thresholds.some(function(k) { return inundated[k] === true; });

  var html = '<div class="slr-bar">';
  html += '<div class="slr-bar-title">' + (anyInundated ? 'Sea Level Rise' : 'Safe From Sea Level Rise') + '</div>';
  html += '<div class="slr-bar-pills">';
  for (var i = 0; i < thresholds.length; i++) {
    var key = thresholds[i];
    var isIn = inundated[key] === true;
    html += '<div class="slr-pill ' + (isIn ? 'slr-inundated' : 'slr-safe') + '">' + key + '</div>';
  }
  html += '</div>';
  if (anyInundated && slr.lowest_threshold_ft != null) {
    html += '<div class="slr-bar-note">Inundated at ' + slr.lowest_threshold_ft + 'ft rise</div>';
  }
  html += '</div>';
  return html;
}

function renderSLR(slr) {
  if (!slr) return '<div class="env-empty">No sea level rise data.</div>';

  var thresholds = ['1ft', '2ft', '3ft', '4ft', '6ft', '10ft'];
  var inundated = slr.inundated_at || {};
  var anyInundated = false;

  var html = '';
  for (var i = 0; i < thresholds.length; i++) {
    var key = thresholds[i];
    var isInundated = inundated[key] === true;
    if (isInundated) anyInundated = true;

    html += '<div class="slr-row">';
    html += '<div class="slr-label">' + key + '</div>';
    html += '<div class="slr-dot ' + (isInundated ? 'inundated' : 'safe') + '"></div>';
    html += '<div class="slr-status">' + (isInundated ? 'Inundated' : 'Above water') + '</div>';
    html += '</div>';
  }

  if (!anyInundated) {
    html = '<div style="font-size:0.85rem; color:var(--text-dim); margin-bottom:8px;">Not projected to be inundated at any modeled threshold.</div>' + html;
  } else if (slr.lowest_threshold_ft != null) {
    html = '<div style="font-size:0.85rem; color:var(--accent-orange); margin-bottom:8px; font-weight:500;">Inundated at ' + slr.lowest_threshold_ft + 'ft rise</div>' + html;
  }

  return html;
}

// ── Soils ──

function renderSoils(zones) {
  if (!zones) return '<div class="env-empty">No data available.</div>';

  var soil = zones.expansive_soil || 'Unknown';
  var soilColor = 'var(--text-bright)';
  var lower = soil.toLowerCase();
  if (lower.indexOf('high') !== -1) soilColor = 'var(--accent-red)';
  else if (lower.indexOf('moderate') !== -1) soilColor = 'var(--accent-orange)';
  else if (lower.indexOf('low') !== -1) soilColor = 'var(--accent-green)';

  return '<div class="soil-label" style="color:' + soilColor + '">' + esc(soil) + '</div>';
}

// ── Faults ──

function mmiColor(mmi) {
  if (mmi < 5) return 'var(--accent-green)';
  if (mmi < 6) return 'var(--accent-green)';
  if (mmi < 7) return 'var(--accent-yellow)';
  if (mmi < 8) return 'var(--accent-orange)';
  if (mmi < 9) return 'var(--accent-red)';
  return '#8b0000';
}

function mmiLabel(mmi) {
  var roman = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII'];
  var idx = Math.min(Math.max(Math.round(mmi) - 1, 0), 11);
  return roman[idx];
}

function renderFaults(faults) {
  var html = '<div class="fault-cards">';

  for (var i = 0; i < faults.length; i++) {
    var f = faults[i];
    var color = mmiColor(f.expected_mmi);

    html += '<div class="fault-card" style="border-top: 3px solid ' + color + '">';
    html += '<div class="fault-name">' + esc(f.name) + '</div>';
    html += '<div class="fault-props">';

    html += faultProp('Distance', kmToMi(f.distance_km));
    html += faultProp('Type', f.type);

    // MMI as badge
    html += '<div>';
    html += '<div class="fault-prop-label">Expected MMI</div>';
    html += '<div class="mmi-badge" style="background:' + color + '">' + mmiLabel(f.expected_mmi) + '</div>';
    html += ' <span style="font-family:var(--font-mono); font-size:0.78rem; color:var(--text-dim)">' + f.expected_mmi.toFixed(1) + '</span>';
    html += '</div>';

    if (f.ucerf3_prob != null) {
      html += faultProp('UCERF3 30yr', (f.ucerf3_prob * 100).toFixed(1) + '%');
    } else if (f.slip_rate_mm_yr != null) {
      html += faultProp('Slip Rate', f.slip_rate_mm_yr + ' mm/yr');
    } else {
      html += faultProp('UCERF3 30yr', 'N/A');
    }

    html += '</div>'; // fault-props
    html += '</div>'; // fault-card
  }

  html += '</div>';
  return html;
}

function faultProp(label, value) {
  return '<div>' +
    '<div class="fault-prop-label">' + esc(label) + '</div>' +
    '<div class="fault-prop-value">' + esc(String(value)) + '</div>' +
    '</div>';
}

// ── Footer ──

function renderFooter() {
  return '<div class="report-footer">' +
    '<div class="report-footer-text">' +
    'Data: USGS UCERF3, FEMA NFHL, CAL FIRE FHSZ, CGS Seismic Hazard Zones, ' +
    'NOAA SLR, Cal EnviroScreen 4.0, DTSC EnviroStor, SWRCB GeoTracker, ' +
    'FAA TFMSC, Caltrans AADT, USGS CoSMoS.<br><br>' +
    'Contains modified Copernicus Sentinel data (2016&ndash;2025), processed by NASA JPL OPERA project.<br><br>' +
    'This report is for informational purposes only and does not constitute professional ' +
    'geological, environmental, or engineering advice. Consult qualified professionals ' +
    'before making property decisions.' +
    '</div>' +
    '<div class="report-footer-links">' +
    '<a href="https://github.com/paulbaumgart/cahazards" target="_blank" rel="noopener">GitHub</a>' +
    '<a href="https://github.com/paulbaumgart/cahazards/issues" target="_blank" rel="noopener">Report a Problem</a>' +
    '</div>' +
    '</div>';
}

// ── Utilities ──

function riskToFlagClass(level) {
  if (!level) return 'flag-low';
  var l = level.toLowerCase();
  if (l === 'severe') return 'flag-severe';
  if (l === 'high') return 'flag-high';
  if (l === 'moderate') return 'flag-moderate';
  if (l === 'elevated') return 'flag-elevated';
  return 'flag-low';
}

function formatNumber(n) {
  if (n == null) return '--';
  return n.toLocaleString();
}

// ── Hazard Documentation Modal ──

function showHazardDoc(hazardKey) {
  var docName = HAZARD_DOCS[hazardKey];
  if (!docName) return;

  // Create modal if it doesn't exist
  var modal = document.getElementById('hazard-doc-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'hazard-doc-modal';
    modal.className = 'doc-modal-overlay';
    modal.innerHTML =
      '<div class="doc-modal">' +
        '<div class="doc-modal-header">' +
          '<span class="doc-modal-title"></span>' +
          '<button class="doc-modal-close" onclick="closeHazardDoc()">&times;</button>' +
        '</div>' +
        '<div class="doc-modal-body"></div>' +
      '</div>';
    document.body.appendChild(modal);
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closeHazardDoc();
    });
  }

  var title = modal.querySelector('.doc-modal-title');
  var body = modal.querySelector('.doc-modal-body');
  title.textContent = HAZARD_LABELS[hazardKey] + ' Model';
  body.innerHTML = '<div class="doc-loading">Loading...</div>';
  modal.classList.add('visible');

  // Fetch markdown from docs/
  fetch('/docs/' + docName + '.md')
    .then(function(r) { return r.ok ? r.text() : Promise.reject(r.status); })
    .then(function(md) { body.innerHTML = renderMarkdown(md); })
    .catch(function() { body.innerHTML = '<p>Documentation not available.</p>'; });
}

function closeHazardDoc() {
  var modal = document.getElementById('hazard-doc-modal');
  if (modal) modal.classList.remove('visible');
}

// Lightweight markdown renderer (headings, paragraphs, bold, italic, lists, tables, code)
function renderMarkdown(md) {
  var lines = md.split('\n');
  var html = '';
  var inList = false;
  var inTable = false;

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];

    // Skip table separator rows
    if (/^\s*\|[\s\-:|]+\|\s*$/.test(line)) continue;

    // Table rows
    if (/^\s*\|/.test(line)) {
      var cells = line.split('|').filter(function(c) { return c.trim() !== ''; });
      if (!inTable) { html += '<table class="doc-table">'; inTable = true; }
      var tag = (i > 0 && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i-1])) ? 'td' : (/^\s*\|[\s\-:|]+\|\s*$/.test(lines[i+1]) ? 'th' : 'td');
      html += '<tr>' + cells.map(function(c) { return '<' + tag + '>' + inlineFormat(c.trim()) + '</' + tag + '>'; }).join('') + '</tr>';
      continue;
    }
    if (inTable) { html += '</table>'; inTable = false; }

    // Headings
    var hMatch = line.match(/^(#{1,4})\s+(.+)/);
    if (hMatch) {
      if (inList) { html += '</ul>'; inList = false; }
      var level = hMatch[1].length;
      html += '<h' + level + ' class="doc-h">' + inlineFormat(hMatch[2]) + '</h' + level + '>';
      continue;
    }

    // List items
    if (/^\s*[-*]\s/.test(line)) {
      if (!inList) { html += '<ul class="doc-list">'; inList = true; }
      html += '<li>' + inlineFormat(line.replace(/^\s*[-*]\s+/, '')) + '</li>';
      continue;
    }
    if (inList) { html += '</ul>'; inList = false; }

    // Empty line
    if (line.trim() === '') continue;

    // Paragraph
    html += '<p>' + inlineFormat(line) + '</p>';
  }

  if (inList) html += '</ul>';
  if (inTable) html += '</table>';
  return html;
}

function inlineFormat(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

// ── Client-side earthquake computation ──
// Fetches USGS NSHMP hazard curve and integrates against HAZUS W1 fragility.
// This runs in the browser to avoid the ~1s API latency in the worker.

var NSHMP_VS30_VALUES = [180, 259, 360, 537, 760, 1150, 2000];

function nearestVs30(vs30) {
  var best = NSHMP_VS30_VALUES[0];
  for (var i = 1; i < NSHMP_VS30_VALUES.length; i++) {
    if (Math.abs(vs30 - NSHMP_VS30_VALUES[i]) < Math.abs(vs30 - best)) {
      best = NSHMP_VS30_VALUES[i];
    }
  }
  return best;
}

function pgaToMMI(pga) {
  var pgaCms2 = pga * 980.665;
  if (pgaCms2 <= 0) return 1;
  var logPGA = Math.log10(pgaCms2);
  return logPGA < 1.57 ? 1.78 + 1.55 * logPGA : -1.60 + 3.70 * logPGA;
}

function woodFrameDamageProb(mmi) {
  if (mmi < 5) return 0;
  var curve = [[5, 0.01], [6, 0.05], [7, 0.15], [8, 0.35], [9, 0.60], [10, 0.80]];
  if (mmi >= 10) return 0.80;
  for (var i = 0; i < curve.length - 1; i++) {
    if (mmi >= curve[i][0] && mmi < curve[i + 1][0]) {
      return curve[i][1] + (curve[i + 1][1] - curve[i][1]) * (mmi - curve[i][0]) / (curve[i + 1][0] - curve[i][0]);
    }
  }
  return 0;
}

function integrateNSHMPDamage(xvalues, yvalues, retrofitted) {
  var retrofitMult = retrofitted ? 0.3 : 1.0;
  var annualDamage = 0;
  for (var i = 0; i < xvalues.length - 1; i++) {
    var pgaMid = (xvalues[i] + xvalues[i + 1]) / 2;
    var pBin = yvalues[i] - yvalues[i + 1];
    if (pBin <= 0) continue;
    annualDamage += pBin * woodFrameDamageProb(pgaToMMI(pgaMid)) * retrofitMult;
  }
  var lastPGA = xvalues[xvalues.length - 1];
  var lastExceed = yvalues[yvalues.length - 1];
  if (lastExceed > 0) {
    annualDamage += lastExceed * woodFrameDamageProb(pgaToMMI(lastPGA)) * retrofitMult;
  }
  return annualDamage;
}

function computeLiquefactionFromNSHMP(xvalues, yvalues, inZone) {
  if (!inZone) return 0;
  var pExceed01g = 0;
  for (var i = 0; i < xvalues.length - 1; i++) {
    if (xvalues[i] <= 0.1 && xvalues[i + 1] >= 0.1) {
      var frac = (0.1 - xvalues[i]) / (xvalues[i + 1] - xvalues[i]);
      pExceed01g = yvalues[i] + frac * (yvalues[i + 1] - yvalues[i]);
      break;
    }
  }
  return pExceed01g * 0.30 * 0.40;
}

function p30(annual) {
  return 1 - Math.pow(1 - annual, 30);
}

function fetchAndComputeEarthquake(data, callback) {
  var vs30 = data.vs30 || 760;
  var lat = data.coordinates.lat;
  var lon = data.coordinates.lon;
  var liqZone = data.zones && data.zones.liquefaction;
  var supportedVs30 = nearestVs30(vs30);

  var url = 'https://earthquake.usgs.gov/nshmp-haz-ws/hazard'
    + '?edition=E2014&region=COUS'
    + '&longitude=' + lon.toFixed(4) + '&latitude=' + lat.toFixed(4)
    + '&imt=PGA&vs30=' + supportedVs30;

  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(resp) {
      if (resp.status !== 'success') { callback(null, 'USGS earthquake service returned an error'); return; }
      var response = resp.response && resp.response[0];
      if (!response) { callback(null, 'USGS earthquake service returned no data'); return; }
      var totalData = null;
      for (var i = 0; i < (response.data || []).length; i++) {
        if (response.data[i].component === 'Total') {
          totalData = response.data[i];
          break;
        }
      }
      if (!totalData) { callback(null, 'USGS earthquake service returned no hazard curve'); return; }

      var xvalues = response.metadata.xvalues;
      var yvalues = totalData.yvalues;

      // Compute for both retrofit states
      var shakingAnnual = integrateNSHMPDamage(xvalues, yvalues, false);
      var liqAnnual = computeLiquefactionFromNSHMP(xvalues, yvalues, liqZone);
      var eqAnnual = 1 - (1 - shakingAnnual) * (1 - liqAnnual);

      var shakingRetro = integrateNSHMPDamage(xvalues, yvalues, true);
      var eqAnnualRetro = 1 - (1 - shakingRetro) * (1 - liqAnnual);

      callback({
        earthquake: { annual_p: eqAnnual, p30yr: p30(eqAnnual) },
        earthquake_retrofitted: { annual_p: eqAnnualRetro, p30yr: p30(eqAnnualRetro) },
      });
    })
    .catch(function(err) {
      callback(null, 'Could not reach USGS earthquake service: ' + (err.message || 'network error'));
    });
}
