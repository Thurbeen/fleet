//! Acceptance for the Gateway's first step: the `[gateway]` section loads,
//! validates and is refused for each invalid case the config page lists,
//! the fake adapter is wired when the manager runs, and the build names the
//! platforms it has. Then the mirror: `fleet gateway receive` creates a
//! Conversation and spools events on it, ConversationController numbers
//! them with the thread's history, dedupes, edits, orphans and retires, and
//! `--beat`, `fleet gateway account` and `fleet gateway status` report each
//! account's listener. Admission, Task creation and GatewayController are
//! later steps.

mod support;

use std::fs;

use fleet_api::gateway::{Author, AuthorKind, ConversationRef, Message};
use serde_json::{Value, json};
use support::{
    ALEX_ID, Env, GATEWAY_ACCOUNT, GATEWAY_CHANNEL, GATEWAY_ROUTE, SAM_ID, assert_condition,
    conversation_name, gateway_section, message_event,
};

/// The fake platform's clock, and the thread the scenarios mirror.
const T0: u64 = 1_730_000_000_000;
const THREAD: &str = "fake:C0123ABCD:1730000000000";
const BOT: &str = "B0FLEET001";

fn person(id: &str, at: u64, text: &str) -> Message {
    Message {
        id: at.to_string(),
        author: Author {
            id: id.into(),
            name: "Sam".into(),
            kind: AuthorKind::Person,
        },
        mentions_fleet: false,
        at,
        text: text.into(),
        key: None,
        starts_thread: false,
    }
}

/// A gateway environment whose fake platform holds one thread with one
/// earlier message from Sam, and the name its Conversation takes.
fn mirror(name: &str) -> (Env, String) {
    let env = Env::gateway(name);
    env.fake_gateway()
        .seed(
            &ConversationRef::split(THREAD).unwrap(),
            vec![person(SAM_ID, T0, "The nightly export failed again.")],
        )
        .unwrap();
    (env, conversation_name(GATEWAY_ACCOUNT, THREAD))
}

fn mention(id: &str, at: u64) -> Value {
    message_event(
        THREAD,
        id,
        ALEX_ID,
        at,
        &format!("<@{BOT}> can you look into it?"),
    )
}

fn message_ids(st: &Value) -> Vec<(String, u64, u64, u64)> {
    st["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| {
            (
                m["id"].as_str().unwrap().to_string(),
                m["seq"].as_u64().unwrap(),
                m["firstSeq"].as_u64().unwrap(),
                m["position"].as_u64().unwrap(),
            )
        })
        .collect()
}

fn root(env: &Env) -> String {
    env.root.to_string_lossy().replace('\\', "/")
}

/// A second route over the fake account, with its own channel, brief and
/// workspace; a case overrides one field to collide with the first.
fn second_route(env: &Env, channel: &str, dir: &str) -> String {
    let root = root(env);
    format!(
        "\n[gateway.routes.ops]\naccount = \"{GATEWAY_ACCOUNT}\"\nchannels = [\"{channel}\"]\nallow = [\"alex\"]\n\
taskConfig = \"conversation\"\nbrief = \"{root}/briefs/ops.md\"\nworkspace = {{ dir = \"{dir}\" }}\n"
    )
}

#[test]
fn the_gateway_section_loads_and_the_fake_platform_is_wired() {
    let env = Env::gateway("loads");
    env.fleet(&["list", "tasks"]).ok();
    assert!(
        !env.root.join("gateway").exists(),
        "no verb opened the adapter yet"
    );
    env.reconcile().ok();
    assert!(
        env.root.join("gateway").is_dir(),
        "the manager run opened the fake adapter at the account's dir"
    );
    let gateway = env.fake_gateway();
    let calls = gateway.state().unwrap().calls;
    assert_eq!(
        calls.keys().collect::<Vec<_>>(),
        ["capabilities", "whoami"],
        "the account's Listener asked for its identity and nothing else was called: {calls:?}"
    );
    let v = Env::new("gateway-version")
        .fleet(&["version", "--json"])
        .ok()
        .json();
    assert_eq!(v["gateways"], json!(["fake"]));
}

