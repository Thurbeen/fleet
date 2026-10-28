//! ConversationController as a pure function: a Conversation, the spooled
//! deliveries, one history read and the tied Task go in; a plan comes out,
//! whose status is the whole mirror with its spool drained. Each case is
//! one transition of the mirror, or one reason nothing moves.

use fleet_api::condition::{Condition, ConditionStatus};
use fleet_api::conversation::{
    ConversationStatus, MESSAGE_CAP, Phase, SEEN_EVENTS_CAP, Tie, TieState,
};
use fleet_api::gateway::{
    Author, AuthorKind, Capabilities, ConversationRef, Delivery, Event, GatewayDefaults,
    HISTORY_FIRST_READ, History, HistoryObservation, HistoryQuery, Marker, Mention, Message,
    RouteConfig, Starter, Uncertainty, Verified,
};
use fleet_api::plan::{Plan, Probe};
use fleet_api::task::Workspace;
use fleet_api::{Object, Observation};
use fleet_controllers::{ConversationController, ConversationInput, ConversationObservations};
use serde_json::{Value, json};

const NOW: u64 = 1_730_001_000_000;
const T0: u64 = 1_730_000_000_000;
const ACCOUNT: &str = "fake-main";
const NAME: &str = "conv-3f9a1c0d2b7e";
const ALEX: &str = "U0123ABCD";
const SAM: &str = "U0456EFGH";
const CREATED_AT: u64 = NOW - 5_000;

fn thread() -> ConversationRef {
    ConversationRef::split("fake:C0123ABCD:1730000000000").unwrap()
}

fn route() -> RouteConfig {
    RouteConfig {
        account: ACCOUNT.into(),
        channels: vec!["C0123ABCD".into()],
        mention: Mention::Required,
        allow: vec!["alex".into()],
        allow_bots: Vec::new(),
        task_config: "conversation".into(),
        brief: "/etc/fleet/briefs/conversation.md".into(),
        workspace: Workspace::at("/work/conversations"),
        refusal: None,
    }
}

fn defaults() -> GatewayDefaults {
    GatewayDefaults {
        poll_ms: 1_000,
        active_ms: 100_000,
        retain_ms: 10_000,
        ..GatewayDefaults::default()
    }
    .for_route(Some(route()))
}

fn person(id: &str, at: u64, text: &str) -> Message {
    Message {
        id: at.to_string(),
        author: Author {
            id: id.into(),
            name: "Someone".into(),
            kind: AuthorKind::Person,
        },
        mentions_fleet: text.contains("<@"),
        at,
        text: text.into(),
        key: None,
        starts_thread: false,
    }
}

fn delivery(id: &str, event: Event) -> Delivery {
    Delivery {
        id: id.into(),
        verified: Verified::Connection,
        received_at: NOW - 500,
        event,
    }
}

fn message(id: &str, m: Message) -> Delivery {
    delivery(
        id,
        Event::Message {
            conversation: thread(),
            message: m,
        },
    )
}

fn edited(id: &str, message_id: &str, text: &str, edited_at: u64) -> Delivery {
    delivery(
        id,
        Event::Edited {
            conversation: thread(),
            id: message_id.into(),
            text: text.into(),
            edited_at,
        },
    )
}

fn removed(id: &str, message_id: &str) -> Delivery {
    delivery(
        id,
        Event::Removed {
            conversation: thread(),
            id: message_id.into(),
        },
    )
}

fn object(status: Option<&ConversationStatus>) -> Object {
    let mut v = json!({
        "apiVersion": "fleet/v1alpha1",
        "kind": "Conversation",
        "metadata": {"name": NAME, "uid": "c7", "generation": 1, "resourceVersion": 4,
                     "creationTimestamp": CREATED_AT},
        "spec": {"account": ACCOUNT, "address": "fake:C0123ABCD:1730000000000", "route": "eng"}
    });
    if let Some(st) = status {
        v["status"] = serde_json::to_value(st).unwrap();
    }
    serde_json::from_value(v).unwrap()
}

