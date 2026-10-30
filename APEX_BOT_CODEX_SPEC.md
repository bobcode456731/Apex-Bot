# Apex MiddleMan Services — Discord Bot Implementation Specification

## Instructions to Codex

Implement this specification in the **existing repository**. Start by inspecting the whole repository, its framework, entry point, current commands, hosting configuration, database/storage, tests, and existing behavior. Reuse the current stack and hosting setup where reasonable. Do not overwrite or replace working project files blindly. Keep a backup or work on a branch before risky changes.

This is an implementation task, not just a planning task. After discovery, create a concise implementation plan, then implement the features, migrations, tests, documentation, and deployment/config updates. Do not stop after producing a plan. If the repository or credentials prevent a feature from being fully tested, implement safely and state exactly what remains unverified. Never claim tests or live Discord behavior succeeded unless actually tested.

Use **prefix commands beginning with `!` only**. Do not add slash commands. Discord's Message Content privileged intent must be enabled for prefix-command handling. Read secrets from environment variables/hosting secrets; never hardcode, print, commit, or log bot tokens or other secrets. Add an `.env.example` containing placeholders only.

Preserve existing setup and hosting whenever possible. Do not invent fees, terms, rules, role prices, or unapproved ticket workflows. There must be no predefined educational triggers such as `!mm`, `!rules`, `!tos`, `!vouch`, `!scam`, `!fees`, or `!support`; the owner will create these later using the custom builder.

## 1. Brand and embed styling

- Brand: Apex MiddleMan Services / `𝙰𝚙𝚎𝚡`.
- Direction: clean esports, dark premium, black and silver with restrained violet.
- Main embed colour: `#8B5CF6`; secondary `#A78BFA`; silver `#D8D8E0`; error `#D95C68`.
- Premium embeds, purposeful emojis, consistent footer styling.
- Do not use the rejected old visual direction: chrome shield, purple smoke, glowing orbital rings, or rocky background.
- Use clear, consistent embed layouts. Avoid excessive decoration or claims that are not grounded in configured server rules.

## 2. Confirmed channel IDs

| Purpose | Channel ID |
|---|---:|
| Middleman panel destination | `1539783649208049704` |
| General support panel destination | `1539785643398533120` |
| Rank-up panel destination | `1552317076549533827` |
| Ticket transcripts/logs | `1552316695744610365` |
| Moderation logs | `1552317097277657129` |
| Role logs | `1552317055217176616` |
| Middleman Terms of Service | `1552292603947520042` |
| Support Terms of Service | `1552305124691415171` |
| Giveaway announcements | `1539328333807100085` |
| Suggestions | `1565009040818573412` |
| Welcome messages | `1539833608544714822` |
| Advanced audit/error logs | `1552317322394468393` |

Use IDs from configuration rather than scattered hardcoded literals where practical. Validate that configured objects exist and provide readable errors if they do not.

## 3. Confirmed role IDs

### Ownership / leadership
- ♜・𝐎𝐖𝐍𝐄𝐑 — `1539256548684202034`
- ༺・𝐇𝐄𝐀𝐃 𝐎𝐖𝐍𝐄𝐑 — `1552668910665597089`
- ⟡・𝐒𝐄𝐑𝐕𝐄𝐑 𝐅𝐎𝐔𝐍𝐃𝐄𝐑 — `1552341106694299699`
- ♔・𝐏𝐑𝐄𝐒𝐈𝐃𝐄𝐍𝐓 — `1552341768492814366`
- ◈・𝐃𝐈𝐑𝐄𝐂𝐓𝐎𝐑 𝐎𝐅 𝐎𝐏𝐒 — `1552341937271611443`
- ⛭・𝐎𝐏𝐄𝐑𝐀𝐓𝐈𝐎𝐍𝐒 𝐋𝐄𝐀𝐃 — `1552342597484285952`
- ⛨・𝐇𝐄𝐀𝐃 𝐎𝐅 𝐒𝐄𝐂𝐔𝐑𝐈𝐓𝐘 — `1552342129542696991`

