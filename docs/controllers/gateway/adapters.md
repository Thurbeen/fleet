# Gateway adapters

Design, not implemented. Slack is the first adapter. Telegram, Discord and
Microsoft Teams are mapped on paper only, to check the
[port](interface.md#gateway-port) against platforms that differ, and what
they showed is folded back into the model under
[what the platforms changed](#what-the-platforms-changed). Every platform
claim cites the platform's own documentation, read on 2026-10-26; a claim
those pages do not state is marked **unverified**.

## Slack, the first adapter

| Port item | Slack |
| --- | --- |
| Account | One workspace (`team_id`, or `enterprise_id` in an Enterprise org, which Slack advises keying stored data on[^slack-ent]), one bot, one `[gateway.accounts.<name>]` entry with `platform = "slack"`. |
| Conversation | A thread: the channel id and the parent's `ts`. A message is in a thread when it has `thread_ts`; `thread_ts == ts` is the parent[^slack-threads]. `ts` is unique within a channel[^slack-threads]. Address `slack:C0123ABCD:1730000000.000100`. A reply is posted with the parent's `ts` as `thread_ts`, never a reply's[^slack-post]. |
| Message ids | `ts`, unique within the channel; the event wrapper's `event_id`, globally unique, is the receive verb's de-duplication key[^slack-events]. |
| Inbound | Message events through the `message.channels` and `message.groups` subscriptions, with `channels:history` and `groups:history`[^slack-message]. Thread replies arrive as ordinary message events; `message_replied` lacks its subtype over the Events API, so a reply is told by `thread_ts != ts`[^slack-replied]. |
| Mention | `<@U…>` with Fleet's bot user id in `text`[^slack-format]. The adapter reads the mention from the message event rather than relying on `app_mention`, which needs `app_mentions.read` and whose behaviour inside threads the reference does not state (**unverified**)[^slack-mention]. |
| Transport | Socket Mode: an app-level token with `connections:write`, `apps.connections.open` for a temporary `wss://` URL, each envelope acknowledged by its `envelope_id` so Slack knows whether to retry[^slack-socket]; within three seconds, per Slack's Java SDK guide[^slack-java] (the Socket Mode page itself gives no limit). While disconnected the app "may lose events", and Slack refreshes connections every few hours; retry counts for Socket Mode are **unverified**[^slack-socket]. The Events API over HTTP instead needs a public endpoint, a 2xx within three seconds, and retries up to three times, at once, after one minute and after five[^slack-events]. Socket Mode apps are not allowed in the public Marketplace[^slack-compare], which does not matter for an internal app. |
| History | `conversations.replies` for a thread, `conversations.history` for a channel, both Tier 3 (50+ per minute)[^slack-replies]. Since 2025-05-29, commercially distributed apps not approved for the Marketplace get Tier 1 (one request a minute, 15 objects) on new installs; internal customer-built apps are not affected[^slack-limits2025]. Fleet's Slack app is an internal app for that reason. |
| Post and open | `chat.postMessage`, with `thread_ts` for a post and without for an open; the new thread's address is the channel and the returned `ts`. 4,000 characters is the recommended length and text over 40,000 is truncated[^slack-post]; the adapter splits at 4,000. `mrkdwn` is Slack's own syntax, not Markdown, and `&`, `<` and `>` are escaped[^slack-format]. About one message a second per channel[^slack-post], so posts to one thread are sequential anyway. |
| Own posts | Every post carries message metadata, `{"event_type": "fleet_post", "event_payload": {"key": "<intent key>"}}`[^slack-post], read back by `conversations.history` and `conversations.replies` with `include_all_metadata`, under `message_metadata:read`[^slack-metadata][^slack-replies]. Metadata is visible to every member of the workspace[^slack-post]: a key names a Task and its `uid`, never a secret. A bot's message carries `bot_id`[^slack-bot]. Whether the app receives its own posts as events is not stated; Bolt's `ignoreSelf` implies it does[^slack-ignoreself], so the adapter reads them as `author.kind = fleet`. |
| Idempotency | None on `chat.postMessage`: `client_msg_id` is not an argument[^slack-post]. Recovery is by the metadata key in a later `history` read. |
| Identity | User ids, unique in their workspace, paired with the team[^slack-user]; in an Enterprise org every user has one global id starting with `U` or `W`[^slack-ent]. The allowlist names ids, never display names or emails. |
| Credentials | `curl`, as the Bitbucket Cloud forge adapter does: the bot token lives in a curl config file the operator owns, named by `curlConfig`; Fleet never constructs an Authorization header. The listener holds the app-level token. |

Two things a Slack route must know. A message posted while the listener was
disconnected may be lost[^slack-socket]: in a tied thread the history
backstop recovers it; a new thread's first mention during an outage is lost
and the person mentions again. And the bot must be a member of every
channel a route covers for message events and history to reach it; whether
`message.channels` delivers without membership is not stated
(**unverified**).

## Cross-platform check

| | Slack | Telegram | Discord | Microsoft Teams |
| --- | --- | --- | --- | --- |
| Conversation | A thread, channel and parent `ts`[^slack-threads] | A forum topic, chat and `message_thread_id`, in supergroups and in private chats with topics enabled[^tg-message][^tg-topics]; otherwise the whole chat. `reply_to_message` is one level deep[^tg-message]: reply chains are not threads. | A thread, which is a channel with a `parent_id`[^dc-threads]; started from a message, its id is that message's[^dc-start] | A channel reply chain, `conversation.id` ending `;messageid=<root>`[^ms-channel]; group chats are not threaded[^ms-concepts] |
| Mention | `<@U…>` in text[^slack-format] | `mention` and `text_mention` entities[^tg-entity]. With privacy mode on, the documented deliveries are commands, replies to the bot and messages via it; a bare mention is not listed (**unverified**)[^tg-privacy] | `mentions` array, `<@id>`[^dc-message][^dc-format] | A `mention` entity whose `mentioned.id` is the bot[^ms-rsc] |
| Unmentioned messages | Delivered with the history scopes[^slack-message] | Only with privacy mode off or the bot an admin[^tg-privacy] | Content only with the privileged `MESSAGE_CONTENT` intent; mentions and DMs carry content without it[^dc-intent] | Only with the RSC permission `ChannelMessage.Read.Group`; by default only mentions[^ms-channel][^ms-rsc] |
| Transport | Socket Mode or HTTPS events[^slack-socket][^slack-events] | `getUpdates` long poll, an update confirmed by a higher `offset`, kept 24 hours; or a webhook, exclusive with polling[^tg-updates] | The Gateway WebSocket; Resume replays lost events, an expired session does not[^dc-gateway]; HTTP webhook events carry no channel messages[^dc-webhooks] | HTTPS POST to one messaging endpoint, JWT-signed; a retry after 15 seconds, so duplicates[^ms-auth][^ms-concepts] |
| History | `conversations.replies`[^slack-replies] | No method for bots; none found (**unverified** by absence) | `GET /channels/{id}/messages` with `after`, 100 per page[^dc-messages] | Microsoft Graph `…/messages/{id}/replies`, under `ChannelMessage.Read.Group` or `.All`[^ms-graph] |
| Own posts | Bot id and message metadata[^slack-metadata] | `from.id` equals `getMe`'s id[^tg-user]; no metadata field on `sendMessage`[^tg-send] | `author.id`; `nonce` with `enforce_nonce` returns the existing message for a repeat "in the past few minutes"[^dc-create] | `28:<appId>`; Graph `from.application`[^ms-graph-from]; no idempotency key documented (**unverified**) |
| Allowlist identity | User id, per workspace or global in Enterprise[^slack-user][^slack-ent] | Numeric `User.id`[^tg-user] | User snowflake; usernames change[^dc-user] | `aadObjectId`, unique to the user; the `29:` id is per bot[^ms-proactive] |
| Open a thread | One `chat.postMessage` | `createForumTopic` then `sendMessage`[^tg-forum] | A message, then Start Thread from Message[^dc-start] | Create a conversation in the channel, with the app installed and the `serviceUrl` of an earlier inbound activity[^ms-proactive] |
| Limits | 4,000 recommended, 40,000 truncated[^slack-post] | 4,096 characters; one message a second per chat, 20 a minute per group[^tg-send][^tg-limits] | 2,000 characters[^dc-create] | About 100 KB, aim for 80 KB[^ms-format]; 7 sends a second per conversation[^ms-rate] |
| Breaks the model | Nothing; lost events while disconnected | No history, no marker: an Unknown send can never be recovered; mid-chat mentions see only what Fleet received; bot-to-bot messages arrive when opted in[^tg-b2b] | Opening is two calls; recovery depends on a short nonce window | Unmentioned replies never arrive without RSC; no marker; the Bot Framework SDK is archived in favour of the Teams SDK, which keeps the HTTPS endpoint[^ms-sdk][^ms-teams-sdk] |

## What the platforms changed

Each correction is already in the [interface](interface.md) and the
[resource](../../resources/conversation.md):

1. **A thread is optional.** Telegram chats without topics, direct messages
   and Teams group chats have no threads. The address is
   `platform:chat[:thread]`, and a chat with no threads is one
   conversation. A route over such a chat ties the whole chat to one Task.
2. **"Every message is a turn" depends on delivery.** Slack delivers every
   message in a channel the bot is in; Telegram, Discord and Teams deliver
   unmentioned messages only under a platform permission. The adapter's
   `capabilities` reports whether unmentioned messages reach Fleet on an
   account; without it a tied thread relays only mentions, and the route
   raises `NeedsAttention/MentionsOnly` once rather than pretending.
3. **Recognising Fleet's posts is a capability, not a given.** Slack has a
   metadata marker, Discord a nonce window, Telegram and Teams nothing.
   `capabilities` reports `marker` as `metadata`, `nonce` with its window,
   or `none`. With `none`, an Unknown post outcome raises
   `NeedsAttention/PostUnconfirmed` and is never repeated, and the
   conversation's later posts wait for a person.
4. **History is a capability.** Slack, Discord and Teams (under a separate
   Graph permission) can read a thread; Telegram cannot. With no history,
   a mention partway through a chat carries only what the receive verb
   spooled, and the join message says so. The backstop poll exists only
   where history does.
5. **Opening a thread is not one call everywhere.** On Discord and Telegram
   it is two; the adapter keys both steps from the one intent key. On
   Discord the thread's id is the starter message's, so a repeated second
   step finds the thread already there.
6. **Identity is an opaque id in a scope.** No allowlist entry is a display
   name, a username or an email; Discord and Telegram usernames change, and
   Teams' per-bot ids are not the person's. `[gateway.people]` maps a
   person's name to one id per account.
7. **Fleet's own bot is not the only bot.** Telegram now delivers other
   bots' messages when a bot opts in[^tg-b2b]; every adapter filters on
   Fleet's own id from `whoami`, and other bots are `bot` authors that
   admission ignores unless a route lists them.

[^slack-ent]: <https://docs.slack.dev/enterprise/developing-for-enterprise-orgs/>
[^slack-threads]: <https://docs.slack.dev/messaging/retrieving-messages>
[^slack-post]: <https://docs.slack.dev/reference/methods/chat.postMessage>
[^slack-events]: <https://docs.slack.dev/apis/events-api/>
[^slack-message]: <https://docs.slack.dev/reference/events/message>
[^slack-replied]: <https://docs.slack.dev/reference/events/message/message_replied>
[^slack-format]: <https://docs.slack.dev/messaging/formatting-message-text>
[^slack-mention]: <https://docs.slack.dev/reference/events/app_mention>
[^slack-socket]: <https://docs.slack.dev/apis/events-api/using-socket-mode>
[^slack-java]: <https://docs.slack.dev/tools/java-slack-sdk/guides/socket-mode/>
[^slack-compare]: <https://docs.slack.dev/apis/events-api/comparing-http-socket-mode>
[^slack-replies]: <https://docs.slack.dev/reference/methods/conversations.replies>
[^slack-limits2025]: <https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps>
[^slack-metadata]: <https://docs.slack.dev/messaging/message-metadata>
[^slack-bot]: <https://docs.slack.dev/reference/events/message/bot_message>
[^slack-ignoreself]: <https://docs.slack.dev/tools/bolt-js/reference/variables/ignoreself>
[^slack-user]: <https://docs.slack.dev/reference/objects/user-object/>
[^tg-message]: <https://core.telegram.org/bots/api#message>
[^tg-topics]: <https://core.telegram.org/bots/features#topics-in-private-chats>
[^dc-threads]: <https://docs.discord.com/developers/topics/threads>
[^dc-start]: <https://docs.discord.com/developers/resources/channel#start-thread-from-message>
[^ms-channel]: <https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-and-group-conversations>
[^ms-concepts]: <https://learn.microsoft.com/en-us/microsoftteams/platform/bots/bot-concepts>
[^tg-entity]: <https://core.telegram.org/bots/api#messageentity>
[^tg-privacy]: <https://core.telegram.org/bots/features#privacy-mode>
[^dc-message]: <https://docs.discord.com/developers/resources/message#message-object>
[^dc-format]: <https://docs.discord.com/developers/reference#message-formatting>
[^ms-rsc]: <https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/enable-receive-all-chat-messages>
[^dc-intent]: <https://docs.discord.com/developers/events/gateway#message-content-intent>
[^tg-updates]: <https://core.telegram.org/bots/api#getting-updates>
[^dc-gateway]: <https://docs.discord.com/developers/events/gateway#resuming>
[^dc-webhooks]: <https://docs.discord.com/developers/events/webhook-events#event-types>
[^ms-auth]: <https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication?view=azure-bot-service-4.0>
[^dc-messages]: <https://docs.discord.com/developers/resources/message#get-channel-messages>
[^ms-graph]: <https://learn.microsoft.com/en-us/graph/api/chatmessage-list-replies?view=graph-rest-1.0>
[^tg-user]: <https://core.telegram.org/bots/api#user>
[^tg-send]: <https://core.telegram.org/bots/api#sendmessage>
[^dc-create]: <https://docs.discord.com/developers/resources/message#create-message>
[^ms-graph-from]: <https://learn.microsoft.com/en-us/graph/api/resources/chatmessagefromidentityset?view=graph-rest-1.0>
[^dc-user]: <https://docs.discord.com/developers/resources/user#user-object>
[^ms-proactive]: <https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages>
[^tg-forum]: <https://core.telegram.org/bots/api#createforumtopic>
[^tg-limits]: <https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this>
[^ms-format]: <https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/format-your-bot-messages>
[^ms-rate]: <https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/rate-limit>
[^tg-b2b]: <https://core.telegram.org/bots/features#bot-to-bot-communication>
[^ms-sdk]: <https://learn.microsoft.com/en-us/azure/bot-service/index-bf-sdk?view=azure-bot-service-4.0>
[^ms-teams-sdk]: <https://learn.microsoft.com/en-us/microsoftteams/platform/teams-sdk/why>
