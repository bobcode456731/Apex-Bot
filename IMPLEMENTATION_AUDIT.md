# ApexBot implementation audit

Audit date: 2026-10-29  
Specification: `APEX_BOT_CODEX_SPEC.md`

## Status meanings

- **Fully implemented and verified** means the code path was inspected and its offline behavior was exercised by a unit test or mocked Discord workflow. It does not mean Discord accepted a live API request.
- **Partially implemented** identifies a concrete limitation in the project behavior or test coverage.
- **Missing** means a required feature is absent.
- **Requires live Discord verification** means only the real server can confirm the live object, permission, hierarchy, intent, message, or interaction behavior.

No live Discord server was contacted. No real bot token was requested, loaded, or printed; configuration tests used placeholders only. Tests used temporary SQLite databases and mocked Discord APIs.

## Project-wide findings

| Area | Status | Evidence |
|---|---|---|
| Python/Discord stack | Fully implemented and verified | Python package layout with `discord.py`, prefix commands, SQLite, Docker and Compose files. Runtime setup was exercised with `discord.py 2.7.1`. |
| Prefix-only requirement | Fully implemented and verified | `ApexBot` uses `!`, Message Content and Members intents are enabled in code, 30 prefix roots register, and the Discord application-command tree is empty. |
| Hosting fit | Fully implemented with an explicit assumption | Docker targets Python 3.12; code requires Python 3.11+. SQLite is suitable for one bot process with a persistent volume. Multi-replica operation and a PostgreSQL adapter are not provided. No specific hosting provider or existing host configuration was present to validate. |
| Real Discord behavior | Requires live Discord verification | The actual server's channels, roles, role order, privacy overwrites, bot permissions, privileged intents, API rate limits, and component rendering were not checked. |
| Required command families | Fully implemented and registered | Moderation, utility, server management, ticket, panel builder, custom embed builder, giveaway, suggestion, and audit commands are present. No required command family is missing. |

## 1. Brand and embed styling

| Requirement | Status | Evidence / notes |
|---|---|---|
| Apex MiddleMan Services / `𝙰𝚙𝚎𝚡` naming and requested palette | Fully implemented and verified | Shared embed constants define primary `#8B5CF6`, secondary `#A78BFA`, silver `#D8D8E0`, and error `#D95C68`; standard embed footers use Apex styling. |
| Clear embed layouts, purposeful mentions, and restrained styling | Fully implemented in code | Shared embed creation is centralized. Mention-bearing sends use explicit allowed-mentions where they can notify users or roles. No rejected chrome-shield, smoke, orbital-ring, or rocky visual asset is included. Live visual appearance still requires Discord verification. |

## 2. Confirmed channel IDs

The configured values below match the specification exactly and are centralized in `apexbot/config.py`; the audit test compares every value. Their existence and permissions in the real server **require live Discord verification**.

| Purpose | Config key | ID | Local status |
|---|---|---:|---|
| Middleman panel | `middleman_panel` | `1539783649208049704` | Fully implemented and verified |
| General support panel | `support_panel` | `1539785643398533120` | Fully implemented and verified |
| Rank-up panel | `rankup_panel` | `1552317076549533827` | Fully implemented and verified |
| Ticket transcripts/logs | `ticket_logs` | `1552316695744610365` | Fully implemented and verified |
| Moderation logs | `moderation_logs` | `1552317097277657129` | Fully implemented and verified |
| Role logs | `role_logs` | `1552317055217176616` | Fully implemented and verified |
| Middleman Terms of Service | `middleman_terms` | `1552292603947520042` | Fully implemented and verified |
| Support Terms of Service | `support_terms` | `1552305124691415171` | Fully implemented and verified |
| Giveaway announcements | `giveaways` | `1539328333807100085` | Fully implemented and verified |
| Suggestions | `suggestions` | `1565009040818573412` | Fully implemented and verified |
| Welcome messages | `welcome` | `1539833608544714822` | Fully implemented and verified |
| Advanced audit/errors | `audit_logs` | `1552317322394468393` | Fully implemented and verified |