### Administration / operations
- ⏣・𝐁𝐎𝐓𝐒 — `1552341466179964978`
- 〄・𝐂𝐎𝐌𝐌𝐔𝐍𝐈𝐓𝐘 𝐌𝐀𝐍𝐀𝐆𝐄𝐑 — `1552343258892472432`
- ⬢・𝐀𝐃𝐌𝐈𝐍𝐈𝐒𝐓𝐑𝐀𝐓𝐎𝐑 — `1552343707074699386`
- ⚒・𝐌𝐎𝐃𝐄𝐑𝐀𝐓𝐎𝐑 — `1552344063003467797`
- 〆・𝐋𝐄𝐀𝐃 𝐃𝐄𝐒𝐈𝐆𝐍𝐄𝐑 — `1552343120551616702`

### Middleman team
- ♕・𝐇𝐄𝐀𝐃 𝐌𝐈𝐃𝐃𝐋𝐄𝐌𝐀𝐍 — `1552345162083078287`
- ✧・𝐄𝐗𝐏𝐄𝐑𝐓 𝐌𝐈𝐃𝐃𝐋𝐄𝐌𝐀𝐍 — `1552344749816414298`
- ⛓・𝐌𝐈𝐃𝐃𝐋𝐄𝐌𝐀𝐍 — `1552358685336928356`

### Community / traders
- ♙・𝐕𝐈𝐏 — `1543618690547843104`
- ⛊・𝐓𝐑𝐔𝐒𝐓𝐄𝐃 𝐓𝐑𝐀𝐃𝐄𝐑™ — `1552471219368894474`
- Ⅱ・𝐋𝐄𝐕𝐄𝐋 𝟐 𝐓𝐑𝐀𝐃𝐄𝐑 — `1552345662841159710`
- Ⅰ・𝐋𝐄𝐕𝐄𝐋 𝟏 𝐓𝐑𝐀𝐃𝐄𝐑 — `1552345934203977728`
- Member role is `@everyone`; it has no separate role ID.

**Important:** Verify the live Discord role hierarchy and native permissions. Never infer hierarchy from a role ID or assume these roles are positioned correctly. Centralize permission checks and combine configured role thresholds with Discord's native permissions/hierarchy checks.

## 4. Moderation commands

Use these command names and minimum configured role requirements:

| Command | Minimum role | Behavior |
|---|---|---|
| `!warn @user [reason]` | Moderator `⚒` | Add persistent warning with a case ID |
| `!warnings @user` | Lead Designer `〆` | Staff-only warning history |
| `!clearwarnings @user [reason]` | Director of Ops `◈` | Archive warnings; confirmation required |
| `!timeout @user 10m [reason]` | Lead Designer `〆` | Discord timeout; support s/m/h/d within Discord's limit |
| `!untimeout @user [reason]` | Lead Designer `〆` | Remove timeout |
| `!kick @user [reason]` | Moderator `⚒` | Kick member |
| `!ban @user [reason]` | Administrator `⬢` | Confirmation required |
| `!unban USER_ID [reason]` | Server Founder `⟡` | Unban by ID |
| `!purge 20` | Administrator `⬢` | Confirmation required; respect Discord bulk-delete age limits |
| `!lock [reason]` | Head Owner `༺` | Save current overwrites and lock configured channel |
| `!unlock [reason]` | Head Owner `༺` | Restore saved overwrites; do not reset them blindly |
| `!slowmode 5` | Head Owner `༺` | Validate supported range and apply slowmode |
| `!help` | Everyone | Categorized command help |

Requirements:
- Persistent warning records with case IDs and timestamps. `!warnings` is staff-only.
- `!clearwarnings`, `!ban`, and `!purge` require an explicit confirmation step before irreversible/destructive action.
- Attempt to DM the affected user where appropriate, but a DM failure must not falsely imply the moderation action failed. Report/log action outcome and DM outcome separately.
- Log actor, target, reason, timestamp, case ID, action outcome, and DM result to moderation channel `1552317097277657129`.
- Bulk purge must respect Discord's age constraints. Do not routinely store deleted message content.
- Lock/unlock must preserve and restore original permission overwrites.
- Validate target and bot role hierarchy, native Discord permissions, user IDs, duration units, and inputs before action.
- Report success only after Discord confirms the underlying action. Keep action failure distinct from logging failure.
- Success embeds use `#8B5CF6`; errors use `#D95C68`; footer: `✦ 𝙰𝚙𝚎𝚡 • Moderation & Security`.