/// A mirror whose address is parsed, with `received` spooled.
fn mirrored(received: Vec<Delivery>) -> ConversationStatus {
    ConversationStatus {
        reference: Some(thread()),
        received,
        ..ConversationStatus::default()
    }
}

fn history(messages: Vec<Message>) -> HistoryObservation {
    let starter = messages.first().map(Starter::message);
    HistoryObservation::Known(History {
        messages,
        left_out: 0,
        starter,
    })
}

fn caps(history: bool) -> Observation<Capabilities> {
    Observation::Known(Capabilities {
        history,
        marker: Marker::Metadata,
        unmentioned: true,
        threads: true,
    })
}

fn run_with(
    obj: &Object,
    obs: ConversationObservations,
    defaults: GatewayDefaults,
    now: u64,
) -> (Plan, ConversationStatus) {
    let input = ConversationInput {
        object: obj,
        observations: obs,
        now,
        defaults,
    };
    let plan = ConversationController.reconcile(&input).unwrap();
    let st = match &plan.status {
        Some(v) => serde_json::from_value(v.clone()).unwrap(),
        None => serde_json::from_value(obj.status.clone()).unwrap_or_default(),
    };
    (plan, st)
}

fn run(obj: &Object, obs: ConversationObservations) -> (Plan, ConversationStatus) {
    run_with(obj, obs, defaults(), NOW)
}

fn probes(obj: &Object, obs: ConversationObservations) -> Vec<Probe> {
    let input = ConversationInput {
        object: obj,
        observations: obs,
        now: NOW,
        defaults: defaults(),
    };
    ConversationController.probes(&input).unwrap()
}

fn cond<'a>(st: &'a ConversationStatus, t: &str) -> &'a Condition {
    st.conditions
        .iter()
        .find(|c| c.type_ == t)
        .unwrap_or_else(|| panic!("no {t} in {st:#?}"))
}

fn seqs(st: &ConversationStatus) -> Vec<(&str, u64, u64, u64)> {
    st.messages
        .iter()
        .map(|m| (m.id.as_str(), m.seq, m.first_seq, m.position))
        .collect()
}

#[test]
fn a_fresh_conversation_wants_its_ref_then_capabilities_then_the_first_read() {
    let obj = object(None);
    assert!(ConversationController::due(&obj, NOW));
    assert!(
        probes(&obj, ConversationObservations::default()).is_empty(),
        "nothing to ask without a ref"
    );
    let parsed = ConversationObservations {
        parsed: Some(Observation::Known(thread())),
        ..ConversationObservations::default()
    };
    assert_eq!(
        probes(&obj, parsed.clone()),
        vec![Probe::Capabilities {
            account: ACCOUNT.into()
        }]
    );
    let with_caps = ConversationObservations {
        capabilities: Some(caps(true)),
        ..parsed.clone()
    };
    assert_eq!(
        probes(&obj, with_caps.clone()),
        vec![Probe::History {
            account: ACCOUNT.into(),
            conversation: thread(),
            query: HistoryQuery::After {
                id: None,
                limit: HISTORY_FIRST_READ
            }
        }]
    );
    let without_history = ConversationObservations {
        capabilities: Some(caps(false)),
        ..parsed.clone()
    };
    assert!(
        probes(&obj, without_history).is_empty(),
        "a platform without history is not read"
    );
    let unknown_caps = ConversationObservations {
        capabilities: Some(Observation::unknown("lost")),
        ..parsed
    };
    assert!(probes(&obj, unknown_caps).is_empty());
    let answered = ConversationObservations {
        history: Some(history(Vec::new())),
        ..with_caps
    };
    assert!(probes(&obj, answered).is_empty(), "everything is answered");
}

