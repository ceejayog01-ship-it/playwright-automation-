/**
 * Centralized selectors for Headway MT5 Web Terminal
 * Multiple strategies per element so the code can fall back when the UI changes.
 * Update ONLY this file when MetaQuotes/Headway changes the page.
 */

export const Selectors = {
  // ---------- Login page ----------
  login: {
    // Primary: placeholder text (very stable from your screenshot)
    loginInput: [
      'input[placeholder="Enter Login"]',
      'input[placeholder*="Login" i]',
      'input[type="text"]',
      '[role="textbox"][name*="login" i]',
    ],
    passwordInput: [
      'input[placeholder="Enter Password"]',
      'input[placeholder*="Password" i]',
      'input[type="password"]',
    ],
    savePasswordCheckbox: [
      'input[type="checkbox"]',
      'label:has-text("Save password") input',
      '[role="checkbox"]',
    ],
    connectButton: [
      'button:has-text("Connect to account")',
      'button:has-text("Connect")',
      'button.green, button[style*="green"]',
      '[role="button"]:has-text("Connect")',
    ],
    // Server is shown as text "Headway-Demo" in your screenshot
    serverText: [
      'text=Headway-Demo',
      'text=/Headway.*Demo/i',
      'div:has-text("Server")',
    ],
  },

  // ---------- Post-login / Trading UI ----------
  trading: {
    // Top bar SELL / volume / BUY (from second screenshot)
    sellButton: [
      'button:has-text("SELL")',
      '[class*="sell"]',
      'div:has-text("SELL")',
    ],
    buyButton: [
      'button:has-text("BUY")',
      '[class*="buy"]',
      'div:has-text("BUY")',
    ],
    volumeInput: [
      'input[type="number"]',
      'input[value="0.01"]',
      '[class*="volume"] input',
      'div:has-text("0.01")',
    ],
    // Bottom navigation
    tradeTab: [
      'text=Trade',
      '[role="tab"]:has-text("Trade")',
      'button:has-text("Trade")',
    ],
    quotesTab: [
      'text=Quotes',
      '[role="tab"]:has-text("Quotes")',
    ],
    chartTab: [
      'text=Chart',
      '[role="tab"]:has-text("Chart")',
    ],
  },

  // ---------- Generic helpers ----------
  common: {
    loadingSpinner: [
      '[class*="loading"]',
      '[class*="spinner"]',
      'text=Verifying',
      'text=Loading',
    ],
    errorMessage: [
      '[class*="error"]',
      'text=/invalid|failed|error|denied/i',
    ],
  },
} as const;

export type SelectorGroup = typeof Selectors;
