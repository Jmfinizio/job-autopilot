/**
 * Job Autopilot: Sheet + Gmail bridge
 *
 * Writes to this Sheet, reads your application emails, and sends your digest,
 * all as you, so no app passwords or Google Cloud setup are needed.
 *
 * Paste this whole file into your Google Sheet: Extensions -> Apps Script.
 * Then set SECRET below, click Deploy -> New deployment -> Web app,
 *   Execute as: Me
 *   Who has access: Anyone
 * and copy the Web app URL into the GitHub secret SHEET_SCRIPT_URL.
 *
 * "Anyone" only means GitHub can reach this URL without a Google login.
 * Every request must include SECRET, or it is refused, so keep SECRET long and random.
 */
const SECRET = 'PASTE-A-LONG-RANDOM-SECRET-HERE';

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return out_({ error: 'bad request' });
  }
  if (!SECRET || SECRET.indexOf('PASTE-') === 0) return out_({ error: 'SECRET is not set in Code.gs' });
  if (req.secret !== SECRET) return out_({ error: 'unauthorized' });

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    switch (req.op) {
      case 'info':
        return out_({ ok: true, url: ss.getUrl(), name: ss.getName() });
      case 'ensure':
        return out_(ensure_(ss, req.tabs));
      case 'values':
        return out_({ ok: true, values: sheet_(ss, req.tab).getDataRange().getDisplayValues() });
      case 'append':
        return out_(append_(sheet_(ss, req.tab), req.rows));
      case 'update':
        return out_(update_(sheet_(ss, req.tab), req.row, req.values));
      case 'replace':
        return out_(replace_(sheet_(ss, req.tab), req.rows));
      case 'gmail_search':
        return out_(gmailSearch_(req.query, req.limit));
      case 'send_email':
        return out_(sendEmail_(req));
      default:
        return out_({ error: 'unknown op ' + req.op });
    }
  } catch (err) {
    return out_({ error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return ContentService.createTextOutput('Job Autopilot bridge is running.');
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function sheet_(ss, name) {
  const sh = ss.getSheetByName(name);
  if (!sh) throw new Error('missing tab ' + name);
  return sh;
}

// Keep everything as plain text so dates like 2026-11-15 aren't reformatted.
function asText_(range) {
  range.setNumberFormat('@');
  return range;
}

function ensure_(ss, tabs) {
  const created = [];
  Object.keys(tabs).forEach(function (name) {
    const cols = tabs[name];
    let sh = ss.getSheetByName(name);
    if (!sh) {
      sh = ss.insertSheet(name);
      created.push(name);
    }
    if (sh.getLastRow() === 0) {
      asText_(sh.getRange(1, 1, Math.max(sh.getMaxRows(), 1000), cols.length));
      sh.getRange(1, 1, 1, cols.length).setValues([cols]).setFontWeight('bold');
      sh.setFrozenRows(1);
    }
  });
  const s1 = ss.getSheetByName('Sheet1');
  if (s1 && s1.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(s1);
  const st = ss.getSheetByName('_state');
  if (st && !st.isSheetHidden()) st.hideSheet();
  return { ok: true, created: created };
}

function append_(sh, rows) {
  if (!rows || !rows.length) return { ok: true, added: 0 };
  const start = sh.getLastRow() + 1;
  asText_(sh.getRange(start, 1, rows.length, rows[0].length)).setValues(rows);
  return { ok: true, added: rows.length };
}

function update_(sh, row, values) {
  asText_(sh.getRange(row, 1, 1, values.length)).setValues([values]);
  return { ok: true };
}

function replace_(sh, rows) {
  sh.clearContents();
  if (rows && rows.length) {
    const width = Math.max.apply(null, rows.map(function (r) { return r.length; }));
    const padded = rows.map(function (r) { while (r.length < width) r.push(''); return r; });
    asText_(sh.getRange(1, 1, padded.length, width)).setValues(padded);
    sh.getRange(1, 1, 1, width).setFontWeight('bold');
  }
  return { ok: true };
}

// ---------------------------------------------------------------- Gmail (read-only + send to yourself)
function gmailSearch_(query, limit) {
  // "-from:me" skips emails you sent; no extra permissions needed
  const q = (query || 'newer_than:3d') + ' -from:me';
  const threads = GmailApp.search(q, 0, Math.min(limit || 100, 200));
  const messages = [];
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      if (m.isDraft && m.isDraft()) return;
      messages.push({
        id: m.getId(), threadId: t.getId(), date: m.getDate().toISOString(),
        from: m.getFrom(), subject: m.getSubject(), body: (m.getPlainBody() || '').slice(0, 6000)
      });
    });
  });
  return { ok: true, messages: messages };   // nothing is marked read, moved, or deleted
}