## 5. Utility commands

- `!serverinfo`: server name, member count, creation date, channel counts, owner.
- `!botinfo`: uptime, latency, version, connection/status.
- `!userinfo @user`: account creation date, server join date, roles, nickname, timeout status, highest role.
- `!roleinfo @role`: colour, member count, role position, key permissions.
- `!help`: premium categorized embeds listing syntax, purpose, and required role. Do not reveal unnecessary private implementation details; restricted commands may be shown with permission labels.

## 6. Autorole, welcome, and server management

### Autorole
- `!autorole set @role`
- `!autorole off`
- `!autorole`
- Only Head Owner `༺` and above may configure it.
- Support exactly one automatic member role. Block dangerous/privileged roles from autorole assignment.
- Persist setting. Log configuration changes to role log channel `1552317055217176616`.
- Report missing roles or API/permission failures honestly.

### Welcome
- `!setwelcome #channel`
- `!welcome-test`
- `!welcome off`
- Minimum Head Owner and above.
- Default destination: `1539833608544714822`.
- Style: title `✦ WELCOME TO APEX`; mention the new member; include configured rules/support references; colour `#8B5CF6`; footer `Apex MiddleMan Services • Community & Support`.
- Persist configuration. `off`, `setwelcome`, and `welcome-test` must have clear, consistent behavior. Do not publish a test message to an unexpected channel.

### Server management
- `!nick @user [nickname]` — Lead Designer `〆`.
- `!role add @user @role` and `!role remove @user @role` — Operations Lead `⛭`.
- Log role changes to `1552317055217176616`; log nickname changes to `1552317097277657129`.
- Enforce native permissions, bot/member/role hierarchy, and protection for privileged roles. Never let these commands bypass platform permissions.

## 7. Ticket system

Three independent ticket types and panels. **Do not provide one command that publishes all three panels together.** Publish each panel individually.

### Panel destinations and routing
1. **Middleman Request**: panel channel `1539783649208049704`; ticket channel name `mm・username`; handled by middleman role `⛓・𝐌𝐈𝐃𝐃𝐋𝐄𝐌𝐀𝐍` (`1552358685336928356`). Form fields: trading partner's Discord username; what is being traded; estimated value; who gives first.
2. **General Support**: panel channel `1539785643398533120`; ticket channel name `support・username`; handled by Moderator (`1552344063003467797`) and above. Form fields: what help is needed; explanation; relevant user IDs/evidence.
3. **Request Role Rank Up — STAFF ONLY**: panel channel `1552317076549533827`; ticket channel name `rankup・username`; handled by Server Founder (`1552341106694299699`). Form fields: current staff role; requested role/rank; reason; relevant staff approval/poll/arrangement. Ordinary members must never open this ticket type. The appearance of a rank-up/purchase panel must not make this a public purchase ticket.

### Ticket lifecycle and access
- At most one open ticket per user **per category**.
- Only opener and authorized staff for that ticket category may access it, except explicit individually added users as described below.
- Staff controls: claim/unclaim, priority Low/Normal/High/Urgent, close.
- Add `!adduser @user` for use inside an open ticket. Only authorized staff for that ticket category and Server Founder+ (subject to native permissions) may use it. Add only the explicitly selected user to that single ticket. Do not grant a staff role, global access, or access to other tickets. Ticket opener cannot use this command to bypass access rules. Record actor, added user, ticket ID, and time.
- When a ticket opens, ping the panel's configured Support Role to Ping. Once claimed, stop repeated opening notifications for that ticket.
- Closing requires confirmation. Closure reason is optional; default is `Trade Complete`.
- Reopening means creating a new ticket; no special reopen-in-place workflow is needed.
- Save transcript successfully before deleting the ticket channel. If transcript saving fails, **keep the ticket open**, report the failure, and alert authorized staff.
- Transcript destination: `1552316695744610365`.
- Transcript must include ticket ID/category/opener ID; claimant and assignment changes; users added; priority history; timeline of open/claim/unclaim/add user/reprioritize/close; messages with timestamps/authors and supported attachments; closure actor/reason/transcript status.
- Log ticket opening, claim/unclaim, Add User, priority changes, closure, and transcript failure including IDs, actors, timestamps, old/new priority, closure reason, and transcript outcome.
- Keep transcripts and ticket logs private. Never delete a ticket if its transcript could not be safely saved.

