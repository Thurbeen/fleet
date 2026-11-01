//! What every curl-backed platform adapter shares: one HTTP answer as
//! `curl --include` prints it, and the budget of calls and time one
//! operation may spend. What an answer means is each platform's own.

use std::time::{Duration, Instant};

use fleet_api::Observation;
use fleet_api::gateway::{
    AccountConfig, Capabilities, ConversationRef, Delivery, Envelope, HistoryObservation,
    HistoryQuery, Identity,
};
use fleet_api::plan::EffectOutcome;

/// One HTTP answer as `curl --include` prints it: the status line and
/// headers, then the body.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Reply {
    /// 0 when no status line was printed.
    pub status: u16,
    /// Lowercased names.
    pub headers: Vec<(String, String)>,
    pub body: String,
    pub exit: Option<i32>,
    /// The first non-empty line of stderr, at most 200 characters.
    pub stderr: String,
}

impl Reply {
    /// Split `--include` output into its last head and the body: an
    /// informational head (`100 Continue`) before the final one is skipped.
    pub fn parse(stdout: &str, exit: Option<i32>, stderr: &[u8]) -> Reply {
        let mut rest = stdout;
        let mut status = 0;
        let mut headers = Vec::new();
        while rest.starts_with("HTTP/") {
            let (head, body) = match rest.find("\r\n\r\n") {
                Some(i) => (&rest[..i], &rest[i + 4..]),
                None => match rest.find("\n\n") {
                    Some(i) => (&rest[..i], &rest[i + 2..]),
                    None => (rest, ""),
                },
            };
            let mut lines = head.lines();
            status = lines
                .next()
                .and_then(|l| l.split_whitespace().nth(1))
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            headers = lines
                .filter_map(|l| l.split_once(':'))
                .map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string()))
                .collect();
            rest = body;
        }
        Reply {
            status,
            headers,
            body: rest.to_string(),
            exit,
            stderr: first_line(stderr),
        }
    }

    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }
}

fn first_line(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes)
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim()
        .chars()
        .take(200)
        .collect()
}

/// The time and calls one operation may spend: `call_timeout` times
/// `calls`, and no more calls than that either.
#[derive(Debug)]
pub struct Budget {
    deadline: Instant,
    calls_left: u32,
    call_timeout: Duration,
}

impl Budget {
    pub fn new(call_timeout: Duration, calls: u32) -> Budget {
        Budget {
            deadline: Instant::now() + call_timeout * calls,
            calls_left: calls,
            call_timeout,
        }
    }

    /// The timeout of the next call, or `None` when the budget is spent.
    pub fn next_timeout(&mut self) -> Option<Duration> {
        if self.calls_left == 0 {
            return None;
        }
        let left = self.deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return None;
        }
        self.calls_left -= 1;
        Some(left.min(self.call_timeout))
    }
}

/// What a curl-backed chat platform supplies; [`Gateway`] is then the same
/// for every such platform: the account check, the address check, and the
/// thread or channel dispatch around each call.
pub(crate) trait CurlPlatform {
    /// The platform's name in messages: `Slack`, `Discord`.
    const NAME: &'static str;
    /// The account this adapter serves.
    fn account(&self) -> &str;
    /// The bound of one call.
    fn call_timeout(&self) -> Duration;
    /// The most calls one operation makes.
    fn max_calls(&self) -> u32;
    /// The parts of a reference are this platform's ids.
    fn check_ref(&self, r: &ConversationRef) -> Result<(), String>;
    fn parse_address(&self, address: &str) -> Result<ConversationRef, String>;
    fn read_event(
        &self,
        account: &AccountConfig,
        envelope: &Envelope,
    ) -> Observation<Option<Delivery>>;
    fn identity(&self) -> Observation<Identity>;
    fn offers(&self) -> Observation<Capabilities>;
    fn thread_history(&self, chat: &str, thread: &str, query: &HistoryQuery) -> HistoryObservation;
    fn channel_history(&self, chat: &str, query: &HistoryQuery) -> HistoryObservation;
    /// Post `text` in `thread`, or open a thread in `chat` with it.
    fn post_chunks(
        &self,
        chat: &str,
        thread: Option<&str>,
        text: &str,
        key: &str,
    ) -> Observation<EffectOutcome>;

    fn other_account(&self, account: &str) -> Option<String> {
        (account != self.account()).then(|| {
            format!(
                "account {account} is not this {} gateway, which serves {}",
                Self::NAME,
                self.account()
            )
        })
    }
}