At startup the bot reports missing configured objects. Each command also validates the channel or role it needs and returns an error when it is missing or unusable. Whether the production IDs resolve and have the intended privacy/permissions remains a live check.

## 3. Confirmed role IDs and hierarchy

The following role IDs match the specification and the local configuration test. Authorization uses live role positions and fails closed if a configured threshold role is absent (except for the server owner); it does not compare role IDs as ranks. Actual role existence, names, ordering, bot top-role placement, and native permissions **require live Discord verification**.

| Role | ID | Role | ID |
|---|---:|---|---:|
| Owner | `1539256548684202034` | Head Owner | `1552668910665597089` |
| Server Founder | `1552341106694299699` | President | `1552341768492814366` |
| Director of Ops | `1552341937271611443` | Operations Lead | `1552342597484285952` |
| Head of Security | `1552342129542696991` | Bots | `1552341466179964978` |
| Community Manager | `1552343258892472432` | Administrator | `1552343707074699386` |
| Moderator | `1552344063003467797` | Lead Designer | `1552343120551616702` |
| Head Middleman | `1552345162083078287` | Expert Middleman | `1552344749816414298` |
| Middleman | `1552358685336928356` | VIP | `1543618690547843104` |
| Trusted Trader | `1552471219368894474` | Level 2 Trader | `1552345662841159710` |
| Level 1 Trader | `1552345934203977728` |  |  |

`@everyone` has no separate configured role ID. Ticket policy thresholds are Middleman for MM, Moderator for support, and Server Founder for rank-up.

## 4. Moderation commands and minimum roles

The table records the configured minimum roles exactly. Command registration, decorator/native-permission declarations, role rejection, confirmation behavior, hierarchy policy, purge count filtering, and a ban API-failure path were tested offline. The actual Discord action and role hierarchy still **require live Discord verification**.

| Command | Minimum configured role | Native permission / behavior | Status |
|---|---|---|---|
| `!warn @user [reason]` | Moderator `⚒` | Persistent warning ID/time; attempts DM; logs action and DM result | Requires live Discord verification |
| `!warnings @user` | Lead Designer `〆` | DM or configured private moderation channel only | Requires live Discord verification |
| `!clearwarnings @user [reason]` | Director of Ops `◈` | Confirmation; archives warning rows | Fully implemented and verified offline; live action pending |
| `!timeout @user 10m [reason]` | Lead Designer `〆` | Native Moderate Members; supports s/m/h/d to 28 days | Requires live Discord verification |
| `!untimeout @user [reason]` | Lead Designer `〆` | Native Moderate Members | Requires live Discord verification |
| `!kick @user [reason]` | Moderator `⚒` | Native Kick Members; actor/bot/target hierarchy | Requires live Discord verification |
| `!ban @user [reason]` | Administrator `⬢` | Native Ban Members; explicit confirmation; no message deletion | Fully implemented and verified offline for confirmation and API failure; live action pending |
| `!unban USER_ID [reason]` | Server Founder `⟡` | Validated Snowflake and native Ban Members | Requires live Discord verification |
| `!purge 20` | Administrator `⬢` | Confirmation; skips command/confirmation messages; deletes at most requested count; Discord age handling | Fully implemented and verified offline for filtering; live action pending |
| `!lock [reason]` | Head Owner `༺` | Native Manage Channels; stores overwrite snapshot first | Requires live Discord verification |
| `!unlock [reason]` | Head Owner `༺` | Restores saved overwrites; never blindly resets; keeps snapshot on failure | Requires live Discord verification |
| `!slowmode 5` | Head Owner `༺` | Native Manage Channels; 0–21,600 seconds | Requires live Discord verification |
| `!help` | Everyone | Categorized command syntax and role labels | Fully implemented and verified by code/runtime registration |

Moderation DM failures do not undo or misstate a confirmed Discord action. Audit logging failures are reported separately. Reasons are bounded and secret-redacted. Deleted purge message content is not stored.

## 5. Utility commands