#[test]
fn an_address_the_adapter_cannot_read_is_unknown_and_tried_again() {
    let obj = object(None);
    let (plan, st) = run(
        &obj,
        ConversationObservations {
            parsed: Some(Observation::unknown("not one token")),
            ..ConversationObservations::default()
        },
    );
    assert!(plan.status.is_some());
    assert_eq!(st.reference, None);
    assert_eq!(cond(&st, "Reachable").status, ConditionStatus::Unknown);
    assert_eq!(cond(&st, "Reachable").reason, "AddressNotParsed");
    assert_eq!(st.phase, Phase::Untied);
}

#[test]
fn the_first_pass_numbers_the_history_and_the_spool_in_one_revision_oldest_first() {
    let mention = person(ALEX, T0 + 300_000, "<@B0FLEET001> can you look into it?");
    let obj = object(Some(&mirrored(vec![message("Ev01", mention.clone())])));
    let obs = ConversationObservations {
        capabilities: Some(caps(true)),
        history: Some(history(vec![
            person(SAM, T0, "The nightly export failed again."),
            person(SAM, T0 + 100_000, "Anyone?"),
        ])),
        ..ConversationObservations::default()
    };
    let (plan, st) = run(&obj, obs);
    assert!(!plan.delete);
    assert_eq!(st.revision, 1);
    assert_eq!(
        seqs(&st),
        vec![
            ("1730000000000", 1, 1, 0),
            ("1730000100000", 1, 1, 1),
            ("1730000300000", 1, 1, 2)
        ]
    );
    assert_eq!(st.messages[0].via.verified, Verified::History);
    assert_eq!(st.messages[0].via.received_at, NOW);
    assert_eq!(st.messages[2].via.verified, Verified::Connection);
    assert_eq!(st.messages[2].via.received_at, NOW - 500);
    assert!(st.messages[2].mentions_fleet);
    assert_eq!(
        st.starter,
        Some(Starter {
            id: Some("1730000000000".into()),
            kind: Some(AuthorKind::Person),
            key: None,
            removed: false
        })
    );
    assert_eq!(st.received, Vec::new(), "the spool is drained");
    assert_eq!(st.seen_events, vec!["Ev01".to_string()]);
    assert_eq!(st.last_message_at, Some(T0 + 300_000));
    assert_eq!(st.observed_at, Some(NOW));
    assert_eq!(st.dropped_before, 0);
    assert_eq!(cond(&st, "Reachable").reason, "Observed");
    assert_eq!(cond(&st, "Tied").status, ConditionStatus::False);
    assert_eq!(cond(&st, "Tied").reason, "Untied");
    assert!(st.conditions.iter().all(|c| c.type_ != "NeedsAttention"));
    // Untied: the next look is the end of its retention, counted from the
    // newer of its last message and its creation.
    assert_eq!(st.not_before, Some(CREATED_AT + 10_000));
}

