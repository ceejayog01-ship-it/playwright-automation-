#!/usr/bin/env node
import { Command } from 'commander';
import * as dotenv from 'dotenv';
import { launchBrowser, closeBrowser } from './browser';
import { performLogin } from './login';
import { placeSimpleMarketOrder } from './order';
import { screenshotOnFailure } from './utils';

dotenv.config();

const program = new Command();
program
  .option('--mode <mode>', 'login | order', 'login')
  .option('--side <side>', 'buy | sell', 'buy')
  .parse(process.argv);

const options = program.opts();

async function main() {
  const login = process.env.MT5_LOGIN;
  const password = process.env.MT5_PASSWORD;
  const url = process.env.WEB_TERMINAL_URL || 'https://hw.online/webterminal/mt5-demo/';

  if (!login || !password) {
    console.error('Missing MT5_LOGIN or MT5_PASSWORD in .env');
    process.exit(1);
  }

  console.log('=== Headway MT5 Web Terminal Automation (DEMO ONLY) ===');
  console.log(`Mode: ${options.mode}`);
  console.log(`URL:  ${url}`);
  console.log('-------------------------------------------------------');

  const { browser, context, page } = await launchBrowser();

  try {
    // Always login first
    const loggedIn = await performLogin(page, { login, password, url });
    if (!loggedIn) {
      console.error('Login failed. Aborting.');
      process.exitCode = 1;
      return;
    }

    if (options.mode === 'order') {
      const side = options.side === 'sell' ? 'sell' : 'buy';
      const ok = await placeSimpleMarketOrder(page, side);
      if (!ok) {
        console.error('Order placement failed.');
        process.exitCode = 1;
      }
    } else {
      console.log('Login-only mode finished successfully.');
      console.log('You can now inspect the browser window or run with --mode=order');
    }

    // Keep browser open a bit so you can visually verify (especially useful when HEADLESS=false)
    if (process.env.HEADLESS !== 'true') {
      console.log('Keeping browser open for 20 seconds for visual inspection...');
      await page.waitForTimeout(20000);
    }
  } catch (err) {
    console.error('Unexpected error:', err);
    await screenshotOnFailure(page, 'unexpected-error');
    process.exitCode = 1;
  } finally {
    await closeBrowser(browser, context);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