| Command | Minimum role | Implementation | Status |
|---|---|---|---|
| `!serverinfo` | Everyone | Server name, member count, creation date, text/voice/category counts, owner | Fully implemented in code; live data rendering pending |
| `!botinfo` | Everyone | Uptime, latency, version, connection state | Fully implemented in code; live data rendering pending |
| `!userinfo [@user]` | Everyone | Account/join dates, roles, nickname, timeout, highest role | Fully implemented in code; live data rendering pending |
| `!roleinfo @role` | Everyone | Colour, member count, position, key permissions | Fully implemented in code; live data rendering pending |
| `!help` | Everyone | Categorized syntax, purpose, and access labels | Fully implemented and registered |

The four live-data utility commands have not been exercised against real guild/member caches; that part requires live Discord verification.

## 6. Autorole, welcome, and server management

| Command/event | Minimum role | Native checks / behavior | Status |
|---|---|---|---|
| `!autorole` | Everyone | Displays persisted role or Off; reports invalid saved ID | Fully implemented and verified with isolated settings |
| `!autorole set @role` | Head Owner+ | Actor and bot Manage Roles; blocks default, managed, privileged, and high roles; saves only after validation | Fully implemented and verified with mocks; live hierarchy pending |
| `!autorole off` | Head Owner+ | Manage Roles; deletes the one guild setting | Fully implemented in code; live check pending |
| `!setwelcome #channel` | Head Owner+ | Manage Server; checks bot View/Send/Embed before saving | Fully implemented and verified for missing View permission |
| `!welcome` | Everyone | Reports On/Off and resolved destination | Fully implemented in code |
| `!welcome off` | Head Owner+ | Manage Server; persists off state | Fully implemented in code; live check pending |
| `!welcome-test` | Head Owner+ | Manage Server; sends only to saved/default welcome destination and reports API failure | Fully implemented and verified with a mocked destination |
| `on_member_join` | Automatic | Assigns one safe role; sends configured welcome with only new-member mention enabled; handles invalid settings and missing permissions | Fully implemented and verified for malformed config; join intent/API behavior pending |
| `!nick @user [nickname]` | Lead Designer+ | Manage Nicknames; actor/bot/target hierarchy; old/new audit | Requires live Discord verification |
| `!role add @user @role` | Operations Lead+ | Manage Roles; actor/bot/member/role hierarchy; blocks protected roles; role log | Requires live Discord verification |
| `!role remove @user @role` | Operations Lead+ | Manage Roles; same protection and logging | Requires live Discord verification |

Welcome references use the configured MM/support terms and support panel IDs. No rules channel ID was supplied in the specification, so the bot does not invent one. Actual join delivery and autorole assignment remain live checks.

## 7. Ticket system

### Ticket type, destination, route, and form

| Ticket type | Panel destination | Ticket naming | Staff threshold | Required form fields | Status |
|---|---:|---|---|---|---|
| Middleman Request | `1539783649208049704` | `mm・username` | Middleman `1552358685336928356`+ | Trading partner's Discord username; what is being traded; estimated value; who gives first | Fully implemented and verified in form/policy tests; live creation pending |
| General Support | `1539785643398533120` | `support・username` | Moderator `1552344063003467797`+ | What help is needed; explanation; relevant user IDs/evidence | Fully implemented and verified in mocked ticket creation; live creation pending |
| Staff-only Rank-up | `1552317076549533827` | `rankup・username` | Server Founder `1552341106694299699`+ | Current staff role; requested role/rank; reason; relevant approval/poll/arrangement | Fully implemented and verified for ordinary-member denial and private-channel policy; live access pending |

### Ticket workflow and access checklist