### Optional starter panel copy (saved drafts only; do not automatically publish)

**Middleman panel**
- Title: `✦ Apex — Welcome to Our MM Service`
- Description: `Welcome to Apex Middleman Services, where trades are handled with clarity, structure and transparency. Read the rules below carefully before requesting a middleman.`
- Fields:
  - `🤝 Request Middleman`: `Read our MM Terms of Service before selecting Request Middleman and completing the ticket form. Make sure both trading parties understand the process before proceeding.`
  - `🏆 Vouch Required`: `You must vouch for your middleman after the trade. Failure to provide your vouch within 24 hours may result in being blacklisted from our MM Service, subject to staff review.`
  - `⚠️ Troll Tickets`: `Creating fake, spam or troll middleman tickets may result in a Middleman Service ban.`
  - `📜 Disclaimer`: `Apex is not responsible for events occurring after a trade has been completed. Please verify all trade details and follow the MM Terms of Service throughout the process.`
- Colour `#8B5CF6`; footer `✦ 𝙰𝚙𝚎𝚡 • Trade Smart. Trade Safely.`; button `🤝 Request Middleman`.

**General support panel**
- Title: `✦ Apex Support`
- Description: `Need assistance with a trade dispute, scam report or community inquiry? Our dedicated support team is here to help keep your experience safe, fair and transparent.`
- Fields:
  - `🛡️ Available Support Services`: `Scam Investigations: submit evidence and relevant details for staff to review suspected fraudulent activity. Trade Disputes: request staff review when a trade goes wrong or disagreement needs attention. General Help: server navigation, rules, services and community inquiries.`
  - `🎫 How to Open a Ticket`: `Open a Ticket; Gather Evidence (uncropped screenshots, chat logs, user IDs); Explain Clearly (timeline/details); Cooperate with Staff.`
  - `📜 Core Rules & Limitations`: `No multiple tickets for the same issue; false reports/manipulated evidence may result in disciplinary action; staff decisions follow applicable rules/review process; share sensitive evidence only with authorized staff through a private ticket.`
- Colour `#8B5CF6`; footer `✦ 𝙰𝚙𝚎𝚡 • Strict Confidentiality for Every Case`; button `🎫 Open Support Ticket`.

**Staff-only rank-up panel**
- Title: `🛒 BUY SERVER ROLES`
- Description: `APEX PREMIUM ROLE CATALOGUE. Upgrade your presence within Apex with premium server role options. Open a purchase ticket to discuss available roles, payment arrangements and eligibility with authorized team.`
- Fields:
  - `PRODUCT`: `Premium Server Roles.`
  - `CURRENCY`: `INR (Indian Rupees).`
  - `CRYPTO`: `LTC / USDT via Trust Wallet, subject to confirmation and agreed terms.`
  - `ACCEPTED TRADES`: `Blox Fruits items, subject to staff approval and mutually agreed terms.`
  - `HOW TO PURCHASE`: `Open purchase ticket, tell staff role interested in, confirm price/payment terms, wait for authorized staff to verify arrangement before role assigned.`
  - `SAFETY NOTE`: `Verify recipient and agreed terms before payment. Never share passwords, recovery phrases or private wallet keys.`
- Colour `#8B5CF6`; footer `✦ 𝙰𝚙𝚎𝚡 • Premium Access. Clear Terms. Verified Handling.`; button `💎 Open Purchase Ticket`.
- This template must be bound to the staff-only rank-up workflow, never public purchase-ticket access.

## 8. Ticket Panel Builder (`!panelbuilder`)

Build a fully interactive builder to create, customize, save, preview, duplicate, manage, and publish ticket panels individually. Do not have a command that sends all three panels at once.

