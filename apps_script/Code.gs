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
