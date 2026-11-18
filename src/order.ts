import { Page } from 'playwright';
import { Selectors } from '../config/selectors';
import { safeClick, findFirstVisible, screenshotOnFailure, sleep } from './utils';

/**
 * Very basic market order helper.
 * This is intentionally minimal for the first resilient version.
 * Later you can expand it to set exact volume, SL, TP, symbol, etc.
 */
export async function placeSimpleMarketOrder(
  page: Page,
  side: 'buy' | 'sell' = 'buy'
): Promise<boolean> {
  console.log(`[order] Attempting simple ${side.toUpperCase()} market order`);

  // Make sure we are on a view that shows the SELL/BUY bar
  // (on mobile it is always visible on Chart; on desktop it may need Trade tab)
  await safeClick(page, Selectors.trading.chartTab, 'Chart tab').catch(() => {});
  await sleep(1500);

  const buttonSelectors = side === 'buy' ? Selectors.trading.buyButton : Selectors.trading.sellButton;

  const clicked = await safeClick(page, buttonSelectors, `${side.toUpperCase()} button`);
  if (!clicked) {
    await screenshotOnFailure(page, `order-${side}-button-missing`);
    return false;
  }

  // Give the terminal time to process
  await sleep(3000);

  // Very rough success check – look for any change or confirmation
  // (real confirmation logic would inspect the Trade/Positions tab)
  console.log(`[order] ${side.toUpperCase()} click sent. Check positions manually for now.`);
  await screenshotOnFailure(page, `order-${side}-clicked`);
  return true;
}