#[test]
fn each_invalid_gateway_case_is_refused_at_load() {
    let env = Env::gateway("invalid");
    let base = gateway_section(&env.root);
    let root = root(&env);
    let dir = format!("{root}/work/conversations");
    let cases: Vec<(String, &str)> = vec![
        (
            base.replace(
                &format!("account = \"{GATEWAY_ACCOUNT}\""),
                "account = \"nobody\"",
            ),
            "routes.eng: account \"nobody\" is not under [gateway.accounts]",
        ),
        (
            base.replace("allow = [\"alex\", \"sam\"]", "allow = [\"alex\", \"kim\"]"),
            "routes.eng: allow names \"kim\", which is not under [gateway.people]",
        ),
        (
            format!("{base}\n[gateway.people.kim]\nelsewhere = \"U0789IJKL\"\n"),
            "people.kim: account \"elsewhere\" is not under [gateway.accounts]",
        ),
        (
            format!(
                "{}\n[gateway.people.kim]\nelsewhere = \"U0789IJKL\"\n",
                base.replace(
                    "allow = [\"alex\", \"sam\"]",
                    "allow = [\"alex\", \"kim\"]"
                )
                .replace(
                    &format!("[gateway.accounts.{GATEWAY_ACCOUNT}]"),
                    &format!(
                        "[gateway.accounts.elsewhere]\nplatform = \"fake\"\nbot = \"B0FLEET002\"\ninbound = \"connection\"\ndir = \"elsewhere\"\n\n[gateway.accounts.{GATEWAY_ACCOUNT}]"
                    )
                )
            ),
            "routes.eng: allow names \"kim\", who has no id on account \"fake-main\"",
        ),
        (
            format!("{base}{}", second_route(&env, GATEWAY_CHANNEL, &format!("{root}/work/ops"))),
            "channel \"C0123ABCD\" belongs to routes eng and ops",
        ),
        (
            format!("{base}{}", second_route(&env, "C0789IJKL", &dir)),
            "workspace of route ops equals, contains or lies inside route eng's",
        ),
        (
            format!("{base}{}", second_route(&env, "C0789IJKL", &format!("{root}/work"))),
            "workspace of route ops equals, contains or lies inside route eng's",
        ),
        (
            format!("{base}{}", second_route(&env, "C0789IJKL", &format!("{dir}/deeper"))),
            "workspace of route ops equals, contains or lies inside route eng's",
        ),
        (
            base.replace(
                &format!("questions = \"{GATEWAY_ROUTE}\""),
                "questions = \"nowhere\"",
            ),
            "gateway.questions \"nowhere\" is not under [gateway.routes]",
        ),
        (
            base.replace("mention = \"required\"", "mention = \"always\""),
            "unknown variant `always`",
        ),
        (
            base.replace("inbound = \"connection\"", "inbound = \"carrier-pigeon\""),
            "unknown variant `carrier-pigeon`",
        ),
        (
            base.replace("inbound = \"connection\"\n", ""),
            "missing field `inbound`",
        ),
        (
            base.replace(
                &format!("brief = \"{root}/briefs/conversation.md\""),
                "brief = \"briefs/conversation.md\"",
            ),
            "routes.eng: brief must be an absolute path",
        ),
        (
            base.replace(
                &format!("workspace = {{ dir = \"{dir}\" }}"),
                "workspace = { dir = \"work/conversations\" }",
            ),
            "routes.eng: workspace.dir must be an absolute path",
        ),
        (
            base.replace("allow = [\"alex\", \"sam\"]", "allow = []"),
            "routes.eng: the route admits nobody",
        ),
        (
            base.replace(
                &format!("channels = [\"{GATEWAY_CHANNEL}\"]"),
                "channels = []",
            ),
            "routes.eng: the route covers no channel",
        ),
        (
            base.replace("mention = \"required\"", "colour = \"red\""),
            "unknown field `colour`",
        ),
        (
            base.replace("callTimeoutMs = 3000", "retainMs = 0"),
            "gateway.retainMs must be at least 1",
        ),
        (
            base.replace(
                &format!("[gateway.people.alex]\n{GATEWAY_ACCOUNT} = \"{ALEX_ID}\""),
                &format!("[gateway.people.alex]\n{GATEWAY_ACCOUNT} = \"\""),
            ),
            "people.alex: the id on account \"fake-main\" is empty",
        ),
    ];
    for (section, needle) in cases {
        env.set_gateway(&section);
        env.fleet(&["list", "tasks"]).code(1).stderr_has(needle);
    }
    // The valid section loads again.
    env.set_gateway(&base);
    env.fleet(&["list", "tasks"]).ok();
}

