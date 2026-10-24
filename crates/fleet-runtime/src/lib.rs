//! Implementations of the runtime contract ([`fleet_api::runtime::Runtime`]).
//!
//! - [`ThurboxRuntime`]: sessions through `thurbox-cli`, on this machine or a
//!   host thurbox reaches over SSH or WSL.
//! - [`FakeRuntime`]: a file-backed fake host for tests and local trials. It
//!   keeps sessions as records, not processes, and runs nothing.

pub mod fake;
pub mod thurbox;

pub use fake::{FakeHost, FakeRuntime, Fault};
pub use thurbox::{ThurboxConfig, ThurboxRuntime};