/// The [`Gateway`] of a [`CurlPlatform`]: invoked once per adapter, since the
/// port is another crate's trait.
///
/// [`Gateway`]: fleet_api::gateway::Gateway
macro_rules! curl_gateway {
    ($t:ty) => {
        impl ::fleet_api::gateway::Gateway for $t {
            fn operation_bound(&self) -> ::fleet_api::Millis {
                let one = fleet_exec::worst_case($crate::curl::CurlPlatform::call_timeout(self))
                    * $crate::curl::CurlPlatform::max_calls(self);
                u64::try_from(one.as_millis()).unwrap_or(u64::MAX)
            }

            fn parse(
                &self,
                account: &str,
                address: &str,
            ) -> ::fleet_api::Observation<::fleet_api::gateway::ConversationRef> {
                if let Some(why) = $crate::curl::CurlPlatform::other_account(self, account) {
                    return ::fleet_api::Observation::unknown(why);
                }
                match $crate::curl::CurlPlatform::parse_address(self, address) {
                    Ok(r) => ::fleet_api::Observation::Known(r),
                    Err(why) => ::fleet_api::Observation::unknown(why),
                }
            }

            fn event(
                &self,
                account: &::fleet_api::gateway::AccountConfig,
                envelope: &::fleet_api::gateway::Envelope,
            ) -> ::fleet_api::Observation<Option<::fleet_api::gateway::Delivery>> {
                $crate::curl::CurlPlatform::read_event(self, account, envelope)
            }

            fn whoami(
                &self,
                account: &str,
            ) -> ::fleet_api::Observation<::fleet_api::gateway::Identity> {
                match $crate::curl::CurlPlatform::other_account(self, account) {
                    Some(why) => ::fleet_api::Observation::unknown(why),
                    None => $crate::curl::CurlPlatform::identity(self),
                }
            }

            fn capabilities(
                &self,
                account: &str,
            ) -> ::fleet_api::Observation<::fleet_api::gateway::Capabilities> {
                match $crate::curl::CurlPlatform::other_account(self, account) {
                    Some(why) => ::fleet_api::Observation::unknown(why),
                    None => $crate::curl::CurlPlatform::offers(self),
                }
            }

            fn history(
                &self,
                account: &str,
                conversation: &::fleet_api::gateway::ConversationRef,
                query: &::fleet_api::gateway::HistoryQuery,
            ) -> ::fleet_api::gateway::HistoryObservation {
                if let Some(why) = $crate::curl::CurlPlatform::other_account(self, account) {
                    return ::fleet_api::gateway::HistoryObservation::unknown(
                        ::fleet_api::gateway::Uncertainty::Unavailable,
                        why,
                    );
                }
                if let Err(why) = $crate::curl::CurlPlatform::check_ref(self, conversation) {
                    return ::fleet_api::gateway::HistoryObservation::unknown(
                        ::fleet_api::gateway::Uncertainty::Unreadable,
                        why,
                    );
                }
                match &conversation.thread {
                    Some(thread) => $crate::curl::CurlPlatform::thread_history(
                        self,
                        &conversation.chat,
                        thread,
                        query,
                    ),
                    None => {
                        $crate::curl::CurlPlatform::channel_history(self, &conversation.chat, query)
                    }
                }
            }

            fn open(
                &self,
                account: &str,
                channel: &::fleet_api::gateway::ConversationRef,
                text: &str,
                intent_key: &str,
            ) -> ::fleet_api::Observation<::fleet_api::plan::EffectOutcome> {
                if let Some(why) = $crate::curl::CurlPlatform::other_account(self, account) {
                    return ::fleet_api::Observation::Known(
                        ::fleet_api::plan::EffectOutcome::Failed { why },
                    );
                }
                if let Err(why) = $crate::curl::CurlPlatform::check_ref(self, channel) {
                    return ::fleet_api::Observation::Known(
                        ::fleet_api::plan::EffectOutcome::Failed { why },
                    );
                }
                if channel.thread.is_some() {
                    return ::fleet_api::Observation::Known(
                        ::fleet_api::plan::EffectOutcome::Failed {
                            why: format!("{} is a thread, not a channel", channel.address()),
                        },
                    );
                }
                $crate::curl::CurlPlatform::post_chunks(self, &channel.chat, None, text, intent_key)
            }

            fn post(
                &self,
                account: &str,
                conversation: &::fleet_api::gateway::ConversationRef,
                text: &str,
                intent_key: &str,
            ) -> ::fleet_api::Observation<::fleet_api::plan::EffectOutcome> {
                if let Some(why) = $crate::curl::CurlPlatform::other_account(self, account) {
                    return ::fleet_api::Observation::Known(
                        ::fleet_api::plan::EffectOutcome::Failed { why },
                    );
                }
                if let Err(why) = $crate::curl::CurlPlatform::check_ref(self, conversation) {
                    return ::fleet_api::Observation::Known(
                        ::fleet_api::plan::EffectOutcome::Failed { why },
                    );
                }
                let Some(thread) = &conversation.thread else {
                    return ::fleet_api::Observation::Known(
                        ::fleet_api::plan::EffectOutcome::Failed {
                            why: format!(
                                "{} is a channel, not a thread; a {} conversation is a thread",
                                conversation.address(),
                                <$t as $crate::curl::CurlPlatform>::NAME
                            ),
                        },
                    );
                };
                $crate::curl::CurlPlatform::post_chunks(
                    self,
                    &conversation.chat,
                    Some(thread),
                    text,
                    intent_key,
                )
            }
        }
    };
}
pub(crate) use curl_gateway;
