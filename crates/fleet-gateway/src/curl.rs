//! What every curl-backed platform adapter shares: one HTTP answer as
//! `curl --include` prints it, and the budget of calls and time one
//! operation may spend. What an answer means is each platform's own.

use std::time::{Duration, Instant};

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