Features:
- Title, description, embed fields, colour, image URL, footer.
- Add/edit/reorder/remove field names and values; each field can be inline or not.
- Select approved ticket type and workflow, button label, button emoji, destination channel, and **Support Role to Ping**.
- Preview; save draft; edit a saved panel; duplicate; list/manage saved panels; publish one panel per explicit action; update a published message only after explicit confirmation; delete with confirmation.
- Use Discord buttons, selects, and modals for a guided multi-step wizard. Do not silently drop fields because Discord modal limits require multiple steps.
- Default create/edit/manage/delete/publish permission: Server Founder (`⟡`) and above, plus native Discord permissions/hierarchy validation.
- Persist template fields, ticket type, button config, destination channel ID, support role ID, creator/timestamps, published message/channel IDs, and configuration state. Buttons must keep working after bot restarts.
- Validate Discord embed limits, colour hex, image URL, channel/role IDs, button values, and ticket type.
- Editing a saved template must not silently modify an already published message. An update must require an explicit user confirmation.
- Ticket access is determined by the approved ticket type and access policy, never by how the embed is worded.

### Support Role to Ping requirements
- Every panel must allow selecting an actual server role for opening notifications; save its ID persistently.
- At ticket opening, ping only that configured role using strict allowed-mentions settings. Never allow user-supplied content to create mass mentions.
- Validate that the role still exists and that Discord/bot permissions permit pinging it. If missing or unusable, return a clear error and alert authorized staff; do not silently substitute a different role.
- Log which role was notified and whether the ping succeeded.
- This role is for notification only and **does not automatically grant ticket access**. Ticket access depends on ticket category and the staff access policy.

## 9. Custom Embed / Trigger Command Builder

The user wants to create custom embed triggers later. Do not predefine, preload, or automatically create these educational/help triggers: `!mm`, `!rules`, `!tos`, `!vouch`, `!scam`, `!fees`, or `!support`.

Implement a generic custom embed/trigger builder, for example `!embedbuilder`, `!embed preview`, and `!embed edit ID`, plus a general command-builder flow to associate a user-defined prefix trigger/alias with a saved embed.

Features:
- User-defined trigger and aliases.
- Title, description, fields, hex colour, image URL, footer, and reasonable supported embed properties.
- Preview, save, edit, duplicate, enable/disable, delete, publish/test, and list/manage saved embeds/triggers.
- Persistent storage.
- Validate trigger syntax, reserved built-in command names, collisions, duplicate aliases, URL/colour, content lengths, and Discord embed limits.
- Never allow a saved trigger to shadow built-in commands, bypass permission checks, or trigger a more privileged action.
- Only Server Founder and above may create/edit/manage custom saved embeds and triggers.
- Publishing/user-trigger execution for saved educational embeds: Middleman role and above; commands must remain staff-only.
- Standard `!announce` / `!embed` family may be supported. Minimum role for ordinary announcements is proposed as Lead Designer unless existing approved config sets a stricter requirement.
- Do not invent or publish fees, TOS, or server rules. User will supply content and create their own triggers.

## 10. Giveaways

- Management minimum role: Director of Ops (`1552341937271611443`).
- Announcement channel: `1539328333807100085`.
- Commands: `!giveaway start`, `!giveaway end ID`, `!giveaway reroll ID`, `!giveaway list`, `!giveaway cancel ID`.
- Any server member can enter; exclude bots. Exactly one entry per member.
- Do not require roles, account age, or server tenure for entry.
- Configurable prize, duration, and winner count.
- Automatic end, winner announcement, rerolls, and entry buttons where suitable.
- Persist giveaway ID, host, created/end timestamps, entrants, winners, and final outcome. Recover scheduled endings after restart.
- Cancelled giveaways produce no winner. If there are no eligible entrants, clearly report no winner.
- Audit create/end/cancel/reroll/winner events.
- Command UX/arguments may be chosen for usability while preserving these rules.

## 11. Suggestions

- Submission channel: `1565009040818573412`.
- Review minimum: Lead Designer (`1552343120551616702`).
- Commands: `!suggest [idea]` for everyone; `!suggestion status ID` for everyone; `!suggestion approve ID`, `!suggestion deny ID [reason]`, and `!suggestion implement ID` for Lead Designer and above.
- Public suggestion content; hide submitter identity from ordinary members. Authorized staff may see the submitter when needed.
- 👍 upvote and 👎 downvote buttons, one vote per member, persistent.
- Persist suggestion ID, votes, status history, decision actor, reason, and time.
- Statuses: Pending, Approved, Declined, Implemented.
- Audit submission and staff decisions/status changes.