#[test]
fn an_account_this_build_cannot_serve_is_refused_when_the_gateway_opens() {
    let env = Env::gateway("platform");
    let base = gateway_section(&env.root);
    let slack = base.replace(
        "platform = \"fake\"\nbot = \"B0FLEET001\"\ninbound = \"connection\"\ndir = \"gateway\"\n",
        "platform = \"slack\"\nteam = \"T0123ABCD\"\nbot = \"U0BOT0001\"\ninbound = \"socket-mode\"\ncli = \"curl\"\ncurlConfig = \"/etc/fleet/slack-curl.conf\"\n",
    );
    assert_ne!(slack, base);
    env.set_gateway(&slack);
    // The section is well formed, so every verb but the manager's runs.
    env.fleet(&["list", "tasks"]).ok();
    env.reconcile()
        .code(1)
        .stderr_has("accounts.fake-main: unknown platform \"slack\"; this build has fake");
    env.set_gateway(&base.replace("dir = \"gateway\"\n", ""));
    env.reconcile()
        .code(1)
        .stderr_has("accounts.fake-main: a fake account needs a dir");
    env.set_gateway(&base.replace("inbound = \"connection\"", "inbound = \"socket-mode\""));
    env.reconcile().code(1).stderr_has(
        "accounts.fake-main: inbound \"socket-mode\" is not how a fake listener delivers",
    );
    env.set_gateway(&base);
    env.reconcile().ok();
}

#[test]
fn a_mention_creates_the_conversation_and_the_first_pass_numbers_it_with_the_history() {
    let (env, conv) = mirror("mirror");
    let out = env
        .receive("Ev01", mention("1730000300000", T0 + 300_000))
        .ok()
        .json();
    assert_eq!(out["outcome"], "written", "{out:#}");
    assert_eq!(out["conversation"], conv);
    let obj = env.fleet(&["get", "conversation", &conv]).ok().json();
    assert_eq!(
        obj["spec"],
        json!({"account": GATEWAY_ACCOUNT, "address": THREAD, "route": GATEWAY_ROUTE})
    );
    assert_eq!(obj["status"]["received"].as_array().unwrap().len(), 1);
    assert_eq!(obj["status"]["received"][0]["id"], "Ev01");

    let report = env.reconcile().ok().json();
    assert_eq!(report["errors"], json!([]), "{report:#}");
    let st = env.conversation_status(&conv);
    assert_eq!(st["phase"], "Untied", "{st:#}");
    assert_eq!(
        st["ref"],
        json!({"platform": "fake", "chat": GATEWAY_CHANNEL, "thread": "1730000000000"})
    );
    assert_eq!(st["revision"], 1);
    assert_eq!(
        message_ids(&st),
        vec![
            ("1730000000000".to_string(), 1, 1, 0),
            ("1730000300000".to_string(), 1, 1, 1),
        ],
        "the history and the event are numbered in one revision, oldest first: {st:#}"
    );
    assert_eq!(st["messages"][0]["via"]["verified"], "history");
    assert_eq!(st["messages"][0]["author"]["id"], SAM_ID);
    assert_eq!(st["messages"][1]["via"]["verified"], "connection");
    assert_eq!(st["messages"][1]["mentionsFleet"], true);
    assert_eq!(
        st["starter"],
        json!({"id": "1730000000000", "kind": "person"})
    );
    assert_eq!(
        st["received"],
        json!([]),
        "the spool was drained in the same write"
    );
    assert_eq!(st["seenEvents"], json!(["Ev01"]));
    assert_eq!(st["droppedBefore"], 0);
    assert_eq!(st["lastMessageAt"], T0 + 300_000);
    assert_condition(&st, "Reachable", "True", "Observed");
    assert_condition(&st, "Tied", "False", "Untied");
    assert!(
        st["notBefore"].as_u64().unwrap() > st["observedAt"].as_u64().unwrap(),
        "retention is the next look: {st:#}"
    );
    env.fleet(&["status", "conversation", &conv])
        .ok()
        .stdout_has("revision 1")
        .stdout_has("2 message(s)");
    env.fleet(&["list", "conversations"])
        .ok()
        .stdout_has("Untied");
    assert_eq!(
        env.fake_gateway().calls("history").unwrap(),
        1,
        "one first read"
    );
    // Quiet: an Untied thread is not read again until something arrives.
    env.reconcile().ok();
    assert_eq!(env.conversation_status(&conv), st);
    env.assert_no_temp();
}

