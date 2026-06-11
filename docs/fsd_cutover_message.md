# Message to FSD (simple version)

> Paste-ready. Edit the placeholders before sending.

---

Hey, small frontend change needed.

We're moving the API to a new server. The base URL will change from:

```
https://tool.lawttorney.com/pyapiv2
```

to:

```
https://api.lawttorney.com/pyapi
```

That's it. Same endpoints, same headers, same responses — only the base URL changes. Should be one line in your config / env file.

Nothing else in the app needs to change.

**I'll tell you when to deploy.** I still need to set up DNS and SSL on the new server. Once that's done (probably later today / tomorrow), I'll ping you and you push the change.

If you want to test against the new backend now, you can hit it via the IP directly: `http://52.66.246.103/pyapi/health` — returns the same JSON the old `/pyapiv2/health` does.

If something breaks after we switch, just revert the base URL back to the old one — the old server stays running for a day or two.

Thanks!

---

## Notes before sending
- Replace `tool.lawttorney.com/pyapiv2` if your current base URL is slightly different
- If you know which file holds the constant (e.g. `src/config.js`, `.env`), say so — saves them a minute
- Don't share `52.66.246.103` in any public channel