## 12. Advanced audit and error logging

- Inspection minimum: Server Founder (`1552341106694299699`).
- Channel: `1552317322394468393`.
- Commands: `!audit status`, `!audit recent`, `!audit errors`, `!audit config`.
- Audit moderation, role changes, all ticket actions including `!adduser`, giveaways, suggestions, saved embed publication, ticket-panel configuration/publication, configuration changes, errors, and security events.
- Preserve dedicated destinations: moderation `1552317097277657129`; roles `1552317055217176616`; ticket transcripts/logs `1552316695744610365`; advanced audit/errors `1552317322394468393`.
- Never log tokens, passwords, private keys, wallet recovery phrases, or other secrets. Avoid unnecessary private ticket/deleted content. Restrict stack traces to authorized staff and redact sensitive values.
- Distinguish failure of the requested action from failure to record its log. Implement reasonable configurable retention.

## 13. Storage, reliability, and security requirements

- Use persistent storage, schema migrations, safe DB transactions, and clear initialization instructions.
- Centralize permission checks, command argument parsing, validation, embed creation, and error handling.
- Validate roles, native Discord permissions, role positions, bot hierarchy, and member hierarchy; protect privileged roles.
- Prevent duplicate open tickets, giveaway entries, and votes.
- Persist configurations and interactive component identifiers/state across restarts.
- Use strict allowed mentions. Avoid mass-mention injection and untrusted content becoming mentions.
- Keep private transcripts private. If transcript saving fails, leave the ticket open and alert staff.
- DM failure is distinct from action failure; logging failure is distinct from action failure.
- `!adduser` must be logged and only grant the selected user access to the one selected ticket.
- Persist configuration only after validation succeeds.
- Handle missing channels/roles, failed uploads, API permission errors, database errors, network failures, and Discord rate limits gracefully.
- Never commit or log secrets. Include a `.gitignore` update if appropriate and ensure real `.env` files are excluded.

## 14. Testing requirements

Inspect the existing stack and test framework first. Add and run automated tests for as much of the following as can be exercised without a live Discord server:

1. Prefix parsing and confirmation that no slash commands were added.
2. Minimum-role authorization plus Discord native permission and hierarchy checks.
3. Invalid arguments, IDs, durations, URLs, colour values, and embed-length limits.
4. Migrations and persistence after process restart.
5. Ticket routing, channel access, and one-open-ticket-per-category enforcement.
6. Claim/unclaim, priority changes, and `!adduser` authorization/scope.
7. Close confirmation; transcript saved before deletion; transcript-failure safety.
8. Panel builder create/edit/preview/save/duplicate/publish/delete.
9. Support Role to Ping persistence, allowed-mention behavior, and missing-role handling.
10. Ticket panel buttons continue working after restart.
11. Ordinary members are denied access to the staff-only rank-up ticket.
12. Custom embed/trigger builder, collision detection, reserved command protection, and absence of preloaded educational triggers.
13. Giveaway entry uniqueness, end/cancel/reroll, and restart recovery.
14. Suggestion identity privacy, vote uniqueness, status transitions, and permission checks.
15. Audit logging and log-failure handling.
16. Autorole, welcome, nickname, and role-management permission checks.
17. Missing permissions/channels/roles and API/database failure paths.

Use mocks and isolated test fixtures where live Discord access is not available. Do not claim a live Discord integration test unless actually run. Report test commands and their real results.

## 15. Delivery checklist

When done, provide:
- Summary of features implemented.
- List of changed/new files.
- Dependencies and hosting configuration changes, if any.
- Database initialization and migration instructions.
- `.env.example` with placeholders only.
- Setup, deploy, restart, and troubleshooting instructions.
- Full command list and minimum role requirements.
- Ticket-panel builder guide, especially how to set Support Role to Ping and publish each panel independently.
- Custom embed/trigger command builder guide.
- Required Discord intents, bot permissions, and manual Developer Portal steps.
- Tests actually run and their results.
- Clear known limitations and any features not verified.

Do not ask the user to repeat requirements listed in this document. If something truly cannot be resolved from the repository or this specification, choose the safest reasonable default, document the assumption, and continue. Do not stop at planning; implement the blueprint in the existing project and verify what is possible.