#[test]
fn a_redelivered_event_changes_nothing_and_edits_and_removals_move_the_mirror() {
    let (env, conv) = mirror("dedupe");
    env.receive("Ev01", mention("1730000300000", T0 + 300_000))
        .ok();
    env.reconcile().ok();
    let before = env.conversation_status(&conv);
    let again = env
        .receive("Ev01", mention("1730000300000", T0 + 300_000))
        .ok()
        .json();
    assert_eq!(again["outcome"], "duplicate", "{again:#}");
    assert_eq!(env.conversation_status(&conv), before);
    // Redelivered and numbered from history too: still one message.
    env.receive(
        "Ev02",
        message_event(
            THREAD,
            "1730000000000",
            SAM_ID,
            T0,
            "The nightly export failed again.",
        ),
    )
    .ok();
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(st["revision"], 1, "nothing new was numbered: {st:#}");
    assert_eq!(st["messages"].as_array().unwrap().len(), 2);
    assert_eq!(st["seenEvents"], json!(["Ev01", "Ev02"]));

    // A newer edit moves the message's number; an older one changes nothing.
    let edited = |id: &str, text: &str, edited_at: u64| {
        json!({"type": "edited", "conversation": {"platform": "fake", "chat": GATEWAY_CHANNEL, "thread": "1730000000000"},
               "id": id, "text": text, "editedAt": edited_at})
    };
    env.receive(
        "Ev03",
        edited(
            "1730000300000",
            "can you look into the export?",
            T0 + 400_000,
        ),
    )
    .ok();
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(st["revision"], 2);
    assert_eq!(
        message_ids(&st)[1],
        ("1730000300000".to_string(), 2, 1, 1),
        "seq moved, firstSeq and position did not: {st:#}"
    );
    assert_eq!(st["messages"][1]["text"], "can you look into the export?");
    assert_eq!(st["messages"][1]["editedAt"], T0 + 400_000);
    env.receive("Ev04", edited("1730000300000", "stale text", T0 + 350_000))
        .ok();
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(
        st["revision"], 2,
        "an older edit delivered late changes nothing"
    );
    assert_eq!(st["messages"][1]["text"], "can you look into the export?");

    // A removal leaves the mirror.
    env.receive(
        "Ev05",
        json!({"type": "removed", "conversation": {"platform": "fake", "chat": GATEWAY_CHANNEL, "thread": "1730000000000"},
               "id": "1730000000000"}),
    )
    .ok();
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(st["revision"], 3);
    assert_eq!(message_ids(&st).len(), 1);
    assert_eq!(st["starter"], json!({"removed": true}), "{st:#}");
    env.assert_no_temp();
}

#[test]
fn the_spool_holds_a_hundred_events_and_refuses_the_next() {
    let (env, conv) = mirror("spool");
    env.reconcile().ok();
    for i in 0..100 {
        let at = T0 + 300_000 + i * 1000;
        let text = if i == 0 {
            format!("<@{BOT}> look")
        } else {
            format!("message {i}")
        };
        env.receive(
            &format!("Ev{i:03}"),
            message_event(THREAD, &at.to_string(), ALEX_ID, at, &text),
        )
        .ok();
    }
    let at = T0 + 999_000;
    env.receive(
        "Ev100",
        message_event(THREAD, &at.to_string(), ALEX_ID, at, "one too many"),
    )
    .code(1)
    .stderr_has("holds 100 unread events");
    let listener = env.listener_status(&format!("listener-{GATEWAY_ACCOUNT}"));
    assert_eq!(
        listener["refused"]["counts"]["SpoolFull"], 1,
        "{listener:#}"
    );
    assert!(
        listener["refused"]["lastFaultAt"].is_null(),
        "a full queue is one thread's trouble, not the listener's fault: {listener:#}"
    );
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(st["revision"], 1);
    assert_eq!(st["messages"].as_array().unwrap().len(), 101, "{st:#}");
    assert_eq!(st["received"], json!([]));
    assert_eq!(st["seenEvents"].as_array().unwrap().len(), 100);
    env.receive(
        "Ev100",
        message_event(THREAD, &at.to_string(), ALEX_ID, at, "one too many"),
    )
    .ok();
}

