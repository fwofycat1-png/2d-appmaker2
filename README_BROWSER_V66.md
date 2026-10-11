# 2d appmaker V66 — original Python browser-port starter

This is the **original V65 Python/Pygame source**, not a rewritten imitation. `browser_app/main.py` is a syntax-checked browser-adapted copy. The project is **not yet verified to run in a browser**.

## How to try it

1. Back up your existing GitHub repository or create a separate branch first.
2. Upload `browser_app/main.py` and `.github/workflows/pages.yml` preserving the directory structure. (GitHub web upload may not preserve hidden folders reliably; `git add` is preferable.)
3. In GitHub repository Settings → Pages → Build and deployment, choose **GitHub Actions**.
4. Go to Actions and run the workflow (or push to `main`).
5. If build and deployment succeed, open `https://fwofycat1-png.github.io/2d-appmaker/` and inspect browser developer console for runtime errors.

### Local build (on a machine with internet)

```bash
python3 -m pip install pygbag
python3 -m pygbag --build browser_app
```

The GitHub workflow expects generated files under `browser_app/build/web`. If your pygbag version outputs elsewhere, adjust the workflow.

## Limitations — read before replacing your website

- **No end-to-end browser test has been performed.** This environment cannot download/install `pygbag`, so runtime and asset loading remain unverified.
- Browser import dialogs (`choose_file`) are disabled with an explicit message; PNG, audio, and project import are not yet implemented.
- The browser's virtual filesystem is not automatically persistent across reloads. Saving/exporting projects to the user's computer needs a browser download bridge.
- Desktop building, subprocess launches, macOS AppleScript, and keychain-backed login do not run in browser. Online Supabase features have not been adapted or verified.
- Pygame display sizing, memory usage, and bundled fonts may require adjustments.
- GitHub Actions may need further configuration or patches based on its build logs.

**Do not delete the V65 desktop source or promise users the browser editor is finished.** This is the first porting/build milestone, not a completed web app.