function sendEmail_(req) {
  const to = req.to || myAddress_();
  if (!to) return { error: "Couldn't find your email address. Add a DIGEST_TO secret in GitHub." };
  GmailApp.sendEmail(to, req.subject, req.text || '', { htmlBody: req.html || '', name: 'Job Autopilot' });
  return { ok: true, to: to };
}

// Your own address, read from your most recent sent email (avoids an extra permission)
function myAddress_() {
  const sent = GmailApp.search('in:sent', 0, 1);
  if (!sent.length) return '';
  const from = sent[0].getMessages()[0].getFrom() || '';
  const m = from.match(/<([^>]+)>/);
  return (m ? m[1] : from).trim();
}

/**
 * Run this once from the editor (pick "authorize" next to Debug, then Run)
 * to approve Gmail access. The web app can't show Google's approval screen itself.
 */
function authorize() {
  GmailApp.getInboxUnreadCount();
  // also approves the optional "Run now" button (talks to GitHub)
  UrlFetchApp.getRequest('https://api.github.com');
}

// =================================================================== Dashboard (inside the Sheet)
// Adds a "Job Autopilot" menu to the Sheet. Only people who can open this Sheet can see it.

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Job Autopilot')
    .addItem('Open dashboard', 'openDashboard')
    .addToUi();
}

function openDashboard() {
  const html = HtmlService.createHtmlOutputFromFile('Dashboard').setWidth(1240).setHeight(820);
  SpreadsheetApp.getUi().showModelessDialog(html, 'Job Autopilot');
}

function tableRecords_(name) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh || sh.getLastRow() < 1) return [];
  const values = sh.getDataRange().getDisplayValues();
  const header = values[0];
  return values.slice(1)
    .map(function (row, i) {
      const o = { _row: i + 2 };
      header.forEach(function (h, j) { o[h] = row[j]; });
      return o;
    })
    .filter(function (o) { return header.some(function (h) { return String(o[h] || '').trim(); }); });
}

function getDashboardData() {
  const props = PropertiesService.getScriptProperties();
  const state = tableRecords_('_state');
  const lastRun = state.reduce(function (m, r) { return r.When > m ? r.When : m; }, '');
  const resume = {};
  tableRecords_('Resume').forEach(function (r) { resume[String(r.Field).toLowerCase()] = r.Value; });
  return {
    jobs: tableRecords_('Jobs'),
    apps: tableRecords_('Applications'),
    settings: tableRecords_('Settings'),
    resume: { file: resume.file || '', updated: resume.updated || '', skills: resume.skills || '',
              roles: resume.roles || '', chars: (resume.text || '').length },
    lastRun: lastRun,
    runNow: Boolean(props.getProperty('GITHUB_TOKEN') && props.getProperty('GITHUB_REPO')),
    repo: props.getProperty('GITHUB_REPO') || '',
    sheetUrl: SpreadsheetApp.getActiveSpreadsheet().getUrl()
  };
}

function findRow_(sh, colName, value) {
  const values = sh.getDataRange().getDisplayValues();
  const col = values[0].indexOf(colName);
  for (let i = 1; i < values.length; i++) if (values[i][col] === value) return { row: i + 1, header: values[0] };
  return null;
}

function setCell_(sh, row, header, colName, value) {
  const c = header.indexOf(colName);
  if (c >= 0) sh.getRange(row, c + 1).setNumberFormat('@').setValue(value);
}