#[test]
fn refusals_and_ignored_events_each_answer_as_the_interface_says() {
    let (env, conv) = mirror("refused");
    env.reconcile().ok();
    let listener = format!("listener-{GATEWAY_ACCOUNT}");
    env.fleet_stdin(
        &["gateway", "receive", "--account", "nobody"],
        &json!({"verified": "connection", "receivedAt": 1, "payload": {}}).to_string(),
    )
    .code(1)
    .stderr_has("account \"nobody\" is not configured under [gateway.accounts]");
    // The wrong `verified` is the listener's own fault.
    env.fleet_stdin(
        &["gateway", "receive", "--account", GATEWAY_ACCOUNT],
        &json!({"verified": "signature", "receivedAt": 1, "payload": {"id": "Ev01", "event": mention("1730000300000", T0 + 300_000)}}).to_string(),
    )
    .code(1)
    .stderr_has("verified \"signature\" is not how account fake-main's listener delivers");
    let st = env.listener_status(&listener);
    assert_eq!(st["refused"]["counts"]["WrongVerified"], 1, "{st:#}");
    assert!(st["refused"]["lastFaultAt"].is_u64());
    // Not an envelope: the listener's fault too, and Unknown.
    env.fleet_stdin(
        &["gateway", "receive", "--account", GATEWAY_ACCOUNT],
        "not json",
    )
    .code(4);
    let st = env.listener_status(&listener);
    assert_eq!(st["refused"]["counts"]["NotEnvelope"], 1, "{st:#}");
    // A payload the adapter cannot read: Unknown, one thread's trouble.
    env.fleet_stdin(
        &["gateway", "receive", "--account", GATEWAY_ACCOUNT],
        &json!({"verified": "connection", "receivedAt": 1, "payload": {"id": "Ev02", "event": {"type": "wave"}}}).to_string(),
    )
    .code(4);
    let st = env.listener_status(&listener);
    assert_eq!(st["refused"]["counts"]["UnreadablePayload"], 1, "{st:#}");
    assert_eq!(st["beatRevision"], 3);
    env.reconcile().ok();
    assert_condition(
        &env.listener_status(&listener),
        "InboundReady",
        "False",
        "NoListener",
    );
    // Ignored, with exit 0 and no Conversation: a reaction, a channel no
    // route covers, and chatter in a thread Fleet does not mirror.
    let ignored = |delivery: &str, payload: Value| -> Value {
        env.fleet_stdin(
            &["gateway", "receive", "--account", GATEWAY_ACCOUNT, "--json"],
            &json!({"verified": "connection", "receivedAt": 1, "payload": {"id": delivery, "event": payload}}).to_string(),
        )
        .ok()
        .json()
    };
    let out = env
        .fleet_stdin(
            &["gateway", "receive", "--account", GATEWAY_ACCOUNT, "--json"],
            &json!({"verified": "connection", "receivedAt": 1, "payload": {"id": "Ev03", "reaction": "eyes"}}).to_string(),
        )
        .ok()
        .json();
    assert_eq!(out["outcome"], "ignored", "{out:#}");
    let out = ignored(
        "Ev04",
        message_event(
            "fake:C0789IJKL:1",
            "1",
            ALEX_ID,
            T0,
            &format!("<@{BOT}> hi"),
        ),
    );
    assert_eq!(out["outcome"], "ignored");
    assert_eq!(out["why"], "no route covers channel C0789IJKL");
    let out = ignored(
        "Ev05",
        message_event(
            THREAD,
            "1730000300000",
            ALEX_ID,
            T0 + 300_000,
            "no mention here",
        ),
    );
    assert_eq!(out["outcome"], "ignored");
    assert_eq!(out["why"], "chatter in a thread Fleet does not mirror");
    env.fleet(&["get", "conversation", &conv]).code(3);
    // A mention from someone not on the allowlist earns a refusal, so it
    // creates the Conversation.
    let out = ignored(
        "Ev06",
        message_event(
            THREAD,
            "1730000300000",
            "U0999ZZZZ",
            T0 + 300_000,
            &format!("<@{BOT}> hi"),
        ),
    );
    assert_eq!(out["outcome"], "written", "{out:#}");
    env.fleet(&["get", "conversation", &conv]).ok();
    // Once mirrored, chatter is spooled too.
    let out = ignored(
        "Ev07",
        message_event(
            THREAD,
            "1730000400000",
            ALEX_ID,
            T0 + 400_000,
            "no mention here",
        ),
    );
    assert_eq!(out["outcome"], "written");
    // A Conversation being deleted takes nothing.
    env.fleet(&["delete", "conversation", &conv])
        .ok()
        .stdout_has(&format!("Conversation/{conv} deleted"));
    env.fleet(&["get", "conversation", &conv]).code(3);
    assert_eq!(
        env.fake_gateway().calls("post").unwrap(),
        0,
        "nothing on the platform changes"
    );
}

