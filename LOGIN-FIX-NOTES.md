# Headway login frame-detection fix

This patch addresses the observed failure where the page screenshot visibly showed
Login / Password / Headway-Demo controls, but Playwright could not find the login
input from the top-level page.

Changes:
- Search the main document and every attached iframe for visible controls.
- Log frame URLs and safe input metadata (never field values/passwords).
- Require a positive visible `Headway-Demo` label before filling credentials.
- Expand login/password/connect selectors to include common HTML attributes.
- Add a TypeScript compile step to GitHub Actions before browser execution.
- Keep the workflow in login-diagnostic mode only. It does not place orders.

After committing, run the workflow once and download the artifact. The next run
will include `automation.log`, a screenshot, and frame metadata. If the login form
is rendered in a frame, the frame-aware locators should now be able to locate it.
If the website uses a closed shadow root, canvas-rendered form, or a different
input structure, the diagnostics will provide evidence for the next targeted fix.

No anti-bot challenge is bypassed. If an access challenge is presented, the run
stops and saves diagnostics.