function saveSettings(pairs) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Settings');
  pairs.forEach(function (p) {
    const hit = findRow_(sh, 'Setting', p[0]);
    if (hit) setCell_(sh, hit.row, hit.header, 'Value', p[1]);
    else sh.appendRow([p[0], p[1], '']);
  });
  return { ok: true };
}

function saveResume(r) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName('Resume');
  if (!sh) { sh = ss.insertSheet('Resume'); sh.getRange(1, 1, 1, 2).setValues([['Field', 'Value']]).setFontWeight('bold'); }
  sh.getRange(2, 1, Math.max(sh.getMaxRows() - 1, 1), 2).clearContent();
  const rows = [
    ['file', r.file || ''],
    ['updated', Utilities.formatDate(new Date(), 'America/New_York', 'yyyy-MM-dd HH:mm')],
    ['skills', (r.skills || []).join(', ')],
    ['roles', (r.roles || []).join(', ')],
    ['text', String(r.text || '').slice(0, 45000)]
  ];
  sh.getRange(2, 1, rows.length, 2).setNumberFormat('@').setValues(rows);
  if (r.addRoles && r.addRoles.length) {
    const st = ss.getSheetByName('Settings');
    const hit = findRow_(st, 'Setting', 'roles');
    const current = hit ? String(st.getRange(hit.row, hit.header.indexOf('Value') + 1).getValue()) : '';
    const merged = current.split(',').map(function (s) { return s.trim(); }).filter(String);
    r.addRoles.forEach(function (x) { if (merged.indexOf(x) < 0) merged.push(x); });
    saveSettings([['roles', merged.join(', ')]]);
  }
  return { ok: true };
}

function updateJobStatus(jobId, status) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Jobs');
  const hit = findRow_(sh, 'Job ID', jobId);
  if (!hit) throw new Error('Job not found');
  setCell_(sh, hit.row, hit.header, 'Status', status);
  return { ok: true };
}

function updateApplication(key, fields) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Applications');
  const hit = findRow_(sh, 'Key', key);
  if (!hit) throw new Error('Application not found');
  Object.keys(fields).forEach(function (k) { setCell_(sh, hit.row, hit.header, k, fields[k]); });
  if (fields.Status) setCell_(sh, hit.row, hit.header, 'Last Update',
    Utilities.formatDate(new Date(), 'America/New_York', 'yyyy-MM-dd'));
  return { ok: true };
}

function addApplication(app) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Applications');
  const today = Utilities.formatDate(new Date(), 'America/New_York', 'yyyy-MM-dd');
  const follow = Utilities.formatDate(new Date(Date.now() + 10 * 864e5), 'America/New_York', 'yyyy-MM-dd');
  const key = (app.company + '|' + app.role).toLowerCase().replace(/[^a-z0-9|]+/g, ' ').trim();
  const header = sh.getRange(1, 1, 1, sh.getLastColumn()).getDisplayValues()[0];
  const row = header.map(function (h) {
    return ({ Company: app.company, Role: app.role, Status: app.status || 'Applied', Applied: today,
              'Last Update': today, 'Follow Up By': follow, Notes: app.notes || '', Key: key })[h] || '';
  });
  sh.getRange(sh.getLastRow() + 1, 1, 1, row.length).setNumberFormat('@').setValues([row]);
  return { ok: true };
}

// ---- optional "Run now" button: triggers the GitHub workflow
function saveGithub(repo, token) {
  const props = PropertiesService.getScriptProperties();
  if (repo) props.setProperty('GITHUB_REPO', repo.trim());
  if (token) props.setProperty('GITHUB_TOKEN', token.trim());
  return { ok: true };
}

function runNow(command) {
  const props = PropertiesService.getScriptProperties();
  const repo = props.getProperty('GITHUB_REPO'), token = props.getProperty('GITHUB_TOKEN');
  if (!repo || !token) throw new Error('Set up "Run now" in Settings first');
  const res = UrlFetchApp.fetch('https://api.github.com/repos/' + repo + '/actions/workflows/autopilot.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: { command: command || 'all', force: false } })
  });
  if (res.getResponseCode() >= 300) throw new Error('GitHub said ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
  return { ok: true };
}