#[test]
fn a_beat_is_recorded_on_the_listener_and_the_account_verbs_report_it() {
    let env = Env::gateway("beat");
    let v = Env::new("gateway-kinds")
        .fleet(&["version", "--json"])
        .ok()
        .json();
    for kind in ["Conversation", "Listener"] {
        assert!(v["kinds"].as_array().unwrap().contains(&json!(kind)));
    }
    let account = env
        .fleet(&["gateway", "account", GATEWAY_ACCOUNT, "--json"])
        .ok()
        .json();
    assert_eq!(account["name"], GATEWAY_ACCOUNT);
    assert_eq!(account["platform"], "fake");
    assert_eq!(account["bot"], BOT);
    assert_eq!(account["inbound"], "connection");
    let digest = account["digest"].as_str().unwrap().to_string();
    assert!(digest.starts_with("sha256:") && digest.len() == 7 + 64);
    env.fleet(&["gateway", "account", "nobody"])
        .code(1)
        .stderr_has("account \"nobody\" is not configured");
    let listener = format!("listener-{GATEWAY_ACCOUNT}");
    // Before the manager ever ran: the verb creates the Listener.
    let out = env.beat("a1", "connected", &digest).ok().json();
    assert_eq!(out["outcome"], "beat", "{out:#}");
    let obj = env.fleet(&["get", "listener", &listener]).ok().json();
    assert_eq!(obj["spec"], json!({"account": GATEWAY_ACCOUNT}));
    let beat = &obj["status"]["beats"]["a1"];
    assert_eq!(beat["state"], "connected");
    assert_eq!(beat["digest"], digest);
    assert!(
        beat["at"].as_u64().unwrap() > 0,
        "stamped by the verb's clock"
    );
    assert!(beat["sentAt"].as_u64().unwrap() > 0);
    assert_eq!(obj["status"]["beatRevision"], 1);
    env.fleet_stdin(
        &["gateway", "receive", "--account", "nobody", "--beat"],
        &json!({"instance": "a1", "state": "connected", "digest": digest, "sentAt": 1}).to_string(),
    )
    .code(1);
    env.fleet_stdin(
        &["gateway", "receive", "--account", GATEWAY_ACCOUNT, "--beat"],
        "{\"instance\": \"a1\"}",
    )
    .code(4);
    assert_eq!(
        env.listener_status(&listener)["refused"]["counts"]["NotEnvelope"],
        1
    );

    let report = env.reconcile().ok().json();
    assert_eq!(report["errors"], json!([]), "{report:#}");
    let st = env.listener_status(&listener);
    assert_eq!(st["accepted"]["digest"], digest, "{st:#}");
    assert_condition(&st, "Accepted", "True", "Loaded");
    assert_condition(&st, "Resolved", "True", "IdentityMatches");
    assert_condition(&st, "InboundReady", "False", "DeliveriesRefused");
    assert_eq!(st["seen"], 2);
    env.fleet(&["gateway", "status"])
        .ok()
        .stdout_has(GATEWAY_ACCOUNT)
        .stdout_has("InboundReady False DeliveriesRefused");
    let rows = env.fleet(&["gateway", "status", "--json"]).ok().json();
    assert_eq!(rows[0]["account"], GATEWAY_ACCOUNT);
    assert_eq!(rows[0]["inbound"]["reason"], "DeliveriesRefused");
    assert_eq!(rows[0]["lastDeliveryAt"], Value::Null);
    // Time passes on the record, not on the clock: the fault ages out, then
    // the beat goes stale.
    let day = 86_400_000u64;
    let age = |f: &dyn Fn(&mut Value)| {
        let path = env.object_path("Listener", &listener);
        let mut obj: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        f(&mut obj["status"]);
        obj["status"]["notBefore"] = json!(0);
        fs::write(&path, obj.to_string()).unwrap();
    };
    age(&|st| {
        let at = st["refused"]["lastFaultAt"].as_u64().unwrap();
        st["refused"]["lastFaultAt"] = json!(at - day);
    });
    env.reconcile().ok();
    assert_condition(
        &env.listener_status(&listener),
        "InboundReady",
        "True",
        "Connected",
    );
    age(&|st| {
        let at = st["beats"]["a1"]["at"].as_u64().unwrap();
        st["beats"]["a1"]["at"] = json!(at - day);
    });
    env.reconcile().ok();
    assert_condition(
        &env.listener_status(&listener),
        "InboundReady",
        "False",
        "ListenerStale",
    );
    env.beat("a1", "connected", &digest).ok();
    env.reconcile().ok();
    assert_condition(
        &env.listener_status(&listener),
        "InboundReady",
        "True",
        "Connected",
    );
    env.beat("a2", "connected", "sha256:old").ok();
    env.beat("a1", "closing", &digest).ok();
    env.reconcile().ok();
    let st = env.listener_status(&listener);
    assert_condition(&st, "InboundReady", "False", "Closing");
    assert_eq!(st["beats"].as_object().unwrap().len(), 2);
    age(&|st| {
        let at = st["beats"]["a1"]["at"].as_u64().unwrap();
        st["beats"]["a1"]["at"] = json!(at - day);
    });
    env.beat("a2", "connecting", "sha256:old").ok();
    env.reconcile().ok();
    assert_condition(
        &env.listener_status(&listener),
        "InboundReady",
        "False",
        "ListenerConfigOld",
    );
    // The platform's identity is checked against the configured bot, every
    // pollMs.
    env.fake_gateway()
        .set_identity(fleet_api::gateway::Identity {
            id: "B0OTHER".into(),
            name: None,
        })
        .unwrap();
    age(&|st| st["observedAt"] = json!(0));
    env.reconcile().ok();
    assert_condition(
        &env.listener_status(&listener),
        "Resolved",
        "False",
        "IdentityMismatch",
    );
    // An account that left the config loses its Listener.
    env.set_gateway(&gateway_section(&env.root).replace(
        &format!("[gateway.accounts.{GATEWAY_ACCOUNT}]"),
        &format!(
            "[gateway.accounts.other]\nplatform = \"fake\"\nbot = \"B0FLEET002\"\ninbound = \"connection\"\ndir = \"gateway-other\"\n\n[gateway.accounts.{GATEWAY_ACCOUNT}]"
        ),
    ));
    env.reconcile().ok();
    env.fleet(&["get", "listener", "listener-other"]).ok();
    assert_condition(
        &env.listener_status("listener-other"),
        "Accepted",
        "False",
        "NoRoute",
    );
    env.set_gateway(&gateway_section(&env.root));
    env.expire_wait_of("Listener", "listener-other");
    env.reconcile().ok();
    env.fleet(&["get", "listener", "listener-other"]).code(3);
    env.fleet(&["get", "listener", &listener]).ok();
}

