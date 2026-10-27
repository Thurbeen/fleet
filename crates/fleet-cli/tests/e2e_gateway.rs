//! Acceptance for the Gateway's first step: the `[gateway]` section loads,
//! validates and is refused for each invalid case the config page lists,
//! the fake adapter is wired when the manager runs, and the build names the
//! platforms it has. The Conversation and Listener kinds, the controllers
//! and the `fleet gateway` verbs are later steps.

mod support;

use serde_json::json;
use support::{ALEX_ID, Env, GATEWAY_ACCOUNT, GATEWAY_CHANNEL, GATEWAY_ROUTE, gateway_section};

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
    assert_eq!(gateway.state().unwrap().calls.len(), 0, "nothing called it");
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
