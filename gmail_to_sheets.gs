/**
 * Gmail → Google Sheets Transaction Sync
 *
 * Parses bank transaction alert emails and appends to the Transactions sheet.
 * Supported (so far):  ICICI Bank CC,  Axis Bank CC
 * Pending samples:     ICICI Savings,  Axis Savings,  Amex (enable alerts first)
 *
 * SETUP (one-time):
 *   1. Go to script.google.com → New project → paste this code
 *   2. Run  setupTriggers()  once → authorise when prompted
 *   3. Script will then run every 15 minutes automatically
 *
 * HOW IT WORKS:
 *   - Searches Gmail for unprocessed alert emails from each bank
 *   - Parses amount, date, description, card/account number
 *   - Deduplicates against existing sheet rows (same date + amount)
 *   - Appends new rows to the Transactions tab
 *   - Labels processed emails "synced-to-sheets" so they are never re-read
 */

// ─── Config ──────────────────────────────────────────────────────────────────

const SHEET_ID        = '1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY';
const TXN_SHEET_NAME  = 'Transactions';
const PROCESSED_LABEL = 'synced-to-sheets';

// Maps card last-4 (as shown in alert emails) → canonical account name in Sheets
// Update ICICI_CC_ACCOUNT if your CC account name in Buxfer is different.
// Update AXIS_CC_ACCOUNT with the exact Axis CC account name from your Buxfer/Sheets Accounts tab.
const ICICI_CC_ACCOUNT = 'ICICIBankSapphiroVisaCreditCard xxxxards';
const AXIS_CC_ACCOUNT  = 'Axis Bank CC';

const CARD_ACCOUNT_MAP = {
  'XX6005': ICICI_CC_ACCOUNT,
  'XX4000': ICICI_CC_ACCOUNT,
  'XX5006': ICICI_CC_ACCOUNT,
  'XX3784': ICICI_CC_ACCOUNT,
  'XX3829': ICICI_CC_ACCOUNT,
  'XX5825': AXIS_CC_ACCOUNT,   // Axis Burgundy CC

};

// ─── Simplified Tagging ───────────────────────────────────────────────────────
// Mirrors key rules from tagging_rules.json.  Add new entries as needed.

const TAG_RULES = [
  { re: /swiggy|zomato|blinkit|zepto|dunzo/i,            tags: 'Food' },
  { re: /amazon pay/i,                                    tags: 'Shopping' },
  { re: /amazon(?!.*pay)|flipkart|myntra|meesho|ajio/i,  tags: 'Shopping' },
  { re: /netflix|spotify|hotstar|prime\s*video|youtube\s*premium/i, tags: 'Entertainment' },
  { re: /uber|ola\s*cab|rapido/i,                         tags: 'Transport' },
  { re: /petrol|fuel|hp\s*petroleum|iocl|bpcl|hpcl/i,    tags: 'Fuel' },
  { re: /hospital|clinic|pharmacy|medical|apollo|manipal|fortis/i, tags: 'Medical' },
  { re: /school|tuition|byju|unacademy|coursera/i,        tags: 'Education' },
  { re: /airtel|jio|vodafone|vi\b|bsnl|act\s*fibernet|broadband/i, tags: 'Utilities' },
  { re: /electricity|bescom|tpddl|msedcl|water\s*bill/i, tags: 'Utilities' },
  { re: /bigbasket|dmart|reliance\s*smart|nature.s\s*basket|more\s*supermarket/i, tags: 'Grocery' },
  { re: /bharatpe/i,                                      tags: 'Shopping' },
  { re: /irctc|railway|train\s*ticket/i,                  tags: 'Travel' },
  { re: /makemytrip|cleartrip|goibibo|indigo|air\s*india|spicejet/i, tags: 'Travel' },
];

function applyTags(description) {
  const d = description || '';
  for (const rule of TAG_RULES) {
    if (rule.re.test(d)) return rule.tags;
  }
  return '';
}

// ─── Date Helpers ─────────────────────────────────────────────────────────────

const MONTH_MAP = {
  Jan:1, Feb:2, Mar:3, Apr:4, May:5, Jun:6,
  Jul:7, Aug:8, Sep:9, Oct:10, Nov:11, Dec:12,
};