#[test]
fn an_orphaned_conversation_keeps_its_messages_and_is_retired_after_its_retention() {
    let (env, conv) = mirror("orphan");
    env.receive("Ev01", mention("1730000300000", T0 + 300_000))
        .ok();
    env.reconcile().ok();
    // The tie is task 2's to make; here it is written into the record.
    let task_name = format!("{conv}-1");
    env.apply(support::task(&task_name, false)).ok();
    let uid = env.get(&task_name)["metadata"]["uid"]
        .as_str()
        .unwrap()
        .to_string();
    let path = env.object_path("Conversation", &conv);
    let mut obj: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    obj["status"]["phase"] = json!("Tied");
    obj["status"]["task"] = json!({"name": task_name, "uid": uid, "n": 1, "state": "created"});
    obj["status"]["notBefore"] = json!(0);
    fs::write(&path, obj.to_string()).unwrap();
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(st["phase"], "Tied", "{st:#}");
    assert_condition(&st, "Tied", "True", "TaskKnown");
    assert_eq!(st["task"]["state"], "created");
    // A Task the store cannot read keeps the thread Tied: the manager
    // reports the unreadable record and decides nothing from it.
    let task_path = env.object_path("Task", &task_name);
    let bytes = fs::read(&task_path).unwrap();
    fs::write(&task_path, &bytes[..bytes.len() / 2]).unwrap();
    env.expire_wait_of("Conversation", &conv);
    let report = env.reconcile().ok().json();
    assert!(
        report["unknowns"]
            .as_array()
            .unwrap()
            .iter()
            .any(|u| u.as_str().unwrap().contains(&task_name)),
        "{report:#}"
    );
    assert_eq!(env.conversation_status(&conv)["phase"], "Tied");
    fs::write(&task_path, &bytes).unwrap();
    // Readably gone: Orphaned, the messages kept through a retention that
    // counts from the orphaning, not from the thread's last message.
    env.fleet(&["delete", "task", &task_name]).ok();
    env.reconcile().ok();
    env.get_absent(&task_name);
    env.expire_wait_of("Conversation", &conv);
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(st["phase"], "Orphaned", "{st:#}");
    assert_condition(&st, "Tied", "False", "TaskGone");
    assert_eq!(st["task"]["state"], "gone");
    assert_eq!(st["messages"].as_array().unwrap().len(), 2);
    let orphaned_at = support::condition(&st, "Tied")["lastTransitionTime"]
        .as_u64()
        .unwrap();
    let retain = 2_592_000_000u64;
    assert_eq!(st["notBefore"], orphaned_at + retain, "{st:#}");
    env.expire_wait_of("Conversation", &conv);
    env.reconcile().ok();
    env.fleet(&["get", "conversation", &conv]).ok();
    // Time passes on the record: the retention runs out, and the object is
    // retired by uid and resourceVersion.
    let mut obj: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    let old = orphaned_at - retain - 1;
    obj["metadata"]["creationTimestamp"] = json!(old);
    obj["status"]["lastMessageAt"] = json!(old);
    for c in obj["status"]["conditions"].as_array_mut().unwrap() {
        if c["type"] == "Tied" {
            c["lastTransitionTime"] = json!(old);
        }
    }
    obj["status"]["notBefore"] = json!(0);
    fs::write(&path, obj.to_string()).unwrap();
    let report = env.reconcile().ok().json();
    assert_eq!(report["errors"], json!([]), "{report:#}");
    env.fleet(&["get", "conversation", &conv]).code(3);
    assert_eq!(env.fake_gateway().calls("post").unwrap(), 0);
    // A later message there is a new thread: the mention rule applies again.
    let out = env
        .receive(
            "Ev02",
            message_event(
                THREAD,
                "1730000500000",
                ALEX_ID,
                T0 + 500_000,
                "still broken",
            ),
        )
        .ok()
        .json();
    assert_eq!(out["outcome"], "ignored", "{out:#}");
    env.receive("Ev03", mention("1730000600000", T0 + 600_000))
        .ok();
    env.fleet(&["get", "conversation", &conv]).ok();
}