| Requirement | Status | Evidence / notes |
|---|---|---|
| One open ticket per user/category | Fully implemented and verified | Partial unique index covers `creating` and `open`; duplicate opening was tested. Different categories are independent. |
| Private channel permissions | Fully implemented in code | Overwrites include opener, bot, and category staff roles only; the configurable notification role is not granted access. Actual effective permissions require live verification. |
| Claim/unclaim and audit actor/history | Fully implemented and verified | Claim uses transactional compare-and-set; actor, claimant target, old/new values, and timestamps are stored. |
| Priority Low/Normal/High/Urgent | Fully implemented in code | Select validates staff access and saves state plus event transactionally. Live component selection remains unverified. |
| Close confirmation and default reason | Fully implemented in code | Reason modal then staff-only confirmation; empty reason becomes `Trade Complete`. Direct closure tests verified transcript-before-delete. Live button/modal UX pending. |
| `!adduser @user` ticket/category scope | Fully implemented and verified | Requires category staff or Founder+, native Manage Channels for actor/bot, rejects opener as actor/target, writes one-ticket ACL event, and attempts permission rollback if DB event fails. Scope and opener bypasses were tested. |
| Support Role to Ping | Fully implemented and verified offline | Role ID stored per panel; opening validates ID, existence, mentionability/bot permission, pings only that exact role with restricted `AllowedMentions`, and does not add it to channel overwrites. Mock opening and corrupt-role behavior were tested. |
| Transcript content | Partially implemented | Contains ticket ID/category/opener, claimant/current priority, event timeline, closure actor/reason, timestamped authors/messages, attachment filename+URL, and stickers. Binary attachment files are not downloaded/embedded; whether metadata links satisfy the server's expectation for “supported attachments” needs owner review. |
| Transcript privacy and failure safety | Fully implemented and verified offline | Requires staff-only destination plus Send Messages/Attach Files. Failed upload keeps status open/channel present, records failure, and alerts category staff where possible. Mock failure was tested. Live channel privacy/API still pending. |
| Closing state/order | Fully implemented and verified | Transcript upload precedes DB closure; channel deletion happens after DB close state and saved transcript. Tests asserted the order. |
| Ticket event audit coverage | Fully implemented in code | Open, claim/unclaim, add-user, priority, close, transcript failure, and opening ping status are recorded/routed. Actual log delivery requires live verification. |
| Persistent ticket controls | Fully implemented and verified offline | Views have stable IDs and are re-registered from stored rows after DB reopen. Actual component continuation after a Discord reconnect requires live verification. |

## 8. Ticket panel builder

| Feature | Status | Evidence / notes |
|---|---|---|
| Guided multi-step builder with workflow select, modals, channel select, role select | Fully implemented and verified locally | `ChannelSelect`/`RoleSelect` types are tested. Long field arrays can be edited after save; builder does not silently truncate the field array. Live UI rendering is unverified. |
| Title, description, 25 fields, inline flag, reorder/edit/remove, colour, image, footer, button label/emoji | Fully implemented and verified offline | Shared embed validation enforces Discord text/field limits; fields editor preserves order and `inline`. Colour, URL, field shape, and emoji are validated. |
| Correct destination and private rank-up destination | Fully implemented and verified offline | Exact destination mapping enforced at save/publish/update; rank-up log/panel privacy rejects `@everyone`, general roles, and member-specific access. Live overwrite behavior remains unverified. |
| Support Role to Ping selection/persistence/notification | Fully implemented and verified offline | Actual role selector, persisted role ID, explicit ping permission checks, no access overwrite, strict mentions, and corrupt-config handling. Live ping outcome remains unverified. |
| Save/edit/preview/duplicate/manage/list | Fully implemented in code | Individual commands and field manager are present. Save/publish, selectors, and duplicate-trigger paths have tests; every UI modal was not individually simulated. |
| Publish one panel at a time | Fully implemented and verified offline | `publish ID` accepts one panel; no publish-all command exists. Mock send and persistence verified. |
| Confirmed update | Fully implemented in code; partially tested | Update asks confirmation and decline was verified not to edit/send. Confirmed update state migration/rollback paths are code-inspected but not all mock branches were exercised. |
| Confirmed delete | Fully implemented and verified offline | Disables a published button before deleting its row; test verifies this ordering. |
| Restarted panel buttons | Fully implemented and verified offline | Published views re-register using persisted panel IDs; database reopen test checks persistent IDs. Real reconnect remains live verification. |

Starter copy in the specification is explicitly optional and “saved drafts only.” No panel content is preloaded or published. Owners enter their own approved copy.

## 9. Custom Embed / Trigger Builder