/** "Apr 26, 2026" → "2026-04-26" */
function parseIciciDate(s) {
  const m = s.match(/(\w{3})\s+(\d{1,2}),\s+(\d{4})/);
  if (!m) return null;
  const mo = String(MONTH_MAP[m[1]] || 1).padStart(2, '0');
  const dy = m[2].padStart(2, '0');
  return `${m[3]}-${mo}-${dy}`;
}

/** "24-03-2026" → "2026-03-24" */
function parseAxisDate(s) {
  const m = s.match(/(\d{2})-(\d{2})-(\d{4})/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : null;
}

// ─── Email Parsers ────────────────────────────────────────────────────────────

/**
 * ICICI Bank Credit Card
 * From:    credit_cards@icici.bank.in
 * Subject: Transaction alert for your ICICI Bank Credit Card
 * Sample:  "Your ICICI Bank Credit Card XX6005 has been used for a transaction of
 *           INR 1,000.00 on Apr 26, 2026 at 03:54:32. Info: UPI-648233625549-INDO IND."
 */
function parseIciciCcEmail(body) {
  // Match amount, date, time, description
  const m = body.match(
    /Credit Card\s+(XX\d+)\s+has been used for a transaction of INR\s+([\d,]+\.?\d*)\s+on\s+(\w+ \d+, \d{4})\s+at\s+([\d:]+)[.\s]*Info:\s*(.+?)(?:\n|\r|\.)/s
  );
  if (!m) return null;

  const card = m[1];
  return {
    card,
    amount:      parseFloat(m[2].replace(/,/g, '')),
    date:        parseIciciDate(m[3]),
    description: m[5].trim(),
    account:     CARD_ACCOUNT_MAP[card] || `ICICI CC ${card}`,
    source:      'email-icici-cc',
    type:        'expense',
  };
}

/**
 * Axis Bank Credit Card
 * From:    alerts@axis.bank.in
 * Subject: INR XXXXX spent on credit card no. XXNNNN
 * Body has structured fields:
 *   Transaction Amount: INR 13414.33
 *   Merchant Name:      AMAZON PAY
 *   Axis Bank Credit Card No.: XX5825
 *   Date & Time:        24-03-2026, 00:10:58 IST
 */
function parseAxisCcEmail(body) {
  const amtM  = body.match(/Transaction Amount[:\s]+INR\s*([\d,]+\.?\d*)/i);
  const mercM = body.match(/Merchant Name[:\s]+(.+?)(?:\r?\n)/i);
  const cardM = body.match(/Credit Card No\.?[:\s]+(XX\d+)/i);
  const dateM = body.match(/Date\s*&?\s*Time[:\s]+(\d{2}-\d{2}-\d{4})/i);

  if (!amtM || !cardM || !dateM) return null;

  const card = cardM[1];
  return {
    card,
    amount:      parseFloat(amtM[1].replace(/,/g, '')),
    date:        parseAxisDate(dateM[1]),
    description: mercM ? mercM[1].trim() : '',
    account:     CARD_ACCOUNT_MAP[card] || `Axis CC ${card}`,
    source:      'email-axis-cc',
    type:        'expense',
  };
}

/**
 * ICICI Bank Savings Account  ← PARSER STUB (need a sample alert email)
 * From:    alerts@icicibank.com  (verify sender)
 * TODO:    Add regex once sample is shared
 */
function parseIciciSavingsEmail(body) {
  // STUB — will be implemented once a sample alert is provided
  return null;
}

/**
 * Axis Bank Savings Account  ← PARSER STUB (need a sample alert email)
 * From:    alerts@axis.bank.in  (same sender, different body format)
 * TODO:    Add regex once sample is shared
 */
function parseAxisSavingsEmail(body) {
  // STUB — will be implemented once a sample alert is provided
  return null;
}

// ─── Sheet Writer ─────────────────────────────────────────────────────────────

/**
 * Appends a parsed transaction to the Transactions sheet.
 * Returns true if appended, false if duplicate.
 *
 * 14-column schema (matches sync_all.py):
 * id | description | amount | type | tags | date | month | year |
 * source | account_name | buxfer_id | expense_type | transfer_from | transfer_to
 */
function appendTransaction(txn) {
  if (!txn || !txn.date || !txn.amount) return false;

  const ss    = SpreadsheetApp.openById(SHEET_ID);
  const sheet = ss.getSheetByName(TXN_SHEET_NAME);
  const data  = sheet.getDataRange().getValues();
  const hdrs  = data[0];
  const colDate = hdrs.indexOf('date');
  const colAmt  = hdrs.indexOf('amount');

  // Dedup: same date + same abs(amount) — matches Python dedup logic
  const key = `${txn.date}|${Math.abs(txn.amount).toFixed(2)}`;
  for (let i = 1; i < data.length; i++) {
    const existingDate = String(data[i][colDate]).split('T')[0];   // handle Date objects
    const existingAmt  = Math.abs(parseFloat(data[i][colAmt]) || 0).toFixed(2);
    if (`${existingDate}|${existingAmt}` === key) {
      Logger.log(`Dedup skip: ${key}`);
      return false;
    }
  }

  const tags  = applyTags(txn.description);
  const d     = new Date(txn.date + 'T00:00:00');
  const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  const year  = String(d.getFullYear());
  const id    = `EMAIL_${txn.source}_${txn.card}_${txn.date}_${txn.amount}`.replace(/\s/g, '_');

  sheet.appendRow([
    id,
    txn.description,
    txn.amount,
    txn.type || 'expense',
    tags,
    txn.date,      // stored as text "YYYY-MM-DD"; Sheets will auto-format as date
    month,
    year,
    txn.source,
    txn.account,
    '',  // buxfer_id
    '',  // expense_type
    '',  // transfer_from
    '',  // transfer_to
  ]);

  Logger.log(`✓ Appended: ${txn.date} | ${txn.account} | ${txn.amount} | ${txn.description}`);
  return true;
}

// ─── Main Sync ────────────────────────────────────────────────────────────────

function syncEmailTransactions() {
  // Ensure the "synced-to-sheets" label exists
  let label = GmailApp.getUserLabelByName(PROCESSED_LABEL);
  if (!label) label = GmailApp.createLabel(PROCESSED_LABEL);

  // Each entry: Gmail search query + parser function
  // Add new banks here as parsers are implemented
  const SOURCES = [
    {
      q:      `from:credit_cards@icici.bank.in subject:"Transaction alert" -label:${PROCESSED_LABEL}`,
      parser: parseIciciCcEmail,
      name:   'ICICI CC',
    },
    {
      q:      `from:alerts@axis.bank.in subject:"spent on credit card" -label:${PROCESSED_LABEL}`,
      parser: parseAxisCcEmail,
      name:   'Axis CC',
    },
    // ICICI Savings — uncomment once parser is implemented
    // { q: `from:alerts@icicibank.com -label:${PROCESSED_LABEL}`, parser: parseIciciSavingsEmail, name: 'ICICI Savings' },
    // Axis Savings — uncomment once parser is implemented
    // { q: `from:alerts@axis.bank.in subject:"debited" -label:${PROCESSED_LABEL}`, parser: parseAxisSavingsEmail, name: 'Axis Savings' },
    // Amex — uncomment after enabling transaction alerts
    // { q: `from:AmericanExpress@welcome.americanexpress.com -label:${PROCESSED_LABEL}`, parser: parseAmexEmail, name: 'Amex' },
  ];

  let totalAdded = 0;
  let totalSkipped = 0;

  for (const src of SOURCES) {
    const threads = GmailApp.search(src.q, 0, 100);
    let srcAdded = 0, srcSkipped = 0;

    for (const thread of threads) {
      for (const msg of thread.getMessages()) {
        const txn = src.parser(msg.getPlainBody());
        if (txn && txn.date) {
          appendTransaction(txn) ? srcAdded++ : srcSkipped++;
        } else {
          Logger.log(`[${src.name}] Could not parse: "${msg.getSubject()}"`);
        }
      }
      thread.addLabel(label);   // mark as processed regardless of parse result
    }

    if (threads.length > 0) {
      Logger.log(`[${src.name}] Processed ${threads.length} threads → added ${srcAdded}, skipped ${srcSkipped}`);
    }
    totalAdded   += srcAdded;
    totalSkipped += srcSkipped;
  }

  Logger.log(`Sync complete. Total added: ${totalAdded}, Total dedup-skipped: ${totalSkipped}`);
}

// ─── One-Time Setup ───────────────────────────────────────────────────────────

/**
 * Run this function ONCE from the Apps Script editor to install the trigger.
 * After that, syncEmailTransactions() runs automatically every 15 minutes.
 */
function setupTriggers() {
  // Remove any existing triggers first (idempotent)
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('syncEmailTransactions')
    .timeBased()
    .everyMinutes(15)
    .create();

  Logger.log('✓ Trigger installed: syncEmailTransactions every 15 minutes.');
}
