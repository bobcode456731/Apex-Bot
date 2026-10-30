# Apex MiddleMan Services bot

Discord prefix bot for Apex MiddleMan Services. It uses `!` commands only, `discord.py`, and a persistent SQLite database. Ticket panels, ticket controls, giveaway entry buttons, and suggestion voting buttons use persistent Discord component IDs and are re-registered from the database after restart.

## Project layout

```text
apexbot/
  bot.py          startup, intents, prefix handling, saved embed trigger dispatch
  config.py       confirmed channel/role IDs and environment settings
  database.py     SQLite schema migration, settings, audit storage, retention
  policy.py       pure role-position policy helpers
  common.py       shared embeds, confirmations, permissions, audit delivery
  moderation.py   warnings, moderation actions, lock/unlock, slowmode
  server.py       autorole, welcome, nickname and role management
  tickets.py      ticket access, forms, lifecycle, persistent controls/transcripts
  builders.py     interactive panel and custom embed/trigger builders
  community.py    giveaways, suggestions, and audit inspection
  validation.py   shared input/Discord embed validation
tests/
  test_core.py             offline validation, SQLite, policy, and configuration tests
  test_discord_runtime.py  discord.py command/view-definition smoke test
  runtime_setup_check.py   no-token bot setup-hook and zero-slash-command check
main.py           environment loading and process entry point
```

## Hosting assumptions

- Python 3.11 or newer; Docker uses Python 3.12.
- Run one bot process against one persistent local SQLite file. This fits a Windows/Linux VM or a single Docker container with a persistent volume. Do not run multiple replicas against the same SQLite file. A host that requires horizontal replicas should use a PostgreSQL-backed adapter; that adapter is not included here.
- `DATABASE_PATH` defaults to `data/apexbot.sqlite3`. Keep that path on persistent storage across restarts and deployments.
- Confirmed Discord channel and role IDs are centralized in `apexbot/config.py`. If the server changes them, update those values there and restart.
- The project directory did not contain hosting files or an existing runtime, so the Docker setup is an optional portable deployment path rather than a claim about a specific host.

## Local setup (Windows PowerShell)

```powershell
py -3 -m venv .venv
\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python main.py
```

Set `DISCORD_TOKEN` in `.env` or in the hosting provider's secret store. `.env` is ignored by Git. Do not paste a real token into source files, issue comments, or logs. `main.py` loads `.env` when `python-dotenv` is installed; environment variables supplied by the host also work.

On first startup, the bot creates the database directory and applies schema migration 1 automatically. Back up the database and its `-wal`/`-shm` files together only after stopping the bot; for a simple backup, stop it and copy the `.sqlite3` file. Keep the same database file for restarts so configuration, warnings, tickets, panels, entries, votes, and audit records persist.

## Docker setup

1. Copy `.env.example` to `.env` and set the real token outside source control.
2. Run `docker compose up -d --build`.
3. The named `apexbot-data` volume persists the database. Use `docker compose logs -f apexbot` for startup/runtime diagnostics and `docker compose restart apexbot` after secret or configuration changes.

Do not remove the named volume when rebuilding the image. Back up the volume before upgrades.

## Discord Developer Portal and server setup

1. Create the Discord application and bot, then put its token in `DISCORD_TOKEN`.
2. Enable **Message Content Intent** and **Server Members Intent** in the Developer Portal. The code enables `message_content` and `members`; prefix parsing and member join/autorole/welcome handling need these privileged intents approved and enabled.
3. Invite the bot with only the permissions the features need. Do not grant Administrator as a shortcut. Common permissions are View Channels, Send Messages, Embed Links, Read Message History, Manage Messages, Manage Channels, Manage Roles, Kick Members, Ban Members, Moderate Members, Manage Nicknames, and Manage Server. Configure Mention Everyone only if the selected Support Role to Ping is not itself mentionable.
4. In the live server, position the bot above members and roles it must moderate or edit. The code resolves each configured minimum role and compares its actual live position; it does not infer role hierarchy from numeric IDs. Native actor permissions and bot/member/role hierarchy checks are also applied to platform actions.
5. Verify each configured channel and role ID in `apexbot/config.py`. Keep the ticket transcript, ticket log, moderation log, role log, and advanced audit channels private. The bot refuses audit delivery if `@everyone`, a non-staff role, or a member-specific overwrite can view a configured logging channel. The transcript handler also requires Send Messages and Attach Files and checks the transcript channel is staff-only.
6. Ensure the bot can view/send/embed in the three panel destinations, giveaway and suggestions destinations, and welcome destination. The rank-up panel destination must deny `@everyone` view access.