#[test]
fn the_spool_is_numbered_even_while_the_history_is_unknown() {
    let obj = object(Some(&mirrored(vec![message(
        "Ev01",
        person(ALEX, T0 + 300_000, "<@B0FLEET001> hi"),
    )])));
    let obs = ConversationObservations {
        capabilities: Some(caps(true)),
        history: Some(HistoryObservation::Unknown {
            kind: Uncertainty::Throttled,
            why: "slow down".into(),
            retry_after: Some(2_500),
        }),
        ..ConversationObservations::default()
    };
    let (_, st) = run(&obj, obs);
    assert_eq!(st.revision, 1);
    assert_eq!(seqs(&st), vec![("1730000300000", 1, 1, 0)]);
    assert_eq!(cond(&st, "Reachable").status, ConditionStatus::Unknown);
    assert_eq!(cond(&st, "Reachable").reason, "Throttled");
    assert_eq!(st.observed_at, None, "the first read is still owed");
    assert_eq!(
        st.not_before,
        Some(NOW + 2_500),
        "the read is retried as the platform asked, before the retention"
    );
    // The history later lands: its messages take the next revision, the
    // mention is one message still.
    let again = object(Some(&st));
    let (_, st) = run(
        &again,
        ConversationObservations {
            capabilities: Some(caps(true)),
            history: Some(history(vec![
                person(SAM, T0, "first"),
                person(ALEX, T0 + 300_000, "<@B0FLEET001> hi"),
            ])),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.revision, 2);
    assert_eq!(
        seqs(&st),
        vec![("1730000000000", 2, 2, 0), ("1730000300000", 1, 1, 0)]
    );
    assert_eq!(st.observed_at, Some(NOW));
}

#[test]
fn a_platform_without_history_mirrors_the_events_alone() {
    let mut starter = person(ALEX, T0, "<@B0FLEET001> hi");
    starter.starts_thread = true;
    let obj = object(Some(&mirrored(vec![message("Ev01", starter)])));
    let (_, st) = run(
        &obj,
        ConversationObservations {
            capabilities: Some(caps(true)),
            history: Some(HistoryObservation::Unsupported),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.revision, 1);
    assert_eq!(cond(&st, "Reachable").status, ConditionStatus::False);
    assert_eq!(cond(&st, "Reachable").reason, "Unsupported");
    assert_eq!(
        st.starter.as_ref().and_then(|s| s.id.clone()),
        Some("1730000000000".into()),
        "a message that starts its thread is the starter"
    );
    assert_eq!(st.observed_at, Some(NOW), "nothing is owed");
    let again = object(Some(&st));
    assert!(
        probes(
            &again,
            ConversationObservations {
                capabilities: Some(caps(false)),
                ..ConversationObservations::default()
            }
        )
        .is_empty()
    );
}

#[test]
fn a_delivery_seen_twice_or_from_history_too_is_one_message() {
    let m = person(ALEX, T0 + 300_000, "<@B0FLEET001> hi");
    let obj = object(Some(&mirrored(vec![message("Ev01", m.clone())])));
    let (_, st) = run(
        &obj,
        ConversationObservations {
            capabilities: Some(caps(true)),
            history: Some(history(vec![person(SAM, T0, "first"), m.clone()])),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.revision, 1);
    assert_eq!(st.messages.len(), 2);
    // Redelivered after the spool was drained, and delivered again as a
    // second delivery id: neither numbers anything.
    let mut st2 = st.clone();
    st2.received = vec![message("Ev01", m.clone()), message("Ev02", m.clone())];
    let (plan, st3) = run(&object(Some(&st2)), ConversationObservations::default());
    assert_eq!(st3.revision, 1);
    assert_eq!(st3.messages, st.messages);
    assert_eq!(st3.received, Vec::new());
    assert_eq!(
        st3.seen_events,
        vec!["Ev01".to_string(), "Ev02".to_string()]
    );
    assert!(plan.status.is_some(), "the drained spool is written");
    // A quiet pass writes nothing.
    let (plan, _) = run(&object(Some(&st3)), ConversationObservations::default());
    assert_eq!(plan, Plan::default());
}

#[test]
fn a_newer_edit_moves_the_number_an_older_one_changes_nothing_and_a_removal_leaves() {
    let obj = object(Some(&mirrored(vec![message(
        "Ev01",
        person(ALEX, T0 + 300_000, "<@B0FLEET001> hi"),
    )])));
    let (_, st) = run(
        &obj,
        ConversationObservations {
            capabilities: Some(caps(true)),
            history: Some(history(vec![person(SAM, T0, "first")])),
            ..ConversationObservations::default()
        },
    );
    let mut st = st;
    st.received = vec![edited("Ev02", "1730000300000", "hi, fixed", T0 + 400_000)];
    let (_, st) = run(&object(Some(&st)), ConversationObservations::default());
    assert_eq!(st.revision, 2);
    assert_eq!(
        seqs(&st),
        vec![("1730000000000", 1, 1, 0), ("1730000300000", 2, 1, 1)]
    );
    assert_eq!(st.messages[1].text, "hi, fixed");
    assert_eq!(st.messages[1].edited_at, Some(T0 + 400_000));
    let mut older = st.clone();
    older.received = vec![edited("Ev03", "1730000300000", "stale", T0 + 350_000)];
    let (_, st) = run(&object(Some(&older)), ConversationObservations::default());
    assert_eq!(
        st.revision, 2,
        "an older edit delivered late changes nothing"
    );
    assert_eq!(st.messages[1].text, "hi, fixed");
    let mut unknown = st.clone();
    unknown.received = vec![edited("Ev04", "nope", "x", T0 + 999_000)];
    let (_, st) = run(&object(Some(&unknown)), ConversationObservations::default());
    assert_eq!(
        st.revision, 2,
        "an edit of a message never seen is drained and ignored"
    );
    let mut removing = st.clone();
    removing.received = vec![removed("Ev05", "1730000000000")];
    let (_, st) = run(
        &object(Some(&removing)),
        ConversationObservations::default(),
    );
    assert_eq!(st.revision, 3);
    assert_eq!(seqs(&st), vec![("1730000300000", 2, 1, 1)]);
    assert_eq!(st.starter, Some(Starter::removed()));
    assert_eq!(st.last_message_at, Some(T0 + 300_000));
}

#[test]
fn an_edit_arriving_with_its_message_in_one_pass_is_applied_without_a_second_number() {
    let obj = object(Some(&mirrored(vec![
        message("Ev01", person(ALEX, T0 + 300_000, "<@B0FLEET001> hi")),
        edited("Ev02", "1730000300000", "hi there", T0 + 310_000),
        message("Ev03", person(SAM, T0 + 320_000, "me too")),
        removed("Ev04", "1730000320000"),
    ])));
    let (_, st) = run(&obj, ConversationObservations::default());
    assert_eq!(st.revision, 1);
    assert_eq!(seqs(&st), vec![("1730000300000", 1, 1, 0)]);
    assert_eq!(st.messages[0].text, "hi there");
    assert_eq!(st.messages[0].edited_at, Some(T0 + 310_000));
}

#[test]
fn the_history_marks_fleets_own_posts_and_a_changed_text_as_an_edit() {
    let mut own = person("B0FLEET001", T0 + 500_000, "Looking now.");
    own.author.kind = AuthorKind::Fleet;
    own.key = Some("task/t/u7/gateway/comment/c1".into());
    let obj = object(Some(&mirrored(vec![message(
        "Ev01",
        person(ALEX, T0, "<@B0FLEET001> hi"),
    )])));
    let (_, st) = run(
        &obj,
        ConversationObservations {
            capabilities: Some(caps(true)),
            history: Some(history(vec![
                person(ALEX, T0, "<@B0FLEET001> hi"),
                own.clone(),
            ])),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.messages.len(), 2);
    assert_eq!(st.messages[1].author.kind, AuthorKind::Fleet);
    assert_eq!(st.messages[1].key, own.key);
    // The backstop later reads a changed text: an edit, numbered anew.
    let mut tied = st.clone();
    tied.task = Some(Tie {
        name: format!("{NAME}-1"),
        uid: "t1".into(),
        n: 1,
        state: TieState::Created,
        trigger: None,
    });
    tied.phase = Phase::Tied;
    tied.observed_at = Some(NOW - 5_000);
    tied.last_message_at = Some(NOW - 50_000);
    let later = object(Some(&tied));
    let task = Observation::Known(Some(task_object("t1")));
    let ask = probes(
        &later,
        ConversationObservations {
            task: Some(task.clone()),
            capabilities: Some(caps(true)),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(
        ask,
        vec![Probe::History {
            account: ACCOUNT.into(),
            conversation: thread(),
            query: HistoryQuery::After {
                id: Some("1730000500000".into()),
                limit: HISTORY_FIRST_READ
            }
        }],
        "the backstop reads after the newest message"
    );
    let mut changed = own.clone();
    changed.text = "Looking now, fixed.".into();
    let (_, st) = run(
        &later,
        ConversationObservations {
            task: Some(task),
            capabilities: Some(caps(true)),
            history: Some(history(vec![changed])),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.revision, 2);
    assert_eq!(st.messages[1].seq, 2);
    assert_eq!(st.messages[1].first_seq, 1);
    assert_eq!(st.messages[1].text, "Looking now, fixed.");
}

fn task_object(uid: &str) -> Object {
    serde_json::from_value(json!({
        "apiVersion": "fleet/v1alpha1",
        "kind": "Task",
        "metadata": {"name": format!("{NAME}-1"), "uid": uid, "generation": 1, "resourceVersion": 9},
        "spec": {"harness": "claude-code", "brief": "b", "workspace": {"dir": "w"}}
    }))
    .unwrap()
}

fn tied() -> ConversationStatus {
    let mut st = mirrored(Vec::new());
    st.phase = Phase::Tied;
    st.task = Some(Tie {
        name: format!("{NAME}-1"),
        uid: "t1".into(),
        n: 1,
        state: TieState::Created,
        trigger: None,
    });
    st.observed_at = Some(NOW - 100);
    st.last_message_at = Some(NOW - 50_000);
    st.revision = 1;
    st
}

#[test]
fn a_tied_thread_asks_for_its_task_and_stays_tied_while_the_task_is_there_or_unknown() {
    let obj = object(Some(&tied()));
    assert_eq!(
        probes(&obj, ConversationObservations::default()),
        vec![Probe::Object {
            kind: "Task".into(),
            name: format!("{NAME}-1")
        }]
    );
    let (plan, st) = run(
        &obj,
        ConversationObservations {
            task: Some(Observation::Known(Some(task_object("t1")))),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.phase, Phase::Tied);
    assert_eq!(cond(&st, "Tied").status, ConditionStatus::True);
    assert_eq!(cond(&st, "Tied").reason, "TaskKnown");
    assert_eq!(cond(&st, "Tied").message, format!("{NAME}-1"));
    assert_eq!(st.not_before, Some(NOW + 1_000), "the backstop cadence");
    assert!(!plan.delete);
    let (_, st) = run(
        &obj,
        ConversationObservations {
            task: Some(Observation::unknown("corrupt record")),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.phase, Phase::Tied, "Unknown never means gone");
    assert_eq!(st.task.as_ref().unwrap().state, TieState::Created);
}

#[test]
fn a_readably_gone_task_or_another_under_its_name_orphans_the_thread() {
    let obj = object(Some(&tied()));
    let (_, st) = run(
        &obj,
        ConversationObservations {
            task: Some(Observation::Known(None)),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.phase, Phase::Orphaned);
    assert_eq!(st.task.as_ref().unwrap().state, TieState::Gone);
    assert_eq!(cond(&st, "Tied").status, ConditionStatus::False);
    assert_eq!(cond(&st, "Tied").reason, "TaskGone");
    assert_eq!(cond(&st, "Tied").last_transition_time, NOW);
    assert_eq!(st.messages, tied().messages, "the messages are kept");
    assert_eq!(
        st.not_before,
        Some(NOW + 10_000),
        "retention counts from the orphaning, later than the last message"
    );
    let (_, st) = run(
        &obj,
        ConversationObservations {
            task: Some(Observation::Known(Some(task_object("t2")))),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(st.phase, Phase::Orphaned, "another Task holds the name");
    // Orphaned: the Task is not asked about again.
    let orphan = object(Some(&st));
    assert!(probes(&orphan, ConversationObservations::default()).is_empty());
}

#[test]
fn an_orphaned_or_untied_thread_is_deleted_once_its_retention_passed() {
    let mut st = tied();
    st.phase = Phase::Orphaned;
    st.task.as_mut().unwrap().state = TieState::Gone;
    fleet_api::condition::set_condition(
        &mut st.conditions,
        "Tied",
        ConditionStatus::False,
        "TaskGone",
        "",
        1,
        NOW - 3_000,
    );
    let obj = object(Some(&st));
    let (plan, st) = run(&obj, ConversationObservations::default());
    assert!(!plan.delete, "not yet: {st:#?}");
    assert_eq!(
        st.not_before,
        Some(NOW + 7_000),
        "from the orphaning, after the creation"
    );
    let (plan, _) = run_with(
        &obj,
        ConversationObservations::default(),
        defaults(),
        NOW + 7_000,
    );
    assert!(plan.delete);
    // Untied: from the later of the last message and the creation.
    let mut untied = mirrored(Vec::new());
    untied.observed_at = Some(NOW - 100);
    untied.last_message_at = Some(CREATED_AT + 5_000);
    let (plan, st) = run(&object(Some(&untied)), ConversationObservations::default());
    assert!(!plan.delete);
    assert_eq!(st.not_before, Some(CREATED_AT + 15_000));
    let (plan, _) = run_with(
        &object(Some(&untied)),
        ConversationObservations::default(),
        defaults(),
        CREATED_AT + 15_000,
    );
    assert!(plan.delete);
    // A thread waiting for the route's workspace is never retired.
    let mut waiting = untied.clone();
    waiting.task = Some(Tie {
        name: format!("{NAME}-1"),
        uid: String::new(),
        n: 1,
        state: TieState::Busy,
        trigger: Some("1730000300000".into()),
    });
    let (plan, st) = run_with(
        &object(Some(&waiting)),
        ConversationObservations::default(),
        defaults(),
        CREATED_AT + 15_000,
    );
    assert!(!plan.delete);
    assert_eq!(st.phase, Phase::Untied);
    assert_eq!(st.not_before, Some(CREATED_AT + 15_000 + 1_000));
}

#[test]
fn an_inactive_tied_thread_is_not_read_and_a_quiet_one_writes_nothing() {
    let mut st = tied();
    st.last_message_at = Some(NOW - 200_000);
    st.observed_at = Some(NOW - 5_000);
    let obj = object(Some(&st));
    let ask = probes(
        &obj,
        ConversationObservations {
            task: Some(Observation::Known(Some(task_object("t1")))),
            capabilities: Some(caps(true)),
            ..ConversationObservations::default()
        },
    );
    assert!(ask.is_empty(), "past activeMs the backstop stops: {ask:?}");
    let mut quiet = st.clone();
    quiet.phase = Phase::Tied;
    fleet_api::condition::set_condition(
        &mut quiet.conditions,
        "Tied",
        ConditionStatus::True,
        "TaskKnown",
        format!("{NAME}-1"),
        1,
        NOW - 50_000,
    );
    quiet.not_before = Some(NOW + 1_000);
    let (plan, _) = run(
        &object(Some(&quiet)),
        ConversationObservations {
            task: Some(Observation::Known(Some(task_object("t1")))),
            ..ConversationObservations::default()
        },
    );
    assert_eq!(plan, Plan::default(), "nothing new: no write");
    assert!(!ConversationController::due(&object(Some(&quiet)), NOW));
    let mut spooled = quiet.clone();
    spooled.received = vec![message("Ev09", person(SAM, NOW - 10, "ping"))];
    assert!(ConversationController::due(&object(Some(&spooled)), NOW));
}

#[test]
fn more_than_the_cap_rotates_the_oldest_and_raises_dropped_before() {
    let mut received: Vec<Delivery> = (0..=MESSAGE_CAP as u64)
        .map(|i| {
            message(
                &format!("Ev{i:03}"),
                person(ALEX, T0 + i * 1000, &format!("m{i}")),
            )
        })
        .collect();
    received.push(edited(
        "EvX",
        "1730000000000",
        "edited oldest",
        T0 + 999_999,
    ));
    let obj = object(Some(&mirrored(received)));
    let (_, st) = run(&obj, ConversationObservations::default());
    assert_eq!(st.messages.len(), MESSAGE_CAP);
    assert_eq!(st.messages[0].id, "1730000001000");
    assert_eq!(st.dropped_before, 1, "the rotated message's number");
    assert_eq!(st.seen_events.len(), MESSAGE_CAP + 2);
    // The seen delivery ids are bounded too.
    let mut many = st.clone();
    many.seen_events = (0..SEEN_EVENTS_CAP as u64)
        .map(|i| format!("old{i}"))
        .collect();
    many.received = vec![message("new", person(SAM, T0 + 5_000_000, "late"))];
    let (_, st) = run(&object(Some(&many)), ConversationObservations::default());
    assert_eq!(st.seen_events.len(), SEEN_EVENTS_CAP);
    assert_eq!(st.seen_events.last().map(String::as_str), Some("new"));
    assert_eq!(st.seen_events.first().map(String::as_str), Some("old1"));
}

#[test]
fn a_route_that_left_the_config_and_a_mentions_only_platform_need_attention() {
    let obj = object(Some(&mirrored(Vec::new())));
    let (_, st) = run_with(
        &obj,
        ConversationObservations::default(),
        defaults().for_route(None),
        NOW,
    );
    let c = cond(&st, "NeedsAttention");
    assert_eq!(
        (c.status, c.reason.as_str()),
        (ConditionStatus::True, "RouteUnknown")
    );
    let (_, st) = run(
        &object(Some(&st)),
        ConversationObservations {
            capabilities: Some(Observation::Known(Capabilities {
                unmentioned: false,
                ..caps(true).known().copied().unwrap()
            })),
            ..ConversationObservations::default()
        },
    );
    let c = cond(&st, "NeedsAttention");
    assert_eq!(
        (c.status, c.reason.as_str()),
        (ConditionStatus::True, "MentionsOnly")
    );
    assert!(c.message.contains("only mentions"));
    let (_, st) = run(
        &object(Some(&st)),
        ConversationObservations {
            capabilities: Some(caps(true)),
            ..ConversationObservations::default()
        },
    );
    let c = cond(&st, "NeedsAttention");
    assert_eq!(
        (c.status, c.reason.as_str()),
        (ConditionStatus::False, "Resolved")
    );
}

#[test]
fn a_deleting_object_asks_nothing_and_plans_nothing_and_malformed_status_is_invalid() {
    let mut obj = object(Some(&mirrored(vec![message(
        "Ev01",
        person(ALEX, T0, "<@B0FLEET001> hi"),
    )])));
    obj.metadata.deletion_timestamp = Some(NOW);
    assert!(!ConversationController::due(&obj, NOW));
    assert!(probes(&obj, ConversationObservations::default()).is_empty());
    let (plan, _) = run(&obj, ConversationObservations::default());
    assert_eq!(plan, Plan::default());
    let mut bad = object(None);
    bad.status = json!({"phase": "Sideways"});
    assert!(
        ConversationController::due(&bad, NOW),
        "reported, not skipped"
    );
    let input = ConversationInput {
        object: &bad,
        observations: ConversationObservations::default(),
        now: NOW,
        defaults: defaults(),
    };
    assert!(matches!(
        ConversationController.reconcile(&input),
        Err(fleet_api::StoreError::Invalid(_))
    ));
    let mut bad_spec = object(None);
    bad_spec.spec = json!({"account": "a"});
    let input = ConversationInput {
        object: &bad_spec,
        observations: ConversationObservations::default(),
        now: NOW,
        defaults: defaults(),
    };
    assert!(ConversationController.probes(&input).is_err());
    let _: Value = json!(null);
}