#[test]
fn manifests_follow_the_write_ownership_rules() {
    let (env, conv) = mirror("manifests");
    env.apply(json!({"apiVersion": "fleet/v1alpha1", "kind": "Listener",
        "metadata": {"name": "listener-fake-main"}, "spec": {"account": GATEWAY_ACCOUNT}}))
        .code(1)
        .stderr_has("the config is the only source");
    // A Conversation by manifest takes its derived name, or none.
    let conversation = |name: Option<&str>, address: &str| {
        let mut metadata = json!({});
        if let Some(n) = name {
            metadata["name"] = json!(n);
        }
        json!({"apiVersion": "fleet/v1alpha1", "kind": "Conversation", "metadata": metadata,
               "spec": {"account": GATEWAY_ACCOUNT, "address": address, "route": GATEWAY_ROUTE}})
    };
    env.apply(conversation(Some("conv-000000000000"), THREAD))
        .code(1)
        .stderr_has("names one object");
    env.apply(conversation(None, "fake:C0123ABCD:nope nope"))
        .code(1)
        .stderr_has("is not one token");
    let mut wrong_route = conversation(None, THREAD);
    wrong_route["spec"]["route"] = json!("nowhere");
    env.apply(wrong_route)
        .code(1)
        .stderr_has("route \"nowhere\" is not under [gateway.routes]");
    env.apply(conversation(None, THREAD))
        .ok()
        .stdout_has(&format!("Conversation/{conv} created"));
    env.apply(conversation(Some(&conv), THREAD))
        .ok()
        .stdout_has(&format!("Conversation/{conv} unchanged"));
    // One thread's derived name with another's address is refused, and so
    // is any change to the spec once created.
    env.apply(conversation(Some(&conv), "fake:C0123ABCD:2"))
        .code(1)
        .stderr_has("names one object");
    let mut other = conversation(Some(&conv), THREAD);
    other["spec"]["pollMs"] = json!(5000);
    env.apply(other).code(1).stderr_has("immutable");
    env.reconcile().ok();
    let st = env.conversation_status(&conv);
    assert_eq!(
        st["revision"], 1,
        "the history alone numbers the thread: {st:#}"
    );
    assert_eq!(st["messages"].as_array().unwrap().len(), 1);
    // A manifest may not carry status, and the verb refuses a Task label it owns.
    let mut with_status = conversation(None, THREAD);
    with_status["status"] = json!({"phase": "Tied"});
    env.apply(with_status)
        .code(1)
        .stderr_has("status is written by controllers");
    let mut labelled = support::task("labelled", false);
    labelled["metadata"]["labels"]["gateway.fleet.dev/conversation"] = json!(conv);
    env.apply(labelled)
        .code(1)
        .stderr_has("gateway.fleet.dev/conversation");
}
