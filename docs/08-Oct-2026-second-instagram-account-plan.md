# Second Instagram account (AI literacy) on the existing Meta app — plan

Author: Claude Sonnet 5.5 — 08-Oct-2026

## Decision
Keep @pawel_and_pawleen (farm + dogs) exactly as is. Add a SECOND Instagram business account for AI literacy,
linked to its OWN new Facebook Page, served by the EXISTING Meta app (the "Yorkies" app). No new app, no app review:
the app stays in development mode, and Boss is its admin, so any account Boss owns can be added.

## Separation of concerns
- New env file `/Users/macmini/bubba-workspace/secrets/ai-literacy-meta.env` (0600), same variable names as the farm file,
  different values. The farm file is never edited by this work.
- New keychain entries with an `ailit-` prefix. Existing `yorkies-` entries untouched.
- The farm pipeline keeps reading only the farm env file. The AI literacy content gets its own small poster/repo that
  points at its own env file. Neither can post to the other's account by accident.

## Who does what
Meta rules (and our safety rules) mean I do NOT create accounts, type passwords, or enter 2FA codes. Boss does only those
three things, on screens I bring up in his signed-in Chrome. I do all other clicking.

### Phase A — Boss (about five minutes)
1. In the Instagram phone app: Add account > Create new account. New username, email, password. (Boss only.)
2. Settings > Account type > switch to Professional > Business/Creator. (Boss or me, via phone — Boss.)
3. Tell me the new username. Done for Boss until Phase D.

### Phase B — Me, in Chrome (facebook.com)
1. Create a new Facebook Page for the AI literacy brand (Pages > Create). Name, category "Education", short bio.
2. Page settings > Linked accounts > Instagram > Connect, choose the new account. Instagram will prompt for the
   account's password — Boss types it (pause and hand over).
3. Confirm the page shows the Instagram account as linked.

### Phase C — Me, in Chrome (developers.facebook.com)
1. Open the existing app, confirm it is in Development mode and Boss is Admin (Roles > Roles).
2. Graph API Explorer: select the Yorkies app, Get User Access Token, tick the same permissions the farm token has
   (pages_show_list, pages_manage_posts, pages_read_engagement, pages_read_user_content, read_insights, the
   instagram_basic / instagram_content_publish / instagram_manage_* set, business_management). In the consent
   dialog, make sure BOTH pages are ticked (the old Yorkies page and the new one) — consent screen can be edited to
   include the new page. Continue as Mark.
3. Copy the short-lived token to a scratch file in the scratchpad (not chat).

### Phase D — Me, in Terminal
1. Exchange short token for long-lived user token (app id and secret from keychain, as the farm recipe does).
2. List pages from that token; pick the NEW page row; its token is the new non-expiring page token.
3. Read the new page's linked Instagram business account id.
4. Verify scopes with the debug-token call.
5. Write `ai-literacy-meta.env` (0600) and keychain `ailit-*` entries.
6. Smoke test: publish one test photo to the new account via a minimal script, then delete it by hand.
   (Counts against that account's own 25-per-day cap, not the farm's.)

### Phase E — Rebuild the engine for the new account (separate task, after A–D work)
- A tiny poster that takes the env file path as an argument, so the same publishing code can serve either account.
  Do NOT bolt the AI literacy account into the farm LaunchAgents.
- Decide content cadence and where media is hosted (the farm uses the Mac's tunnel; reuse it or a different host).

## Risks and fallbacks
- Meta's screens drift; if a screen does not match, I screenshot, adapt, and tell Boss — not guess.
- If Meta flags the new account as suspicious (new account + immediate API use), let it age a day or two before Phase D.
- Instagram may force the Page link through the Instagram app instead of the web; then Boss does that tap on the phone.
- Rollback is trivial: delete the new env file and keychain entries. The farm side is never touched.

## Known unknown
Whether consent in Graph Explorer lets me tick the new page without re-running the whole login. Expected yes (the
dialog has an "edit access" step); if not, Boss approves the app once more from the Facebook side.

## Update 08-Oct-2026 — account made, branding and YouTube link added

Boss created the Instagram account "voynichlabs" on his phone (added under his existing login).

### Branding (reuse what the YouTube channel already has, so everything matches)
- Source art: `/Volumes/Samsung 9100 SSD/music-videos/toolcall/brand/avatar.jpg` (800x800) and `banner.jpg` (yellow on black, "AI GONE WILD").
- Generator: `music-videos/tools/brand.py` (avatar and banner modes). Instagram has no banner; use the avatar plus the bio.
- Bio draft: "AI literacy, made in the open. Songs, videos and experiments by Larry, Bubba and Mark." plus link to voynichlabs.org.
- Instagram profile link list: voynichlabs.org and the YouTube channel (Instagram allows several links).

### Linking to YouTube (both directions)
1. Instagram bio/links: add the YouTube channel URL.
2. YouTube Studio > Customization > Basic info > Links: add the Instagram profile URL.
3. voynichlabs.org footer/social row: add the Instagram link.

### Progress log
- DONE: Facebook Page "VoynichLabs" created (Education), website set, avatar and cover uploaded, marketing emails off. Page id 61595220061536.
- DONE: Facebook settings > Linked accounts > Instagram > Connect started (professional-account conversion happens as part of connecting).
- WAITING ON BOSS: Instagram sign-in/approval step (password). Then Phase C (Graph token) and D (env file, smoke test).

## Update 29-Oct-2026 — done, by a different route (Claude Opus 5.5 Medium)

- **Why the Page route never worked:** the Boss's Facebook account is permanently barred from business features (Meta cites something from around 2021), so linking the voynichlabs Instagram to the VoynichLabs Page is always refused. Do not retry it. The 8-Oct attempt also coincided with Facebook invalidating the farm key; the farm sat silent until 28-Oct.
- **Route used instead:** Instagram API with Instagram login, on the existing Yorkies app (its Instagram app is Yorkies-IG). No Facebook Page involved. voynichlabs is an accepted Instagram Tester; key generated on the app's Instagram API setup page.
- **Credentials:** `/Users/macmini/bubba-workspace/secrets/ai-literacy-meta.env` (0600) and keychain `ailit-ig-access-token`, `ailit-ig-user-id`, `ailit-ig-username`. Calls go to `graph.instagram.com`, not `graph.facebook.com`, with the Instagram user id, not a Page id.
- **Expiry:** the key lasts about 60 days. LaunchAgent `ai.bubba.voynichlabs-ig-token-refresh` runs `~/bin/voynichlabs-ig-token-refresh.py` every Monday morning to renew it. If it ever dies, switch Chrome's Instagram to voynichlabs (it is saved there), then a human clicks Generate token on the dashboard. "Insufficient developer role" means the popup opened under the wrong Instagram account.
- **Verified:** profile read and an unpublished test container both succeeded. Nothing has been posted yet; Phase E (a poster and content plan) is still open.
