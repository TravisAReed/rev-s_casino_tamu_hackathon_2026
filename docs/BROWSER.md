# Browser preparation

The runner attaches to an already-running Chromium over CDP on
`127.0.0.1:9333`. It never handles your Google password.

## Easy path

```bash
./browser/start.sh
```

This launches one long-lived headed Chromium with a dedicated profile under
`.pw-profile/` and CDP on `127.0.0.1:9333`, and opens the casino entrance. It is
idempotent: if something is already on `:9333`, it just reports that.

## Hardened Linux: "No usable sandbox"

Some Linux installs block Chromium's sandbox
(`kernel.apparmor_restrict_unprivileged_userns=1`), which shows as
`FATAL: ... No usable sandbox!`. Launch with the sandbox off:

```bash
CHROME_FLAGS="--no-sandbox --disable-setuid-sandbox" ./browser/start.sh
```

Turning off the sandbox only weakens that one browser instance. Use a dedicated
profile (the default `.pw-profile/`) and only visit td-revs in it.

## Manual launch

```bash
CHROME="$(ls -d ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome | tail -1)"
"$CHROME" --user-data-dir="$PWD/.pw-profile" \
  --remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 \
  --no-first-run --no-default-browser-check \
  https://td-revs.vercel.app/
```

## Sign in

In that Chromium window, open https://td-revs.vercel.app/ and sign in with **your
own Google account**. Scores only count on the signed-in account. Leave the
window open while the runner works.

## Verify

```bash
curl -s http://127.0.0.1:9333/json/version
```

An HTTP 200 with a JSON body means the runner can attach. `.pw-profile/` holds your
login cookies — never commit it or share it.