| Requirement | Status | Evidence / notes |
|---|---|---|
| No preloaded `!mm`, `!rules`, `!tos`, `!vouch`, `!scam`, `!fees`, `!support` | Fully implemented and verified | Fresh DB has zero custom embed rows; those names are deliberately allowed for owner-created content. |
| Trigger/aliases, embeds, fields, preview/save/edit/duplicate/manage/enable/disable/delete/publish/list | Fully implemented in code | Database table, guided modals, persistent fields editor, and command family present. Trigger save/collision/normalization and duplicate normalization were tested. |
| Built-in/reserved collision and alias uniqueness | Fully implemented and verified offline | Validated reserved-root list, syntax, duplicate names, and transaction-protected collision queries. |
| Founder+ creation/edit/management | Fully implemented in code | Command and each builder interaction recheck configured Founder threshold and native Manage Server. Both role and native-denial paths are tested. |
| Middleman+ publish and trigger execution | Fully implemented and verified offline | Trigger dispatch rejects non-members/non-staff, built-in commands win, and sends use no mentions. Mock dispatch and bare-prefix behavior tested. |
| Secret-like content and mention safety | Fully implemented in code | Secret patterns rejected/redacted; trigger outputs suppress mentions. Regexes cannot guarantee detection of unlabeled secrets or all wallet phrase formats. |
| Live custom publishing and UI flow | Requires live Discord verification | No actual message or builder interaction was sent. |

Optional `!announce`/`!embed` command family is not implemented; the specification says it may be supported, so this is not a required-feature gap.

## 10. Giveaways

| Requirement | Status | Evidence / notes |
|---|---|---|
| Management role and channel | Fully implemented in code | Director of Ops `1552341937271611443`+, native Manage Server; fixed channel `1539328333807100085`. Missing channel and send permissions are reported. Live IDs/permissions pending. |
| `!giveaway start`, `end ID`, `reroll ID`, `list`, `cancel ID` | Fully implemented in code | All five subcommands are registered. |
| Any member may enter; bots excluded; one entry/member | Fully implemented and verified offline | Entry button checks guild/message/end state and bot status; database primary key blocks duplicates. |
| Prize/duration/winner count; automatic end | Fully implemented in code | Duration parser, 1–20 winners, 1–200 char prize; 30-second task scans due DB rows on startup. Live scheduler/restart timing pending. |
| Persist host, dates, entrants, winners, final outcome | Fully implemented and verified offline | SQLite rows and entry table; no-winner and winner outcomes tested. |
| Cancel no winner; reroll excludes prior winners | Fully implemented and verified offline | Cancel/reroll test verifies no winner on cancel and reroll selection from a remaining entrant. |
| Create/end/cancel/reroll/winner audit | Fully implemented in code | Events are written to audit storage and channel destinations; delivery requires live verification. |
| Live button/message/timer behavior | Requires live Discord verification | No public giveaway was started or entered. |

## 11. Suggestions

| Requirement | Status | Evidence / notes |
|---|---|---|
| `!suggest [idea]` public submission, submitter hidden | Fully implemented and verified offline | Bot requires permission to delete the source prefix command before public posting; suggestion embed omits identity; public fallback is generic; publish uses no mentions. |
| Suggestion channel `1565009040818573412` | Fully implemented in code | Fixed config key and channel permissions are checked before submission. Live channel pending. |
| `!suggestion status ID` for everyone | Fully implemented in code | Public output contains status/dates only, not content/submitter. |
| Approve/deny/implement minimum Lead Designer | Fully implemented and verified offline | Denial and Pending→Approved→Implemented history/actor paths tested. Decline stores reason. |
| 👍/👎 persistent buttons, one vote/member | Fully implemented and verified offline | Stable component IDs, unique DB key, forged message/invalid vote checks and DB failure path tested. |
| Status history/decision actor/reason/time/audit | Fully implemented and verified offline | Submission and decision rows are persisted; transitions tested. |
| Staff visibility of submitter | Partially implemented | Submitter ID is stored and accessible in SQLite, but there is no staff-only `!suggestion submitter ID` lookup command. Add one only if staff need in-Discord access. |
| Live submit, reaction rendering, and permissions | Requires live Discord verification | No suggestion was posted to a live channel. |

## 12. Advanced audit and error logging