## Commands and access

“Role+” means the configured role with that exact ID or a role positioned at or above it in the live server. Some commands also require the named native Discord permission. The server owner always passes role thresholds, but Discord still enforces platform hierarchy and permissions for the underlying action.

| Command | Minimum access |
|---|---|
| `!help`, `!serverinfo`, `!botinfo`, `!userinfo [@user]`, `!roleinfo @role` | Everyone |
| `!warn @user [reason]`, `!kick @user [reason]` | Moderator+; Kick also needs native Kick Members and the bot permission |
| `!warnings @user`, `!timeout @user 10m [reason]`, `!untimeout @user [reason]`, `!nick @user [nickname]` | Lead Designer+; platform commands also need the matching native permission |
| `!clearwarnings @user [reason]` | Director of Ops+; confirmation required |
| `!ban @user [reason]`, `!purge 20` | Administrator+; confirmation required; native permissions required |
| `!unban USER_ID [reason]` | Server Founder+ and native Ban Members |
| `!lock [reason]`, `!unlock [reason]`, `!slowmode 5` | Head Owner+ and native Manage Channels |
| `!autorole`, `!autorole set @role`, `!autorole off` | Head Owner+; changes need native Manage Roles |
| `!setwelcome #channel`, `!welcome off`, `!welcome-test` | Head Owner+; changes/tests need native Manage Server |
| `!role add @user @role`, `!role remove @user @role` | Operations Lead+ and native Manage Roles |
| `!panelbuilder ...` | Server Founder+ and native Manage Server |
| `!adduser @user` inside an open ticket | Staff at/above that category's configured minimum or Server Founder+; actor and bot need Manage Channels |
| `!embedbuilder ...` create/edit/manage | Server Founder+ and native Manage Server; publish/test is Middleman+ |
| `!giveaway start|end|reroll|list|cancel ...` | Director of Ops+ and native Manage Server |
| `!suggest [idea]`, `!suggestion status ID` | Everyone |
| `!suggestion approve|deny|implement ID` | Lead Designer+ |
| `!audit status|recent|errors|config` | Server Founder+ |

Warning history is sent by DM or shown only inside the private moderation log channel. Audit reports are sent by DM or shown only in the private audit log channel.

No slash commands are registered. No educational triggers are preloaded. The owner can add `!mm`, `!rules`, `!tos`, `!vouch`, `!scam`, `!fees`, or `!support` later through the custom builder with their own content.

## Ticket panels and lifecycle

1. Run `!panelbuilder`, choose exactly one workflow (Middleman, General Support, or staff-only Rank-up), and complete the guided content, delivery, and button steps. The fields JSON accepts an ordered array such as:

   ```json
   [{"name":"What is being traded?","value":"Describe both sides.","inline":false}]
   ```

   For larger panels, save the draft first, then use `!panelbuilder manage-fields ID` to add, edit, remove, and reorder up to 25 fields without a modal-size limit.
2. Use the builder's Discord channel selector and role selector to choose the exact configured destination and an actual **Support Role to Ping**. This role is a notification only; ticket access comes from the ticket type's live minimum role position. The role must exist, cannot be `@everyone`, and must be mentionable or the bot needs Mention Everyone.
3. Save the draft. `!panelbuilder list`, `!panelbuilder preview ID`, `!panelbuilder edit ID`, `!panelbuilder fields ID <JSON>`, `!panelbuilder manage-fields ID`, and `!panelbuilder duplicate ID` manage one saved panel at a time.
4. `!panelbuilder publish ID` publishes only that panel to its selected destination. There is no command to publish all three together. Editing the saved template does not edit its current Discord message; `!panelbuilder update ID` requires a separate confirmation. `!panelbuilder delete ID` also confirms and disables the published button before removing the saved configuration.
5. Rank-up panels are bound to the configured staff rank-up channel and that channel must deny `@everyone` view. The button also checks Server Founder+ at click time, so a public purchase workflow is never created.