| Requirement | Status | Evidence / notes |
|---|---|---|
| Founder+ `!audit status`, `recent`, `errors`, `config` | Fully implemented in code | Permission is rechecked; reports go by DM or the configured staff-only channel. |
| Dedicated moderation/role/ticket/audit channels | Fully implemented in code | Uses exact configured IDs and private-channel checks. |
| Action actor/target/reason/time/outcome/DM/case | Fully implemented in code | Audit events include IDs/outcome/details; moderation adds reason, case, and DM status. |
| Panel config/publication, embeds, tickets, role/config, giveaways, suggestions | Fully implemented in code | Each major action family has audit calls; custom trigger publication is now audited. |
| Log delivery/database failure does not claim requested action failed | Fully implemented in code | Audit returns success/failure separately and logs incomplete delivery; moderation and ticket responses distinguish delivery failure. |
| Secret/private-content redaction and no stack traces to public logs | Fully implemented in code | Structured error events include exception class rather than stack/error body; `safe_text` redacts common credentials and neutralizes mass mentions. |
| Retention | Fully implemented and verified offline | `LOG_RETENTION_DAYS` prunes audit rows on startup; retention test passed. |
| Real log privacy and delivery | Requires live Discord verification | The bot can verify `@everyone`, non-staff role, and member-overwrite access from live channel permissions, but actual destination settings were not available. |

## 13. Storage, reliability, and security

| Requirement | Status | Evidence / notes |
|---|---|---|
| Persistent storage/migration/transactions | Fully implemented and verified offline | SQLite WAL, foreign keys, `user_version` migration 1, transaction rollback tests, restart persistence tests. |
| Tables | Fully implemented and verified | `settings`, `warnings`, `moderation_cases`, `locked_channels`, `tickets`, `ticket_events`, `panel_templates`, `custom_embeds`, `giveaways`, `giveaway_entries`, `suggestions`, `suggestion_votes`, `suggestion_history`, `audit_events`. |
| Duplicate prevention | Fully implemented and verified | Unique partial ticket index, giveaway-entry primary key, suggestion-vote primary key, transactional custom trigger/alias collision checks. |
| Persistent component state | Fully implemented and verified offline | Panel, ticket, giveaway, and suggestion views are reconstructed from the DB; reopened-DB view registration was tested. |
| Centralized validation/permission/embed/error handling | Fully implemented in code | Helpers in `validation.py`, `policy.py`, and `common.py`; safe generic component/modal/command errors. |
| Allowed mentions | Fully implemented in code | Notification sends allow only exact intended user/role; public custom embed/suggestion/audit output suppresses mentions. |
| Role/channel/hierarchy checks | Fully implemented in code; live state pending | Configured thresholds use current positions; native permissions and bot hierarchy are checked where actions touch Discord. |
| Secrets and ignored files | Fully implemented and verified by file inspection | `.env.example` contains a placeholder, `Settings` rejects the placeholder as a token and blank DB paths, `.env` is ignored, no token is in source. |
| Multi-instance hosting | Partially implemented by design | One process with one persistent SQLite volume is supported. Do not run multiple replicas on the same SQLite file; a PostgreSQL adapter is not included. |

## 14. Tests actually run

At the audited revision, **65 tests passed**. The 65 are behavior tests over temporary SQLite databases and mocked Discord objects, not a live integration suite.

| Command | Result |
|---|---|
| `$env:PYTHONPATH = (Resolve-Path .audit-deps).Path; & 'C:\Users\SBI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -v` | 65 tests passed, 0 failures |
| `& 'C:\Users\SBI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m compileall -q apexbot tests main.py` | Passed, no compilation errors |
| `$env:PYTHONPATH = (Resolve-Path .audit-deps).Path; & 'C:\Users\SBI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/runtime_setup_check.py` | Passed: 30 prefix roots registered; 0 application/slash commands |

The suite includes exact channel/role ID matching, command minimum/native permission declarations, role hierarchy policy, invalid configuration and embed values, one-open-ticket uniqueness, actual mocked ticket creation, support-role mentions, opener `!adduser` bypass rejection, ticket claim history, transcript contents/order/failure safety, persistent controls after DB reopen, panel selects/save/publish/update-confirmation/delete, custom trigger collisions and staff gating, giveaway uniqueness/end/cancel/reroll/no-entrant behavior, suggestion privacy/votes/transitions, autorole/welcome validation, moderation confirmation/API failure, log privacy, and safe component errors.