Tickets are private to the opener and roles at/above the relevant live threshold: Middleman for MM, Moderator for support, Server Founder for rank-up. The configured ping role does not grant access. Each user may have one open ticket per type. Ticket controls allow claim/unclaim, priority, and transcript-before-delete closure. The closer enters an optional reason (default `Trade Complete`) and confirms. If the transcript upload fails, the ticket stays open and staff are alerted.

## Custom embeds and triggers

Run `!embedbuilder` to create a trigger, aliases, title, description, ordered fields, hex colour, optional HTTP(S) image, and footer through guided modals. For larger embeds, save first and use `!embedbuilder manage-fields ID` to add, edit, remove, and reorder up to 25 fields. Preview and manage with `!embedbuilder list`, `edit ID`, `preview ID`, `duplicate ID trigger`, `enable ID`, `disable ID`, `delete ID`, or `publish ID`. Deletion confirms. Trigger/alias syntax, built-in command collisions, duplicate aliases, URLs, colours, secret-like content, and Discord embed limits are checked. Saved triggers execute only for Middleman+; publishing is also Middleman+. No rules, TOS, fees, or other community content is invented.

## Giveaways, suggestions, audit

- `!giveaway start 1d 2 <prize>` publishes in the configured giveaway channel. `!giveaway end ID`, `reroll ID`, `list`, and `cancel ID` manage them. Entry buttons exclude bots and the database enforces one entry per member. The background task resumes scheduled endings after restart.
- `!suggest <idea>` removes the source prefix message before posting the idea without a submitter name. The bot needs Manage Messages in the command channel for anonymity. The suggestion ID is sent to the submitter by DM; when DMs are closed, the fallback public confirmation contains no identity or suggestion text. Votes are one per member and persist. `!suggestion status ID` is public; staff decisions are tracked in the private audit log.
- `!audit status`, `recent`, `errors`, and `config` are Founder+ only and private. Audit events are stored in SQLite and old audit rows are pruned using `LOG_RETENTION_DAYS` at startup. Audit delivery and action outcomes are kept separate; log-channel failures are recorded to process logs.

## Tests and limitations

Run the offline suite with:

```powershell
python -m unittest discover -v
```

The full offline suite exercises parser/config validation, command permission gates, actual mocked ticket and panel workflows, transcript contents/failure safety, giveaway and suggestion state changes, database restart persistence, persistent-view registration, logging privacy, and component error handling. It includes 65 tests at the current audited revision. The no-token setup check is:

```powershell
python tests/runtime_setup_check.py
```

It registers all cogs and confirms 30 prefix command roots and zero application/slash commands. The project also compiles with Python's bytecode compiler.

### Troubleshooting

- Prefix commands are ignored: confirm Message Content Intent is enabled in both the Developer Portal and the invite/runtime, then restart the bot.
- Member joins do not receive autorole or welcome: confirm Server Members Intent, the saved role/channel ID, and the bot's role position and permissions.
- A ticket panel button says unavailable after restart: confirm the same database file is mounted and the panel row remains `published`. Published components are re-registered from that state.
- Transcript closure fails: verify the configured transcript channel exists, is private from `@everyone`, and allows the bot to Send Messages and Attach Files. The ticket is deliberately retained on failure.
- Audit messages are missing: verify both the selected log channel and advanced audit channel exist, deny `@everyone` view, and allow the bot to send embeds.
- Autorole/role changes fail: place the bot above the selected target role and grant native Manage Roles; configured role names alone do not override Discord hierarchy.
- Database resets on deploy: mount the persistent `DATABASE_PATH` or Docker named volume and keep it across image rebuilds.

No bot token or live Discord connection was available in the project folder. Therefore Discord API permissions, role/channel IDs in the actual server, interactions, message delivery, restart recovery of real Discord components, and moderation/ticket/giveaway/suggestion operations still need a controlled live-server check. See the test run recorded in the delivery response for exact local results.