The Windows sandbox's asyncio loop stalled before some async tests began, so the full unit suite and setup check were run with approved sandbox escalation. The test data remained local and isolated; no network calls were made. The dependencies in `.audit-deps` were used for the audit run (`discord.py 2.7.1`, within `requirements.txt`).

## 15. Remaining gaps and safe Discord verification

### Remaining gaps

- No live Discord verification was performed. Every real role/channel ID, name, position, permission overwrite, bot permission, privileged intent approval, component callback, message send, and API rate-limit behavior remains unverified.
- Ticket transcripts include supported attachment metadata and URLs, not attachment bytes.
- Suggestion submitter IDs are stored for staff inspection, but there is no staff-only in-Discord lookup command.
- SQLite is a single-process design. A host that restarts without preserving its volume will lose state; horizontal replicas need a different storage adapter.
- The spec did not include a rules channel ID, so welcome messages reference the supplied terms/support destinations only.

No required command family or core builder/ticket/community workflow was found missing. The optional built-in announcement family and optional starter panel drafts are not provided; no educational content is generated automatically.

### Safe manual test procedure

Use a disposable staging guild or a private cloned server, never the active Apex server for these tests. Make a staging copy of the project and replace the centralized channel/role IDs in that copy with staging IDs; use a separate empty database and staging bot application. Keep the token only in the staging host's secret store or local `.env` and never paste it into chat or source.

1. In the Developer Portal for the staging bot, enable Message Content and Server Members intents. Invite it with only the listed feature permissions; do not grant Administrator as a shortcut. Place its role above the staging staff/member roles it must manage.
2. Create private staging transcript, ticket-log, moderation-log, role-log, and audit channels. Confirm `@everyone`, general trader/member roles, and member overrides cannot view them. Keep the rank-up panel channel private. Make the test Support Role mentionable so Mention Everyone is not needed.
3. Start against the staging database and verify `!help`, `!audit status`, channel/role diagnostics, and `!welcome-test` destination. Do not test `!welcome-test` in a channel where a public message would be unwelcome.
4. Use `!panelbuilder` to create, preview, save, duplicate, and publish one panel at a time. Confirm its saved channel and Support Role to Ping. Click the panel as an ordinary staging member, verify only the configured role is pinged, open one ticket per category, and confirm a second open ticket in the same category is blocked.
5. Verify only the category staff roles and opener can view each ticket. Try rank-up as an ordinary test member (must be denied), then as a Founder test member. Test claim/unclaim, each priority, and `!adduser @user`; verify the added user sees only that ticket. Try the command as the opener and as a non-staff (both must be denied).
6. In staging, test transcript close with a small text/attachment ticket, then restart the bot with the same staging DB and click a still-open ticket/panel/giveaway/suggestion control. Confirm the view remains active and the transcript was posted before ticket deletion. Test upload failure only by temporarily removing Attach Files in staging; the ticket must remain open.
7. Create a harmless test custom embed in the staging builder. Verify only Middleman+ can publish or invoke its trigger, built-in commands cannot be shadowed, and no mentions fire from its content.
8. Start a short staging giveaway, enter as two test accounts (bot accounts must be rejected), end/cancel/reroll it, and verify the DB outcome and announcement. Post a harmless staging suggestion, vote from multiple test accounts, and verify status transitions and privacy.
9. Test moderation only on disposable staging accounts/channels: warning, timeout, kick, ban, unban, confirmed purge, lock/unlock, and slowmode. Use a test member and non-sensitive test messages. Verify a failed DM does not reverse action status and that logs distinguish action outcome from delivery outcome.
10. Check `!audit recent`, `!audit errors`, and the dedicated logs. Confirm no token/password/private-key content appears. Keep the staging database and channels separate from production throughout.

Only after those staging checks should the production IDs/permissions be reviewed and a controlled production rollout be considered. No destructive or public action was performed on the real server during this audit.
